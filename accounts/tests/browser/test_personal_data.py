"""The settings area's personal-data page (epic-doctor-profile entry 2) in Chromium against
live_server: a Doctor created by signup opens it from the home, changes city, profession
and the WhatsApp consent, saves, sees the change on the home card, opens an unbuilt
section; on a phone the page stacks in one column."""
import pytest

pytest.importorskip('pytest_playwright')

from playwright.sync_api import expect  # noqa: E402

from accounts.models import Profile  # noqa: E402
from legal.models import LegalChoice  # noqa: E402
from signup.tests.browser.helpers import follow  # noqa: E402

from .helpers import at, log_in, log_out, sign_up  # noqa: E402

pytestmark = [
    pytest.mark.browser,
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
    pytest.mark.usefixtures('portal_urls'),
]


def test_doctor_changes_their_details_and_sees_them_on_the_home(live_server, page):
    sign_up(page, live_server)
    log_out(page, live_server)
    log_in(page, live_server)

    follow(page, 'Personal data Settings')
    at(page, live_server, 'en', 'settings/')
    expect(page.locator('h1')).to_have_text('Review and/or change your details')
    expect(page.locator('[data-readonly="email"]')).not_to_be_editable()

    page.get_by_label('Location (city)').fill('Madrid')
    page.get_by_label('Profession').select_option('orthodontist')
    page.get_by_label('I agree to be contacted by MyTreatPlan via WhatsApp.').check()
    page.locator('[data-save]').click()

    at(page, live_server, 'en', 'settings/')
    expect(page.get_by_text('Your details have been saved.')).to_be_visible()
    expect(page.locator('[data-profile-line]')).to_have_text('Orthodontist · Madrid')
    profile = Profile.objects.get()
    assert (profile.city, profile.profession) == ('Madrid', 'orthodontist')
    assert LegalChoice.objects.filter(document__kind='whatsapp_contact',
                                      accepted=True).count() == 1

    # A bad landline: the field error, nothing saved.
    page.get_by_label('Landline', exact=True).fill('12')
    page.locator('[data-save]').click()
    expect(page.get_by_text('Enter a valid phone number for the selected prefix.')).to_be_visible()
    assert Profile.objects.get().landline == ''

    # An unbuilt section, inside the area; Cancel goes home.
    page.get_by_role('link', name='Change password').click()
    at(page, live_server, 'en', 'settings/password/')
    expect(page.get_by_text('This section is coming soon.')).to_be_visible()
    page.goto(f'{live_server.url}/en/settings/')
    page.locator('[data-cancel]').click()
    at(page, live_server, 'en', 'home/')
    expect(page.locator('[data-profile-line]')).to_have_text('Orthodontist · Madrid')


def test_phone_width_stacks_the_page(live_server, page):
    sign_up(page, live_server)
    log_out(page, live_server)
    log_in(page, live_server)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(f'{live_server.url}/en/settings/')

    photo = page.locator('[data-photo]').bounding_box()
    city = page.get_by_label('Location (city)').bounding_box()
    assert city['y'] >= photo['y'] + photo['height']
    title = page.get_by_label('Title / Treatment').bounding_box()
    profession = page.get_by_label('Profession').bounding_box()
    assert profession['y'] >= title['y'] + title['height']
    assert page.evaluate('document.documentElement.scrollWidth') <= 390
