"""Upload purposes: what a stored file is for, and who may upload and download it.

Each feature that stores files registers its purpose once (in its AppConfig.ready):

    storage.purposes.register('order_scan', can_upload, can_download, max_size=...)

- `can_upload(user)`: may this (logged-in, active) user start an upload of this purpose?
  It is asked again for every signed request of the upload.
- `can_download(user, obj)`: may this user download this StoredObject?
- `max_size`: the largest file in bytes (default 1 GiB), checked before the upload starts
  and again when it completes.
- `download_quarantine`: whether objects not yet scanned (state quarantine) may be
  downloaded. Only the staging sample purpose sets it (decision 2026-11-06); every real
  purpose downloads `clean/` objects only.

A purpose's name is stored on each StoredObject: never rename one that has objects.
"""
import re
from dataclasses import dataclass
from typing import Callable

GiB = 1024 ** 3
DEFAULT_MAX_SIZE = GiB
NAME_RE = re.compile(r'^[a-z][a-z0-9_]{0,39}$')


@dataclass(frozen=True)
class Purpose:
    name: str
    can_upload: Callable
    can_download: Callable
    max_size: int = DEFAULT_MAX_SIZE
    download_quarantine: bool = False


_registry = {}


def register(name, can_upload, can_download, *, max_size=DEFAULT_MAX_SIZE,
             download_quarantine=False):
    """Register a purpose; a second registration of the same name is an error."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError('a purpose name is 1-40 lowercase letters, digits or "_"')
    if name in _registry:
        raise ValueError(f'purpose {name!r} is already registered')
    if not callable(can_upload) or not callable(can_download):
        raise TypeError('can_upload and can_download must be callables')
    if isinstance(max_size, bool) or not isinstance(max_size, int) or max_size < 1:
        raise ValueError('max_size must be a positive number of bytes')
    purpose = Purpose(name, can_upload, can_download, max_size, download_quarantine)
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
    """Whether `user` may download `obj`: the purpose's hook, and only clean objects
    unless the purpose allows quarantine downloads."""
    if purpose is None or not _active(user):
        return False
    if obj.state != obj.CLEAN and not purpose.download_quarantine:
        return False
    return bool(purpose.can_download(user, obj))
