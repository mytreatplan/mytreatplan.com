"""The doctor's cart (epic-ordering 9.3): the one place that writes `CartItem`.

- `add_tps(doctor, patient, *, maker, other_name, in_house, size, add_on_codes)`: put a
  treatment-plan service (TPS) for one of the doctor's active patients in the cart. Every
  refusal is a `CartRefused` with a code (listed in `CODES`); nothing is changed then.
- `offered_virtuaortho(doctor)`, `add_virtuaortho(doctor, patient, *, variant, choice, size,
  add_on_codes, quantity, address_id)` (9.4): a VirtuaOrtho One or DigitalTwin service, ODB,
  retainers or Spark on Demand, shipped to one of the doctor's usable addresses (stored on the
  item; 9.6 snapshots it and calls `mark_used`). No maker: MyTreatPlan makes it.
- `items_of(doctor)`, `remove(doctor, item_id)`.

The product is never chosen by code: it is the first (by code) of the products the doctor
is offered (`subscriptions.service.offers_for`); size `ice` is the ICE product (either
branch, the maker kept as for a TPS), else the category (`tps_in_house` for
printing, else `tps_branded`) and size. The add-ons are only those `resolver.add_ons`
offers on it, checked by `resolver.price`. A named maker needs a saved login
(`accounts.makers.has_login`); "not decided yet" and "other" never block. No patient name or
maker credential is read, logged or put in a message here.
"""
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from accounts import fiscal, makers, shipping
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
NO_ADDRESS = 'no_address'
ADDRESS = 'address'
QUANTITY = 'quantity'
NOT_FINISHED = 'not_finished'
CODES = (NOT_A_DOCTOR, NO_PATIENT, NO_ENTITY, MAKER_UNKNOWN, MAKER_LOGIN, OTHER_NAME,
         LIVE_WORK_ORDER, IN_CART, NOT_OFFERED, ADD_ON, NO_ADDRESS, ADDRESS, QUANTITY,
         NOT_FINISHED)

# VirtuaOrtho (9.4). The variants of step 2 and the category each one is sold in.
ONE, DIGITALTWIN = 'one', 'digitaltwin'
VARIANTS = {ONE: Product.VIRTUAORTHO_ONE, DIGITALTWIN: Product.VIRTUAORTHO_DIGITALTWIN}
# What a choice picks: 'case' by the variant's size; 'odb' and 'spark' by the product's id
# (one arch or two arches); 'retainers' the one product.
CASE, ODB, RETAINERS, SPARK = 'case', 'odb', 'retainers', 'spark'
VIRTUAORTHO_CATEGORIES = (Product.VIRTUAORTHO_ONE, Product.VIRTUAORTHO_DIGITALTWIN, Product.ODB,
                          Product.RETAINERS, Product.SPARK_ON_DEMAND)
# Under DigitalTwin an ODB always carries this add-on (not a choice); under One never.
ODB_DT = 'ODB-DT'

# The sizes in the order the wizard lists them; any other size follows, by name.
ICE_SIZE = 'ice'  # step 2's size choice for the ICE (its own category, no size)
SIZE_ORDER = (ICE_SIZE, 'short', 'medium', 'long')


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


def offered_ice(doctor, day=None):
    """(product, Money) of the doctor's ICE (the first by code), or None."""
    found = sorted(((product, money) for product, money in subscriptions.offers_for(doctor, day)
                    if product.category == Product.ICE),
                   key=lambda pair: (pair[0].code or '', str(pair[0].pk)))
    return found[0] if found else None


def offered_virtuaortho(doctor, day=None):
    """[(product, Money)] of the VirtuaOrtho products (One, DigitalTwin, ODB, retainers,
    Spark on Demand) the doctor is offered, by code. The wizard shows its VirtuaOrtho branch
    only when this holds a One or a DigitalTwin product (`variants_offered`)."""
    return sorted(((product, money) for product, money in subscriptions.offers_for(doctor, day)
                   if product.category in VIRTUAORTHO_CATEGORIES),
                  key=lambda pair: (pair[0].code or '', str(pair[0].pk)))


