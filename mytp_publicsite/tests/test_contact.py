"""The contact form (story 1.5): every row of the plan's I/O matrix, plus the admin and the
deploy check. The mail goes out once the row is committed (the on-commit callbacks are run
by `post`), in a background thread run inline here (`inline_send`); the mailer is locmem."""
import json
import logging
import re
import smtplib
import time

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.checks import run_checks
from django.core.mail import EmailMessage
from django.db import connection
from django.test import Client

from mytp_publicsite import contact
from mytp_publicsite.checks import NO_CONTACT_INBOX, check_contact_inbox
from mytp_publicsite.models import ContactMessage, MailStatus

pytestmark = pytest.mark.django_db

INBOX = 'webform@example.test'
FROM = 'MyTreatPlan <noreply@example.test>'
VALID = {
    'first_name': 'Ana',
    'middle_name': 'M.',
    'surname': 'García López',
    'country': 'Spain',
    'phone': '+34 (600) 123-456',
    'email': 'ana.garcia@example.com',
    'aligners': 'yes',
    'decision': 'no',
}
# What must never reach a log or a report.
VALUES = ('Ana', 'García', 'Spain', '123-456', 'ana.garcia@example.com')
THANKS = 'Thank you for your message.'
GENERIC = "We couldn&#x27;t process your request. Please try again later."


@pytest.fixture(autouse=True)
def setup(monkeypatch, settings):
    settings.CONTACT_INBOX = INBOX
    settings.DEFAULT_FROM_EMAIL = FROM
    monkeypatch.setattr(contact, 'in_background', lambda function: function())
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def post(client, django_capture_on_commit_callbacks):
    """POST the form as the page's script would (JS flag, a render time 10 s ago), the
    on-commit callbacks run."""
    def send(fields=None, lang='en', ip='203.0.113.7', stamp=None, js='1', browser=None,
             **extra):
        data = {**VALID, **(fields or {}), 'cf_js': js, 'website': '',
                'cf_ts': contact.make_stamp(time.time() - 10) if stamp is None else stamp,
                **extra}
        data = {key: value for key, value in data.items() if value is not None}
        with django_capture_on_commit_callbacks(execute=True):
            return (browser or client).post(f'/{lang}/contact/', data, REMOTE_ADDR=ip,
                                            HTTP_HX_REQUEST='true')
    return send


def _html(response):
    return response.content.decode()


def _assert_thanks(response):
    assert response.status_code == 200
    html = _html(response)
    assert THANKS in html and 'data-contact-thanks' in html
    assert '<form' not in html


def _assert_nothing_kept(mailoutbox):
    assert ContactMessage.objects.count() == 0
    assert mailoutbox == []


# --- The page ---------------------------------------------------------------------------

@pytest.mark.parametrize('lang, texts', [
    ('en', ['First name', 'Middle name or initial (optional)', 'Surname', 'Country',
            'Phone', 'Email', 'Interested in aligners?', 'Makes purchasing decisions?',
            '>Send</button>',
            'We use your details only to answer you. See our <a href="/en/privacy/">'
            'Privacy policy</a>.']),
    ('es', ['Nombre', 'Segundo nombre o inicial (opcional)', 'Apellidos', 'País',
            'Teléfono', 'Correo electrónico', '¿Le interesan los alineadores?',
            '¿Toma decisiones de compra?', '>Enviar</button>',
            'Usamos sus datos solo para responderle. Consulte nuestra <a '
            'href="/es/privacy/">Política de privacidad</a>.']),
])
def test_home_shows_the_form(client, lang, texts):
    html = _html(client.get(f'/{lang}/'))

    form = html[html.index('id="contact-form"'):]
    for text in texts:
        assert text in form, text
    for name in ('first_name', 'middle_name', 'surname', 'country', 'phone', 'email'):
        assert f'name="{name}"' in form
    assert form.count('name="aligners"') == 2 and form.count('name="decision"') == 2
    assert f'hx-post="/{lang}/contact/"' in form
    assert 'name="cf_js" value=""' in form and 'name="website"' in form
    assert re.search(r'name="cf_ts" value="[^"]+"', form)
    assert 'csrfmiddlewaretoken' in form
    assert '<button type="submit" disabled data-contact-send' in form  # until the script
    assert '/static/js/contact-form.js' in html
    # The mailto stays, above the form.
    assert html.index('href="mailto:sayhi@mytreatplan.com"') < html.index('id="contact-form"')


