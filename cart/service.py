"""The doctor's cart (epic-ordering 9.3): the one place that writes `CartItem`.

- `add_tps(doctor, patient, *, maker, other_name, in_house, size, add_on_codes)`: put a
  treatment-plan service (TPS) for one of the doctor's active patients in the cart. Every
  refusal is a `CartRefused` with a code (listed in `CODES`); nothing is changed then.
- `offered_virtuaortho(doctor)`, `add_virtuaortho(doctor, patient, *, variant, choice, size,
  add_on_codes, quantity, address_id)` (9.4): a VirtuaOrtho One or DigitalTwin service, ODB,
  retainers or Spark on Demand, shipped to one of the doctor's usable addresses (stored on the
  item; 9.6 snapshots it and calls `mark_used`). No maker: MyTreatPlan makes it.
- `check_work_order(doctor, work_order)`, `add_work_order(doctor, work_order, *, maker,
  other_name, in_house, add_on_codes, variant, quantity, address_id)` (9.9): a manual work
  order clinical ops opened (`patients.service.create_manual_work_order`) goes into the
  cart with its product fixed and the options the doctor chooses, priced at the doctor's
  effective tier at that moment (no price is stored on the work order); the item keeps the
  work order's id, which the order line carries.
- `items_of(doctor)`, `remove(doctor, item_id)`.
- `finish(doctor, voucher_code='')` (9.6): turn the cart into one service order through
  `payments.service.create_order` (patient code, maker and shipping address copied per line;
  each planning line takes the ICE credit of its patient's paid ICE: 9.7, `patients.ice`),
  cancelling the doctor's earlier unpaid cart order first (all or nothing), except one
  waiting for a bank transfer (9.17); the items stay until the order is paid (`paid`, the
  receiver of the order's `paid` hook, then removes the paid patients' items and marks the
  snapshotted addresses used) or approved by bank transfer (`transfer_approved`, the receiver
  of the `transfer_approved` hook, removes the same items).

The product is never chosen by code: it is the first (by code) of the products the doctor
is offered (`subscriptions.service.offers_for`); size `ice` is the ICE product (either
branch, the maker kept as for a TPS), else the category (`tps_in_house` for
printing, else `tps_branded`) and size. The add-ons are only those `resolver.add_ons`
offers on it, checked by `resolver.price`. A named maker needs a saved login
(`accounts.makers.has_login`); "not decided yet" and "other" never block. No patient name or
maker credential is read, logged or put in a message here.
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from accounts import fiscal, makers, shipping
from accounts.client_sync import is_client
from catalogue import resolver
from catalogue.models import Product
from patients import ice as ice_credits
from patients import service as patients_service
from payments import service as payments
from payments.models import Order
from subscriptions import service as subscriptions
from subscriptions import vouchers as voucher_redemptions

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
EMPTY = 'empty'
ADDRESS_GONE = 'address_gone'
PAYMENT_OPEN = 'payment_open'
ICE_TAKEN = 'ice_taken'
WORK_ORDER = 'work_order'
CODES = (NOT_A_DOCTOR, NO_PATIENT, NO_ENTITY, MAKER_UNKNOWN, MAKER_LOGIN, OTHER_NAME,
         LIVE_WORK_ORDER, IN_CART, NOT_OFFERED, ADD_ON, NO_ADDRESS, ADDRESS, QUANTITY,
         NOT_FINISHED, EMPTY, ADDRESS_GONE, PAYMENT_OPEN, ICE_TAKEN, WORK_ORDER)
SOURCE = 'cart'

# VirtuaOrtho (9.4). The variants of the VirtuaOrtho type step and the category each one is sold in.
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
ICE_SIZE = 'ice'  # the options step's size choice for the ICE (its own category, no size)
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


def virtuaortho_lines(doctor, offered=None, held=None):
    """[(variant, choice, product, Money)] the VirtuaOrtho rows a doctor can buy, the wizard's
    and the product page's one rule: each variant (`variants_offered`) with its sizes, the ODB
    (under DigitalTwin priced with its ODB-DT add-on, and left out without it), retainers and
    Spark on Demand (under One only). Unsized One/DigitalTwin rows and rows with no price are
    left out. Retainers are listed whatever the patient: the wizard drops them itself."""
    offered = offered if offered is not None else offered_virtuaortho(doctor)
    held = held if held is not None else subscriptions.effective(doctor).subscription
    twin = resolver.add_on(ODB_DT)
    lines = []
    for variant in variants_offered(offered):
        category = VARIANTS[variant]
        for product, money in offered:
            choice = {category: CASE, Product.ODB: ODB, Product.RETAINERS: RETAINERS,
                      Product.SPARK_ON_DEMAND: SPARK}.get(product.category)
            if choice is None or (choice == SPARK and variant != ONE) \
                    or (choice == CASE and not product.size):
                continue
            price = money
            if choice == ODB and variant == DIGITALTWIN:
                if twin is None or twin not in resolver.add_ons(product):
                    continue
                try:
                    price = resolver.price(product, held, [twin])
                except ValueError:
                    continue
            if price is None:
                continue
            lines.append((variant, choice, product, price))
    return lines


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


def _require_doctor(doctor):
    if not is_client(doctor) or doctor.is_erased:
        raise CartRefused(NOT_A_DOCTOR)


def _entity_patient(doctor, code):
    """The doctor's own patient with this code, when the doctor has a usable fiscal entity;
    else `CartRefused` (`NO_PATIENT`, then `NO_ENTITY`)."""
    patient = patients_service.patient_of(doctor, code)
    if patient is None:
        raise CartRefused(NO_PATIENT)
    if not fiscal.has_usable_entity(doctor):
        raise CartRefused(NO_ENTITY)
    return patient


def _require_free(doctor, patient, is_ice):
    """`LIVE_WORK_ORDER` for a patient with a blocking work order (a live one, or one waiting
    for a bank transfer, epic-ordering 9.8) unless it is a live ICE and the line is not an
    ICE (a TPS is the planning after it); `IN_CART` for a patient already in the cart."""
    if not patients_service.may_take_line(patients_service.blocking_work_order(patient), is_ice):
        raise CartRefused(LIVE_WORK_ORDER)
    if CartItem.objects.filter(doctor_id=doctor.pk, patient_id=patient.pk).exists():
        raise CartRefused(IN_CART)


def add_tps(doctor, patient, *, maker, other_name='', in_house=False, size, add_on_codes=()):
    """Put a TPS or an ICE for `patient` in `doctor`'s cart and return the `CartItem`; `CartRefused`
    (see the module docstring and `CODES`) for anything not allowed."""
    _require_doctor(doctor)
    patient = _entity_patient(doctor, getattr(patient, 'code', None))
    maker, other_name = _maker(doctor, maker, other_name, in_house)
    # An ICE is refused with any blocking work order, a TPS unless it is a live ICE.
    _require_free(doctor, patient, size == ICE_SIZE)
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
        if not size:
            raise CartRefused(NOT_OFFERED)
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
    aligners of a Spark product (within its range), else 1. A blocking work order (a live one,
    or one waiting for a bank transfer) refuses unless it is a live ICE. `CartRefused` for
    anything not allowed; nothing is changed then."""
    _require_doctor(doctor)
    patient = _entity_patient(doctor, getattr(patient, 'code', None))
    _require_free(doctor, patient, False)
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


