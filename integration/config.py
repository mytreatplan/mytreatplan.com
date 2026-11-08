"""The configuration snapshot from the admin site (epic-admin-interface entry 4, AD-3 item 1,
AD-4, AD-12, AD-22, interface I5).

The admin site owns the configuration (the admin directory: who an admin actor UUID is;
the catalogue, epic-catalogue entry 2; the country map, entry 3) and serves it signed
at ``GET /api/v1/config``; this portal pulls it and keeps applying the last good one
while the admin site is down. Admin users are never created here (AD-22).

`pull()` (the `pullconfig` job every 5 minutes, and the task `pull_config_task`, enqueued
by the admin site's signed nudge, ``POST /api/v1/config/nudge``):

1. records the attempt (`ConfigState.last_pull_at`);
2. fetches the answer with a signed GET (mytp-core's `signed_get`) from the admin site
   (`outbox.admin_site_url()`: INTERFACE_ADMIN_SITE, or the only INTERFACE_PEERS entry);
3. verifies it before anything is applied (mytp-core's `verify_snapshot`: the admin's
   public key from INTERFACE_PEER_KEYS, the issuer, this site as the audience, ``typ``,
   ``sha`` and ``ver``, the contract's shape, the reader format);
4. in one transaction with the state row locked: a version higher than the applied one
   replaces the admin directory and every registered section (`register_section_applier`:
   the catalogue app's `catalogue`, the regions app's `country_map`; a section the
   snapshot lacks is applied as None: the catalogue empties its tables, the country map
   keeps its rows) and becomes the applied version; the same version changes nothing (a
   good pull); a lower one is refused (`stale`).

Every refusal or failure leaves the applied snapshot untouched and records its code only
(`ConfigState.last_error`; never the snapshot): `network`, `peer_url`,
`http_<status>[_<code>]`, `bad_answer`, `bad_signature`, `audience`, `expired`,
`invalid_body`, `newer_format` (the format is newer than `SNAPSHOT_READER`: a warning
line once per version), `stale`, `not_configured` (no admin site, peer keys or signing
key: `pull_config` fails), `signing_key` (the KMS could not unwrap the signing key now),
`apply_failed`. A good pull clears it.
When no good pull has happened for WARN_AFTER (an hour), one Sentry warning per outage
(the error code only), marked in `warned_at` before it is sent (after the commit), and
reset by the next good pull (decision 2026-11-05).
"""
import logging
from datetime import timedelta
from functools import partial

import sentry_sdk
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from mytp_core import interface, snapshots
from mytp_core.contracts import ConfigSnapshot

from .models import AdminDirectoryEntry, ConfigState
from .outbox import _error_code, admin_site_url

logger = logging.getLogger('integration.config')

CONFIG_PATH = '/api/v1/config'
WARN_AFTER = timedelta(hours=1)
# Set while a pull task is queued (the nudge view's coalescing); expires by itself, so a
# lost task never blocks the next nudge for longer than this.
PULL_PENDING_KEY = 'mytp:config:pull-pending'
PULL_PENDING_SECONDS = 300

APPLIED = 'applied'
UNCHANGED = 'unchanged'
STALE = 'stale'
NOT_CONFIGURED = 'not_configured'
APPLY_FAILED = 'apply_failed'
SIGNING_KEY = 'signing_key'


# Section name -> apply(value): called in _apply's transaction with the snapshot's field
# of that name (a mytp-core model, or None when the snapshot has no such section). It
# returns a short summary for the applied log line (counts only, never contents), or None.
_SECTION_APPLIERS = {}
# The snapshot's own fields, applied by _apply itself.
HEADER_FIELDS = frozenset({'version', 'min_reader', 'generated_at', 'admin_directory'})


def register_section_applier(name, apply):
    """Apply the snapshot's section `name` (a field of mytp-core's ConfigSnapshot) with
    `apply(value)` whenever a new version is applied, inside its transaction: an error
    rolls the whole snapshot back (`apply_failed`). `value` is None when the snapshot
    has no such section (an older admin site): the applier decides what that means (the
    catalogue empties its tables, the country map keeps its rows).
    Registering the same function again is a no-op; another one under a taken name, or a
    name that is not a field of ConfigSnapshot, is a ValueError."""
    if name not in ConfigSnapshot.model_fields or name in HEADER_FIELDS:
        raise ValueError(f'{name!r} is not a section of the configuration snapshot.')
    if _SECTION_APPLIERS.get(name, apply) is not apply:
        raise ValueError(f'A config section applier for {name!r} is already registered.')
    _SECTION_APPLIERS[name] = apply


def section_applier_names():
    return tuple(_SECTION_APPLIERS)


