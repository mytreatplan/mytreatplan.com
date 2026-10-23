import re

from django.conf import settings
from django.core.checks import Error, register

from accounts.crypto import DEFAULT_KEY_PROVIDER, KMS_PROVIDER as KMS_KEY_PROVIDER
from signup.twilio_verify import TWILIO_PROVIDER
from signup.verification import FAKE_PROVIDER

DRAFT_LEGAL_TEXTS = 'mytp_publicsite.E001'
INSECURE_DB_TLS = 'mytp_publicsite.E002'
TURNSTILE_TEST_KEYS = 'mytp_publicsite.E003'
PLAIN_FIELD_KEYS = 'mytp_publicsite.E004'
NO_ERASURE_RETENTION = 'mytp_publicsite.E005'
FAKE_VERIFICATION_PROVIDER = 'mytp_publicsite.E006'
NO_CONTACT_INBOX = 'mytp_publicsite.E007'

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


@register('security', deploy=True)
def check_field_key_provider(app_configs, **kwargs):
    """Block a deploy whose field encryption keys sit in plain text in settings.env
    (story 2.12): the encryption policy wants them wrapped by the KMS.

    Runs only with `check --deploy`. Fails while DEBUG is off and FIELD_KEY_PROVIDER is
    not the KMS provider.
    """
    provider = getattr(settings, 'FIELD_KEY_PROVIDER', '') or DEFAULT_KEY_PROVIDER
    if settings.DEBUG or provider == KMS_KEY_PROVIDER:
        return []
    return [Error(
        f'FIELD_KEY_PROVIDER is {provider!r}: the field encryption keys are not wrapped by '
        'the KMS.',
        hint=f'Set FIELD_KEY_PROVIDER={KMS_KEY_PROVIDER} with FIELD_KMS_KEYS and '
             'BLIND_INDEX_KMS_KEY (deploy/README.md, step 12, "Move to the KMS provider").',
        id=PLAIN_FIELD_KEYS,
    )]


@register('security', deploy=True)
def check_erasure_retention(app_configs, **kwargs):
    """Block a deploy without the retention period of erased accounts (accounts.erasure),
    which the lawyer sets: without it, accounts can't be erased from the admin.

    Runs only with `check --deploy`. Fails while DEBUG is off and
    ACCOUNT_ERASURE_RETENTION_DAYS is unset or below 1 (treated as unset).
    """
    days = getattr(settings, 'ACCOUNT_ERASURE_RETENTION_DAYS', None)
    if settings.DEBUG or (days is not None and days >= 1):
        return []
    return [Error(
        'ACCOUNT_ERASURE_RETENTION_DAYS must be a positive number of days (it is '
        f"{'unset' if days is None else days}): the admin cannot erase accounts.",
        hint="Set ACCOUNT_ERASURE_RETENTION_DAYS in settings.env to the number of days the "
             "lawyer gives for keeping an erased account as evidence (deploy/README.md, "
             "\"Erase an account\").",
        id=NO_ERASURE_RETENTION,
    )]


@register('security', deploy=True)
def check_verification_provider(app_configs, **kwargs):
    """Block a deploy whose signup codes go through the fake provider (signup entry 11):
    no real person receives a code, and each code is written to the server log.

    Runs only with `check --deploy`. Fails while DEBUG is off and VERIFICATION_PROVIDER is
    the fake.
    """
    provider = getattr(settings, 'VERIFICATION_PROVIDER', '') or FAKE_PROVIDER
    if settings.DEBUG or provider != FAKE_PROVIDER:
        return []
    return [Error(
        f'VERIFICATION_PROVIDER is {provider!r}: signup codes are written to the server log '
        'and never reach anyone.',
        hint=f'Set VERIFICATION_PROVIDER={TWILIO_PROVIDER} and the Twilio credentials in '
             'settings.env (deploy/README.md, "Twilio Verify").',
        id=FAKE_VERIFICATION_PROVIDER,
    )]


@register('mail', deploy=True)
def check_contact_inbox(app_configs, **kwargs):
    """Block a deploy whose contact form mails no one (story 1.5): its messages would only
    be stored.

    Runs only with `check --deploy`. Fails while DEBUG is off and CONTACT_INBOX is empty.
    """
    if settings.DEBUG or (getattr(settings, 'CONTACT_INBOX', '') or '').strip():
        return []
    return [Error(
        "CONTACT_INBOX is empty: the contact form's messages are stored but mailed to no one.",
        hint='Set CONTACT_INBOX=webform@mytreatplan.com in settings.env (deploy/README.md, '
             '"Email").',
        id=NO_CONTACT_INBOX,
    )]
