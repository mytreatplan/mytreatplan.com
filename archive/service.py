"""The account archive (epic-ordering 9.12, AD-19).

`build(user)` returns the account's archive document (versioned JSON, `"schema": 1`):
account and profile, consents, fiscal entities, shipping addresses, patients with their
work orders, order and invoice summaries (numbers, dates, amounts, patient codes) and the
deletion traces. Never: maker logins or names, image bytes.

`write(user)` encrypts it with the `age` binary to ARCHIVE_AGE_RECIPIENT (the plain text
goes to age's stdin and never touches the disk), puts it to the archive bucket with S3
Object Lock in COMPLIANCE mode until now + ARCHIVE_RETENTION_YEARS and the storage class
ARCHIVE_STORAGE_CLASS, then checks it with HeadObject (size, SHA-256, lock mode and
retain-until) and records an `AccountArchive` row. An account that already has its row is
only verified (a run that failed after the put). Nothing is deleted here: the caller
(accounts.erasure.purge_erased) deletes the live rows only after `write` returns.

The object key is `accounts/<user uid>.json.age`: no patient name, email or file name in
a key, a log, a task argument or an error (`ArchiveError` carries a fixed message and an
S3 error code only).
"""
import base64
import hashlib
import json
import math
import shutil
import subprocess
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from storage import s3

from .models import AccountArchive

SCHEMA = 1
KEY_PREFIX = 'accounts/'
KEY_SUFFIX = '.json.age'
LOCK_MODE = 'COMPLIANCE'
AGE_TIMEOUT = 120


class ArchiveError(RuntimeError):
    """The archive could not be written or verified. The message never holds personal
    data."""


def object_key(uid):
    return f'{KEY_PREFIX}{uid}{KEY_SUFFIX}'


def retention_years():
    years = getattr(settings, 'ARCHIVE_RETENTION_YEARS', None)
    if not years or years < 1:
        raise ArchiveError('ARCHIVE_RETENTION_YEARS is not set')
    return years


def retain_until(now):
    """When an object written at `now` may be deleted: ARCHIVE_RETENTION_YEARS years."""
    return now + timedelta(days=math.ceil(retention_years() * 365.25))


def expiration_days():
    """The bucket lifecycle's Expiration, in days: the retention plus one day."""
    return math.ceil(retention_years() * 365.25) + 1


# --- The document ----------------------------------------------------------------------------

def _row(obj, fields):
    return {name: getattr(obj, name) for name in fields}


def _country(value):
    return str(value) if value else ''


