"""Signup entry 14: the spec's Success signal in Chromium against live_server, in English
and in Spanish. A new Doctor completes the four steps through the real pages (htmx, the
vendored JavaScript, the built CSS), verifies the email and the mobile with the fake
provider's codes, and lands logged in on the welcome screen. The other parts of the
signal (a second attempt blocked with a recovery link, the 24 h purge and the 30-day
anonymization) are policy rows 3, 4 and 2 in test_failure_policies.py."""
import re

import pytest

pytest.importorskip('pytest_playwright')

from django.contrib.auth import get_user_model  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

from legal.models import LegalChoice  # noqa: E402
from signup.models import PendingRegistration  # noqa: E402

from ..helpers import MOBILE_E164, NORMALIZED  # noqa: E402
from .helpers import (email_code, enter_code, fill_step1, mobile_code, press,  # noqa: E402
                      url)

pytestmark = [
    pytest.mark.browser,
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
    pytest.mark.usefixtures('portal_urls'),
]

User = get_user_model()

TEXTS = {
    'en': {
        'step': 'Step {} of 4', 'continue': 'Continue', 'create': 'Create account',
        'account': 'Create your account', 'legal': 'Terms and consent',
        'terms_required': 'You must accept the Terms to continue.',
        'conditions_required': 'You must accept the Conditions to continue.',
        'email': 'Check your email', 'mobile': 'Check your mobile',
        'whatsapp': 'by WhatsApp', 'welcome': 'Welcome to MyTreatPlan',
        'signed_in': 'Your account is ready. You are signed in as',
    },
    'es': {
        'step': 'Paso {} de 4', 'continue': 'Continuar', 'create': 'Crear cuenta',
        'account': 'Cree su cuenta', 'legal': 'Términos y consentimiento',
        'terms_required': 'Debe aceptar los Términos para continuar.',
        'conditions_required': 'Debe aceptar las Condiciones para continuar.',
        'email': 'Revise su correo electrónico', 'mobile': 'Revise su móvil',
        'whatsapp': 'por WhatsApp', 'welcome': 'Le damos la bienvenida a MyTreatPlan',
        'signed_in': 'Su cuenta está lista. Ha iniciado sesión como',
    },
}


def _assert_real_assets(page):
    """The page runs on the built CSS and the vendored htmx, not bare HTML."""
    assert page.evaluate("""() => [...document.styleSheets].some((sheet) =>
        sheet.href && sheet.href.includes('/css/site.css') && sheet.cssRules.length > 0)""")
    assert page.evaluate('() => Boolean(window.htmx)')


def _heading(page, text, step=None, lang='en'):
    expect(page.locator('h1')).to_have_text(text)
    if step is not None:
        expect(page.get_by_text(TEXTS[lang]['step'].format(step), exact=True)).to_be_visible()


@pytest.mark.parametrize('lang', ['en', 'es'])
def test_success_signal(live_server, page, lang):
    """Success signal (spec): the four steps in `lang`, both codes, and the welcome screen,
    logged in. At the legal step the Terms and the Conditions are required; the Ormco
    TPS contract and the commercial consent start unticked and stay so."""
    t = TEXTS[lang]
    page.goto(f'{live_server.url}/{lang}/signup/')
    expect(page.locator('html')).to_have_attribute('lang', lang)
    _assert_real_assets(page)
    _heading(page, t['account'], 1, lang)

    # Step 1.
    fill_step1(page)
    press(page, t['continue'])
    expect(page).to_have_url(url(lang, 'legal/'))

    # Step 2: nothing ticked by default; the Terms and the Conditions are required.
    _heading(page, t['legal'], 2, lang)
    for box in ('#id_terms', '#id_conditions', '#id_ormco_tps', '#id_commercial_consent'):
        expect(page.locator(box)).not_to_be_checked()
    press(page, t['continue'])
    expect(page.get_by_text(t['terms_required'])).to_be_visible()
    expect(page.get_by_text(t['conditions_required'])).to_be_visible()
    expect(page).to_have_url(url(lang, 'legal/'))
    page.check('#id_terms')
    page.check('#id_conditions')
    expect(page.locator('#id_ormco_tps')).not_to_be_checked()
    expect(page.locator('#id_commercial_consent')).not_to_be_checked()
    press(page, t['continue'])

    # Step 3: the email code.
    expect(page).to_have_url(url(lang, 'verify-email/'))
    _heading(page, t['email'], 3, lang)
    expect(page.locator('body')).to_contain_text(NORMALIZED)
    enter_code(page, email_code(), t['continue'])

    # Step 4: the mobile code (by WhatsApp); its button creates the account.
    expect(page).to_have_url(url(lang, 'verify-mobile/'))
    _heading(page, t['mobile'], 4, lang)
    expect(page.locator('body')).to_contain_text(t['whatsapp'])
    expect(page.locator('body')).to_contain_text(MOBILE_E164)
    enter_code(page, mobile_code(), t['create'])

    # The welcome screen (login required), logged in as the new Doctor.
    expect(page).to_have_url(re.compile(rf'/{lang}/welcome/$'))
    expect(page.locator('h1')).to_have_text(t['welcome'])
    expect(page.locator('body')).to_contain_text(f"{t['signed_in']} {NORMALIZED}")
    page.reload()
    expect(page).to_have_url(re.compile(rf'/{lang}/welcome/$'))

    user = User.objects.get()
    assert user.email == NORMALIZED and user.phone == MOBILE_E164
    assert not PendingRegistration.objects.exists()
    choices = {choice.document.kind: choice.accepted
               for choice in LegalChoice.objects.filter(user=user)}
    assert choices == {'terms': True, 'conditions': True, 'ormco_tps': False,
                       'commercial_consent': False}
