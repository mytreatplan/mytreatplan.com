"""The doctor area's filters (accounts.doctor_area): `{{ user|initials }}` and
`{{ user|display_name }}`, and `{{ url_name|in_settings }}` (the gear is current)."""
from django import template

from accounts import doctor_area

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
