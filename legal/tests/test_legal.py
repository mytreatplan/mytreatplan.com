"""Story 2.6: legal documents (versions, current version, drafts), the admin rules for
published versions, the Markdown rendering and the version 1 drafts."""
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.utils import timezone, translation

from legal.models import LegalChoice, LegalDocument
from legal.rendering import render_markdown

pytestmark = pytest.mark.django_db

User = get_user_model()
KINDS = ['terms', 'conditions', 'ormco_tps', 'commercial_consent']


def _publish(kind='terms', published_at=None, **fields):
    return LegalDocument.objects.create(
        kind=kind, title_en=fields.pop('title_en', 'Terms v-next'),
        title_es=fields.pop('title_es', 'Términos v-next'), body_en='Body', body_es='Cuerpo',
        published_at=published_at or timezone.now(), **fields)


# --- Version 1 drafts (data migration) -------------------------------------------------

def test_version_1_of_each_kind_is_published_as_a_draft():
    current = LegalDocument.objects.current_by_kind()
    assert sorted(current) == sorted(KINDS)
    for document in current.values():
        assert document.version == 1 and document.is_published()
        assert '(draft)' in document.title_en and '(borrador)' in document.title_es
        assert '**Draft.**' in document.body_en and '**Borrador.**' in document.body_es


def test_commercial_consent_draft_names_the_group_the_channels_and_no_sharing():
    consent = LegalDocument.objects.current('commercial_consent')
    for body in (consent.body_en, consent.body_es):
        for company in ('MyTPDSO Limited', 'MyTPDSO Spain SL', 'MyTPDSO LLC-FZ'):
            assert company in body
        assert 'WhatsApp' in body and 'SMS' in body
    assert 'Ireland' in consent.body_en and 'Spain' in consent.body_en
    assert 'United Arab Emirates' in consent.body_en
    assert 'email' in consent.body_en and 'phone' in consent.body_en
    assert 'never shared outside' in consent.body_en
    assert 'correo electrónico' in consent.body_es and 'teléfono' in consent.body_es
    assert 'nunca se comparten fuera' in consent.body_es


# --- Versions --------------------------------------------------------------------------

def test_version_is_previous_plus_one_per_kind():
    assert _publish('terms').version == 2
    assert _publish('terms').version == 3
    assert _publish('conditions').version == 2


def test_kind_and_version_are_unique():
    with pytest.raises(IntegrityError), transaction.atomic():
        LegalDocument.objects.create(kind='terms', version=1, title_en='x', title_es='x',
                                     body_en='x', body_es='x')


def test_new_published_version_is_current_at_once():
    v2 = _publish('terms')
    assert LegalDocument.objects.current('terms') == v2
    assert LegalDocument.objects.current_by_kind()['terms'] == v2


def test_future_or_unset_published_at_is_a_draft():
    v2 = _publish('terms')
    _publish('terms', published_at=timezone.now() + timedelta(days=1))  # v3, future
    LegalDocument.objects.create(kind='terms', title_en='x', title_es='x', body_en='x',
                                 body_es='x')  # v4, no date
    assert LegalDocument.objects.current('terms') == v2


def test_title_and_body_follow_the_language():
    document = _publish('terms')
    with translation.override('es'):
        assert (document.title, document.body) == ('Términos v-next', 'Cuerpo')
    with translation.override('en'):
        assert (document.title, document.body) == ('Terms v-next', 'Body')


# --- Choices ---------------------------------------------------------------------------

def test_a_choice_has_exactly_one_owner():
    document = LegalDocument.objects.current('terms')
    with pytest.raises(IntegrityError), transaction.atomic():
        LegalChoice.objects.create(document=document, accepted=True, decided_at=timezone.now())


def test_a_users_choices_protect_the_user_and_the_document():
    user = User.objects.create_user('ana@example.com', 'violet-orbit-canyon-42')
    document = LegalDocument.objects.current('terms')
    LegalChoice.objects.create(document=document, accepted=True, decided_at=timezone.now(),
                               user=user)
    with pytest.raises(ProtectedError):
        user.delete()
    with pytest.raises(ProtectedError):
        document.delete()


# --- Rendering -------------------------------------------------------------------------

