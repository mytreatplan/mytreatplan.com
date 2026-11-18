"""The regional reveal seam for a doctor's maker login (epic-doctor-profile entry 8): the
functions epic-clinical-ops serves over remote reads (4.5, `integration.remote.remote_read`),
shaped like `integration/sample.py`. 6.8 builds only these; there is no mytp-core
resource, route or admin screen yet (epic-clinical-ops adds them with a core release, and
the resource's rule: Sysadmin and Clinical Manager for doctors in their regions, a
Clinical User only for doctors with a case assigned to them).

The object id is the login's UUID. The framework logs who, when, the resource, the object
and the field names (never values) in `RemoteAccess` before `build` runs, and it is the
only place a username or a password leaves the database. Nothing here logs a value.
"""
from django.core.exceptions import ValidationError

from .models import MakerLogin

RESOURCE = 'maker_login'
# The fields a reveal may ask for.
FIELDS = ('maker', 'other_name', 'username', 'password')


def load(object_id):
    """The login with this UUID in this region, or None (an unknown or another region's
    id: the framework answers 404)."""
    if object_id is None:
        return None
    try:
        return MakerLogin.objects.filter(pk=object_id).first()
    except (ValueError, TypeError, ValidationError):
        return None


def assignee(record):
    """Who has a case of this doctor assigned: nobody until epic-clinical-ops supplies the
    assignments, so a Clinical User is refused."""
    return None


def version(record):
    """The login's version: when it was saved, in microseconds (a replace changes it)."""
    return int(record.saved_at.timestamp() * 1_000_000)


def build(record, fields):
    """The requested `fields` of the login, decrypted (only those: ValueError for a name
    that is not in FIELDS)."""
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise ValueError(f'Not a maker login field: {", ".join(sorted(unknown))}')
    return {name: getattr(record, name) for name in fields}
