"""The one write path of orders (epic-payments entry 1, AD-15, AD-16; user decisions
2026-11-18). Everything that creates or moves an order goes through here.

Creating (`create_order`, `create_subscription_order`): the order is priced by
`subscriptions.service.price_order_for` (a service order) or
`catalogue.pricing.price_subscription` (a subscription purchase) with the buyer's VIES
status, and copied: totals, VAT groups, company, gateways, and per line the priced values and
the provider cost. Nothing here does price arithmetic. It is born in `checkout` with the
buyer's default usable fiscal entity (or the one given); the `created` hook fires there. A
voucher is read again from the catalogue (`voucher_unknown` when it is gone) and checked for
use (`subscriptions.vouchers.check_use`, `voucher_used`) before anything is written; the
redemption itself is reserved, consumed and released by the hooks of `subscriptions.vouchers`. An
order with nothing to pay (0.00) and an entity is paid at creation (no gateway; its `paid`
hook fires too). Without a payment method for a total above 0 no order is started.

Moving: `choose_entity` (checkout only: copies the entity again and re-prices from the
stored line inputs), `start_payment` (freezes the order as `pending_payment` and marks the
entity used, in the same transaction), `mark_paid`, `cancel`, `expire` and `expire_stale`.
Each locks the order (`select_for_update`), checks `expected_version`, raises the version by
one, stores the actor and adds an `OrderChange`; each state change fires its hook inside the
transaction, exactly once (a replay on an order already in the target state does nothing and
fires nothing; a hook that raises rolls the transition back: `HookFailed`).

Payments (entry 2): `start_payment` also opens a `Payment` (one attempt at the gateway; one
open attempt per order: a retry first closes the previous one at the gateway, or is refused
while its session may still be live: `AttemptOpen`). While an attempt is open the order is
frozen: `cancel` is refused and `expire` / `expire_stale` skip it until the attempt's
session timed out. `confirm_payment` is the one way a gateway's payment pays an order (the
webhook task and the return page): the payment was re-read from the gateway, its amount and
currency equal the order's, and it is the order's open attempt; anything else is a payment
that needs staff attention.
"""
import logging
import uuid
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from accounts import fiscal
from catalogue import pricing, resolver
from catalogue.models import AddOn, Voucher
from invoices import feed
from mytp_core.money import Money
from subscriptions import service as subscriptions_service
from subscriptions import vouchers as voucher_redemptions

from . import gateways, hooks, reference
from .models import Order, OrderChange, OrderLine, Payment

logger = logging.getLogger(__name__)

Actor = subscriptions_service.Actor
DOCTOR, ADMIN, SYSTEM = 'doctor', 'admin', 'system'
SYSTEM_ACTOR = subscriptions_service.SYSTEM_ACTOR
doctor_actor = subscriptions_service.doctor_actor
admin_actor = subscriptions_service.admin_actor

CREATED, ENTITY_CHOSEN, PAYMENT_STARTED = 'created', 'entity_chosen', 'payment_started'
BANK_TRANSFER = 'bank_transfer'
PAID, CANCELLED, EXPIRED = 'paid', 'cancelled', 'expired'
# How long an order may stay unpaid in `checkout` or `pending_payment` before the
# `expire_orders` job expires it (a bank transfer's 14 days: its attempt's session keeps the
# order open).
EXPIRY_HOURS = 24
_REFERENCE_TRIES = 5


class OrderError(Exception):
    """Base of the refusals below: nothing was changed."""


class StaleVersion(OrderError):
    """The order changed since the caller read it; `current` is its version now."""

    def __init__(self, current):
        super().__init__(f'the order is at version {current}')
        self.current = current


class InvalidTransition(OrderError):
    """The order's state does not allow this move (a paid order cannot be cancelled)."""


class InvalidActor(OrderError):
    """Not an actor: a doctor or an admin user has a UUID, the system has none."""


class EntityRefused(OrderError):
    """The fiscal entity is not one of the buyer's usable entities."""