def test_home_does_not_show_the_uae_club_question(client):
    html = _html(client.get('/en/'))

    assert 'name="club"' not in html
    assert 'UAE club' not in html


def test_contact_url_takes_only_posts(client):
    assert client.get('/en/contact/').status_code == 405


# --- Valid ------------------------------------------------------------------------------

def test_a_valid_message_is_stored_and_mailed(post, mailoutbox):
    response = post()

    _assert_thanks(response)
    message = ContactMessage.objects.get()
    assert (message.first_name, message.middle_name, message.surname, message.country,
            message.phone, message.email) == ('Ana', 'M.', 'García López', 'Spain',
                                              '+34 (600) 123-456', 'ana.garcia@example.com')
    assert (message.aligners, message.club, message.decision) == ('yes', '', 'no')
    assert message.mail_status == MailStatus.SENT

    [mail] = mailoutbox
    assert mail.to == [INBOX]
    assert mail.from_email == FROM
    assert mail.reply_to == ['ana.garcia@example.com']
    assert mail.subject == 'New contact from the website: Ana García López'
    sent = message.sent_at.strftime('%Y-%m-%d %H:%M:%S')
    assert mail.body == (
        'First name: Ana\n'
        'Middle name / initial: M.\n'
        'Surname: García López\n'
        'Country: Spain\n'
        'Phone: +34 (600) 123-456\n'
        'Email: ana.garcia@example.com\n'
        'Interested in aligners?: yes\n'
        'Wants to know about the UAE club?: -\n'
        'Makes purchasing decisions?: no\n'
        '\n'
        f'Sent from mytreatplan.com on {sent} UTC\n')
    assert mail.content_subtype == 'plain'


def test_empty_optional_fields_read_as_a_dash(post, mailoutbox):
    post({'middle_name': '', 'aligners': None, 'decision': None})

    body = mailoutbox[0].body
    for label in ('Middle name / initial', 'Interested in aligners?',
                  'Wants to know about the UAE club?', 'Makes purchasing decisions?'):
        assert f'{label}: -\n' in body


def test_personal_fields_are_encrypted_at_rest(post):
    post()

    with connection.cursor() as cursor:
        cursor.execute('SELECT first_name, middle_name, surname, country, phone, email '
                       f'FROM {ContactMessage._meta.db_table}')
        row = cursor.fetchone()
    for value in row:
        assert value.startswith('enc:v1:')
    stored = ' '.join(row)
    for value in VALUES:
        assert value not in stored


def test_control_characters_become_spaces_then_trimmed(post, mailoutbox):
    post({'first_name': '\tAna\r\nMaría\x00 ', 'surname': 'López\x7f'})

    message = ContactMessage.objects.get()
    assert message.first_name == 'Ana  María'
    assert message.surname == 'López'
    assert mailoutbox[0].subject == 'New contact from the website: Ana  María López'


def test_the_mail_goes_only_once_the_row_is_committed(client, monkeypatch,
                                                      django_capture_on_commit_callbacks):
    jobs = []
    monkeypatch.setattr(contact, 'in_background', jobs.append)
    data = {**VALID, 'cf_js': '1', 'website': '', 'cf_ts': contact.make_stamp(time.time() - 10)}

    with django_capture_on_commit_callbacks() as callbacks:
        client.post('/en/contact/', data)
        assert jobs == []  # nothing before the commit

    assert len(callbacks) == 1
    callbacks[0]()
    assert len(jobs) == 1


# --- Invalid ----------------------------------------------------------------------------

