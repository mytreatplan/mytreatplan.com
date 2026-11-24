"""Orders (epic-payments entry 1, AD-6, AD-15, AD-16, AD-20; user decisions 2026-11-18):
one `Order` per purchase, its `OrderLine`s and one `OrderChange` row per change.

Written only by `payments.service`: every change locks the order, checks its version,
raises it by one, stores who made it and adds a change row. Amounts are `Money` in the
code and `DecimalField(17, 2)` here, with the one `currency` of the order. The lines keep
what `subscriptions.service.price_order_for` (or `catalogue.pricing.price_subscription`)
priced, copied: a later change of the catalogue never changes an order. The company is
copied too (code and legal name; the snapshot tables are read-only and may be re-created).

The buyer's fiscal entity is copied into encrypted `bill_*` fields (the fields of
`accounts.fiscal.FIELDS`) with the entity's id, version and VIES status; `fiscal_entity`
is a plain link that goes to NULL when the entity is erased.

States: `checkout` -> `pending_payment` -> `paid`; `checkout` / `pending_payment` ->
`cancelled` / `expired`; `paid` -> `refunded` (epic-payments entry 7, a full refund). Neither the order nor its lines are listed in the Django admin;
an order goes with no user: the link to the user is SET_NULL (the order is a legal
record).
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django_countries.fields import CountryField

from accounts.crypto import EncryptedCharField, EncryptedEmailField, EncryptedJSONField

# Who made a change (AD-22).
ACTOR_TYPES = [('doctor', 'Doctor'), ('admin', 'Admin user'), ('system', 'System')]


class AppendOnly(RuntimeError):
    """A change row was to be updated or deleted one by one: the change rows are
    append-only."""


class Order(models.Model):
    # AD-5: crosses sites (entry 8 sends orders to the admin site); the UUID is its global
    # id.
    CROSSES_SITES = True

    CHECKOUT = 'checkout'
    PENDING_PAYMENT = 'pending_payment'
    PAID = 'paid'
    CANCELLED = 'cancelled'
    EXPIRED = 'expired'
    REFUNDED = 'refunded'
    STATES = [(CHECKOUT, 'Checkout'), (PENDING_PAYMENT, 'Pending payment'), (PAID, 'Paid'),
              (CANCELLED, 'Cancelled'), (EXPIRED, 'Expired'), (REFUNDED, 'Refunded')]
    OPEN_STATES = (CHECKOUT, PENDING_PAYMENT)
    SERVICE = 'service'
    SUBSCRIPTION = 'subscription'
    KINDS = [(SERVICE, 'Service order'), (SUBSCRIPTION, 'Subscription purchase')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # `KM-482915` (payments.reference), unique on this instance.
    reference = models.CharField(max_length=9, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                             related_name='orders')
    state = models.CharField(max_length=16, choices=STATES, default=CHECKOUT)
    kind = models.CharField(max_length=12, choices=KINDS)
    currency = models.CharField(max_length=3)
    company_code = models.CharField(max_length=20)
    company_legal_name = models.CharField(max_length=200)
    # The gateways the pricing offered (billing rule and region), and the one chosen.
    gateways = models.JSONField(default=list)
    gateway = models.CharField(max_length=20, blank=True, default='')
    net_total = models.DecimalField(max_digits=17, decimal_places=2)
    discount_total = models.DecimalField(max_digits=17, decimal_places=2)
    ice_total = models.DecimalField(max_digits=17, decimal_places=2)
    voucher_taken = models.DecimalField(max_digits=17, decimal_places=2)
    vat_total = models.DecimalField(max_digits=17, decimal_places=2)
    total = models.DecimalField(max_digits=17, decimal_places=2)
    # [{"percent": "21.00", "text": "", "base": "100.00", "vat": "21.00"}, ...]
    vat_groups = models.JSONField(default=list)
    # The catalogue voucher's id (redeeming it is epic-subscriptions'), or None.
    voucher_id = models.UUIDField(null=True, blank=True)

    # The buyer's fiscal entity: a link that goes with the entity, and the copy.
    fiscal_entity = models.ForeignKey('accounts.FiscalEntity', null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name='+')
    bill_entity_id = models.UUIDField(null=True, blank=True)
    bill_entity_version = models.PositiveIntegerField(null=True, blank=True)
    bill_vies_status = models.CharField(max_length=14, blank=True, default='')
    bill_kind = models.CharField(max_length=10, blank=True, default='')
    bill_first_name = EncryptedCharField(max_length=150, blank=True)
    bill_last_name = EncryptedCharField(max_length=150, blank=True)
    bill_legal_name = EncryptedCharField(max_length=200, blank=True)
    bill_tax_id = EncryptedCharField(max_length=40, blank=True)
    bill_vat_id = EncryptedCharField(max_length=40, blank=True)
    bill_address_line1 = EncryptedCharField(max_length=200, blank=True)
    bill_address_line2 = EncryptedCharField(max_length=200, blank=True)
    bill_city = EncryptedCharField(max_length=150, blank=True)
    bill_postal_code = EncryptedCharField(max_length=30, blank=True)
    bill_province = EncryptedCharField(max_length=150, blank=True)
    bill_country = CountryField(blank=True)
    bill_billing_email = EncryptedEmailField(max_length=254, blank=True)

    version = models.PositiveIntegerField(default=1)
    actor_type = models.CharField(max_length=10, choices=ACTOR_TYPES)
    actor_uuid = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    pending_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    expired_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', 'id']
        indexes = [models.Index(fields=['state', 'created_at'])]

    def __str__(self):
        return f'Order {self.reference} ({self.state})'

    @property
    def is_open(self):
        return self.state in self.OPEN_STATES

    @property
    def bill_display_name(self):
        if self.bill_kind == 'company':
            return self.bill_legal_name
        return f'{self.bill_first_name} {self.bill_last_name}'.strip()


class OrderLine(models.Model):
    """One line: its inputs (to re-price the order when its fiscal entity changes: the
    product or subscription, the quantity, the add-ons and the ICE credit asked) and the
    priced values copied from the pricing. Subscription purchases have one line with
    `subscription_id` and `period`."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='lines')
    position = models.PositiveSmallIntegerField()
    # Inputs.
    product_id = models.UUIDField(null=True, blank=True)
    subscription_id = models.UUIDField(null=True, blank=True)
    period = models.CharField(max_length=10, blank=True, default='')
    ice_asked = models.DecimalField(max_digits=17, decimal_places=2, null=True, blank=True)
    # Snapshot.
    code = models.CharField(max_length=64, blank=True, default='')
    name = models.CharField(max_length=150)
    quantity = models.PositiveIntegerField()
    # [{"id": ..., "code": ..., "name": ..., "percent": "5.00" | None, "amount": ... | None}]
    add_ons = models.JSONField(default=list)
    list_amount = models.DecimalField(max_digits=17, decimal_places=2)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=17, decimal_places=2)
    ice = models.DecimalField(max_digits=17, decimal_places=2)
    voucher_share = models.DecimalField(max_digits=17, decimal_places=2)
    net = models.DecimalField(max_digits=17, decimal_places=2)
    vat_percent = models.DecimalField(max_digits=5, decimal_places=2)
    vat_text = models.CharField(max_length=500, blank=True, default='')
    # An included case use (epic-subscriptions 13 registers the hook that sets it).
    included_case = models.BooleanField(default=False)
    # The patient's code when the line is a case for a patient (epic-payments entry 6; 9.1
    # fills it), copied to the invoice line. A database default too, so the release before
    # this one can still insert rows while this one is being deployed.
    patient_code = models.CharField(max_length=32, blank=True, default='', db_default='')
    # The maker the doctor chose for a planning line (a named maker, `undecided`, `other`,
    # or '' for in-house printing / no maker) and the name given with `other` (epic-ordering
    # 9.6; the work order of 9.8 reads them). Database defaults, as `patient_code`.
    maker = models.CharField(max_length=20, blank=True, default='', db_default='')
    other_name = models.CharField(max_length=60, blank=True, default='', db_default='')
    # Where a VirtuaOrtho line is shipped: a copy of the doctor's address when the order was
    # made (`id`, `label`, `recipient_name`, `clinic_name`, `contact_phone`, `address_line1`,
    # `address_line2`, `city`, `postal_code`, `province`, `country`), encrypted; None for any
    # other line. Never copied to an invoice.
    shipping = EncryptedJSONField(null=True, blank=True)
    # The provider cost of the line (the product's `resolver.cost` total times the
    # quantity) in the order's currency, and whether every cost link had a price.
    provider_cost = models.DecimalField(max_digits=17, decimal_places=2, default=0)
    cost_complete = models.BooleanField(default=True)

    class Meta:
        ordering = ['order_id', 'position']
        constraints = [models.UniqueConstraint(fields=('order', 'position'),
                                               name='payments_orderline_position')]


class OrderChangeQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnly('the order changes are append-only')

    def delete(self):
        raise AppendOnly('the order changes are append-only')


class OrderChange(models.Model):
    """One change of an order: when, who (actor type and UUID), what (`event`: created,
    entity_chosen, payment_started, paid, cancelled, expired, refunded), the state before ('' on
    creation) and after, and the order's version after it. Append-only, as far as the ORM
    guards it (as the subscription change rows)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='changes')
    at = models.DateTimeField(default=timezone.now)
    actor_type = models.CharField(max_length=10, choices=ACTOR_TYPES)
    actor_uuid = models.UUIDField(null=True, blank=True)
    event = models.CharField(max_length=30)
    from_state = models.CharField(max_length=16, blank=True, default='')
    to_state = models.CharField(max_length=16)
    version = models.PositiveIntegerField()
    # Small facts of the change (the gateway chosen), never personal data.
    detail = models.JSONField(default=dict, blank=True)

    objects = OrderChangeQuerySet.as_manager()

    class Meta:
        ordering = ['at', 'version', 'id']

    def __str__(self):
        return f'{self.event} (version {self.version})'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnly('the order changes are append-only')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnly('the order changes are append-only')


class Payment(models.Model):
    """One payment attempt at one gateway (epic-payments entry 2, user decision 2026-11-19).
    Written only by `payments.service`. `open`: the doctor was sent to the gateway and the
    gateway's session may still be live (`session_expires_at`); there is at most one open
    attempt per order (a database constraint). `confirmed`: the gateway said it was paid and
    the order was paid by it. `failed`: the gateway said it failed. `closed`: the attempt was
    closed at the gateway, replaced by a retry or its session timed out. `needs_attention`:
    a confirmed payment that could not pay the order (the order was not payable, or the
    amount or currency differ): staff check it and refund by hand if money moved.
    `gateway_payment_id` is the gateway's own id of the payment (unique per gateway)."""

    OPEN, CONFIRMED, FAILED, CLOSED, NEEDS_ATTENTION = (
        'open', 'confirmed', 'failed', 'closed', 'needs_attention')
    STATES = [(OPEN, 'Open'), (CONFIRMED, 'Confirmed'), (FAILED, 'Failed'),
              (CLOSED, 'Closed'), (NEEDS_ATTENTION, 'Needs attention')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='payments')
    gateway = models.CharField(max_length=20)
    gateway_payment_id = models.CharField(max_length=100, blank=True, default='')
    amount = models.DecimalField(max_digits=17, decimal_places=2)
    currency = models.CharField(max_length=3)
    state = models.CharField(max_length=16, choices=STATES, default=OPEN)
    session_expires_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    # Bank transfer (entry 5): when the day-7 reminder was sent (null: not yet).
    reminded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=('gateway', 'gateway_payment_id'), name='payments_payment_gateway_id',
                condition=~models.Q(gateway_payment_id='')),
            models.UniqueConstraint(
                fields=('order',), name='payments_payment_one_open',
                condition=models.Q(state='open')),
        ]

    def __str__(self):
        return f'Payment {self.pk} ({self.state})'