# --- A manual work order (9.9) ---------------------------------------------------------------

# The VirtuaOrtho categories whose variant is fixed by the product.
_FIXED_VARIANT = {Product.VIRTUAORTHO_ONE: ONE, Product.VIRTUAORTHO_DIGITALTWIN: DIGITALTWIN,
                  Product.SPARK_ON_DEMAND: ONE}


def work_order_variant(doctor, product, variant=''):
    """The VirtuaOrtho variant a manual work order's product is bought under: fixed by a
    One, DigitalTwin or Spark product; `variant` (one the doctor is offered) for an ODB,
    whose DigitalTwin form carries ODB DigitalTwin; for retainers the first variant offered
    (it changes nothing). '' when there is none (`CartRefused` `NOT_OFFERED` for an ODB)."""
    if product.category in _FIXED_VARIANT:
        return _FIXED_VARIANT[product.category]
    offered = variants_offered(offered_virtuaortho(doctor))
    if product.category == Product.ODB:
        if variant not in offered:
            raise CartRefused(NOT_OFFERED)
        return variant
    return offered[0] if offered else ''


def check_work_order(doctor, work_order):
    """(work order, patient, product) when the doctor may add the manual work order
    `work_order` (a `WorkOrder` or its id) to the cart now, else `CartRefused`:
    `NOT_A_DOCTOR`; `WORK_ORDER` when it is not one of the doctor's manual work orders
    waiting to be added (another doctor's, already paid, gone, or held by an order waiting
    for a bank transfer); `NO_PATIENT`, `NO_ENTITY`; `LIVE_WORK_ORDER` when the patient has a
    blocking work order (a live ICE lets anything but an ICE through, as for `add_tps`);
    `IN_CART` when the patient already has an item; `NOT_OFFERED` when the doctor's
    subscription does not offer its product today (a VirtuaOrtho product: not among the
    rows `virtuaortho_lines` gives the doctor, under its variant when the product fixes
    one); `NOT_FINISHED` for retainers without a finished treatment. Nothing is
    changed."""
    _require_doctor(doctor)
    row = patients_service.manual_work_order_of(doctor, getattr(work_order, 'pk', work_order))
    if row is None or row.order_id is not None:
        raise CartRefused(WORK_ORDER)
    patient = _entity_patient(doctor, row.patient.code)
    product = resolver.product(row.product_id) if row.product_id else None
    _require_free(doctor, patient, product is not None and product.category == Product.ICE)
    if product is None or product.pk not in {offered.pk for offered, _money
                                             in subscriptions.offers_for(doctor)}:
        raise CartRefused(NOT_OFFERED)
    if product.category in VIRTUAORTHO_CATEGORIES:
        fixed = _FIXED_VARIANT.get(product.category)
        if not any(offered.pk == product.pk and (fixed is None or line_variant == fixed)
                   for line_variant, _choice, offered, _price in virtuaortho_lines(doctor)):
            raise CartRefused(NOT_OFFERED)
        if product.category == Product.RETAINERS \
                and not patients_service.has_finished_treatment(patient):
            raise CartRefused(NOT_FINISHED)
    return row, patient, product


