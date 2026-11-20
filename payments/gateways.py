"""The payment gateway adapters (epic-payments entry 1; entry 2 is the full interface;
entries 3-4 add the real ones).

An adapter registers once, in its AppConfig.ready:

    payments.gateways.register('redsys', start, enabled=None, verify_webhook=..., ...)

Every operation is an optional callable; an adapter declares what it supports by giving it
(`Adapter.supports(op)`), and a missing one is never called (`UnsupportedOperation`).

- `start(payment, request)`: the URL the doctor is redirected to (a hosted page) once the
  order is `pending_payment` and `payment` (a `payments.models.Payment`, open) was created
  for it. It records the gateway's own id with `payments.service.set_gateway_payment_id`.
- `verify_webhook(request) -> WebhookEvent`: checks the signature (and the timestamp, within
  `timestamp_tolerance`, where the gateway sends one) of a webhook; raises `BadSignature`,
  `Stale(event)` or `Malformed`. It reads `request.body` only. It trusts nothing in the body.
- `fetch_payment(payment) -> GatewayPayment`: re-reads the payment from the gateway; the
  only source of the amount, currency and status that pay an order. Raises `GatewayError`
  (or any error of the HTTP helpers) when the gateway cannot be read.
- `cancel_attempt(payment)`: closes the open session at the gateway, so it can no longer be
  paid. A gateway without it waits for the session to time out (`session_lifetime`).
- `refund(payment, amount) -> str` (the gateway's refund id), `save_card(user) -> str` (a
  card token), `charge_saved_card(user, token, amount, currency) -> GatewayPayment`: used by
  entries 7 and later.
- `session_lifetime`: how long an open attempt can still be paid (default 30 minutes);
  `timestamp_tolerance`: default 5 minutes.
- `required_settings`: the settings (secrets, from settings.env) that must be set while the
  adapter is available on a public site (`payments.E003`).

`enabled` (optional, a callable without arguments) says whether the adapter is available
now (the fake one reads its flag there). An order offers the gateways its pricing named
(`Order.gateways`, from the billing rule and the region) that have an available adapter,
plus the fake one when it is available.
"""
import re
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Callable, Optional

NAME_RE = re.compile(r'^[a-z][a-z0-9_]{0,19}$')
FAKE = 'fake'

OPERATIONS = ('start', 'verify_webhook', 'fetch_payment', 'cancel_attempt', 'refund',
              'save_card', 'charge_saved_card')
DEFAULT_SESSION_LIFETIME = timedelta(minutes=30)
DEFAULT_TIMESTAMP_TOLERANCE = timedelta(minutes=5)

# GatewayPayment.status
PAID, PENDING, FAILED = 'paid', 'pending', 'failed'


class GatewayError(Exception):
    """The gateway could not be read or refused a call (never carries secrets or card
    data)."""


class UnsupportedOperation(GatewayError):
    """The adapter does not support this operation."""


class WebhookRefused(Exception):
    """Base of the refusals of `verify_webhook`."""


class BadSignature(WebhookRefused):
    """The signature is missing or wrong."""


class Malformed(WebhookRefused):
    """The body is not what the gateway sends."""


class Stale(WebhookRefused):
    """Signed correctly but its timestamp is outside the tolerance (a replay); carries the
    parsed `event`."""

    def __init__(self, event=None):
        super().__init__('stale webhook')
        self.event = event


@dataclass(frozen=True)
class WebhookEvent:
    """What a verified webhook says; informational until the payment is re-read.
    `event_id` is the gateway's event id, else its payment id (the dedupe key). `status`
    (PAID or FAILED, optional) is what the event says of the payment; it is stored on the
    log row, where a gateway whose signed notification is the record of the payment
    (Redsys, entry 3) reads it back in `fetch_payment`."""
    event_id: str
    payment_id: str
    amount: Optional[Decimal] = None
    currency: str = ''
    status: Optional[str] = None


@dataclass(frozen=True)
class GatewayPayment:
    """A payment as the gateway reports it when re-read: `status` is PAID, PENDING or
    FAILED."""
    gateway_payment_id: str
    status: str
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class Adapter:
    name: str
    start: Callable
    enabled: Optional[Callable] = None
    verify_webhook: Optional[Callable] = None
    fetch_payment: Optional[Callable] = None
    cancel_attempt: Optional[Callable] = None
    refund: Optional[Callable] = None
    save_card: Optional[Callable] = None
    charge_saved_card: Optional[Callable] = None
    session_lifetime: timedelta = DEFAULT_SESSION_LIFETIME
    timestamp_tolerance: timedelta = DEFAULT_TIMESTAMP_TOLERANCE
    required_settings: tuple = field(default_factory=tuple)
    # Optional `accepts(order) -> bool` (entry 5): whether this gateway can take this
    # order at all (bank transfer needs the billing company's bank details).
    accepts: Optional[Callable] = None

    def available(self):
        return self.enabled is None or bool(self.enabled())

    def supports(self, operation):
        if operation not in OPERATIONS:
            raise ValueError(f'unknown operation {operation!r}')
        return getattr(self, operation) is not None

    def call(self, operation, *args, **kwargs):
        if not self.supports(operation):
            raise UnsupportedOperation(f'{self.name} does not support {operation}')
        return getattr(self, operation)(*args, **kwargs)


_registry = {}


def register(name, start, enabled=None, **operations):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError('a gateway name is 1-20 lowercase letters, digits or "_"')
    if name in _registry:
        raise ValueError(f'gateway {name!r} is already registered')
    _registry[name] = Adapter(name, start, enabled, **operations)


def unregister(name):
    """Remove an adapter (for tests); a missing one is ignored."""
    _registry.pop(name, None)


def adapter(name):
    """The adapter `name` when it is registered and available now, else None."""
    found = _registry.get(name)
    return found if found is not None and found.available() else None


def available_adapters():
    """Every adapter available now."""
    return [found for found in list(_registry.values()) if found.available()]


def check_timestamp(adapter_, sent_at, now, event=None):
    """Raise `Stale` when `sent_at` (an aware datetime) is further from `now` than the
    adapter's tolerance (either way)."""
    if abs(now - sent_at) > adapter_.timestamp_tolerance:
        raise Stale(event)


def offered(order):
    """The gateway names the doctor may choose for `order`, in order: the order's priced
    gateways that have an available adapter that accepts the order, then the fake one when
    it is available and the order has something to pay."""
    if order.total <= 0:
        return []
    names = []
    for name in order.gateways:
        found = adapter(name) if name != FAKE else None
        if found is not None and (found.accepts is None or found.accepts(order)):
            names.append(name)
    if adapter(FAKE) is not None:
        names.append(FAKE)
    return names