def test_markdown_is_rendered():
    html = render_markdown(
        '## Heading\n\nSome *emphasis* and **strong**, a [link](https://example.com).\n\n'
        '- one\n- two\n\nThen:\n\n1. first\n\n| a | b |\n|---|---|\n| 1 | 2 |\n')
    for fragment in ('<h2>Heading</h2>', '<em>emphasis</em>', '<strong>strong</strong>',
                     '<ul>', '<li>one</li>', '<ol>', '<table>', '<th>a</th>', '<td>1</td>'):
        assert fragment in html
    assert 'href="https://example.com"' in html and 'rel="noopener noreferrer nofollow"' in html


@pytest.mark.parametrize('source, forbidden', [
    ('<script>alert(1)</script>', '<script'),
    ('<style>body{}</style>', '<style'),
    ('<div onclick="x()">raw</div>', '<div'),
    ('Text <b onmouseover="x()">bold</b>', '<b '),
    ('<img src=x onerror=alert(1)>', '<img'),
    ('[click](javascript:alert(1))', 'javascript:'),
    ('<iframe src="https://example.com"></iframe>', '<iframe'),
])
def test_markdown_is_sanitized(source, forbidden):
    assert forbidden not in render_markdown(source)


def test_raw_html_shows_as_text():
    assert '&lt;script&gt;' in render_markdown('<script>alert(1)</script>')


def test_table_alignment_style_is_dropped():
    html = render_markdown('| a |\n|:-:|\n| 1 |\n')
    assert '<table>' in html and 'style=' not in html


# --- Admin -----------------------------------------------------------------------------

@pytest.fixture
def admin_client(client):
    admin = User.objects.create_superuser('admin@example.com', 'violet-orbit-canyon-42')
    client.force_login(admin)
    return client


ADMIN = '/admin/legal/legaldocument/'


def test_admin_adds_a_version_with_the_next_number(admin_client):
    response = admin_client.post(f'{ADMIN}add/', {
        'kind': 'terms', 'title_en': 'Terms of Use', 'title_es': 'Términos de uso',
        'body_en': 'Final', 'body_es': 'Final', 'published_at_0': '2000-01-01',
        'published_at_1': '00:00:00'})
    assert response.status_code == 302, response.context['adminform'].form.errors
    document = LegalDocument.objects.get(kind='terms', version=2)
    assert LegalDocument.objects.current('terms') == document


def test_admin_shows_a_published_version_read_only_without_delete(admin_client):
    published = LegalDocument.objects.current('terms')
    page = admin_client.get(f'{ADMIN}{published.pk}/change/')
    assert page.status_code == 200
    html = page.content.decode()
    assert 'name="title_en"' not in html and 'name="body_en"' not in html
    assert f'{ADMIN}{published.pk}/delete/' not in html

    response = admin_client.post(f'{ADMIN}{published.pk}/change/', {
        'title_en': 'Changed', 'title_es': 'Cambiado', 'body_en': 'x', 'body_es': 'x'})
    assert response.status_code == 403
    assert admin_client.post(f'{ADMIN}{published.pk}/delete/', {'post': 'yes'}).status_code == 403
    published.refresh_from_db()
    assert published.title_en != 'Changed'


def test_admin_can_change_and_delete_a_draft(admin_client):
    draft = _publish('terms', published_at=timezone.now() + timedelta(days=1))
    html = admin_client.get(f'{ADMIN}{draft.pk}/change/').content.decode()
    assert 'name="title_en"' in html and 'name="kind"' not in html  # kind is fixed
    assert admin_client.post(f'{ADMIN}{draft.pk}/delete/', {'post': 'yes'}).status_code == 302
    assert not LegalDocument.objects.filter(pk=draft.pk).exists()


def test_admin_list_has_no_bulk_delete(admin_client):
    html = admin_client.get(ADMIN).content.decode()
    assert 'delete_selected' not in html
    assert 'current' in html


def test_admin_preview_renders_sanitized_markdown(admin_client):
    draft = _publish('terms', published_at=timezone.now() + timedelta(days=1))
    draft.body_en = '## Preview heading\n\n<script>alert(1)</script>'
    draft.save()
    html = admin_client.get(f'{ADMIN}{draft.pk}/change/').content.decode()
    assert '<h2>Preview heading</h2>' in html and '<script>alert(1)' not in html
