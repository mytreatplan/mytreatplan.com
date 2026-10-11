from django.conf import settings
from django.core.checks import Error, register

DRAFT_LEGAL_TEXTS = 'mytp_publicsite.E001'
INSECURE_DB_TLS = 'mytp_publicsite.E002'

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
