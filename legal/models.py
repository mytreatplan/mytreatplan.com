"""Legal documents and the choices made on them (CAP-4, CAP-13).

A `LegalDocument` is one version of one kind of document (Terms, Conditions, the Ormco
TPS contract, commercial consent), in English and Spanish, with a Markdown body
(legal.rendering). The current version of a kind is the latest whose `published_at` has
passed: publishing a new version in the admin applies at once, with no deploy. A
published version is never changed; a change is a new version (legal.admin).

A `LegalChoice` is the evidence of one decision: which version was shown, whether it was
accepted, and when. It belongs to a pending registration while the signup is in progress
(gone with it), then to the user (kept: a user with choices cannot be deleted).
"""
from django.conf import settings
from django.db import models, transaction
from django.db.models import Max, Q
from django.utils import timezone, translation


class LegalDocumentQuerySet(models.QuerySet):
    def published(self, now=None):
        """Versions whose `published_at` has passed."""
        return self.filter(published_at__lte=now or timezone.now())

    def current(self, kind, now=None):
        """The current version of `kind` (the latest published), or None."""
        return self.published(now).filter(kind=kind).order_by('-version').first()

    def current_by_kind(self, now=None):
        """{kind: current version} for every kind that has one."""
        now = now or timezone.now()
        documents = {}
        for document in self.published(now).order_by('kind', '-version'):
            documents.setdefault(document.kind, document)
        return documents


class LegalDocument(models.Model):
    class Kind(models.TextChoices):
        TERMS = 'terms', 'Terms'
        CONDITIONS = 'conditions', 'Conditions'
        ORMCO_TPS = 'ormco_tps', 'Ormco TPS contract'
        COMMERCIAL_CONSENT = 'commercial_consent', 'Commercial consent'

    # Kinds that must be accepted to sign up; the others are optional.
    REQUIRED_KINDS = (Kind.TERMS, Kind.CONDITIONS)

    kind = models.CharField(max_length=30, choices=Kind.choices)
    # Set on the first save: the previous version of this kind + 1.
    version = models.PositiveIntegerField(editable=False)
    title_en = models.CharField('title (English)', max_length=200)
    title_es = models.CharField('title (Spanish)', max_length=200)
    body_en = models.TextField('body (English)', help_text='Markdown.')
    body_es = models.TextField('body (Spanish)', help_text='Markdown.')
    # Empty or in the future: a draft, not shown. Once passed, the version is current
    # (until a later one is published) and can no longer be changed.
    published_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When this version applies. Leave empty for a draft. Once it has '
                  'passed, this version can no longer be changed or deleted.')
    created_at = models.DateTimeField(auto_now_add=True)

    objects = LegalDocumentQuerySet.as_manager()

    class Meta:
        ordering = ['kind', '-version']
        constraints = [
            models.UniqueConstraint(fields=['kind', 'version'], name='legal_document_kind_version'),
        ]

    def __str__(self):
        return f'{self.get_kind_display()} v{self.version}'

    def is_published(self, now=None):
        return self.published_at is not None and self.published_at <= (now or timezone.now())

    def _spanish(self):
        return (translation.get_language() or '').startswith('es')

    @property
    def title(self):
        """The title in the current language."""
        return self.title_es if self._spanish() else self.title_en

    @property
    def body(self):
        """The Markdown body in the current language."""
        return self.body_es if self._spanish() else self.body_en

    def save(self, *args, **kwargs):
        if self.version is None:
            with transaction.atomic():
                last = (LegalDocument.objects.filter(kind=self.kind)
                        .aggregate(last=Max('version'))['last'])
                self.version = (last or 0) + 1
                super().save(*args, **kwargs)
            return
        super().save(*args, **kwargs)


class LegalChoiceQuerySet(models.QuerySet):
    def copy_to(self, **owner):
        """Copy these choices (same version, decision and time) to `owner`
        (pending_registration=... or user=...)."""
        LegalChoice.objects.bulk_create([
            LegalChoice(document_id=choice.document_id, accepted=choice.accepted,
                        decided_at=choice.decided_at, **owner)
            for choice in self])


class LegalChoice(models.Model):
    """One decision on one document version: accepted or not, and when. Owned by a
    pending registration (signup in progress) or by a user, never both."""

    document = models.ForeignKey(LegalDocument, on_delete=models.PROTECT, related_name='choices')
    accepted = models.BooleanField()
    decided_at = models.DateTimeField()
    pending_registration = models.ForeignKey(
        'signup.PendingRegistration', null=True, blank=True, on_delete=models.CASCADE,
        related_name='legal_choices')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.PROTECT, related_name='legal_choices')

    objects = LegalChoiceQuerySet.as_manager()

    class Meta:
        ordering = ['decided_at', 'id']
        constraints = [
            models.CheckConstraint(
                condition=(Q(pending_registration__isnull=False, user__isnull=True)
                           | Q(pending_registration__isnull=True, user__isnull=False)),
                name='legal_choice_one_owner'),
        ]

    def __str__(self):
        return f'{self.document}: {"accepted" if self.accepted else "not accepted"}'
