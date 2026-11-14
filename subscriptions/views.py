"""The doctor's upgrade screen (epic-subscriptions entry 2, user decisions 2026-11-14).

- `upgrade` (`/<lang>/upgrade/`): the levels of the doctor's country group with their net
  prices and "+ VAT where applicable", the current level marked, a button on each higher
  level and "Not now" to the welcome page. A doctor on a manual subscription sees its
  name as the current level, the standard levels for information, no buttons and
  "Continue". Signup sends a new doctor here once; any signed-in doctor may come back.
  Nothing on offer (no country group, a group that sells neither Advanced nor Premium, a
  staff account): redirected to the welcome page.
- `upgrade_purchase` (`/<lang>/upgrade/<level>/`): a placeholder until the purchase page
  (entry 5) replaces it: "coming soon" and a link back. Only a level the doctor can
  upgrade to: any other level is 404; a doctor on a manual subscription goes back to
  `upgrade`.

Everything about the subscription comes from `subscriptions.service.upgrade_options`;
the prices are the catalogue's (AD-16), shown with the `money` filter.
"""
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import get_language
from django.utils.translation import pgettext_lazy

from . import service

# A level's name when its catalogue row has none in any language.
TIER_NAMES = {
    'basic': pgettext_lazy('subscription level', 'Basic'),
    'advanced': pgettext_lazy('subscription level', 'Advanced'),
    'premium': pgettext_lazy('subscription level', 'Premium'),
}


def _name(row, tier, language):
    """The row's public name in `language`, else the translated tier name."""
    return (row.name_in(language) if row is not None else '') or TIER_NAMES[tier]


def _level(level, language):
    """A listed level as the template shows it."""
    return {
        'tier': level.tier,
        'name': _name(level.subscription, level.tier, language),
        'description': level.subscription.description_in(language),
        'free': level.free, 'monthly': level.monthly, 'upfront': level.upfront,
        'current': level.current, 'can_upgrade': level.can_upgrade,
    }


@login_required
def upgrade(request):
    options = service.upgrade_options(request.user)
    if not options.on_offer:
        return redirect('welcome')
    language = get_language()
    return render(request, 'subscriptions/upgrade.html', {
        'current_name': _name(options.subscription, options.tier, language),
        'manual': options.manual,
        'levels': [_level(level, language) for level in options.levels],
    })


@login_required
def upgrade_purchase(request, level):
    options = service.upgrade_options(request.user)
    if not options.on_offer:
        return redirect('welcome')
    if not options.can_switch:
        return redirect('upgrade')
    found = options.level(level)
    if found is None or not found.can_upgrade:
        raise Http404('not a level this doctor can upgrade to')
    return render(request, 'subscriptions/upgrade_purchase.html', {
        'level': _level(found, get_language()),
    })
