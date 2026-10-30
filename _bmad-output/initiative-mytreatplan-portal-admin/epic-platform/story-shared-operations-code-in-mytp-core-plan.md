---
title: 'Shared operations code in mytp-core'
type: 'refactor'
ticket: '5'
created: '2026-10-30'
status: 'built'
baseline_revision: '26e4df88b9d0c6bff28b26eccfa19da0de6ad3e0'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-mytreatplan-portal-admin/architecture-portal-admin/architecture-portal-admin.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The admin site (entry 10) needs the same error reporting with its scrubber, scheduled-job runner with Sentry cron monitors, health check and database backups as the portal, but they live in the portal. AD-23 also needs the scrubber to drop more than it does today, and with several sites on one host the cron monitors must be told apart by site (AD-8, AD-24).

**Approach:** Move `mytreatplan/sentry.py`, `mytp_publicsite/jobs.py` with the `runjob` command, the `/healthz` view and the `backupdb` command into mytp-core as release `v0.2.0`, with the project-specific parts as settings; extend the scrubber with named sensitive fields; put `SITE` in monitor slugs and in `/healthz`. The portal installs the new wheel and uses the shared versions; job names, commands and backups otherwise behave as today.

## Decisions

- Old monitors (user, 2026-10-30): dev's slugs change to `mytp-dev-<job>`; after the merge the user deletes the six `mytp-staging-*` cron monitors in Sentry.

## Boundaries & Constraints

**Always:** command names (`runjob`, `backupdb`) and `deploy/jobs.conf` format unchanged; existing backups stay restorable by `deploy/restore-db.sh` (same object naming); `/healthz` keeps answering `ok` (200) or `unavailable` (503) on its first line and stays free of secrets and versions; the scrubber never sends request bodies, cookies, headers, query strings, emails, phone numbers, IPs or the named sensitive fields; the package imports no portal code.

**Never:** no change to job schedules, backup format or encryption; no Sentry token or API calls from code; no admin-site code (entry 10); no per-site backup prefixes (entry 13).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Cron check-in | `runjob clearsessions` on site dev | Sentry check-ins on monitor `mytp-dev-clearsessions` | Failure reported on the same monitor |
| Health | GET /healthz on dev, database up | 200, first line `ok`, then `site: dev` | — |
| Health down | Database unreachable | 503, first line `unavailable` | — |
| Scrubbed event | Event with `extra`/`contexts`/breadcrumb data keys such as `patient_name`, `password`, `token`, `email` | Those values replaced by the redaction marker | — |
| Ignored loggers | Logger listed in the setting (portal: `signup.verification`) | Never sent to Sentry | — |

</frozen-after-approval>

## Code Map

- `mytreatplan/sentry.py` -- `redact`, `scrub_event`, `init_sentry`; only portal-specific part is `CODE_LOGGERS=('signup.verification',)` :24 → a setting `MYTP_SENTRY_IGNORED_LOGGERS`. Called from `mytreatplan/settings.py:166-174` (`DjangoIntegration`, `send_default_pii=False`, `max_request_body_size='never'`, `include_local_variables=False`, `before_send=scrub_event`). Moves to `mytp_core/sentry.py`.
- `mytp_publicsite/jobs.py` -- `Job`, `oncalendar_to_crontab`, `parse_jobs`, `jobs_file`, `load_jobs`, `monitor_slug` :117; module-level `JOBS_FILE` :15 and `INSTALLED_JOBS_FILE` :19 become lazily read settings `MYTP_JOBS_FILE` (default `BASE_DIR/deploy/jobs.conf`) and `MYTP_INSTALLED_JOBS_FILE` (default `SITE_HOME/shared/jobs.conf`). Moves to `mytp_core/jobs.py`.
- `mytp_publicsite/management/commands/runjob.py`, `backupdb.py` -- move to `mytp_core/management/commands/`. `backupdb`'s `OBJECT_BASENAME='mytreatplan'` :48 becomes setting `MYTP_BACKUP_BASENAME` (default `mytreatplan`) so restore-db.sh still matches.
- `/healthz` -- `mytp_publicsite/views.py:158-169`, routed at `mytreatplan/urls.py:24,33` (root, not i18n); nginx `location = /healthz` without basic auth (site.conf.template:60). Moves to `mytp_core/views.py`; the portal routes to it.
- Tests: `mytp_publicsite/tests/test_monitoring.py` (40), `test_backups.py` (37), plus slug assertions in test_kms.py:211 and `eventlog/tests/test_retention.py:301`; fixtures `conftest.py:182-194` (`_runjob_with`) and `:210-219` (`sentry_capture`) import the moved modules.
- Comments to update: `deploy/jobs.conf:6,9`, `deploy/systemd/job@.service.template:17`.
- Packaging: `../mytp-core` (version, README, tests, CI), `scripts/vendor-mytp-core.sh v0.2.0`, `vendor/`, `requirements.txt`.

## Design Notes

