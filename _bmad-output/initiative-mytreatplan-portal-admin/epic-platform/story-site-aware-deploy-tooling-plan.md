---
title: 'Site-aware deploy tooling'
type: 'feature'
ticket: '1'
created: '2026-10-30'
status: 'done'
baseline_revision: 'a55f95b9fde89ac28be94ab2402bac178def89ea'
route: 'full'
route_source: 'auto'
risk: 'high'
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

**Problem:** The deploy tooling assumes one site per host: the `mytp` user, `/srv/mytp`, `mytp-gunicorn`, `mytp-job@*` timers (provision deletes any it does not list), one nginx site, one sudoers rule, and CI that ships release.sh with each deploy. Staging must run dev, dev-ae and admindev side by side (AD-12), and the admin repo's CI must deploy with the same tooling.

**Approach:** Give provision.sh, release.sh, run-job.sh, the systemd units, jobs, nginx, sudoers and the staging CI job a site name, so each site has its own Linux user, home, socket, units, timers, nginx site, htpasswd file and sudoers rule. Provision installs release.sh host-wide so any repo's CI can deploy a tarball to its site. Add a `SITE` setting the app reads for its paths. Move the live dev site to the new layout with a runbook a person runs.

## Decisions

- Naming (user, 2026-10-30): Linux user `mytp-<site>`, home `/srv/mytp-<site>`, units `mytp-<site>-gunicorn.{socket,service}` and `mytp-<site>-job@<job>.{service,timer}`, nginx `mytp-<site>.conf`, htpasswd `/etc/nginx/mytp-<site>.htpasswd`, sudoers `/etc/sudoers.d/mytp-<site>-deploy`; staging sites `dev`, `dev-ae`, `admindev`.
- Moving dev (user, 2026-10-30): in place, with a runbook the user runs over SSH (about 5 minutes of downtime); `/srv/mytp` kept one day as a fallback, then deleted by the runbook's last step.
- dev's database (user, 2026-10-30): unchanged; only new sites get their own database.

## Boundaries & Constraints

**Always:** idempotent provision (a second run reports no changes); one site's provision or deploy never touches another site's files, units, timers or nginx config; host-wide pieces (packages, SSH hardening, ufw, certbot, the release command) stay shared; the existing release flow is kept (flock, venv per release, migrate before switch, health check, rollback, keep 5); secrets stay in each site's settings.env, typed by a person.

**Never:** no change to the database or its contents; no dev-ae or admindev site (entries 7 and 10); no Sentry slug change (entry 5); no production deploy job.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| New site | `provision.sh <site> <domain> <htpasswd-user>` on a host with dev | Second site created; dev's units, timers, nginx and files unchanged | — |
| Re-run | Same command again | "No changes." | — |
| Bad site name | Name with spaces, uppercase or path characters | Refused before any change | Exit non-zero with the allowed pattern |
| Deploy | CI uploads a tarball and runs the host release command for its site | Release built, switched, health-checked under that site | Rollback to the previous release on a failed check, as today |
| Unknown site on deploy | Release command for a site that was never provisioned | Refused | Exit non-zero naming the missing home |

</frozen-after-approval>

## Code Map

