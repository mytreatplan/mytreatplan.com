"""The doctor's upgrade screen (epic-subscriptions entry 2, user decisions 2026-11-14).

- `upgrade` (`/<lang>/upgrade/`): the levels of the doctor's country group with their net
  prices and "+ VAT where applicable", the current level marked, a button on each higher
  level and "Not now" to the welcome page. A doctor on a manual subscription sees its
  name as the current level, the standard levels for information, no buttons and
  "Continue". Signup sends a new doctor here once; any signed-in doctor may come back.
  Nothing on offer (no country group, a group that sells neither Advanced nor Premium, a
  staff account): redirected to the welcome page.
- `upgrade_purchase` (`/<lang>/upgrade/<level>/`, entry 5, user decisions 2026-11-26):
  the purchase page of a level a Basic doctor can upgrade to (any other level is 404).
  GET shows the periods with their net prices and "+ VAT where applicable" (monthly, a
  12-month commitment, only when `service.monthly_possible`; a year upfront) and an
  optional subscription voucher code. POST calls `service.purchase`, which owns the rules
  (periods offered, voucher with the year upfront only, the open order, saving the card
  whenever a gateway of the doctor can, who may buy) and creates the order; the view sends
  the doctor to its checkout; an order a voucher covers in
  full is paid at once and goes to its return page. A refusal (a period not offered, a
  voucher refused, no payment method) shows the page again with the message and the
  typed code, and creates nothing. A doctor with an open subscription order goes to its
  checkout (no second order); a manual record, or a paid tier that counts, goes back to
  `upgrade` (changing between paid tiers is 8.8).
- Annual renewals (epic-subscriptions 8.6, user decisions 2026-11-27): under "Your current
  level" a doctor on a running annual subscription (`service.renewal_info`) sees the renewal
  date, the saved card's last 4 or "payment link" and a Turn off / Turn on button (a POST to
  `upgrade_auto_renew`, calling `service.set_auto_renew` with the shown version); within the
  last 30 days of the term, "Renew now" opens `upgrade_purchase` for the same level in
  renewal mode: a year upfront only, with the voucher field; POST calls
  `service.renew_now`, which owns the rules (a forged POST outside the window is refused).
- Monthly charges (epic-subscriptions 8.7, user decisions 2026-11-28): a monthly payer sees
  the next charge date, the card and the same Turn off / Turn on button (off: they keep
  paying until the commitment ends, then Basic); no "Renew now".

Everything about the subscription comes from `subscriptions.service.upgrade_options`;
the prices are the catalogue's (AD-16), shown with the `money` filter. A level is named
as `subscriptions.levels.level_name` names it (the upgrade reminder shares it).
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import get_language, gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from catalogue import pricing
from payments import service as payments
from payments.views import refusal_message

from . import service
from .levels import level_name as _name

MONTHLY, UPFRONT = service.MONTHLY, service.UPFRONT
VOUCHER_LENGTH = 40


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
        'renewal': service.renewal_info(request.user),
    })


@login_required
@require_http_methods(['POST'])
def upgrade_auto_renew(request):
    """Turn auto-renew on (`auto_renew=on`) or off, at the version the screen showed; the
    rules are the service's."""
    on = request.POST.get('auto_renew') == 'on'
    try:
        version = int(request.POST.get('version', ''))
    except ValueError:
        version = -1
    try:
        service.set_auto_renew(request.user, on, expected_version=version)
    except service.StaleVersion:
        messages.error(request, _('Your subscription changed meanwhile. Please try again.'))
    except (service.ChangeRefused, service.RecordGone):
        messages.error(request, _('Automatic renewal cannot be changed now.'))
    else:
        messages.success(request, _('Automatic renewal is on.') if on
                         else _('Automatic renewal is off.'))
    return redirect('upgrade')


