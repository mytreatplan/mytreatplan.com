---
title: 'Retention jobs'
type: 'feature'
ticket: '10'
created: '2026-10-14'
status: 'built'
baseline_revision: 'fb18cfd4cd811a925e70af17257dd0927cfeb053'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing removes old data:
- a signup left unfinished keeps its personal data in `PendingRegistration` forever;
- its event-log attempt stays open;
- the event log keeps personal values with no end date (CAP-9, CAP-10).

**Approach:**
- One retention service that the jobs call, and that entry 8 (cancel) reuses:
  - every hour, purge unfinished signups older than 24 h and close their attempts as `timeout`;
  - every day, anonymize the log 30 days after an attempt ends;
  - every day, delete attempts that ended more than 2 years ago.
- The jobs run through epic 1's scheduled-job setup (`deploy/jobs.conf`). A person installs the timers on staging.

## Boundaries & Constraints

**Always:**
- **Pending purge:**
  - It deletes every `PendingRegistration` whose `created_at` is 24 h old or more, which is the edge of `active()`. Its legal choices go with it (CASCADE).
  - Each deleted row's open attempt gets a `purged` event, with reason `timeout` and no `data`, and then ends with outcome `timeout`.
  - Open signup attempts started 24 h ago or more that no pending row points to also end `timeout`. These are attempts refused at step 1, or offers never taken. Without this they would never end and never be anonymized.
- **Cache clean-up:** the same hourly job deletes expired rows of the database cache. They hold blind indexes, and with `MAX_ENTRIES=100000` Django rarely culls them.
- **Log anonymization:** for every attempt whose `ended_at` is 30 days old or more, its events' `data` is set to NULL. Event type, step, field, reason and times stay, so the attempt can still be reconstructed without personal values.
- **Log deletion:** attempts whose `ended_at` is 2 years old or more are deleted with their events. An attempt with no `ended_at` is never anonymized or deleted by age.
- The rules apply to every attempt `kind` (signup now, login later).
- The service functions take `now` (default `timezone.now()`) so tests can time-travel. Each returns its counts.
- They are idempotent: a second run changes nothing. They work in batches, so a large backlog doesn't hold one long transaction.
- Entry 8 reuse: one function removes a given pending registration and ends its attempt with a given outcome (`timeout` here, `cancelled` in entry 8).
- **Commands:** `purge_pending_signups` and `eventlog_retention`. Each prints its counts and no personal values. They are added to `deploy/jobs.conf` as hourly and daily jobs.
- `deploy/README.md` says to run `provision.sh` again to install the new timers, and how to check them.
- Decision (user, 2026-10-14): every attempt, abandoned ones included, keeps its log personal data for 30 days after it ends. The 24 h purge clears the registration store only. CAP-9's success line and the spec's Success signal are reworded to say so ("its log is anonymized 30 days after the attempt ends, as CAP-10"). `registration-failure-policies.md`'s preamble is reworded too.
- Decision (user, 2026-10-14): the plan is kept whole, slightly above the size guideline.

**Never:**
- Touching users, their legal choices, or attempts still open (other than the 24 h rule above).
- Logging or printing personal values from the jobs.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Stale pending | Row created 24 h 1 min ago, open attempt | Row and its legal choices deleted; `purged` (reason `timeout`, no data); attempt ended `timeout` | No error expected |
| Fresh pending | Row created 23 h 59 min ago | Untouched | No error expected |
| Resumed row | New row that kept an old `created_at` past 24 h | Purged (resume never extends the window) | No error expected |
| Orphan attempt | Open signup attempt started 25 h ago, no row points to it | Ended `timeout` | No error expected |
| Recent orphan | Open attempt started 2 h ago, no row | Untouched | No error expected |
| Expired cache | Expired and live cache rows | Expired rows deleted, live rows kept | No error expected |
| Anonymize | Attempt ended 30 days 1 min ago | All its events' `data` NULL; other columns intact | No error expected |
| Too recent | Attempt ended 29 days ago | `data` kept | No error expected |
| Delete | Attempt ended 2 years 1 day ago | Attempt and events gone | No error expected |
| Open old attempt | No `ended_at` | Never anonymized or deleted by age | No error expected |
| Idempotent | Run twice | Second run reports zero changes | No error expected |
| Jobs | `runjob purgepending`, `runjob eventlogretention` | Run the commands; counts printed, no personal values | No error expected |

</frozen-after-approval>

## Code Map

- `signup/models.py`:
  - `ACTIVE_FOR` (24 h) and `PendingRegistrationQuerySet.active()` set the window. Add `expired(now)` as its complement (`created_at__lte`).
  - `attempt` is a FK to `eventlog.Attempt` (SET_NULL).
- `signup/views.py` `_abandon_attempts` ends attempts after a commit. Reuse its pattern, but call `eventlog.service.record` / `end_attempt` with an explicit time where needed. `end_attempt` uses `timezone.now()`, which is fine for the job.
- New `signup/retention.py`: `purge_pending(now=None, batch_size=...)`, `remove_pending(row, outcome)` (for entry 8), `end_orphan_attempts(now=None)`, `clear_expired_cache(now=None)`. For the cache, use a `DELETE` on `settings.CACHES['default']['LOCATION']` where `expires < now`, through `connection.ops.quote_name`.
- New `eventlog/retention.py`: `anonymize(now=None)` (`Event.objects.filter(attempt__ended_at__lte=..., data__isnull=False).update(data=None)` in batches by pk) and `delete_old(now=None)`.
  - `EncryptedJSONField` is in `accounts/crypto.py`; `NULL` is stored as SQL NULL, so `isnull` works.
  - Constants: `ANONYMIZE_AFTER = 30 days`, `DELETE_AFTER = 730 days`.
