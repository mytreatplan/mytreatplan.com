"""The doctor's shipping addresses (epic-doctor-profile entry 7, AD-2, AD-15): where
physical products are sent. `accounts.models.ShippingAddress` holds them; this module is
the only writer, and what epic-ordering reads. It mirrors accounts.fiscal.

Every write is atomic and takes the user's row lock first (`select_for_update`), so two
tabs, or an order and the settings page, never interleave:

- `create(user, data)`: validates (`problems`), refuses the 11th active address, saves;
  the first active address becomes the default;
- `update(user, address_id, data, *, version)`: refuses a stale `version` (StaleVersion),
  validates the merged data, saves in place and goes `version` up when something changed;
- `make_default(user, address_id)`: exactly one default among the non-archived addresses;
- `remove(user, address_id)`: a hard delete while the address is unused, else it is
  archived (hidden, not usable); a removed default passes to the most recently changed
  remaining address;
- `mark_used(address, at)`: for epic-ordering, when an order uses the address (idempotent);
- `erase(user)`: the account erasure deletes all of them.

Rules (`problems`): the address line 1, city, postal code (not for AE), recipient name,
contact phone and country are required. The phone is parsed with `phonenumbers`, the
address country being the default region, and stored in E.164. The country must be in this
site's region and not banned (regions.countries.lookup_country), checked at every save, so
an address whose country changed since (banned, or moved to the other region) cannot be
saved again unless its country is changed. The same check, now, decides which addresses
are `usable_addresses`. At most MAX_ADDRESSES active (not archived) addresses per doctor.

What epic-ordering reads: `usable_addresses(user)`, `default_address(user)`,
`has_usable_address(user)` and `mark_used(address)`.

Every write that changes what the admin site's client record copies (create, a changed
update, a changed default, remove, `mark_used` when it sets the date) emits `client.updated`
(accounts.client_sync, epic-doctor-profile entry 9), inside the write's transaction with
the user's row locked. Nothing here logs a value. Only ids appear in exceptions and logs.
"""
import phonenumbers
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django_countries import countries

from mytp_core.contracts import find_violations
from regions.countries import lookup_country

from . import client_sync
from .models import ShippingAddress

MAX_ADDRESSES = 10
# The shipping country whose postal code is optional (the UAE has none).
NO_POSTAL_COUNTRY = 'AE'
# The fields a writer may set (everything else is the service's).
FIELDS = ('label', 'recipient_name', 'clinic_name', 'contact_phone', 'delivery_notes',
          'address_line1', 'address_line2', 'city', 'postal_code', 'province', 'country')
_TEXT_FIELDS = tuple(name for name in FIELDS if name not in ('country', 'contact_phone'))
# The problem codes `problems` returns, by field ('__all__' for none): required, invalid,
# too_long, country_banned, country_other_region, limit, unusable.
ALL = '__all__'


class ShippingInvalid(Exception):
    """A write was refused; `problems` is {field: code} (see `problems`)."""

    def __init__(self, problems):
        super().__init__(', '.join(f'{field}: {code}' for field, code in problems.items()))
        self.problems = problems


class StaleVersion(Exception):
    """The address changed since the writer read it (its `version` moved on)."""


def _max_length(name):
    return ShippingAddress._meta.get_field(name).max_length


def parse_phone(number, country=''):
    """The contact phone in E.164 form, or None when it is not a valid number. A number
    without a prefix is read in `country` (a code of a region)."""
    number = (number or '').strip()
    if not number or any(char.isalpha() for char in number):
        return None
    region = (country or '').upper() or None
    try:
        parsed = phonenumbers.parse(number, region)
    except phonenumbers.NumberParseException:
        return None
    if parsed.extension or not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def normalize_newlines(text):
    """`text` with \\r\\n and a lone \\r turned into \\n."""
    return text.replace('\r\n', '\n').replace('\r', '\n')


