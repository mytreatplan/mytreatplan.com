"""Account erasure from the admin (plan-account-erasure.md): every row of the plan's I/O
matrix, the purge job and its schedule."""
from datetime import timedelta

import pytest
from dateutil.relativedelta import relativedelta
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import Client
from django.db import connection
from django.utils import timezone

from accounts.crypto import email_index, phone_index
from accounts.erasure import erase_user, purge_erased
from eventlog import retention as eventlog_retention
from eventlog import service
from eventlog.models import Attempt, Event
from legal.models import LegalChoice, LegalDocument
from mytp_core import jobs
from signup.forms import email_is_registered, phone_is_registered

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = 'violet-orbit-canyon-42'
EMAIL = 'ana.garcia@example.com'
FIRST_NAME = 'Anabel'
LAST_NAME = 'Garcia-Lorente'
PHONE = '+34622918374'
CHANGELIST = '/admin/accounts/user/'
CONSENT = LegalDocument.Kind.COMMERCIAL_CONSENT


@pytest.fixture(autouse=True)
def retention(settings):
    settings.ACCOUNT_ERASURE_RETENTION_DAYS = 30


def _document(kind, published_at=None):
    return LegalDocument.objects.create(
        kind=kind, title_en='x', title_es='x', body_en='x', body_es='x',
        published_at=published_at or timezone.now() - timedelta(days=1))


def _user(email=EMAIL, **fields):
    defaults = dict(title='dr', first_name=FIRST_NAME, last_name=LAST_NAME, country='ES',
                    phone=PHONE, how_found='others', how_found_other='A friend')
    user = User.objects.create_user(email, PASSWORD, **{**defaults, **fields})
    user.last_login = timezone.now()
    user.save()
    return user


def _choose(user, kind, accepted, when=None):
    document = LegalDocument.objects.current(kind) or _document(kind)
    return LegalChoice.objects.create(user=user, document=document, accepted=accepted,
                                      decided_at=when or timezone.now() - timedelta(hours=1))


@pytest.fixture
def staff_client(staff):
    client = Client()
    client.force_login(staff)
    return client


def _erase(client, *users, confirm=True, url=CHANGELIST):
    """The admin action: its confirmation page, then the confirm button."""
    data = {'action': 'erase_accounts', '_selected_action': [u.pk for u in users]}
    page = client.post(url, data)
    data['post'] = 'yes'
    if confirm:
        data['confirm'] = 'yes'
    return page, client.post(url, data, follow=True)


def _messages(response):
    return [str(m) for m in response.context['messages']]


# --- Erase -----------------------------------------------------------------------------------

def test_erase_through_the_admin(staff_client, staff):
    user = _user()
    group = Group.objects.create(name='editors')
    user.groups.add(group)
    user.user_permissions.add(Permission.objects.get(codename='view_user'))
    terms = _choose(user, LegalDocument.Kind.TERMS, True)
    consent = _choose(user, CONSENT, True)
    before = timezone.now()

    page, done = _erase(staff_client, user)

    assert page.status_code == 200
    content = page.content.decode()
    assert 'Accounts to erase: 1.' in content
    assert "Erasing can't be undone." in content
    assert 'Its retention ends on' in content
    assert 'the next daily purge then deletes it for good' in content
    assert 'name="confirm"' in content and 'required' in content
    assert done.status_code == 200
    assert 'Accounts erased: 1.' in _messages(done)

    user.refresh_from_db()
    assert user.is_erased and user.erased_at >= before
    assert user.purge_after == user.erased_at + timedelta(days=30)
    assert not (user.is_active or user.is_staff or user.is_superuser)
    assert not user.has_usable_password()
    assert not user.groups.exists() and not user.user_permissions.exists()
    # Kept as evidence.
    assert (user.email, user.title, user.first_name, user.last_name, user.country.code,
            user.phone) == (EMAIL, 'dr', FIRST_NAME, LAST_NAME, 'ES', PHONE)
    assert user.date_joined
    # Cleared.
    assert (user.how_found, user.how_found_other, user.last_login) == ('', '', None)
    # Blind indexes.
    assert user.email_index == f'erased:{user.pk}'
    assert user.erased_email_index == email_index(EMAIL)
    assert user.phone_index == ''
    # The choices kept, and the commercial consent withdrawn on the current version.
    choices = list(user.legal_choices.order_by('decided_at', 'id'))
    assert choices[:2] == [terms, consent]
    withdrawal = choices[2]
    assert (withdrawal.document, withdrawal.accepted, withdrawal.decided_at) == (
        LegalDocument.objects.current(CONSENT), False, user.erased_at)
    assert len(choices) == 3
    # The admin log: "Erased", User <pk>, the staff member; no personal values.
    entry = LogEntry.objects.get(object_id=str(user.pk))
    assert (entry.action_flag, entry.change_message, entry.object_repr, entry.user) == (
        CHANGE, 'Erased', f'User {user.pk}', staff)
    for value in (EMAIL, FIRST_NAME, LAST_NAME):
        assert value not in entry.object_repr + entry.change_message
        assert all(value not in m for m in _messages(done))