class NoEntity(OrderError):
    """The order has no usable fiscal entity yet."""


class EntityChanged(OrderError):
    """The order's copy of its fiscal entity is not the entity's current state: choose it
    again (`choose_entity`) before paying."""


class NoPaymentMethod(OrderError):
    """The order needs payment and no gateway is offered."""


class GatewayRefused(OrderError):
    """The gateway is not one of the order's offered gateways."""


class AttemptOpen(OrderError):
    """A payment attempt of the order is open: its gateway session may still be live, so
    the order cannot be cancelled or expired and another attempt cannot be started yet."""


class AmountMismatch(OrderError):
    """The amount received is not the order's total (no partial payments)."""


class HookFailed(OrderError):
    """A hook receiver raised; the transition was rolled back."""

    def __init__(self, event):
        super().__init__(f'a {event} hook failed')
        self.event = event


def _check_actor(actor):
    if not isinstance(actor, Actor) or actor.type not in (DOCTOR, ADMIN, SYSTEM):
        raise InvalidActor(f'not an actor: {actor!r}')
    if (actor.type == SYSTEM) != (actor.uuid is None):
        raise InvalidActor('the system has no UUID; a doctor or an admin user has one')


# --- Copying the pricing ----------------------------------------------------------------------

_BILL = {name: ('bill_billing_email' if name == 'billing_email' else f'bill_{name}')
         for name in fiscal.FIELDS}


def _country(user):
    return user.country.code if user.country else ''


def _text(amount):
    return str(amount)


def _snapshot_entity(order, entity):
    """Copy the fiscal entity into the order (or empty the copy for None)."""
    for name, target in _BILL.items():
        value = '' if entity is None else getattr(entity, name)
        setattr(order, target, value.code if name == 'country' and value else value)
    order.fiscal_entity = entity
    order.bill_entity_id = entity.pk if entity is not None else None
    order.bill_entity_version = entity.version if entity is not None else None
    order.bill_vies_status = fiscal.vies_status(entity) if entity is not None else ''


def _copy_totals(order, priced):
    order.currency = priced.currency
    order.company_code = priced.company.code
    order.company_legal_name = priced.company.legal_name
    order.gateways = list(priced.gateways)
    order.net_total = priced.net_total.amount
    order.discount_total = priced.discount_total.amount
    order.ice_total = priced.ice_total.amount
    order.voucher_taken = priced.voucher_taken.amount
    order.vat_total = priced.vat_total.amount
    order.total = priced.total.amount
    order.vat_groups = [{'percent': _text(group.percent), 'text': group.text,
                         'base': _text(group.base.amount), 'vat': _text(group.vat.amount)}
                        for group in priced.vat_groups]


def _line_rows(order, priced, asked):
    """The `OrderLine`s (unsaved) of `priced`; `asked` is each line's input: a
    `pricing.OrderLine` for a service order, (subscription, period) for a purchase."""
    rows = []
    for position, (line, wanted) in enumerate(zip(priced.lines, asked), start=1):
        row = OrderLine(
            order=order, position=position, quantity=line.quantity,
            list_amount=line.list_amount.amount, discount_percent=line.discount_percent,
            discount_amount=line.discount.amount, ice=line.ice.amount,
            voucher_share=line.voucher.amount, net=line.net.amount,
            vat_percent=line.vat_percent, vat_text=line.vat_text)
        if line.product is not None:
            product = line.product
            row.product_id, row.code, row.name = product.pk, product.code or '', product.name
            row.add_ons = [{'id': str(add_on.pk), 'code': add_on.code,
                            'name': add_on.name_in('en'),
                            'percent': None if add_on.percent is None else _text(add_on.percent),
                            'amount': None if add_on.amount is None else _text(add_on.amount)}
                           for add_on in wanted.add_ons]
            row.ice_asked = None if wanted.ice is None else wanted.ice.amount
            cost = resolver.cost(product)
            row.provider_cost = (cost.total * line.quantity).amount
            row.cost_complete = bool(cost.complete)
        else:
            subscription, period = wanted
            row.subscription_id, row.period = subscription.pk, period
            row.code = subscription.code or ''
            row.name = subscription.name_in('en') or subscription.code or subscription.tier
            row.provider_cost, row.cost_complete = Decimal('0.00'), True
        rows.append(row)
    return rows