def clean_data(data):
    """`data` ({field: value}, FIELDS only: ValueError for any other) normalised: text
    stripped, the country uppercased. The phone is rewritten to E.164 when it is valid
    (else only stripped: `problems` refuses it)."""
    unknown = set(data) - set(FIELDS)
    if unknown:
        raise ValueError(f'Not a shipping address field: {", ".join(sorted(unknown))}')
    cleaned = {name: (value or '').strip() if isinstance(value, str) or value is None
               else value for name, value in data.items()}
    if 'country' in cleaned:
        cleaned['country'] = str(cleaned['country'] or '').strip().upper()
    if cleaned.get('delivery_notes'):
        # A browser posts a line break as \r\n; store (and count) it as \n.
        cleaned['delivery_notes'] = normalize_newlines(cleaned['delivery_notes'])
    if cleaned.get('contact_phone'):
        e164 = parse_phone(cleaned['contact_phone'], cleaned.get('country', ''))
        if e164:
            cleaned['contact_phone'] = e164
    return cleaned


def problems(data):
    """{field: code} of what is wrong with `data` (the full, cleaned set of FIELDS; see
    `clean_data`), empty when it can be saved. Codes: `required`, `invalid` (the phone,
    an unknown country), `too_long`, and `country_banned` and `country_other_region`
    (on `country`)."""
    found = {}
    country = data.get('country') or ''
    postal = () if country == NO_POSTAL_COUNTRY else ('postal_code',)
    for name in ('recipient_name', 'contact_phone', 'address_line1', 'city', *postal):
        if not data.get(name):
            found[name] = 'required'
    for name in _TEXT_FIELDS:
        if name not in found and len(data.get(name) or '') > _max_length(name):
            found[name] = 'too_long'
    # Text the central payload rules refuse (a link to object storage, a data: URI, 200+
    # base64-like characters) could not go to the admin site (client.updated).
    for name in _TEXT_FIELDS:
        if name not in found and data.get(name) and find_violations({'value': data[name]}):
            found[name] = 'not_allowed'
    # Without a country a local number cannot be read; the country's own error shows.
    if data.get('contact_phone') and 'contact_phone' not in found and country:
        if parse_phone(data['contact_phone'], country) != data['contact_phone']:
            found['contact_phone'] = 'invalid'
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
        raise ValueError('Shipping addresses belong to doctors (client accounts) only.')


def _lock(user):
    return get_user_model().objects.select_for_update().get(pk=user.pk)


def active(user):
    """The doctor's non-archived addresses, the default first, then oldest first."""
    return (ShippingAddress.objects.filter(user_id=user.pk, archived_at__isnull=True)
            .order_by('-is_default', 'created_at', 'pk'))


def _values(address):
    return {name: getattr(address, name) for name in FIELDS} | {
        'country': address.country.code if address.country else ''}


def create(user, data):
    """Save a new address for `user` from `data` (FIELDS; the optional ones may be
    missing). Returns it. Raises ShippingInvalid for a problem, or when `user` already has
    MAX_ADDRESSES active addresses (`__all__`: `limit`); ValueError for an unknown field
    or a non-doctor."""
    _check_user(user)
    values = {name: '' for name in FIELDS} | clean_data(data)
    found = problems(values)
    if found:
        raise ShippingInvalid(found)
    with transaction.atomic():
        locked = _lock(user)
        found = problems(values)
        existing = active(locked)
        if existing.count() >= MAX_ADDRESSES:
            found[ALL] = 'limit'
        if found:
            raise ShippingInvalid(found)
        # The new address (its country is allowed: `problems` passed) becomes the default
        # when there is none, or the current one can no longer be used.
        current = existing.filter(is_default=True).first()
        takes_default = current is None or not is_usable(current)
        if takes_default and current is not None:
            ShippingAddress.objects.filter(pk=current.pk).update(is_default=False)
        address = ShippingAddress.objects.create(user=locked, is_default=takes_default,
                                                 **values)
        client_sync.emit(locked)
        return address


