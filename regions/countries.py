"""The country map's seed lists and its lookup (epic-regions entry 1).

Signup, the geo-IP suggestion and the doctor profile's billing country all ask one
question: which region serves this country, and, when it is the other region, where to
send the person. `lookup_country` answers it from the map (regions.models.Country).

The lists below are the agreed seed (user, 2026-10-30 and 2026-11-01). The seed migration
(regions/migrations/0002_seed_country_map.py) keeps its own frozen copy; a test fails
when the migrated map and these lists diverge. Since epic-catalogue entry 3 the Sysadmin
changes a country on the admin site, and the configuration snapshot's `country_map`
section replaces the rows by code (regions.applier); these lists stay the seed.
"""
from dataclasses import dataclass

from django.conf import settings
from django.utils import translation

from regions.models import BANNED, Country
from regions.profiles import DEFAULT_REGION, get_profile

# Served by the ae region.
AE_COUNTRIES = frozenset({
    'AE', 'SA', 'KW', 'QA', 'BH', 'OM', 'JO', 'LB', 'IQ', 'PS', 'YE', 'TR', 'EG', 'MA', 'ZA',
})

# The 54 sovereign African states.
AFRICAN_STATES = frozenset({
    'DZ', 'AO', 'BJ', 'BW', 'BF', 'BI', 'CV', 'CM', 'CF', 'TD', 'KM', 'CG', 'CD', 'CI',
    'DJ', 'EG', 'GQ', 'ER', 'SZ', 'ET', 'GA', 'GM', 'GH', 'GN', 'GW', 'KE', 'LS', 'LR',
    'LY', 'MG', 'MW', 'ML', 'MR', 'MU', 'MA', 'MZ', 'NA', 'NE', 'NG', 'RW', 'ST', 'SN',
    'SC', 'SL', 'SO', 'ZA', 'SS', 'SD', 'TZ', 'TG', 'TN', 'UG', 'ZM', 'ZW',
})
# African territories that are banned too: Western Sahara, Réunion, Mayotte, Saint Helena
# with Ascension (AC) and Tristan da Cunha (TA), which only phonenumbers knows as codes,
# and the British Indian Ocean Territory.
AFRICAN_TERRITORIES = frozenset({'EH', 'RE', 'YT', 'SH', 'AC', 'TA', 'IO'})
# Banned outside Africa.
OTHER_BANNED = frozenset({'IR', 'SY', 'KP', 'CU', 'RU', 'BY'})

# Every African country not served by ae, plus the others.
BANNED_COUNTRIES = (AFRICAN_STATES | AFRICAN_TERRITORIES | OTHER_BANNED) - AE_COUNTRIES

# Every other country is eu, and so is a code with no row (DEFAULT_REGION).
DEFAULT_STATE = DEFAULT_REGION

# The signup path on every region's site (signup/urls.py `signup` under i18n_patterns);
# a test pins it to reverse('signup').
SIGNUP_PATH = '/{language}/signup/'


def seed_state(code):
    """The state the seed gives `code`."""
    if code in AE_COUNTRIES:
        return 'ae'
    if code in BANNED_COUNTRIES:
        return BANNED
    return DEFAULT_STATE


def region_site_url(profile):
    """The public site URL of region `profile`: settings.REGION_SITE_URLS (the
    REGION_SITE_URL_<CODE> override), else its production site URL."""
    return settings.REGION_SITE_URLS.get(profile.code, profile.site_url).rstrip('/')


@dataclass(frozen=True)
class CountryAnswer:
    """What the map says about a country. `state` is a region code or BANNED; for a
    country of the other region, `site_url` and `signup_url` say where to send the person
    (otherwise both are empty)."""
    code: str
    state: str
    site_url: str = ''
    signup_url: str = ''

    @property
    def banned(self):
        return self.state == BANNED

    @property
    def own_region(self):
        return self.state == settings.REGION_PROFILE.code

    @property
    def other_region(self):
        return not self.banned and not self.own_region


def _signup_language(profile, language):
    """`language` when the target region offers it, otherwise its first language."""
    language = (language or '').split('-')[0].lower()
    return language if language in profile.languages else profile.languages[0]


def lookup_country(code, language=None):
    """The map's answer for ISO alpha-2 `code` (any case; blank, None or a code with no
    row reads as eu). For a country of the other region, the answer carries that region's
    site URL and its signup URL, in `language` (default: the active one) when the region
    offers it, otherwise in its first language."""
    code = (code or '').strip().upper()
    state = None
    if code:
        state = Country.objects.filter(code=code).values_list('state', flat=True).first()
    state = state or DEFAULT_STATE

    if state == BANNED or state == settings.REGION_PROFILE.code:
        return CountryAnswer(code=code, state=state)

    profile = get_profile(state)
    site_url = region_site_url(profile)
    lang = _signup_language(profile, language or translation.get_language())
    return CountryAnswer(code=code, state=state, site_url=site_url,
                         signup_url=site_url + SIGNUP_PATH.format(language=lang))