def _inputs(order):
    """The line inputs of a stored order, as pricing takes them."""
    rows = list(order.lines.order_by('position'))
    if order.kind == Order.SUBSCRIPTION:
        row = rows[0]
        subscription = resolver.subscription_by_id(row.subscription_id)
        if subscription is None:
            raise pricing.OrderRefused(
                pricing.SUBSCRIPTION_NOT_OFFERED,
                'the subscription is no longer in the catalogue', subject=None)
        return [(subscription, row.period)]
    asked = []
    for row in rows:
        product = resolver.product(row.product_id)
        if product is None:
            raise pricing.OrderRefused(pricing.PRODUCT_NOT_OFFERED,
                                       'the product is no longer in the catalogue')
        add_ons = tuple(AddOn.objects.filter(
            pk__in=[item['id'] for item in row.add_ons]).order_by('code', 'id'))
        if len(add_ons) != len(row.add_ons):
            raise pricing.OrderRefused(pricing.ADD_ON_REFUSED,
                                       'an add-on is no longer in the catalogue',
                                       subject=product)
        ice = None if row.ice_asked is None else Money(row.ice_asked, order.currency)
        asked.append(pricing.OrderLine(product, row.quantity, add_ons, ice))
    return asked


def _price(user, kind, asked, entity, voucher):
    vies = fiscal.vies_status(entity)
    if kind == Order.SUBSCRIPTION:
        (subscription, period), = asked
        return pricing.price_subscription(_country(user), subscription, period, vies=vies,
                                          voucher=voucher, doctor=user.uid)
    return subscriptions_service.price_order_for(user, asked, vies=vies, voucher=voucher)


def _unknown_voucher():
    raise pricing.OrderRefused(pricing.VOUCHER_UNKNOWN,
                               'the voucher is not in this region\'s catalogue')


def _voucher_of(order):
    if not order.voucher_id:
        return None
    voucher = Voucher.objects.filter(pk=order.voucher_id).first()
    if voucher is None:
        raise pricing.OrderRefused(pricing.VOUCHER_NOT_USABLE,
                                   'the voucher is no longer in the catalogue')
    return voucher


# --- Writing ---------------------------------------------------------------------------------

def _stamp(order, actor, now):
    order.version += 1
    order.actor_type, order.actor_uuid = actor.type, actor.uuid
    order.updated_at = now


def _change(order, actor, event, from_state, now, **detail):
    OrderChange.objects.create(
        order=order, at=now, actor_type=actor.type, actor_uuid=actor.uuid, event=event,
        from_state=from_state, to_state=order.state, version=order.version, detail=detail)


def _fire(event, order):
    try:
        hooks.fire(event, order)
    except Exception as failure:
        logger.error('order %s: a %s hook failed: %s', order.pk, event,
                     type(failure).__name__, exc_info=True)
        raise HookFailed(event) from failure


def _lock(order_id):
    pk = order_id.pk if isinstance(order_id, Order) else order_id
    order = Order.objects.select_for_update().filter(pk=pk).first()
    if order is None:
        raise OrderError(f'no order {pk}')
    return order


def _check_version(order, expected_version):
    if order.version != expected_version:
        raise StaleVersion(order.version)


def _usable(user, entity):
    """`entity` (a FiscalEntity or its id) when it is one of the user's usable entities."""
    try:
        pk = uuid.UUID(str(getattr(entity, 'pk', entity)))
    except ValueError:
        raise EntityRefused('not a fiscal entity') from None
    found = fiscal.usable_entities(user).filter(pk=pk).first()
    if found is None:
        raise EntityRefused('not one of the buyer\'s usable fiscal entities')
    return found


