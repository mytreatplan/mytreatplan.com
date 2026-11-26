"""The one place that writes patients and work orders (epic-ordering 9.1, AD-15, AD-21).

- `create_patient`: a patient with a generated code (region prefix, `-`, 8 random Crockford
  base32 characters from `secrets`; drawn again on a collision, never derived from the
  patient's data).
- `move_work_order`: a stage change, checked by the core's `mytp_core.stages` table, under
  the row lock and the version. The one-live-work-order rule is also a partial unique
  constraint; here it surfaces as `LiveWorkOrderExists`.
- `blocking_work_order`: what keeps a patient from a new service (epic-ordering 9.8): a
  live work order, or a bank-transfer work order (`pending_payment` with an `order_id`) of
  another order. A manual pending-payment work order (no order, 9.9) never blocks.
- The order hooks (epic-ordering 9.8), one work order per order line that carries a
  patient code, each idempotent per line:
  `hold_work_orders_for_transfer` (`transfer_approved`: created in `pending_payment`),
  `create_work_orders_for_order` (`paid`: the line's `pending_payment` row moves to
  `pending_assignment`, else one is created there; a live ICE of the patient of a line
  that is not an ICE is `closed` first) and `cancel_work_orders_for_order` (`cancelled`,
  `expired`: the order's `pending_payment` rows are `cancelled`). A failure rolls the
  order's transition back (`payments.service.HookFailed`).
- `names_for_invoice`: the invoice PDF's name lookup (invoices.patients).

Every create and stage change records a `work_order.created` or `work_order.stage_changed`
event in the outbox (mytp-core 0.35.0), in the same transaction: the work order's id,
stage, product category and time, never the patient's name or code.

No patient name or comment is logged here.
"""
import logging
import secrets
import unicodedata

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from catalogue import resolver
from catalogue.models import Product
from integration import outbox
from mytp_core import stages
from mytp_core.contracts import WORK_ORDER_CREATED, WORK_ORDER_STAGE_CHANGED
from mytp_core.stages import InvalidStageTransition  # noqa: F401  (re-exported)
from subscriptions.service import SYSTEM_ACTOR, Actor

from .models import Patient, WorkOrder

logger = logging.getLogger(__name__)

# Crockford base32: no I, L, O, U.
CROCKFORD = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
CODE_LENGTH = 8
_CODE_TRIES = 10
ACTOR_TYPES = ('doctor', 'admin', 'system')


class PatientError(Exception):
    """Base of the refusals below: nothing was changed."""


class PatientInvalid(PatientError):
    """A patient needs a first name, a surname or a reference."""


class InvalidActor(PatientError):
    """Not an actor: a doctor or an admin user has a UUID, the system has none."""


class StaleVersion(PatientError):
    """The row changed since the caller read it."""

    def __init__(self, current):
        super().__init__(f'the row is at version {current}')
        self.current = current


class LiveWorkOrderExists(PatientError):
    """The patient already has a work order in a live stage."""


class WorkOrderRefused(PatientError):
    """A paid order line could not become a work order (no such patient of the doctor)."""


def _check_actor(actor):
    if not isinstance(actor, Actor) or actor.type not in ACTOR_TYPES:
        raise InvalidActor(f'not an actor: {actor!r}')
    if (actor.type == 'system') != (actor.uuid is None):
        raise InvalidActor('the system has no UUID; a doctor or an admin user has one')


def generate_code():
    """A fresh patient code of this region, e.g. `EU-7K3QX9MD` (not checked for use)."""
    prefix = settings.REGION_PROFILE.patient_code_prefix
    return prefix + '-' + ''.join(secrets.choice(CROCKFORD) for _ in range(CODE_LENGTH))


def create_patient(doctor, *, first_name='', surname='', reference='', comments='', actor):
    """Create a patient of `doctor`; at least one of the first name, surname and reference
    is needed (`PatientInvalid`). Returns the `Patient`."""
    _check_actor(actor)
    first_name, surname = (first_name or '').strip(), (surname or '').strip()
    reference, comments = (reference or '').strip(), (comments or '').strip()
    if not (first_name or surname or reference):
        raise PatientInvalid('a first name, a surname or a reference is needed')
    now = timezone.now()
    for attempt in range(_CODE_TRIES):
        try:
            with transaction.atomic():
                return Patient.objects.create(
                    doctor=doctor, code=generate_code(), first_name=first_name,
                    surname=surname, reference=reference, comments=comments,
                    actor_type=actor.type, actor_uuid=actor.uuid, created_at=now,
                    updated_at=now)
        except IntegrityError:
            # The code is taken: draw again.
            if attempt == _CODE_TRIES - 1:
                raise


