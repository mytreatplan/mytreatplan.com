"""The doctor's profile photo (epic-doctor-profile entry 4, user decisions 2026-11-16).

The storage purpose `profile_photo` (storage.purposes, registered in AccountsConfig.ready):
uploads by a signed-in doctor (a client account: not staff, not erased), at most
MAX_SIZE (5 MiB), declared JPEG, PNG or WebP only (refused at sign otherwise); downloads
by the object's owner only (no staff view yet: later stories add one through the logged
download path, AD-9). After a clean virus scan the scan task runs `check_image`: Pillow
opens and verifies the file, which must really be a JPEG, PNG or WebP of the declared
type, at most MAX_SIDE x MAX_SIDE pixels, with Pillow's decompression-bomb guard on;
anything else is rejected as the scan rejects a file. No crop or resize: the page shows
it inside the round frame (`object-cover`).

One photo per doctor, on accounts.models.Profile: `photo` (shown) and `photo_pending`
(the newest upload, waiting for its scan). The flow:

- the widget uploads through /storage/sign/ and /storage/complete/, then posts the new
  object's id to the settings page `settings_photo` (`attach`): only an object of this
  purpose uploaded by the doctor, without an owner and not being deleted (404
  otherwise). It becomes the doctor's (`owner`) and their pending photo; an earlier
  pending upload is deleted (with its trace, the doctor as actor). If the scan already
  rejected it (a tiny file scanned before the attach came), it is deleted instead and
  the page shows the reject message; if the scan already passed it, it is the photo at
  once;
- an upload never attached (the tab closed after it) is deleted by the hourly sweep
  UNCLAIMED_AFTER later, by the system (the purpose's `unclaimed_after`);
- once its scan passes, `on_clean` (the purpose's hook) promotes it: it becomes the photo
  and the previous photo is deleted (actor: the uploader). An object already clean when
  it is attached is promoted at once. Until then the old photo (or the initials) stays,
  and "My details" says the photo is being checked;
- a rejected upload leaves the old photo (or the initials); "My details" shows the
  reject message once (`settle`), without a reason, and unlinks it;
- `remove` (the confirmed Delete) unlinks the photo and any pending upload at once and
  deletes both; `erase` does the same for an erased account (accounts.erasure, actor: the
  staff member erasing it), with the account's unattached uploads of this purpose.

Every deletion goes through storage.service.delete (a DeletionTrace with a blank
`patient_code`). The photo stays regional: it is not in `client.updated` and no photo
change emits an event. The doctor's own pages show it through one presigned inline link
per request (`avatar`, storage.service.owner_inline_url), with no access record: the
AD-9 exception for one's own photo. Nothing here logs a file name, a key or a URL.
"""
import io
import logging
import warnings
from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction

from django.db.models import Q

from storage import purposes, s3, service
from storage.models import StoredObject

from . import client_sync
from .models import Profile

logger = logging.getLogger(__name__)

PURPOSE = 'profile_photo'
MiB = 1024 * 1024
MAX_SIZE = 5 * MiB
MAX_SIDE = 6000
# Pillow's format name -> the declared type it must have been uploaded as.
FORMATS = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp'}
CONTENT_TYPES = tuple(FORMATS.values())
# An upload not attached this long after it was recorded is deleted by the sweep.
UNCLAIMED_AFTER = timedelta(hours=6)

# What `attach` did.
PENDING = 'pending'
PROMOTED = 'promoted'
REJECTED = 'rejected'


class NotAttachable(Exception):
    """The object may not become this doctor's photo (the view answers 404)."""


# --- The purpose ----------------------------------------------------------------------

def can_upload(user):
    """A signed-in doctor: a client account (not staff), not erased."""
    return client_sync.is_client(user) and not user.is_erased


def storage_available():
    """Whether this site has its bucket settings (dev-ae has none yet): without them
    nothing can be uploaded, shown or deleted, so the photo pages are not offered."""
    return s3.missing_setting() is None


def in_use(obj):
    """Whether a profile links `obj` (the sweep keeps it even without an owner)."""
    return Profile.objects.filter(Q(photo=obj) | Q(photo_pending=obj)).exists()


def can_download(user, obj):
    """The owner only."""
    return obj.owner_id is not None and obj.owner_id == user.pk