def add_work_order(doctor, work_order, *, maker='', other_name='', in_house=False,
                   add_on_codes=(), variant='', quantity=1, address_id=''):
    """Put the manual work order `work_order` in `doctor`'s cart with its product (fixed)
    and the options chosen, and return the `CartItem` (it keeps the work order's id). Checked
    as `check_work_order`, then as `add_tps` (a TPS: the maker, `in_house` only for an ICE,
    a branded TPS never and an in-house one always) or `add_virtuaortho` (`variant` for an
    ODB, the shipping address, the aligners of a Spark product, retainers only after a
    finished treatment), with the add-ons the product offers. Priced, like the other items,
    with the doctor's effective subscription at this moment. `CartRefused` for anything not
    allowed; nothing is changed then."""
    row, patient, product = check_work_order(doctor, work_order)
    codes = [(code or '').strip().upper() for code in add_on_codes]
    address = None
    if product.category in VIRTUAORTHO_CATEGORIES:
        maker, other_name = '', ''
        variant = work_order_variant(doctor, product, variant)
        if product.category == Product.RETAINERS \
                and not patients_service.has_finished_treatment(patient):
            raise CartRefused(NOT_FINISHED)
        if not shipping.has_usable_address(doctor):
            raise CartRefused(NO_ADDRESS)
        try:
            address = shipping.usable_addresses(doctor).filter(pk=address_id).first()
        except (ValueError, ValidationError):
            address = None
        if address is None:
            raise CartRefused(ADDRESS)
        try:
            quantity = int(quantity) if product.per_unit else 1
        except (TypeError, ValueError):
            raise CartRefused(QUANTITY) from None
        is_odb_twin = product.category == Product.ODB and variant == DIGITALTWIN
        if is_odb_twin and ODB_DT not in codes:
            codes.append(ODB_DT)
        if ODB_DT in codes and not is_odb_twin:
            raise CartRefused(ADD_ON)
    else:
        in_house = {Product.TPS_IN_HOUSE: True, Product.TPS_BRANDED: False}.get(
            product.category, bool(in_house))
        maker, other_name = _maker(doctor, maker, other_name, in_house)
        quantity = 1
    chosen = [resolver.add_on(code) for code in codes]
    if None in chosen or len(set(codes)) != len(codes):
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
                maker=maker, other_name=other_name, quantity=quantity,
                shipping_address=address, work_order_id=row.pk)
    except IntegrityError:
        raise CartRefused(IN_CART) from None


