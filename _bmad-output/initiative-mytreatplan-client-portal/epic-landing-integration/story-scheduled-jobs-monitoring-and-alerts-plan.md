---
title: 'Scheduled jobs, monitoring and alerts'
type: 'feature'
ticket: '9'
created: '2026-10-11'
status: 'built'
baseline_revision: '6b27169cc0f014905d7dbfe73335ef7aa2123d26'
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

**Problem:** Staging is live, but nobody would know if it went down, threw errors or skipped a scheduled job, and there's no standard way to run scheduled Django commands. Later stories (retention jobs 2.10, CRM retries 1.6, Twilio alerts 2.11) depend on both.

**Approach:** Run management commands from systemd timers in the live release. Report errors and missed or failed jobs to Sentry's EU region with personal data scrubbed. Add a lightweight health URL outside basic auth for UptimeRobot. All alerts go to devops@mytreatplan.com.

## Boundaries & Constraints

**Always:**
- **Scheduled jobs:** a templated pair `mytp-job@.service` / `mytp-job@<name>.timer`, installed by `provision.sh`. The service runs `deploy/run-job.sh <name>` as `mytp`, which loads `shared/settings.env` and runs `manage.py <command>` in `/srv/mytp/current`. Jobs are declared in one versioned file, `deploy/jobs.conf` (name, command, schedule as a systemd `OnCalendar`). Timers are `Persistent=true`. The first job is `clearsessions`, daily.
- **Sentry:** `sentry-sdk` is initialised in settings only when `SENTRY_DSN` is set (env), so local development and CI send nothing.
  - `send_default_pii=False`, with a `before_send` hook that removes request data, cookies, headers, user email and IP, and query strings.
  - `environment` comes from env (`staging`); `release` is the git SHA, which `release.sh` writes to `shared/settings.env` or exposes as env.
  - The EU region DSN (`*.de.sentry.io`).
- **Missed and failed jobs:** `run-job.sh` reports a Sentry cron check-in (in progress, then ok or error) under a monitor slug `mytp-<env>-<name>`, so a job that fails or doesn't run on schedule raises a Sentry alert. A non-zero exit also fails the systemd unit, visible in `journalctl`.
- **Health URL:** `GET /healthz` (no language prefix, not in the sitemap) returns `200 ok`, with no DB or personal data. It returns 503 if a trivial DB query fails. Nginx serves it **without** basic auth, and it carries `noindex` like everything else.
- **UptimeRobot** watches `https://dev.mytpdso.com/healthz` and sends alerts to devops@mytreatplan.com. The Sentry project's alert rules email the same address.
- **Secrets:** `SENTRY_DSN` lives only in `shared/settings.env` and `deploy/staging.env.example` lists the key; no secrets in the repo.
- Decision (user, 2026-10-11): keep this plan whole (~2,200 tokens).
- Decision (user, 2026-10-11): story 1.9 split. Backups are 1.14 and KMS is 1.15. Sentry, EU region; UptimeRobot; alerts to devops@mytreatplan.com; systemd timers.

**Never:**
- Backups (1.14), KMS (1.15), production (1.12), email sending (1.5).
- Sending personal data (form fields, emails, IPs, cookies) to Sentry. Health output that reveals versions, settings or errors.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Health OK | `GET /healthz`, DB reachable | 200, body `ok`, no basic auth required | — |
| Health DB down | `GET /healthz`, DB query fails | 503, body `unavailable` | — |
| Sentry off | `SENTRY_DSN` unset | No Sentry initialisation; nothing sent | — |
| Error scrubbed | An exception in a request with cookies, query string, form data | Event reaches Sentry without cookies, headers, query string, body, user email or IP | — |
| Job runs | Timer fires `clearsessions` | Command runs in `current` as `mytp`; Sentry check-in ok | — |
| Job fails | Command exits non-zero | Sentry check-in error, alert email; unit failed | — |
| Job missed | Timer doesn't fire on schedule | Sentry missed-check-in alert | — |
| Down | Gunicorn stopped | UptimeRobot alert to devops@mytreatplan.com | — |