def build(user):
    """The archive document of `user` (a dict; `dumps` turns it into JSON bytes)."""
    from accounts.models import FiscalEntity, Profile, ShippingAddress
    from invoices.models import Invoice
    from legal.models import LegalChoice
    from patients.models import Patient, WorkOrder
    from payments.models import Order
    from storage.models import DeletionTrace

    profile = Profile.objects.filter(user_id=user.pk).first()
    address = ('address_line1', 'address_line2', 'city', 'postal_code', 'province')
    fiscal = []
    for e in FiscalEntity.objects.filter(user_id=user.pk).order_by('created_at', 'id'):
        row = _row(e, ('id', 'kind', 'is_default', 'archived_at', 'created_at', 'used_at',
                       'first_name', 'last_name', 'legal_name', 'tax_id', 'vat_id',
                       'billing_email') + address)
        row['country'] = _country(e.country)
        fiscal.append(row)
    shipping = []
    for a in ShippingAddress.objects.filter(user_id=user.pk).order_by('created_at', 'id'):
        row = _row(a, ('id', 'label', 'recipient_name', 'clinic_name', 'contact_phone',
                       'delivery_notes', 'is_default', 'archived_at', 'created_at')
                   + address)
        row['country'] = _country(a.country)
        shipping.append(row)
    consents = [{'kind': c.document.kind, 'version': c.document.version,
                 'accepted': c.accepted, 'decided_at': c.decided_at}
                for c in LegalChoice.objects.filter(user_id=user.pk).select_related('document')]
    patients = []
    codes = []
    for p in Patient.objects.filter(doctor_id=user.pk).order_by('created_at', 'code'):
        codes.append(p.code)
        row = _row(p, ('id', 'code', 'first_name', 'surname', 'reference', 'comments',
                       'status', 'created_at', 'updated_at'))
        row['work_orders'] = [
            _row(w, ('id', 'code', 'stage', 'order_line_id', 'order_id', 'product_id',
                     'created_at', 'updated_at'))
            for w in WorkOrder.objects.filter(patient_id=p.pk).order_by('created_at', 'id')]
        patients.append(row)
    orders = []
    for o in Order.objects.filter(user_id=user.pk).order_by('created_at', 'id'):
        row = _row(o, ('id', 'reference', 'state', 'kind', 'currency', 'net_total', 'vat_total',
                       'total', 'created_at', 'paid_at', 'cancelled_at', 'expired_at',
                       'refunded_at'))
        row['lines'] = [_row(line, ('code', 'name', 'quantity', 'net', 'patient_code'))
                        for line in o.lines.all()]
        invoice = Invoice.objects.filter(order_id=o.pk).first()
        invoices = [invoice] if invoice else []
        invoices += list(Invoice.objects.filter(original__order_id=o.pk))
        row['invoices'] = [
            {**_row(i, ('number', 'kind', 'issue_date', 'currency', 'net_total', 'vat_total',
                        'total')),
             'patient_codes': sorted({line.patient_code for line in i.lines.all()
                                      if line.patient_code})}
            for i in invoices]
        orders.append(row)
    traces = DeletionTrace.objects.filter(actor_uuid=user.uid)
    if codes:
        from django.db.models import Q
        traces = DeletionTrace.objects.filter(Q(actor_uuid=user.uid) | Q(patient_code__in=codes))
    return {
        'schema': SCHEMA,
        'archived_at': timezone.now(),
        'account': {
            **_row(user, ('uid', 'email', 'title', 'first_name', 'middle_name', 'last_name',
                          'phone', 'home_region', 'how_found', 'how_found_other',
                          'date_joined', 'erased_at', 'purge_after')),
            'country': _country(user.country),
            'profile': (_row(profile, ('language', 'city', 'time_zone', 'profession',
                                       'landline', 'alternative_email'))
                        if profile else None),
        },
        'consents': consents,
        'fiscal_entities': fiscal,
        'shipping_addresses': shipping,
        'patients': patients,
        'orders': orders,
        'deletion_traces': [
            _row(t, ('object_id', 'patient_code', 'actor_type', 'actor_uuid', 'deleted_at'))
            for t in traces.order_by('deleted_at', 'object_id')],
    }


def dumps(document):
    return json.dumps(document, sort_keys=True, ensure_ascii=False, default=str,
                      separators=(',', ':')).encode('utf-8')


# --- Encrypting, putting, checking -----------------------------------------------------------

