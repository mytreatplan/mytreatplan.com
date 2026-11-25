"""The ICE deduction (epic-ordering 9.7; user decisions 2026-11-25): a doctor who paid an
initial case evaluation (ICE) for a patient gets its net amount off that patient's planning
(a TPS or a VirtuaOrtho service), once.

- `available_credit(doctor, patient_code, company_code, currency)`: (the ICE order line,
  its `net` as `Money`) of the oldest ICE the doctor paid for the patient, billed by that
  company in that currency, that no live deduction holds and no planning paid after it
  without a deduction used (an ICE paid before 9.7); None otherwise. Only an order
  that is `paid` gives one (a `refunded` order gives none). `cart.service` asks it for each
  planning line when the cart becomes an order; the pricing caps the credit at the line.
- `reserve(order)`, `consume(order)` and `release(order)`: the receivers of the order hooks
  `created`, `paid`, and `cancelled` / `expired` (registered in `PatientsConfig.ready`).
  They re-derive each credited line's ICE line (a line with `ice_asked`; none for an order
  without one, or a subscription order) and are idempotent per order and patient. A
  reservation the database refuses (two orders racing for one ICE) or an ICE that is no
  longer the one the line was priced with raises `IceTaken`, which rolls the order back
  (`payments.service.HookFailed`). A consumed deduction is never released.

No patient name is read or logged here: lines and deductions name the patient by code only,
and the log lines name the order by its UUID.
"""
import logging
from dataclasses import replace

from django.db import IntegrityError, transaction
from django.utils import timezone

from catalogue import pricing, resolver
from catalogue.models import Product
from mytp_core.money import Money
from payments.models import Order, OrderLine

from .models import IceDeduction

logger = logging.getLogger(__name__)

RESERVED, CONSUMED, RELEASED = (IceDeduction.RESERVED, IceDeduction.CONSUMED,
                                IceDeduction.RELEASED)


class IceTaken(Exception):
    """A credited line's ICE is held by another order, or is no longer the credit the line
    was priced with: the order is not created."""


def _is_ice(line):
    product = resolver.product(line.product_id) if line.product_id else None
    return product is not None and product.category == Product.ICE


def _planned_since(ice_line, order_id=None):
    """Whether a planning line of the same doctor and patient, billed by the same company in
    the same currency, was paid after the ICE (its order paid or refunded since) with no
    deduction pointing at it: a planning paid in full before 9.7 was the ICE's first later
    planning, so the ICE is used. A line with `included_case` set, or of the order
    `order_id` (the one being reserved or consumed), does not count."""
    order = ice_line.order
    planning = Product.objects.filter(category__in=pricing.PLANNING_CATEGORIES).values('pk')
    deducted = IceDeduction.objects.values('order_line_id')
    after = ({'order__paid_at__gt': order.paid_at} if order.paid_at is not None
             else {'order__created_at__gt': order.created_at})
    return (OrderLine.objects
            .filter(order__user_id=order.user_id, order__kind=Order.SERVICE,
                    order__state__in=(Order.PAID, Order.REFUNDED),
                    order__company_code=order.company_code, order__currency=order.currency,
                    patient_code=ice_line.patient_code, product_id__in=planning,
                    included_case=False, **after)
            .exclude(pk__in=deducted).exclude(order_id=order_id).exists())


def _ice_lines(doctor_id, patient_code, company_code, currency, order_id=None):
    """The paid ICE lines of the doctor for the patient, billed by that company in that
    currency, that no live deduction holds and no later planning used (`_planned_since`),
    oldest first (an ICE paid at 0.00 gives none)."""
    held = IceDeduction.objects.filter(state__in=IceDeduction.LIVE_STATES).values('ice_line_id')
    candidates = (OrderLine.objects.select_related('order')
                  .filter(order__user_id=doctor_id, order__kind=Order.SERVICE,
                          order__state=Order.PAID, order__company_code=company_code,
                          order__currency=currency, patient_code=patient_code)
                  .exclude(product_id=None).exclude(pk__in=held)
                  .order_by('order__paid_at', 'order__created_at', 'position'))
    return [line for line in candidates
            if _is_ice(line) and line.net > 0 and not _planned_since(line, order_id)]


