"""The doctor's fiscal entities (epic-doctor-profile entry 5, AD-2, AD-14, AD-15): who a
doctor bills as. `accounts.models.FiscalEntity` holds them; this module is the only
writer, and what epic-ordering and epic-payments read.

Every write is atomic and takes the user's row lock first (`select_for_update`, as
accounts.profile), so two tabs, or a checkout and the settings page, never interleave:

- `create(user, data)`: validates (`problems`), refuses the 11th active entity, saves; the
  first active entity becomes the default;
- `update(user, entity_id, data, *, version)`: refuses a stale `version` (StaleVersion),
  validates the merged data, saves in place and goes `version` up when something changed;
- `make_default(user, entity_id)`: exactly one default among the non-archived entities;
- `remove(user, entity_id)`: a hard delete while the entity is unused, else it is archived
  (hidden, not usable); a removed default passes to the most recently changed remaining
  entity;
- `mark_used(entity, at)`: for epic-payments, when an order uses the entity (idempotent);
- `erase(user)`: the account erasure deletes all of them.

Rules that depend on the billing country (`problems`): the name(s) (a person's first and
last name, or the company's legal name), the tax id and the country are always required;
the address line 1, city and postal code are required for every country except AE (the
UAE's invoices need only the ids). The VAT id exists only for AE (it is cleared for any
other country). The country must be in this site's region and not banned
(regions.countries.lookup_country), checked at every save, so an entity whose country
changed since (banned, or moved to the other region) cannot be saved again unless its
country is changed. The same check, now, decides which entities are `usable_entities`.
At most MAX_ENTITIES active (not archived) entities per doctor.

Nothing here logs a value, and nothing emits `client.updated` (entry 9 does, with the
core release). Only ids appear in exceptions.
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from django_countries import countries

from regions.countries import lookup_country

from . import client_sync
from .models import FiscalEntity, normalize_fiscal_id

MAX_ENTITIES = 10
# The billing country that has a VAT id of its own (the UAE uses two ids).
VAT_COUNTRY = 'AE'
# The fields a writer may set (everything else is the service's).
FIELDS = ('kind', 'first_name', 'last_name', 'legal_name', 'tax_id', 'vat_id',
          'address_line1', 'address_line2', 'city', 'postal_code', 'province', 'country',
          'billing_email')
_TEXT_FIELDS = tuple(name for name in FIELDS if name not in ('kind', 'country'))
# The problem codes `problems` returns, by field ('__all__' for none): required, invalid,
# too_long, country_banned, country_other_region, limit.
ALL = '__all__'


class FiscalInvalid(Exception):
    """A write was refused; `problems` is {field: code} (see `problems`)."""

    def __init__(self, problems):
        super().__init__(', '.join(f'{field}: {code}' for field, code in problems.items()))
        self.problems = problems


class StaleVersion(Exception):
    """The entity changed since the writer read it (its `version` moved on)."""


def is_vat_country(country):
    return (country or '').upper() == VAT_COUNTRY


def _max_length(name):
    return FiscalEntity._meta.get_field(name).max_length


def clean_data(data):
    """`data` ({field: value}, FIELDS only: ValueError for any other) normalised: text
    stripped, ids uppercased without spaces or dashes, the country uppercased, and only
    what belongs to the kind and the country kept (a person has no legal name, a company
    no first or last name, a country other than AE no VAT id)."""
    unknown = set(data) - set(FIELDS)
    if unknown:
        raise ValueError(f'Not a fiscal entity field: {", ".join(sorted(unknown))}')
    cleaned = {name: (value or '').strip() if isinstance(value, str) or value is None
               else value for name, value in data.items()}
    for name in ('tax_id', 'vat_id'):
        if name in cleaned:
            cleaned[name] = normalize_fiscal_id(cleaned[name])
    if 'country' in cleaned:
        cleaned['country'] = str(cleaned['country'] or '').strip().upper()
        if not is_vat_country(cleaned['country']) and 'vat_id' in cleaned:
            cleaned['vat_id'] = ''
    kind = cleaned.get('kind')
    if kind == FiscalEntity.PERSON and 'legal_name' in cleaned:
        cleaned['legal_name'] = ''
    if kind == FiscalEntity.COMPANY:
        for name in ('first_name', 'last_name'):
            if name in cleaned:
                cleaned[name] = ''
    return cleaned


def problems(data):
    """{field: code} of what is wrong with `data` (the full, cleaned set of FIELDS; see
    `clean_data`), empty when it can be saved. Codes: `required`, `invalid`, `too_long`,
    `country_banned` and `country_other_region` (on `country`)."""
    found = {}
    kind = data.get('kind')
    if kind not in (FiscalEntity.PERSON, FiscalEntity.COMPANY):
        found['kind'] = 'invalid'
    names = {FiscalEntity.PERSON: ('first_name', 'last_name'),
             FiscalEntity.COMPANY: ('legal_name',)}.get(kind, ())
    country = data.get('country') or ''
    address = () if is_vat_country(country) else ('address_line1', 'city', 'postal_code')
    for name in (*names, 'tax_id', *address):
        if not data.get(name):
            found[name] = 'required'
    for name in _TEXT_FIELDS:
        if name not in found and len(data.get(name) or '') > _max_length(name):
            found[name] = 'too_long'
    if data.get('billing_email') and 'billing_email' not in found:
        try:
            validate_email(data['billing_email'])
        except ValidationError:
            found['billing_email'] = 'invalid'
    if not country:
        found['country'] = 'required'
    elif country not in countries:
        found['country'] = 'invalid'
    else:
        answer = lookup_country(country)
        if answer.banned:
            found['country'] = 'country_banned'
        elif answer.other_region:
            found['country'] = 'country_other_region'
    return found


def _check_user(user):
    if not client_sync.is_client(user) or user.is_erased:
        raise ValueError('Fiscal entities belong to doctors (client accounts) only.')


def _lock(user):
    return get_user_model().objects.select_for_update().get(pk=user.pk)


def active(user):
    """The doctor's non-archived entities, the default first, then oldest first."""
    return (FiscalEntity.objects.filter(user_id=user.pk, archived_at__isnull=True)
            .order_by('-is_default', 'created_at', 'pk'))