def fold(text):
    """Lower-case `text` and drop its accents (NFKD, combining marks removed), the form the
    chooser's search compares in (patient-list.js folds the typed term the same way)."""
    decomposed = unicodedata.normalize('NFKD', text or '')
    return ''.join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def search_text(patient):
    """What the patient chooser searches: the code, first name, surname and reference,
    folded; an erased patient carries the code only (no name ever matches it)."""
    if patient.status == Patient.ERASED:
        return fold(patient.code)
    return fold(' '.join(
        part for part in (patient.code, patient.first_name, patient.surname,
                          patient.reference) if part))


def patients_of(doctor):
    """The doctor's patients, newest first."""
    return Patient.objects.filter(doctor_id=doctor.pk)


def patient_of(doctor, code):
    """The doctor's active patient with `code`, or None (another doctor's, erased or
    unknown)."""
    return patients_of(doctor).filter(code=code, status=Patient.ACTIVE).first()


def live_work_order(patient):
    """The patient's work order in a live stage, or None."""
    return WorkOrder.objects.filter(
        patient_id=patient.pk, stage__in=sorted(stages.LIVE_STAGES)).first()


def blocking_work_order(patient, exclude_order=None):
    """The work order that keeps `patient` from a new service, or None (epic-ordering 9.8):
    a bank-transfer work order (`pending_payment` with an `order_id`) of an order other than
    `exclude_order` (an `Order` or its id), which always blocks and so comes first; else
    the live work order (a live ICE still lets a planning through: the caller checks it
    with `is_live_ice`). A `pending_payment` row without an order (a manual one,
    9.9) never blocks, and neither do an order's own rows when it is `exclude_order`."""
    pending = WorkOrder.objects.filter(patient_id=patient.pk, stage=stages.PENDING_PAYMENT,
                                       order_id__isnull=False)
    if exclude_order is not None:
        pending = pending.exclude(order_id=getattr(exclude_order, 'pk', exclude_order))
    return pending.order_by('created_at', 'id').first() or live_work_order(patient)


def is_ice_work_order(work_order):
    """Whether `work_order` is an ICE's. False when its product is no longer in the
    catalogue (it may be a TPS, so a second one is refused)."""
    product = resolver.product(work_order.product_id) if work_order.product_id else None
    return product is not None and product.category == Product.ICE


def is_live_ice(work_order):
    """Whether `work_order` is a live ICE work order (the only one a planning may follow)."""
    return (work_order is not None and work_order.stage in stages.LIVE_STAGES
            and is_ice_work_order(work_order))


# The categories whose approved work order is a "finished treatment" (epic-ordering 9.4):
# retainers follow one of them.
FINISHED_CATEGORIES = (Product.VIRTUAORTHO_ONE, Product.VIRTUAORTHO_DIGITALTWIN,
                       Product.SPARK_ON_DEMAND)


def has_finished_treatment(patient):
    """Whether the patient has a work order in stage `approved` whose product is a
    VirtuaOrtho One, a DigitalTwin or a Spark on Demand (a cancelled or closed one does not
    count).
    Until epic-case-lifecycle moves cases to `approved`, only a fixture has one."""
    ids = list(WorkOrder.objects.filter(patient_id=patient.pk, stage=stages.APPROVED)
               .exclude(product_id=None).values_list('product_id', flat=True))
    return bool(ids) and Product.objects.filter(
        pk__in=ids, category__in=FINISHED_CATEGORIES).exists()


def _product_category(work_order):
    product = resolver.product(work_order.product_id) if work_order.product_id else None
    return product.category if product is not None else ''


def _record_event(event_type, work_order):
    """The work order's event in the outbox, inside the caller's transaction: its id, stage,
    product category and time (no patient name or code; mytp-core refuses one)."""
    outbox.record(event_type, work_order.pk, work_order.version, {
        'work_order_id': work_order.pk, 'stage': work_order.stage,
        'product_category': _product_category(work_order), 'at': work_order.updated_at})


def _move_locked(locked, to_stage, actor):
    """Move the locked row to `to_stage` (checked by the core's table), raise its version,
    and record `work_order.stage_changed`."""
    stages.check_transition(locked.stage, to_stage)
    locked.stage = to_stage
    locked.version += 1
    locked.actor_type, locked.actor_uuid = actor.type, actor.uuid
    locked.updated_at = timezone.now()
    try:
        with transaction.atomic():
            locked.save()
    except IntegrityError:
        raise LiveWorkOrderExists('the patient already has a live work order') from None
    _record_event(WORK_ORDER_STAGE_CHANGED, locked)
    return locked


def move_work_order(work_order, to_stage, expected_version, actor):
    """Move `work_order` to `to_stage` if the core's table allows it
    (`InvalidStageTransition`) and `expected_version` is current (`StaleVersion`), with
    its `work_order.stage_changed` event. Returns the locked, updated row."""
    _check_actor(actor)
    with transaction.atomic():
        locked = WorkOrder.objects.select_for_update().get(pk=work_order.pk)
        if locked.version != expected_version:
            raise StaleVersion(locked.version)
        return _move_locked(locked, to_stage, actor)


