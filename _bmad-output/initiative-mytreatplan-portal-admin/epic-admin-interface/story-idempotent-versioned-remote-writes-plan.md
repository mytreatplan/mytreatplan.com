---
title: 'Idempotent versioned remote writes'
type: 'feature'
ticket: '6'
created: '2026-11-09'
status: 'built'
baseline_revision: 'portal bc86f82, core f5a64a3, admin aaa705d'
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

**Problem:** I7 (AD-3, AD-15): admin users can read regional records live (4.5) but cannot change them; a write must apply once even when retried, must not overwrite a newer change, must go through the region's own service code, and must be in the region's access log.

**Approach:** Three repos, three PRs (mytp-core 0.16.0 → admin → portal). **mytp-core:** a write contract (idempotency key, expected version, changes), writable fields per role in `mytp_core.access`, error codes `stale_version` (409) and `idempotency_key_reused` (422). **Portal:** signed `POST /api/v1/remote/sample-record` that checks the claims and writable fields, logs first (fail closed), then applies through `integration.sample.update_sample_record` (row lock, version check, version + 1), storing the key and the response so a retry answers the same. **Admin:** a client `remote.write()` and a form on the sample-record page.

## Boundaries & Constraints

**Always:**
- Request (closed model): `idempotency_key` (UUID, made by the admin client per form render), `expected_version` (int), `changes` (closed per resource: for the sample, `note` ≤ 500). Token carries the actor as in 4.5 (`sub`, `role`, `regions`).
- Rules: `mytp_core.access` gains writable fields per role per resource (allow-list); a field outside it → 403 `forbidden` (reason `field`). The admin matrix's CHANGE on the resource's area must agree (test extended from 4.5's `remote_access_mismatches`).
- Portal order: claims and rules → idempotency lookup (same actor + key: same body → the stored answer; different body → 422) → access log row written (fail closed, 503 `access_log_unavailable`, nothing applied) → service function in its own transaction: lock, version check (stale → 409 `stale_version` with the current version, logged as refused best effort), apply, version + 1, store key + response → 200 with the record as the actor may read it, `no-store`.
- `SampleRecord.version` (bigint, starts at 1); `seed_sample_record` resets it.
- Nothing of the record in the admin database, logs, cache or task arguments (AD-23); the admin audit gets one row per write with resource, region, object id and outcome only.

- Decision (user, 2026-11-09): idempotency keys in a portal table `RemoteWrite` (actor + key unique, request hash, status, response body), purged after 7 days by the daily `purge_outbox` job.
- Decision (user, 2026-11-09): a retry with the same key and body gets the stored status and body (only fields that actor may read); the same key with another body gets 422 `idempotency_key_reused`.
- Decision (user, 2026-11-09): an integer `version` on the record, sent as `expected_version` in the body; a 409 `stale_version` carries the current version.
- Decision (user, 2026-11-09): a new `operation` column (`read`/`write`) on `RemoteAccess`, the returned field names in `fields`, as reads (user, 2026-11-09); the row is written before applying (granted = allowed to try); a stale write adds a refused row.
- Decision (user, 2026-11-09): the sample write changes `note` only, for the Sysadmin, the Clinical Manager and an assigned Clinical User (the matrix CHANGE on `patients`).
- Decision (user, 2026-11-09): a note form on the existing sample-record page (key and version hidden, from the last read; a 409 shows "changed meanwhile, reload"), and an admin audit row without values.
- Decision (user, 2026-11-09): one plan, three PRs (mytp-core 0.16.0 → admin, deployed first → portal), kept whole (recommended default; question 7 not answered explicitly).

**Never:** no change to the read path's behaviour or codes, `InterfaceCall` semantics, the outbox or eventlog contracts; no bulk writes; no writes to `amount` or `patient_name` from clinical roles.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Write | Clinical Manager, note, version 1 | 200, version 2, one access row (write, granted) | — |
| Retry | same key + body | the same 200, applied once | — |
| Key reused | same key, other body | 422 `idempotency_key_reused`, nothing applied | — |
| Stale | expected_version 1, row at 2 | 409 `stale_version`, current version in the body | logged refused |
| Not allowed field | Clinical Manager writes `amount` | 403 `forbidden` | logged refused |
| Not assigned | Clinical User, other assignee | 403 | logged refused |
| Log unwritable | access row insert fails | 503, nothing applied | — |
| Admin database | after writes | no record data; audit rows without values | — |

</frozen-after-approval>

## Code Map

Paths relative to `.../code/`.
- Portal: `integration/remote.py` `remote_read` :67-112 (order to mirror; `error` :49), `integration/access_log.py` :28-30 (`durable=True`: log before the service transaction), `integration/models.py` `RemoteAccess` :202-259, `SampleRecord` :262-285 (no version), `integration/sample.py` :34-42, `integration/views.py` :153-167 and POST precedents :52/:85/:124, `integration/calls.py` `signed_endpoint` :65, `integration/urls.py` :12, `integration/outbox.py` `purge` :334, `seed_sample_record`.
- mytp-core: `access.py` roles :26-33, `SAMPLE_RECORD` rules :84-100, `decide` :146; `contracts/remote.py` :37-88; `contracts/errors.py` :20, :30-43; `interface.py` `signed_post` :677, `remote_actor` :511; `models.py`/`tasks.py` `TaskEffect`/`run_once` (pattern for the key table).
- Admin: `interface/remote.py` `read` :64-102, `_failure` :55; `interface/remote_sample.py` :43-96 + template; `permissions/matrix.py` patients CHANGE (:88-89, :125, :136), `remote_access_mismatches`; `audit/service.py`.

## Tasks & Acceptance

**Execution:**
- [x] mytp-core: write request/response models, writable rules, error codes, schemas, tests, 0.16.0, README.
- [x] Portal: migration (version, `RemoteWrite`, `RemoteAccess.operation`), endpoint, service function, purge, runbook, tests.
- [x] Admin: `remote.write()`, form, audit, matrix agreement test, README, tests.
- [x] Both sites: vendor the new core (wheel built locally from the working tree; the release rebuilds it from the tag).

**Acceptance Criteria:**
- Given two admin users write the same record from version 1, when both send, then exactly one is applied and the other gets 409.
- Given a write retried after a timeout, when it arrives twice, then the note changes once and both answers are equal.

## Implementation Notes

- mytp-core 0.16.0: `access.Rule` gains `writable` (default empty, so two-field rules are unchanged); import-time checks: writable ⊆ the role's readable fields, and no clinical role writes a financial field or patient name. `access.decide_write(resource, actor, region, names, assignee=None)` = `decide` + every name in `writable` (else reason `field`; no names is refused too); `access.writable(resource, role)`. Sample: `note` writable by Sysadmin, Clinical Manager, Clinical User. `contracts.remote`: `RemoteWriteRequest` (closed `WriterModel`: `idempotency_key` canonical UUID, `expected_version` strict int 1..2^63-1, `changes` 1-50 snake_case names → JSON values), `remote_write_request(...)`, `SampleRecordChanges` (closed, `note` ≤ 500) in `RESOURCE_WRITERS`, `changes_for(resource, changes)`, optional `RemoteRecord.version` (`remote_record(..., version=)`, left out when None), `StaleVersion` + `stale_version_body(current)` (`{"error": {...}, "current_version": n}`). `contracts.errors`: `INVALID_BODY` (400), `STALE_VERSION` (409), `IDEMPOTENCY_KEY_REUSED` (422), `REMOTE_WRITE_STATUS`/`REMOTE_WRITE_MESSAGES`/`remote_write_error`. Three new schema files; `remote_record.json` regenerated. `interface.PeerCallFailed.data`: the peer's JSON error object (≤ 4 KiB) so the admin reads `current_version`.
- Portal: migration `integration/0007_remote_writes` (`SampleRecord.version` bigint default 1, `RemoteAccess.operation` read/write default read, `RemoteWrite`: actor_uuid + idempotency_key unique, resource, object_id, request_hash (SHA-256 of canonical JSON of resource, expected version, changes), status, `response` encrypted JSON, created_at). Same route `/api/v1/remote/sample-record` now GET + POST. `integration.remote.remote_write` order: admin issuer → actor/region/role (403) → body (400) → field allow-list (403 `field`, refused row lists the attempted names) → closed changes model (400) → load (404) → assigned (403) → key lookup (same hash: stored answer re-filtered to what the actor may read now; other: 422, logged refused) → granted `write` row committed (durable; 503 if not) → one transaction: claim the key (insert under the unique constraint, so a concurrent same-key retry blocks then replays), `sample.update_sample_record` (select_for_update, version check → `StaleVersion`, apply, version + 1), store the answer → 200 `no-store`. Stale: rollback + refused row `stale_version` + 409 with `current_version`. Reads now carry `version`. `purge_writes()` (7 days) called by `purge_outbox`, which now prints `... events deleted, <n> remote write keys deleted`. `seed_sample_record` sets version 1. Runbook "Remote writes" section, jobs.conf comment.
- Admin: `interface.remote.write(...)` validates `changes` with `changes_for` (no call when invalid), `signed_post` with the scope, `RemoteWriteFailed` (subclass of `RemoteReadFailed`) per code with `current_version` for 409; answer check shared with `read` (`_checked`), a write answer must carry `version`; `read` now returns `version` too. Page: after a read, users with CHANGE on `patients` and writable `note` get a note form (hidden region, object_id, expected_version, idempotency_key = uuid4 per render) posting to new `POST /patients/write/` (`patients_write`, `area_required(patients, CHANGE)`, added to `audit.registry.AUDITED_VIEWS` with an exercise). Applied: new record + fresh form; 409: "changed meanwhile" + Reload button, no form; other failures re-show the same bound form (same key) so resending is a retry. Audit: new action `remote_write` (migration `audit/0003`), `audit.service.record_remote` writes target `remote.sample_record`, object id, `before={}`, `after={"resource","region","outcome"}`. `permissions.matrix.remote_access_mismatches` also compares CHANGE roles with roles that have writable fields. README "Remote writes".
- Deviations / choices not spelled out in the plan:
  - `RemoteRecord.version` (an optional field) added to the read answer as well: the form's hidden version "from the last read" needs it. Additive; reads without it parse as before, and no form is offered then.
  - `invalid_body` (400) added to the core write codes (the portal's re-sync endpoints already used that code locally); 400s are logged refused.
  - Refused write rows carry the attempted field names (names only), so a `field` refusal shows which field.
  - A replay answered from the key is logged (review patch 5): a durable granted `write` row with `access_reason` `replay` (now `replay:<names>`) before the stored answer leaves (503 if it cannot be written); the 422 is logged refused. A request that loses the in-flight race replays without a second row (its own granted row is already written).
  - The key is claimed before the record lock (in the same transaction) so a retry arriving while the first is in flight replays instead of getting 409; this is what the "retried after a timeout" criterion needs.
  - The admin audit entry is written after the region answers, in its own transaction; a failure there is logged (error class) and not raised, since the region's change cannot be undone. Every write sent gets one entry, outcome `applied` or the failure code. Applied: `object_id` is the answer's record. Failed: `object_id` is empty and `after` adds `claimed_object_id` (the form's). A write refused before any call (`RemoteWriteFailed.sent` False: malformed change, no scope, region not set up, no signing key) gets no entry (choice for review patch 12).
  - The write request has an optional `object_id` (review patch 1): with it the region changes that record or answers 404, never another (the reseed case is tested); without it, the newest sample record. The admin always sends the form's.
  - The "resend the same request (command)" manual check is a browser resubmit of the answer page (same form, same key); no new admin command.
  - Core `PeerCallFailed.data` is a small API addition the plan did not name.

- Review patches (2026-11-09), applied in all three checkouts:
  - Core: optional `object_id` on `RemoteWriteRequest` (left out when None; README compatibility rule: a field added to the closed request is sent only once the portals accept it, so portals first; a new required field needs portals accepting both shapes or a new API version). `remote_write_error('stale_version')` raises ValueError; `stale_version_body` builds the 409 itself. `decide_write` raises TypeError for a `str`/`bytes` `names`. Tests: `invalid_body` error, a 0.15-shaped reader parsing an answer with `version`, `PeerCallFailed.data` None for non-JSON, > 4 KiB and a JSON list. Wheel rebuilt and re-vendored (sha256 aba3f248…aacf5).
  - Portal: replay logging (above); migration 0007 now sets `db_default` (`operation` 'read', `version` 1) as `accounts/0014` does, with a test inserting rows the old way; `object_id` in `request_hash`, `load(object_id)` and a 404 for any other record; tests for the reseed case, a key reused for another record, two granted rows in the in-flight test, the same key in flight with another body (422, PostgreSQL), and a failure in `update` or `build` after the claim rolling the key back so a retry applies. Runbook updated.
  - Admin: the form's `object_id` is sent; an answer for another record shows a warning; `idempotency_key_reused` shows Reload and no form like `stale_version`; failure pages answer 409 (stale, key reused), 403, 400, 404, 503 (unavailable, log unavailable, not set up) or 502 (unauthorized, bad answer); `expected_version` max 2^63-1; the note normalises `\r\n` to `\n` before the length check; the invalid-form page names no region when there is none and shows the form's first error. Tests: audit exercise asserts `applied`, statuses, not-sent writes unaudited, a region outside the scope and one without a site (no call, no entry), a real 409 checked for AD-23. README updated.
- Returned field names (user decision, 2026-11-09): a granted `write` row's `fields` are the names the answer returns, as a read's (`decision.fields`; for a replay, what the actor reads now). No new column: `access_reason` carries the changed names, `write:<names>` for an applied write and `replay:<names>` for a replay (`integration.remote.access_reason_for`; names that do not fit the 50-character column are counted as `+N`; the full list is the request's `changes`, which are at most the role's writable fields). Refused rows keep the attempted names. Tests and runbook updated; the admin site has nothing that depends on it (its suite was not rerun for this change).

## Verification results

After the returned-field-names change (2026-11-09, sandbox off, fresh PostgreSQL 17 cluster, since stopped and deleted; portal only, as nothing on the admin site or in core depends on it):
- Portal, SQLite: 2254 passed, 8 skipped, 3 failed (the 3 known PostgreSQL-only failures in `accounts/tests/test_crypto.py`). Portal, PostgreSQL 17: 2265 passed, 0 skipped. `makemigrations --check`: no changes.

After the review patches (2026-11-09, sandbox off, wheel rebuilt, fresh PostgreSQL 17 cluster, since stopped and deleted):
- mytp-core: 1193 passed.
- Portal, SQLite: 2251 passed, 8 skipped, 3 failed (the 3 known PostgreSQL-only failures in `accounts/tests/test_crypto.py`). Portal, PostgreSQL 17: 2262 passed, 0 skipped.
- Admin, SQLite: 1013 passed, 8 skipped. Admin, PostgreSQL 17: 1021 passed.
- `manage.py check` and `makemigrations --check`: clean on both sites.

First run, before the review patches:

Run 2026-11-09, sandbox off, wheel `mytp_core-0.16.0-py3-none-any.whl` built from ../mytp-core's working tree and installed in both venvs.
- mytp-core: 1185 passed.
- Portal, SQLite: 2243 passed, 7 skipped (PostgreSQL-only), 4 failed: the 3 known PostgreSQL-only failures in `accounts/tests/test_crypto.py` plus `accounts/tests/test_password_reset.py::test_link_redirects_to_the_new_password_form_without_its_secrets`, an existing flake (the 2-character uid `MQ` occurring by chance in the page); it passed 5 of 5 reruns.
- Portal, PostgreSQL 17 (throwaway cluster, since deleted): 2254 passed, 0 skipped (including both concurrency tests: two users from version 1 → one 200 and one 409; the same key twice in flight → equal answers, applied once).
- Admin, SQLite: 1000 passed, 8 skipped. Admin, PostgreSQL 17: 1008 passed.
- `manage.py check` and `makemigrations --check`: clean on both sites.

## Plan Change Log

- 2026-11-09: frozen decision on `RemoteAccess.operation` changed by the user, from "the written field names in `fields`" to "the returned field names in `fields`, as reads (user, 2026-11-09)". Granted write rows now list the names the answer returns; the changed names go in `access_reason` (`write:note`, `replay:note` for a replay); refused rows still list the attempted names.

## Review Triage Log

Opus reviews, one per repo (2026-11-09); no blocking defects:
- patch (portal, medium): a replay returned the stored answer (patient name included) without an access row → a durable granted `write` row (`access_reason` `replay`) before any replay, 503 when it cannot be written.
- patch (portal, medium): migration 0007's NOT NULL columns had no database default (old code inserting during a rollback would fail every read and download) → `db_default`s, rollback test.
- patch (all, low→medium): writes were not tied to a record (deviation 7) → optional `object_id` in the request, in the request hash, 404 for another record, 422 for a key reused on another record; the admin sends it and warns on a mismatch; a failed write audits the claimed id only.
- patch (core, low): `remote_write_error('stale_version')` could build a 409 without `current_version`; `decide_write` with a `str`; extra contract and `PeerCallFailed.data` tests.
- patch (admin, low): `idempotency_key_reused` shows Reload; failure pages carry real statuses; no audit entry for a write never sent (`RemoteWriteFailed.sent`); `expected_version` bounded; `\r\n` counted as one character; stronger audit, region and 409/AD-23 tests; invalid-form page texts.
- patch (portal, low): in-flight test proves both requests passed the lookup; same key in flight with another body → 422; a failure after the claim rolls the key back.
- resolved by the user (2026-11-09): returned fields. A write row's `fields` name the written fields only, while the answer also returns other readable fields (reads log the returned ones).

## Verification

**Commands:**
- mytp-core, admin and portal suites — all pass; CI on PostgreSQL.

**Manual checks (staging):**
- On admindev as a Clinical Manager: change the sample note on dev and on dev-ae; resend the same request (command) → same answer, one change; a write from an old version → 409; each write in that region's `RemoteAccess`; admindev's database holds no note text.