@pytest.mark.parametrize('fields, field, error', [
    ({'first_name': ''}, 'first_name', 'This field is required.'),
    ({'surname': '   '}, 'surname', 'This field is required.'),
    ({'country': ''}, 'country', 'This field is required.'),
    ({'phone': ''}, 'phone', 'This field is required.'),
    ({'email': ''}, 'email', 'This field is required.'),
    ({'first_name': 'A' * 81}, 'first_name', 'at most 80 characters'),
    ({'middle_name': 'A' * 81}, 'middle_name', 'at most 80 characters'),
    ({'country': 'A' * 81}, 'country', 'at most 80 characters'),
    ({'email': 'a' * 250 + '@x.es'}, 'email', 'at most 254 characters'),
    ({'email': 'not-an-email'}, 'email', 'Enter a valid email address.'),
    ({'phone': '12345'}, 'phone', 'Enter a valid phone number'),
    ({'phone': '1' * 33}, 'phone', 'at most 32 characters'),
    ({'phone': '600 123 456 ext'}, 'phone', 'Enter a valid phone number'),
    ({'aligners': 'maybe'}, 'aligners', 'Select a valid choice.'),
    ({'decision': 'YES'}, 'decision', 'Select a valid choice.'),
    ({'club': 'si'}, 'club', 'Select a valid choice.'),
])
def test_invalid_fields_show_their_errors(post, mailoutbox, fields, field, error):
    response = post(fields)

    assert response.status_code == 200
    html = _html(response)
    assert 'data-contact-form' in html and THANKS not in html
    match = re.search(rf'<ul id="contact_{field}_error"[^>]*>(.*?)</ul>', html, re.S)
    assert match and error in match.group(1), html
    _assert_nothing_kept(mailoutbox)


def test_an_invalid_form_keeps_what_was_typed_and_its_render_time(post):
    stamp = contact.make_stamp(time.time() - 10)

    html = _html(post({'email': 'nope'}, stamp=stamp))

    assert 'value="Ana"' in html and 'value="García López"' in html
    assert 'id="contact_aligners_0" checked' in html
    assert f'name="cf_ts" value="{stamp}"' in html
    assert 'name="cf_js" value=""' in html  # the script sets it again after the swap


def test_a_corrected_form_is_accepted_at_once(post, mailoutbox):
    """The render time stays the first one, so a quick correction is not "too fast"."""
    stamp = contact.make_stamp(time.time() - 4)
    post({'email': 'nope'}, stamp=stamp)

    _assert_thanks(post(stamp=stamp))
    assert ContactMessage.objects.count() == 1 and len(mailoutbox) == 1


# --- Bots: the same thank-you, nothing stored or sent -------------------------------------

@pytest.mark.parametrize('extra', [
    {'website': 'https://spam.example'},
    {'website': ' x '},
    {'js': ''},
    {'js': None},
    {'js': 'true'},
    {'stamp': contact.make_stamp(time.time() + 10_000)},  # in the future
    {'stamp': ''},
    {'cf_ts': None},
    {'stamp': 'tampered'},
    {'stamp': contact.make_stamp(time.time() - 10)[:-2] + 'xx'},
], ids=['honeypot', 'honeypot-spaces', 'no-js', 'js-missing', 'js-other', 'future',
        'stamp-empty', 'stamp-missing', 'stamp-garbage', 'stamp-bad-signature'])
def test_bot_cases_get_the_thank_you(post, mailoutbox, extra):
    _assert_thanks(post(**extra))
    _assert_nothing_kept(mailoutbox)


def test_too_fast_and_too_old_are_bot_cases(post, mailoutbox):
    now = time.time()
    for age in (0, 2.5, 6 * 3600 + 5):
        _assert_thanks(post(stamp=contact.make_stamp(now - age)))
    _assert_nothing_kept(mailoutbox)

    for age in (3.5, 6 * 3600 - 60):
        _assert_thanks(post(stamp=contact.make_stamp(now - age)))
    assert ContactMessage.objects.count() == 2


def test_a_stamp_signed_for_something_else_is_a_bot_case(post, mailoutbox):
    from django.core import signing

    _assert_thanks(post(stamp=signing.dumps(time.time() - 10)))  # another salt
    _assert_thanks(post(stamp=signing.dumps('soon', salt=contact.STAMP_SALT)))
    _assert_nothing_kept(mailoutbox)


