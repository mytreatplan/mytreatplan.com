---
title: 'Client record sync and erasure'
type: 'feature'
ticket: '8'
created: '2026-11-06'
status: 'done'
baseline_revision: 'e8ba2d25506c8ed2a60f26b15f27f4ab616ea389'
route: 'full'
route_source: 'auto'
risk: 'high'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** I8 (AD-2, AD-15, AD-18): the admin site has no copy of the doctors (clients) registered in each region, and nothing removes such a copy when a region erases a doctor.

**Approach:** Three repos, three PRs, as 4.3/4.4. **mytp-core 0.11.0**: closed writer payloads `client.updated` (full state) and `client.erased` (tombstone) plus their reader models; the staging-only `sample.recorded` type goes (4.3's rule: removed when entry 8's first real type exists). **Portal** (`accounts` + `integration`): one service `accounts.client_sync` called in the transaction of every client write (signup, Django admin change, erasure) that bumps the account's `sync_version` and records the event in the outbox; a signed re-sync endpoint and a backfill command. **Admin**: a `clients` app holding the client record encrypted with the admin's keys, applied by the event ingest, deleted on `client.erased`; a re-sync request command.

## Boundaries & Constraints

**Always:**
- Payload `client.updated` (closed, `check_central_payload`): `title`, `first_name`, `middle_name`, `last_name`, `email`, `phone` (E.164 or empty), `country` (ISO alpha-2), `home_region`, `active`, `joined_at` (Z), `how_found` (the signup choice code, or empty; `how_found_other` free text is not sent). Sent in clear over the signed channel; never ciphertext. `client.erased`: empty payload. `aggregate_id` = the account's `uid`; `aggregate_version` = the account's new `sync_version`.
- Portal: `User.sync_version` (bigint, default 0, migration). `client_sync.emit(user)` runs inside the caller's `transaction.atomic()`, locks the user row, bumps `sync_version`, and records `client.erased` when the account is erased, `client.updated` otherwise. Called from: signup `_create_account` (after the save), `accounts/admin.py` `UserAdmin.save_model` (add and change), `erasure.erase_user` (inside its atomic block). Not called for password, session or re-encryption writes. `purge_erased` sends nothing. Staff accounts (`is_staff` or `is_superuser`) are not clients: `emit` records nothing for them, except `client.erased` when an account that was a client becomes staff (sync_version > 0).
- Portal re-sync: `POST /api/v1/clients/resync` signed by the admin site only (as the config nudge), body `{"aggregate_id": "<uid>"}` → 202 and a task that calls `emit` for that account (unknown uid → 404 `unknown_client`). Command `sync_clients [--all | <uid>...]` emits current state (backfill of existing accounts on staging).
- Admin: `clients.ClientRecord(uid pk, region FK (the signing site's Region), home_region, title, first/middle/last name, email, phone — encrypted with `mytp_core.crypto` fields and blind indexes for email and phone as `staff.User` —, country, active, joined_at, version, updated_at)`. Appliers in `interface/events.py`: `client.updated` → upsert (payload re-validated with the reader model); `client.erased` → delete the record (the `AggregateVersion` row stays, so later lower versions are `stale`). Nothing of the payload in logs or FailedEvent.
- Admin re-sync: command `resync_client <uid> [--region <code>]` (region from the record when it exists) → one signed POST to that region's instance; outcome printed; logged.
- Sample removal: mytp-core payload, portal `emit_sample_event`, check E004 and `INTERFACE_SAMPLE_EVENTS` (both sites), admin applier and its check, README/runbook lines.

- Decision (user, 2026-11-06): portal staff accounts are not clients and are never sent; a client made staff is sent as `client.erased`.
- Decision (user, 2026-11-06): `how_found` (the signup choice) is part of the payload, for sales; its free-text "other" is not.
- Decision (user, 2026-11-06): the re-sync is triggered by the admin command only; a button comes with epic-sales-finance's client page.
- Decision (user, 2026-11-06): one plan, three PRs (mytp-core 0.11.0 → admin, deployed first → portal), kept whole (~2200 tokens, above the size target).

