"""The doctor's maker logins (epic-doctor-profile entry 8, AD-2): the username (or e-mail)
and the password a doctor gives for an aligner maker's platform, so a specialist can work
there for them. `accounts.models.MakerLogin` holds them, field-encrypted and regional;
this module is the only writer, and what epic-ordering reads.

Every write is atomic and takes the user's row lock first (`select_for_update`):

- `save(user, maker, data, *, other_name=None)`: creates the login, or overwrites the
  existing one (both fields are given again; the old values are replaced and `saved_at`
  is updated). A named maker has one login; an `other` login is found by its name;
- `add_other(user, name, data)`: a new `other` login; a name already used (any case) or a
  sixth login is refused;
- `remove(user, login_id)`: deletes the login (MakerLogin.DoesNotExist when it is not the
  user's);
- `erase(user)`: the account erasure deletes all of them.

`data` is {'username': ..., 'password': ...}. Rules (`problems`): both are required; the
username is stripped, the password is stored as typed; lengths as the model's; the other
name is required (at most 60 characters), unique per doctor ignoring case, at most
MAX_OTHER others per doctor.

What epic-ordering reads: `has_login(user, maker)` and `saved_makers(user)` (the five
named makers only: "other" and "not decided yet" never block ordering). What the settings
page reads: `logins(user)`, metadata only. The values are read only by
`accounts.makers_remote`, the regional reveal seam of epic-clinical-ops.

Nothing here logs a value, and nothing emits `client.updated`. Only ids appear in
exceptions and logs.
"""
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Value
from django.db.models.functions import Lower
from django.utils import timezone

from . import client_sync
from .models import MakerLogin

# The named makers, in the page's order, with their (untranslated) brand names.
MAKER_NAMES = {
    MakerLogin.Maker.INVISALIGN.value: 'Invisalign',
    MakerLogin.Maker.SPARK.value: 'Ormco Spark',
    MakerLogin.Maker.ANGEL.value: 'Angel Aligner',
    MakerLogin.Maker.SOLVENTUM.value: 'Solventum',
    MakerLogin.Maker.SMARTEE.value: 'Smartee',
}
NAMED = tuple(MAKER_NAMES)
OTHER = MakerLogin.Maker.OTHER.value
MAX_OTHER = 5
ALL = '__all__'
# The problem codes `problems` and the writers return, by field ('__all__' for none):
# required, too_long, duplicate (other_name), limit.


class MakerInvalid(Exception):
    """A write was refused; `problems` is {field: code}."""

    def __init__(self, problems):
        super().__init__(', '.join(f'{field}: {code}' for field, code in problems.items()))
        self.problems = problems


def _max_length(name):
    return MakerLogin._meta.get_field(name).max_length


def clean_data(data):
    """`data` ({'username', 'password'}; ValueError for anything else) with the username
    stripped; the password is kept as typed."""
    unknown = set(data) - {'username', 'password'}
    if unknown:
        raise ValueError(f'Not a maker login field: {", ".join(sorted(unknown))}')
    return {'username': (data.get('username') or '').strip(),
            'password': data.get('password') or ''}


def problems(data, other_name=None):
    """{field: code} of what is wrong with `data` (cleaned) and, for an `other` login, its
    `other_name` (None for a named maker); empty when it can be saved."""
    found = {}
    for name in ('username', 'password'):
        if not data.get(name):
            found[name] = 'required'
        elif len(data[name]) > _max_length(name):
            found[name] = 'too_long'
    if other_name is not None:
        if not other_name:
            found['other_name'] = 'required'
        elif len(other_name) > MakerLogin.OTHER_NAME_MAX_LENGTH:
            found['other_name'] = 'too_long'
    return found


def _check_user(user):
    if not client_sync.is_client(user) or user.is_erased:
        raise ValueError('Maker logins belong to doctors (client accounts) only.')


def _lock(user):
    return get_user_model().objects.select_for_update().get(pk=user.pk)