def _periods(level, monthly_possible):
    """The periods the doctor may buy `level` for, as the page offers them."""
    periods = []
    if level.monthly is not None and monthly_possible:
        periods.append({'value': MONTHLY, 'price': level.monthly})
    if level.upfront is not None:
        periods.append({'value': UPFRONT, 'price': level.upfront})
    return periods


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def upgrade_purchase(request, level):
    options = service.upgrade_options(request.user)
    if not options.on_offer:
        return redirect('welcome')
    renewal = service.renewal_info(request.user)
    if renewal is not None and level == renewal.tier:
        return _renewal(request, renewal)
    if not options.can_switch or options.tier in service.PAID_TIERS:
        if options.can_switch:
            messages.info(request, _('To change between paid levels, please contact us.'))
        return redirect('upgrade')
    found = options.level(level)
    if found is None or not found.can_upgrade:
        raise Http404('not a level this doctor can upgrade to')
    pending = service.open_purchase(request.user)
    if pending is not None:
        return redirect('checkout', pk=pending.pk)
    monthly_possible = service.monthly_possible(request.user)
    periods = _periods(found, monthly_possible)
    if request.method == 'POST':
        return _purchase(request, found, periods)
    return _page(request, found, periods)


def _renewal(request, renewal):
    """The purchase page in renewal mode ("Renew now"): the held level, a year upfront
    only, the voucher field. Outside the last 30 days of the term, back to `upgrade`."""
    if not renewal.can_renew_now:
        return redirect('upgrade')
    pending = service.open_purchase(request.user)
    if pending is not None:
        return redirect('checkout', pk=pending.pk)
    language = get_language()
    row = renewal.subscription
    level = {'tier': renewal.tier, 'name': _name(row, renewal.tier, language),
             'description': row.description_in(language), 'renewal': True,
             'ends_on': renewal.ends_on}
    periods = [{'value': UPFRONT, 'price': renewal.price}]
    if request.method != 'POST':
        return _page(request, level, periods)
    voucher_code = (request.POST.get('voucher') or '').strip()[:VOUCHER_LENGTH]
    try:
        order = service.renew_now(request.user, voucher_code)
    except pricing.OrderRefused as refused:
        return _page(request, level, periods, refusal_message(refused), voucher_code)
    except payments.NoPaymentMethod:
        return _page(request, level, periods,
                     _('There is no payment method for this order yet.'), voucher_code)
    except payments.OrderError:
        return _page(request, level, periods,
                     _('Something went wrong. Please try again in a moment.'), voucher_code)
    if order.paid_at is not None:
        return redirect('checkout_return', pk=order.pk)
    return redirect('checkout', pk=order.pk)


def _page(request, level, periods, error='', voucher_code='', chosen=''):
    values = [period['value'] for period in periods]
    if not isinstance(level, dict):
        level = _level(level, get_language())
    return render(request, 'subscriptions/upgrade_purchase.html', {
        'level': level, 'periods': periods,
        'chosen': chosen if chosen in values else (UPFRONT if UPFRONT in values
                                                   else values[0] if values else ''),
        'error': error, 'voucher_code': voucher_code,
    })


def _purchase(request, level, periods):
    """Ask the service to buy `level` for the posted period and voucher code, and send the
    doctor on; a refusal shows the page again with its message, and nothing is created.
    The rules (periods offered, voucher with monthly, the open order, the card) are the
    service's."""
    period = request.POST.get('period', '')
    voucher_code = (request.POST.get('voucher') or '').strip()[:VOUCHER_LENGTH]
    try:
        order = service.purchase(request.user, level.subscription, period, voucher_code)
    except pricing.OrderRefused as refused:
        return _page(request, level, periods, refusal_message(refused), voucher_code,
                     period)
    except payments.NoPaymentMethod:
        return _page(request, level, periods,
                     _('There is no payment method for this order yet.'), voucher_code,
                     period)
    except payments.OrderError:
        return _page(request, level, periods,
                     _('Something went wrong. Please try again in a moment.'),
                     voucher_code, period)
    if order.paid_at is not None:  # a voucher covered it all: paid at once, no gateway
        return redirect('checkout_return', pk=order.pk)
    return redirect('checkout', pk=order.pk)