def _values(entity):
    return {name: getattr(entity, name) for name in FIELDS} | {
        'country': entity.country.code if entity.country else ''}


def create(user, data):
    """Save a new entity for `user` from `data` (all of FIELDS but the optional ones).
    Returns it. Raises FiscalInvalid for a problem, or when `user` already has MAX_ENTITIES
    active entities (`__all__`: `limit`); ValueError for an unknown field or a non-doctor."""
    _check_user(user)
    values = {name: '' for name in FIELDS} | clean_data(data)
    with transaction.atomic():
        locked = _lock(user)
        found = problems(values)
        existing = active(locked)
        if existing.count() >= MAX_ENTITIES:
            found[ALL] = 'limit'
        if found:
            raise FiscalInvalid(found)
        # The new entity (its country is allowed: `problems` passed) becomes the default
        # when there is none, or the current one can no longer be used.
        current = existing.filter(is_default=True).first()
        takes_default = current is None or not is_usable(current)
        if takes_default and current is not None:
            FiscalEntity.objects.filter(pk=current.pk).update(is_default=False)
        return FiscalEntity.objects.create(user=locked, is_default=takes_default, **values)


def update(user, entity_id, data, *, version):
    """Change the active entity `entity_id` of `user` with `data` (any of FIELDS; the rest
    stays), if its `version` is still `version` (else StaleVersion). Saved in place;
    `version` goes up when something changed. Raises FiscalInvalid, ValueError (as
    `create`) and FiscalEntity.DoesNotExist (not the user's, or archived)."""
    _check_user(user)
    changes = clean_data(data)
    with transaction.atomic():
        locked = _lock(user)
        entity = (FiscalEntity.objects.select_for_update()
                  .get(pk=entity_id, user=locked, archived_at__isnull=True))
        if entity.version != version:
            raise StaleVersion(str(entity.pk))
        values = clean_data(_values(entity) | changes)
        found = problems(values)
        if found:
            raise FiscalInvalid(found)
        before = _values(entity)
        changed = [name for name in FIELDS if values[name] != before[name]]
        for name in changed:
            setattr(entity, name, values[name])
        if changed:
            entity.version += 1
            entity.save()
        return entity