def variants_offered(offered):
    """The variants (ONE, DIGITALTWIN) with a product in `offered`."""
    return [variant for variant, category in VARIANTS.items()
            if any(product.category == category for product, _money in offered)]


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
    """Put a TPS or an ICE for `patient` in `doctor`'s cart and return the `CartItem`; `CartRefused`
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
    # An ICE is refused with any live work order.
    if patients_service.live_work_order(patient) is not None \
            and (size == ICE_SIZE or not patients_service.live_is_ice(patient)):
        raise CartRefused(LIVE_WORK_ORDER)
    if CartItem.objects.filter(doctor_id=doctor.pk, patient_id=patient.pk).exists():
        raise CartRefused(IN_CART)
    if size == ICE_SIZE:
        ice = offered_ice(doctor)
        product = ice[0] if ice else None
    else:
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


def _virtuaortho_product(offered, variant, choice, size, patient):
    """The offered product a (variant, choice, size) picks, or CartRefused."""
    if variant not in VARIANTS or variant not in variants_offered(offered):
        raise CartRefused(NOT_OFFERED)
    wanted = {CASE: VARIANTS[variant], ODB: Product.ODB, RETAINERS: Product.RETAINERS,
              SPARK: Product.SPARK_ON_DEMAND}.get(choice)
    # Spark on Demand is for One only.
    if wanted is None or (choice == SPARK and variant != ONE):
        raise CartRefused(NOT_OFFERED)
    found = [product for product, _money in offered if product.category == wanted]
    if choice == CASE:
        found = [product for product in found if product.size == size]
    elif choice in (ODB, SPARK):
        found = [product for product in found if str(product.pk) == str(size)]
    if not found:
        raise CartRefused(NOT_OFFERED)
    if choice == RETAINERS and not patients_service.has_finished_treatment(patient):
        raise CartRefused(NOT_FINISHED)
    return found[0]


def add_virtuaortho(doctor, patient, *, variant, choice, size='', add_on_codes=(), quantity=1,
                    address_id=''):
    """Put a VirtuaOrtho item for `patient` in `doctor`'s cart and return the `CartItem`.
    `variant` is ONE or DIGITALTWIN; `choice` is CASE (with `size`), ODB or SPARK (with
    `size` the product's id), or RETAINERS (needs a finished treatment). `quantity`: the
    aligners of a Spark product (within its range), else 1. A live work order refuses unless
    it is an ICE. `CartRefused` for anything not allowed; nothing is changed then."""
    if not is_client(doctor) or doctor.is_erased:
        raise CartRefused(NOT_A_DOCTOR)
    patient = patients_service.patient_of(doctor, getattr(patient, 'code', None))
    if patient is None:
        raise CartRefused(NO_PATIENT)
    if not fiscal.has_usable_entity(doctor):
        raise CartRefused(NO_ENTITY)
    if patients_service.live_work_order(patient) is not None \
            and not patients_service.live_is_ice(patient):
        raise CartRefused(LIVE_WORK_ORDER)
    if CartItem.objects.filter(doctor_id=doctor.pk, patient_id=patient.pk).exists():
        raise CartRefused(IN_CART)
    product = _virtuaortho_product(offered_virtuaortho(doctor), variant, choice, size, patient)
    if not shipping.has_usable_address(doctor):
        raise CartRefused(NO_ADDRESS)
    try:
        address = shipping.usable_addresses(doctor).filter(pk=address_id).first()
    except (ValueError, ValidationError):
        address = None
    if address is None:
        raise CartRefused(ADDRESS)
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise CartRefused(QUANTITY) from None
    codes = [(code or '').strip().upper() for code in add_on_codes]
    if choice == ODB and variant == DIGITALTWIN and ODB_DT not in codes:
        codes.append(ODB_DT)
    chosen = [resolver.add_on(code) for code in codes]
    if None in chosen or len(set(codes)) != len(codes):
        raise CartRefused(ADD_ON)
    # ODB DigitalTwin is not a choice: never under One, and under DigitalTwin only forced.
    if ODB_DT in codes and not (choice == ODB and variant == DIGITALTWIN):
        raise CartRefused(ADD_ON)
    offered_codes = {add_on.code for add_on in resolver.add_ons(product)}
    if any(add_on.code not in offered_codes for add_on in chosen):
        raise CartRefused(ADD_ON)
    try:
        resolver.price(product, subscriptions.effective(doctor).subscription, chosen, quantity)
    except resolver.QuantityRefused:
        raise CartRefused(QUANTITY) from None
    except ValueError:
        raise CartRefused(ADD_ON) from None
    try:
        with transaction.atomic():
            return CartItem.objects.create(
                doctor=doctor, patient=patient, product_id=product.pk, add_on_codes=codes,
                maker='', other_name='', quantity=quantity, shipping_address=address)
    except IntegrityError:
        raise CartRefused(IN_CART) from None


def items_of(doctor):
    """The doctor's cart items, oldest first, with their patients."""
    return list(CartItem.objects.filter(doctor_id=doctor.pk).select_related('patient', 'shipping_address'))


def remove(doctor, item_id):
    """Delete the doctor's item `item_id`; False when it is not theirs (or unknown)."""
    deleted, _ = CartItem.objects.filter(doctor_id=doctor.pk, pk=item_id).delete()
    return bool(deleted)
