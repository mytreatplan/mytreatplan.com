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
`cancelled` / `expired`. Neither the order nor its lines are listed in the Django admin;
an order goes with no user: the link to the user is SET_NULL (the order is a legal
record).
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django_countries.fields import CountryField

from accounts.crypto import EncryptedCharField, EncryptedEmailField

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
    STATES = [(CHECKOUT, 'Checkout'), (PENDING_PAYMENT, 'Pending payment'), (PAID, 'Paid'),
              (CANCELLED, 'Cancelled'), (EXPIRED, 'Expired')]
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
    entity_chosen, payment_started, paid, cancelled, expired), the state before ('' on
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