class WebhookLog(models.Model):
    """One row per webhook received, accepted or refused (epic-payments entry 2, user
    decision 2026-11-19): when, from which IP, the result, the gateway's event and payment
    ids, the amount the event claimed, and the SHA-256 and size of the body, never the body.
    Kept 180 days (`purge_webhook_logs`). An accepted row is also the task's work item:
    `processed_at` (set when the payment was re-read and confirmed or refused), `attempts`
    and `next_try_at` (the backoff after a failed re-read, AD-8). Rows whose event was taken
    in (`DEDUPED`) are unique per (gateway, event id): the database refuses the same event
    twice."""

    ACCEPTED, DUPLICATE, BAD_SIGNATURE, STALE = 'accepted', 'duplicate', 'bad_signature', 'stale'
    AMOUNT_MISMATCH, UNKNOWN, NOT_PAYABLE = 'amount_mismatch', 'unknown', 'not_payable'
    TOO_LARGE, RATE_LIMITED = 'too_large', 'rate_limited'
    RESULTS = [(ACCEPTED, 'Accepted'), (DUPLICATE, 'Duplicate'),
               (BAD_SIGNATURE, 'Bad signature'), (STALE, 'Stale'),
               (AMOUNT_MISMATCH, 'Amount mismatch'), (UNKNOWN, 'Unknown'),
               (NOT_PAYABLE, 'Not payable'), (TOO_LARGE, 'Too large'),
               (RATE_LIMITED, 'Rate limited')]
    # The results of an event that was taken in (the task may change `accepted` to the
    # other two): the event id is unique among them.
    DEDUPED = (ACCEPTED, AMOUNT_MISMATCH, NOT_PAYABLE)
    # `status`: what the event said of the payment (the values of payments.gateways).
    PAID, FAILED = 'paid', 'failed'
    STATUSES = [(PAID, 'Paid'), (FAILED, 'Failed')]

    id = models.BigAutoField(primary_key=True)
    gateway = models.CharField(max_length=20)
    received_at = models.DateTimeField(default=timezone.now, db_index=True)
    ip = models.CharField(max_length=64, blank=True, default='')
    result = models.CharField(max_length=16, choices=RESULTS)
    event_id = models.CharField(max_length=100, blank=True, default='')
    gateway_payment_id = models.CharField(max_length=100, blank=True, default='')
    amount = models.DecimalField(max_digits=17, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True, default='')
    body_sha256 = models.CharField(max_length=64, blank=True, default='')
    body_size = models.PositiveIntegerField(default=0)
    # What the verified event said of the payment (epic-payments entry 3, user decision
    # 2026-11-19): paid, failed, or blank (the event does not say). Redsys's
    # `fetch_payment` reads the latest of an attempt: its signed notification is the record.
    status = models.CharField(max_length=10, choices=STATUSES, blank=True, default='')
    payment = models.ForeignKey(Payment, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name='webhooks')
    processed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_try_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['received_at', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=('gateway', 'event_id'), name='payments_webhooklog_event',
                condition=~models.Q(event_id='') & models.Q(
                    result__in=['accepted', 'amount_mismatch', 'not_payable'])),
        ]

    def __str__(self):
        return f'Webhook {self.pk} {self.gateway} ({self.result})'


