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
- `create_manual_work_order` (epic-ordering 9.9): the seam clinical ops call (later, over
  their remote write; today only tests and the fixture `patients.tests.conftest.
  manual_work_order`) to open a case for one of a doctor's patients before it is paid: a
  `pending_payment` row with the product and no order or line. It never blocks the patient;
  the doctor sees it on the home page and adds it to the cart (`cart.service.
  add_work_order`, priced at their tier then), and the order line carries its id.
  `manual_work_orders` lists a doctor's.
- The order hooks (epic-ordering 9.8), one work order per order line that carries a
  patient code, each idempotent per line:
  `hold_work_orders_for_transfer` (`transfer_approved`: created in `pending_payment`; a
  line paying for a manual work order gives that row the order's `order_id` instead, so
  it blocks the patient like a hold),
  `create_work_orders_for_order` (`paid`: the line's `pending_payment` row moves to
  `pending_assignment`, the line's manual row being adopted (its `order_line_id` and
  `order_id` set, never a second row), else one is created there; a live ICE of the
  patient of a line that is not an ICE is `closed` first) and
  `cancel_work_orders_for_order` (`cancelled`, `expired`: the order's `pending_payment`
  rows are `cancelled`, except a manual one, which only loses the order's `order_id` and
  waits again). A failure rolls the order's transition back
  (`payments.service.HookFailed`).
- `update_comments` and `set_active` (epic-case-lifecycle 10.4): the patient file's comments
  (at most 2000 characters) and active/inactive control, each under the row lock and the
  version (`StaleVersion`), storing the actor. Inactive keeps the patient listed and any
  live case going; only new orders are refused.
- `current_work_orders`: the current case (live, else latest) of many patients in one query,
  for the patient list's stage column.
- `names_for_invoice`: the invoice PDF's name lookup (invoices.patients).

Every create and stage change records a `work_order.created` or `work_order.stage_changed`
event in the outbox (mytp-core 0.35.0), in the same transaction: the work order's id,
stage, product category and time, never the patient's name or code.

