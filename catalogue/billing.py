"""The region's company against the billing companies the admin site sent (epic-catalogue
entry 9, user decision 2026-11-11).

The region profile names the region's company for the footer and the legal texts
(`settings.REGION_PROFILE.company`: a name and a registration); the admin site's billing
companies are what invoices will print. `profile_mismatch()` compares the two: the stored
billing company whose code is the region's (`regions.profiles.billing_company_code`: IE
for the EU region, AE for the UAE one) against the profile. When its legal name, or its
company number or license, is another one than the profile's, the snapshot apply logs a
warning (catalogue.applier) and `price_catalogue` prints it. Nothing is refused: the
snapshot applies and the rules are used as sent.

The registration matches when the company's number or license is the profile's
registration text, or is one of its words (the profile says "Company number 815526
(Ireland)"; the admin site may hold that text or just "815526", never a part of the
number), compared without case and with single spaces. A region whose company the admin site has not sent (no billing company of
its code) is not a mismatch.
"""
from django.conf import settings

from regions.profiles import billing_company_code

from .models import BillingCompany


def _plain(text):
    return ' '.join((text or '').split()).casefold()


def profile_mismatch(profile=None):
    """'' when the stored billing company of the region agrees with the region profile's
    company (or there is none to compare); otherwise one line saying what differs. It
    names the legal names and registrations, which are public company data."""
    profile = profile or settings.REGION_PROFILE
    code = billing_company_code(profile)
    company = (BillingCompany.objects.filter(code=code).order_by('-active', 'id').first()
               if code else None)
    if company is None:
        return ''
    differs = []
    if _plain(company.legal_name) != _plain(profile.company.name):
        differs.append(f'legal name "{company.legal_name}" (the profile: '
                       f'"{profile.company.name}")')
    number, registration = _plain(company.company_number), _plain(profile.company.registration)
    # The whole text, or one of its words with a digit: "81552" is not "815526", and
    # "Ireland" is not a number.
    words = {word.strip('()[],;:') for word in registration.split()
             if any(character.isdigit() for character in word)}
    if not number or (number != registration and number not in words):
        differs.append(f'company number or license "{company.company_number}" (the '
                       f'profile: "{profile.company.registration}")')
    if not differs:
        return ''
    return (f'the billing company {company.code} differs from the {profile.code} region '
            f'profile: {"; ".join(differs)}')
