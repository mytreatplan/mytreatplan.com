import json

from django.conf import settings
from django.db import connection
from django.http import HttpResponse
from django.templatetags.static import static
from django.utils.safestring import mark_safe
from django.utils.translation import gettext
from django.template.loader import select_template
from django.utils.translation import get_language
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe
from django.views.generic import TemplateView

# Escapes that keep a JSON document safe inside <script> (same set as Django's json_script).
_JSON_SCRIPT_ESCAPES = {ord('>'): '\\u003E', ord('<'): '\\u003C', ord('&'): '\\u0026'}


def organization_json_ld():
    """schema.org Organization for the home page: the Irish company and the three offices."""
    site_url = settings.SITE_URL
    data = {
        '@context': 'https://schema.org',
        '@type': 'Organization',
        'name': 'MyTreatPlan',
        'legalName': 'MyTPDSO Limited',
        'url': f'{site_url}/',
        'logo': f"{site_url}{static('img/Logo_MyTreatPlan.webp')}",
        'description': gettext(
            'Treatment planning services, remote virtual orthodontist support and education '
            'for orthodontic practices. Offices in Dublin, Dubai and Madrid.'),
        'identifier': {
            '@type': 'PropertyValue',
            'name': 'Company number',
            'value': '815526',
        },
        'address': {
            '@type': 'PostalAddress',
            'streetAddress': '19 Baggot Street Lower',
            'addressLocality': 'Dublin 2',
            'postalCode': 'D02 X658',
            'addressCountry': 'IE',
        },
        'location': [
            {'@type': 'Place', 'name': 'Dublin & Cork Offices — European HQ',
             'address': {'@type': 'PostalAddress', 'addressLocality': 'Dublin',
                         'addressCountry': 'IE'}},
            {'@type': 'Place', 'name': 'Global HQ',
             'address': {'@type': 'PostalAddress', 'addressLocality': 'Dubai',
                         'addressCountry': 'AE'}},
            {'@type': 'Place', 'name': 'Madrid Offices',
             'address': {'@type': 'PostalAddress', 'addressLocality': 'Madrid',
                         'addressCountry': 'ES'}},
        ],
        'areaServed': ['Middle East', 'Africa', 'Turkey', 'Europe', 'North America'],
        'sameAs': [
            'https://www.linkedin.com/company/mytreatplan/',
            'https://www.facebook.com/mytreatplan',
            'https://www.instagram.com/mytreatplan',
        ],
    }
    return mark_safe(json.dumps(data, ensure_ascii=False).translate(_JSON_SCRIPT_ESCAPES))


class HomeView(TemplateView):
    template_name = 'publicsite/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['organization_json_ld'] = organization_json_ld()
        return context


class LegalPageView(TemplateView):
    """A legal page whose body is the current language's text when it exists.

    Looks for publicsite/legal/_<body>_body_<lang>.html first, then the English
    _<body>_body.html. Adding a translated text is a matter of adding that template.
    """
    body = ''

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lang = (get_language() or settings.LANGUAGE_CODE).split('-')[0]
        english = f'publicsite/legal/_{self.body}_body.html'
        candidates = [english] if lang == 'en' else [
            f'publicsite/legal/_{self.body}_body_{lang}.html', english]
        template = select_template(candidates)
        body_lang = 'en' if template.template.name == english else lang
        context['legal_body_template'] = template.template.name
        context['legal_body_lang'] = body_lang
        context['legal_body_is_fallback'] = body_lang != lang
        return context


class PrivacyView(LegalPageView):
    template_name = 'publicsite/privacy.html'
    body = 'privacy'


class TermsView(LegalPageView):
    template_name = 'publicsite/terms.html'
    body = 'terms'


@require_safe
def robots_txt(request):
    if getattr(settings, 'ROBOTS_NOINDEX', False):
        # Staging: disallow everything and advertise no sitemap.
        lines = ['User-agent: *', 'Disallow: /']
        return HttpResponse('\n'.join(lines) + '\n', content_type='text/plain; charset=utf-8')
    lines = [
        'User-agent: *',
        'Allow: /',
        '',
        f'Sitemap: {settings.SITE_URL}/sitemap.xml',
    ]
    return HttpResponse('\n'.join(lines) + '\n', content_type='text/plain; charset=utf-8')


@never_cache
@require_safe
def healthz(request):
    """Uptime probe (UptimeRobot, outside basic auth). Says only `ok` or `unavailable`:
    no versions, settings or error details."""
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception:
        return HttpResponse('unavailable', status=503, content_type='text/plain; charset=utf-8')
    return HttpResponse('ok', content_type='text/plain; charset=utf-8')
