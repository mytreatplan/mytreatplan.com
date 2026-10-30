"""Region profiles (architecture AD-13, epic-platform entry 6).

The portal runs as one codebase per region; `REGION` in settings.env selects one profile
here, and every region-specific value comes from it (or, later, from the synced
configuration). Settings exposes the selected profile as `settings.REGION_PROFILE`.
Comparing against a region name anywhere else is not allowed
(regions/tests/test_guard.py).

Plain data plus a lookup, with no Django imports: settings.py imports this module
before any app is loaded. No secrets: keys, credentials and the KMS service settings stay
in settings.env, and so do the storage bucket and the KMS endpoint (KMS_ENDPOINT), which
are env settings only.
"""
from dataclasses import dataclass

# Display names of the languages a profile may list (Django's LANGUAGES pairs).
LANGUAGE_NAMES = {
    'en': 'English',
    'es': 'Español',
}


class UnknownRegion(ValueError):
    """`REGION` names no profile."""


@dataclass(frozen=True)
class RegionCompany:
    """The region's company, for the footer and legal texts (used from epic-regions 2.6)."""
    name: str
    registration: str


@dataclass(frozen=True)
class RegionProfile:
    code: str
    # Production domains, the canonical one first. They are the default ALLOWED_HOSTS,
    # CSRF_TRUSTED_ORIGINS (https://) and SITE_URL (the first); staging sets its own in
    # settings.env.
    domains: tuple[str, ...]
    company: RegionCompany
    time_zone: str
    languages: tuple[str, ...]
    currencies: tuple[str, ...]
    # Default object-storage endpoint for the region's files (AD-9); settings expose it as
    # STORAGE_ENDPOINT, which settings.env overrides. Empty: not chosen yet.
    storage_endpoint: str
    # Payment gateways available in the region (adapters: epic-payments).
    gateways: tuple[str, ...]
    # Patient code prefix (AD-5, e.g. EU-7K3QX9MD) and invoice instance prefix (AD-7).
    patient_code_prefix: str
    invoice_prefix: str

    @property
    def site_url(self):
        return f'https://{self.domains[0]}'

    @property
    def origins(self):
        return tuple(f'https://{domain}' for domain in self.domains)

    @property
    def language_choices(self):
        """Django's LANGUAGES: (code, name) pairs."""
        return [(code, LANGUAGE_NAMES[code]) for code in self.languages]


PROFILES = {
    'eu': RegionProfile(
        code='eu',
        domains=('mytreatplan.com', 'www.mytreatplan.com'),
        company=RegionCompany(name='MyTPDSO Limited',
                              registration='Company number 815526 (Ireland)'),
        time_zone='Europe/Madrid',
        languages=('en', 'es'),
        currencies=('EUR', 'USD'),
        storage_endpoint='https://s3.gra.io.cloud.ovh.net',
        gateways=('redsys', 'revolut', 'bank_transfer'),
        patient_code_prefix='EU',
        invoice_prefix='EU',
    ),
    'ae': RegionProfile(
        code='ae',
        domains=('mytreatplan.ae', 'www.mytreatplan.ae'),
        company=RegionCompany(name='MyTPDSO L.L.C-FZ',
                              registration='License 2542361.01 (Dubai)'),
        time_zone='Asia/Dubai',
        languages=('en',),
        currencies=('AED', 'USD'),
        storage_endpoint='',  # set at epic-uae-launch
        gateways=(),  # none until epic-uae-launch
        patient_code_prefix='AE',
        invoice_prefix='AE',
    ),
}

DEFAULT_REGION = 'eu'


def get_profile(code):
    """The profile of region `code`; UnknownRegion (naming the valid ones) otherwise."""
    try:
        return PROFILES[code]
    except (KeyError, TypeError):
        raise UnknownRegion(
            f'REGION must be one of {", ".join(sorted(PROFILES))}, got {code!r}') from None