@pytest.mark.parametrize('field', ['first_name', 'middle_name', 'surname', 'country'])
@pytest.mark.parametrize('link', ['http://spam.example', 'see HTTPS://x.y', 'www.spam.example',
                                  '[URL=x]', '<a href=x>'])
def test_a_url_in_a_name_or_the_country_is_a_bot_case(post, mailoutbox, field, link):
    _assert_thanks(post({field: link[:80]}))
    _assert_nothing_kept(mailoutbox)


def test_a_bot_case_with_errors_still_gets_the_thank_you(post, mailoutbox):
    _assert_thanks(post({'email': ''}, js=''))
    _assert_nothing_kept(mailoutbox)


@pytest.mark.parametrize('fields', [
    {'first_name': 'www.spam.example', 'email': 'not-an-email'},
    {'country': 'Visit http://spam.example ' + 'x' * 80},
    {'surname': '\x00<a\nhref=x>', 'phone': ''},
], ids=['link-and-bad-email', 'link-over-80', 'link-after-cleaning-and-missing-phone'])
def test_a_url_is_a_bot_case_whatever_the_other_errors(post, mailoutbox, fields):
    _assert_thanks(post(fields))
    _assert_nothing_kept(mailoutbox)


def test_bot_cases_do_not_count_toward_the_limits(post, mailoutbox):
    for _ in range(contact.IP_LIMIT + 2):
        post(js='')
    for _ in range(contact.IP_LIMIT):
        _assert_thanks(post())
    assert ContactMessage.objects.count() == contact.IP_LIMIT


# --- Limits -------------------------------------------------------------------------------

@pytest.fixture
def blocks(monkeypatch):
    reported = []
    monkeypatch.setattr(contact, '_report_block', reported.append)
    return reported


def test_the_sixth_message_from_one_ip_within_the_hour_is_refused(post, mailoutbox, blocks):
    for _ in range(contact.IP_LIMIT):
        _assert_thanks(post())

    response = post()
    html = _html(response)
    assert response.status_code == 200 and GENERIC in html and 'role="alert"' in html
    assert 'value="Ana"' in html  # what was typed stays
    assert ContactMessage.objects.count() == contact.IP_LIMIT
    assert len(mailoutbox) == contact.IP_LIMIT
    assert blocks == [contact.IP_BLOCKED]

    post()  # a later block in the same hour is not reported again
    assert blocks == [contact.IP_BLOCKED]
    # Another IP still passes.
    _assert_thanks(post(ip='198.51.100.20'))
    assert ContactMessage.objects.count() == contact.IP_LIMIT + 1


