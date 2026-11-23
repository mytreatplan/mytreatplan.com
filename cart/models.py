"""The doctor's cart (epic-ordering 9.3, AD-2): regional only, never on the admin site.

`CartItem`: one planning service (a TPS) chosen for one patient of the doctor, written only
by `cart.service`. The product is stored by id (the wizard picks it by category and size,
never by code); the maker choice is a named maker (`accounts.makers.NAMED`), `undecided`,
`other` with `other_name`, or '' for in-house printing. 9.6 turns the items into an order;
9.6/9.8 carry the maker choice onto the order line and the work order.
"""
import uuid

from django.conf import settings
from django.db import models

from accounts.makers import MAKER_NAMES

UNDECIDED = 'undecided'
OTHER = 'other'
OTHER_NAME_MAX_LENGTH = 60
MAKER_CHOICES = [*MAKER_NAMES.items(), (UNDECIDED, 'Not decided yet'), (OTHER, 'Other')]


class CartItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name='cart_items')
    patient = models.ForeignKey('patients.Patient', on_delete=models.CASCADE,
                                related_name='cart_items')
    product_id = models.UUIDField()
    add_on_codes = models.JSONField(default=list, blank=True)
    # '' for in-house printing.
    maker = models.CharField(max_length=20, choices=MAKER_CHOICES, blank=True, default='')
    other_name = models.CharField(max_length=OTHER_NAME_MAX_LENGTH, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']
        constraints = [models.UniqueConstraint(fields=('doctor', 'patient'),
                                               name='cart_one_service_per_patient')]

    def __str__(self):
        # Never a patient's name.
        return f'Cart item {self.pk}'
