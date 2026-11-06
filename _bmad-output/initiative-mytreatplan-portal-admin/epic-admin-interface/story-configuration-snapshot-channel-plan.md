---
title: 'Configuration snapshot channel'
type: 'feature'
ticket: '4'
created: '2026-11-05'
status: 'done'
baseline_revision: 'c6c6541aba08b0833236fbcb72baf2c3085b2fb5'
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

**Problem:** I5 (AD-3 item 1, AD-4, AD-12, AD-22): regions need admin-owned configuration (first the admin directory: who an admin actor UUID is) and keep working when the admin site is down; no channel carries it yet.

**Approach:** Three repos, built in order, three PRs. **mytp-core 0.10.0**: the snapshot contract (`ConfigSnapshot`, `AdminDirectoryEntry`), `SNAPSHOT_READER` format number, and `sign_snapshot` / `verify_snapshot` (Ed25519 JWT over the snapshot's canonical JSON hash). **Admin** (`interface`): a stored snapshot rebuilt with a higher version in the same transaction as every directory change, served signed at `GET /api/v1/config`, and a signed nudge to every paired portal after commit. **Portal** (`integration`): `pull_config` (job every 5 minutes, and a task enqueued by the signed nudge endpoint) that verifies, applies a higher version atomically, and otherwise keeps the current one.

## Boundaries & Constraints

**Always:**
- Snapshot body: `{version: int ≥ 1, min_reader: int, generated_at: Z-time, admin_directory: [{id, display_name, title, active, time_zone}]}`. Readers ignore unknown fields (later sections: catalogue, billing rules, vouchers, country map — epic-catalogue).
- Response: `{"snapshot": {...}, "signature": "<jwt>"}`; JWT EdDSA by the admin's interface key, claims `iss` (admin site), `aud` (the requesting site), `iat`, `exp` ≤ 60 s, `typ: "config"`, `ver` (= snapshot version), `sha` (sha256 of the snapshot's canonical JSON). Verified against the admin's peer keys (kid) before anything is applied.
- Admin: `ConfigSnapshot` (one row: version, body, built_at), rebuilt under `select_for_update` in the transaction of `create_user`, `update_user`, `deactivate_user`, `reactivate_user`, `delete_user`, `update_profile` (`staff/service.py`), only when a directory field changed; a data migration builds version 1. Nudge: a task enqueued on commit, one signed `POST /api/v1/config/nudge` per `Region` with a `site`, failures logged only (the pull covers them).
- Portal: `ConfigState` (one row: version, applied_at, last_pull_at, last_success_at, last_error code, warned_at — the 1-hour warning saved before sending, as the outbox's 24-hour one) and `AdminDirectoryEntry` rows (id pk, display_name, title, active, time_zone). Apply in one transaction with the state row locked: version > current → replace the directory, store version; otherwise nothing changes. `min_reader` > `SNAPSHOT_READER` → keep the current one, log a warning (once per version). Nudge endpoint answers 202 and enqueues the pull; repeated nudges coalesce to one pending pull.
- Every refusal (bad/absent signature, `sha` or `ver` mismatch, lower/equal version, invalid body, admin unreachable) leaves the applied snapshot untouched and records `last_error` (code only). Job `pullconfig` every 5 minutes (`*-*-* *:00/5:00 UTC`); command `show_config` prints version, applied time and the directory (names are not patient data).
- Interface `n`/`n−1`: path under `/api/v1/` as entry 1.

- Decision (user, 2026-11-05): deleted admin users (`deleted_at` set) stay in the directory with `active: false`, so old regional logs still show a name.
- Decision (user, 2026-11-05): when the last successful pull is older than 1 hour, one Sentry warning per outage (no snapshot contents); reset by the next successful pull.
- Decision (user, 2026-11-05): one plan, three PRs: mytp-core 0.10.0 → admin (deploys first) → portal.
- Decision (user, 2026-11-05): the plan is kept whole (~2100 tokens, above the size target).
- Decision (user, 2026-11-05, after review): a snapshot with the version already applied is a good pull (`unchanged`: nothing applied, `last_error` cleared, success time set); only a lower version is refused (`stale`). This replaces "lower/equal" in the refusal list above.

**Never:** no admin users created in a region (AD-22); no snapshot contents in logs or Sentry; no push of the snapshot body in the nudge (the portal always pulls); no catalogue/region-table payload (epic-catalogue); no UI in the portal.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Change + nudge | display name edited on admindev | snapshot v+1; dev and dev-ae apply it within seconds | — |
| Nudge lost | portal down during nudge | applied at the next 5-minute pull | — |
| Admin down | pull fails | current snapshot kept; `last_error` = network/http code | retry next run |
| Older snapshot | portal has v5, admin serves v4 | not applied | `stale` logged |
| Unsigned / tampered | signature missing, wrong key, `sha` mismatch | not applied | `bad_signature` logged |
| Newer format | `min_reader` 2, portal reads 1 | v kept | warning once per version |
| No change | edit of a non-directory field (role, phone) | version unchanged, no nudge | — |
| First pull | portal with no state | applies whatever version | — |

</frozen-after-approval>

## Code Map

Repos (branch `story/4.4-config-snapshot` in each, already created; do not push, tag or open PRs). Baselines: mytp-core c7b4997cafa8ce5663ec2413b92e52c3af46c5fd (v0.9.0), portal c6c6541aba08b0833236fbcb72baf2c3085b2fb5, admin a8e7d900d1ae0daea7b820e8615527f7ea321e70. This plan file lives in the planning worktree `.../code/mytreatplan-planning` (write Implementation Notes there); code goes in the main checkouts. The vendored wheel may be built from the untagged branch for local tests; it is rebuilt from tag v0.10.0 at release. Test commands: as in 4.3's plan (`epic-admin-interface/story-outbox-and-event-ingest-plan.md`); admin venv `/private/tmp/claude-501/admin-venv` (install the wheel with `uv pip install --python ... --reinstall --no-deps`), portal venv `../.venv`.

Paths relative to `.../code/` (`/Users/jadeandres/Library/CloudStorage/GoogleDrive-mytpdso@gmail.com/Mi unidad/Desarrollo Portal MyTreatplan/code/`):
- mytp-core (0.9.0): `mytp_core/interface.py` — `signed_get` :598 (returns `PeerResponse` :478 `.status/.data`), `PeerCallFailed` :124, `InterfaceError` :101, `VerificationFailed` :113, signing key/keyring helpers used by `sign_request`; `contracts/base.py` `ContractModel` :12 (reader), `WriterModel` :19; `contracts/events.py` `utc_text`; `contracts/__init__.py` `SCHEMA_MODELS` :39 + `schemas/*.json` + drift test `tests/test_contracts.py:304`; `__init__.py:22` version.
- Admin `mytp-admin`: `staff/models.py` `User` :97 (`id`, `display_name`, `title`, `is_active`, `time_zone`, `deleted_at` :142), `Region` :55 (`site`, `instance_url`); `staff/service.py` writes :62–:219, `update_profile` :287, `EDITABLE_FIELDS` :43; `interface/views.py` ping :14 (signed GET pattern), `calls.py` `signed_endpoint` :77, `urls.py`, `models.py` (`AggregateVersion` :88 pattern); outbound call pattern `interface/management/commands/ping_peer.py`; tasks via `mytp_core.tasks.enqueue_on_commit` (example `layout/management/commands/enqueue_sample_task.py`); tests `interface/tests/test_ping.py` seeds :27–33, `signed()`, `FakePortal` :284.
- Portal `mytreatplan.com`: `integration/outbox.py` `admin_site()` :174 (split out the base URL: `admin_site_url()`), `tasks.py` (`@task`, coalescing like `_nudge_once` :134), `views.py:13` ping, `urls.py`, `calls.py:65` `signed_endpoint` + `keyring()`, `checks.py`, `models.py`; `deploy/jobs.conf`; `deploy/README.md` runbook; tests `integration/tests/test_ping.py`, `test_outbox.py` `FakeAdmin` :49.
- Release as 4.3: tag mytp-core v0.10.0, vendor in both sites (update `test_vendored_core.py` and the admin's `layout/tests/test_deploy.py` wheel name).

## Tasks & Acceptance

**Execution:**
- [ ] mytp-core: contract models + JSON Schemas, `SNAPSHOT_READER = 1`, `sign_snapshot` / `verify_snapshot` (refusal codes), tests, 0.10.0, README.
- [ ] Admin `interface` + `staff/service.py`: `ConfigSnapshot` + migration (data migration v1), rebuild hook, `GET /api/v1/config`, nudge task and command `nudge_config`, README, tests (rebuild only on directory change, concurrent edits give distinct increasing versions on PostgreSQL, response verifies).
- [ ] Portal `integration`: `ConfigState`, `AdminDirectoryEntry` + migration, `config.pull()`, command `pull_config`, task + `POST /api/v1/config/nudge`, `show_config`, jobs.conf, runbook, tests for every matrix row.
- [ ] Both sites: vendor 0.10.0.

**Acceptance Criteria:**
- Given a pull fails halfway through applying, when the transaction rolls back, then the previous directory and version are intact.
- Given two pulls run at once on PostgreSQL, when both fetch the same new version, then it is applied once and the version never goes down.

## Implementation Notes

2026-11-05, implementer. Nothing committed, tagged or pushed in any repo (branches `story/4.4-config-snapshot`, working trees only).

- mytp-core 0.10.0: `mytp_core/contracts/config.py` (`ConfigSnapshot`, `AdminDirectoryEntry`, `ConfigResponse`, `SNAPSHOT_READER = 1`, `canonical_json`, `snapshot_sha`; names ≤ 150, time zone 1–64, ids unique, `active` strict bool), `mytp_core/snapshots.py` (`sign_snapshot`, `verify_snapshot`, `SnapshotRefused(code, version)`), schema files `config_snapshot.json` + `config_response.json`, version bump, README ("Configuration snapshot", compatibility line), `tests/test_snapshots.py`. Refusal codes: `bad_signature` (absent/garbage token, other alg, other `iss`, unknown kid, bad signature, wrong claim types, `typ`≠config, `sha` or `ver` mismatch), `audience`, `expired` (30 s skew as entry 1), `invalid_body`, `newer_format`. The `sha` covers the snapshot object as sent (unknown fields included), so later sections need no format change. sign/verify live in a new module (not `interface.py`) because `contracts` must not import `interface`.
- Admin: `interface.ConfigSnapshot` (pk 1, check constraint) + migration 0004 (data migration builds v1); `interface/config.py` (`rebuild`, `current`, `nudge_portals`, task `nudge_portals_task`); `GET /api/v1/config` (503 `unavailable` when the key cannot be unwrapped); command `nudge_config`; README "Configuration snapshot". The hook is in `create_staff_user_audited` (covers `create_user` and `create_sysadmin`), `update_user`, `deactivate_user`, `reactivate_user`, `delete_user`, `update_profile`. `rebuild()` locks the row, rebuilds the whole directory and compares it with the stored one (so "only when a directory field changed" holds for any write, including email/role/phone edits) — a missing row (a flushed test DB) is created as v1. Nudge body is `{}`.
- Portal: `ConfigState` + `AdminDirectoryEntry` + migration 0004; `integration/config.py` (`pull`, `request_pull`); `outbox.admin_site_url()` split out; task `pull_config_task`; `POST /api/v1/config/nudge` (202 `{"status": "queued" | "already_queued"}`); commands `pull_config`, `show_config`; job `pullconfig`; runbook "Configuration snapshot"; settings comment.
- Interpretations (for review): (1) an equal version is a good pull (`unchanged`, `last_error` cleared, `last_success_at` set); only a lower one is refused (`stale`). Counting "equal" as a refusal would leave `last_error` set and fire the 1-hour outage warning in the steady state. (2) `ConfigState` has two fields beyond the plan's list: `newer_format_version` (the once-per-version warning) and `created_at` (outage start for a site that never had a good pull). (3) Coalescing uses `cache.add('mytp:config:pull-pending', timeout=300)` (the database cache's atomic add), cleared when the task starts; the view never imports the task backend. (4) `stale`, `newer_format` and other refusals do not count as a good pull for the outage warning. (5) Additional failure codes: `not_configured` (no admin site/peer keys/signing key; the command fails), `apply_failed` (logged with the exception type only, never its text), plus the outbox's `network`/`peer_url`/`http_<status>[_<code>]`/`bad_answer`.
- Vendoring: both sites carry `vendor/mytp_core-0.10.0-py3-none-any.whl` built with `uv build --wheel` from the uncommitted mytp-core working tree (SOURCE_DATE_EPOCH = mytp-core HEAD c7b4997's commit time). The admin moves from 0.8.0 (it never vendored 0.9.0). At release: commit mytp-core, tag v0.10.0, then `MYTP_CORE_REPO=../mytp-core scripts/vendor-mytp-core.sh v0.10.0` in both sites (the admin repo has the same script) to replace these wheels; `test_vendored_core.py` and the admin's `layout/tests/test_deploy.py` already expect 0.10.0.
- Verification run: mytp-core 961 passed (SQLite). Admin 811 passed + 4 skipped (SQLite) and 815 passed (local PostgreSQL 17, concurrency tests included). Portal 1886 passed + 3 known PostgreSQL-only failures + 4 skipped (SQLite); full suite 1893 passed on local PostgreSQL 17 (no skips; both new concurrency tests ran).

- Release (2026-11-06): all 11 patch rows of the Review Triage Log applied; full suites after the fixes: mytp-core 962, admin 814 (4 PostgreSQL-only skips), portal 1889 (+3 known PostgreSQL-only failures, 4 skips). mytp-core afc5fe3 tagged v0.10.0 (CI green on master and tag); both sites vendor the wheel of the tag (sha256 61460c74…). Admin rebased onto PR #15 (0.9.0 → 0.10.0; its test_vendored_core.py now checks 0.10.0 and the snapshot API). PRs: mytp-admin (deploy first), then the portal.

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, opus, one reviewer per repo, 2026-11-05): 17 findings. high 1, medium 2, low 11, false 1, rejected low 2. One intent conflict settled by the user (equal version, decision "after review").

| # | Repo | Finding | Verdict | Route | Evidence / action |
|---|------|---------|---------|-------|-------------------|
| 1 | portal | `pull_config` raises CommandError on every refusal; `runjob` sends a Sentry error + failed check-in every 5 minutes during an outage | high | patch | Breaks the one-warning-per-outage decision; fail only for `not_configured`, print the code otherwise (as `dispatch_outbox`). |
| 2 | core | `newer_format` checked after full model validation: a real format-2 snapshot is refused as `invalid_body` without a version | medium | patch | Reproduced by the reviewer; check `min_reader` (strict int) on the raw dict once `sha` matches, before full validation. |
| 3 | portal | Equal version treated as a good pull, against "lower/equal" in the frozen block | medium | intent_gap → resolved | The user chose (a): equal = good pull (decision "after review"); no code change. |
| 4 | portal | `newer_format` also logged as "config pull refused" on every pull | low | patch | Skip the generic line for `newer_format` (its own line is once per version). |
| 5 | portal | `show_config` creates the state row (sets `created_at`, moving the outage clock) | low | patch | Read without creating. |
| 6 | portal | Nudge accepted from any signed peer, not only the admin site | low | patch | Check `iss` against `admin_site_url()` site; 401-style refusal otherwise. |
| 7 | portal | KMS unwrap failure reported as `not_configured` | low | patch | Own code `signing_key`. |
| 8 | portal | `_error_code` copied from `outbox.py` | low | patch | Reuse the outbox's. |
| 9 | both | Logger `interface.config` / `integration.config` not in `LOGGING`: INFO lines dropped on the servers | low | patch | Add both entries (found by the admin reviewer; the portal has the same gap). |
| 10 | admin | 503 on a KMS failure recorded as an accepted call | low | patch | Set the outcome as `calls._unavailable` does. |
| 11 | admin | A stored body that fails the contract gives a 500 on every pull | low | patch | Validate in `snapshot_body()` before saving (the user write fails instead). |
| 12 | admin | README "Shared code" row names the 0.5.0 wheel | low | patch | Name 0.10.0. |
| 13 | core | Schema files missing from the diff | false | rejected | The staged diff excluded `schemas/*.json` on purpose; both files exist and are committed with the change. |
| 14 | core | `ConfigResponse.snapshot` schema is free-form | low | rejected | No non-Python reader exists; the code validates the snapshot itself; the fix adds schema plumbing. |
| 15 | portal | Concurrent pulls can record a false `stale` after a newer version applied | low | rejected | Needs a job pull and a nudged pull overlapping across an admin change; one log line (no Sentry after #1); the fix adds locking logic. |

## Verification

**Commands:**
- mytp-core, admin and portal suites (local dev notes) — all pass; CI on PostgreSQL runs the concurrency tests.

**Manual checks (staging, after both deploys):**
- Re-provision dev, dev-ae, admindev (new job `pullconfig`); pair dev-ae with admindev as 4.1 (keys, peers, `ping_peer`).
- `show_config` on dev and dev-ae shows the admindev directory; edit an admin user's display name → shown on both within seconds; stop admindev's gunicorn, `pull_config` on dev: error recorded, directory kept; start it: next pull OK.
- A tampered or lower snapshot: covered by tests (no staging step).

## Staging

2026-11-06, run by Claude on the staging host with the user's permission (admin #16 run 42201632743 and portal #70 run 42203929735 deployed green):
1. Re-provisioned dev (timer `pullconfig`), dev-ae (timers `pullconfig`, `dispatchoutbox`, `purgeoutbox`) and admindev (no changes).
2. dev-ae paired with admindev: its own signing key (kid `sig20261106`, written straight into settings.env, only the public key printed), `add_peer_key ae sig20261106 … --site dev-ae` on admindev, `INTERFACE_PEERS`/`INTERFACE_PEER_KEYS` copied from dev; `check` clean; `ping_peer` both ways OK.
3. `pull_config` on dev and dev-ae: v1 applied, 3 directory entries, as admindev.
4. Display name of a test admin user changed on admindev through `staff.service.update_user` → admindev v2; nudges `POST /api/v1/config/nudge` 202 on both at 10:45:27–28 UTC; dev and dev-ae showed v2 and the new name within 20 s.
5. admindev's gunicorn stopped: `pull_config` on dev → `refused (http_502)`, exit 0, v2 kept, dev up; admindev started → `v2 unchanged`, error cleared.
Left to the user: disable the Sentry monitor `pullconfig` on dev and dev-ae (and dev-ae's `dispatchoutbox`, `purgeoutbox`) after their first run.
