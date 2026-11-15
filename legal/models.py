"""Legal documents and the choices made on them (CAP-4, CAP-13).

A `LegalDocument` is one version of one kind of document (Terms, Conditions, the Ormco
TPS contract, commercial consent; the WhatsApp contact consent, epic-doctor-profile
entry 2, offered on the personal-data page and not at signup), in English and Spanish, with a Markdown body
(legal.rendering). The current version of a kind is the latest whose `published_at` has
passed: publishing a new version in the admin applies at once, with no deploy. A
published version is never changed; a change is a new version (legal.admin).

Each document belongs to a region (epic-regions 2.6, R5): every instance reads, numbers
and offers only the documents of its own region (settings.REGION_PROFILE), so a site
never shows another region's text. A document is `provisional` until the lawyer's final
text replaces it; the signup legal step says so beside it.

A `LegalChoice` is the evidence of one decision: which version was shown, whether it was
accepted, and when. It belongs to a pending registration while the signup is in progress
(gone with it), then to the user (kept: a user with choices cannot be deleted).
"""
import uuid

from django.conf import settings
from django.db import models, transaction
from django.db.models import Max, Q
from django.db.models.functions import UUID4
from django.utils import timezone, translation

from regions.profiles import REGION_CHOICES


# The region of a row the previous release inserted (no `region` in its model): the
# database default. A database belongs to one site, so such a row is the site's own.
UNSET_REGION = ''


def site_region():
    """The region of this instance (settings.REGION_PROFILE): a new document's `region`."""
    return settings.REGION_PROFILE.code


def site_regions():
    """The `region` values that are this instance's documents: its region, and unset."""
    return (site_region(), UNSET_REGION)


class LegalDocumentQuerySet(models.QuerySet):
    def of_site(self):
        """The documents of this instance's region, with the rows whose region is unset
        (the previous release's: see LegalDocument.region)."""
        return self.filter(region__in=site_regions())

    def published(self, now=None):
        """Versions whose `published_at` has passed."""
        return self.filter(published_at__lte=now or timezone.now())

    def current(self, kind, now=None):
        """The current version of `kind` in this instance's region (the latest published),
        or None."""
        return self.of_site().published(now).filter(kind=kind).order_by('-version').first()

    def current_by_kind(self, now=None):
        """{kind: current version} for every kind that has one in this instance's region."""
        now = now or timezone.now()
        documents = {}
        for document in self.of_site().published(now).order_by('kind', '-version'):
            documents.setdefault(document.kind, document)
        return documents

    def current_for_signup(self, now=None):
        """`current_by_kind` limited to the kinds the signup legal step offers
        (LegalDocument.SIGNUP_KINDS)."""
        return {kind: document for kind, document in self.current_by_kind(now).items()
                if kind in LegalDocument.SIGNUP_KINDS}


class LegalDocument(models.Model):
    class Kind(models.TextChoices):
        TERMS = 'terms', 'Terms'
        CONDITIONS = 'conditions', 'Conditions'
        ORMCO_TPS = 'ormco_tps', 'Ormco TPS contract'
        COMMERCIAL_CONSENT = 'commercial_consent', 'Commercial consent'
        # Epic-doctor-profile entry 2: chosen on the personal-data page only.
        WHATSAPP_CONTACT = 'whatsapp_contact', 'WhatsApp contact consent'

    # Kinds that must be accepted to sign up; the others are optional.
    REQUIRED_KINDS = (Kind.TERMS, Kind.CONDITIONS)
    # Kinds the signup legal step offers (in this order). The WhatsApp consent is not one:
    # it is given on the personal-data page (accounts.profile).
    SIGNUP_KINDS = (Kind.TERMS, Kind.CONDITIONS, Kind.ORMCO_TPS, Kind.COMMERCIAL_CONSENT)
    # Optional consents a doctor ticks or unticks on the personal-data page.
    PROFILE_CONSENT_KINDS = (Kind.WHATSAPP_CONTACT, Kind.COMMERCIAL_CONSENT)
    CROSSES_SITES = True

    # AD-5: the id other sites know this record by (the integer key never leaves the
    # instance). Set once, never changed, never shown in a form. The database default
    # gives rows inserted without it (the previous release during a deploy) their own.
    uid = models.UUIDField(default=uuid.uuid4, db_default=UUID4(), unique=True,
                           editable=False)
    # R5 (epic-regions 2.6): the region whose instance offers this document, set once at
    # creation and never shown in a form. Its database default is region-neutral ('', so
    # the migrations are the same on every instance): rows inserted without it (the
    # previous release during a deploy) get '', which legal 0007 fills with the instance
    # region. A row inserted after that fill keeps '' and is read as the site's own (a
    # database belongs to one site): offered, listed and counted in the numbering like
    # the others, and given the site's region when saved through the model.
    region = models.CharField(max_length=2, choices=REGION_CHOICES, default=site_region,
                              db_default=UNSET_REGION, editable=False)
    kind = models.CharField(max_length=30, choices=Kind.choices)
    # Set on the first save: the previous version of this region and kind + 1.
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
    # Not the lawyer's final text yet: the signup legal step shows a notice beside it.
    # Every document from before epic-regions 2.6 is provisional.
    provisional = models.BooleanField(
        default=True, db_default=True,
        help_text="Tick while this is not the lawyer's final text: signup shows a "
                  '"provisional text" notice beside it.')
    created_at = models.DateTimeField(auto_now_add=True)

    objects = LegalDocumentQuerySet.as_manager()

    class Meta:
        ordering = ['kind', '-version']
        constraints = [
            models.UniqueConstraint(fields=['region', 'kind', 'version'],
                                    name='legal_document_region_kind_version'),
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
        if not self.region:
            self.region = site_region()  # a new row, or one the previous release inserted
        if self.version is None:
            with transaction.atomic():
                # The unset rows are the site's too (LegalDocument.region).
                regions = ((self.region, UNSET_REGION) if self.region == site_region()
                           else (self.region,))
                last = (LegalDocument.objects.filter(region__in=regions, kind=self.kind)
                        .aggregate(last=Max('version'))['last'])
                self.version = (last or 0) + 1
                super().save(*args, **kwargs)
            return
        super().save(*args, **kwargs)


class LegalChoice(models.Model):
    """One decision on one document version: accepted or not, and when. Owned by a
    pending registration (signup in progress) or by a user, never both."""

    CROSSES_SITES = True

    # AD-5: the id other sites know this record by (the integer key never leaves the
    # instance). Set once, never changed, never shown in a form. The database default
    # gives rows inserted without it (the previous release during a deploy) their own.
    uid = models.UUIDField(default=uuid.uuid4, db_default=UUID4(), unique=True,
                           editable=False)
    document = models.ForeignKey(LegalDocument, on_delete=models.PROTECT, related_name='choices')
    accepted = models.BooleanField()
    decided_at = models.DateTimeField()
    pending_registration = models.ForeignKey(
        'signup.PendingRegistration', null=True, blank=True, on_delete=models.CASCADE,
        related_name='legal_choices')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.PROTECT, related_name='legal_choices')

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