def test_erase_ends_the_sessions(staff):
    user = _user()
    client = Client()
    client.force_login(user)
    assert client.get('/en/').wsgi_request.user.is_authenticated

    erase_user(user, by=staff)

    assert not client.get('/en/').wsgi_request.user.is_authenticated
    assert not Client().login(email=EMAIL, password=PASSWORD)


# --- Re-register -------------------------------------------------------------------------------

def test_erased_email_and_phone_are_free_again(staff):
    user = _user()
    erase_user(user, by=staff)

    assert not email_is_registered(EMAIL.upper())
    assert not phone_is_registered(PHONE)
    assert not User.objects.by_email(EMAIL).exists()
    again = User.objects.create_user(EMAIL, PASSWORD, phone=PHONE)
    assert again.email_index == email_index(EMAIL)
    assert again.erased_email_index == ''
    assert User.objects.filter(erased_email_index=email_index(EMAIL)).get() == user


def test_erased_email_passes_signup_step1(staff, portal_urls):
    from signup.tests.helpers import EMAIL_ADDRESS, MOBILE_E164, _step1

    user = _user(EMAIL_ADDRESS, phone=MOBILE_E164)
    response = _step1(Client())
    assert response.status_code == 200  # a duplicate while the account is live
    erase_user(user, by=staff)

    response = _step1(Client())

    assert response.status_code == 302, response.context['form'].errors


# --- Find for evidence --------------------------------------------------------------------------

@pytest.mark.parametrize('status, found', [('', False), ('active', False), ('erased', True),
                                           ('all', True)])
def test_admin_search_finds_an_erased_account_under_erased_or_all(staff_client, staff,
                                                                   status, found):
    user = _user()
    erase_user(user, by=staff)
    query = {'q': EMAIL.upper()}
    if status:
        query['status'] = status

    response = staff_client.get(CHANGELIST, query)

    assert response.status_code == 200
    assert (user in response.context['cl'].result_list) is found


def test_default_list_hides_erased_users(staff_client, staff):
    erased, live = _user(), _user('bea@example.org')
    erase_user(erased, by=staff)

    default = staff_client.get(CHANGELIST)
    erased_only = staff_client.get(CHANGELIST, {'status': 'erased'})
    every = staff_client.get(CHANGELIST, {'status': 'all'})

    assert set(default.context['cl'].result_list) == {staff, live}
    assert list(erased_only.context['cl'].result_list) == [erased]
    assert set(every.context['cl'].result_list) == {staff, live, erased}
    filters = default.content.decode()
    for label in ('Active', 'Erased', 'All'):
        assert label in filters


# --- Unset retention -----------------------------------------------------------------------------

