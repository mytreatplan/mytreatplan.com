---
title: 'Admin site skeleton on admindev'
type: 'feature'
ticket: '10'
created: '2026-10-31'
status: 'in-review'
baseline_revision: '4057e30070b50fc34416a6dbef3cfff8f173898d'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-mytreatplan-portal-admin/architecture-portal-admin/architecture-portal-admin.md'
  - '{project-root}/deploy/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The admin site (ERP/CRM for all regions) is a separate project with its own repo, users and database (AD-1, AD-10, AD-12), and epics 3–5 build on it. The private repo `mytreatplan/mytp-admin` exists and is empty; nothing runs on admindev.mytpdso.com.

**Approach:** Scaffold the admin repo as a Django 6.1 project following the portal's conventions (settings.env via python-decouple, SITE `admindev`, mytp-core 0.3.0 vendored for Sentry with scrubber, runjob, healthz, backupdb and the task worker), with the AdminLTE 4.10 static layout (assets vendored from npm, not the `adminlte-django` package; AD-11), noindex and a disallow-all robots.txt, and a CI pipeline that tests and deploys to admindev through the host's `mytp-release`. The portal's `release.sh` stops requiring the portal-only `static/css/site.css` so any repo's tarball deploys.

## Decisions

- Database (user, 2026-10-31): the user creates database `mytp_admin` and user `mytp_admin` in the OVH Managed PostgreSQL panel (same service as dev) and types the password into admindev's settings.env when asked.
- Sentry (user, 2026-10-31): the same Sentry project as dev, told apart by the site (monitors `mytp-admindev-*`).
- Team password (user, 2026-10-31): admindev uses dev's team password (dev's htpasswd file copied on the server).
- DNS (user, 2026-10-31): admindev.mytpdso.com was created by the user.

## Boundaries & Constraints

**Always:** the admin repo never imports portal code, and the portal never imports admin code (AD-10); the admin site has its own database and Linux user; secrets live only in admindev's settings.env and GitHub secrets, typed or generated without being printed; staging stays behind the team password and is never linked from mytreatplan domains.

**Never:** no admin users, roles or 2FA (epic-admin-access); no field encryption in use yet (keys are generated only if the KMS step is cheap; otherwise epic 3 or 4 adds them); no interface endpoints (epic 4); no production deploy.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Deploy | Push to mytp-admin master | CI tests, builds the tarball, deploys to admindev; release health-checks | Rollback as for the portal |
| Page | GET https://admindev.mytpdso.com/ with the team password | Empty AdminLTE layout, `noindex` meta and header | — |
| Robots | GET /robots.txt | `Disallow: /` | — |
| Health | GET /healthz (no password) | `ok`, `site: admindev` | 503 `unavailable` when the database is down |
| Worker | Sample task enqueued on admindev | Runs once on admindev's worker | — |
| Portal deploy | Portal tarball after the release.sh change | Still deploys to dev | — |

</frozen-after-approval>

## Code Map

