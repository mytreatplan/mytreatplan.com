"""The signup tests' shared flow helpers and test values: a valid step 1, the legal step,
the codes (from the fake provider), and the paths through the wizard."""
import re

from conftest import TURNSTILE_TEST_TOKEN
from legal.models import LegalDocument
from signup.captcha import TOKEN_FIELD
from signup.forms import shown_document_ids
from signup.verification import EMAIL, SMS, FakeVerificationProvider

EMAIL_ADDRESS = 'ana.garcia@Example.COM'
NORMALIZED = 'ana.garcia@example.com'
PASSWORD = 'violet-orbit-canyon-42'


FIRST_NAME = 'Anabel'
LAST_NAME = 'Garcia-Montenegro'
MOBILE = '622 918 374'  # Spain
MOBILE_E164 = '+34622918374'

# A valid step 1, without the email and passwords (see _step1).
STEP1 = {
    'title': 'dr',
    'first_name': FIRST_NAME,
    'middle_name': '',
    'last_name': LAST_NAME,
    'country': 'ES',
    'phone_prefix': 'ES',
    'phone': MOBILE,
    'how_found': 'linkedin',
    'how_found_other': '',
}


def _step1(client, lang='en', email=EMAIL_ADDRESS, password=PASSWORD, password2=None,
           **fields):
    """Post a valid step 1 (with the Turnstile test token, as the widget would), with
    any field replaced through `fields`."""
    return client.post(f'/{lang}/signup/', {
        **STEP1,
        TOKEN_FIELD: TURNSTILE_TEST_TOKEN,
        'email': email, 'password1': password,
        'password2': password if password2 is None else password2,
        **fields,
    })


# The legal step: the required Terms and Conditions ticked (see _legal).
LEGAL = {'terms': 'on', 'conditions': 'on'}


def _legal(client, lang='en', **fields):
    """Post the legal step with the Terms and the Conditions ticked, the optional ones
    not, as shown with the current versions, with any field added or replaced through
    `fields` (None leaves it out)."""
    shown = shown_document_ids(LegalDocument.objects.current_by_kind().values())
    data = {name: value for name, value in {**LEGAL, 'shown': shown, **fields}.items()
            if value is not None}
    return client.post(f'/{lang}/signup/legal/', data)


def _to_code_step(client, lang='en', **fields):
    """Steps 1 and 2 (legal): the email code is sent."""
    response = _step1(client, lang=lang, **fields)
    assert response.status_code == 302, response.context['form'].errors
    return _legal(client, lang=lang)


def _code_from_log(caplog):
    lines = [r.getMessage() for r in caplog.records if r.name == 'signup.verification']
    assert lines, 'no code was logged'
    return re.search(r'(\d{6})$', lines[-1]).group(1)


def _current_code(channel, contact, purpose='signup'):
    """The fake provider's valid code for this contact and purpose."""
    code = FakeVerificationProvider.current_code(channel, contact, purpose)
    assert code, 'no valid code'
    return code


def _email_code(client, lang='en', email=NORMALIZED):
    """Post the right email code (the email step)."""
    return client.post(f'/{lang}/signup/verify-email/', {'code': _current_code(EMAIL, email)})


def _mobile_code(client, lang='en', phone=MOBILE_E164):
    """Post the right mobile code (the mobile step, the last one: the account is created)."""
    return client.post(f'/{lang}/signup/verify-mobile/', {'code': _current_code(SMS, phone)})


def _to_mobile_step(client, lang='en', **fields):
    """Steps 1, 2 (legal) and 3 (email code): the mobile code is sent."""
    _to_code_step(client, lang=lang, **fields)
    response = _email_code(client, lang=lang, email=fields.get('email', NORMALIZED).lower())
    assert response['Location'] == f'/{lang}/signup/verify-mobile/'
    return response


def _complete(client, lang='en', **fields):
    """A whole signup; returns the response to the mobile code (to the welcome page)."""
    _to_mobile_step(client, lang=lang, **fields)
    return _mobile_code(client, lang=lang)


def _wrong(code):
    return f'{(int(code) + 1) % 1_000_000:06d}'


# A ciphertext as stored in an encrypted field.
ENCRYPTED = re.compile(r'^enc:v1:[\w-]+:[A-Za-z0-9+/=]+$')