def _create(user, kind, asked, *, actor, voucher, entity):
    _check_actor(actor)
    if entity is None:
        entity = fiscal.default_entity(user)
    else:
        entity = _usable(user, entity)
    if voucher is not None:
        # The snapshot row as it is now: a code that is gone (unknown, or another region's)
        # is refused with its own reason, and the checks below read the current row.
        voucher = Voucher.objects.filter(pk=voucher.pk).first() or _unknown_voucher()
    priced = _price(user, kind, asked, entity, voucher)  # OrderRefused: no order
    if voucher is not None:
        # One use at a time (epic-subscriptions entry 4): a clear refusal here; two orders
        # racing past this are stopped by the constraints at the `created` hook.
        voucher_redemptions.check_use(voucher, user.uid)
    now = timezone.now()
    for attempt in range(_REFERENCE_TRIES):
        try:
            with transaction.atomic():
                order = Order(
                    user=user, kind=kind, voucher_id=voucher.pk if voucher else None,
                    reference=reference.unused(
                        lambda ref: Order.objects.filter(reference=ref).exists()),
                    actor_type=actor.type, actor_uuid=actor.uuid, created_at=now,
                    updated_at=now)
                _copy_totals(order, priced)
                if priced.needs_payment and not gateways.offered(order):
                    raise NoPaymentMethod('no payment method is available for this order')
                _snapshot_entity(order, entity)
                order.save()
                OrderLine.objects.bulk_create(_line_rows(order, priced, asked))
                _change(order, actor, CREATED, '', now)
                _fire('created', order)
                if not priced.needs_payment and entity is not None:
                    _pay_free(order, actor, now)
                return order
        except IntegrityError:
            # Two orders drew the same reference at once: draw again.
            if attempt == _REFERENCE_TRIES - 1:
                raise


def create_order(user, lines, *, actor, voucher=None, entity=None):
    """Create the service order of `user` for `lines` (`catalogue.pricing.OrderLine`s),
    optionally with a voucher (`catalogue.models.Voucher`) and a fiscal entity (the
    default usable one when None); see the module docstring. Raises `pricing.OrderRefused`,
    `EntityRefused` or `NoPaymentMethod`, creating nothing."""
    return _create(user, Order.SERVICE, list(lines), actor=actor, voucher=voucher,
                   entity=entity)


def create_subscription_order(user, subscription, period, *, actor, voucher=None,
                              entity=None):
    """Create the order of buying `subscription` (a `catalogue.models.Subscription`) for a
    `period` (`monthly` or `upfront`): one line with its period, billed by the
    `subscription` rule's company and VAT (for epic-subscriptions entry 5)."""
    return _create(user, Order.SUBSCRIPTION, [(subscription, period)], actor=actor,
                   voucher=voucher, entity=entity)


def _pay_free(order, actor, now):
    """A 0.00 order is frozen and paid in one move (its entity used, its hook fired)."""
    fiscal.mark_used(order.fiscal_entity)
    before = order.state
    order.state, order.paid_at = Order.PAID, now
    order.pending_at = now
    _stamp(order, actor, now)
    order.save()
    _change(order, actor, PAID, before, now, free=True)
    _fire('paid', order)


def choose_entity(order, entity, *, expected_version, actor):
    """Use `entity` (one of the buyer's usable entities) for the order in `checkout`:
    copy it again and re-price the order from its stored line inputs (its VIES status
    changes the VAT). Also re-copies the same entity after it changed. Returns the
    order."""
    _check_actor(actor)
    with transaction.atomic():
        locked = _lock(order)
        _check_version(locked, expected_version)
        if locked.state != Order.CHECKOUT:
            raise InvalidTransition('the fiscal entity changes only in checkout')
        if locked.user is None:
            raise InvalidTransition('the buyer\'s account is gone')
        entity = _usable(locked.user, entity)
        asked = _inputs(locked)
        priced = _price(locked.user, locked.kind, asked, entity,
                        _voucher_of(locked))  # OrderRefused: nothing changed
        _copy_totals(locked, priced)
        _snapshot_entity(locked, entity)
        locked.lines.all().delete()
        OrderLine.objects.bulk_create(_line_rows(locked, priced, asked))
        now = timezone.now()
        _stamp(locked, actor, now)
        locked.save()
        _change(locked, actor, ENTITY_CHOSEN, locked.state, now)
        return locked


