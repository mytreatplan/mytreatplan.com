import json

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.utils.safestring import mark_safe
from django.utils.translation import gettext
from django.template.loader import select_template
from django.utils.translation import get_language
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST, require_safe
from django.views.generic import TemplateView

from signup.forms import refuse

from . import contact as contact_form
from .forms import ContactForm

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
        context['contact_form'] = ContactForm()
        context['contact_stamp'] = contact_form.make_stamp()
        return context


CONTACT_FORM_TEMPLATE = 'publicsite/contact/_form.html'
CONTACT_THANKS_TEMPLATE = 'publicsite/contact/_thanks.html'


@sensitive_post_parameters()
@never_cache
@require_POST
def contact(request):
    """The contact form's POST (htmx, from the home page's Contact section): the form
    again with its errors (or the generic "try again later" over a limit), else the
    thank-you, also for every bot case (mytp_publicsite.contact)."""
    thanks = render(request, CONTACT_THANKS_TEMPLATE)
    if (contact_form.dropped_before_validation(request.POST)
            or contact_form.has_link(request.POST)):
        return thanks
    form = ContactForm(request.POST)
    # The render time stays the first one, so a corrected form is not "too fast".
    context = {'contact_form': form,
               'contact_stamp': request.POST[contact_form.STAMP_FIELD]}
    if not form.is_valid():
        return render(request, CONTACT_FORM_TEMPLATE, context)
    if not contact_form.reserve(request):
        refuse(form, 'rate_limited')
        return render(request, CONTACT_FORM_TEMPLATE, context)
    contact_form.accept(form.cleaned_data)
    return thanks


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
