---
title: 'Outbox and event ingest'
type: 'feature'
ticket: '3'
created: '2026-11-05'
status: 'done'
baseline_revision: '24bc90149b5e72c4086390f74fd692766bb89679'
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

**Problem:** I4 (AD-3 item 2, AD-18, AD-19): events (client records, contact messages, the accounting feed) must flow from each portal to the admin site reliably — exactly once in effect, in order per aggregate, surviving crashes and an admin outage — and nothing does that yet.

**Approach:** Three repos, built in order. **mytp-core 0.8.0**: `signed_post` (JSON body), a writer base model (`extra='forbid'`) and a payload registry `{type: model}` so every event type has a closed payload model, plus two parked fixes (the `fresh_code` wording; nothing else). **Portal** (`integration`): an `OutboxEvent` row written in the caller's transaction by `outbox.record(type, aggregate_id, aggregate_version, payload)`, a dispatcher that sends pending rows per aggregate in sequence order with retries kept on the row, a 30-day purge of delivered rows, and a staging-only sample event to prove the path. **Admin** (`interface`): `POST /api/v1/events` that verifies the signature, checks the envelope's region against the signing site, deduplicates on `event_id` (ids only), applies an aggregate only for a higher version, and records rejected events as failed for a Sysadmin list.

## Boundaries & Constraints

