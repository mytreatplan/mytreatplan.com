"""The message row (epic-case-lifecycle 10.2): what is to be told to whom about which version
of a work order, over which channel, and how it went. No patient data: the work order is a
plain UUID and the patient is reached through it only when a message is rendered. Written
only by `notifications.service`."""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class Message(models.Model):
    PENDING_APPOINTMENT = 'case_pending_appointment'
    PENDING_REVIEW = 'case_pending_review'
    PENDING_APPROVAL = 'case_pending_approval'
    KINDS = [(PENDING_APPOINTMENT, 'Case pending appointment'),
             (PENDING_REVIEW, 'Case pending review'),
             (PENDING_APPROVAL, 'Case pending approval')]

    EMAIL = 'email'
    CHANNELS = [(EMAIL, 'Email')]

    PENDING, SENT, FAILED = 'pending', 'sent', 'failed'
    STATES = [(PENDING, 'Pending'), (SENT, 'Sent'), (FAILED, 'Failed')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=40, choices=KINDS)
    channel = models.CharField(max_length=10, choices=CHANNELS, default=EMAIL)
    # Plain UUID, no database link (as the other work order references); with the version it
    # makes the row once per real arrival at a stage.
    work_order_id = models.UUIDField()
    work_order_version = models.PositiveIntegerField()
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                  related_name='+')
    state = models.CharField(max_length=10, choices=STATES, default=PENDING)
    # `no_address` or the exception type of a failed send, for 10.3 to act on.
    reason = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']
        constraints = [models.UniqueConstraint(
            fields=('work_order_id', 'work_order_version', 'channel'),
            name='notifications_one_message_per_version_and_channel')]