- `deploy/provision.sh` -- steps 1–3 and the letsencrypt dir/certbot.timer are host-wide; steps 4–10 (user, dirs, sudoers, gunicorn units, jobs, htpasswd, nginx, cert) become per site. Hard-coded names at :33-36, :147-148, :154-173, :181-237 (the unlisted-timer glob at :205 must match only this site), :265-283. Idempotency helpers `install_file` (:55), `ensure_dir` (:67) and the CHANGES counter are reused.
- `deploy/release.sh` -- inputs `<sha> <tarball>`; APP_HOME :13, SOCKET :15, SERVICE :16, cleanup of `incoming/$SHA-release.sh` :35, restart :66, health check over the socket :45-65, rollback :131-148. Becomes the host-wide command taking the site first.
- `deploy/run-job.sh` :12 -- APP_HOME from the site.
- `deploy/sudoers/mytp-deploy` -- one rule per site, restarting only that site's gunicorn.
- `deploy/systemd/` -- `mytp-gunicorn.{socket,service}`, `mytp-job@.service`, `mytp-job.timer.template`: user, paths, socket and unit names become per site; a per-site job unit template (the `%i` stays the job name).
- `deploy/nginx/mytp.conf.template` -- upstream name, `ssl_session_cache` zone, htpasswd path and static alias per site; `__DOMAIN__` stays.
- `.github/workflows/ci.yml` :117-163 -- SSH user, upload paths and the release invocation per site; secrets `STAGING_HOST`, `STAGING_SSH_KEY`, `STAGING_KNOWN_HOSTS` unchanged.
- `mytreatplan/settings.py` :177 `BACKUP_TMP_DIR` default and `mytp_publicsite/jobs.py` :19 `INSTALLED_JOBS_FILE` -- derive from the new `SITE` setting (home of the site) instead of `/srv/mytp`.
- `deploy/staging.env.example` -- absolute `/srv/mytp/shared/...` paths (:64, :103-104) and a new `SITE` line.
- `deploy/README.md` -- every `/srv/mytp`, `mytp@`, `mytp-gunicorn`, `mytp-job@` reference (about 150); add the dev migration runbook.
- Tests pinning the old strings: `mytp_publicsite/tests/test_deploy.py` :60-75, :92-105, :122; `test_monitoring.py` :521-560; `test_backups.py` :127, :170, :393, :571. Slug tests :477/:493 stay (slug is entry 5).
- Do not change `00-mytp-hardening.conf` (host-wide), `backupdb`'s `PGAPPNAME`, `kmscheck`'s data-key name, or `/healthz`.

## Tasks & Acceptance

**Execution:**
- [x] `deploy/provision.sh` -- take the site name first, validate it, split host-wide from per-site steps, name every per-site artifact from the site, install the release command host-wide.
- [x] `deploy/release.sh`, `deploy/run-job.sh`, `deploy/sudoers/`, `deploy/systemd/`, `deploy/nginx/` -- per-site names and paths; release refuses an unprovisioned site.
- [x] `.github/workflows/ci.yml` -- deploy dev through the host release command as the dev site's user.
- [x] `mytreatplan/settings.py`, `mytp_publicsite/jobs.py`, `deploy/staging.env.example` -- `SITE` setting; paths derived from it.
- [x] `mytp_publicsite/tests/` -- update pinned strings; add tests that every per-site template and script uses the site placeholder and no `/srv/mytp` or `mytp-gunicorn` literal remains; `bash -n` on the scripts.
- [x] `deploy/README.md` -- per-site commands throughout and a "Move dev to the per-site layout" runbook.

**Acceptance Criteria:**
- Given the branch merged and the runbook done on staging, when CI deploys master, then dev serves from its per-site home, units and nginx site, and `/healthz` answers 200.
- Given dev on the new layout, when provision runs for a throwaway second site name and then for dev again, then the second site gets its own user, home, units and nginx config, dev is unchanged, and the dev re-run prints "No changes."
- Given the old single-site layout, when the runbook's last step runs, then no `mytp-gunicorn`, `mytp-job@*` timer, `sites-enabled/mytp.conf` or `/etc/sudoers.d/mytp-deploy` remains active.

## Implementation Notes