**Always:**
- One event per POST, body = the envelope JSON (`EventEnvelope`), ≤ 256 KB. The writer validates the payload against the type's registered model (`extra='forbid'`) and `check_central_payload` before the row is written; an unregistered type cannot be recorded.
- Portal row: `event_id` (uuid4), `type`, `version`, `aggregate_id`, `aggregate_version`, `sequence` (per aggregate, allocated under a row lock on the aggregate's last row, starting at 1), `occurred_at`, `payload`, `state` (pending / delivered / rejected), `attempts`, `next_attempt_at`, `last_error` (code + short text, no payload), `delivered_at`. `record()` must run inside `transaction.atomic()` (refuse otherwise) and enqueues the nudge with `transaction.on_commit`.
- Dispatcher (`dispatch_outbox`): per aggregate, only the lowest-sequence pending row is eligible (head of line: a failing event holds back that aggregate's later events, never other aggregates); rows taken with `select_for_update(skip_locked=True)` on PostgreSQL so two runs never send the same row at once; outcome per admin answer: 2xx `accepted`/`duplicate`/`stale` → delivered; 2xx `rejected` → rejected (final, not retried, admin shows it); network error, 5xx, 401, 503 → retry per the backoff decision. Idempotent: a killed run leaves rows pending and the next run resends; the admin's dedupe makes that harmless.
- Admin ingest answers 200 with `{"result": "accepted" | "duplicate" | "stale" | "rejected", "reason"?}` once the envelope is stored or refused; 400 only for an unparsable body (`error_body`), 401/503 as entry 1. Region check: `Region.objects.get(site=iss).code == envelope.region`, else `rejected` (`region_mismatch`). Dedupe: `ProcessedEvent(event_id pk, received_at)` keeps ids only; a known id → `duplicate`. Version: `AggregateVersion(type, aggregate_id, version)`; `aggregate_version` ≤ stored → `stale` (id still recorded). Unknown event type → `accepted` and ignored (AD-12), id recorded. All in one transaction.
- Failed events (admin): `FailedEvent(event_id, iss, type, aggregate_id, reason, received_at, envelope_without_payload)` — never the payload; Sysadmin-only area `interface_events` ("Interface events") with an `AdminListView` (date filter, region filter); dismiss per decision, audited.
- Sample (staging only, behind `INTERFACE_SAMPLE_EVENTS=True`, refused by a system check on production sites): event type `sample.recorded` with a closed payload `{label: str}`; portal command `emit_sample_event <aggregate uuid> <version> [--label]`; admin applies it to `AggregateVersion` only. Removed when entry 8's first real type exists.
- Jobs: portal `dispatchoutbox` every minute (`*-*-* *:00/1:00 UTC`) plus the on-commit nudge task; `purgeoutbox` daily (delivered rows > 30 days; rejected rows kept); admin `purgeprocessedevents` is not added (ids kept, AD-19).
- Parked items folded in: both sites' ping `time` in the contract form (trailing `Z`); mytp-core `policy.py` `fresh_code` docstring/comment; remove mytp-admin `staff/signin.py:121-125` workaround comment; both sites vendor mytp-core 0.8.0 (pydantic arrives).

- Decision (user, 2026-11-05): the dispatcher runs as the `dispatchoutbox` job every minute plus a nudge task enqueued on commit of each `record()`; both are idempotent sweeps; no long-running loop.
- Decision (user, 2026-11-05): retryable failures (network error, 5xx, 401, 503) are retried forever with backoff `min(2^(attempts-1) minutes, 60 minutes)`; when a row has been failing for 24 hours one Sentry warning is sent (once per row; no payload, only event id, type and last error code); rows are never dropped.
- Decision (user, 2026-11-05): a Sysadmin views failed events (date, site, type, aggregate, reason — never the payload) and can dismiss one (with confirmation, audited); no retry action.
- Decision (user, 2026-11-05): one plan over three repos (above the size target, accepted), built mytp-core 0.8.0 → portal → admin, three PRs.

- Decision (user, 2026-11-05, after review): the per-aggregate sequence is kept in a counter row `OutboxAggregate(aggregate_id pk, last_sequence)` locked by `record()` only (not the aggregate's last outbox row), so concurrent writes never fail, the dispatcher never blocks writers, and `purgeoutbox` deletes every delivered row older than 30 days, payload included.

**Never:**
- No batching, no admin→portal events (snapshots are entry 4), no payload stored in failed-event rows or logs, no real event types (entries 8, 9).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | sample event on dev | delivered once; admin `accepted`; version stored | — |
| Dispatcher killed mid-send | row stays pending, admin already stored | resend → `duplicate` → delivered; applied once | — |
| Admin down | 3 failed attempts | rows pending with attempts/last_error/next_attempt_at; delivered after admin returns | backoff |
| Order | v1 fails, v2 queued (same aggregate) | v2 not sent before v1 delivered | — |
| Other aggregate | aggregate A blocked | aggregate B still delivered | — |
| Older version | v2 applied, v1 arrives | `stale`, version stays 2 | — |
| Wrong region | dev signs an envelope with region `ae` | `rejected` (`region_mismatch`); FailedEvent listed; portal row `rejected` | — |
| Unknown type | `x.future` | `accepted`, ignored, id recorded | — |
| Invalid payload at write | extra field / patient key | `record()` raises; nothing written; caller's transaction rolls back | — |
| Purge | delivered 31 days ago / rejected 31 days ago | deleted / kept | — |

</frozen-after-approval>

## Code Map

Repos (branch `story/4.3-outbox` in each; do not push, tag or open PRs):
- mytp-core `.../code/mytp-core` (v0.7.0, baseline 540127c98e83542f363f1ccd3932abf624be4c71): `interface.py` `signed_get` :512-556 (pattern for `signed_post`; `_NoRedirect`/`_open`, http/DEBUG guard, `PeerCallFailed` :122, `request_hash` :347, `sign_request` :353, `MAX_RESPONSE_BYTES` :70-74); `contracts/base.py` `ContractModel` (reader, `extra='ignore'`) → add a writer base; `contracts/__init__.py` `SCHEMA_MODELS` :30; `contracts/events.py` `EventEnvelope` :92-101, `_z_text` :64; `contracts/checks.py` `check_central_payload` :254; `policy.py` :13, :47 (fresh_code wording); `tasks.py` `enqueue_on_commit` :54, `run_once`/`effect_done` :72,94.
- Portal `.../code/mytreatplan.com` (this plan's repo): `integration/` (`peers.py:35` `peer_url`, `calls.py`, `views.py:16` ping time, `management/commands/ping_peer.py`, `purge_interface_calls` pattern); settings `TASKS` :361-375; `deploy/jobs.conf` (minute step format, `workerheartbeat`); worker `deploy/systemd/worker.service.template:28-37`; task tests `mytp_publicsite/tests/test_tasks.py:32-34,110,130` (db_worker --batch, kill/rerun); `select_for_update` precedents `signup/views.py:407`; SQLite drops `skip_locked` silently (vendor-skip concurrency tests); `conftest.py:66-74`.
- Admin `.../code/mytp-admin` (baseline 2a3c76738d923f18e8ad9150e8d8fbbdbf3a0507): `interface/calls.py:78` `signed_endpoint` (sets `request.interface_claims`), `views.py:11,17`, `urls.py:6`, `models.py`, `service.py:19-77` (audited service pattern), `tests/test_ping.py:28-69` (signed request helpers; extend with a body); `staff/models.py:64-67` Region.site; `permissions/areas.py:43-72` (new area), `matrix.py:84-85` (Sysadmin gets every area); `lists/views.py:96-140`, `lists/sample.py:41-95` (list example); `audit/registry.py:15,34`; `staff/signin.py:121-125` (comment to remove); `deploy/jobs.conf`.
- Release: as 4.1 — mytp-core tag v0.8.0 first (CI checks tag = version), then `scripts/vendor-mytp-core.sh v0.8.0` in both sites.
- Test commands: as in 4.1's plan (portal local dev notes; admin venv; mytp-core `../.venv`).

## Tasks & Acceptance

**Execution:**
- [ ] mytp-core: `signed_post`; writer base model + payload registry (`register_payload`, `payload_model_for`) + `sample.recorded`; `policy.py` wording; tests; 0.8.0; README.
- [ ] Portal `integration`: `OutboxEvent` + migration, `outbox.record`, dispatcher (command `dispatch_outbox`, task nudge), `purge_outbox`, `emit_sample_event`, sample system check, ping `Z`, jobs.conf, settings/examples, runbook, tests (matrix rows; PostgreSQL-only concurrency test).
- [ ] Admin `interface`: ingest view, `ProcessedEvent`, `AggregateVersion`, `FailedEvent` + migrations, area + list + actions (audited), ping `Z`, remove the comment, tests.
- [ ] Both sites: vendor 0.8.0, requirements (pydantic and deps).

**Acceptance Criteria:**
- Given the dispatcher run twice concurrently on PostgreSQL, when 20 pending rows exist, then each is sent once.
- Given a FailedEvent, when a Sysadmin opens it, then no payload is shown or stored.

## Implementation Notes

## Plan Change Log

- 2026-11-05, review pass 1: (a) the sequence rule ("row lock on the aggregate's last row") failed under PostgreSQL READ COMMITTED (stale snapshot → collisions, a bounded retry failing callers), coupled writers to the dispatcher's HTTP call, and forced purge to keep last rows with payloads; the user chose a counter row (frozen decision "after review"). (b) `AggregateVersion(type, …)` is read as the aggregate kind (the event type's part before the first dot, e.g. `client` for `client.updated`/`client.erased`), so versions are per aggregate across its event types (AD-18). KEEP: head-of-line selection, one event per transaction, retry classification, the failed-event list and dismiss.

## Review Triage Log

Pass 1 (quick lens, opus, one reviewer per repo, 2026-11-05): 24 findings. high 3, medium 8, low 10, false/rejected 3.

| # | Repo | Finding | Verdict | Route | Evidence / action |
|---|------|---------|---------|-------|-------------------|
| 1 | portal | Sequence allocation on the aggregate's last row collides under READ COMMITTED; bounded retry fails the caller's transaction | high | intent_gap → resolved | Frozen rule; the user chose a counter row (`OutboxAggregate`). |
| 2 | portal | Dispatcher holds the head row's lock during the HTTP call, blocking writers of that aggregate | medium | intent_gap → resolved | Fixed by the counter row (writers no longer lock outbox rows). |
| 3 | portal | Purge keeps every aggregate's last delivered row with its payload for ever | high | intent_gap → resolved | Fixed by the counter row; all delivered rows > 30 days purged. |
| 4 | portal | "Stop after 3 failures" counts event-specific failures and can starve other aggregates | high | patch | Counts only admin-unreachable failures; test with failing rows plus a healthy aggregate. |
| 5 | admin | AggregateVersion keyed by full event type, so versions are not per aggregate (AD-18) | medium | bad_plan → patch | Plan wording ambiguous; read as the aggregate kind (Plan Change Log); migration + test. |
| 6 | admin | Concurrency test depends on a seeded region flushed by earlier transactional tests (would fail on PostgreSQL CI) | medium | patch | Uses the root `region()` helper. |
| 7 | core | `InvalidPayload` can quote values (pydantic `msg`, dict keys in the path) | medium | patch | Error type codes; placeholders for keys. |
| 8 | core | Only the top-level payload model is checked as closed | medium | patch | Registration walks nested models and refuses open types. |
| 9 | core | Oversized body raises an error shaped like a network failure (retried forever) | medium | patch | Distinct final error; portal marks the row rejected locally. |
| 10 | portal | Nudge task can hold the single worker ~50 s; one nudge per record() | medium | patch | One nudge per transaction, short sweep. |
| 11 | portal | "Recorded" log line written before commit | low | patch | On-commit. |
| 12 | portal | 24-hour warning not guaranteed once | low | patch | `warned_at` saved before sending. |
| 13 | portal | E005 misses several peers without INTERFACE_ADMIN_SITE | low | patch | Check calls `outbox.admin_site()`. |
| 14 | portal | `_error_code` labels local refusals as `network` | low | patch | Only transport failures are `network`. |
| 15 | portal | No PostgreSQL test of head-of-line under two runs or of concurrent record() | low | patch | Both added. |
| 16 | admin | A known event id resent with different content is not answered `duplicate` | low | patch | Dedupe lookup first. |
| 17 | admin | Deep JSON nesting → RecursionError → 500 | low | patch | 400 `invalid_body`. |
| 18 | admin | No PostgreSQL test of the version check under concurrency | low | patch | Two tests added. |
| 19 | core | `reason` not limited to `rejected` | low | patch | Enforced. |
| 20 | core | `utc_text` treats a None-offset tzinfo as local | low | patch | `utcoffset() is None` guard. |
| 21 | core | `json_body` non-str keys can duplicate | low | patch | Refused. |
| 22 | core | A 106-character docstring line | low | patch | Wrapped. |
| 23 | admin | 400 also for a JSON object without a canonical event_id or over 256 KB (plan: "only unparsable") | low | rejected | Only a bug in our own writer can produce these (it builds and size-checks every envelope); retried with the 24-hour warning, which surfaces the bug. Recorded here. |
| 24 | portal | Vendored wheel not built from a tag | false | rejected | Known; rebuilt from v0.8.0 at release. |

## Verification

**Commands:**
- mytp-core suite; portal suite (local dev notes); admin suite — all pass; CI on PostgreSQL runs the concurrency test.

**Manual checks (if no CLI):**
- On staging (user): set `INTERFACE_SAMPLE_EVENTS=True` on dev and admindev; `emit_sample_event` on dev → admindev stores the version; stop admindev's gunicorn, emit, see the row pending with attempts; start it, delivered; emit an older version → stale; a wrong-region test event → listed under Interface events.

## Staging

2026-11-05, run by Claude on the staging host with the user's permission (admin #14 run 42076868310 and portal #68 run 42077587477 deployed green):
1. dev re-provisioned (timers `dispatchoutbox`, `purgeoutbox` installed); admindev re-provisioned (no changes; no new admin job).
2. `INTERFACE_SAMPLE_EVENTS=True` added to dev's and admindev's settings.env; both restarted, `check --deploy` without errors.
3. `emit_sample_event d48f571b-… 1` on dev → delivered by the nudge within seconds (0 attempts); admindev `AggregateVersion` sample = 1.
4. admindev gunicorn stopped, v2 emitted → pending, 1 attempt, `http_502`, next attempt 1 minute later; admindev started → delivered by the `dispatchoutbox` timer; admindev version 2.
5. v1 emitted again → delivered as stale; admindev keeps 2.
6. v3 with `--region ae` → dev row `rejected: region_mismatch`; admindev `FailedEvent` (sample.recorded, region_mismatch).
Left to the user: disable the Sentry monitors `dispatchoutbox` and `purgeoutbox` after their first run; a look at Admin → Interface events in the browser (the row is there). dev-ae has the timers only after its next re-provision and is not paired with admindev yet (entry 4).