class PullFailed(Exception):
    """A pull was refused or failed; `code` is what `ConfigState.last_error` holds."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def state():
    """The state row (created on first use)."""
    while True:
        row = ConfigState.objects.filter(pk=ConfigState.SINGLETON).first()
        if row is not None:
            return row
        try:
            with transaction.atomic():
                return ConfigState.objects.create(pk=ConfigState.SINGLETON)
        except IntegrityError:
            continue


def _locked_state():
    state()
    return ConfigState.objects.select_for_update().get(pk=ConfigState.SINGLETON)


def _fetch():
    """The verified ConfigSnapshot from the admin site, or PullFailed(code)."""
    try:
        site, base_url = admin_site_url()
        keyring = interface.Keyring.from_settings()
        interface.signing_key_setting()
    except interface.InterfaceError:
        raise PullFailed(NOT_CONFIGURED) from None
    try:
        answer = interface.signed_get(base_url + CONFIG_PATH, audience=site)
    except interface.SigningKeyUnavailable:
        raise PullFailed(SIGNING_KEY) from None
    except interface.PeerCallFailed as exc:
        raise PullFailed(_error_code(exc)[:64]) from None
    try:
        return snapshots.verify_snapshot(answer.data, keyring=keyring, issuer=site)
    except snapshots.SnapshotRefused as exc:
        if exc.code == snapshots.NEWER_FORMAT:
            _warn_newer_format(exc.version)
        raise PullFailed(exc.code) from None


def _warn_newer_format(version):
    """One warning line per version kept out for its newer format."""
    with transaction.atomic():
        row = _locked_state()
        if row.newer_format_version == version:
            return
        row.newer_format_version = version
        row.save(update_fields=['newer_format_version'])
    logger.warning('config snapshot v%s kept out: its format is newer than this site '
                   'reads (reader %s); the current one stays', version,
                   snapshots.SNAPSHOT_READER)


def _apply(snapshot, now, summaries=None):
    """APPLIED, UNCHANGED or STALE, in one transaction with the state row locked. The
    appliers' summaries are appended to `summaries` (a list) when given."""
    with transaction.atomic():
        row = _locked_state()
        if row.version is not None and snapshot.version <= row.version:
            return UNCHANGED if snapshot.version == row.version else STALE
        AdminDirectoryEntry.objects.all().delete()
        AdminDirectoryEntry.objects.bulk_create(
            AdminDirectoryEntry(id=entry.id, display_name=entry.display_name,
                                title=entry.title, active=entry.active,
                                time_zone=entry.time_zone)
            for entry in snapshot.admin_directory)
        for name, apply in _SECTION_APPLIERS.items():
            summary = apply(getattr(snapshot, name, None))
            if summary and summaries is not None:
                summaries.append(summary)
        row.version = snapshot.version
        row.applied_at = now
        row.save(update_fields=['version', 'applied_at'])
    return APPLIED


def _send_outage_warning(code):
    message = f'Configuration snapshot not pulled for an hour ({code})'
    logger.warning(message)
    with sentry_sdk.new_scope() as scope:
        scope.set_tag('config.error', code)
        sentry_sdk.capture_message(message, level='warning')


def _record_failure(code, now):
    with transaction.atomic():
        row = _locked_state()
        row.last_error = code
        since = row.last_success_at or row.created_at
        fields = ['last_error']
        if row.warned_at is None and now - since >= WARN_AFTER:
            row.warned_at = now
            fields.append('warned_at')
            transaction.on_commit(partial(_send_outage_warning, code))
        row.save(update_fields=fields)


def _record_success(now):
    ConfigState.objects.filter(pk=ConfigState.SINGLETON).update(
        last_success_at=now, last_error='', warned_at=None)


def pull(now=None):
    """Pull, verify and apply the admin site's snapshot. Returns (APPLIED or UNCHANGED,
    the version) or raises PullFailed(code), the code recorded; the applied snapshot is
    untouched by any refusal or failure."""
    now = now or timezone.now()
    state()
    ConfigState.objects.filter(pk=ConfigState.SINGLETON).update(last_pull_at=now)
    try:
        snapshot = _fetch()
        summaries = []
        try:
            outcome = _apply(snapshot, now, summaries)
        except Exception as error:  # noqa: BLE001 - rolled back; recorded, never quoted
            # The error's text may quote a value of the snapshot: its type only.
            logger.error('config snapshot v%s not applied (%s)', snapshot.version,
                         type(error).__name__)
            raise PullFailed(APPLY_FAILED) from None
        if outcome == STALE:
            raise PullFailed(STALE)
    except PullFailed as exc:
        _record_failure(exc.code, now)
        if exc.code != snapshots.NEWER_FORMAT:  # that one has its own line, once per version
            logger.warning('config pull refused: %s', exc.code)
        raise
    _record_success(now)
    if outcome == APPLIED:
        logger.info('config snapshot v%s applied (%s admin directory entries)%s',
                    snapshot.version, len(snapshot.admin_directory),
                    ''.join(f'; {summary}' for summary in summaries))
    return outcome, snapshot.version


def request_pull():
    """Enqueue a pull task unless one is already queued (the nudge view). Returns whether
    one was enqueued."""
    if not cache.add(PULL_PENDING_KEY, 1, timeout=PULL_PENDING_SECONDS):
        return False
    from .tasks import pull_config_task
    try:
        pull_config_task.enqueue()
    except Exception:
        cache.delete(PULL_PENDING_KEY)
        raise
    return True