def check_image(obj, read):
    """Whether the clean-scanned object is a real JPEG, PNG or WebP of its declared type,
    at most MAX_SIDE pixels a side, that Pillow can fully decode (the decompression-bomb
    guard stays on: its warning counts as a failure too)."""
    from PIL import Image

    data = read()
    if not data or len(data) > MAX_SIZE:
        return False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=list(FORMATS)) as image:
                kind = image.format
                width, height = image.size
                if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE):
                    return False
                image.verify()
            # verify() leaves the image unusable and does not decode every format's
            # pixels: open it again and decode it whole (a truncated file fails here).
            with Image.open(io.BytesIO(data), formats=list(FORMATS)) as image:
                image.load()
    except Exception:  # noqa: BLE001 - whatever Pillow refuses is not an accepted photo
        return False
    declared = (obj.content_type or '').split(';', 1)[0].strip().lower()
    return FORMATS.get(kind) == declared


def on_clean(obj):
    """The purpose's hook after the scan promoted `obj`: if it is still its owner's
    pending photo, it becomes the photo."""
    user_id = (Profile.objects.filter(photo_pending=obj).values_list('user_id', flat=True)
               .first())
    if user_id is None:
        return
    with transaction.atomic():
        get_user_model().objects.select_for_update().filter(pk=user_id).first()
        profile = Profile.objects.select_for_update().filter(user_id=user_id).first()
        if profile is None or profile.photo_pending_id != obj.pk:
            return
        fresh = StoredObject.objects.filter(pk=obj.pk).first()
        if fresh is None or fresh.state != StoredObject.CLEAN or fresh.delete_requested_at:
            return
        _promote(profile, fresh)


def register():
    purposes.register(PURPOSE, can_upload, can_download, max_size=MAX_SIZE,
                      content_types=CONTENT_TYPES, validate=check_image, on_clean=on_clean,
                      unclaimed_after=UNCLAIMED_AFTER, in_use=in_use)


# --- Changes --------------------------------------------------------------------------

def _promote(profile, obj):
    """Under the profile's lock: `obj` (clean) becomes the photo; the previous one is
    deleted, by the uploader."""
    old = profile.photo
    profile.photo = obj
    profile.photo_pending = None
    profile.save(update_fields=['photo', 'photo_pending'])
    if old is not None and old.pk != obj.pk:
        service.delete(old, obj.uploaded_by)


def _locked_profile(user, *, create=False):
    get_user_model().objects.select_for_update().filter(pk=user.pk).first()
    profile = Profile.objects.select_for_update().filter(user_id=user.pk).first()
    if profile is None and create:
        profile = Profile.objects.create(user_id=user.pk)
    return profile


def attach(user, object_id):
    """Make the uploaded object `object_id` the doctor's pending photo (see the module
    docstring). Returns PENDING, PROMOTED (already clean: it is the photo now) or
    REJECTED (the scan already rejected it: it is deleted, nothing is linked). Raises
    NotAttachable for anything that is not the doctor's own unclaimed upload of this
    purpose."""
    if not can_upload(user):
        raise NotAttachable
    with transaction.atomic():
        get_user_model().objects.select_for_update().filter(pk=user.pk).first()
        obj = (StoredObject.objects.select_for_update()
               .filter(pk=object_id, purpose=PURPOSE, uploaded_by_id=user.pk,
                       owner__isnull=True, delete_requested_at__isnull=True).first())
        if obj is None:
            raise NotAttachable
        if obj.state == StoredObject.REJECTED:
            # As `settle` does for a rejected pending upload: the row goes (the scan's
            # system trace stays: the deletion's trace is get_or_create).
            service.delete(obj, user)
            return REJECTED
        obj.owner_id = user.pk
        obj.save(update_fields=['owner'])
        profile = _locked_profile(user, create=True)
        earlier = profile.photo_pending
        profile.photo_pending = obj
        profile.save(update_fields=['photo_pending'])
        if earlier is not None and earlier.pk != obj.pk:
            service.delete(earlier, user)
        if obj.state == StoredObject.CLEAN:
            _promote(profile, obj)
            return PROMOTED
        return PENDING


