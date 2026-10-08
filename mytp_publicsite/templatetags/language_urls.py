from django import template
from django.urls import translate_url

register = template.Library()


@register.simple_tag(takes_context=True)
def translated_url(context, lang_code, absolute=False):
    """The current page's URL in another language (same view and arguments, same query string).

    Falls back to the current URL if it cannot be resolved. With absolute=True the result
    includes scheme and host and drops the query string, for <link rel="alternate" hreflang> tags.
    """
    request = context.get('request')
    if request is None:
        return ''
    if absolute:
        # Alternates name the page itself, so they carry no query string.
        return request.build_absolute_uri(translate_url(request.path, lang_code))
    return translate_url(request.get_full_path(), lang_code)