def update(user, address_id, data, *, version):
    """Change the active address `address_id` of `user` with `data` (any of FIELDS; the
    rest stays), if its `version` is still `version` (else StaleVersion). Saved in place;
    `version` goes up when something changed. Raises ShippingInvalid, ValueError (as
    `create`) and ShippingAddress.DoesNotExist (not the user's, or archived)."""
    _check_user(user)
    changes = clean_data(data)
    with transaction.atomic():
        locked = _lock(user)
        address = (ShippingAddress.objects.select_for_update()
                   .get(pk=address_id, user=locked, archived_at__isnull=True))
        if address.version != version:
            raise StaleVersion(str(address.pk))
        values = clean_data(_values(address) | changes)
        found = problems(values)
        if found:
            raise ShippingInvalid(found)
        before = _values(address)
        changed = [name for name in FIELDS if values[name] != before[name]]
        for name in changed:
            setattr(address, name, values[name])
        if changed:
            address.version += 1
            address.save()
            client_sync.emit(locked)
        return address


def make_default(user, address_id):
    """Make the active address `address_id` the default (the others stop being it).
    ShippingAddress.DoesNotExist when it is not the user's or is archived; ShippingInvalid
    (`__all__`: `unusable`) when its country is no longer allowed. Does not touch
    `version` or `updated_at`."""
    _check_user(user)
    with transaction.atomic():
        locked = _lock(user)
        address = ShippingAddress.objects.get(pk=address_id, user=locked,
                                              archived_at__isnull=True)
        if not is_usable(address):
            raise ShippingInvalid({ALL: 'unusable'})
        if not address.is_default:
            active(locked).filter(is_default=True).update(is_default=False)
            ShippingAddress.objects.filter(pk=address.pk).update(is_default=True)
            address.is_default = True
            client_sync.emit(locked)
        return address


DELETED, ARCHIVED = 'deleted', 'archived'


def remove(user, address_id, *, now=None):
    """Delete the active address `address_id`, or archive it when an order has used it.
    Returns DELETED or ARCHIVED. When it was the default, the most recently changed
    remaining usable address becomes the default. ShippingAddress.DoesNotExist as
    `make_default`."""
    _check_user(user)
    with transaction.atomic():
        locked = _lock(user)
        address = ShippingAddress.objects.select_for_update().get(
            pk=address_id, user=locked, archived_at__isnull=True)
        was_default = address.is_default
        if address.used_at is None:
            address.delete()
            outcome = DELETED
        else:
            address.is_default = False
            address.archived_at = now or timezone.now()
            address.save(update_fields=['is_default', 'archived_at'])
            outcome = ARCHIVED
        if was_default:
            # The most recently changed usable address; none when none is usable.
            newest = ('-updated_at', '-created_at', '-pk')
            successor = usable_addresses(locked).order_by(*newest).first()
            if successor is not None:
                ShippingAddress.objects.filter(pk=successor.pk).update(is_default=True)
        client_sync.emit(locked)
        return outcome


def mark_used(address, at=None):
    """Record that an order used `address` (epic-ordering): sets `used_at` to `at` (now by
    default) unless it is already set. Idempotent; returns whether it was set now."""
    with transaction.atomic():
        locked = _lock(address.user)
        current = ShippingAddress.objects.select_for_update().get(pk=address.pk)
        if current.used_at is None:
            current.used_at = at or timezone.now()
            ShippingAddress.objects.filter(pk=current.pk).update(used_at=current.used_at)
            address.used_at = current.used_at
            client_sync.emit(locked)
            return True
        address.used_at = current.used_at
        return False


def erase(user):
    """Erasure: delete all of the account's shipping addresses, archived ones included."""
    ShippingAddress.objects.filter(user_id=user.pk).delete()


# --- What the order flows read ---------------------------------------------------------------

def country_allowed(code):
    """Whether this site may ship to `code` now: in its region and not banned."""
    return lookup_country(code).own_region


def is_usable(address):
    """Whether `address` can be used for a new order: not archived and its country allowed
    now."""
    return (address.archived_at is None
            and country_allowed(address.country.code if address.country else ''))


def usable_addresses(user):
    """The doctor's addresses that can be used for a new order now (not archived, country
    in this site's region and not banned), the default first."""
    rows = active(user)
    allowed = [code for code in rows.values_list('country', flat=True).distinct()
               if country_allowed(code)]
    return rows.filter(country__in=allowed)


def has_usable_address(user):
    return usable_addresses(user).exists()


def default_address(user):
    """The doctor's default address when it is usable now, else None."""
    return usable_addresses(user).filter(is_default=True).first()
