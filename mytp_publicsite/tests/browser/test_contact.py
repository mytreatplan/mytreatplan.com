"""The contact form (story 1.5) in Chromium against live_server: the page's script sets the
JS flag and enables Send, htmx posts the form and swaps in its errors, then the thank-you,
which takes the focus; the message is stored and mailed (locmem outbox, sent inline)."""
import pytest

pytest.importorskip('pytest_playwright')

from playwright.sync_api import expect  # noqa: E402

from mytp_publicsite import contact  # noqa: E402
from mytp_publicsite.models import ContactMessage, MailStatus  # noqa: E402

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def quick_and_inline(monkeypatch, settings):
    """No 3-second wait in the test, and the mail sent in the request."""
    settings.CONTACT_INBOX = 'webform@example.test'
    monkeypatch.setattr(contact, 'MIN_FILL_SECONDS', 0)
    monkeypatch.setattr(contact, 'in_background', lambda function: function())


def test_send_a_message_after_fixing_an_error(live_server, page, mailoutbox):
    page.goto(f'{live_server.url}/en/')
    form = page.locator('form[data-contact-form]')
    send = form.get_by_role('button', name='Send', exact=True)
    expect(send).to_be_enabled()  # the script ran
    expect(form.locator('input[name="cf_js"]')).to_have_value('1')
    expect(form.locator('input[name="club"]')).to_have_count(0)

    page.fill('#contact_first_name', 'Ana')
    page.fill('#contact_surname', 'García')
    page.fill('#contact_country', 'Spain')
    page.fill('#contact_phone', '+34 600 123 456')
    page.fill('#contact_email', 'not-an-email')
    form.locator('label[for="contact_aligners_0"]').click()
    send.click()

    # The form comes back with the error next to the email, what was typed kept.
    expect(page.locator('#contact_email_error')).to_have_text('Enter a valid email address.')
    expect(page.locator('#contact_email')).to_be_focused()
    expect(page.locator('#contact_first_name')).to_have_value('Ana')
    expect(page.locator('#contact_aligners_0')).to_be_checked()
    assert ContactMessage.objects.count() == 0

    page.fill('#contact_email', 'ana@example.com')
    page.locator('form[data-contact-form]').get_by_role('button', name='Send').click()

    thanks = page.locator('[data-contact-thanks]')
    expect(thanks).to_have_text('Thank you for your message. We will get back to you soon.',
                                use_inner_text=True)
    expect(thanks).to_be_focused()
    expect(page.locator('form[data-contact-form]')).to_have_count(0)
    message = ContactMessage.objects.get()
    assert (message.first_name, message.aligners, message.mail_status) == (
        'Ana', 'yes', MailStatus.SENT)
    assert len(mailoutbox) == 1 and mailoutbox[0].reply_to == ['ana@example.com']


def test_the_spanish_page_answers_in_spanish(live_server, page):
    page.goto(f'{live_server.url}/es/')
    page.locator('form[data-contact-form]').get_by_role('button', name='Enviar').click()

    expect(page.locator('#contact_first_name_error')).to_have_text('Este campo es obligatorio.')
    expect(page.locator('#contact_first_name')).to_be_focused()


def test_the_limit_error_takes_the_focus(live_server, page):
    """Over a limit no field is invalid: the general error box takes the focus."""
    import time

    from django.core.cache import cache

    cache.set(contact.GLOBAL_KEY, [time.time()] * contact.GLOBAL_LIMIT, timeout=3600)
    page.goto(f'{live_server.url}/en/')
    page.fill('#contact_first_name', 'Ana')
    page.fill('#contact_surname', 'García')
    page.fill('#contact_country', 'Spain')
    page.fill('#contact_phone', '+34 600 123 456')
    page.fill('#contact_email', 'ana@example.com')
    page.locator('form[data-contact-form]').get_by_role('button', name='Send').click()

    box = page.locator('[data-contact-errors]')
    expect(box).to_contain_text('Please try again later.')
    expect(box).to_be_focused()
    assert ContactMessage.objects.count() == 0
