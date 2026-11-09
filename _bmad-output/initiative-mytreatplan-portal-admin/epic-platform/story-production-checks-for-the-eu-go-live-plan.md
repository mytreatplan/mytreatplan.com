---
title: 'Production checks for the EU go-live (with the PostgreSQL 18 client)'
type: 'chore'
ticket: '14'
created: '2026-11-09'
status: 'built'
baseline_revision: 'portal d796b10, admin 60a90f8'
route: 'full'
route_source: 'user'
risk: 'low'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** E1, E9: epic-eu-golive's Decision requires each epic to list runnable production checks under its Done when 3; epic-platform has none yet. Found during 1.13's staging: admindev's database is a separate PostgreSQL 18 service while the host has only the version 17 client tools, so admindev's nightly `backupdb` has failed every night since at least 2026-11-03 (and its restore test would fail too); the runbooks still say one PostgreSQL 17 service serves all three sites.

**Approach:** (1) `provision.sh` installs the PostgreSQL 18 client tools host-wide (they also serve the 17 databases); docs corrected (admindev on its own 18 service, its scratch database there, the team Mac's client); admindev's backup and restore test proved on staging. (2) epic-platform's production checks written as a sub-list under epic-eu-golive's Done when 3, in the format of epic-admin-access's (entry 12): one runnable check per epic-platform Done when item and per area (deploy, admin instance, backups and restore, KMS, storage, scanning, monitoring), with production prerequisites named.

## Boundaries & Constraints

**Always:**
- Client tools: installed by `provision.sh` idempotently (written only when different, as the other steps); `pg_dump`/`pg_restore` resolve to version 18 through Debian's wrapper; the existing "client ≥ server" check unchanged; `postgresql-client-17` may stay.
- Production checks: only `portal-eu` and `admin` (portal-ae belongs to epic-uae-launch); each check is a command or a short click path with its expected result; temporary test users as the eu-golive Decision says; prerequisites listed where production differs from staging (Sentry plan upgrade for cron monitors, production bucket/prefixes/keys/environments, no sample settings).
- epic-eu-golive: only the new sub-list under Done when 3 (and a Notes line); nothing else in that file changes.

- Decision (user, 2026-11-09): the PostgreSQL 18 client comes from the official PostgreSQL apt repository (PGDG, signed, its key pinned to the repo), added by `provision.sh`; be ready for when Debian ships 18: the PGDG source is a separate, removable step, the package name stays `postgresql-client-18` (the same in Debian), and the runbook says how to drop PGDG then (remove the source and key files, `apt-get update`, the package then comes from Debian).
- Decision (user, 2026-11-09): pin the explicit `postgresql-client-18`; later majors are added deliberately.
- Decision (user, 2026-11-09): production checks cover `portal-eu` and `admin` only; checks that depend on decisions still open in 1.12 or on the Sentry plan upgrade carry that prerequisite.
- Decision (user, 2026-11-09): one portal PR (provision, tests, docs, the eu-golive sub-list) plus an admin README fix; no mytp-core change.

**Never:** no production deploy, no change to backup/restore code, no staging-only setting enabled in production checks.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Provision | host with client 17 only | client 18 installed, `pg_dump --version` 18 | — |
| Re-provision | already installed | 0 changes | — |
| admindev backup | after provision | `backupdb: uploaded staging-admindev/...` | — |
| dev backup | 17 server, client 18 | uploaded as before | — |
| eu-golive | after the story | an epic-platform sub-list, one check per Done when item | — |

</frozen-after-approval>

## Code Map

- Portal: `deploy/provision.sh` `PACKAGES` :91-92, install loop :148-159, `install_file` (pattern), ClamAV step :162 (placement); tests `mytp_publicsite/tests/test_deploy.py` :410 (apt stubs), `storage/tests/test_scan_deploy.py` :86-87; README :353-354 (`pg_dump --version` 17), :1313-1314 (one service), :1473, `deploy/restore-db.sh` :32, :37 (`MIN_PG_MAJOR=17`).
- mytp-core (no change): `backupdb.py` :247-255 (`shutil.which`), :280-301 (client ≥ server); `restoretest.py` :386-408.
- Admin: README :521 (service).
- Epics: `epic-platform/epic-platform.md` (Done when 1-5), `epic-eu-golive/epic-eu-golive.md` (Done when 3, Decision 2026-11-05), precedent `epic-admin-access/story-production-checks-for-the-eu-go-live-plan.md` and its sub-list (commit 29de64e).
- Runbook commands per area: `/healthz`, `check --deploy`, `kmscheck`, `storage_bucket --inspect`, `sweep_storage`, `clamd_check`, `backupdb`, `restoretest`, `systemctl list-timers`, `enqueue_sample_task`.

## Tasks & Acceptance

**Execution:**
- [x] `provision.sh`: PGDG source (key, sources file with `signed-by`), `postgresql-client-18`; tests; docs corrected (services, versions, team Mac).
- [x] epic-eu-golive Done when 3: the epic-platform sub-list; epic-platform Notes line.

**Acceptance Criteria:**
- Given the host re-provisioned, when admindev's `backupdb` runs, then it uploads under `staging-admindev/`.
- Given epic-eu-golive, when read, then every epic-platform Done when item has a runnable production check.

## Implementation Notes

- `deploy/provision.sh`: the package loop became `install_packages` (one `apt-get update` per run, `APT_UPDATED`). New steps after "Packages": "PostgreSQL apt repository (PGDG)" (its own block, removable with the `PGDG_*`/`OS_RELEASE` settings) and "PostgreSQL client tools" (`PG_CLIENT_PACKAGES=(postgresql-client-18)`).
  - Key: fetched from `https://www.postgresql.org/media/keys/ACCC4CF8.asc` at each run and checked with `gpg --with-colons --show-keys`. The file must hold exactly one primary key, with fingerprint `B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8` (pinned as `PGDG_KEY_FINGERPRINT`), and its `pub` validity must be neither `e` (expired) nor `r` (revoked). Anything else stops the run with an ERROR before anything is written, the installed key included: another key, an expired or revoked one, or a garbled download. The key is installed to `/etc/apt/keyrings/mytp-pgdg.asc` only when it differs. A failed download keeps an installed key (WARNING) and stops on a host without one. The pinned fingerprint was checked against the published key by hashing its key packet (one primary key, no subkeys).
  - `/etc/apt/sources.list.d/mytp-pgdg.sources` (deb822: `URIs` apt.postgresql.org, `Suites <codename>-pgdg` from `/etc/os-release`, `Architectures` from `dpkg --print-architecture`, `Signed-By` that key) and `/etc/apt/preferences.d/mytp-pgdg` (review patch 2) are each written only when they differ. The preferences have two entries: `Package: *` from origin apt.postgresql.org at priority 100, and `postgresql-client-18 postgresql-client-common libpq5` from that origin at 500. `apt-get update` runs only when one of the three files changed.
- Deviation, `PACKAGES`: `postgresql-client` (the metapackage) was replaced by the explicit `PG_CLIENT_PACKAGES`, and `gpg` was added for the key check. Reason: from PGDG the metapackage follows the newest major on an `apt upgrade`, against "later majors are added deliberately". The runbook's one-off removes Debian's metapackage on the staging host (`apt-cache policy postgresql-client` then shows `Installed: (none)`); `postgresql-client-17` stays.
- Deviation / consequence, libpq (review patch 1): `postgresql-client-18` needs PGDG's `libpq5` 18, which replaces Debian's for the whole host. psycopg 3.3.6 is installed without its binary extra, so every site's database connections, TLS `verify-full` included, load that library. libpq security fixes now come from PGDG updates, not Debian's security archive. The README says so, and the staging checks print `psycopg.pq.__impl__`/`version()` (expected `python 18xxxx`) and run `manage.py check --database default`, a real `verify-full` connection. The one-off restarts each site's Gunicorn and worker so they load the new library.
- Deviation, `deploy/restore-db.sh` (review patch 7): only its comment and two error messages changed. They now say that a dump made by `pg_dump` 18 needs `pg_restore` 18 (`brew install postgresql@18`). `MIN_PG_MAJOR` stays 17 (the plan forbids restore logic changes); raising it to 18 is left for the user to decide. The existing test for "pg_restore 16 is older than 17" still passes.
- Tests: `mytp_publicsite/tests/test_deploy.py`.
  - `no_privileges` also stubs `curl` and `gpg`.
  - `_pgdg_steps` runs the packages, PGDG and client steps with `sudo`, `apt-get`, `dpkg`, `dpkg-query`, `curl` and `gpg` executables. The gpg stub answers only a call with `--with-colons` and `--show-keys`, lists `pub`/`sub` rows with their validity and an `fpr` after each, and fails on a file that is no key.
  - Cases: first run (the key; the sources fields including `Architectures: amd64`; one update; the client install; 4 changes); the preferences' two entries; re-run (0 changes, no apt call); a new base package (one update); a changed sources or preferences file alone (update, file restored); a wrong primary key, an extra primary key, the pinned fingerprint only on a subkey, or no key (exit 1, none of the three files written); an expired or revoked key (exit 1, the installed key unchanged); a garbled download while a key is installed (exit 1 "not the pinned PGDG key", the installed key unchanged, no apt call: deliberately stricter than a failed download); download failures with and without an installed key; and a static test of the settings and the step order.
  - `test_backups.py`'s package test now checks `PG_CLIENT_PACKAGES`.
- Docs, portal `deploy/README.md`:
  - New section "PostgreSQL client tools": the services and versions, the source and key (validity), the apt pinning, libpq from PGDG, one explicit major, and checks (`apt-cache policy libpq5 postgresql-client-18`, `postgresql-common` staying Debian's, psycopg's libpq version, `check --database default`, `/healthz`).
  - The staging one-off: refresh `~/deploy` and `~/deploy-admindev` first and check `grep -c '^PG_CLIENT_PACKAGES='` gives 1 each; re-provision; remove the metapackage; restart and back up the three sites; end with `apt-cache policy postgresql-client` showing `Installed: (none)`.
  - Dropping PGDG: the three files, including the preferences file.
  - Elsewhere: step 10.6 (`pg_dump --version` 18); the sites table (Database row); per-site step 5 (scratch databases each on their site's service, `admindev_restore_test` on the 18 service, the owners loop per service) and step 6 (host and port from the site's service); "Restore test (manual)" (PostgreSQL 18 on the Mac).
- Docs, admin repo: `README.md` "Server setup" step 2 (its own 18 service), the restore-test table row, the manual restore note and the admindev values. `deploy/jobs.conf`'s `restoretest` comment is fixed (comment only): `admindev_restore_test` is on admindev's own PostgreSQL 18 service, and dev and dev-ae share the 17 one.
- Epics:
  - epic-eu-golive Done when 3 gets the "epic-platform (entry 14)" sub-list: Before, then 3f–3m, letters continuing after epic-admin-access's 3a–3e. In 3l, the dumps are `<production prefix>mytreatplan-<UTC>.dump.age` / `<production prefix>mytp-admin-<UTC>.dump.age`, with the prefix set in 1.12, and the failing `restoretest` command is written out in full.
  - epic-eu-golive also gets one Notes line (the 2026-11-09 Decision), and epic-platform gets one Notes line.
- Not changed: mytp-core.

## Verification results

- Portal suite (sandbox off, SQLite): 2353 passed, 8 skipped, 3 failed — the 3 known PostgreSQL-only failures in `accounts/tests/test_crypto.py` (after the review patches; 16 PGDG tests among them).
- Admin suite: not run (README and a `jobs.conf` comment only; no admin test reads the changed lines).
- Not run here: real `gpg`, `apt-get`, apt pinning and the PGDG repository (stubbed in the tests). The staging re-provision exercises them, including that `postgresql-client-18` resolves its dependencies under the preferences.
- Owed on staging (the user), the "PostgreSQL client tools" one-off:
  - Refresh both `deploy/` copies (`PG_CLIENT_PACKAGES` count 1 each).
  - Re-provision dev, dev-ae and admindev: `pg_dump --version` 18, a second run "No changes".
  - `apt-cache policy libpq5 postgresql-client-18` shows both from apt.postgresql.org; `postgresql-common` stays Debian's.
  - `apt-get remove postgresql-client`, then `Installed: (none)`.
  - Restart each site, then the psycopg check (`python 18xxxx`) and `check --database default` on each site.
  - `backupdb` on the three sites with 2 recipients each (admindev under `staging-admindev/`).
  - admindev's `restoretest`, once `admindev_restore_test` is on its 18 service.

## Plan Change Log

## Review Triage Log

Opus review, security and operations focus (2026-11-09); no blocker; the fingerprint check, https-only download, write order and idempotency hold.
- patch (medium): PGDG's `libpq5` 18 becomes every app's libpq (psycopg without a binary extra) → documented, staging checks for the libpq version and a `verify-full` connection, processes restarted.
- patch (low-medium): PGDG not pinned → apt preferences: everything from PGDG at 100, only the client, its common files and libpq5 at 500.
- patch (low): expired or revoked keys refused; `Architectures:` in the sources file; the one-off refreshes both deploy copies first and ends with the metapackage gone; eu-golive 3l wording; stronger gpg stub and key-case tests.
- patch (low, wording only): `restore-db.sh` comments and messages say a pg_dump 18 dump needs pg_restore 18; raising `MIN_PG_MAJOR` left to the user (restore code is out of scope).

## Verification

**Commands:**
- Portal suite — all pass; CI on PostgreSQL.

**Manual checks (staging):**
- Re-provision the host; `pg_dump --version` 18; `backupdb` on dev, dev-ae and admindev all upload with 2 recipients; (after the user's 1.13 steps) `restoretest` on admindev against its scratch database on the 18 service.
