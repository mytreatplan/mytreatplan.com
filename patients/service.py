"""The one place that writes patients and work orders (epic-ordering 9.1, AD-15, AD-21).

- `create_patient`: a patient with a generated code (region prefix, `-`, 8 random Crockford
  base32 characters from `secrets`; drawn again on a collision, never derived from the
  patient's data).
- `move_work_order`: a stage change, checked by the core's `mytp_core.stages` table, under
  the row lock and the version. The one-live-work-order rule is also a partial unique
  constraint; here it surfaces as `LiveWorkOrderExists`.
- `create_work_orders_for_order`: the receiver of the order's `paid` hook; one work order
  in `pending_assignment` per order line that carries a patient code, idempotent per line.
  A failure rolls the payment back (`payments.service.HookFailed`).
- `names_for_invoice`: the invoice PDF's name lookup (invoices.patients).

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
from mytp_core import stages
from mytp_core.stages import InvalidStageTransition  # noqa: F401  (re-exported)
from subscriptions.service import Actor

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


def live_is_ice(patient):
    """Whether the patient's live work order is an ICE (the case wizard lets a TPS follow
    it, epic-ordering 9.3). False without a live work order, and when its product is no longer in the
    catalogue (it may be a TPS, so a second one is refused)."""
    work_order = live_work_order(patient)
    if work_order is None:
        return False
    product = resolver.product(work_order.product_id) if work_order.product_id else None
    return product is not None and product.category == Product.ICE


# The categories whose approved work order is a "finished treatment" (epic-ordering 9.4):
# retainers follow one of them.
FINISHED_CATEGORIES = (Product.VIRTUAORTHO_ONE, Product.VIRTUAORTHO_DIGITALTWIN,
                       Product.SPARK_ON_DEMAND)


def has_finished_treatment(patient):
    """Whether the patient has a work order in stage `approved` whose product is a
    VirtuaOrtho One, a DigitalTwin or a Spark on Demand (a cancelled one does not count).
    Until epic-case-lifecycle moves cases to `approved`, only a fixture has one."""
    ids = list(WorkOrder.objects.filter(patient_id=patient.pk, stage=stages.APPROVED)
               .exclude(product_id=None).values_list('product_id', flat=True))
    return bool(ids) and Product.objects.filter(
        pk__in=ids, category__in=FINISHED_CATEGORIES).exists()


def move_work_order(work_order, to_stage, expected_version, actor):
    """Move `work_order` to `to_stage` if the core's table allows it
    (`InvalidStageTransition`) and `expected_version` is current (`StaleVersion`).
    Returns the locked, updated row."""
    _check_actor(actor)
    with transaction.atomic():
        locked = WorkOrder.objects.select_for_update().get(pk=work_order.pk)
        if locked.version != expected_version:
            raise StaleVersion(locked.version)
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
        return locked


def _is_case(line):
    """Whether an order line is a case (9.1: an ICE; 9.8 extends this to every service). A
    product no longer in the catalogue is read as a case: only a case line is given a
    patient code at order creation."""
    product = resolver.product(line.product_id) if line.product_id else None
    return product is None or product.category == Product.ICE


def create_work_orders_for_order(order):
    """The `paid` receiver: a work order in `pending_assignment` for each case line (an
    ICE) of the paid `order` that has a patient code. Idempotent per order line. Raises
    `WorkOrderRefused` or `LiveWorkOrderExists`, which roll the payment back."""
    for line in order.lines.exclude(patient_code='').order_by('position'):
        if not _is_case(line) or WorkOrder.objects.filter(order_line_id=line.pk).exists():
            continue
        patient = Patient.objects.filter(
            code=line.patient_code, doctor_id=order.user_id, status=Patient.ACTIVE).first()
        if patient is None:
            raise WorkOrderRefused(f'order {order.pk}: line {line.position} names no '
                                   f'patient of the buyer')
        now = timezone.now()
        try:
            with transaction.atomic():
                WorkOrder.objects.create(
                    patient=patient, order_line_id=line.pk, product_id=line.product_id,
                    code=line.code, stage=stages.PENDING_ASSIGNMENT,
                    actor_type='system', actor_uuid=None, created_at=now, updated_at=now)
        except IntegrityError:
            raise LiveWorkOrderExists(
                f'order {order.pk}: the patient already has a live work order') from None


def names_for_invoice(user, codes):
    """`{code: name}` for the codes of `user`'s patients that have a name or a reference;
    an erased patient or another doctor's code is left out."""
    found = {}
    for patient in patients_of(user).filter(code__in=list(codes), status=Patient.ACTIVE):
        name = patient.display_name
        if name:
            found[patient.code] = name
    return found
