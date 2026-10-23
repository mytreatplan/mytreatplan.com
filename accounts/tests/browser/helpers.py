"""The login browser tests' shared steps, in English or Spanish: signup through the real
pages, the login (the password, the choice of contact, the code), the logout from the
header, and the password reset link from the test mail outbox.

Codes come from the fake provider (accounts.tests.helpers._right_code), reset links from
the outbox (the fragment link of accounts.password_reset.reset_url), never from the
pages. Signup's steps are signup/tests/browser/helpers.py's."""
import re

from playwright.sync_api import expect

from signup.limits import COOLDOWN_SECONDS
from signup.tests.browser.helpers import (enter_code, fill_email, follow, mobile_code,
                                          new_page, press, to_mobile_step, wait_for_token)
from signup.tests.helpers import EMAIL_ADDRESS, MOBILE_E164, PASSWORD

from ..helpers import _right_code

# What the person sees, by language (the msgstr of locale/es/LC_MESSAGES/django.po).
TEXTS = {
    'en': {
        'create': 'Create account', 'log_out': 'Log out',
        'logged_out': 'You have logged out.', 'login': 'Log in',
        'verify': "Confirm it's you", 'code': 'Enter your code',
        'remember': 'Remember me on this device/browser for 90 days',
        'welcome': 'Welcome to MyTreatPlan',
        'recover': 'Recover your password', 'reset': 'Reset your password',
        'send_link': 'Send the link', 'sent': 'Check your email',
        'new_password': 'Choose a new password', 'change': 'Change password',
        'changed': 'Your password has been changed. Please log in.',
        'mail_subject': 'Reset your MyTreatPlan password',
    },
    'es': {
        'create': 'Crear cuenta', 'log_out': 'Cerrar sesión',
        'logged_out': 'Ha cerrado la sesión.', 'login': 'Iniciar sesión',
        'verify': 'Confirme que es usted', 'code': 'Introduzca su código',
        'remember': 'Recordarme en este dispositivo/navegador durante 90 días',
        'welcome': 'Le damos la bienvenida a MyTreatPlan',
        'recover': 'Recuperar su contraseña', 'reset': 'Restablecer su contraseña',
        'send_link': 'Enviar el enlace', 'sent': 'Revise su correo electrónico',
        'new_password': 'Elija una nueva contraseña', 'change': 'Cambiar la contraseña',
        'changed': 'Su contraseña se ha cambiado. Inicie sesión.',
        'mail_subject': 'Restablezca su contraseña de MyTreatPlan',
    },
}

NEW_PASSWORD = 'amber-glacier-meadow-77'


def at(page, live_server, lang, path):
    """The page is at /<lang>/<path> (with no query string)."""
    expect(page).to_have_url(f'{live_server.url}/{lang}/{path}')


def status(page, text):
    """The message (Django's messages framework) shown on the page."""
    return page.get_by_role('status').filter(has_text=text)


def sign_up(page, live_server, lang='en', phone=MOBILE_E164):
    """A whole signup through the pages: the Doctor (mobile `phone`, E.164 in Spain)
    lands logged in on welcome."""
    step1 = {} if phone == MOBILE_E164 else {'phone': phone.removeprefix('+34')}
    to_mobile_step(page, live_server, lang=lang, **step1)
    enter_code(page, mobile_code(phone), TEXTS[lang]['create'])
    at(page, live_server, lang, 'welcome/')


def log_out(page, live_server, lang='en'):
    """Log out from the header's button: the login page, with the message."""
    press(page, TEXTS[lang]['log_out'])
    at(page, live_server, lang, 'login/')
    expect(status(page, TEXTS[lang]['logged_out'])).to_be_visible()


def submit_password(page, lang='en', email=EMAIL_ADDRESS, password=PASSWORD):
    """The login form, sent once the Turnstile stub's token is in."""
    page.fill('#id_email', email)
    page.fill('#id_password', password)
    wait_for_token(page)
    press(page, TEXTS[lang]['login'])


def choose(page, contact):
    """On the choice page: send the code to 'email' or 'mobile'."""
    new_page(page, page.locator(f'button[value="{contact}"]').click)