def work_order_in_cart(doctor):
    """The ids of the manual work orders in the doctor's cart."""
    return set(CartItem.objects.filter(doctor_id=doctor.pk, work_order_id__isnull=False)
               .values_list('work_order_id', flat=True))


def items_of(doctor):
    """The doctor's cart items, oldest first, with their patients."""
    return list(CartItem.objects.filter(doctor_id=doctor.pk).select_related('patient', 'shipping_address'))


def remove(doctor, item_id):
    """Delete the doctor's item `item_id`; False when it is not theirs (or unknown)."""
    deleted, _ = CartItem.objects.filter(doctor_id=doctor.pk, pk=item_id).delete()
    return bool(deleted)


# --- Finishing the order (9.6) ---------------------------------------------------------------

_SHIPPING_FIELDS = ('label', 'recipient_name', 'clinic_name', 'contact_phone', 'address_line1',
                    'address_line2', 'city', 'postal_code', 'province')


def _shipping_snapshot(address):
    """A copy of the address as it is now (and its id, to mark it used when paid)."""
    snapshot = {name: getattr(address, name) or '' for name in _SHIPPING_FIELDS}
    snapshot['id'] = str(address.pk)
    snapshot['country'] = address.country.code if address.country else ''
    return snapshot


def _order_lines(items, doctor=None):
    """The `OrderLine`s of the cart items, or `CartRefused`. With `doctor`, each TPS or
    VirtuaOrtho line of a patient takes the ICE credit the doctor may deduct for that
    patient (`patients.ice.available_credit`, epic-ordering 9.7)."""
    lines = []
    for item in items:
        product = resolver.product(item.product_id)
        if product is None:
            raise CartRefused(NOT_OFFERED)
        add_ons = [resolver.add_on(code) for code in item.add_on_codes]
        if None in add_ons:
            raise CartRefused(ADD_ON)
        snapshot = None
        if product.category in VIRTUAORTHO_CATEGORIES:
            address = item.shipping_address
            if address is None or not shipping.is_usable(address):
                raise CartRefused(ADDRESS_GONE)
            snapshot = _shipping_snapshot(address)
        lines.append(resolver.OrderLine(
            product, item.quantity, tuple(add_ons), None, item.patient.code, item.maker,
            item.other_name, snapshot, item.work_order_id))
    if doctor is not None:
        country = doctor.country.code if doctor.country else ''
        lines = ice_credits.credited_lines(doctor, lines, country)
    return lines