@pytest.mark.parametrize('days', [None, 0, -5])
def test_unset_retention_refuses(staff_client, staff, settings, days):
    settings.ACCOUNT_ERASURE_RETENTION_DAYS = days
    user = _user()

    page = staff_client.post(CHANGELIST, {'action': 'erase_accounts',
                                          '_selected_action': [user.pk]}, follow=True)
    _page, done = _erase(staff_client, user)

    for response in (page, done):
        assert ('Accounts cannot be erased: ACCOUNT_ERASURE_RETENTION_DAYS is not set to a '
                'positive number of days.' in _messages(response))
    user.refresh_from_db()
    assert not user.is_erased and user.is_active and user.has_usable_password()
    with pytest.raises(ImproperlyConfigured):
        erase_user(user, by=staff)
    user.refresh_from_db()
    assert not user.is_erased


# --- Own account / already erased ---------------------------------------------------------------

def test_own_account_is_skipped(staff_client, staff):
    page = staff_client.post(CHANGELIST, {'action': 'erase_accounts',
                                          '_selected_action': [staff.pk]}, follow=True)

    assert 'Your own account was skipped.' in _messages(page)
    staff.refresh_from_db()
    assert not staff.is_erased and staff.is_active
    with pytest.raises(ValueError):
        erase_user(staff, by=staff)


def test_already_erased_is_skipped(staff_client, staff):
    erased, live = _user(), _user('bea@example.org')
    erase_user(erased, by=staff)
    first_erasure = User.objects.get(pk=erased.pk).erased_at

    # Erased accounts are listed (and so selectable) under the "Erased" or "All" status.
    page, done = _erase(staff_client, erased, live, staff, url=f'{CHANGELIST}?status=all')

    assert 'Accounts to erase: 1.' in page.content.decode()
    assert 'Accounts skipped because they are already erased: 1.' in _messages(page)
    assert 'Your own account was skipped.' in _messages(page)
    assert 'Accounts erased: 1.' in _messages(done)
    # The skips are told once, on the confirmation page only.
    assert 'Your own account was skipped.' not in _messages(done)
    assert not any('already erased' in m for m in _messages(done))
    assert User.objects.get(pk=erased.pk).erased_at == first_erasure
    assert User.objects.get(pk=live.pk).is_erased
    assert LogEntry.objects.filter(object_id=str(erased.pk)).count() == 1
    assert not erase_user(User.objects.get(pk=erased.pk), by=staff)


# --- Commercial consent withdrawal ---------------------------------------------------------------

def test_no_consent_version_adds_no_withdrawal(staff):
    LegalDocument.objects.filter(kind=CONSENT).update(published_at=None)
    user = _user()

    erase_user(user, by=staff)

    assert not user.legal_choices.exists()


def test_already_declined_consent_adds_no_withdrawal(staff):
    user = _user()
    _choose(user, CONSENT, True, timezone.now() - timedelta(days=2))
    declined = _choose(user, CONSENT, False)

    erase_user(user, by=staff)

    assert list(user.legal_choices.order_by('decided_at'))[-1] == declined
    assert user.legal_choices.count() == 2


# --- Re-encrypt ----------------------------------------------------------------------------------

def test_reencrypt_fields_keeps_the_tombstone(staff, capsys):
    user = _user()
    erase_user(user, by=staff)

    call_command('reencrypt_fields')

    user.refresh_from_db()
    assert user.email_index == f'erased:{user.pk}'
    assert user.erased_email_index == email_index(EMAIL)
    assert user.phone_index == ''
    assert user.email == EMAIL and user.phone == PHONE


def test_save_with_update_fields_keeps_the_indexes(staff):
    user = _user()
    erase_user(user, by=staff)
    user = User.objects.get(pk=user.pk)

    user.save(update_fields=['email', 'phone'])

    user = User.objects.get(pk=user.pk)
    assert (user.email_index, user.erased_email_index, user.phone_index) == (
        f'erased:{user.pk}', email_index(EMAIL), '')
    assert phone_index(PHONE) not in User.objects.values_list('phone_index', flat=True)


# --- Purge ---------------------------------------------------------------------------------------

