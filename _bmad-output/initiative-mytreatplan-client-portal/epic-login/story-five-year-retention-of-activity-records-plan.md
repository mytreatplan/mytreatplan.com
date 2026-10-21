---
title: 'Five-year retention of activity records'
type: 'feature'
ticket: '8'
created: '2026-10-21'
status: 'built'
baseline_revision: 'e8baf0aa906b61c50d5a1f3538462a277e44fa30'
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

**Problem:** Login records (3.4, kind `login`) and page records (3.7, kind `page`) must be kept intact, IP and User-Agent included, for 5 years, a legal requirement, and then deleted. Since 3.4 and 3.7, the retention job skips both kinds (`eventlog/retention.py` `EXCLUDED_KINDS`), so they're never deleted. An erased account's records must survive its purge for the same 5 years: they hold the account's pk, with no foreign key.

**Approach:**
- Give activity records (`login`, `page`) their own rule: never anonymized, and deleted once they ended 5 years ago (per decision 2).
- Run that rule daily with a Sentry monitor, per decision 1.
- Signup attempts keep their 30-day anonymization and 2-year deletion unchanged.

## Boundaries & Constraints

**Always:**
- Activity records keep their field encryption (`Event.data`) for their whole life. They're never anonymized.
- Age counts from the attempt's `ended_at`, as for signup. An attempt still open is never deleted by age.
- The deletion works in batches, returns counts, prints counts only (never personal values), and changes nothing on a second run.
- The kinds covered by the 5-year rule are one named constant (`login`, `page`), so a future activity kind is added in one place.
- The purge of erased accounts (`accounts.erasure.purge_erased`) doesn't touch activity records.

**Never:**
- Changing signup's 30-day and 2-year rules or their results.
- Anonymizing activity records.
- Deleting any activity record younger than the period.
- A new job, timer or Sentry monitor (decision 1), or a setting for the period (decision 3).

**Decisions (2026-10-21):**
1. The existing daily `eventlogretention` job (`deploy/jobs.conf`, 03:45 UTC, its Sentry monitor `mytp-staging-eventlogretention`) runs the 5-year rule too. `eventlog_retention` applies signup's rules, then the activity rule, and prints all counts. No server step is needed.
2. "5 years" is 5 calendar years from the attempt's `ended_at` (`dateutil.relativedelta(years=5)`). An attempt is deleted once `ended_at <= now - 5 calendar years`.
3. The period is a constant in `eventlog/retention.py` (`ACTIVITY_RETENTION = relativedelta(years=5)`). A change goes through code review.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Old login record | `login` attempt ended 5 calendar years and a day ago | Deleted with its event | — |
| Leap day | Attempt ended 29 Feb 2028, job on 28 Feb 2033 / 1 Mar 2033 | Kept / deleted (calendar years, decision 2) | — |
| Young record | `page` attempt ended 5 years minus a day ago | Kept, data intact | — |
| 30+ days | Activity attempt ended 31 days ago | Data intact (not anonymized) | — |
| 2+ years | Activity attempt ended 3 years ago | Kept | — |
| Signup | Signup attempts at 31 days / 3 years | Anonymized / deleted as today | — |
| Erased account | Account erased and purged; its records 1 year old | Records remain with the pk | — |
| Open attempt | Activity attempt with no `ended_at` | Never deleted | — |
| Second run | Job run twice | Second run deletes nothing | — |
| Output | Job run | Counts only | — |
| Monitor | `eventlogretention` run through `runjob` | Its existing Sentry check-ins (in progress, ok) | Error check-in on failure |

</frozen-after-approval>

## Code Map

