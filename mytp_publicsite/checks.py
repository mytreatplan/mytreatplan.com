import re

from django.conf import settings
from django.core.checks import Error, register

DRAFT_LEGAL_TEXTS = 'mytp_publicsite.E001'
INSECURE_DB_TLS = 'mytp_publicsite.E002'
TURNSTILE_TEST_KEYS = 'mytp_publicsite.E003'

SECURE_DB_SSLMODES = ('verify-full', 'verify-ca')


@register('legal', deploy=True)
def check_legal_texts_final(app_configs, **kwargs):
    """Block a production deploy while the Privacy and Terms pages show draft texts.

    Runs only with `check --deploy`. Fails while LEGAL_TEXTS_FINAL is False and DEBUG is off.
    """
    if settings.DEBUG or getattr(settings, 'LEGAL_TEXTS_FINAL', False):
        return []
    return [Error(
        'The Privacy and Terms pages still show the draft legal texts ported from '
        'mytreatplan.ae (LEGAL_TEXTS_FINAL is False).',
        hint="Replace publicsite/legal/_privacy_body.html and _terms_body.html with the "
             "lawyer's mytreatplan.com texts, add the Spanish ones as _privacy_body_es.html "
             "and _terms_body_es.html, then set LEGAL_TEXTS_FINAL = True in settings.",
        id=DRAFT_LEGAL_TEXTS,
    )]


@register('security', deploy=True)
def check_db_tls(app_configs, **kwargs):
    """Block a deploy whose database connection does not verify the server's certificate.

    Runs only with `check --deploy`. Fails while DEBUG is off and DB_SSLMODE is not
    verify-full or verify-ca.
    """
    sslmode = getattr(settings, 'DB_SSLMODE', '')
    if settings.DEBUG or sslmode in SECURE_DB_SSLMODES:
        return []
    return [Error(
        f"DB_SSLMODE is {repr(sslmode) if sslmode else 'unset'}: the database connection "
        "does not verify the server's TLS certificate.",
        hint='Set DB_SSLMODE=verify-full (or verify-ca) and DB_SSLROOTCERT to the path of '
             "the database provider's CA certificate.",
        id=INSECURE_DB_TLS,
    )]


# Cloudflare's published Turnstile test keys: site keys like 1x00000000000000000000AA and
# secrets like 1x0000000000000000000000000000000AA (prefix 1x, 2x or 3x, zeros, two letters).
_TURNSTILE_TEST_KEY = re.compile(r'^[123]x0+[A-F]{2}$')


@register('security', deploy=True)
def check_turnstile_keys(app_configs, **kwargs):
    """Block a deploy whose signup CAPTCHA (signup entry 9) runs on Cloudflare's test keys,
    which let every request through.

    Runs only with `check --deploy`. Fails while DEBUG is off and TURNSTILE_SITE_KEY or
    TURNSTILE_SECRET_KEY is one of Cloudflare's test keys (the settings default to them).
    """
    if settings.DEBUG:
        return []
    names = [name for name in ('TURNSTILE_SITE_KEY', 'TURNSTILE_SECRET_KEY')
             if _TURNSTILE_TEST_KEY.match(getattr(settings, name, '') or '')]
    if not names:
        return []
    return [Error(
        f"{' and '.join(names)} {'is' if len(names) == 1 else 'are'} Cloudflare's Turnstile "
        "test key: the signup CAPTCHA lets every request through.",
        hint="Create a Turnstile site for this domain in the Cloudflare dashboard (Managed "
             "mode) and set TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY in settings.env.",
        id=TURNSTILE_TEST_KEYS,
    )]
