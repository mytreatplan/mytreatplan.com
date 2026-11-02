"""The account a signup creates records the instance's region as its home region (R2 / AD-14,
epic-regions 2.2), whatever the country given at step 1."""
import pytest
from django.contrib.auth import get_user_model

from regions.profiles import get_profile

from .helpers import _complete

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

User = get_user_model()


@pytest.mark.parametrize('region', ['eu', 'ae'])
def test_signup_records_the_instance_region(client, settings, region):
    settings.REGION = region
    settings.REGION_PROFILE = get_profile(region)

    response = _complete(client, country='ES')

    assert response['Location'] == '/en/welcome/'
    user = User.objects.get()
    assert user.country == 'ES'
    assert user.home_region == region
