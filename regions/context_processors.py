"""The region's company in every template (epic-regions 2.6): the footer and the public
legal texts name the company of this instance's profile (settings.REGION_PROFILE), never
a hard-coded one."""
from django.conf import settings
from django.utils.translation import gettext
from django_countries import countries


def company_address(company):
    """The company's registered office as one line: its address lines, then the country
    named in the active language (e.g. "..., Ireland" in English, "..., Irlanda" in
    Spanish)."""
    office = company.office
    return ', '.join((*office.lines, str(countries.name(office.country_code))))


def company_registration(company):
    """The company's registration line in the active language."""
    return gettext(company.registration)


def region_company(request):
    """`region_company` (regions.profiles.RegionCompany), and its registration line and
    office in the active language: `region_company_registration`,
    `region_company_address`."""
    company = settings.REGION_PROFILE.company
    return {'region_company': company,
            'region_company_registration': company_registration(company),
            'region_company_address': company_address(company)}
