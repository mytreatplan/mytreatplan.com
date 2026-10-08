from django import template
from django.conf import settings
from django.urls import translate_url

register = template.Library()


@register.simple_tag(takes_context=True)
def translated_url(context, lang_code, absolute=False):
    """The current page's URL in another language (same view and arguments, same query string).

    Falls back to the current URL if it cannot be resolved. With absolute=True the result
    is on settings.SITE_URL and drops the query string, for <link rel="alternate" hreflang> tags.
    """
    request = context.get('request')
    if request is None:
        return ''
    if absolute:
        # Alternates name the page itself, so they carry no query string.
        return site_url(translate_url(request.path, lang_code))
    return translate_url(request.get_full_path(), lang_code)


@register.simple_tag
def site_url(path='/'):
    """An absolute URL on settings.SITE_URL for a root-relative path (e.g. request.path)."""
    return f"{settings.SITE_URL}/{str(path).lstrip('/')}"


@register.simple_tag(takes_context=True)
def canonical_url(context):
    """The current page on settings.SITE_URL, without query string."""
    request = context.get('request')
    return site_url(request.path if request is not None else '/')