def test_the_ip_window_slides(post, monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(contact, '_now', lambda: now[0])
    for _ in range(contact.IP_LIMIT):
        post(stamp=contact.make_stamp(now[0] - 10))
    now[0] += contact.WINDOW_SECONDS - 5
    assert GENERIC in _html(post(stamp=contact.make_stamp(now[0] - 10)))

    now[0] += 10
    _assert_thanks(post(stamp=contact.make_stamp(now[0] - 10)))


def test_the_sixty_first_message_overall_within_the_hour_is_refused(post, mailoutbox, blocks):
    now = time.time()
    cache.set(contact.GLOBAL_KEY, [now - 100] * (contact.GLOBAL_LIMIT - 1) + [now - 4000],
              timeout=3600)
    _assert_thanks(post(ip='198.51.100.1'))  # the 60th (one is older than an hour)

    response = post(ip='198.51.100.2')
    assert GENERIC in _html(response)
    assert ContactMessage.objects.count() == 1 and len(mailoutbox) == 1
    assert blocks == [contact.GLOBAL_BLOCKED]
    post(ip='198.51.100.2')
    post(ip='198.51.100.3')  # another IP's first block is reported
    assert blocks == [contact.GLOBAL_BLOCKED, contact.GLOBAL_BLOCKED]


def test_the_ip_is_never_stored_plain(post):
    post(ip='203.0.113.77')

    with connection.cursor() as cursor:
        cursor.execute('SELECT cache_key, value FROM django_cache')
        rows = cursor.fetchall()
    assert rows
    assert '203.0.113.77' not in json.dumps(rows, default=str)


def test_a_block_report_carries_no_values(post, sentry_capture, caplog):
    with caplog.at_level(logging.WARNING):
        for _ in range(contact.IP_LIMIT + 1):
            post()

    messages = [event.get('logentry', {}).get('message') or event.get('message')
                for event in sentry_capture.events]
    assert messages == ['Contact form rate limit: ip']
    text = json.dumps(sentry_capture.events, default=str) + caplog.text
    for value in (*VALUES, '203.0.113.7'):
        assert value not in text


def test_a_cache_failure_lets_the_message_pass(post, mailoutbox, monkeypatch, sentry_capture):
    def broken(*args, **kwargs):
        raise RuntimeError('cache down')
    monkeypatch.setattr(contact, 'cache_mutex', broken)

    _assert_thanks(post())

    assert ContactMessage.objects.count() == 1 and len(mailoutbox) == 1
    messages = [event.get('logentry', {}).get('message') or event.get('message')
                for event in sentry_capture.events]
    assert 'Contact form error: reserve (RuntimeError)' in messages


def test_a_busy_lock_blocks_without_a_report(post, mailoutbox, monkeypatch, sentry_capture,
                                            blocks):
    """A lock not got within the wait is not a cache failure: the message is refused, as
    over a limit, so the limits hold under load."""
    from accounts.cache_mutex import CounterBusy

    def busy(key, **kwargs):
        raise CounterBusy(key)
    monkeypatch.setattr(contact, 'cache_mutex', busy)

    response = post()

    assert GENERIC in _html(response) and 'data-contact-errors' in _html(response)
    _assert_nothing_kept(mailoutbox)
    assert sentry_capture.events == [] and blocks == []


# --- Mail failure -------------------------------------------------------------------------

def test_a_failed_send_keeps_the_message_and_is_reported_without_values(
        post, mailoutbox, monkeypatch, sentry_capture, caplog):
    def refuse(self, *args, **kwargs):
        raise smtplib.SMTPRecipientsRefused({INBOX: (550, b'ana.garcia@example.com refused')})
    monkeypatch.setattr(EmailMessage, 'send', refuse)

    with caplog.at_level(logging.WARNING):
        response = post()

    _assert_thanks(response)
    assert ContactMessage.objects.get().mail_status == MailStatus.FAILED
    messages = [event.get('logentry', {}).get('message') or event.get('message')
                for event in sentry_capture.events]
    assert messages == ['Contact form error: send (SMTPRecipientsRefused)']
    text = json.dumps(sentry_capture.events, default=str) + caplog.text
    for value in VALUES:
        assert value not in text


def test_an_empty_inbox_keeps_the_message_unsent(post, mailoutbox, settings):
    settings.CONTACT_INBOX = ''

    _assert_thanks(post())

    assert ContactMessage.objects.get().mail_status == MailStatus.FAILED
    assert mailoutbox == []


def test_nothing_logs_the_values(post, caplog):
    with caplog.at_level(logging.DEBUG):
        post()
        post({'email': 'bad'})
        post({'first_name': 'www.x'})
    for value in VALUES:
        assert value not in caplog.text


# --- Spanish ----------------------------------------------------------------------------

def test_spanish_errors_and_thank_you(post):
    html = _html(post({'first_name': '', 'phone': '12'}, lang='es'))

    assert 'Este campo es obligatorio.' in html
    assert 'Introduzca un número de teléfono válido' in html
    assert 'Nombre' in html and 'Enviar' in html
    assert 'Usamos sus datos solo para responderle.' in html

    response = post(lang='es')
    assert 'Gracias por su mensaje.' in _html(response)
    assert ContactMessage.objects.count() == 1


def test_spanish_limit_error(post):
    for _ in range(contact.IP_LIMIT):
        post(lang='es')

    assert 'Inténtelo de nuevo más tarde.' in _html(post(lang='es'))


# --- The hidden UAE club question ----------------------------------------------------------

def test_a_posted_club_answer_is_validated_and_stored(post, mailoutbox):
    _assert_thanks(post({'club': 'yes'}))

    assert ContactMessage.objects.get().club == 'yes'
    assert 'Wants to know about the UAE club?: yes\n' in mailoutbox[0].body


# --- The admin --------------------------------------------------------------------------

@pytest.fixture
def staff_client():
    user = get_user_model().objects.create_superuser('staff@example.net',
                                                     'amber-glacier-meadow-77')
    browser = Client()
    browser.force_login(user)
    return browser


def test_the_admin_lists_and_shows_a_message_read_only(post, staff_client):
    post()
    message = ContactMessage.objects.get()

    listing = _html(staff_client.get('/admin/mytp_publicsite/contactmessage/'))
    assert 'Ana M. García López' in listing and 'Spain' in listing

    response = staff_client.get(f'/admin/mytp_publicsite/contactmessage/{message.pk}/change/')
    assert response.status_code == 200
    html = _html(response)
    for value in ('Ana', 'M.', 'García López', 'Spain', '+34 (600) 123-456',
                  'ana.garcia@example.com', 'Sent'):
        assert f'<div class="readonly">{value}</div>' in html, value
    for name in ('first_name', 'surname', 'email', 'phone', 'country'):
        assert f'name="{name}"' not in html
    assert 'name="_save"' not in html

    # Nothing can be changed or added.
    staff_client.post(f'/admin/mytp_publicsite/contactmessage/{message.pk}/change/',
                      {'first_name': 'Eve'})
    message.refresh_from_db()
    assert message.first_name == 'Ana'
    assert staff_client.get('/admin/mytp_publicsite/contactmessage/add/').status_code == 403


def test_the_admin_filters_unsent_messages(post, staff_client, monkeypatch):
    post()
    monkeypatch.setattr(EmailMessage, 'send', lambda self, *a, **k: (_ for _ in ()).throw(
        smtplib.SMTPException('down')))
    post({'first_name': 'Bea'})

    html = _html(staff_client.get('/admin/mytp_publicsite/contactmessage/?mail_status=failed'))
    assert 'Bea' in html and 'Ana M.' not in html


def test_the_admin_deletes_a_message_and_logs_no_values(post, staff_client):
    from django.contrib.admin.models import LogEntry

    post()
    message = ContactMessage.objects.get()

    response = staff_client.post(
        f'/admin/mytp_publicsite/contactmessage/{message.pk}/delete/', {'post': 'yes'})

    assert response.status_code == 302
    assert ContactMessage.objects.count() == 0
    entry = LogEntry.objects.get()
    assert 'Ana' not in entry.object_repr and 'García' not in entry.object_repr


# --- The deploy check -------------------------------------------------------------------

@pytest.mark.parametrize('inbox', ['', '   '])
def test_an_empty_inbox_fails_the_deploy_check(settings, inbox):
    settings.DEBUG = False
    settings.CONTACT_INBOX = inbox

    errors = check_contact_inbox(None)

    assert [e.id for e in errors] == [NO_CONTACT_INBOX]
    assert 'CONTACT_INBOX' in errors[0].msg
    assert NO_CONTACT_INBOX in {e.id for e in run_checks(include_deployment_checks=True)}
    assert NO_CONTACT_INBOX not in {e.id for e in run_checks()}


def test_an_inbox_passes_the_deploy_check(settings):
    settings.DEBUG = False
    settings.CONTACT_INBOX = 'webform@mytreatplan.com'

    assert check_contact_inbox(None) == []


def test_the_inbox_check_is_skipped_with_debug_on(settings):
    settings.DEBUG = True
    settings.CONTACT_INBOX = ''

    assert check_contact_inbox(None) == []


def test_the_inbox_setting_has_no_default(monkeypatch):
    import runpy

    from django.conf import settings as django_settings

    monkeypatch.setenv('CONTACT_INBOX', '')
    path = str(django_settings.BASE_DIR / 'mytreatplan' / 'settings.py')
    assert runpy.run_path(path)['CONTACT_INBOX'] == ''
    monkeypatch.setenv('CONTACT_INBOX', ' webform@mytreatplan.com ')
    assert runpy.run_path(path)['CONTACT_INBOX'] == 'webform@mytreatplan.com'
