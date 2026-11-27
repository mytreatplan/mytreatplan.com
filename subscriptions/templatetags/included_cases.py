"""`{% included_cases as cases %}` (epic-subscriptions 13): the signed-in doctor's included
TPS branded cases for the home's profile card. `cases` is None (no grant counts: Basic,
expired, manual, a term over, or a grant of 0), or `{'left', 'total'}` from
`subscriptions.service.included_left`.

It runs once per request (kept on the request). A fault while building it is logged as an
error and gives nothing: the page never fails because of it."""
import logging

from django import template

from subscriptions import service

logger = logging.getLogger(__name__)
register = template.Library()

_KEPT = '_included_cases'


def _cases(request):
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return None
    try:
        found = service.included_left(user)
        if found is None:
            return None
        left, total = found
        return {'left': left, 'total': total}
    except Exception:  # never a 500 from the home
        logger.exception('included cases: building them failed; none shown')
        return None


@register.simple_tag(takes_context=True)
def included_cases(context):
    """The request user's included cases (see the module docstring); None without a
    request."""
    request = context.get('request')
    if request is None:
        return None
    if not hasattr(request, _KEPT):
        setattr(request, _KEPT, _cases(request))
    return getattr(request, _KEPT)