- **Portal repo** (`deploy/release.sh` :158) -- replace the `static/css/site.css` requirement with a generic one (the tarball has `manage.py` and a `REVISION`-independent marker such as `deploy/release-ready` that each repo's CI build creates); update `test_deploy.py` and the README. This reaches the host only after provision runs again (1.1 note).
- **Admin repo** (`../mytp-admin`, empty clone) -- new Django project `mytp_admin` (settings, urls, wsgi), an app for the layout (base template on AdminLTE 4.10 assets vendored by an npm script like the portal's `vendor:htmx`), `robots.txt`, noindex middleware or template meta + `X-Robots-Tag`, mytp-core 0.3.0 wheel in `vendor/` with `scripts/vendor-mytp-core.sh`, `deploy/jobs.conf` (backupdb, kmscheck only if KMS keys exist, workerheartbeat, prunetasks, clearsessions), `requirements.txt`, `pytest.ini`, tests, README, `.gitignore` (with `!` exceptions for any `settings.py` the global ignore hides), `.github/workflows/ci.yml` (test on PostgreSQL 17 + deploy-staging as `mytp-admindev` to `mytp-release admindev`, mirroring the portal's job and its tip-of-master check).
- **Server (hitl, Claude over SSH with the user's OK)** -- `provision.sh admindev admindev.mytpdso.com <htpasswd-user>` from the portal's merged `deploy/` (team password copied from dev's htpasswd file), admindev's `settings.env` (SECRET_KEY generated on the server, DATABASE_URL to the admin database, SITE=admindev, REGION unset, Sentry), a new deploy key pair whose public half goes in `/srv/mytp-admindev/.ssh/authorized_keys` and private half into the admin repo's `STAGING_SSH_KEY` secret (with `STAGING_HOST` and `STAGING_KNOWN_HOSTS` from `ssh-keyscan`).
- **Needs the user:** the DNS A record `admindev.mytpdso.com` → 137.74.133.106 (before the certificate), and the admin database on the managed PostgreSQL (see Open Questions).

## Tasks & Acceptance

**Execution:**
- [x] portal `deploy/release.sh`, tests, README -- generic release check (separate small portal PR, merged and provisioned first).
- [x] `../mytp-admin` -- project scaffold, layout, robots/noindex, mytp-core, jobs, worker settings, tests, CI; first commit on `master` (pushed by the orchestrator after review).
- [ ] server -- provision admindev, settings.env, deploy key and repo secrets, first deploy.

**Acceptance Criteria:**
- Given the DNS record and the database, when the admin repo's master is pushed, then admindev deploys and serves the empty AdminLTE layout behind the team password, with noindex and a disallow-all robots.txt.
- Given admindev running, when `/healthz` is fetched, then it shows `ok` and `site: admindev`, and a sample task runs once on its worker.

## Implementation Notes

- `../mytp-admin` is an empty clone of `mytreatplan/mytp-admin`. The portal change goes on the portal branch `story/1.10-generic-release-check` (already checked out). Commit there on `master`; do not push either repo; do not touch the server (the orchestrator does the server step).

## Plan Change Log

- 2026-10-31 (implementation): the portal's Gunicorn unit hard-coded `mytreatplan.wsgi:application`, so an admin release could never start. After review: `provision.sh` renders the module from `WSGI_MODULE` (default `mytreatplan.wsgi`, so dev's unit and old releases are unchanged); admindev is provisioned with `WSGI_MODULE=mytp_admin.wsgi`. `release.sh` has `RELEASE_TOOL_VERSION=2` and provision skips (with a warning) a release.sh older than the installed one.
- 2026-10-31 (implementation): `provision.sh` installs timers from the `jobs.conf` beside it, so admindev is provisioned from a separate copy of the portal's `deploy/` (`~/deploy-admindev`) with the admin's `jobs.conf` in it (admin README step 3). The admin repo carries `deploy/jobs.conf`, `deploy/run-job.sh` (a copy of the portal's: the job units run the live release's) and `deploy/staging.env.example`.
- 2026-10-31 (implementation): the admin repo has `locale/` (the release command's `compilemessages` refuses a project without one) and silences `mytp_core.E001`/`E002` (no field keys; a test fails if an encrypted field appears while silenced). The wsgi does not call `validate_field_keys()` until keys exist.
- 2026-10-31 (implementation): the `backupdb` job runs at 02:40 UTC (dev's at 02:30) and names dumps `mytp-admin-<UTC>.dump.age`; it fails until admindev's `BACKUP_*` settings are filled. HTMX 2.0.11 is vendored with AdminLTE (AD-11).

## Review Triage Log

- Pass 1 (quick): 4 findings; medium 2, low 1, not-a-defect 1. Patched 3.
  - medium, patch: the shared gunicorn unit started `wsgi:application`, so dev's pre-merge releases (no root wsgi.py) could not be rolled back to. The WSGI module is now a per-site provision value (`WSGI_MODULE`, default `mytreatplan.wsgi`; admindev `mytp_admin.wsgi`); dev's unit is unchanged; root wsgi.py files removed.
  - medium, patch: provisioning from an older deploy/ copy could downgrade the host-wide mytp-release (back to the site.css check) and break admindev. `RELEASE_TOOL_VERSION=2` in release.sh; provision keeps a newer installed command with a WARNING.
  - low, patch: the admin README's settings.env step pointed at the portal's staging.env.example; step 3 now copies the admin's.
  - not a defect: the acceptance criteria need the server step, still to do.
- After patches: portal full suite 1542 passed (3 known PostgreSQL-only failures); bash -n clean; admin deploy tests 18/18 (full admin suite 41/41 earlier on SQLite and PostgreSQL 17).

## Verification

**Commands:**
- Admin repo: its test command (SQLite locally; PostgreSQL 17 in CI) -- expected: all pass; `manage.py check --deploy` with staging-like env -- expected: only accepted warnings.
- Portal: full suite (non-secret test keys; venv `../.venv`; sandbox off) -- expected: all pass except the 3 known PostgreSQL-only tests; `bash -n deploy/*.sh`.

**Manual checks (if no CLI):**
- After the server step: `curl -u` the team password `https://admindev.mytpdso.com/` shows the layout; `curl https://admindev.mytpdso.com/healthz` shows `site: admindev`; `robots.txt` disallows all; the admin worker runs a sample task once.