- Monitor slug: `mytp-<SITE>-<job>` (was `mytp-<SENTRY_ENVIRONMENT>-<job>`); the environment stays the Sentry environment.
- `/healthz` body: `ok\nsite: <SITE>\n`; entry 6 adds `region: <REGION>`. Keyword monitors matching `ok` keep working.
- Named sensitive fields (scrubbed anywhere in `extra`, `contexts`, `tags`, breadcrumb `data`, case-insensitive key match): `password`, `secret`, `token`, `key`, `authorization`, `cookie`, `email`, `phone`, `name`, `first_name`, `last_name`, `patient`, `address`, `iban`, plus a setting to add more.
- mytp-core becomes `0.2.0` (new modules, no breaking change to 0.1.0's API).

## Tasks & Acceptance

**Execution:**
- [x] `../mytp-core/mytp_core/` -- `sentry.py`, `jobs.py`, `views.py` (healthz), commands `runjob` and `backupdb`, their settings and tests; version 0.2.0, README; commit and tag `v0.2.0` locally (no push).
- [x] `mytreatplan/sentry.py`, `mytp_publicsite/jobs.py`, `mytp_publicsite/views.py`, `mytreatplan/urls.py`, `mytreatplan/settings.py` -- re-export or route to the package; new settings.
- [x] `mytp_publicsite/management/commands/` -- remove `runjob.py` and `backupdb.py` (now in the package app).
- [x] tests and `conftest.py` -- point at the package; update slug assertions to `mytp-dev-…`; add tests for the I/O matrix rows.
- [x] `scripts/vendor-mytp-core.sh v0.2.0`, `vendor/`, `requirements.txt` -- vendor the new wheel, remove 0.1.0.
- [x] `deploy/` comments and `deploy/README.md` "Scheduled jobs" -- the new slug format and the one-off removal of the old `mytp-staging-*` monitors.

**Acceptance Criteria:**
- Given the portal on mytp-core v0.2.0, when the full suite runs, then it passes with the updated slugs.
- Given the merge deployed to dev, when a job runs, then its check-in lands on `mytp-dev-<job>` and `/healthz` answers `ok` with `site: dev`.
- Given a test event carrying a patient-like field in extra data, when it is scrubbed, then the value never leaves the site.

## Implementation Notes

- Setup: `../mytp-core` is the local clone (pushed `master` at the v0.1.0 tag). Commit and tag `v0.2.0` there; do not push either repo. The global gitignore excludes `settings.py`; `tests/settings.py` is already force-tracked via `.gitignore`.

## Plan Change Log

## Review Triage Log

- Pass 1 (quick): 4 findings; medium 2, low 2. All patched.
  - medium, patch: `is_sensitive_key` checked only the part after the last dot, so `patient.notes`, `secret.value`, `token.value` were sent unredacted. Every key is now matched with separators removed; a safe-key list (`MYTP_SENTRY_SAFE_KEYS`, default `password_reset.action`) keeps the portal's known tags.
  - medium, patch: whole-word matching missed `patients`, `apikey`, `phonenumber`, `emailaddress`, `fullname`. Field names now match as case-insensitive substrings (over-redaction accepted); tests cover the five keys.
  - low, patch: the `name` field blanked the SDK's own contexts (`runtime.name`, `os.name`) on every event. SDK contexts skip field-name matching but keep the email/IP/phone redaction.
  - low, patch: `deploy/README.md:1351` still named `mytp-staging-clearsessions`; now `mytp-dev-clearsessions`.
- After patches: mytp-core v0.2.0 moved to f50cac9 (unpushed before), clean clone 282 passed; wheel rebuilt; portal full suite 1468 passed, 3 known PostgreSQL-only failures; `check` and `makemigrations --check` clean.

## Verification

**Commands:**
- Portal full suite, from the repo root (non-secret test keys; venv `../.venv`; browser tests need the sandbox off): `export FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU="; export DATABASE_URL="sqlite:///$TMPDIR/x.sqlite3"; ../.venv/bin/python -m pytest -q -p no:cacheprovider --tb=line` -- expected: all pass except the 3 known PostgreSQL-only tests in `accounts/tests/test_crypto.py`.
- mytp-core: its README test command, also from a clean clone of the `v0.2.0` tag -- expected: all pass.
- `../.venv/bin/python manage.py check` -- expected: no issues.

**Manual checks (if no CLI):**
- After the merge (release.sh unchanged, so no re-provision needed): on dev, `sudo systemctl start mytp-dev-job@clearsessions` reports to Sentry monitor `mytp-dev-clearsessions`; `curl https://dev.mytpdso.com/healthz` shows `ok` and `site: dev`.

## Staging

- Merged in PR #51 as merge commit a0a21909ad36387c2893b8d3dbedae70873bbc0c (2026-10-30). CI run 41054001369 on master: test and deploy-staging green (no re-provision needed).
- https://dev.mytpdso.com/healthz answers `ok` / `site: dev`.
- Pending (user): the first `mytp-dev-*` cron check-in (purgepending at 13:15 UTC), Notify on the new monitors, deletion of the six `mytp-staging-*` monitors; then this plan is marked done.