def encrypt(plain):
    """`plain` encrypted with the `age` binary to ARCHIVE_AGE_RECIPIENT (through stdin and
    stdout: no plain text on disk). Raises ArchiveError."""
    age = shutil.which('age')
    if not age:
        raise ArchiveError('the age binary is not installed')
    try:
        result = subprocess.run([age, '-r', settings.ARCHIVE_AGE_RECIPIENT.strip()],
                                input=plain, capture_output=True, timeout=AGE_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ArchiveError(f'age failed ({type(exc).__name__})') from None
    if result.returncode != 0 or not result.stdout:
        raise ArchiveError('age failed')
    return result.stdout


def _same_instant(a, b):
    return a is not None and abs((a - b).total_seconds()) < 2


def _head(client, key):
    """HeadObject of `key`: the response, or None when there is no such object. Any other
    failure is an ArchiveError."""
    try:
        with s3.out_of_sentry():
            return client.head_object(Bucket=settings.ARCHIVE_BUCKET, Key=key,
                                      ChecksumMode='ENABLED')
    except Exception as exc:  # noqa: BLE001
        if s3.is_not_found(exc):
            return None
        raise ArchiveError(f'head failed ({s3.describe_error(exc)})') from None


def _verify(head, *, size=None, sha256_b64=None, md5_hex=None, retain=None):
    """The HeadObject response `head` checked: the size (when known), the checksum (the
    SHA-256 the store returns when it has one, else the ETag against the MD5), the
    COMPLIANCE lock and the retain-until date (equal to `retain` when given, else in the
    future). Raises ArchiveError naming only what differed. Returns the retain-until."""
    problems = []
    if size is not None and head.get('ContentLength') != size:
        problems.append('size')
    if sha256_b64 is not None:
        if head.get('ChecksumSHA256'):
            if head['ChecksumSHA256'] != sha256_b64:
                problems.append('checksum')
        elif md5_hex is not None and str(head.get('ETag', '')).strip('"') != md5_hex:
            problems.append('checksum')
    if head.get('ObjectLockMode') != LOCK_MODE:
        problems.append('lock mode')
    until = head.get('ObjectLockRetainUntilDate')
    if until is None:
        problems.append('retain-until')
    elif retain is not None:
        if not _same_instant(until, retain):
            problems.append('retain-until')
    elif until <= timezone.now():
        problems.append('retain-until')
    if problems:
        raise ArchiveError('archive object differs: ' + ', '.join(problems))
    return until


def write(user, *, client=None, now=None):
    """Archive `user` (see the module doc). Returns the `AccountArchive` row. Raises
    ArchiveError (or the model's own errors) and leaves every live row alone.

    With no row yet, the key is looked at first: an object already there (a put whose
    check or row failed) is verified (COMPLIANCE lock, retain-until in the future; its size
    and checksum are unknown) and recorded, never put again, so the bucket never gets a
    second locked version."""
    client = client or s3.make_archive_client()
    existing = AccountArchive.objects.filter(user_uid=user.uid).first()
    if existing is not None:
        _verify(_head_or_fail(client, existing.object_key), size=existing.size,
                sha256_b64=base64.b64encode(bytes.fromhex(existing.sha256)).decode(),
                retain=existing.retain_until)
        return existing
    key = object_key(user.uid)
    head = _head(client, key)
    if head is not None:
        until = _verify(head)
        sha = head.get('ChecksumSHA256')
        return AccountArchive.objects.create(
            user_uid=user.uid, object_key=key, size=head.get('ContentLength') or 0,
            sha256=base64.b64decode(sha).hex() if sha else '',
            written_at=head.get('LastModified') or timezone.now(), retain_until=until)
    now = now or timezone.now()
    retain = retain_until(now)
    data = encrypt(dumps(build(user)))
    digest = hashlib.sha256(data).digest()
    sha256_b64 = base64.b64encode(digest).decode()
    md5 = hashlib.md5(data)  # noqa: S324 - the store's integrity header, not security
    try:
        with s3.out_of_sentry():
            client.put_object(
                Bucket=settings.ARCHIVE_BUCKET, Key=key, Body=data,
                ContentType='application/octet-stream',
                StorageClass=getattr(settings, 'ARCHIVE_STORAGE_CLASS', '') or 'STANDARD_IA',
                ContentMD5=base64.b64encode(md5.digest()).decode(),
                ChecksumSHA256=sha256_b64,
                ObjectLockMode=LOCK_MODE, ObjectLockRetainUntilDate=retain)
    except Exception as exc:  # noqa: BLE001
        raise ArchiveError(f'put failed ({s3.describe_error(exc)})') from None
    _verify(_head_or_fail(client, key), size=len(data), sha256_b64=sha256_b64,
            md5_hex=md5.hexdigest(), retain=retain)
    return AccountArchive.objects.create(
        user_uid=user.uid, object_key=key, sha256=digest.hex(), size=len(data),
        written_at=now, retain_until=retain)


def _head_or_fail(client, key):
    head = _head(client, key)
    if head is None:
        raise ArchiveError('archive object is missing')
    return head


def delete_expired(now=None):
    """Delete the rows whose `retain_until` has passed (the bucket's lifecycle removes
    the object). Returns how many."""
    now = now or timezone.now()
    return AccountArchive.objects.filter(retain_until__lte=now).delete()[0]