- Decision (user, 2026-11-06, after review): outbox payloads are stored encrypted with the portal's field keys (mytp-core's encrypted JSON field) and decrypted only to send (this changes the 4.3 outbox's storage, allowed here); when an account is erased, its still-pending `client.updated` rows are deleted before `client.erased` is recorded; `purge_erased` deletes every outbox row of the purged accounts; the full no-plaintext signup test is restored.

**Never:** no client list or detail UI on the admin (epic-sales-finance); no fiscal entities or addresses (epic-doctor-profile extends the payload); no patient data; no admin-initiated erasure (only the region erases, AD-18); no change to outbox, ingest order, `AggregateVersion` semantics or erasure retention.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Signup | doctor completes signup on dev / dev-ae | `client.updated` v1; admin record with region eu / ae | — |
| Change | Django admin edits name, country, active | `client.updated` v+1; record updated | — |
| Signup rolled back | signup transaction fails | no event, no version bump | — |
| Erase | `erase_user` on dev | `client.erased`; admin record deleted, version row kept | — |
| Late update | `client.updated` older than the erase arrives | `stale`; record stays deleted | — |
| Re-sync | admin `resync_client <uid>` | portal emits current state at v+1; record rebuilt | 404 unknown uid printed |
| Re-sync of erased | uid erased on the portal | `client.erased` sent again | — |
| Backfill | `sync_clients --all` | every existing account sent once | — |
| Non-client write | password reset, login, re-encryption | no event | — |
| Staff | superuser created / client made staff | nothing / `client.erased` | — |

</frozen-after-approval>

## Code Map

Repos (branch `story/4.8-client-sync` in each, already created; do not push, tag or open PRs). Baselines: mytp-core afc5fe35b16ecda9dfac1a24e332ab6851716c06 (v0.10.0), portal e8ba2d25506c8ed2a60f26b15f27f4ab616ea389, admin 9d0a4f8157f819993a5f7aea9248080034021a3e. This plan lives in the planning worktree `.../code/mytreatplan-planning` (write Implementation Notes there); code goes in the main checkouts. The vendored wheel may be built from the untagged core tree for local tests (rebuilt from tag v0.11.0 at release); update both sites' `test_vendored_core.py` and the admin's `layout/tests/test_deploy.py` wheel name. Tests: portal venv `../.venv` with the local-dev-notes command (3 known PostgreSQL-only failures); admin venv `/private/tmp/claude-501/admin-venv` (install wheels with `uv pip install --no-cache --python ... --reinstall --no-deps`; env as in the 4.4 plan's Code Map); mytp-core `../.venv`. If a local PostgreSQL 17 is available, run the concurrency tests on it as 4.4 did.

Paths relative to `.../code/` (`/Users/jadeandres/Library/CloudStorage/GoogleDrive-mytpdso@gmail.com/Mi unidad/Desarrollo Portal MyTreatplan/code/`).
- mytp-core (0.10.0): `contracts/payloads.py` `register_payload` :113 (decorator, checks nested models closed :63), `payload_model_for` :136, `build_payload` :146, `SampleRecorded` :171-178 (to remove); `contracts/base.py` `WriterModel` :19 / `ContractModel` :12; `contracts/checks.py` `check_central_payload` :254 (names, email, phone allowed; keys starting `patient` and file-like keys refused); `contracts/events.py` `EventEnvelope` :109; schemas + drift test as before.
- Portal: `accounts/models.py` `User` :65-164 (`uid` :87, `home_region` :96, encrypted `email` :98, names :101-103, `phone` :105; `title`, `country`, `date_joined`; `erased_at` :115); `signup/views.py` `_create_account` :398 (atomic :406, save :421); `accounts/admin.py` `UserAdmin` :94 (readonly :119; action `erase_accounts` :155-187); `accounts/erasure.py` `erase_user` :69 (atomic + select_for_update; save ~:91-96), `purge_erased` :119; `accounts/managers.py` `_create_user` :30 (createsuperuser — staff, never synced); `integration/outbox.py` `record`; `integration/views.py` `config_nudge` :30-50 (admin-only signed POST pattern), `urls.py`, `tasks.py`; `emit_sample_event`, `checks.py` E004.
- Admin: `interface/events.py` `appliers()` :55, `_apply_version_only` :51, `SAMPLE_RECORDED` import :36, `ingest` :111, `_lock_version` :94, `aggregate_kind` :88; `interface/models.py` `AggregateVersion` :88; `staff/models.py` :12-13, :122-131, `set_blind_indexes` :186, `AUDIT_EXCLUDED` :151 (encrypted field pattern); `interface/config.py` `nudge_portals` :126 (signed POST pattern); `Region` (site, instance_url); `permissions/areas.py:56` `clients` area exists (no UI here).
- Release as 4.4: tag v0.11.0, vendor in both sites; admin deploys first.

## Tasks & Acceptance

**Execution:**
- [ ] mytp-core: `ClientUpdated`, `ClientErased` writer models registered + reader models, schemas, remove `sample.recorded`, tests, 0.11.0, README.
- [ ] Admin: `clients` app (model, encrypted fields, migration), appliers, `resync_client`, remove the sample applier/check/setting, README, tests (every matrix row on the admin side; personal fields are ciphertext in the database).
- [ ] Portal: `sync_version` + migration, `accounts/client_sync.py`, calls in the three write paths, resync endpoint + task, `sync_clients`, remove the sample command/check/setting, runbook, tests (every matrix row; an emit inside a rolled-back transaction leaves nothing).
- [ ] Both sites: vendor 0.11.0.

**Acceptance Criteria:**
- Given a client record on admindev, when its row is read straight from the database, then names, email and phone are ciphertext.
- Given two concurrent changes of one account on PostgreSQL, when both commit, then their versions are distinct and the admin ends with the later state.

## Implementation Notes

2026-11-06, implementer. Nothing committed, tagged or pushed in any repo (branches `story/4.8-client-sync`, working trees only).

- mytp-core 0.11.0: `mytp_core/contracts/clients.py` (`CLIENT_UPDATED`, `CLIENT_ERASED`, writer models `ClientUpdated` / `ClientErased`, reader models `ClientUpdatedReader` / `ClientErasedReader`, `CLIENT_READERS`), registered in `payloads.py`; `sample.recorded` (`SAMPLE_RECORDED`, `SampleRecorded`, `payload_sample_recorded.json`) removed; schema files `payload_client_updated.json`, `payload_client_erased.json`; version bump; README ("Client record events", compatibility line). Field rules: `title`/`how_found` codes `^[a-z0-9_]*$` ≤ 30 or empty; names ≤ 150; email 3–254 with one `@`; phone `^(\+[1-9][0-9]{1,14})?$`; country `^([A-Z]{2})?$`; `home_region` a region code (required); `active` strict bool; `joined_at` UTC `Z` (a non-UTC offset is refused). Writer and reader declare the same fields (a test compares them).
- Admin: app `clients` (`ClientRecord` + migration 0001; names, email, phone with `mytp_core.crypto` fields; `email_index`/`phone_index` blind indexes, not unique), command `resync_client`, logger `clients.resync`; `interface/events.py` appliers are now `Applier(reader, apply)`; `interface/checks.py` (E001) and `INTERFACE_SAMPLE_EVENTS` removed; README ("Client records"), `settings.env.example`, `test_vendored_core.py`, `layout/tests/test_deploy.py`.
- Portal: `User.sync_version` (migration 0014), `accounts/client_sync.py` (`emit`, `resync`, `payload_of`), calls in `signup.views._create_account`, `UserAdmin.save_model`, `erasure.erase_user`; `POST /api/v1/clients/resync` (`integration.views.clients_resync`), task `resync_client_task`, logger `integration.clients`; command `accounts/management/commands/sync_clients.py`; `emit_sample_event`, check E004 and `INTERFACE_SAMPLE_EVENTS` removed (settings, `settings.env.example`, `deploy/staging.env.example`); runbook `deploy/README.md` "Client records" (replaces "The staging sample event").
- Interpretations (for review):
  1. `ClientRecord` also stores `how_found` (not in the plan's field list): the payload carries it "for sales" and dropping it on arrival would lose it. Plain text (a code, not personal data).
  2. Ingest: a type the site applies is re-validated with its reader model before the event id is recorded; a refusal is `rejected` (`invalid_payload`) with a FailedEvent without the payload (as `region_mismatch`). The existing steps keep their order; this is a new step between the FailedEvent check and recording the id, so nothing is recorded or applied for a bad payload.
  3. Lost-update guard: `User.save()` without `update_fields` on a loaded row now saves every field except `sync_version` (and deferred ones). Without it the Django admin's save (object fetched before a concurrent re-sync) would write back an older `sync_version`, and the next version would repeat one the admin site already holds (`stale`). Only `client_sync` writes it (`update()` under the row lock). Side effect: saving an instance whose row was deleted now raises instead of re-inserting.
  4. `sync_version` has `db_default=0` too (rows the previous release inserts while the deploy migrates).
  5. A staff account with `sync_version` > 0 sends `client.erased` on every admin save (repeated tombstones, harmless); made non-staff again it sends `client.updated`. An erasure always sends `client.erased`, even at `sync_version` 0.
  6. Re-sync endpoint: 404 `unknown_client` also for a staff account never sent (nothing would be emitted); 400 `invalid_body` for a body that is not `{"aggregate_id": "<canonical uuid>"}`; only the admin site (other signed peers 401, as the nudge, via a shared `_not_the_admin_site`). The task returns `'unknown'` for an account purged after the 202.
  7. `resync_client`: `--region` is required without a record and must match the record's region when both exist.
  8. `home_region` '' (a row inserted by the previous release, accounts 0013) is sent as the instance region.
- RISK (needs a decision): the outbox stores the `client.updated` payload in clear (`integration_outboxevent.payload`, JSON) until it is delivered and purged (30 days); pending and rejected rows are kept indefinitely. The portal otherwise never stores email, names or mobile in clear. `signup/tests/test_wizard.py::test_no_table_holds_the_plaintext_…` now skips that one table for email/names/mobile (still checks password and codes everywhere). A fix (e.g. `OutboxEvent.payload` as mytp-core's `EncryptedJSONField`, decrypted at send time) is an outbox change, which the plan's Never list excludes, so it was not done.
- Review fixes (2026-11-06, after the user's "after review" decision on the outbox):
  - Portal outbox: `OutboxEvent.payload` is mytp-core's `EncryptedJSONField` (field keys); migration `integration/0005_outbox_payload_encrypted` encrypts existing rows (rename → add → copy → drop → not null; reversible). The dispatcher takes rows with `defer('payload')`, so the payload is decrypted only when `envelope_of` builds the envelope. The RISK bullet above is resolved; the signup no-plaintext test checks every table again, the outbox included. Note: the rename means the previous release cannot insert outbox rows during the seconds the migration runs.
  - Erase: `client_sync.emit` deletes the aggregate's still-pending `client.updated` rows before recording `client.erased` (same transaction; also when a client is made staff). An account erased (or made staff) with `sync_version == 0` records nothing; its re-sync is 404 `unknown_client`.
  - `purge_erased` deletes every outbox row (any state) of the purged accounts' uids in the purge transaction.
  - `sync_clients`: each account in its own try/except (ValueError / OutboxError), failures named by uid on stderr, the others sent, non-zero exit at the end.
  - Re-sync endpoint: the body must be exactly `{"aggregate_id": "<uuid>"}` (extra keys → 400 `invalid_body`).
  - Tests: a password reset (`NewPasswordForm.save`) and `erase_user` with stale instances keep `sync_version` (only `emit` moves it).
  - Admin ingest: 4.3's order is restored; the reader validation moved into the client appliers (after the version check). Appliers are plain `applier(envelope)` again and may raise `events.Refused(reason)`: the id and the version bump are rolled back (a savepoint around steps 5–6) and the event is `rejected` with a FailedEvent without payload. Reasons: `invalid_payload`; `region_mismatch` when an existing record belongs to another region than the envelope's (no other region's site can move or delete it). Tests for update and erase, an invalid payload keeping the version, and the re-sync answer chains (rebuilt record; erased record stays deleted).
  - mytp-core `Email`: 3–254 characters and a non-empty domain after the last `@` (`^[\s\S]*@[^@]+$`), so quoted local parts pass; schema re-exported; wheel rebuilt and re-vendored in both sites (sha256 4f9b882e0e75…).
  - Covering tests run: core `tests/test_contracts.py` 248; admin `clients` + `interface/tests/test_events.py` 63 on PostgreSQL 17, 59 + 4 skips on SQLite; portal `test_client_sync`, `test_erasure`, `test_outbox`, `test_wizard`, `test_vendored_core` 169 on PostgreSQL 17, 165 + 4 skips on SQLite; `makemigrations --check` clean on the portal.
- Vendoring: both sites carry `vendor/mytp_core-0.11.0-py3-none-any.whl` built with `pip wheel` from the uncommitted mytp-core working tree (SOURCE_DATE_EPOCH = afc5fe3's commit time; sha256 d3e206009a18…). At release: commit mytp-core, tag v0.11.0, then `MYTP_CORE_REPO=../mytp-core scripts/vendor-mytp-core.sh v0.11.0` in both sites; `test_vendored_core.py` and the admin's `layout/tests/test_deploy.py` already expect 0.11.0. Deploy order: admin first (it accepts `client.*`), then the portal; then remove `INTERFACE_SAMPLE_EVENTS` from both settings.env and run `sync_clients --all` on dev and dev-ae.
- Verification run: mytp-core 981 passed. Admin 836 passed + 5 PostgreSQL-only skips (SQLite); 841 passed on a throwaway local PostgreSQL 17 (concurrency tests included). Portal 1918 passed + 3 known PostgreSQL-only failures + 5 skips (SQLite); 1926 passed on PostgreSQL 17 (both new concurrency tests ran). `makemigrations --check` clean in both sites.

- Release (2026-11-06): review fixes applied (all `patch` rows of the triage log and the user's outbox decision); full suites: mytp-core 989, admin 842 (5 PostgreSQL-only skips), portal 1927 (+3 known PostgreSQL-only failures, 5 skips); PostgreSQL 17 runs by the implementer passed. mytp-core 6c47da3 tagged v0.11.0; both sites vendor the wheel of the tag (sha256 e320f7942055…). PRs: mytp-admin (deploy first), then the portal. Note: the outbox migration renames the payload column, so outbox writes of the previous release fail during the deploy's migration step (a signup in that window fails and can be retried).

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, opus, one reviewer per repo, 2026-11-06): 14 findings. medium 4, low 7, intent_gap 1 (resolved by the user), kept 1, rejected 1.

| # | Repo | Finding | Verdict | Route | Evidence / action |
|---|------|---------|---------|-------|-------------------|
| 1 | portal | Outbox keeps email, names, mobile in clear (pending/rejected rows forever, also after purge); the no-plaintext signup test was narrowed | medium | intent_gap → resolved | User decision "after review": encrypt outbox payloads, drop pending updates on erase, delete outbox rows on purge, restore the test. |
| 2 | admin | Another region can overwrite or delete a record (no check of the record's region) | medium | patch | Applier refuses (`region_mismatch`) when the record's region differs, with the version row left unchanged (rollback). |
| 3 | portal | `sync_clients --all` stops at the first account whose payload fails the contract | medium | patch | Per-account try/except, uid named, others sent, non-zero exit at the end. |
| 4 | core | `Email` pattern refuses addresses Django accepts (quoted local parts) → signup rolls back | medium | patch | Same rule as the portal: length limits and one `@`-separated domain via Django-compatible check, or no pattern beyond length + `@`. |
| 5 | admin | Payload re-validation added as a new ingest step before the id is stored (Never: no ingest order change) | low | patch | Move the reader validation into the client appliers (after the version check); 4.3's order unchanged. |
| 6 | portal | An erased staff account never sent is sent as `client.erased` (erase clears is_staff first) | low | patch | Erased with `sync_version == 0` → nothing; same in `sync_clients` and the re-sync view. |
| 7 | portal | Re-sync endpoint accepts extra body keys | low | patch | Closed body: exactly `aggregate_id`, else 400 `invalid_body`. |
| 8 | portal | `User.save()` override affects every save; untested on password reset / erasure paths | low | patch | Tests for both paths through the new save. |
| 9 | admin | No test chains a re-sync answer to a rebuilt record | low | patch | Test added. |
| 10 | admin | `how_found` column not in the plan's field list | low | kept | Follows the user's decision to send `how_found` for sales; recorded here. |
| 11 | admin | Re-sync of an erased client only covered as erased v1 → v2 | low | rejected | Covered by #9's chained test and the existing tombstone tests. |

## Verification

**Commands:**
- mytp-core, admin and portal suites (local dev notes) — all pass; CI on PostgreSQL runs the concurrency test.

**Manual checks (staging, after both deploys):**
- Remove `INTERFACE_SAMPLE_EVENTS` from dev's and admindev's settings.env; `sync_clients --all` on dev and dev-ae → admindev holds their clients with regions eu / ae.
- A signup on dev and on dev-ae → a client on admindev each; a Django-admin name change → updated; `resync_client <uid>` → rebuilt; erase on dev → removed from admindev; a raw database read shows ciphertext.

## Staging

2026-11-06, run by Claude on the staging host with the user's permission (admin #17 run 42220938758, portal #72 run 42236203609 deployed green):
1. `INTERFACE_SAMPLE_EVENTS` removed from dev's and admindev's settings.env (without printing them); both restarted and active.
2. `sync_clients --all`: dev 1 skipped (its only account is staff), dev-ae 1 `client.updated` → admindev holds 1 client in region `ae`; delivered.
3. A test doctor (ES) created on dev through `UserAdmin.save_model` (the Django-admin add path; a browser signup was not run) → `client.updated` v1 → admindev record (region eu, home eu, names, ES, active, v1).
4. Its first name changed through `UserAdmin.save_model` (change) → v2 → record updated.
5. `resync_client <uid>` on admindev → HTTP 202 → v3, record rebuilt.
6. Raw database reads: admindev's `clients_clientrecord` names and email hold no plain text (ciphertext); dev's outbox payloads of that account hold no plain text.
7. `erase_user` on dev → `client.erased` v4 → admindev record deleted, its version row kept (client, 4).
Note: `check --deploy` on dev shows the existing `mytp_publicsite.E001` (draft legal texts, LEGAL_TEXTS_FINAL False), unrelated to this story.