</frozen-after-approval>

## Code Map

- `mytreatplan/settings.py` -- env-driven via decouple (`DEBUG`, `ROBOTS_NOINDEX`, `PORTAL_ENABLED`, `SITE_URL`, DB TLS from 1.8). Add the `SENTRY_*` settings and the init.
- `mytreatplan/urls.py` -- root-level `robots.txt` and `sitemap.xml`, `admin/`, `i18n_patterns(...)`. Add `healthz` at the root.
- `mytp_publicsite/views.py` -- `robots_txt` uses `require_safe`. Add the `healthz` view the same way.
- `mytp_publicsite/middleware.py` -- `RobotsNoindexMiddleware`, unchanged.
- `deploy/nginx/mytp.conf.template` -- HTTPS server with `auth_basic` at server level. Its locations are `/.well-known/acme-challenge/` (`auth_basic off`), `/static/` and `/`. Add `location = /healthz` with `auth_basic off`, proxied like `/`.
- `deploy/provision.sh` -- idempotent helpers (`install_file`, `ensure_dir`, `changed`). Install the job units and enable a timer per line of `jobs.conf`, removing timers no longer listed.
- `deploy/release.sh` -- unpacks into `releases/<sha>`. Expose the SHA for Sentry's `release`, e.g. by writing `RELEASE_SHA` into an env file the unit reads, or as part of `current`.
- `deploy/systemd/` -- `mytp-gunicorn.{socket,service}`. Add `mytp-job@.service` and the timer template.
- `deploy/README.md` -- the runbook. Add Sentry project setup (EU, alert rules to devops@), UptimeRobot setup, the `SENTRY_DSN` line, how to add a job, and how to read job logs.
- `requirements.txt` -- add pinned `sentry-sdk`.
- Tests: `mytp_publicsite/tests/` (no DB except where noted). Existing: `test_deploy.py` (bash -n, workflow, template asserts) and `test_settings_staging.py` (env-driven settings loader).

## Tasks & Acceptance

