"""Invoices (epic-payments entry 6, AD-7): one `Invoice` per paid order (or per manual
sale), its `InvoiceLine`s and the `InvoiceSeries` counter of each (billing company,
instance, year).

An invoice is a legal record, copied from the order (or given by the manual sale) and never
re-priced: the seller's identity from the billing company at issue time, the buyer's
snapshot in encrypted `bill_*` fields (as the order's), the lines, the VAT groups and the
totals. Nothing links it to the user or the fiscal entity (the buyer's entity id is a plain
UUID), so the erasure of an account never touches it; the order link is SET_NULL.

Append-only, as far as the ORM guards it, following `payments.models.OrderChange`: `save` of
an existing row, `delete`, `QuerySet.update`, `QuerySet.bulk_update` and `QuerySet.delete`
raise `AppendOnly`. The one exception is the e-invoice transmission status
(`EINVOICE_FIELDS`), saved only with `update_fields` limited to them, which
`invoices.service.set_einvoice_status` does. Written only by `invoices.service`.
"""
import uuid

from django.db import models
from django.utils import timezone
from django_countries.fields import CountryField

from accounts.crypto import EncryptedCharField, EncryptedEmailField

ACTOR_TYPES = [('doctor', 'Doctor'), ('admin', 'Admin user'), ('system', 'System')]

# The only fields that change after issue.
EINVOICE_FIELDS = frozenset({'einvoice_status', 'einvoice_reference', 'einvoice_updated_at'})


class AppendOnly(RuntimeError):
    """An invoice or an invoice line was to be changed or deleted: they are append-only."""


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnly('invoices are append-only')

    def bulk_update(self, *args, **kwargs):
        raise AppendOnly('invoices are append-only')

    def delete(self):
        raise AppendOnly('invoices are append-only')


class InvoiceSeries(models.Model):
    """The counter of one (billing company code, instance, year): `last_number` is the last
    number issued. Locked while a number is taken (`invoices.service._next_number`)."""

    company_code = models.CharField(max_length=16)
    instance = models.CharField(max_length=8)
    year = models.PositiveSmallIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('company_code', 'instance', 'year'),
                                               name='invoices_series_key')]

    def __str__(self):
        return f'{self.company_code}-{self.instance}-{self.year}: {self.last_number}'


class Invoice(models.Model):
    ORDER = 'order'
    MANUAL = 'manual'
    KINDS = [(ORDER, 'Order'), (MANUAL, 'Manual sale')]
    NONE, PENDING, SENT, ACCEPTED, REJECTED = 'none', 'pending', 'sent', 'accepted', 'rejected'
    EINVOICE_STATUSES = [(NONE, 'None'), (PENDING, 'Pending'), (SENT, 'Sent'),
                         (ACCEPTED, 'Accepted'), (REJECTED, 'Rejected')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # `<company>-<instance>-<year>-<6 digits>`, e.g. ES-EU-2026-000001.
    number = models.CharField(max_length=40, unique=True)
    kind = models.CharField(max_length=10, choices=KINDS)
    # One invoice per order (a OneToOne is unique; NULL for a manual sale and after the
    # order is gone).
    order = models.OneToOneField('payments.Order', null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name='invoice')
    company_code = models.CharField(max_length=16)
    instance = models.CharField(max_length=8)
    year = models.PositiveSmallIntegerField()

    # The seller, from the billing company at issue time.
    seller_legal_name = models.CharField(max_length=200)
    seller_address = models.TextField(blank=True, default='')
    seller_country = models.CharField(max_length=2, blank=True, default='')
    seller_company_number = models.CharField(max_length=64, blank=True, default='')
    seller_vat_number = models.CharField(max_length=64, blank=True, default='')
    seller_tax_number = models.CharField(max_length=64, blank=True, default='')

    # The buyer, as the order's snapshot (no link to the entity or the user).
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

    currency = models.CharField(max_length=3)
    net_total = models.DecimalField(max_digits=17, decimal_places=2)
    discount_total = models.DecimalField(max_digits=17, decimal_places=2)
    ice_total = models.DecimalField(max_digits=17, decimal_places=2)
    voucher_taken = models.DecimalField(max_digits=17, decimal_places=2)
    vat_total = models.DecimalField(max_digits=17, decimal_places=2)
    total = models.DecimalField(max_digits=17, decimal_places=2)
    # [{"percent": "21.00", "text": "", "base": "100.00", "vat": "21.00"}, ...]
    vat_groups = models.JSONField(default=list)

    # The moment of issue (UTC) and its date and zone in the company's legal time zone.
    issued_at = models.DateTimeField(default=timezone.now)
    issue_date = models.DateField()
    time_zone = models.CharField(max_length=64)

    # Who issued it (the system for a paid order).
    actor_type = models.CharField(max_length=10, choices=ACTOR_TYPES)
    actor_uuid = models.UUIDField(null=True, blank=True)

    # The only fields that change after issue (EINVOICE_FIELDS).
    einvoice_status = models.CharField(max_length=10, choices=EINVOICE_STATUSES, default=NONE)
    einvoice_reference = models.CharField(max_length=100, blank=True, default='')
    einvoice_updated_at = models.DateTimeField(null=True, blank=True)
    # Reserved for the VeriFactu and UAE e-invoicing fields.
    extensions = models.JSONField(default=dict, blank=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ['issued_at', 'number']
        indexes = [models.Index(fields=['company_code', 'instance', 'year'])]

    def __str__(self):
        return self.number

    @property
    def bill_display_name(self):
        if self.bill_kind == 'company':
            return self.bill_legal_name
        return f'{self.bill_first_name} {self.bill_last_name}'.strip()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            update_fields = kwargs.get('update_fields')
            if (update_fields is None or not update_fields
                    or not set(update_fields) <= EINVOICE_FIELDS):
                raise AppendOnly('an issued invoice changes only its e-invoice fields')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnly('invoices are append-only')


class InvoiceLine(models.Model):
    """One line, copied from the order line (or given by the manual sale). `tax_category`:
    S standard, Z zero rated, E exempt, AE reverse charge."""

    STANDARD, ZERO, EXEMPT, REVERSE_CHARGE = 'S', 'Z', 'E', 'AE'
    TAX_CATEGORIES = [(STANDARD, 'Standard'), (ZERO, 'Zero rated'), (EXEMPT, 'Exempt'),
                      (REVERSE_CHARGE, 'Reverse charge')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='lines')
    position = models.PositiveSmallIntegerField()
    code = models.CharField(max_length=64, blank=True, default='')
    name = models.CharField(max_length=150)
    quantity = models.PositiveIntegerField()
    # The amount for the quantity, as the order line's.
    list_amount = models.DecimalField(max_digits=17, decimal_places=2)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=17, decimal_places=2)
    ice = models.DecimalField(max_digits=17, decimal_places=2)
    voucher_share = models.DecimalField(max_digits=17, decimal_places=2)
    net = models.DecimalField(max_digits=17, decimal_places=2)
    vat_percent = models.DecimalField(max_digits=5, decimal_places=2)
    vat_text = models.CharField(max_length=500, blank=True, default='')
    tax_category = models.CharField(max_length=2, choices=TAX_CATEGORIES)
    included_case = models.BooleanField(default=False)
    patient_code = models.CharField(max_length=32, blank=True, default='')

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ['invoice_id', 'position']
        constraints = [models.UniqueConstraint(fields=('invoice', 'position'),
                                               name='invoices_line_position')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnly('invoice lines are append-only')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnly('invoice lines are append-only')