def _others(user):
    return MakerLogin.objects.filter(user_id=user.pk, maker=OTHER)


def _write(user, maker, data, other_name, *, must_be_new):
    _check_user(user)
    if maker not in NAMED and maker != OTHER:
        raise ValueError(f'Unknown maker: {maker}')
    if maker != OTHER and other_name:
        raise ValueError('Only an "other" login has a maker name.')
    name = (other_name or '').strip() if maker == OTHER else None
    values = clean_data(data)
    found = problems(values, name)
    if found:
        raise MakerInvalid(found)
    with transaction.atomic():
        locked = _lock(user)
        # An erasure may have won the lock since the check above.
        _check_user(locked)
        rows = MakerLogin.objects.select_for_update().filter(user=locked, maker=maker)
        if maker == OTHER:
            # The database rule is on Lower(other_name); look it up with the same
            # expression (iexact compiles to UPPER on PostgreSQL).
            rows = rows.annotate(_lowered=Lower('other_name')).filter(
                _lowered=Lower(Value(name)))
        row = rows.first()
        if row is not None and must_be_new:
            raise MakerInvalid({'other_name': 'duplicate'})
        if row is None:
            if maker == OTHER and _others(locked).count() >= MAX_OTHER:
                raise MakerInvalid({ALL: 'limit'})
            row = MakerLogin(user=locked, maker=maker, other_name=name or '')
        row.username = values['username']
        row.password = values['password']
        row.saved_at = timezone.now()
        row.save()
        return row.pk


def save(user, maker, data, *, other_name=None):
    """Save the login of `user` for `maker` (a named maker or `other`, with its
    `other_name`) from `data`: created, or overwritten when there is one already (the old
    username and password are gone; `saved_at` is now). Returns the login's id. Raises
    MakerInvalid for a problem (or the sixth other), ValueError for an unknown maker or
    field, a maker name on a named maker, or a non-doctor."""
    return _write(user, maker, data, other_name, must_be_new=False)


def add_other(user, name, data):
    """A new `other` login named `name`: MakerInvalid (`other_name`: `duplicate`) when the
    doctor has one with that name already, ignoring case. Otherwise as `save`."""
    return _write(user, OTHER, data, name, must_be_new=True)


def remove(user, login_id):
    """Delete the login `login_id` of `user`. MakerLogin.DoesNotExist when it is not the
    user's."""
    _check_user(user)
    with transaction.atomic():
        locked = _lock(user)
        _check_user(locked)
        MakerLogin.objects.select_for_update().get(pk=login_id, user=locked).delete()


def erase(user):
    """Erasure: delete all of the account's maker logins."""
    MakerLogin.objects.filter(user_id=user.pk).delete()


# --- What the pages and the order flows read: metadata only ------------------------------------

_META = ('id', 'maker', 'other_name', 'saved_at')


def logins(user):
    """The doctor's logins, metadata only ({'id', 'maker', 'other_name', 'saved_at'}; never
    a username or a password): the named makers in their order, then the others by name."""
    rows = list(MakerLogin.objects.filter(user_id=user.pk).values(*_META))
    order = {code: index for index, code in enumerate(NAMED)}
    return sorted(rows, key=lambda row: (order.get(row['maker'], len(order)),
                                         row['other_name'].casefold()))


def login_meta(user, login_id):
    """The metadata of the user's login `login_id`, or None."""
    return (MakerLogin.objects.filter(user_id=user.pk, pk=login_id).values(*_META)
            .first())


def has_login(user, maker):
    """Whether `user` has a saved login for the named `maker` (epic-ordering's check)."""
    return maker in NAMED and MakerLogin.objects.filter(user_id=user.pk,
                                                         maker=maker).exists()


def saved_makers(user):
    """The named makers `user` has a login for, in the page's order (for the wizard)."""
    saved = set(MakerLogin.objects.filter(user_id=user.pk, maker__in=NAMED)
                .values_list('maker', flat=True))
    return [code for code in NAMED if code in saved]