- Templates renamed to site-neutral names with a `__SITE__` placeholder: `systemd/gunicorn.{socket,service}.template`, `systemd/job@.service.template`, `systemd/job.timer.template`, `nginx/site.conf.template`, `sudoers/deploy.template`. provision.sh renders them with sed.
- Site name pattern `^[a-z]([a-z0-9-]{0,18}[a-z0-9])?$` (max 20, so `mytp-<site>` fits a Linux user name), the same in provision.sh, release.sh, run-job.sh and settings.py.
- Host release command: `/usr/local/bin/mytp-release <site> <sha> <tarball>`, installed by provision.sh from `deploy/release.sh`. It refuses an unprovisioned site (names `/srv/mytp-<site>`), a different user than `mytp-<site>`, and a settings.env whose `SITE` differs. The tarball is the only upload; CI no longer ships release.sh.
- run-job.sh takes `<site> <name>`; the job unit passes the site.
- `SITE` setting (default `dev`, validated) and `SITE_HOME` = `/srv/mytp-<SITE>`; `BACKUP_TMP_DIR` default and `INSTALLED_JOBS_FILE` derive from it.
- Nginx upstream and ssl session cache zone are `mytp-<site>-gunicorn` / `mytp-<site>-ssl`.
- Runbook order: merge first (the deploy fails at SSH and changes nothing), stop the old site, provision dev, copy shared files and the deploy key, re-run the failed deploy job. Downtime = that deploy job.

## Plan Change Log

## Review Triage Log

- Pass 1 (quick): 2 findings; medium 1, low 1. Both patched.
  - medium, patch: README runbook step 9 (fallback) claimed deploys were blocked, but steps 4–6 had already set up `mytp-dev` (home, settings.env, sudoers, deploy key), so a master push would migrate the shared dev database under the restored old release and restart `mytp-dev-gunicorn`. The fallback now moves the deploy key aside, says not to merge while in fallback, and the retry puts the key back and re-runs provision.
  - low, patch: `mytreatplan/settings.py` KMS comment left one 126-character line; rewrapped to the file's width.
- After patches: full suite 1574 passed, 3 known PostgreSQL-only failures; `manage.py check` clean; `bash -n` clean on deploy/*.sh. shellcheck not installed.

## Verification

**Commands:**
- Full suite, from the repo root (non-secret test keys; the venv is `../.venv`; browser tests need the sandbox off): `export FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU="; export DATABASE_URL="sqlite:///$TMPDIR/x.sqlite3"; ../.venv/bin/python -m pytest -q -p no:cacheprovider --tb=line` -- expected: all pass except the 3 known PostgreSQL-only tests in `accounts/tests/test_crypto.py`.
- `bash -n deploy/*.sh` and, if installed, `shellcheck deploy/*.sh` -- expected: no errors.

**Manual checks (if no CLI):**
- On staging, a person runs the README runbook over SSH, then the acceptance checks above.

## Staging

- Merged in PR #49 as merge commit a2abe94babfd2faaed637587f17b398f37e34217 (2026-10-30). The PR's CI first failed on the flaky browser test `signup/tests/browser/test_failure_policies.py::test_row5_email_in_a_pending_registration` and passed on re-run (deferred-work entry).
- Runbook "Move dev to the per-site layout" run by Claude over SSH with the user's go-ahead (2026-10-30), steps 2–8: deploy/ copied; old site stopped; provision `dev` (38 changes); shared files carried over (no old path left, SITE=dev); deploy key installed; CI run 41035750675 re-run: test and deploy-staging green. Downtime about 10 minutes.
- Checks: `current` → releases/a2abe94…; `mytp-dev-gunicorn.socket` active; 6 `mytp-dev-job@*` timers, 0 `mytp-job@*`; `mytp-dev-job@clearsessions` result success; provision re-run "No changes."; https://dev.mytpdso.com/healthz 200.
- Throwaway site `tmpcheck` provisioned (own user, home, 6 timers, nginx site), dev's units/nginx/sudoers byte-identical before and after, dev re-run "No changes."; tmpcheck removed the same hour.
- The user confirmed signup and login pages open on dev (2026-10-30).
- Step 10 run by Claude over SSH with the user's OK (2026-10-31): old mytp-job@* timers and units, mytp-gunicorn socket and service, nginx mytp.conf and mytp.htpasswd, /etc/sudoers.d/mytp-deploy, user mytp and /srv/mytp removed; checks: no old units loaded, sites-enabled has only mytp-dev.conf, sudoers only mytp-dev-deploy, no user mytp, /srv holds only mytp-dev; dev's socket, worker and 8 timers active; /healthz 200.