- New commands: `signup/management/commands/purge_pending_signups.py` (purge, orphans, cache) and `eventlog/management/commands/eventlog_retention.py`. Follow `mytp_publicsite/management/commands/backupdb.py` for output style.
- `deploy/jobs.conf`:
  - `purgepending | purge_pending_signups | *-*-* *:15:00 UTC`
  - `eventlogretention | eventlog_retention | *-*-* 03:45:00 UTC`

  `mytp_publicsite/jobs.py` already parses hourly `*` hours. Its tests (`mytp_publicsite/tests/test_jobs*.py`) check that every line maps to a crontab.
- `deploy/README.md`, section 8 (jobs check): add the two jobs and a manual `systemctl start` check.
- `spec-client-portal-signup/spec-client-portal-signup.md`: CAP-9 success (line 48) and the Success signal (line 90). `spec-client-portal-signup/registration-failure-policies.md`: preamble line 5 ("Once a registration is cancelled or expires, its personal data is gone; the log keeps only..."). Reword these to the decision: the registration store is cleared at once, and the log's personal values go 30 days after the attempt ends.
- Tests: new `signup/tests/test_retention.py` and `eventlog/tests/test_retention.py`. Use `now=` arguments, or set `created_at` / `ended_at` with `.update()`, since `auto_now_add` ignores passed values.

## Tasks & Acceptance

**Execution:**
- [x] `signup/models.py`, `signup/retention.py`, the command -- pending purge, orphan attempts, cache clean-up, the reusable removal.
- [x] `eventlog/retention.py`, the command -- anonymize and delete.
- [x] `deploy/jobs.conf`, `deploy/README.md` -- the two jobs and how to install and check them.
- [x] `spec-client-portal-signup/` -- CAP-9, the Success signal and the policies preamble, per the decision.
- [x] Tests -- every matrix row.

**Acceptance Criteria:**
- Given staging after the merge, when a person runs `provision.sh` again, then `systemctl list-timers` shows `mytp-job@purgepending` and `mytp-job@eventlogretention`, and starting each by hand reports ok in Sentry Crons.
- Given CI on PostgreSQL, when the suite runs, then every test passes.

## Implementation Notes

- `remove_pending(row, outcome)` deletes the row first; only if it deleted it, and only if the attempt is still open, does it record `purged` (reason = outcome, no data) and end the attempt. An attempt already ended (e.g. `abandoned` by a resume) keeps its outcome.
- Orphan attempts are ended by a bulk `update` (ended_at = current time, outcome `timeout`), with no event, and only for `kind='signup'`. An old attempt still referenced by any pending row is not an orphan.
- `clear_expired_cache` is a single `DELETE ... WHERE expires < now` (seconds precision, as Django's cache) and does nothing unless the default cache is the database cache.
- `delete_old` returns `(attempts, events)`; the job prints both.
- Verified on SQLite (only the 3 known PostgreSQL-only tests fail) and on a local PostgreSQL cluster (full suite passes); `makemigrations --check`: no changes.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-14):** high 0, medium 2, low 2, false 0, maybe-false 0. Four patches; nothing rejected or deferred.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | CAP-8, the Constraints bullet and the policies' cancel row still say the log keeps no personal data, contradicting the 30-day decision | medium | patch | Real: entry 8 is built against CAP-8. Fix: all three reworded to the decision. The epic file's "Done when" line is in the ticket tree, which the workflow doesn't edit; it reads as the registration store. |
| 2 | `end_orphan_attempts` re-checks only `ended_at` in the UPDATE, so a row created between SELECT and UPDATE can lose its live attempt | low | patch | Real but narrow (a tab left open past 24 h, submitted in that window). Direct correction: the UPDATE goes through the same orphan filter. |
| 3 | `_pending_registration` loads rows past 24 h. Once purged mid-wizard, `save()` re-INSERTs the row's personal data, or the legal step's `bulk_create` hits an FK error (500) | medium | patch | Real: plain `.get(pk=...)` ignored `active()`. Fix: only active rows load. An expired row counts as gone, and the person starts at step 1. Test added. |
| 4 | The module docstring says every function takes `now` and returns counts, but `remove_pending` does neither | low | patch | Direct correction of the docstring. |

## Verification

**Commands:**
- `DATABASE_URL="sqlite:///$TMPDIR/x.sqlite3" ../.venv/bin/python -m pytest -q` (with test keys) -- expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py makemigrations --check` -- expected: no changes.

Done locally (2026-10-14) after the review patches, on SQLite: `pytest` 667 passed, 6 skipped (PyYAML), 3 failed only because those tests use PostgreSQL-only raw SQL. Before the patches, the implementer's run on a throwaway PostgreSQL passed all 669 tests. `makemigrations --check` reports no changes. Pending: CI on PostgreSQL and the staging check (`provision.sh` again; both timers listed; a manual start of each reports ok in Sentry Crons).