def test_purge_deletes_due_accounts_with_their_choices(staff):
    user = _user()
    _choose(user, LegalDocument.Kind.TERMS, True)
    erase_user(user, by=staff)
    purge_after = User.objects.get(pk=user.pk).purge_after

    assert purge_erased(now=purge_after - timedelta(seconds=1)) == 0
    assert User.objects.filter(pk=user.pk).exists()

    assert purge_erased(now=purge_after) == 1
    assert not User.objects.filter(pk=user.pk).exists()
    assert not LegalChoice.objects.filter(user_id=user.pk).exists()
    assert purge_erased(now=purge_after) == 0


def test_purge_leaves_live_and_not_due_accounts(staff):
    live = _user('live@example.org')
    _choose(live, LegalDocument.Kind.TERMS, True)
    not_due = _user()
    erase_user(not_due, by=staff)

    assert purge_erased(now=timezone.now() + timedelta(days=29)) == 0
    assert purge_erased(now=timezone.now() + timedelta(days=3650)) == 1
    assert User.objects.filter(pk=live.pk).exists()
    assert live.legal_choices.count() == 1
    assert User.objects.filter(pk=staff.pk).exists()


def test_purge_keeps_the_activity_records_for_their_5_years(staff):
    """Epic-login entry 8: an erased account's login and page records (1 year old) stay,
    with its pk and their IP and User-Agent, through the purge and the retention job."""
    user = _user()
    pk = user.pk
    data = {'ip': '203.0.113.9', 'user_agent': 'Mozilla/5.0 (ErasedBrowser)'}
    ended = timezone.now() - timedelta(days=365)
    for kind, event in ((Attempt.LOGIN, 'login'), (Attempt.PAGE, 'page_view')):
        attempt = service.start_attempt(kind, account_id=pk)
        service.record(attempt, event, data=data)
        Attempt.objects.filter(pk=attempt.pk).update(ended_at=ended, outcome=event)
    erase_user(user, by=staff)

    assert purge_erased(now=User.objects.get(pk=pk).purge_after) == 1
    assert not User.objects.filter(pk=pk).exists()
    eventlog_retention.anonymize()
    eventlog_retention.delete_old()
    assert eventlog_retention.delete_old_activity() == (0, 0)

    records = Event.objects.filter(attempt__account_id=pk).order_by('attempt__kind')
    # With the purge's own record (QA A6).
    assert [r.attempt.kind for r in records] == [Attempt.ACCOUNT, Attempt.LOGIN, Attempt.PAGE]
    assert all(r.data == data for r in records[1:])


def test_purge_leaves_one_account_record_with_the_encrypted_email(staff):
    """QA A6: before the account goes, one `account` record (`account_purged`) tied to its
    pk, holding only its email, encrypted, so its 5-year records can be tied to it."""
    user = _user()
    pk = user.pk
    erase_user(user, by=staff)
    before = timezone.now()

    assert purge_erased(now=User.objects.get(pk=pk).purge_after) == 1

    assert not User.objects.filter(pk=pk).exists()
    [record] = Event.objects.filter(attempt__account_id=pk)
    assert record.attempt.kind == Attempt.ACCOUNT == 'account'
    assert record.attempt.outcome == record.event == 'account_purged'
    assert record.attempt.ended_at >= before
    assert (record.step, record.field, record.reason) == ('purge', '', '')
    # Read by pk, it decrypts to the email, and holds nothing more.
    assert record.data == {'email': EMAIL}
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT data FROM {Event._meta.db_table} WHERE id = %s', [record.pk])
        [stored] = cursor.fetchone()
    for value in (EMAIL, FIRST_NAME, LAST_NAME, PHONE):
        assert value not in str(stored)
    assert EMAIL not in ' '.join([record.step, record.event, record.field, record.reason,
                                  record.attempt.kind, record.attempt.outcome])