- `eventlog/retention.py` -- `ANONYMIZE_AFTER` (30 days), `DELETE_AFTER` (730 days), `BATCH_SIZE`, `anonymize`, `delete_old`, `EXCLUDED_KINDS = (Attempt.LOGIN, Attempt.PAGE)`. Rename `EXCLUDED_KINDS` to `ACTIVITY_KINDS`, keep it excluded from the signup rules, and add `delete_old_activity(now=None, batch_size=...)` on the 5-year period.
- `eventlog/management/commands/eventlog_retention.py` -- the daily command (prints counts). Per decision 1, extend it or add `activity_retention`.
- `deploy/jobs.conf` -- `eventlogretention | eventlog_retention | *-*-* 03:45:00 UTC`. A new job line needs `provision.sh` re-run on the server and its Sentry monitor's notifications set (`deploy/README.md`, the jobs section and step 8.5). `mytp_publicsite/jobs.py` parses it, and the tests check every line (`mytp_publicsite/tests/test_deploy.py`, `test_monitoring.py`).
- `eventlog/models.py` -- `Attempt.LOGIN`, `Attempt.PAGE`, `ended_at` (indexed).
- `accounts/erasure.py` `purge_erased` -- must leave activity records alone. It already does; add a test.
- `python-dateutil` (already in `requirements.txt`) -- `relativedelta(years=5)` if decision 2 picks calendar years.
- Tests: `eventlog/tests/test_retention.py` (its two activity tests from 3.4 and 3.7 change), `accounts/tests/test_erasure.py` (purge), and the `release_jobs` fixture in the root conftest for `runjob`.

## Tasks & Acceptance

**Execution:**
- [x] `eventlog/retention.py` -- `ACTIVITY_KINDS`, `ACTIVITY_RETENTION` (decisions 2 and 3), `delete_old_activity`.
- [x] `eventlog/management/commands/eventlog_retention.py` -- also run `delete_old_activity`; print its counts (decision 1). `deploy/jobs.conf` unchanged.
- [x] `deploy/README.md` -- the retention rules of the `eventlogretention` job (signup and activity).
- [x] `eventlog/tests/test_retention.py`, `accounts/tests/test_erasure.py` -- every matrix row.

**Acceptance Criteria:**
- Given an activity record 5 years and a day old and one 5 years minus a day old, when the job runs, then only the older is deleted, and the younger's IP and User-Agent are intact.
- Given the full suite, when it runs, then signup's retention tests pass unchanged.

## Implementation Notes

- `EXCLUDED_KINDS` renamed `ACTIVITY_KINDS`; `delete_old` and the new `delete_old_activity` share one batched `_delete` helper. The cutoff is `now - ACTIVITY_RETENTION` with `ended_at <= cutoff`, so a record ended exactly 5 calendar years ago is deleted.
- The command's output keeps signup's counts as the line's prefix and appends `N activity attempts deleted (M events)`, so signup's job test passes unchanged.
- The 3.4 and 3.7 retention tests (`accounts/tests/test_login_records.py`, `test_page_records.py`) now also check kept at 5 years minus a day (IP and User-Agent intact) and deleted at 5 years plus a day. In `eventlog/tests/test_retention.py`, the old login-only tests now cover both activity kinds. `test_erasure.py` imports the module as `eventlog_retention` because its autouse `retention` fixture shadows the name.
- The monitor row is covered by `runjob` with a fake Sentry: `in_progress` then `ok`, and `error` when `delete_old_activity` raises.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-21):** high 0, medium 0, low 2, false 0, maybe-false 0. Two patches. No bug in the production code.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | AC 1 ("when the job runs", 5 years ± 1 day) was shown only by calling `delete_old_activity()` directly; the job test's young record was 31 days old | low | patch | The job test now runs `runjob eventlogretention` with records 5 calendar years + 1 day old (deleted) and − 1 day old (kept, IP and User-Agent intact after decryption). |
| 2 | The test helper `_activity` gave every activity attempt `outcome='login'`, unlike real page records (`page_view`) | low | patch | The outcome is now the recorded event, as `record_activity` does. |

After the patches (2026-10-21), on SQLite with Chromium (sandbox lifted): the full suite gives 1,211 passed, 3 failed (the known PostgreSQL-only tests). `makemigrations --check`: no changes.

## Verification

**Commands:**
- The local suite command (test keys, SQLite), outside the sandbox for the browser tests. Expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py makemigrations --check`. Expected: no changes.

**Results (2026-10-21):** `DATABASE_URL=sqlite:///$TMPDIR/x.sqlite3 pytest` outside the sandbox: 1211 passed; only the 3 known PostgreSQL-only tests in `accounts/tests/test_crypto.py` failed. `makemigrations --check --dry-run`: no changes.