**Execution:**
- [x] `requirements.txt`, `mytreatplan/settings.py`, `deploy/staging.env.example` -- the Sentry init with the scrubbing `before_send`, gated on `SENTRY_DSN`.
- [x] `mytp_publicsite/views.py`, `mytreatplan/urls.py` -- `/healthz` (`require_safe`; DB `SELECT 1`; 200/503).
- [x] `deploy/nginx/mytp.conf.template` -- `location = /healthz` with `auth_basic off`.
- [x] `deploy/jobs.conf`, `deploy/run-job.sh`, `deploy/systemd/mytp-job@.service` and timer template -- the job runner with Sentry cron check-ins (via `sentry-sdk`'s monitor API or a small `manage.py` wrapper command).
- [x] `deploy/provision.sh` -- install and enable the timers from `jobs.conf` idempotently.
- [x] `deploy/release.sh` -- expose the release SHA for Sentry.
- [x] `deploy/README.md` -- Sentry, UptimeRobot and job runbook steps.
- [x] Tests for every matrix row testable in code: Health OK, Health DB down, Sentry off, Error scrubbed (unit-test `before_send`), plus `bash -n` and template asserts for the job files and the `/healthz` location.

**Acceptance Criteria:**
- Given the runbook steps are done, when an error is forced on staging, then it appears in Sentry (EU) without personal data, and devops@mytreatplan.com gets the alert.
- Given `clearsessions` is scheduled, when its timer fires, then Sentry shows a successful check-in; when the job is made to fail, then an alert email arrives.
- Given UptimeRobot is set up, when Gunicorn is stopped on staging, then devops@mytreatplan.com gets a down alert, and an up alert after restarting.

## Implementation Notes

- Sentry: `mytreatplan/sentry.py` (`init_sentry`, `scrub_event`). Besides `send_default_pii=False`, it sets `max_request_body_size='never'` and `include_local_variables=False` (frame locals could hold form data). `scrub_event` keeps only `request.method` and `request.url` (query stripped) and `user.id`; it also strips query strings from breadcrumb URLs. Settings: `SENTRY_DSN`, `SENTRY_ENVIRONMENT` (default `development`), `SENTRY_RELEASE` from env `RELEASE_SHA`, else the release's `REVISION` file.
- Release SHA: `release.sh` writes `releases/<sha>/REVISION` (so both Gunicorn and jobs see it through `current`); `shared/settings.env` is not rewritten. `/REVISION` is git-ignored.
- Jobs: `deploy/jobs.conf` is `name | command | schedule`. Check-ins are done by a management command, `manage.py runjob <name>` (`mytp_publicsite/management/commands/runjob.py`), which reads `jobs.conf` from the release and upserts the Sentry monitor (`mytp-<env>-<name>`, crontab, UTC, 30 min margin, 60 min max runtime). Schedules must be OnCalendar in the form `[Mon..Fri ]*-*-<day|*> <hour|*>:<minute>[:00] UTC` so they map to a crontab (`mytp_publicsite/jobs.py`); tests check every line of `jobs.conf`. `clearsessions` runs at `*-*-* 03:30:00 UTC`.
- Timers: `deploy/systemd/mytp-job.timer.template` rendered by `provision.sh` into `/etc/systemd/system/mytp-job@<name>.timer`; `mytp-job@.service` runs `/bin/bash /srv/mytp/current/deploy/run-job.sh %i` as `mytp`. `run-job.sh` relies on the release's `settings.env` link (decouple reads it) rather than sourcing the file in bash.
- `/healthz`: `never_cache` + `require_safe`, root-level, text/plain. Tests fake the DB cursor (the project's tests run without a test database).
- `release.sh` still health-checks `/robots.txt`, unchanged.

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, 2026-10-11): high 0, medium 2, low 4, false 0, maybe-false 0.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | Timer schedules (from provision's `jobs.conf`) and the Sentry monitor schedule (from the release's `jobs.conf`) can drift → false missed alerts; removed jobs fail as "unknown job" | medium | patch | Confirmed by reading provision.sh and runjob. Patch: provision installs `shared/jobs.conf`; runjob reads it; release.sh warns on mismatch; README. |
| 2 | Exception and log messages can carry personal data (e.g. IntegrityError with an email) to Sentry | medium | patch | Confirmed: `scrub_event` only touches request, user and breadcrumb URLs. Breaks the Never rule. Patch: redact emails and IPs in exception, logentry and breadcrumb messages, drop `logentry.params`, plus a test. |
| 3 | Removing a job leaves its Sentry monitor alerting | low | patch | README step to delete the monitor. |
| 4 | Wrapping weekday ranges produce invalid crontab ranges | low | patch | Confirmed (`Mon..Sun` → `1-0`). Patch: reject wrapping ranges in validation, plus tests. |
| 5 | Timers can fire before the first release exists | low | rejected | Only between first provisioning and first deploy (already past on staging); a guard would hide real "no release" failures later. |
| 6 | Runbook alert rule only fires on new issues | low | patch | README: add the regression condition. |

## Verification

Done locally (2026-10-11): `pytest` 151 passed, 6 skipped (PyYAML-dependent workflow tests; PyYAML not installed locally); `python manage.py check` no issues (also with a dummy `SENTRY_DSN`); `bash -n deploy/*.sh` ok; `python manage.py runjob clearsessions` ok against the dev DB. Mutation check: disabling `scrub_event` makes the WSGI-level scrubbing test fail. Manual staging checks below are pending (need the merge and the runbook steps 8-9).

**Commands:**
- `pytest` -- expected: all pass
- `python manage.py check` -- expected: no issues
- `bash -n deploy/*.sh` -- expected: no errors

**Manual checks (if no CLI):**
- On staging after merge and the runbook: `curl https://dev.mytpdso.com/healthz` returns `ok` without a password; a forced error appears in Sentry, scrubbed; `systemctl list-timers 'mytp-job@*'` shows `clearsessions`; Sentry shows its check-in; stopping Gunicorn triggers the UptimeRobot alert.
