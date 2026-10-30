"""The contact form's messages (story 1.5; mytp_publicsite.contact).

One row per accepted message: the sender's details, encrypted at rest as every personal
field (encryption policy, "Personal fields"), the three yes/no answers, when it was sent,
and what became of the mail to CONTACT_INBOX. Kept until an admin deletes it (decision 1):
no automatic deletion. The admin is read-only, plus delete.
"""
import uuid

from django.db import models
from django.db.models.functions import UUID4
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.crypto import EncryptedCharField, EncryptedEmailField

# The longest values accepted (enviar.php's limits).
NAME_MAX_LENGTH = 80
COUNTRY_MAX_LENGTH = 80
PHONE_MAX_LENGTH = 32
EMAIL_MAX_LENGTH = 254


class Answer(models.TextChoices):
    YES = 'yes', _('Yes')
    NO = 'no', _('No')


class MailStatus(models.TextChoices):
    PENDING = 'pending', _('Pending')
    SENT = 'sent', _('Sent')
    FAILED = 'failed', _('Failed')


class ContactMessage(models.Model):
    CROSSES_SITES = True

    # AD-5: the id other sites know this record by (the integer key never leaves the
    # instance). Set once, never changed, never shown in a form. The database default
    # gives rows inserted without it (the previous release during a deploy) their own.
    uid = models.UUIDField(default=uuid.uuid4, db_default=UUID4(), unique=True,
                           editable=False)
    first_name = EncryptedCharField(_('first name'), max_length=NAME_MAX_LENGTH)
    middle_name = EncryptedCharField(_('middle name or initial'), max_length=NAME_MAX_LENGTH,
                                     blank=True)
    surname = EncryptedCharField(_('surname'), max_length=NAME_MAX_LENGTH)
    country = EncryptedCharField(_('country'), max_length=COUNTRY_MAX_LENGTH)
    phone = EncryptedCharField(_('phone'), max_length=PHONE_MAX_LENGTH)
    email = EncryptedEmailField(_('email'), max_length=EMAIL_MAX_LENGTH)
    aligners = models.CharField(_('interested in aligners?'), max_length=3,
                                choices=Answer.choices, blank=True)
    # Not shown on the page for now (decision 3): no input is rendered, so it is empty
    # unless a request posts it.
    club = models.CharField(_('wants to know about the UAE club?'), max_length=3,
                            choices=Answer.choices, blank=True)
    decision = models.CharField(_('makes purchasing decisions?'), max_length=3,
                                choices=Answer.choices, blank=True)
    sent_at = models.DateTimeField(_('sent at'), default=timezone.now, editable=False)
    mail_status = models.CharField(_('mail'), max_length=10, choices=MailStatus.choices,
                                   default=MailStatus.PENDING, editable=False)

    class Meta:
        ordering = ('-sent_at', '-id')
        verbose_name = _('contact message')
        verbose_name_plural = _('contact messages')

    def __str__(self):
        # No personal value: this is what the admin log and its messages record.
        return f'{self._meta.verbose_name} {self.pk}'
