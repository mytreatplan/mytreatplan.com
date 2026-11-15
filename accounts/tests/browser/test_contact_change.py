"""A code-confirmed mobile change (epic-doctor-profile entry 3) in Chromium against
live_server, with the fake provider: a Doctor created by signup opens the change from the
personal-data page, types the new mobile and the password, enters the code sent to the
new number and is back on the page, still signed in, with the new mobile."""
import pytest

pytest.importorskip('pytest_playwright')

from django.contrib.auth import get_user_model  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

from accounts import profile  # noqa: E402
from signup.tests.browser.helpers import enter_code, follow, press  # noqa: E402
from signup.tests.helpers import PASSWORD, _current_code  # noqa: E402
from signup.verification import SMS  # noqa: E402

from .helpers import at, sign_up, status  # noqa: E402

pytestmark = [
    pytest.mark.browser,
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
    pytest.mark.usefixtures('portal_urls'),
]

NEW_E164 = '+34622918375'


@pytest.fixture(autouse=True)
def inline_notice(monkeypatch):
    from accounts import password_reset

    monkeypatch.setattr(password_reset, 'in_background', lambda function: function())


def test_doctor_changes_their_mobile_with_a_code(live_server, page, mailoutbox):
    sign_up(page, live_server)
    page.goto(f'{live_server.url}/en/settings/')
    expect(page.locator('[data-readonly="phone"]')).not_to_be_editable()

    follow(page, 'Change your mobile phone number')
    at(page, live_server, 'en', 'settings/mobile/')
    page.fill('#id_phone', '622 918 375')
    page.fill('#id_password', PASSWORD)
    press(page, 'Send the code')

    at(page, live_server, 'en', 'settings/mobile/code/')
    expect(page.locator('[data-sent]')).to_contain_text(NEW_E164)
    enter_code(page, _current_code(SMS, NEW_E164, profile.CHANGE_PURPOSE), 'Confirm')

    at(page, live_server, 'en', 'settings/')
    expect(status(page, 'Your mobile phone number has been changed.')).to_be_visible()
    expect(page.locator('[data-readonly="phone"]')).to_have_value(NEW_E164)
    assert get_user_model().objects.get().phone == NEW_E164
    assert len(mailoutbox) == 1