def start_payment(order, gateway, *, expected_version, actor):
    """Freeze the order as `pending_payment` with `gateway` (one of `gateways.offered`)
    and mark its fiscal entity used, in one transaction; from `pending_payment` (a retry
    after a failure) only the gateway may change. A 0.00 order needs no gateway
    (`gateway` ''): it is paid here. Returns the order."""
    _check_actor(actor)
    # Gateway calls, outside the lock. A bank transfer attempt is closed later, under the
    # lock, once every check has passed (a refused request must not lose it).
    _cancel_live_attempts(getattr(order, 'pk', order), exclude=BANK_TRANSFER)
    with transaction.atomic():
        locked = _lock(order)
        _check_version(locked, expected_version)
        if locked.state not in Order.OPEN_STATES:
            raise InvalidTransition(f'an order that is {locked.state} cannot be paid')
        entity = locked.fiscal_entity
        now = timezone.now()
        if locked.state == Order.CHECKOUT:
            if entity is None or not fiscal.is_usable(entity):
                raise NoEntity('the order has no usable fiscal entity')
        elif locked.bill_entity_id is None:
            raise NoEntity('the order has no fiscal entity')
        if locked.state == Order.CHECKOUT and (
                entity.version != locked.bill_entity_version
                or fiscal.vies_status(entity) != locked.bill_vies_status):
            raise EntityChanged('the fiscal entity changed since it was chosen')
        if locked.total <= 0:
            if gateway:
                raise GatewayRefused('an order of 0.00 has no gateway')
            _pay_free(locked, actor, now)
            return locked
        if gateway not in gateways.offered(locked):
            raise GatewayRefused(f'the gateway {gateway!r} is not offered for this order')
        live = _live_attempts(locked, now)
        if [payment for payment in live if payment.gateway != BANK_TRANSFER]:
            raise AttemptOpen('a payment attempt of the order is open')  # one opened since
        lifetime = gateways.adapter(gateway).session_lifetime
        session_ends = now + lifetime
        if gateway == BANK_TRANSFER:
            # The 14 days run from the first freeze: choosing transfer again never renews them.
            session_ends = min(session_ends, (locked.pending_at or now) + lifetime)
            if session_ends <= now:
                raise GatewayRefused('the 14 days of the bank transfer have ended')
        for payment in live:  # the open transfer attempt: closed locally, nothing to cancel
            _set_payment_state(payment, Payment.CLOSED, now)
        before = locked.state
        if before == Order.CHECKOUT:
            fiscal.mark_used(entity)
            locked.pending_at = now
        locked.state, locked.gateway = Order.PENDING_PAYMENT, gateway
        _stamp(locked, actor, now)
        locked.save()
        _change(locked, actor, PAYMENT_STARTED, before, now, gateway=gateway)
        Payment.objects.create(
            order=locked, gateway=gateway, amount=locked.total, currency=locked.currency,
            session_expires_at=session_ends,
            created_at=now, updated_at=now)
        return locked


def _move(order, to, event, allowed, stamp_field, *, expected_version, actor, now=None):
    """Move the order to `to` once, with its hook: a replay on an order already there
    returns it untouched."""
    _check_actor(actor)
    with transaction.atomic():
        locked = _lock(order)
        if locked.state == to:
            return locked
        _check_version(locked, expected_version)
        if locked.state not in allowed:
            raise InvalidTransition(f'an order that is {locked.state} cannot be {to}')
        now = now or timezone.now()
        if to in (Order.CANCELLED, Order.EXPIRED) and _live_attempts(locked, now):
            raise AttemptOpen('a payment attempt of the order is open')
        before = locked.state
        locked.state = to
        setattr(locked, stamp_field, now)
        _stamp(locked, actor, now)
        locked.save()
        _change(locked, actor, event, before, now)
        _fire(event, locked)
        return locked