def _create_work_order(order, line, patient, stage):
    """A work order of `line` in `stage`, with its `work_order.created` event."""
    now = timezone.now()
    try:
        with transaction.atomic():
            work_order = WorkOrder.objects.create(
                patient=patient, order_line_id=line.pk, order_id=order.pk,
                product_id=line.product_id, code=line.code, stage=stage,
                actor_type=SYSTEM_ACTOR.type, actor_uuid=None, created_at=now,
                updated_at=now)
    except IntegrityError:
        raise LiveWorkOrderExists(
            f'order {order.pk}: the patient already has a live work order') from None
    _record_event(WORK_ORDER_CREATED, work_order)
    return work_order


def _is_case(line):
    """Whether an order line is a case (epic-ordering 9.8: every line that names a patient,
    any service; a test or subscription line names none)."""
    return bool(line.patient_code)


def _is_ice_line(line):
    product = resolver.product(line.product_id) if line.product_id else None
    return product is not None and product.category == Product.ICE


def _case_lines(order):
    """(line, patient) for each case line of `order`, in order; `WorkOrderRefused` when a
    line names no active patient of the buyer."""
    found = []
    for line in order.lines.exclude(patient_code='').order_by('position'):
        if not _is_case(line):
            continue
        patient = Patient.objects.filter(
            code=line.patient_code, doctor_id=order.user_id, status=Patient.ACTIVE).first()
        if patient is None:
            raise WorkOrderRefused(f'order {order.pk}: line {line.position} names no '
                                   f'patient of the buyer')
        found.append((line, patient))
    return found


def _line_work_order(order, line, patient):
    """The locked work order of `line`: by its line id, else (the line ids changed) the
    order's row for the patient (a patient is named once per order). None if there is none."""
    rows = WorkOrder.objects.select_for_update()
    return (rows.filter(order_line_id=line.pk).first()
            or rows.filter(order_id=order.pk, patient_id=patient.pk)
            .order_by('created_at', 'id').first())


def hold_work_orders_for_transfer(order):
    """The `transfer_approved` receiver: a work order in `pending_payment` for each case
    line of `order` (it blocks the patient until the order is paid or cancelled). Fired
    each time the transfer is chosen: a line that has one already gets nothing more. A
    patient's live ICE stays live until the planning is paid."""
    for line, patient in _case_lines(order):
        if _line_work_order(order, line, patient) is None:
            _create_work_order(order, line, patient, stages.PENDING_PAYMENT)


def _close_live_ice(patient):
    """A planning of `patient` was paid: their live ICE work order, if any, is `closed`
    (never `cancelled`, user decision 2026-11-25)."""
    live = WorkOrder.objects.select_for_update().filter(
        patient_id=patient.pk, stage__in=sorted(stages.LIVE_STAGES)).first()
    if live is not None and is_ice_work_order(live):
        _move_locked(live, stages.CLOSED, SYSTEM_ACTOR)


def create_work_orders_for_order(order):
    """The `paid` receiver: a work order in `pending_assignment` for each case line of the
    paid `order` (a line that names a patient): the line's `pending_payment` row (a bank
    transfer) moves there, else one is created. For a line that is not an ICE, the
    patient's live ICE work order is `closed` first. Idempotent per order line. Raises
    `WorkOrderRefused` or `LiveWorkOrderExists`, which roll the payment back."""
    for line, patient in _case_lines(order):
        existing = _line_work_order(order, line, patient)
        if existing is not None and existing.stage != stages.PENDING_PAYMENT:
            continue
        if not _is_ice_line(line):
            _close_live_ice(patient)
        if existing is not None:
            _move_locked(existing, stages.PENDING_ASSIGNMENT, SYSTEM_ACTOR)
        else:
            _create_work_order(order, line, patient, stages.PENDING_ASSIGNMENT)


def cancel_work_orders_for_order(order):
    """The `cancelled` and `expired` receiver: the order's `pending_payment` work orders
    (a bank transfer that never came) are `cancelled`. Idempotent."""
    for work_order in WorkOrder.objects.select_for_update().filter(
            order_id=order.pk, stage=stages.PENDING_PAYMENT).order_by('created_at', 'id'):
        _move_locked(work_order, stages.CANCELLED, SYSTEM_ACTOR)


def names_for_invoice(user, codes):
    """`{code: name}` for the codes of `user`'s patients that have a name or a reference;
    an erased patient or another doctor's code is left out."""
    found = {}
    for patient in patients_of(user).filter(code__in=list(codes), status=Patient.ACTIVE):
        name = patient.display_name
        if name:
            found[patient.code] = name
    return found
