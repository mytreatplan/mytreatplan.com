---
title: 'Contact messages to the admin site'
type: 'feature'
ticket: '9'
created: '2026-11-07'
status: 'built'
baseline_revision: 'portal 78b82eb, core 35fd7cb, admin efb3c9f'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** I9 (AD-18): contact messages sent on a regional site reach only the company inbox by email; the admin site holds no copy for epic-sales-finance's list.

**Approach:** Same shape as 4.8's client records, over three repos: closed payloads `contact_message.updated` (full state) and `contact_message.deleted` (tombstone) in mytp-core; the portal records the event in the outbox in the transaction that saves the message (and sends the tombstone when a staff member deletes it); the admin stores the message encrypted in a `contact_messages` app and deletes it on the tombstone; a re-sync request and a backfill command. The email to the company inbox stays.

## Boundaries & Constraints

**Always:**
- Payload `contact_message.updated` (closed, `check_central_payload`): `first_name`, `middle_name`, `surname`, `email`, `phone` (the form's free format, ≤ 32, digits, spaces and `+()-.`), `country` (the form's free text, ≤ 80), `aligners`, `club`, `decision` (`yes`/`no`/empty), `sent_at` (Z). `contact_message.deleted`: empty. `aggregate_id` = the message's `uid` (story 1.8); `aggregate_version` = its new `sync_version`.
- Portal: `ContactMessage.sync_version` (bigint, default 0, `db_default=0`); `mytp_publicsite/contact_sync.py` `emit(message)` inside `transaction.atomic()` (lock, bump, record), called by `contact.accept` (create and emit in one atomic block; the inbox email stays `on_commit` as today); deletion through the Django admin (`delete_model`, `delete_queryset`) deletes the message's pending `.updated` rows, records `.deleted` at v+1 (nothing when it was never sent) and then deletes the row, all in one transaction. Signed `POST /api/v1/contact-messages/resync` (admin site only, closed body `{"aggregate_id"}`, 202 / 404 `unknown_contact_message` / 400 `invalid_body`); command `sync_contact_messages --all | <uid>...` (per-message, failures named).
- Admin: `contact_messages.ContactMessageRecord(uid pk, region FK (signing site), names, email, phone, country — encrypted —, answers, sent_at, version, updated_at)`, email blind index; appliers `contact_message.updated` (upsert, payload re-read with the reader model, region guard) and `contact_message.deleted` (delete the record; the version row stays); command `resync_contact_message <uid> [--region]`. No UI (epic-sales-finance's list).
- Nothing of a message in logs, FailedEvent, Sentry or task arguments.

- Decision (user, 2026-11-07): on `contact_message.deleted` the admin deletes the record, as 4.8 (its version row stays as the trace).
- Decision (user, 2026-11-07): the inbox mail status is not sent to the admin site.
- Decision (user, 2026-11-07): built after 4.5's release; this story ships mytp-core 0.14.0.
- Decision (user, 2026-11-08): a message whose payload the central contract (or the outbox size check) refuses is kept and mailed without its admin copy; Sentry gets the error class only and `sync_contact_messages` names it; any other failure rolls back the message and the event.
- Decision (user, 2026-11-07): kept whole (~1900 tokens), one plan, three PRs (core → admin, deployed first → portal).

**Never:** no change to the inbox email, the outbox, the ingest order, `AggregateVersion` semantics or 4.8's client paths; no new form field (the form has no free-text body today); no admin UI.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Sent | contact form on dev / dev-ae | one record on admindev (region eu / ae); inbox email still sent | — |
| Admin down | admindev stopped when sent | delivered by the dispatcher later; one record | — |
| Save fails | message create rolls back | no event | — |
| Deleted | staff deletes it in dev's Django admin | tombstone; record gone on admindev; pending update dropped | — |
| Never sent | deleted before any event (v0) | nothing sent | — |
| Re-sync | `resync_contact_message <uid>` | state sent again at v+1 | 404 unknown |
| Backfill | `sync_contact_messages --all` | each existing message sent once | failures named |
| Wrong region | another site sends an update for this uid | `rejected` (`region_mismatch`), version unchanged | — |

</frozen-after-approval>

## Code Map

Paths relative to `.../code/`.
- Portal: `mytp_publicsite/models.py` `ContactMessage` :35 (`CROSSES_SITES` :36, `uid` :41, encrypted fields :43-49, answers, `sent_at`, `mail_status`); `mytp_publicsite/views.py` `contact` :89 (not atomic; `accept` :106); `mytp_publicsite/contact.py` `accept` :266 (create + `on_commit` mail), `mail_job` :232, `has_link` :143; `mytp_publicsite/forms.py` phone pattern :19, :67; `mytp_publicsite/admin.py` `ContactMessageAdmin` :8 (read-only; delete is the only change path). Templates to mirror: `accounts/client_sync.py` (`emit` :65, `payload_of` :44, `resync` :92, pending-row deletion :80); `accounts/management/commands/sync_clients.py`; `integration/views.py` `_not_the_admin_site` :35, `clients_resync` :73, `integration/urls.py`, `integration/tasks.py` `resync_client_task` :57; `integration/outbox.py` `record` :141 (payload already stored encrypted).
- mytp-core: `contracts/clients.py` (types :34-48, readers :50/:66, writers :70/:87, `CLIENT_READERS` :92) → `contracts/contact_messages.py`; `contracts/payloads.py` `register_payload` :114 (calls :172-173); exports `contracts/__init__.py` :38/:70; schemas `schemas/payload_*.json` + drift test; event type rule `events.py:35`.
- Admin: `clients/models.py` `ClientRecord` (pattern), `interface/events.py` `Refused` :63, `_read` :71, `_client_record` :80, `_apply_client_updated` :90, `_apply_client_erased` :104, `appliers()` :112; `clients/management/commands/resync_client.py`; `mytp_admin/settings.py` `INSTALLED_APPS` :199, LOGGING :398; area `contact_messages` exists (`permissions/areas.py:59`, no UI here).
- Release as 4.8; built after 4.5's mytp-core release (0.13.0) → this ships 0.14.0.

## Tasks & Acceptance

**Execution:**
- [x] mytp-core: writer + reader models, registration, schemas, tests, version, README.
- [x] Admin: `contact_messages` app (model, migration), appliers, `resync_contact_message`, README, tests (admin rows of the matrix; ciphertext in the database).
- [x] Portal: `sync_version` + migration, `contact_sync.py`, atomic save in `accept`, admin delete hooks, re-sync endpoint + task, `sync_contact_messages`, runbook, tests (portal rows; the inbox email still sent once).
- [x] Both sites: vendor the new core.

**Acceptance Criteria:**
- Given a message saved and its event recorded, when the inbox email fails, then the event is still delivered (the email does not gate the copy).
- Given a staff member deletes several messages at once in the Django admin, when the action completes, then each sent message has one tombstone.

## Implementation Notes

**mytp-core 0.14.0** (`story/4.9-contact-messages`): `mytp_core/contracts/contact_messages.py`
(`ContactMessageUpdated` / `ContactMessageDeleted` writers, `...Reader` readers,
`CONTACT_MESSAGE_UPDATED`, `CONTACT_MESSAGE_DELETED`, `CONTACT_MESSAGE_READERS`), registered
in `payloads.py`, exported from `contracts/__init__.py` (and `SCHEMA_MODELS`), schemas
`payload_contact_message_updated.json` / `payload_contact_message_deleted.json` (exported with
`export_contract_schemas`). Field rules: names and country `max_length=80`; phone
`max_length=32`, pattern `^[0-9 +().\-]*$` (empty allowed, as the contract does not need
the form's 6-character minimum); email reuses `clients.Email`; answers `^(yes|no)?$`;
`sent_at` `UtcTime`. Version 0.14.0 in `pyproject.toml` and `__init__.py`; README
("Contact message events", schema list, compatibility rules). Tests:
`tests/test_contact_messages.py` (new) and the registered-types assertion in
`tests/test_contracts.py`.

**Admin** (`story/4.9-contact-messages`): app `contact_messages` (`ContactMessageRecord`,
migration `0001_initial`; names, email, phone, country encrypted; `email_index` blind
index; answers, `sent_at`, `version`, `updated_at` plain; `region` FK PROTECT,
`related_name='contact_messages'`); appliers `_apply_contact_message_updated` /
`_apply_contact_message_deleted` in `interface/events.py` (the region guard is now a shared
`_locked_record(model, envelope)`; `_client_record` keeps its name and behaviour and calls
it); command `resync_contact_message <uid> [--region]` (`contact_messages.resync` logger);
`INSTALLED_APPS`, LOGGING, `pytest.ini` testpaths; README (apps table, events table,
"Contact messages" section). Tests: `contact_messages/tests/test_contact_messages.py`
(reuses the fixtures and `send`/`FakePortal` of `clients/tests/test_clients.py`).

**Portal**: `ContactMessage.sync_version` + migration `0005_sync_version`;
`mytp_publicsite/contact_sync.py` (`payload_of`, `emit`, `delete`, `resync`);
`contact.accept` creates and emits in one `transaction.atomic()` (the inbox mail stays
`on_commit`); `ContactMessageAdmin.delete_model` / `delete_queryset` call
`contact_sync.delete` in a transaction; `POST /api/v1/contact-messages/resync`
(`integration.views.contact_messages_resync`, logger `integration.contact_messages`) and
`integration.tasks.resync_contact_message_task(uid)`; command
`sync_contact_messages --all | <uid>...`; runbook `deploy/README.md` ("Outbox" →
"Contact messages", and a pointer in the contact inbox paragraph). Tests:
`mytp_publicsite/tests/test_contact_sync.py` (new); `test_contact.py`'s
"mail only once committed" test now picks the mail's callback among the on-commit
callbacks (the outbox adds its log line and nudge).

**Both sites**: the 0.14.0 wheel was built from mytp-core's working tree (no tag yet) into
`vendor/` in place of 0.13.0, the `requirements.txt` line updated, the vendored-core tests
(and the admin's `layout/tests/test_deploy.py` wheel regex and README) moved to 0.14.0, and
the wheel installed in both venvs. The release must rebuild it from the `v0.14.0` tag with
`scripts/vendor-mytp-core.sh` (the bytes will differ: `SOURCE_DATE_EPOCH`).

**Deviations and choices**
- `emit` order is lock → record → bump (the plan says lock, bump, record): the outbox
  checks the payload before writing, so a refusal leaves `sync_version` untouched; same
  transaction, same result otherwise.
- `accept` keeps a message whose event the outbox refuses (pending the user's approval):
  the emit runs in a savepoint and only `SYNC_REFUSALS` = `(InvalidPayload,
  ContractViolation, UnknownEventType, OutboxError)` are caught (the contract's checks, e.g.
  an email the form accepts but the central checks refuse, such as one containing
  `x-amz-signature`; or the size refusal). The message is kept and mailed without its
  copy, the failure is reported as `Contact form error: sync (<class>)` (no values), so the
  form never 500s on it; `sync_contact_messages` names such a message. Any other error,
  an unrelated `ValueError` included, rolls back the message and the event together.
- `accept` registers the mail's on-commit callback (`partial(start_mail, pk)`, a named
  function) right after the create and BEFORE the emit savepoint, so the outbox's later
  non-robust callbacks (`_log_recorded`, `_enqueue_nudge`) cannot drop the mail if they
  raise after commit.
- `integration.views` gained a small `_resync_uid` helper used only by the new endpoint;
  `clients_resync` is left as it was (no change to 4.8's client paths).
- The admin's region guard was factored into `_locked_record` (shared by both kinds);
  `_client_record` still exists with the same behaviour.
- No change to `purge_outbox` or the outbox: delivered `.updated` rows of a deleted message
  stay until the outbox's own retention (the plan only drops pending ones).

**Review patches (coordinator, 2026-11-07)**
- Core: README's registered-types sentence lists the contact message types; a
  parametrised test that a `data:` URI country and an email containing `X-Amz-Signature`
  raise `ContractViolation` from `build_payload` without quoting the value. Wheel rebuilt
  and re-vendored in both sites (sha256 `7db0dd90...ba99f07`), installed in both venvs.
- Admin: positive log-line assertions (`event accepted: ...`, `event rejected: ...
  (region_mismatch)` / `(invalid_payload)`) next to the no-secrets checks; a test pinning
  that another region's `contact_message.deleted` for an unknown uid is accepted and
  creates the version row; README "Known limits" (the region guard needs an existing
  record; a wrong-region event at or below the stored version is `stale`).
- Portal: mail callback registered before the emit (test: a raising `_enqueue_nudge` after
  commit, the mail still sent exactly once); narrowed catch (tests: an unrelated
  `ValueError` in emit rolls back; an `OutboxError` keeps the message); `test_contact.py`
  asserts the mail callback is the first one (`callbacks[0].func is contact.start_mail`);
  bulk-delete atomicity test (a failure on the second message rolls back the first's
  delete and tombstone); a non-admin signed peer with a bad re-sync body gets 401, not
  400; runbook backfill lines for dev-ae.

## Plan Change Log

- 2026-11-08: the user accepted keeping a contract-refused message without its admin copy (decision line in the frozen block).

## Review Triage Log

Opus reviews, one per repo (2026-11-07):
- patch (portal, medium): the inbox mail's on-commit callback ran after the outbox's non-robust callbacks; a failing nudge enqueue would drop the mail → mail callback registered before the emit savepoint; test with the nudge raising.
- intent_gap (portal, accepted by the user 2026-11-08): a payload refused by the central contract keeps the message and its mail without the admin copy (Sentry by class, named by the backfill) → catch narrowed to contract/outbox refusals; any other error still rolls back.
- patch (portal): `OutboxError` treated as a refusal; callback test asserts order, not `<lambda>`; bulk-delete atomicity test; non-admin peer with a bad resync body → 401 test; runbook dev-ae lines.
- patch (core): README registered-types sentence; `ContractViolation` without the value test.
- patch (admin): positive log assertions; cross-region tombstone for an unknown uid pinned by a test; README known limits.
- defer (admin, accepted limit as 4.8): the region guard needs an existing record; a wrong-region event at or below the stored version answers `stale`, not `rejected`.
- reject (admin nit): `--region` case-sensitive, same as `resync_client`.

## Verification

**Commands:**
- mytp-core, admin and portal suites — all pass.

**Verification results (local, 2026-11-07, SQLite; after the review patches):**
- mytp-core: 1115 passed.
- Admin: 927 passed, 5 skipped (PostgreSQL-only row-lock tests); `manage.py check` no
  issues; `makemigrations --check` no changes.
- Portal (whole suite): 2096 passed, 5 skipped, 3 failed: the 3 known PostgreSQL-only
  tests in `accounts/tests/test_crypto.py` (`test_pending_registration_row`,
  `test_rotation_with_reencrypt_fields`,
  `test_data_migration_encrypts_existing_rows_and_reverses`); `manage.py check` no
  issues; `makemigrations --check` no changes.

**Manual checks (staging):**
- `sync_contact_messages --all` on dev and dev-ae; a contact form sent on dev and on dev-ae → a record each on admindev; with admindev stopped, send one on dev → delivered after it starts; delete one in dev's Django admin → gone on admindev; raw admin database read shows ciphertext; the inbox email still arrives.