def mark_paid(order, *, expected_version, actor):
    """The payment arrived: `pending_payment` -> `paid`, firing `paid` once. Already paid:
    nothing happens."""
    return _move(order, Order.PAID, PAID, (Order.PENDING_PAYMENT,), 'paid_at',
                 expected_version=expected_version, actor=actor)


def cancel(order, *, expected_version, actor):
    """The buyer gives up: an unpaid order -> `cancelled`, firing `cancelled`. A paid or
    expired order is refused. An open bank transfer attempt is closed here (nothing to
    cancel at a bank), in the same transaction; an open attempt of another gateway still
    refuses (`AttemptOpen`)."""
    with transaction.atomic():
        locked = _lock(order)  # the order first, then its attempts (as `mark_transfer_paid`)
        if locked.state in Order.OPEN_STATES and locked.version == expected_version:
            _cancel_live_attempts(locked.pk, only=BANK_TRANSFER)
        return _move(locked, Order.CANCELLED, CANCELLED, Order.OPEN_STATES, 'cancelled_at',
                     expected_version=expected_version, actor=actor)


def expire(order, *, expected_version, actor=SYSTEM_ACTOR, now=None):
    """An unpaid order ran out of time -> `expired`, firing `expired`. Refused
    (`AttemptOpen`) while a payment attempt of the order has a live gateway session."""
    return _move(order, Order.EXPIRED, EXPIRED, Order.OPEN_STATES, 'expired_at',
                 expected_version=expected_version, actor=actor, now=now)


def expire_stale(now=None):
    """Expire every `checkout` order created, and every `pending_payment` order frozen,
    more than EXPIRY_HOURS ago (the `expire_orders` job, every 15 minutes); each in its own
    transaction, a failing one logged by its UUID and skipped. Returns how many were
    expired. Running it again expires none more."""
    now = now or timezone.now()
    cutoff = now - timedelta(hours=EXPIRY_HOURS)
    expired = 0
    stale = (Q(state=Order.CHECKOUT, created_at__lt=cutoff)
             | Q(state=Order.PENDING_PAYMENT, pending_at__lt=cutoff))
    for pk, version in list(Order.objects.filter(stale).values_list('pk', 'version')):
        try:
            expire(pk, expected_version=version, now=now)
        except (StaleVersion, AttemptOpen):
            continue  # moved meanwhile, or paying now; the next run looks again
        except OrderError as failure:
            logger.error('order %s: not expired: %s', pk, type(failure).__name__)
            continue
        expired += 1
    return expired


def order_of(user, order_id):
    """The order `order_id` when it is `user`'s, else None."""
    if not isinstance(order_id, UUID):
        try:
            order_id = uuid.UUID(str(order_id))
        except ValueError:
            return None
    return Order.objects.filter(pk=order_id, user_id=user.pk).first()


# --- Payment attempts and confirmation (entry 2) ---------------------------------------------

CONFIRMED, STILL_PENDING, FAILED_AT_GATEWAY = 'confirmed', 'pending', 'failed'
AMOUNT_MISMATCH, NOT_PAYABLE = 'amount_mismatch', 'not_payable'


def _live_attempts(order, now):
    """The order's open attempts whose gateway session may still be live; those whose
    session timed out are closed here (the caller holds the order's lock)."""
    live = []
    for payment in order.payments.select_for_update().filter(state=Payment.OPEN):
        if payment.session_expires_at is not None and payment.session_expires_at <= now:
            _set_payment_state(payment, Payment.CLOSED, now)
        else:
            live.append(payment)
    return live


