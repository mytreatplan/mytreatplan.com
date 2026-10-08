from django.conf import settings
from django.core.checks import Error, register

DRAFT_LEGAL_TEXTS = 'mytp_publicsite.E001'


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