def test_purge_record_is_kept_5_years_then_deleted(staff):
    """QA A6: an activity record, never anonymized, deleted 5 calendar years after it."""
    user = _user()
    pk = user.pk
    erase_user(user, by=staff)
    purge_erased(now=User.objects.get(pk=pk).purge_after)
    later = timezone.now() + timedelta(days=800)

    eventlog_retention.anonymize(now=later)
    eventlog_retention.delete_old(now=later)
    almost = timezone.now() + relativedelta(years=5) - timedelta(days=1)
    assert eventlog_retention.delete_old_activity(now=almost) == (0, 0)
    [record] = Event.objects.filter(attempt__account_id=pk)
    assert record.data == {'email': EMAIL}

    past = timezone.now() + relativedelta(years=5) + timedelta(days=1)
    assert eventlog_retention.delete_old_activity(now=past) == (1, 1)
    assert not Event.objects.filter(attempt__account_id=pk).exists()


def test_purge_keeps_an_account_whose_email_cannot_be_read(staff, sentry_capture):
    """An undecryptable email stops only its own account: reported without values, kept
    for the next run; the rest of the batch is purged."""
    import json

    import sentry_sdk

    broken, purged = _user(), _user('other@example.org', phone='')
    for user in (broken, purged):
        erase_user(user, by=staff)
    with connection.cursor() as cursor:
        cursor.execute(f'UPDATE {User._meta.db_table} SET email = %s WHERE id = %s',
                       ['enc:v1:test1:not-a-ciphertext', broken.pk])

    assert purge_erased(now=timezone.now() + timedelta(days=31)) == 1
    sentry_sdk.flush()

    assert User.objects.filter(pk=broken.pk).exists()
    assert not User.objects.filter(pk=purged.pk).exists()
    assert not Event.objects.filter(attempt__account_id=broken.pk).exists()
    [record] = Event.objects.filter(attempt__account_id=purged.pk)
    assert record.data == {'email': 'other@example.org'}
    messages = [e.get('logentry', {}).get('message') or e.get('message')
                for e in sentry_capture.events]
    assert any(m and m.startswith('Account purge error: read_email (') for m in messages)
    text = json.dumps(sentry_capture.events)
    assert 'not-a-ciphertext' not in text and 'other@example.org' not in text


def test_purge_keeps_an_account_whose_record_cannot_be_written(staff, monkeypatch):
    """The record is the only tie left to the person: without it (record_activity, which
    reports its own failures, returns None) the account waits for the next purge."""
    kept, purged = _user(), _user('other@example.org', phone='')
    for user in (kept, purged):
        erase_user(user, by=staff)
    real = service.record_activity

    def record_activity(*args, account_id=None, **kwargs):
        if account_id == kept.pk:
            return None
        return real(*args, account_id=account_id, **kwargs)
    monkeypatch.setattr('accounts.erasure.record_activity', record_activity)

    assert purge_erased(now=timezone.now() + timedelta(days=31), batch_size=1) == 1
    assert User.objects.filter(pk=kept.pk).exists()
    assert not User.objects.filter(pk=purged.pk).exists()

    monkeypatch.setattr('accounts.erasure.record_activity', real)
    assert purge_erased(now=timezone.now() + timedelta(days=31)) == 1
    assert not User.objects.filter(pk=kept.pk).exists()


def test_purge_works_in_batches(staff):
    users = [_user(f'user{n}@example.org', phone='') for n in range(5)]
    for user in users:
        erase_user(user, by=staff)

    assert purge_erased(now=timezone.now() + timedelta(days=31), batch_size=2) == 5
    assert not User.objects.filter(pk__in=[u.pk for u in users]).exists()


def test_job_prints_only_the_count(staff, release_jobs, capsys, settings):
    user = _user()
    settings.ACCOUNT_ERASURE_RETENTION_DAYS = 1
    erase_user(user, by=staff, now=timezone.now() - timedelta(days=2))

    call_command('purge_erased_accounts')
    assert capsys.readouterr().out == '1\n'

    call_command('runjob', 'purgeerased')
    out = capsys.readouterr().out
    assert '0\n' in out and 'job purgeerased: ok' in out
    for value in (EMAIL, FIRST_NAME, LAST_NAME, PHONE):
        assert value not in out


def test_jobs_conf_schedules_the_purge_daily():
    job = jobs.load_jobs(jobs.release_jobs_file())['purgeerased']
    assert (job.command, job.args) == ('purge_erased_accounts', ())
    assert job.crontab == '15 4 * * *'


