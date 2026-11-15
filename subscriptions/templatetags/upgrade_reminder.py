"""`{% upgrade_reminder as reminder %}` (epic-subscriptions entry 3): the upgrade reminder
of the signed-in doctor, for the doctor header (`templates/doctor_base.html`) and the
home's profile card. `reminder` is None, or `{'tier', 'name', 'url'}`: the current level,
its name as the upgrade screen shows it (`subscriptions.levels.level_name`, in the page's
language) and the address of the upgrade screen.

The rule is `subscriptions.service.reminder`'s; it runs once per request (the value is
built in the language active at its first use and kept on the request for every later
use: the header and the home together). A fault while building it (the service, the
name, the address) is logged as an error and gives no reminder: the page never fails
because of it."""
import logging

from django import template
from django.urls import reverse
from django.utils.translation import get_language

from subscriptions import service
from subscriptions.levels import level_name

logger = logging.getLogger(__name__)
register = template.Library()

_KEPT = '_upgrade_reminder'


def _reminder(request):
    """The value the tag gives (None or the dict), built entirely inside the guard."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return None
    try:
        found = service.reminder(user)
        if found is None:
            return None
        return {'tier': found.tier,
                'name': level_name(found.subscription, found.tier, get_language()),
                'url': reverse('upgrade')}
    except Exception:  # never a 500 from the header
        logger.exception('upgrade reminder: building it failed; none shown')
        return None


@register.simple_tag(takes_context=True)
def upgrade_reminder(context):
    """The request user's reminder (see the module docstring); None without a request."""
    request = context.get('request')
    if request is None:
        return None
    if not hasattr(request, _KEPT):
        setattr(request, _KEPT, _reminder(request))
    return getattr(request, _KEPT)
