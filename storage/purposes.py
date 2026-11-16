"""Upload purposes: what a stored file is for, and who may upload and download it.

Each feature that stores files registers its purpose once (in its AppConfig.ready):

    storage.purposes.register('order_scan', can_upload, can_download, max_size=...,
                              content_types=..., validate=..., on_clean=...)

- `can_upload(user)`: may this (logged-in, active) user start an upload of this purpose?
  It is asked again for every signed request of the upload.
- `can_download(user, obj)`: may this user download this StoredObject?
- `max_size`: the largest file in bytes (default and at most 1 GiB: clamd's scan limits,
  deploy/clamav/clamd.conf, are set for it), checked before the upload starts and again
  when it completes.
- `content_types` (optional): the declared types (the browser's `contentType`, without
  parameters) an upload may start with; any other is refused at sign (400). None (the
  default): any type, as before.
- `validate(obj, read)` (optional): asked by the scan task (storage.tasks) after a clean
  scan and before the promotion; `read()` returns the object's bytes. False (or any
  exception but an S3 one, which is retried) rejects the object like the scan rejects a
  file (outcome `invalid_content`: deleted from the bucket, row `rejected`, the system's
  DeletionTrace).
- `on_clean(obj)` (optional): called once the object was promoted to `clean`, after that
  commits (the purpose links it to its owner's record, for instance). Its failure is
  logged by the object's UUID and leaves the object clean.
- `unclaimed_after` (optional, a timedelta): for a purpose whose objects get an `owner`
  once the feature claims them, the hourly sweep (storage.sweep) deletes, through
  storage.service.delete by the system, each object of the purpose still without an
  owner and not being deleted that long after it was recorded (an upload whose claim
  never came). `in_use(obj)` (optional) keeps one the feature still links all the same.

Only scanned objects (state `clean`, epic-platform entry 12) are ever downloadable, for
every purpose: never one in quarantine or rejected.

A purpose's name is stored on each StoredObject: never rename one that has objects.
"""
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Optional

GiB = 1024 ** 3
DEFAULT_MAX_SIZE = GiB
# The largest file clamd fully scans (StreamMaxLength, MaxFileSize, MaxScanSize in
# deploy/clamav/clamd.conf are set just above it): a larger limit would only store files
# the scan rejects.
MAX_SCANNED_SIZE = GiB
NAME_RE = re.compile(r'^[a-z][a-z0-9_]{0,39}$')


@dataclass(frozen=True)
class Purpose:
    name: str
    can_upload: Callable
    can_download: Callable
    max_size: int = DEFAULT_MAX_SIZE
    content_types: Optional[frozenset] = None
    validate: Optional[Callable] = None
    on_clean: Optional[Callable] = None
    unclaimed_after: Optional[timedelta] = None
    in_use: Optional[Callable] = None

    def allows_type(self, content_type):
        """Whether an upload declaring `content_type` may start (its parameters, such as
        `; charset=...`, are ignored; the comparison ignores case)."""
        if self.content_types is None:
            return True
        return (content_type or '').split(';', 1)[0].strip().lower() in self.content_types


_registry = {}


def register(name, can_upload, can_download, *, max_size=DEFAULT_MAX_SIZE,
             content_types=None, validate=None, on_clean=None, unclaimed_after=None,
             in_use=None):
    """Register a purpose; a second registration of the same name is an error."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError('a purpose name is 1-40 lowercase letters, digits or "_"')
    if name in _registry:
        raise ValueError(f'purpose {name!r} is already registered')
    if not callable(can_upload) or not callable(can_download):
        raise TypeError('can_upload and can_download must be callables')
    if (isinstance(max_size, bool) or not isinstance(max_size, int)
            or not 1 <= max_size <= MAX_SCANNED_SIZE):
        raise ValueError(f'max_size must be a number of bytes from 1 to {MAX_SCANNED_SIZE}')
    if content_types is not None:
        if isinstance(content_types, str) or not content_types or not all(
                isinstance(t, str) and t and t == t.strip().lower() for t in content_types):
            raise ValueError('content_types must be a non-empty collection of lowercase types')
        content_types = frozenset(content_types)
    if any(hook is not None and not callable(hook) for hook in (validate, on_clean, in_use)):
        raise TypeError('validate, on_clean and in_use must be callables')
    if unclaimed_after is not None and (not isinstance(unclaimed_after, timedelta)
                                        or unclaimed_after <= timedelta(0)):
        raise ValueError('unclaimed_after must be a positive timedelta')
    purpose = Purpose(name, can_upload, can_download, max_size, content_types, validate,
                      on_clean, unclaimed_after, in_use)
    _registry[name] = purpose
    return purpose


def unregister(name):
    """Remove a purpose (tests)."""
    _registry.pop(name, None)


def get(name):
    """The registered purpose called `name`, or None."""
    return _registry.get(name) if isinstance(name, str) else None


def names():
    return sorted(_registry)


def _active(user):
    return bool(user is not None and user.is_authenticated and user.is_active)


def may_upload(purpose, user):
    """Whether `user` (logged in and active) may upload for `purpose`."""
    if purpose is None or not _active(user):
        return False
    return bool(purpose.can_upload(user))


def may_download(purpose, user, obj):
    """Whether `user` may download `obj`: only a clean (scanned) object, and only when
    the purpose's hook allows it."""
    if purpose is None or not _active(user):
        return False
    if obj.state != obj.CLEAN:
        return False
    return bool(purpose.can_download(user, obj))