# --- Unconfirmed ---------------------------------------------------------------------------------

def test_unconfirmed_changes_nothing(staff_client):
    user = _user()

    page, again = _erase(staff_client, user, confirm=False)

    assert again.status_code == 200
    content = again.content.decode()
    assert 'Accounts to erase: 1.' in content
    assert 'Tick &quot;I understand this can&#x27;t be undone&quot; to erase.' in content
    assert 'Your own account was skipped.' not in _messages(again)
    user.refresh_from_db()
    assert not user.is_erased and user.is_active
    assert not LogEntry.objects.filter(object_id=str(user.pk)).exists()


# --- No permission -------------------------------------------------------------------------------

def test_staff_without_delete_permission_cannot_erase():
    clerk = User.objects.create_user('clerk@example.net', PASSWORD, is_staff=True)
    clerk.user_permissions.add(*Permission.objects.filter(
        codename__in=['view_user', 'change_user']))
    client = Client()
    client.force_login(clerk)
    user = _user()

    listing = client.get(CHANGELIST)
    _page, done = _erase(client, user)

    assert listing.status_code == 200
    assert 'erase_accounts' not in listing.context['cl'].model_admin.get_actions(
        listing.wsgi_request)
    assert 'erase_accounts' not in listing.content.decode()
    user.refresh_from_db()
    assert not user.is_erased and user.is_active


def test_staff_with_delete_permission_can_erase():
    clerk = User.objects.create_user('clerk@example.net', PASSWORD, is_staff=True)
    clerk.user_permissions.add(*Permission.objects.filter(
        codename__in=['view_user', 'change_user', 'delete_user']))
    client = Client()
    client.force_login(clerk)
    user = _user()

    _erase(client, user)

    assert User.objects.get(pk=user.pk).is_erased


# --- Plain delete and the erased account's page --------------------------------------------------

def test_plain_delete_is_off(staff_client):
    user = _user()

    listing = staff_client.get(CHANGELIST)
    delete_page = staff_client.get(f'{CHANGELIST}{user.pk}/delete/')
    delete_post = staff_client.post(f'{CHANGELIST}{user.pk}/delete/', {'post': 'yes'})
    action = staff_client.post(CHANGELIST, {'action': 'delete_selected',
                                            '_selected_action': [user.pk], 'post': 'yes'})

    assert 'delete_selected' not in listing.content.decode()
    assert delete_page.status_code == 403 and delete_post.status_code == 403
    assert action.status_code in (200, 302)
    assert User.objects.filter(pk=user.pk).exists()


def test_erased_account_is_read_only(staff_client, staff):
    user = _user()
    erase_user(user, by=staff)
    url = f'{CHANGELIST}{user.pk}/change/'

    page = staff_client.get(url)
    post = staff_client.post(url, {'email': 'new@example.org'})
    password = staff_client.get(f'{CHANGELIST}{user.pk}/password/')

    assert page.status_code == 200
    content = page.content.decode()
    for value in (EMAIL, FIRST_NAME, LAST_NAME):
        assert value in content
    assert 'name="email"' not in content and 'name="_save"' not in content
    assert 'deletelink' not in content and f'{user.pk}/delete/' not in content
    assert post.status_code == 403
    assert password.status_code == 403
    assert User.objects.get(pk=user.pk).email == EMAIL


# --- Spanish -------------------------------------------------------------------------------------

def test_admin_texts_in_spanish(staff_client):
    user = _user()
    staff_client.cookies['django_language'] = 'es'

    listing = staff_client.get(CHANGELIST, headers={'accept-language': 'es'})
    page = staff_client.post(CHANGELIST, {'action': 'erase_accounts',
                                          '_selected_action': [user.pk]},
                             headers={'accept-language': 'es'})

    assert 'Borrar cuenta' in listing.content.decode()
    assert 'Borradas' in listing.content.decode()
    content = page.content.decode()
    assert 'Cuentas que se van a borrar: 1.' in content
    assert 'El borrado no se puede deshacer.' in content
    assert 'Entiendo que no se puede deshacer' in content
