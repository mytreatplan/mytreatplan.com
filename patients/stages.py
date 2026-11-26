"""The doctor-facing label of each work order stage (`mytp_core.stages`), translated (EN/ES).
The portal shows the case's stage to the doctor with these (epic-case-lifecycle 10.1); the
visible text is the label, and the stage code appears only in a `data-case-stage` attribute."""
from django.utils.translation import gettext_lazy as _
from mytp_core import stages

STAGE_LABELS = {
    stages.PENDING_PAYMENT: _('Pending payment'),
    stages.PENDING_ASSIGNMENT: _('Pending assignment'),
    stages.ASSIGNED: _('Assigned'),
    stages.PENDING_REGISTERS_UPLOAD: _('Records requested'),
    stages.PROCESSING_PRESCRIPTION: _('Processing prescription'),
    stages.PRESCRIPTION_DONE: _('Prescription done'),
    stages.PENDING_APPOINTMENT: _('Appointment requested'),
    stages.IN_DEVELOPMENT: _('In development'),
    stages.PENDING_REVIEW: _('Under review'),
    stages.PENDING_APPROVAL: _('Awaiting your approval'),
    stages.APPROVED: _('Approved'),
    stages.CANCELLED: _('Cancelled'),
    stages.CLOSED: _('Closed'),
}
assert set(STAGE_LABELS) == set(stages.STAGES)


def stage_label(stage):
    """The translated label of `stage` (the code itself for an unknown one)."""
    return STAGE_LABELS.get(stage, stage)