def available_credit(doctor, patient_code, company_code, currency):
    """(ICE order line, `Money`) of the ICE credit the doctor's planning for `patient_code`
    may take, or None (see the module docstring)."""
    if doctor is None or not patient_code or not company_code or not currency:
        return None
    found = _ice_lines(doctor.pk, patient_code, company_code, currency)
    if not found:
        return None
    return found[0], Money(found[0].net, currency)


def _credited(order):
    """The order's lines that asked for an ICE credit (by position): none for a subscription
    order or one without `ice_asked`. A line with `included_case` set takes no ICE."""
    if order.kind != Order.SERVICE:
        return []
    return list(order.lines.exclude(ice_asked=None).exclude(included_case=True)
                .order_by('position'))


def _deductions(order):
    """{patient_code: deduction} of the order, locked."""
    return {found.patient_code: found for found in
            IceDeduction.objects.select_for_update().filter(order_id=order.pk)}


def _reserve_line(order, line, now):
    if order.user is None:
        raise IceTaken(f'order {order.pk}: an ICE credit is reserved for an account')
    found = _ice_lines(order.user_id, line.patient_code, order.company_code, order.currency,
                       order.pk)
    if not line.patient_code or not found or found[0].net != line.ice_asked:
        raise IceTaken(f'order {order.pk}: line {line.position} asked for an ICE credit '
                       'that is no longer available')
    try:
        with transaction.atomic():
            return IceDeduction.objects.create(
                ice_line_id=found[0].pk, order_id=order.pk, order_line_id=line.pk,
                doctor_uid=order.user.uid, patient_code=line.patient_code, state=RESERVED,
                amount=line.ice, currency=order.currency, created_at=now, updated_at=now)
    except IntegrityError:
        raise IceTaken(f'order {order.pk}: line {line.position}\'s ICE is held by another '
                       'order') from None


def reserve(order):
    """The order was created: hold the ICE of each credited line (`reserved`). Idempotent;
    an order with no credited line is left alone. Raises `IceTaken`."""
    lines = _credited(order)
    if not lines:
        return []
    existing = _deductions(order)
    now = timezone.now()
    return [existing.get(line.patient_code) or _reserve_line(order, line, now)
            for line in lines]


def consume(order):
    """The order was paid: its deductions are `consumed` (and point at the lines as they are
    now). Idempotent. A credited line without a deduction is reserved first; when its ICE is
    held by another order meanwhile the payment still goes through and the case is logged
    (the money has arrived: a hook must not undo it)."""
    lines = _credited(order)
    if not lines:
        return []
    existing = _deductions(order)
    now = timezone.now()
    done = []
    for line in lines:
        deduction = existing.get(line.patient_code)
        if deduction is None:
            try:
                deduction = _reserve_line(order, line, now)
            except IceTaken:
                logger.error('order %s: an ICE credit could not be deducted at payment',
                             order.pk)
                continue
        if deduction.state == RELEASED:
            logger.error('order %s: paid, but an ICE deduction was released', order.pk)
        elif deduction.state == RESERVED:
            deduction.state, deduction.updated_at = CONSUMED, now
            deduction.order_line_id, deduction.amount = line.pk, line.ice
            deduction.save(update_fields=['state', 'order_line_id', 'amount', 'updated_at'])
        done.append(deduction)
    return done


def release(order):
    """The order was cancelled or expired: its `reserved` deductions are `released` and the
    ICE can be deducted again. A consumed or released one stays. Idempotent."""
    now = timezone.now()
    released = []
    for deduction in _deductions(order).values():
        if deduction.state == RESERVED:
            deduction.state, deduction.updated_at = RELEASED, now
            deduction.save(update_fields=['state', 'updated_at'])
            released.append(deduction)
    return released


def credited_lines(doctor, lines, country):
    """`lines` (`pricing.OrderLine`s) with the ICE credit each planning line of a patient may
    take (`available_credit`), for a doctor billed in `country`. A line whose company or
    currency cannot be known gets none; a line with no credit is returned as it is."""
    group = resolver.country_group(country)
    result = []
    for line in lines:
        credit = None
        if (group is not None and line.patient_code and line.ice is None
                and line.product.category in pricing.PLANNING_CATEGORIES):
            rule = resolver.billing_rule_for(group, line.product.category)
            if rule is not None:
                credit = available_credit(doctor, line.patient_code, rule.company.code,
                                          line.product.net_price.currency)
        result.append(replace(line, ice=credit[1]) if credit else line)
    return result