No patient name or comment is logged here.
"""
import logging
import secrets
import unicodedata
import uuid

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from catalogue import resolver
from catalogue.models import Product
from integration import outbox
from mytp_core import moves as core_moves
from mytp_core import stages
from mytp_core.contracts import WORK_ORDER_CREATED, WORK_ORDER_STAGE_CHANGED
from mytp_core.stages import InvalidStageTransition  # noqa: F401  (re-exported)
from subscriptions.service import SYSTEM_ACTOR, Actor

from . import moves as move_receivers
from .models import Patient, StageChange, WorkOrder

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


class MoveRefused(PatientError):
    """A named move the stage, the actor or the arguments do not allow (a doctor move by
    an admin, a staff move by a doctor, another doctor's patient, a missing or
    unneeded assignee): nothing was changed."""


class LiveWorkOrderExists(PatientError):
    """The patient already has a work order in a live stage."""


class WorkOrderRefused(PatientError):
    """A paid order line could not become a work order (no such patient of the doctor), or
    a manual work order was refused (not an active patient of the doctor, or not a product
    a case is made of): nothing was created."""


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


COMMENTS_MAX = 2000


def _locked_patient(patient):
    """The locked row of a patient that is not erased (`PatientInvalid` for an erased or
    missing one)."""
    locked = Patient.objects.select_for_update().filter(pk=patient.pk).first()
    if locked is None or locked.status == Patient.ERASED:
        raise PatientInvalid('no such patient')
    return locked


def _write_locked(locked, actor, **fields):
    for name, value in fields.items():
        setattr(locked, name, value)
    locked.version += 1
    locked.actor_type, locked.actor_uuid = actor.type, actor.uuid
    locked.updated_at = timezone.now()
    locked.save()
    return locked


def update_comments(patient, text, expected_version, actor):
    """Replace the patient's comments (at most 2000 characters, `PatientInvalid`), under the
    row lock: `StaleVersion` unless `expected_version` is current. The text is never logged.
    Returns the locked, updated row."""
    _check_actor(actor)
    text = (text or '').strip()
    if len(text) > COMMENTS_MAX:
        raise PatientInvalid('the comments are too long')
    with transaction.atomic():
        locked = _locked_patient(patient)
        if locked.version != expected_version:
            raise StaleVersion(locked.version)
        return _write_locked(locked, actor, comments=text)


def set_active(patient, active, expected_version, actor):
    """Make the patient active or inactive (a live case carries on; only new orders are
    refused), under the row lock: `StaleVersion` unless `expected_version` is current.
    Returns the locked, updated row."""
    _check_actor(actor)
    with transaction.atomic():
        locked = _locked_patient(patient)
        if locked.version != expected_version:
            raise StaleVersion(locked.version)
        return _write_locked(
            locked, actor, status=Patient.ACTIVE if active else Patient.INACTIVE)


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


def current_work_order(patient):
    """The case the patient's file shows: the live work order, else the latest one (by
    creation), or None when there is none."""
    return live_work_order(patient) or WorkOrder.objects.filter(
        patient_id=patient.pk).order_by('-created_at', '-id').first()


def current_work_orders(patients):
    """The current work order (`current_work_order`: the live one, else the latest) of each
    of `patients`, in one query: a dict of patient id to `WorkOrder`; a patient without a
    case is absent."""
    ids = [patient.pk for patient in patients]
    live = set(stages.LIVE_STAGES)
    chosen = {}
    for work_order in WorkOrder.objects.filter(patient_id__in=ids).order_by(
            'created_at', 'id'):
        held = chosen.get(work_order.patient_id)
        # Ascending creation: a later one replaces an earlier, but never a live one.
        if held is None or held.stage not in live:
            chosen[work_order.patient_id] = work_order
    return chosen


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


def may_take_line(blocking, is_ice):
    """Whether a patient whose `blocking_work_order` is `blocking` may take a new service
    line: nothing blocks, or a live ICE blocks and the line is not an ICE (the one ICE rule,
    for the cart and for the order)."""
    return blocking is None or (not is_ice and is_live_ice(blocking))


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


def _record_stage_change(work_order, from_stage, move, actor, at):
    """The history row of a stage change (creation: `from_stage` empty)."""
    StageChange.objects.create(
        work_order=work_order, move=move, from_stage=from_stage, to_stage=work_order.stage,
        version=work_order.version, actor_type=actor.type, actor_uuid=actor.uuid, at=at)


def _move_locked(locked, to_stage, actor, move=''):
    """Move the locked row to `to_stage` (checked by the core's table), raise its version,
    write the history row (`move`: the named move, else empty) and record
    `work_order.stage_changed`."""
    stages.check_transition(locked.stage, to_stage)
    from_stage = locked.stage
    locked.stage = to_stage
    locked.version += 1
    locked.actor_type, locked.actor_uuid = actor.type, actor.uuid
    locked.updated_at = timezone.now()
    try:
        with transaction.atomic():
            locked.save()
    except IntegrityError:
        raise LiveWorkOrderExists('the patient already has a live work order') from None
    _record_stage_change(locked, from_stage, move, actor, locked.updated_at)
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


def _owns_patient(actor, work_order):
    return Patient.objects.filter(pk=work_order.patient_id, doctor__uid=actor.uuid).exists()


def apply_move(work_order, move, expected_version, actor, assignee=None):
    """Apply the named `move` (`mytp_core.moves`) to `work_order` as `actor`
    (epic-case-lifecycle 10.1, AD-15): under the row lock, `StaleVersion` unless
    `expected_version` is current; `MoveRefused` (nothing changed) when the move is
    unknown, not allowed from the stage, or not for this kind of actor (a staff move needs
    an admin actor, a doctor move a doctor actor who owns the patient), or when `assign`
    has no `assignee` (an admin UUID) or another move has one. Then the stage change with
    its history row and `work_order.stage_changed` event, and the receivers registered for
    the move (`patients.moves`), inside the transaction. Returns the locked, updated row."""
    _check_actor(actor)
    with transaction.atomic():
        locked = WorkOrder.objects.select_for_update().get(pk=work_order.pk)
        if locked.version != expected_version:
            raise StaleVersion(locked.version)
        actor_kind = {'admin': core_moves.STAFF, 'doctor': core_moves.DOCTOR}.get(actor.type)
        try:
            to_stage = core_moves.check_move(move, locked.stage, actor_kind)
        except core_moves.MoveNotAllowed as refused:
            raise MoveRefused(str(refused)) from None
        if actor_kind == core_moves.DOCTOR and not _owns_patient(actor, locked):
            raise MoveRefused('the doctor does not own the patient')
        if (move == 'assign') != (assignee is not None):
            raise MoveRefused('assign needs an assignee, and only assign takes one')
        if assignee is not None:
            try:
                locked.assignee_uuid = uuid.UUID(str(assignee))
            except ValueError:
                raise MoveRefused('the assignee is not a UUID') from None
        _move_locked(locked, to_stage, actor, move=move)
        move_receivers.fire(move, locked)
        return locked


# The categories a manual work order may carry (epic-ordering 9.9): the services a cart
# item is made of (a planning, an ICE, a VirtuaOrtho service).
MANUAL_CATEGORIES = (Product.TPS_BRANDED, Product.TPS_IN_HOUSE, Product.ICE,
                     Product.VIRTUAORTHO_ONE, Product.VIRTUAORTHO_DIGITALTWIN, Product.ODB,
                     Product.RETAINERS, Product.SPARK_ON_DEMAND)


def create_manual_work_order(doctor, patient_code, product_id, *, actor):
    """Open a manual work order for `doctor`'s active patient `patient_code` (patients are
    named by code across sites, AD-23) and the catalogue product `product_id`, before it
    is paid (epic-ordering 9.9). The seam clinical ops will call (epic-clinical-ops adds
    the remote resource, write and screen; `actor` is then their admin user).

    The row is in `pending_payment` with the product and no order or order line, and its
    `work_order.created` event is recorded. It never blocks the patient's other services;
    no price is stored (the doctor's tier prices it when they add it to the cart). The
    doctor cannot decline it. Returns the `WorkOrder`; `WorkOrderRefused` (nothing
    created) when the code is not one of the doctor's active patients or the product is
    unknown or not a case service; `InvalidActor` for a bad actor."""
    _check_actor(actor)
    patient = patient_of(doctor, patient_code) if doctor is not None else None
    if patient is None:
        raise WorkOrderRefused('not an active patient of the doctor')
    product = resolver.product(product_id) if product_id else None
    if product is None or product.category not in MANUAL_CATEGORIES:
        raise WorkOrderRefused('not a product a case is made of')
    now = timezone.now()
    with transaction.atomic():
        work_order = WorkOrder.objects.create(
            patient=patient, order_line_id=None, order_id=None, product_id=product.pk,
            code=product.code or '', stage=stages.PENDING_PAYMENT, actor_type=actor.type,
            actor_uuid=actor.uuid, created_at=now, updated_at=now)
        _record_stage_change(work_order, '', '', actor, now)
        _record_event(WORK_ORDER_CREATED, work_order)
    return work_order


def _manual_rows():
    """Manual work orders: `pending_payment` with no order line (9.9)."""
    return WorkOrder.objects.filter(stage=stages.PENDING_PAYMENT, order_line_id__isnull=True)


def manual_work_orders(doctor):
    """The doctor's manual work orders waiting to be paid (of their active patients),
    oldest first, with their patients: free ones and those whose order waits for a bank
    transfer (`order_id` set)."""
    return list(_manual_rows().filter(
        patient__doctor_id=doctor.pk, patient__status=Patient.ACTIVE)
        .select_related('patient').order_by('created_at', 'id'))


def manual_work_order_of(doctor, work_order_id):
    """The doctor's manual work order `work_order_id` (a UUID or its string), or None."""
    try:
        work_order_id = uuid.UUID(str(work_order_id))
    except ValueError:
        return None
    return next((row for row in manual_work_orders(doctor) if row.pk == work_order_id), None)


def _manual_work_order(line, patient):
    """The locked manual row `line` pays for (its `work_order_id`): the patient's
    `pending_payment` row with no order line, or None (none named, or it is gone, cancelled
    or already adopted)."""
    work_order_id = getattr(line, 'work_order_id', None)
    if not work_order_id:
        return None
    return _manual_rows().select_for_update().filter(
        pk=work_order_id, patient_id=patient.pk).first()


def _save_locked(locked):
    """Save a change of the locked row that is not a stage change (its `order_id`): its
    version is raised, no event is recorded (the stage is the same)."""
    locked.version += 1
    locked.actor_type, locked.actor_uuid = SYSTEM_ACTOR.type, SYSTEM_ACTOR.uuid
    locked.updated_at = timezone.now()
    locked.save(update_fields=['order_id', 'version', 'actor_type', 'actor_uuid',
                               'updated_at'])
    return locked


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
    _record_stage_change(work_order, '', '', SYSTEM_ACTOR, now)
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
    patient's live ICE stays live until the planning is paid.
    A line that pays for a manual work order (9.9) gives that row the order's `order_id`
    (no new row), so it blocks the patient until the order is paid, cancelled or expires;
    `WorkOrderRefused` when that row is held by another order's transfer."""
    for line, patient in _case_lines(order):
        if _line_work_order(order, line, patient) is not None:
            continue
        manual = _manual_work_order(line, patient)
        if manual is None:
            _create_work_order(order, line, patient, stages.PENDING_PAYMENT)
        elif manual.order_id is None:
            manual.order_id = order.pk
            _save_locked(manual)
        elif manual.order_id != order.pk:
            raise WorkOrderRefused(f'order {order.pk}: line {line.position} pays for a work '
                                   f'order held by another order')


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
    transfer) moves there; else the manual work order the line pays for (9.9) is adopted
    (its `order_line_id` and `order_id` set) and moves there; else one is created. For a
    line that is not an ICE, the patient's live ICE work order is `closed` first.
    Idempotent per order line. Raises `WorkOrderRefused` (also when the line's manual work
    order is held by another order's bank transfer, as at transfer approval) or
    `LiveWorkOrderExists` (the one-live rule, checked again here), which roll the payment
    back."""
    for line, patient in _case_lines(order):
        existing = (_line_work_order(order, line, patient)
                    or _manual_work_order(line, patient))
        if existing is not None and existing.stage != stages.PENDING_PAYMENT:
            continue
        if existing is not None and existing.order_line_id is None \
                and existing.order_id not in (None, order.pk):
            raise WorkOrderRefused(f'order {order.pk}: line {line.position} pays for a work '
                                   f'order held by another order')
        if not _is_ice_line(line):
            _close_live_ice(patient)
        if existing is not None:
            if existing.order_line_id is None:
                # A manual work order: this line adopts it (never a second row).
                existing.order_line_id, existing.order_id = line.pk, order.pk
            _move_locked(existing, stages.PENDING_ASSIGNMENT, SYSTEM_ACTOR)
        else:
            _create_work_order(order, line, patient, stages.PENDING_ASSIGNMENT)


def cancel_work_orders_for_order(order):
    """The `cancelled` and `expired` receiver: the order's `pending_payment` work orders
    (a bank transfer that never came) are `cancelled`, except a manual one (9.9, no order
    line): it loses the order's `order_id` and is a plain manual work order again (still
    `pending_payment`, no longer blocking, on the doctor's home page). Idempotent."""
    for work_order in WorkOrder.objects.select_for_update().filter(
            order_id=order.pk, stage=stages.PENDING_PAYMENT).order_by('created_at', 'id'):
        if work_order.order_line_id is None:
            work_order.order_id = None
            _save_locked(work_order)
        else:
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