def _set_payment_state(payment, state, now, **fields):
    payment.state, payment.updated_at = state, now
    for name, value in fields.items():
        setattr(payment, name, value)
    payment.save()


def _cancel_live_attempts(order_id, only=None, exclude=None):
    """Before a new attempt: close every live open attempt at its gateway (`cancel_attempt`,
    an HTTP call: no transaction or lock is held) and mark each closed in its own short
    transaction once the gateway confirmed. A gateway that cannot cancel (or is not
    available), or fails to: `AttemptOpen` until the session times out. `only`: just the
    attempts of that gateway; `exclude`: all but that gateway's."""
    now = timezone.now()
    attempts = Payment.objects.filter(order_id=order_id, state=Payment.OPEN)
    if only is not None:
        attempts = attempts.filter(gateway=only)
    if exclude is not None:
        attempts = attempts.exclude(gateway=exclude)
    for payment in attempts:
        if payment.session_expires_at is not None and payment.session_expires_at <= now:
            continue  # timed out: closed under the lock by `_live_attempts`
        found = gateways.adapter(payment.gateway)
        if found is None or not found.supports('cancel_attempt'):
            raise AttemptOpen('the previous attempt is open until its session times out')
        try:
            found.call('cancel_attempt', payment)
        except Exception as failure:  # noqa: BLE001 - any gateway fault: keep it open
            logger.warning('order %s: an attempt was not closed at %s (%s)', order_id,
                           payment.gateway, type(failure).__name__)
            raise AttemptOpen('the previous attempt could not be closed') from failure
        with transaction.atomic():
            locked = Payment.objects.select_for_update().get(pk=payment.pk)
            if locked.state == Payment.OPEN:
                _set_payment_state(locked, Payment.CLOSED, timezone.now())


def open_payment(order):
    """The order's open attempt, or None."""
    return Payment.objects.filter(order_id=getattr(order, 'pk', order),
                                  state=Payment.OPEN).first()


def extend_session(payment, lifetime, now=None):
    """The doctor is sent to the gateway's page now: an open attempt's session may be
    paid until `now + lifetime` (moved forward only; no gateway call). Returns the
    attempt."""
    now = now or timezone.now()
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        until = now + lifetime
        if locked.state == Payment.OPEN and (locked.session_expires_at is None
                                             or locked.session_expires_at < until):
            _set_payment_state(locked, Payment.OPEN, now, session_expires_at=until)
        return locked


def set_gateway_payment_id(payment, gateway_payment_id):
    """Record the gateway's id of the attempt (an adapter's `start` calls this)."""
    payment.gateway_payment_id = gateway_payment_id
    payment.updated_at = timezone.now()
    payment.save(update_fields=['gateway_payment_id', 'updated_at'])


def fail_payment(payment):
    """The gateway says the attempt failed (the doctor may retry or cancel): an open
    attempt becomes `failed`; any other is left as it is."""
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.state == Payment.OPEN:
            _set_payment_state(locked, Payment.FAILED, timezone.now())
        return locked


def report_attention(order_id, what):
    """An error to Sentry (and the log) naming the order by its UUID only (AD-23)."""
    import sentry_sdk
    message = f'payment {what}: order {order_id}'
    logger.warning(message)  # not error: the one explicit capture below reaches Sentry
    with sentry_sdk.new_scope() as scope:
        scope.set_tag('payments.attention', what)
        sentry_sdk.capture_message(message, level='error')