def _cancel_earlier(doctor):
    """Cancel the doctor's earlier unpaid cart orders (their vouchers are released). A
    `pending_payment` order that ever had a bank transfer attempt is left alone (epic-ordering
    9.17): its items already left the cart and the doctor may have sent the money."""
    transfer = Order.objects.filter(payments__gateway=payments.BANK_TRANSFER).values('pk')
    earlier = Order.objects.filter(
        user_id=doctor.pk, kind=Order.SERVICE, state__in=Order.OPEN_STATES,
        changes__event=payments.CREATED, changes__detail__source=SOURCE).exclude(
        state=Order.PENDING_PAYMENT, pk__in=transfer).distinct()
    for order in earlier:
        try:
            payments.cancel(order, expected_version=order.version,
                            actor=payments.doctor_actor(doctor))
        except payments.AttemptOpen:
            raise CartRefused(PAYMENT_OPEN) from None


def finish(doctor, voucher_code=''):
    """Create the service order of the doctor's cart and return it (in `checkout`, or paid
    at once when it comes to 0.00). `CartRefused` (`EMPTY`, `NO_ENTITY`, `ADDRESS_GONE`,
    `PAYMENT_OPEN`, `NOT_OFFERED`, `ADD_ON`, `ICE_TAKEN`: an ICE credit was taken by another
    order meanwhile), `catalogue.pricing.OrderRefused` (a voucher
    refusal, two companies, two currencies, a patient with a case in progress...) or a
    `payments.service.OrderError`: nothing is changed then, the earlier order included. The
    items stay in the cart until the order is paid or approved by bank transfer (9.17); an
    order waiting for a transfer is not cancelled here."""
    if not is_client(doctor) or doctor.is_erased:
        raise CartRefused(NOT_A_DOCTOR)
    items = items_of(doctor)
    if not items:
        raise CartRefused(EMPTY)
    if not fiscal.has_usable_entity(doctor):
        raise CartRefused(NO_ENTITY)
    _order_lines(items)  # CartRefused before anything is locked
    code = (voucher_code or '').strip()
    voucher = voucher_redemptions.voucher_for_code(code) if code else None
    with transaction.atomic():
        # One finish at a time per doctor: a second request waits, then cancels this order.
        get_user_model().objects.select_for_update().get(pk=doctor.pk)
        _cancel_earlier(doctor)
        # The ICE credits are looked up once the earlier order released its own (9.7).
        lines = _order_lines(items, doctor)
        try:
            return payments.create_order(doctor, lines, actor=payments.doctor_actor(doctor),
                                         voucher=voucher, source=SOURCE)
        except payments.HookFailed as failure:
            if isinstance(failure.__cause__, ice_credits.IceTaken):
                raise CartRefused(ICE_TAKEN) from None
            raise


def _remove_ordered(order):
    """Delete the cart items of the order's patients (each line's patient and product)."""
    for line in order.lines.exclude(patient_code=''):
        CartItem.objects.filter(doctor_id=order.user_id, patient__code=line.patient_code,
                                product_id=line.product_id).delete()


def transfer_approved(order):
    """The receiver of the order's `transfer_approved` hook (epic-ordering 9.17): the doctor
    chose the bank transfer, so the items of the order's patients leave the cart (the
    addresses are marked used only when it is paid). Idempotent."""
    if order.user_id is None:
        return
    _remove_ordered(order)


def paid(order):
    """The receiver of the order's `paid` hook: the items of the paid patients leave the
    cart and each shipping address the order snapshotted is marked used. Idempotent. An order
    that ever had a bank transfer attempt removed its items at approval (9.17): an item added
    since for the same patient belongs to no paid order and stays."""
    if order.user_id is None:
        return
    if not order.payments.filter(gateway=payments.BANK_TRANSFER).exists():
        _remove_ordered(order)
    for line in order.lines.all():
        if not line.shipping:  # an encrypted column: read here, never queried
            continue
        address = shipping.ShippingAddress.objects.filter(
            pk=line.shipping.get('id'), user_id=order.user_id).first()
        if address is not None:
            shipping.mark_used(address)
