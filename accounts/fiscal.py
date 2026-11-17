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

Tax id checks and VIES (entry 6). `problems` adds per-country checks of the tax id (Spain:
DNI/NIE for a person, CIF for a company, with the checksum; Portugal: the NIF checksum; a
foreign country prefix is refused; accounts.taxid) and of the AE VAT id (15 digits). Every
other country's tax id stays free text. For a billing country in the EU-27 the entity also
has a `vies_status` (the pricing resolver's `catalogue.pricing.VIES_*` values): `valid`,
`invalid` (not found in VIES, or not a well-formed VAT number: VAT is charged, the save is
never refused for it), `pending` (VIES was unavailable) or `not_applicable` (any other
country). The service owns it, with `vies_checked_at` and the retry fields: `create` and
`update` ask VIES (accounts.vies) after the offline checks and outside any lock, only when
the tax id or the country is new or changed, and write the answer under the lock; a stale
edit is refused as before. A `pending` entity is retried by `retry_due` (the `retry_vies`
job): 15 minutes after the first failure, 1 hour after the second, then every 6 hours; one
error is logged (the entity id only) once it has been pending for 3 days. `vies_status`
gives the status to hand to `subscriptions.service.price_order_for(..., vies=...)`.

Nothing here logs a value, and nothing emits `client.updated` (entry 9 does, with the
core release). Only ids appear in exceptions and logs.
"""
import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from django_countries import countries

from regions.countries import lookup_country

from catalogue.pricing import (VIES_INVALID, VIES_NOT_APPLICABLE, VIES_PENDING,
                              VIES_VALID)
from . import client_sync, taxid, vies
from .models import FiscalEntity, normalize_fiscal_id

logger = logging.getLogger(__name__)

MAX_ENTITIES = 10
# The billing country that has a VAT id of its own (the UAE uses two ids).
VAT_COUNTRY = 'AE'
# The fields a writer may set (everything else is the service's).
FIELDS = ('kind', 'first_name', 'last_name', 'legal_name', 'tax_id', 'vat_id',
          'address_line1', 'address_line2', 'city', 'postal_code', 'province', 'country',
          'billing_email')
_TEXT_FIELDS = tuple(name for name in FIELDS if name not in ('kind', 'country'))
# The problem codes `problems` returns, by field ('__all__' for none): required, invalid,
# too_long, country_banned, country_other_region, limit, and for the tax id tax_id_invalid,
# tax_id_kind and tax_id_prefix, for the VAT id vat_id_invalid (accounts.taxid).
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
    `country_banned` and `country_other_region` (on `country`), and `tax_id_invalid`,
    `tax_id_kind`, `tax_id_prefix` (on `tax_id`) and `vat_id_invalid` (on `vat_id`)."""
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
    if data.get('tax_id') and 'tax_id' not in found and kind in (
            FiscalEntity.PERSON, FiscalEntity.COMPANY):
        code = taxid.tax_id_problem(data['tax_id'], country, kind)
        if code:
            found['tax_id'] = code
    if data.get('vat_id') and 'vat_id' not in found:
        code = taxid.vat_id_problem(data['vat_id'], country)
        if code:
            found['vat_id'] = code
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
    found = problems(values)
    if found:
        raise FiscalInvalid(found)
    if active(user).count() >= MAX_ENTITIES:  # before VIES: nothing is sent for a refusal
        raise FiscalInvalid({ALL: 'limit'})
    # VIES, outside any lock (a slow answer never holds the user's row).
    check = _check_fields(values, timezone.now())
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
        return FiscalEntity.objects.create(user=locked, is_default=takes_default, **values,
                                           **check)


def update(user, entity_id, data, *, version):
    """Change the active entity `entity_id` of `user` with `data` (any of FIELDS; the rest
    stays), if its `version` is still `version` (else StaleVersion). Saved in place;
    `version` goes up when something changed. Raises FiscalInvalid, ValueError (as
    `create`) and FiscalEntity.DoesNotExist (not the user's, or archived)."""
    _check_user(user)
    changes = clean_data(data)
    entity = FiscalEntity.objects.get(pk=entity_id, user_id=user.pk, archived_at__isnull=True)
    if entity.version != version:
        raise StaleVersion(str(entity.pk))
    values = clean_data(_values(entity) | changes)
    found = problems(values)
    if found:
        raise FiscalInvalid(found)
    before = _values(entity)
    # A new tax id or country is checked again, outside any lock; any other change keeps
    # the status.
    check = None
    if (values['tax_id'], values['country']) != (before['tax_id'], before['country']):
        check = _check_fields(values, timezone.now())
    with transaction.atomic():
        locked = _lock(user)
        entity = (FiscalEntity.objects.select_for_update()
                  .get(pk=entity_id, user=locked, archived_at__isnull=True))
        # The version moves with every change, so a changed tax id or country is stale too.
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
        if check is not None:
            for name, value in check.items():
                setattr(entity, name, value)
        if changed:
            entity.version += 1
            entity.save()
        return entity


# --- VIES (entry 6) ----------------------------------------------------------------------------

# The wait before the next try, by the number of failed tries so far; then every 6 hours.
RETRY_AFTER = {1: timedelta(minutes=15), 2: timedelta(hours=1)}
RETRY_LATER = timedelta(hours=6)
ALERT_AFTER = timedelta(days=3)
RETRY_BATCH = 100
# What resets the check fields.
_CLEARED = {'vies_checked_at': None, 'vies_pending_since': None, 'vies_attempts': 0,
            'vies_next_try_at': None, 'vies_alerted_at': None}


def retry_delay(attempts):
    """How long to wait for the next try after `attempts` failed tries."""
    return RETRY_AFTER.get(attempts, RETRY_LATER)


def _answer_fields(answer, now, entity=None):
    """The VIES fields after `answer` (vies.VALID, INVALID or UNAVAILABLE) at `now`;
    `entity` is the pending one being retried (its attempts and pending-since carry on)."""
    if answer == vies.UNAVAILABLE:
        attempts = (entity.vies_attempts if entity else 0) + 1
        return _CLEARED | {
            'vies_status': VIES_PENDING, 'vies_attempts': attempts,
            'vies_pending_since': (entity.vies_pending_since if entity else None) or now,
            'vies_next_try_at': now + retry_delay(attempts),
            'vies_alerted_at': entity.vies_alerted_at if entity else None}
    return _CLEARED | {
        'vies_status': VIES_VALID if answer == vies.VALID else VIES_INVALID,
        'vies_checked_at': now}


def _check_fields(values, now, entity=None):
    """The VIES fields for an entity with `values` (tax_id, country), asking VIES when the
    country is in the EU and the tax id is a well-formed VAT number for it. Call it
    outside any transaction."""
    plan = taxid.vies_plan(values['tax_id'], values['country'])
    if plan.action == taxid.NOT_APPLICABLE:
        return _CLEARED | {'vies_status': VIES_NOT_APPLICABLE}
    if plan.action == taxid.BAD:
        return _answer_fields(vies.INVALID, now)
    return _answer_fields(vies.fetch_vies(plan.country, plan.number), now, entity)


def vies_status(entity):
    """The status to give `price_order_for(..., vies=...)`: the entity's, or
    `not_applicable` when there is none."""
    return entity.vies_status if entity is not None else VIES_NOT_APPLICABLE


def tax_id_problem(entity):
    """The offline problem code of the entity's stored tax id or AE VAT id, or None: an
    id saved before the checks existed may fail them and stays usable, but the next edit
    must fix it (the list card says so)."""
    country = entity.country.code if entity.country else ''
    return (taxid.tax_id_problem(entity.tax_id, country, entity.kind)
            or taxid.vat_id_problem(entity.vat_id, country))


def _write_check(entity, fields):
    """Store `fields` on the pending `entity` if it still has the tax id and country that
    were checked (an edit meanwhile discards the answer). Under the row lock; returns
    whether it was written."""
    with transaction.atomic():
        row = (FiscalEntity.objects.select_for_update()
               .filter(pk=entity.pk, archived_at__isnull=True, vies_status=VIES_PENDING,
                       tax_id_index=entity.tax_id_index, country=entity.country.code)
               .first())
        if row is None:
            return False
        FiscalEntity.objects.filter(pk=row.pk).update(**fields)
        return True


def retry_due(now=None):
    """One run of the `retry_vies` job: ask VIES again for each due pending entity (at
    most RETRY_BATCH, oldest due first; archived ones never), then log one error for each
    entity pending for ALERT_AFTER, once. Returns the counts {'checked', 'answered',
    'still_pending', 'discarded', 'alerted'}. Never logs a value."""
    now = now or timezone.now()
    counts = {'checked': 0, 'answered': 0, 'still_pending': 0, 'discarded': 0, 'alerted': 0}
    due = (FiscalEntity.objects
           .filter(archived_at__isnull=True, vies_status=VIES_PENDING,
                   vies_next_try_at__lte=now).order_by('vies_next_try_at', 'pk')[:RETRY_BATCH])
    for entity in list(due):
        counts['checked'] += 1
        values = {'tax_id': entity.tax_id,
                  'country': entity.country.code if entity.country else ''}
        fields = _check_fields(values, now, entity)
        if not _write_check(entity, fields):
            counts['discarded'] += 1
        elif fields['vies_status'] == VIES_PENDING:
            counts['still_pending'] += 1
        else:
            counts['answered'] += 1
    overdue = FiscalEntity.objects.filter(
        archived_at__isnull=True, vies_status=VIES_PENDING, vies_alerted_at__isnull=True,
        vies_pending_since__lte=now - ALERT_AFTER)
    for pk in list(overdue.values_list('pk', flat=True)):
        if FiscalEntity.objects.filter(pk=pk, vies_alerted_at__isnull=True).update(
                vies_alerted_at=now):
            counts['alerted'] += 1
            logger.error('VIES check still pending after %d days for fiscal entity %s',
                         ALERT_AFTER.days, pk)
    return counts


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
