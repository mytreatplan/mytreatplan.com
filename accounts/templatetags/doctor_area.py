"""The doctor area's filters (accounts.doctor_area): `{{ user|initials }}` and
`{{ user|display_name }}`, and `{{ url_name|in_settings }}` (the gear is current); and
`{% profile_photo as photo %}`, the signed-in user's photo (accounts.photo.avatar: one
presigned link per request; `photo.url` is '' for the initials)."""
from django import template

from accounts import doctor_area
from accounts import photo as profile_photos

register = template.Library()


@register.filter
def initials(user):
    """The avatar's letters (accounts.doctor_area.initials)."""
    return doctor_area.initials(user)


@register.filter
def in_settings(url_name):
    """Whether a URL name is one of the settings area's (`settings`, `settings_*`)."""
    return url_name == 'settings' or str(url_name or '').startswith('settings_')


@register.filter
def display_name(user):
    """The doctor's name with their title (accounts.doctor_area.display_name)."""
    return doctor_area.display_name(user)


@register.simple_tag(takes_context=True)
def profile_photo(context):
    """The signed-in user's Avatar (accounts.photo.avatar), or the initials' without a
    request."""
    request = context.get('request')
    if request is None:
        return profile_photos.NO_AVATAR
    return profile_photos.avatar(request)