class Refund(models.Model):
    """One refund of an order (epic-payments entry 7, AD-7), written only by
    `payments.refunds`. Written `pending` and committed before the gateway is called;
    `done` once the credit note is issued and the order is `refunded`; `failed` when the
    gateway refused (no money moved); `needs_attention` when the gateway did not answer or
    answered something unknown (Redsys may have refunded; it is never retried automatically and
    blocks any new refund of the order until staff resolve it). `manual`: staff refunded by hand, with a `note`, and no
    gateway was called. At most one `pending` refund per order (a database constraint).
    `gateway_refund_id` is not unique: Redsys repeats it for every refund of a payment."""

    PENDING, DONE, FAILED, NEEDS_ATTENTION = 'pending', 'done', 'failed', 'needs_attention'
    STATES = [(PENDING, 'Pending'), (DONE, 'Done'), (FAILED, 'Failed'),
              (NEEDS_ATTENTION, 'Needs attention')]
    CANCELLATION, GOODWILL, ERROR = 'cancellation', 'goodwill', 'error'
    REASONS = [(CANCELLATION, 'Cancellation'), (GOODWILL, 'Goodwill'), (ERROR, 'Error')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # The refund's own key (idempotency key at a gateway that takes one).
    key = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='refunds')
    payment = models.ForeignKey(Payment, null=True, blank=True, on_delete=models.PROTECT,
                                related_name='refunds')
    amount = models.DecimalField(max_digits=17, decimal_places=2)
    currency = models.CharField(max_length=3)
    state = models.CharField(max_length=16, choices=STATES, default=PENDING)
    manual = models.BooleanField(default=False)
    note = models.CharField(max_length=500, blank=True, default='')
    reason = models.CharField(max_length=12, choices=REASONS)
    gateway_refund_id = models.CharField(max_length=100, blank=True, default='')
    actor_type = models.CharField(max_length=10, choices=ACTOR_TYPES)
    actor_uuid = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    credit_note = models.ForeignKey('invoices.Invoice', null=True, blank=True,
                                    on_delete=models.PROTECT, related_name='refunds')

    class Meta:
        ordering = ['created_at', 'id']
        constraints = [
            models.UniqueConstraint(fields=('order',), name='payments_refund_one_pending',
                                    condition=models.Q(state='pending')),
        ]

    def __str__(self):
        return f'Refund {self.pk} ({self.state})'
