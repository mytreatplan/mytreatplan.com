"""The doctor's cart (epic-ordering 9.3): the one place that writes `CartItem`.

- `add_tps(doctor, patient, *, maker, other_name, in_house, size, add_on_codes)`: put a
  treatment-plan service (TPS) for one of the doctor's active patients in the cart. Every
  refusal is a `CartRefused` with a code (listed in `CODES`); nothing is changed then.
- `items_of(doctor)`, `remove(doctor, item_id)`.

The product is never chosen by code: it is the first (by code) of the products the doctor
is offered (`subscriptions.service.offers_for`) in the category (`tps_in_house` for
printing, else `tps_branded`) and size. The add-ons are only those `resolver.add_ons`
offers on it, checked by `resolver.price`. A named maker needs a saved login
(`accounts.makers.has_login`); "not decided yet" and "other" never block. No patient name or
maker credential is read, logged or put in a message here.
"""
from django.db import IntegrityError, transaction

from accounts import fiscal, makers
from accounts.client_sync import is_client
from catalogue import resolver
from catalogue.models import Product
from patients import service as patients_service
from subscriptions import service as subscriptions

from .models import MAKER_CHOICES, OTHER, OTHER_NAME_MAX_LENGTH, UNDECIDED, CartItem

# The refusal codes.
NOT_A_DOCTOR = 'not_a_doctor'
NO_PATIENT = 'no_patient'
NO_ENTITY = 'no_entity'
MAKER_UNKNOWN = 'maker_unknown'
MAKER_LOGIN = 'maker_login'
OTHER_NAME = 'other_name'
LIVE_WORK_ORDER = 'live_work_order'
IN_CART = 'in_cart'
NOT_OFFERED = 'not_offered'
ADD_ON = 'add_on'
CODES = (NOT_A_DOCTOR, NO_PATIENT, NO_ENTITY, MAKER_UNKNOWN, MAKER_LOGIN, OTHER_NAME,
         LIVE_WORK_ORDER, IN_CART, NOT_OFFERED, ADD_ON)

# The sizes in the order the wizard lists them; any other size follows, by name.
SIZE_ORDER = ('ice', 'short', 'medium', 'long')


class CartRefused(Exception):
    """An item was refused; `code` is one of `CODES`."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def category_of(in_house):
    return Product.TPS_IN_HOUSE if in_house else Product.TPS_BRANDED


def offered_tps(doctor, day=None):
    """[(product, Money)] of the TPS products (branded and in-house) the doctor is offered,
    by code."""
    wanted = (Product.TPS_BRANDED, Product.TPS_IN_HOUSE)
    return sorted(((product, money) for product, money in subscriptions.offers_for(doctor, day)
                   if product.category in wanted and product.size),
                  key=lambda pair: (pair[0].code or '', str(pair[0].pk)))


def size_rank(size):
    return (SIZE_ORDER.index(size), size) if size in SIZE_ORDER else (len(SIZE_ORDER), size)


def _maker(doctor, maker, other_name, in_house):
    """(maker, other_name) as stored, or CartRefused."""
    if in_house:
        return '', ''
    if maker not in {code for code, _label in MAKER_CHOICES}:
        raise CartRefused(MAKER_UNKNOWN)
    if maker in makers.NAMED:
        if not makers.has_login(doctor, maker):
            raise CartRefused(MAKER_LOGIN)
        return maker, ''
    if maker == OTHER:
        name = (other_name or '').strip()
        if not name or len(name) > OTHER_NAME_MAX_LENGTH:
            raise CartRefused(OTHER_NAME)
        return OTHER, name
    return UNDECIDED, ''


def add_tps(doctor, patient, *, maker, other_name='', in_house=False, size, add_on_codes=()):
    """Put a TPS for `patient` in `doctor`'s cart and return the `CartItem`; `CartRefused`
    (see the module docstring and `CODES`) for anything not allowed."""
    if not is_client(doctor) or doctor.is_erased:
        raise CartRefused(NOT_A_DOCTOR)
    patient = patients_service.patient_of(doctor, getattr(patient, 'code', None))
    if patient is None:
        raise CartRefused(NO_PATIENT)
    if not fiscal.has_usable_entity(doctor):
        raise CartRefused(NO_ENTITY)
    maker, other_name = _maker(doctor, maker, other_name, in_house)
    # A live work order refuses, unless it is an ICE (a TPS is the planning after it).
    if patients_service.live_work_order(patient) is not None \
            and not patients_service.live_is_ice(patient):
        raise CartRefused(LIVE_WORK_ORDER)
    if CartItem.objects.filter(doctor_id=doctor.pk, patient_id=patient.pk).exists():
        raise CartRefused(IN_CART)
    category = category_of(in_house)
    product = next((offered for offered, _money in offered_tps(doctor)
                    if offered.category == category and offered.size == size), None)
    if product is None:
        raise CartRefused(NOT_OFFERED)
    codes = [(code or '').strip().upper() for code in add_on_codes]
    chosen = [resolver.add_on(code) for code in codes]
    if None in chosen or len(set(codes)) != len(codes):
        raise CartRefused(ADD_ON)
    offered_codes = {add_on.code for add_on in resolver.add_ons(product)}
    if any(add_on.code not in offered_codes for add_on in chosen):
        raise CartRefused(ADD_ON)
    try:
        resolver.price(product, subscriptions.effective(doctor).subscription, chosen)
    except ValueError:
        raise CartRefused(ADD_ON) from None
    try:
        with transaction.atomic():
            return CartItem.objects.create(
                doctor=doctor, patient=patient, product_id=product.pk, add_on_codes=codes,
                maker=maker, other_name=other_name)
    except IntegrityError:
        raise CartRefused(IN_CART) from None


def items_of(doctor):
    """The doctor's cart items, oldest first, with their patients."""
    return list(CartItem.objects.filter(doctor_id=doctor.pk).select_related('patient'))


def remove(doctor, item_id):
    """Delete the doctor's item `item_id`; False when it is not theirs (or unknown)."""
    deleted, _ = CartItem.objects.filter(doctor_id=doctor.pk, pk=item_id).delete()
    return bool(deleted)