def _unlink_all(user, actor):
    profile = _locked_profile(user)
    if profile is None:
        return []
    objects = [obj for obj in (profile.photo, profile.photo_pending) if obj is not None]
    if objects:
        profile.photo = profile.photo_pending = None
        profile.save(update_fields=['photo', 'photo_pending'])
    for obj in objects:
        service.delete(obj, actor)
    return objects


def has_photo(user):
    """Whether the profile links a photo or a pending upload (the Delete page's test,
    independent of the presigned link)."""
    return Profile.objects.filter(user_id=user.pk).filter(
        Q(photo__isnull=False) | Q(photo_pending__isnull=False)).exists()


def remove(user):
    """The confirmed Delete: unlink the photo and any pending upload and delete both.
    Returns whether there was anything to delete."""
    with transaction.atomic():
        return bool(_unlink_all(user, user))


def erase(user, by):
    """Erasure (accounts.erasure.erase_user, in its transaction): delete the photo, the
    pending upload and the account's other uploads of this purpose, by `by`."""
    _unlink_all(user, by)
    for obj in StoredObject.objects.filter(purpose=PURPOSE, uploaded_by_id=user.pk,
                                           delete_requested_at__isnull=True):
        service.delete(obj, by)


def settle(user):
    """The "My details" page's look at the pending upload: one already clean (its hook
    did not run) is promoted now; a rejected one is unlinked and deleted, and True is
    returned once, for the reject message."""
    profile = Profile.objects.filter(user_id=user.pk).select_related('photo_pending').first()
    pending = profile.photo_pending if profile is not None else None
    if pending is None or pending.state == StoredObject.QUARANTINE:
        return False
    with transaction.atomic():
        profile = _locked_profile(user)
        if profile is None or profile.photo_pending_id != pending.pk:
            return False
        pending = StoredObject.objects.filter(pk=pending.pk).first()
        if pending is None:
            return False
        if pending.state == StoredObject.CLEAN and pending.delete_requested_at is None:
            _promote(profile, pending)
            return False
        # The reject message is shown on this first render only: the rejected row is
        # unlinked and deleted now (its keys are already gone; the scan's system
        # DeletionTrace stays, as the deletion's trace is get_or_create).
        profile.photo_pending = None
        profile.save(update_fields=['photo_pending'])
        service.delete(pending, user)
        return pending.state == StoredObject.REJECTED


# --- Showing it -----------------------------------------------------------------------

@dataclass(frozen=True)
class Avatar:
    """What the doctor's pages show: `url` (the photo's presigned inline link, '' for the
    initials) and `pending` (an upload is being checked)."""

    url: str = ''
    pending: bool = False


NO_AVATAR = Avatar()
_REQUEST_ATTRIBUTE = '_mytp_profile_photo'


def avatar_of(user):
    """The Avatar of `user` now: a link only for a clean photo they own; the initials on
    any failure (logged without the key or the name)."""
    if user is None or not user.is_authenticated or user.pk is None:
        return NO_AVATAR
    profile = (Profile.objects.filter(user_id=user.pk)
               .select_related('photo', 'photo_pending').first())
    if profile is None:
        return NO_AVATAR
    pending = (profile.photo_pending is not None
               and profile.photo_pending.state == StoredObject.QUARANTINE
               and profile.photo_pending.delete_requested_at is None)
    photo = profile.photo
    url = ''
    if (photo is not None and photo.state == StoredObject.CLEAN
            and photo.delete_requested_at is None and photo.owner_id == user.pk):
        try:
            url = service.owner_inline_url(photo, user)
        except Exception as exc:  # noqa: BLE001 - the page shows the initials instead
            logger.warning('accounts: profile photo link failed (%s)', type(exc).__name__)
            url = ''
    return Avatar(url=url, pending=pending)


def avatar(request):
    """The signed-in user's Avatar, worked out once per request (the header, the cards
    and the photo block share one presigned link)."""
    cached = getattr(request, _REQUEST_ATTRIBUTE, None)
    if cached is None:
        cached = avatar_of(getattr(request, 'user', None))
        setattr(request, _REQUEST_ATTRIBUTE, cached)
    return cached


def forget(request):
    """Drop the request's cached Avatar (after a change in the same request)."""
    if hasattr(request, _REQUEST_ATTRIBUTE):
        delattr(request, _REQUEST_ATTRIBUTE)