def log_in(page, live_server, lang='en', contact='email', phone=MOBILE_E164,
           remember=False, password=PASSWORD):
    """The password, the choice of `contact` and its code (ticking "Remember me" with
    `remember`): the welcome page."""
    t = TEXTS[lang]
    submit_password(page, lang, password=password)
    at(page, live_server, lang, 'login/verify/')
    expect(page.locator('h1')).to_have_text(t['verify'])
    choose(page, contact)
    at(page, live_server, lang, 'login/code/')
    expect(page.locator('h1')).to_have_text(t['code'])
    if remember:
        page.get_by_label(t['remember']).check()
    enter_code(page, _right_code(contact, phone=phone), t['login'])
    at(page, live_server, lang, 'welcome/')
    expect(page.locator('h1')).to_have_text(t['welcome'])


def wait_for_mail(page, mailoutbox):
    """The reset mail, sent by the request's background thread to the test outbox."""
    for _ in range(100):
        if mailoutbox:
            return mailoutbox[-1]
        page.wait_for_timeout(100)
    raise AssertionError('no reset mail arrived')


def reset_link(page, mailoutbox, live_server, lang='en'):
    """The link in the reset mail: the reset page, the uid and token in its fragment."""
    message = wait_for_mail(page, mailoutbox)
    assert message.subject == TEXTS[lang]['mail_subject']
    pattern = rf'{re.escape(live_server.url)}/{lang}/reset/#\S+'
    return re.search(pattern, message.body).group(0)


def request_reset(page, live_server, lang='en', email=EMAIL_ADDRESS):
    """On the request page: ask for the link; the one answer follows."""
    t = TEXTS[lang]
    at(page, live_server, lang, 'password-reset/')
    expect(page.locator('h1')).to_have_text(t['reset'])
    wait_for_token(page)
    page.fill('#id_email', email)
    press(page, t['send_link'])
    at(page, live_server, lang, 'password-reset/sent/')
    expect(page.locator('h1')).to_have_text(t['sent'])


def set_new_password(page, live_server, link, lang='en', password=NEW_PASSWORD):
    """Open the emailed link and set `password`: the login page, with the message."""
    t = TEXTS[lang]
    page.goto(link)
    at(page, live_server, lang, 'password-reset/new/')
    expect(page.locator('h1')).to_have_text(t['new_password'])
    page.fill('#id_password1', password)
    page.fill('#id_password2', password)
    press(page, t['change'])
    at(page, live_server, lang, 'login/')
    expect(status(page, t['changed'])).to_be_visible()


def success_journey(page, live_server, mailoutbox, clock, lang='en'):
    """The spec's success signal through the pages in `lang`: signup; a login with the
    password and the email code, "Remember me" ticked; after a logout, a login without a
    code; a logout; signup's recovery link (the live email check's), the reset through
    the mailed link; a login with the new password and a code. Ends logged in on
    welcome. Needs SITE_URL set to live_server's URL (the mailed link's base)."""
    t = TEXTS[lang]
    sign_up(page, live_server, lang)
    log_out(page, live_server, lang)

    # The second factor by email, with "Remember me".
    log_in(page, live_server, lang, remember=True)
    log_out(page, live_server, lang)

    # The remembered device: the password alone lands on welcome.
    submit_password(page, lang)
    at(page, live_server, lang, 'welcome/')
    expect(page.locator('h1')).to_have_text(t['welcome'])
    log_out(page, live_server, lang)

    # A forgotten password: signup's recovery link opens the request page.
    page.goto(f'{live_server.url}/{lang}/signup/')
    wait_for_token(page)
    fill_email(page, EMAIL_ADDRESS)
    expect(page.locator('#email-check [data-recovery-link]')).to_have_text(t['recover'])
    follow(page, t['recover'])
    request_reset(page, live_server, lang)
    set_new_password(page, live_server, reset_link(page, mailoutbox, live_server, lang), lang)

    # The new password; the reset forgot the device, so a code again (once the first
    # login code's resend cooldown has passed).
    clock.advance(COOLDOWN_SECONDS)
    log_in(page, live_server, lang, password=NEW_PASSWORD)