def confirm_payment(payment, gateway_payment):
    """Apply what the gateway reports of `payment` (re-read from the gateway, never from a
    webhook's body): the one path that pays an order, for the webhook task and the return
    page. Idempotent. Returns CONFIRMED, STILL_PENDING, FAILED_AT_GATEWAY, AMOUNT_MISMATCH
    or NOT_PAYABLE.

    Paid at the gateway: the order is paid (`mark_paid`, its `paid` hook once) only when the
    payment is the order's open attempt, the order is `pending_payment` and the gateway's
    amount and currency equal the order's total and currency. A different amount or
    currency, or an order that is not payable (paid already, cancelled, expired, or the
    attempt was closed or replaced since: a retry may switch gateways), leaves the order as
    it is and marks the payment `needs_attention`, with an error to Sentry by order UUID.
    A hook that raises (`HookFailed`) rolls everything back."""
    with transaction.atomic():
        order = _lock(payment.order_id)
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        now = timezone.now()
        if locked.state == Payment.CONFIRMED:
            return CONFIRMED
        if gateway_payment.status == gateways.FAILED:
            if locked.state == Payment.OPEN:
                _set_payment_state(locked, Payment.FAILED, now)
            return FAILED_AT_GATEWAY
        if gateway_payment.status != gateways.PAID:
            return STILL_PENDING
        if locked.state == Payment.NEEDS_ATTENTION:
            return NOT_PAYABLE
        if (gateway_payment.amount != order.total or gateway_payment.amount != locked.amount
                or gateway_payment.currency != order.currency
                or gateway_payment.currency != locked.currency):
            _set_payment_state(locked, Payment.NEEDS_ATTENTION, now)
            report_attention(order.pk, 'amount or currency differs')
            return AMOUNT_MISMATCH
        if locked.state != Payment.OPEN or order.state != Order.PENDING_PAYMENT:
            _set_payment_state(locked, Payment.NEEDS_ATTENTION, now)
            report_attention(order.pk, 'confirmed for an order that is not payable')
            return NOT_PAYABLE
        mark_paid(order, expected_version=order.version, actor=SYSTEM_ACTOR)
        _set_payment_state(locked, Payment.CONFIRMED, now, confirmed_at=now)
        feed.send_payment(locked)
        return CONFIRMED


def mark_transfer_paid(order, amount, *, expected_version, actor):
    """Staff (or the system) recorded that a bank transfer of `amount` arrived (epic-payments
    entry 5): the order's open `bank_transfer` attempt is `confirmed` and the order is
    paid (`mark_paid`, its `paid` hook once), in one transaction. Refused, changing
    nothing: `InvalidTransition` unless the order is `pending_payment` (an expired or
    cancelled order is re-sold or refunded by hand), `NoPaymentMethod` without an open
    transfer attempt, `AmountMismatch` unless `amount` equals the total, `StaleVersion`.
    An order already paid by this service is returned unchanged."""
    _check_actor(actor)
    if actor.type == DOCTOR:
        raise InvalidActor('a transfer is recorded by staff or the system')
    currency = getattr(amount, 'currency', None)
    if currency is not None and str(currency) != order.currency:
        raise AmountMismatch('the currency is not the order\'s')
    amount = getattr(amount, 'amount', amount)
    try:
        amount = Decimal(str(amount))
    except ArithmeticError:
        raise AmountMismatch('not an amount') from None
    with transaction.atomic():
        locked = _lock(order)
        if locked.state == Order.PAID and locked.payments.filter(
                gateway=BANK_TRANSFER, state=Payment.CONFIRMED).exists():
            return locked
        _check_version(locked, expected_version)
        if locked.state != Order.PENDING_PAYMENT:
            raise InvalidTransition(f'an order that is {locked.state} cannot be paid')
        attempt = locked.payments.select_for_update().filter(
            state=Payment.OPEN, gateway=BANK_TRANSFER).first()
        if attempt is None:
            raise NoPaymentMethod('the order has no open bank transfer')
        if amount != locked.total:
            raise AmountMismatch('the amount is not the order\'s total')
        now = timezone.now()
        paid = mark_paid(locked, expected_version=locked.version, actor=actor)
        _set_payment_state(attempt, Payment.CONFIRMED, now, confirmed_at=now)
        feed.send_payment(attempt)
        return paid


def needs_attention(order):
    """Whether a confirmed payment of the order could not pay it (the doctor sees an error
    message, staff check it)."""
    return order.payments.filter(state=Payment.NEEDS_ATTENTION).exists()