def make_default(user, entity_id):
    """Make the active entity `entity_id` the default (the others stop being it).
    FiscalEntity.DoesNotExist when it is not the user's or is archived; FiscalInvalid
    (`__all__`: `unusable`) when its country is no longer allowed. Does not touch `version`
    or `updated_at`."""
    _check_user(user)
    with transaction.atomic():
        locked = _lock(user)
        entity = FiscalEntity.objects.get(pk=entity_id, user=locked, archived_at__isnull=True)
        if not is_usable(entity):
            raise FiscalInvalid({ALL: 'unusable'})
        if not entity.is_default:
            active(locked).filter(is_default=True).update(is_default=False)
            FiscalEntity.objects.filter(pk=entity.pk).update(is_default=True)
            entity.is_default = True
        return entity


DELETED, ARCHIVED = 'deleted', 'archived'


def remove(user, entity_id, *, now=None):
    """Delete the active entity `entity_id`, or archive it when an order has used it.
    Returns DELETED or ARCHIVED. When it was the default, the most recently changed
    remaining active entity becomes the default. FiscalEntity.DoesNotExist as
    `make_default`."""
    _check_user(user)
    with transaction.atomic():
        locked = _lock(user)
        entity = FiscalEntity.objects.select_for_update().get(
            pk=entity_id, user=locked, archived_at__isnull=True)
        was_default = entity.is_default
        if entity.used_at is None:
            entity.delete()
            outcome = DELETED
        else:
            entity.is_default = False
            entity.archived_at = now or timezone.now()
            entity.save(update_fields=['is_default', 'archived_at'])
            outcome = ARCHIVED
        if was_default:
            # The most recently changed usable entity, else any active one.
            newest = ('-updated_at', '-created_at', '-pk')
            successor = (usable_entities(locked).order_by(*newest).first()
                         or active(locked).order_by(*newest).first())
            if successor is not None:
                FiscalEntity.objects.filter(pk=successor.pk).update(is_default=True)
        return outcome


def mark_used(entity, at=None):
    """Record that an order used `entity` (epic-payments): sets `used_at` to `at` (now by
    default) unless it is already set. Idempotent; returns whether it was set now."""
    with transaction.atomic():
        _lock(entity.user)
        current = FiscalEntity.objects.select_for_update().get(pk=entity.pk)
        if current.used_at is None:
            current.used_at = at or timezone.now()
            FiscalEntity.objects.filter(pk=current.pk).update(used_at=current.used_at)
            entity.used_at = current.used_at
            return True
        entity.used_at = current.used_at
        return False


def erase(user):
    """Erasure: delete all of the account's fiscal entities, archived ones included (the
    invoices' and the accounting's own snapshots are the legal record)."""
    FiscalEntity.objects.filter(user_id=user.pk).delete()


# --- What the order flows read ---------------------------------------------------------------

def country_allowed(code):
    """Whether this site may bill to `code` now: in its region and not banned."""
    return lookup_country(code).own_region


def is_usable(entity):
    """Whether `entity` can be used for a new order: not archived and its country allowed
    now."""
    return (entity.archived_at is None
            and country_allowed(entity.country.code if entity.country else ''))


def usable_entities(user):
    """The doctor's entities that can be used for a new order now (not archived, country in
    this site's region and not banned), the default first."""
    rows = active(user)
    allowed = [code for code in rows.values_list('country', flat=True).distinct()
               if country_allowed(code)]
    return rows.filter(country__in=allowed)


def has_usable_entity(user):
    return usable_entities(user).exists()


def default_entity(user):
    """The doctor's default entity when it is usable now, else None."""
    return usable_entities(user).filter(is_default=True).first()
