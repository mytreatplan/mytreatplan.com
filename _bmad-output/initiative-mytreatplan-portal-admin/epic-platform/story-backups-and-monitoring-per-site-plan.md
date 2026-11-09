---
title: 'Backups and monitoring per site'
type: 'feature'
ticket: '13'
created: '2026-11-09'
status: 'built'
baseline_revision: 'portal ec3133e, core 1574a31, admin 129bf59'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** E9 (AD-24): the three staging sites back up, but dev and admindev share the `staging/` prefix (a restore of "dev's latest" can pick the admin dump), admindev has no restore support (the script checks the portal's user table), all three report to Sentry as one `staging` environment, and the only restore test was run by hand on a laptop.

**Approach:** Each site backs up under its own prefix with its own 30-day rule; each site reports to its own Sentry environment; a shared `restoretest` job in mytp-core restores the site's latest dump into a scratch database on a server and checks it, reporting the outcome; runbooks in both repos. mytp-core 0.18.0 → admin → portal.

## Boundaries & Constraints

**Always:**
- `backupdb` unchanged in behaviour except as decided (recipients); dumps stay encrypted end to end; nothing deleted by the jobs (lifecycle rules expire).
- Restore: the target database name ends `_restore_test` and must be empty or dropped by the job itself; the latest dump is chosen by prefix **and** basename; the sanity check is per site (the site's user table and the migration count), not the portal's `accounts_user` everywhere.
- The team's `age` private key never goes to the server.
- No secrets, keys or URLs in logs or Sentry; outcome codes and sizes only.
- Steps needing OVH, Sentry or UptimeRobot settings are runbook steps for the user (hitl).

- Decision (user, 2026-11-09): prefixes `staging-<site>/` for all three sites (dev moves from `staging/`; old `staging/` dumps expire under their old rule); one 30-day rule per prefix, set by the user as bucket owner.
- Decision (user, 2026-11-09): Sentry environments named after the site: `dev`, `dev-ae`, `admindev` (production later: `portal-eu`, `portal-ae`, `admin`).
- Decision (user, 2026-11-09): a second, restore-only `age` identity: `backupdb` encrypts to both recipients (team + restore); the restore identity is stored on the host wrapped by the site's KMS key and unwrapped in memory only by `restoretest`; the team key never goes to the server.
- Decision (user, 2026-11-09): the scratch database is a `<site>_restore_test` database on each site's managed PostgreSQL service, with its own user (created by the user).
- Decision (user, 2026-11-09): backups stay daily (nightly, unchanged).
- Decision (user, 2026-11-09): UptimeRobot `/healthz` monitors added for dev-ae and admindev (runbook; the user clicks).
- Decision (user, 2026-11-09): one plan, three PRs (mytp-core 0.18.0 → admin → portal), kept whole.
- Decision (user, 2026-11-09): the automatic restore test runs weekly per site, staggered on Sunday night; a log line, a Sentry check-in (disabled on the current plan) and a Sentry error on failure.
- Decision (user, 2026-11-09): for staging, dev-ae's restore identity wrapped by dev's EU KMS service key is acceptable (only the 600 file permission between the Unix users separates dev and dev-ae); production regions each get their own key service.

**Never:** no change to the production plan, the database services' own backups, the file bucket (1.11), or AD-24's 30-day retention.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Nightly | each site | a new encrypted dump under `staging-<site>/` | — |
| Restore test | weekly, per site | latest own dump restored into the scratch database, checks pass, scratch emptied, one success line (+ check-in) | — |
| Wrong dump | admindev's dump newer in a shared prefix | never picked for dev (prefix + basename) | — |
| Corrupt / missing dump | none in 8 days, or restore fails | one Sentry error with the site and a code | — |
| Sentry | an error on dev-ae | shows under environment of dev-ae (decision below) | — |

</frozen-after-approval>

## Code Map

- mytp-core: `mytp_core/management/commands/backupdb.py` (checks :200-271, pipe :275-304, upload/head :308-324, key :75-77); `mytp_core/sentry.py` `init_sentry` :209; `mytp_core/jobs.py` slugs :147-150 (docstring on environments); `tasks.py` heartbeat :24-46.
- Portal: `deploy/restore-db.sh` (guards :56-103, `--latest` :115-116 prefix only, `accounts_user` :134), `deploy/jobs.conf:17`, `deploy/staging.env.example` :16, :25, :105, :117, `mytreatplan/settings.py` :232, :260-272, `deploy/provision.sh` :92, :313-314, :436-489, README "Backups and restore" step 10 (:277-334), "Restore test (quarterly)" :1165-1209, "Add dev-ae" step 6 :1866-1884, Sentry step 8.4 :176, UptimeRobot step 9 :242-258, :1956-1958.
- Admin: `deploy/jobs.conf:17`, `deploy/staging.env.example:21,:31`, `mytp_admin/settings.py` :103, :157-165, `staff.User` (sanity table), README :595, :610-612.
- Architecture: AD-24 (:237-243), naming conventions (sites `portal-eu`, `portal-ae`, `admin`).

## Tasks & Acceptance

**Execution:**
- [x] mytp-core: `backupdb` (recipients per decision 3), `restoretest` command (latest by prefix + basename, decrypt, restore into the scratch database, per-site checks, clean up, report), settings checks, tests with fakes, 0.18.0, README.
- [x] Portal + admin: settings (`RESTORE_*`, the user table per site), `jobs.conf` entries, `staging.env.example`, `restore-db.sh` fixed (basename, per-site table) for manual use, runbooks (prefix move, rules, Sentry environments and alert filters, restore identity, UptimeRobot).
- [x] Both sites: vendor the new core.

**Acceptance Criteria:**
- Given dev and admindev dumps in the bucket, when dev's restore test runs, then it restores a dev dump only.
- Given a failed restore, when the job ends, then the scratch database is empty and one error names the site and a code.

## Implementation Notes

Uncommitted in all three repos (branch `story/1.13-backups-monitoring` each); nothing pushed.

**mytp-core 0.18.0** (version in `pyproject.toml` and `mytp_core/__init__.py`; wheel built
locally from the working tree, the release rebuilds it from the tag):
- `backupdb`: optional `BACKUP_AGE_RESTORE_RECIPIENT`; when set, `age --encrypt -r <team>
  -r <restore>` (validated as an `age1…` key, must differ from the team key); the success
  line adds `<n> recipient(s)`. `pg_environment(db=None, appname=…)` reused by the restore.
- `mytp_core/restore_identity.py`: the site's restore identity as a JSON file
  (`RESTORE_AGE_IDENTITY_FILE`, default `SITE_HOME/shared/restore-identity.json`, mode 600):
  AES-256-GCM under a KMS data key of `KMS_SERVICE_KEY_ID` (only the wrapped key stored),
  associated data binds it to `SITE` and the recipient. `read_file` (no KMS), `unwrap`
  (KMS, in memory), `wrap`, `write_file` (atomic, 600, no silent replace).
- `generate_restore_identity [--replace | --public]`: `age-keygen` output kept in memory,
  wrapped and written; prints only `BACKUP_AGE_RESTORE_RECIPIENT=age1…`.
- `restoretest`: settings (bucket, prefix, basename, restore recipient,
  `RESTORE_TEST_DATABASE` whose NAME ends `_restore_test`, differs from the live NAME, own
  USER; `RESTORE_TEST_USER_TABLE` default the `AUTH_USER_MODEL` table;
  `RESTORE_TEST_MAX_AGE_DAYS` default 8) → tools (`age`, `pg_restore` ≥ scratch server) →
  `BACKUP_TMP_DIR` 0700 → identity file matches the recipient → scratch emptied if a killed
  run left anything → list `<prefix><basename>-`, newest by the name's UTC stamp with a
  full-name regex → download (ciphertext) + size check → identity unwrapped in memory and
  passed to `age --decrypt --identity /dev/fd/<n>` through a pipe → `pg_restore --no-owner
  --no-privileges --single-transaction --exit-on-error --dbname=<scratch>` → checks
  (migrations present, every restored migration known to the live DB, user table present)
  → always: scratch reset (drop non-system schemas, `CREATE SCHEMA public`, recount 0),
  file removed. One `CommandError` `restoretest: site=<SITE> code=<code>: <detail>`
  (codes settings, tools, tmp-dir, identity, scratch-db, list, no-dump, stale-dump,
  download, decrypt, restore, check, cleanup, interrupted); `age`/`pg_restore` stderr is
  discarded (it can quote rows), DB errors reported by class + SQLSTATE only, no
  exception chaining. Success: `restoretest: ok site=… dump=<stamp> (<h> h old, <bytes>
  bytes) migrations=<restored>/<live> users=<n> in <s>s`. Never writes or deletes objects.
  After review: refuses a scratch role with `rolsuper`/`rolcreaterole`/`rolcreatedb`
  (code `scratch-db`); ignores PG* environment variables (hidden while psycopg connects;
  `pg_environment` already drops them); refuses a newest dump dated > 1 h in the future
  (`stale-dump`); `\Z`-anchored regexes (scratch name, user table, dump name, basename,
  age keys); downloads with `download_fileobj` into the open mkstemp file; when
  `pg_restore` fails first and `age` dies of SIGPIPE the code is `restore`; SIGTERM is a
  `BaseException` (`Interrupted`) so no `except Exception` swallows it and the clean-up
  still runs (code `interrupted`). Docstrings say secrets are dropped (`del`), not zeroed.
  `write_file` and `generate_restore_identity` treat a symlink (even dangling) as an
  existing file and replace the link, never its target.
- `jobs.py`: optional 4th jobs.conf field, the sites a line applies to; `Job.sites`,
  `applies_to`, `parse_job_lines`, `parse_jobs(text, site=None)` / `load_jobs(path=None,
  site=None)` default to `settings.SITE`; a name may repeat only on lines for disjoint site
  lists. Docstring of `monitor_slug` now says each site has its own Sentry environment.
- Tests: `tests/test_restoretest.py` (fakes for age, age-keygen, pg_restore, S3, KMS and
  the scratch DB; both acceptance criteria; pg_restore failing first, future dump,
  trailing newlines, powerful scratch role, PG* variables, identity pipe closed in the
  parent and not passed to pg_restore, SIGTERM clean-up, no stray download file,
  symlinks, no file left when the KMS fails), `tests/test_restoretest_e2e.py` (real
  pg_dump/age/age-keygen/pg_restore on PostgreSQL, 4 tests incl. pg_restore failing first
  → `restore` and a damaged dump → exactly `decrypt`; with `MYTP_CORE_TEST_DB=postgres` it
  **fails** when a tool is missing, too old, or the user cannot create a role/database),
  per-site job lines in `test_operations.py`, the second recipient in `test_backupdb.py`.
  CI (`.github/workflows/ci.yml`) installs `age` and `postgresql-client-17` (PGDG), puts
  `/usr/lib/postgresql/17/bin` first on PATH and checks the versions, so the e2e runs
  there. README: "Restore test" section, settings, compatibility note for 0.18.0.

**Portal:** `settings.py` (`BACKUP_AGE_RESTORE_RECIPIENT`, `RESTORE_AGE_IDENTITY_FILE`,
`RESTORE_TEST_DATABASE` from `RESTORE_TEST_DATABASE_URL` with `DB_SSLMODE`/`DB_SSLROOTCERT`,
`RESTORE_TEST_USER_TABLE='accounts_user'`); `jobs.conf` (`restoretest` Sun 21:30 UTC for
dev, 22:30 for dev-ae); `provision.sh` (reads the optional sites field, installs only the
site's own lines; the field must not be empty, spaces around commas are allowed, each
site is validated like core's `SITE_RE` and may appear once); `staging.env.example` (`SENTRY_ENVIRONMENT=dev`,
`BACKUP_PREFIX=staging-dev/`, the two new keys, dev-ae differences); `restore-db.sh`
(`--basename`, `--user-table`, `BACKUP_PREFIX` limited to `[A-Za-z0-9._/-]` and stripped
as text, `--latest` by prefix + basename, newest by the name's stamp, listed before the
dry run ends so a dry run names the dump); `deploy/README.md` (overview rows, step
8.4/8.6 Sentry, step 9 UptimeRobot, step 10, Scheduled jobs and Rollback notes on
mytp-core < 0.18, "Restore test (weekly, on the server)", "Per-site backups, restore
test and monitoring", the one runbook for all three sites, "Restore test (manual)",
"Add dev-ae"); tests in `test_backups.py` (env example, per-site jobs, provision's job
loops run in bash against a fake systemd folder: site filter, bad sites fields,
overlapping site lines, a timer removed when its line moves to another site, the admin
`jobs.conf` for admindev; restore-db.sh `--latest`/`--dry-run`/`--basename`/
`--user-table`/prefix checks; settings by reloading the module under a MonkeyPatch'd
environment), `test_monitoring.py`, `test_vendored_core.py`. Vendored `mytp_core-0.18.0` replaces 0.17.0.

**Admin:** `settings.py` (same keys, `RESTORE_TEST_USER_TABLE='staff_user'`); `jobs.conf`
(`restoretest` Sun 23:30 UTC); `staging.env.example` (`SENTRY_ENVIRONMENT=admindev`,
`BACKUP_PREFIX=staging-admindev/`, the two new keys); README (wheel name, steps 9–10,
Sentry, new "Backups and the restore test": a summary table and a pointer to the
portal's runbook with only admindev's particulars: its values, its `jobs.conf` through
`~/deploy-admindev` with `WSGI_MODULE`, its Sentry monitors, rollback); tests
(`layout/tests/test_deploy.py` wheel name, jobs, env example, settings by reload;
`interface/tests/test_vendored_core.py`). Vendored `mytp_core-0.18.0` replaces 0.17.0.

**Deviations from the plan:**
1. Staggering dev and dev-ae needed a per-site schedule in one shared `jobs.conf`: new
   optional 4th field (mytp-core `jobs.py` + portal `provision.sh`). An older
   `provision.sh` refuses such a line, so dev/dev-ae must be re-provisioned from the
   merged portal `deploy/` (step 8 below). Production portal sites have no `restoretest`
   line (production plan out of scope).
2. One restore identity **per site** (decision 3 says "a second identity"), each wrapped
   by its site's KMS service key. admindev's service key is its own, so dev's or dev-ae's
   KMS access does not open admindev's identity file. dev-ae's identity, however, is
   wrapped by dev's EU service key (as its field keys): between dev and dev-ae only the
   600 file permission between the Unix users `mytp-dev` and `mytp-dev-ae` separates the
   two identity files. Accepted for staging (Decision, user, 2026-11-09, frozen block);
   production regions get their own key services.
3. The "KMS-wrapped file" is envelope encryption (KMS data key + AES-GCM, as the field
   keys), not a direct KMS encrypt call.
4. `BACKUP_AGE_RESTORE_RECIPIENT` is optional in `backupdb` (nightly backups keep working
   before the setup); `restoretest` requires it (code `settings`).
5. The per-site check is stronger than a migration count: every restored migration must
   be known to the live database (another site's dump fails), plus the user table.
6. The scratch database is emptied by dropping its schemas and recreating `public` (no
   `DROP DATABASE`: the managed-service user cannot), so the restore user must own its
   database and `public` schema (runbook step 5).
7. "Settings checks" are in-command (code `settings`), as `backupdb`'s; no Django system
   check. `init_sentry` is unchanged: the environments are only `settings.env` values.
8. `pg_restore`/`age` output is never shown or logged (codes and sizes only).
9. README "Restore test (quarterly)" became "Restore test (manual)".

**User steps on staging, in order.** One source of truth for all three sites: the
portal's `deploy/README.md`, "Per-site backups, restore test and monitoring" (the admin
README only points there, with admindev's particulars).
0. Release: commit and tag mytp-core `v0.18.0`, push, CI green (it now installs `age` and
   the PostgreSQL 17 client and runs the e2e); then the admin PR, then the portal PR
   (decision 7); wait for each deploy to be green.
1. As the bucket owner: read the lifecycle rules first (only `expire-dumps-30d` for
   `staging/` and `expire-dev-ae-dumps-30d` for `staging-dev-ae/` may be there; anything
   else: stop and keep it), then put the four 30-day rules with their IDs
   (`expire-dumps-30d` staging/, `expire-dev-dumps-30d` staging-dev/,
   `expire-dev-ae-dumps-30d` staging-dev-ae/ unchanged, `expire-admindev-dumps-30d`
   staging-admindev/) and read them back.
2. On the host: dev's `BACKUP_PREFIX=staging-dev/`, admindev's `staging-admindev/` (sed,
   prints nothing; dev-ae already `staging-dev-ae/`); check each file with its own value.
3. Sentry first: the issue alert and the metric monitors filtered on `staging` → All
   Environments (so no alert goes dark).
4. `SENTRY_ENVIRONMENT=<site>` in the three `settings.env` (each checked with its own
   value); restart gunicorn + worker; a test message from each site shows under its
   environment.
5. Managed PostgreSQL panel: plain databases and users `dev_restore_test`,
   `dev_ae_restore_test`, `admindev_restore_test` (passwords in the password manager;
   never `avnadmin` or a user that can create roles or databases); as `avnadmin`, make
   each user owner of its database and `public` schema and check its role flags are
   `f|f|f`.
6. Each site: delete an empty `RESTORE_TEST_DATABASE_URL=` line, append the real one with
   the getpass prompt; count 1; psql check.
7. Each site: delete an empty `BACKUP_AGE_RESTORE_RECIPIENT=` line, run
   `manage.py generate_restore_identity` (live release) and append its recipient line;
   counts 1; `restore-identity.json` is 600.
8. Check each site's live release reports `mytp_core.__version__` ≥ 0.18.0, then
   re-provision dev, dev-ae (merged portal `deploy/`) and admindev (`~/deploy-admindev`
   with the admin `jobs.conf`, `WSGI_MODULE=mytp_admin.wsgi`): three `restoretest`
   timers.
9. `backupdb` by hand on each site: `staging-<site>/…`, `2 recipient(s)`.
10. `restoretest` by hand on each site: `restoretest: ok site=…`; one failure check on dev
    (unreachable scratch URL) → one Sentry issue in environment `dev`.
11. Disable the three new `mytp-<site>-restoretest` cron monitors (plan without crons).
12. UptimeRobot: monitors for dev-ae and admindev `/healthz` (three Up).
13. Next morning, as bucket owner: one fresh dump under each new prefix, none new under
    `staging/`. After 30 days the `staging/` rule may be dropped.

Rollback note (runbook, "Scheduled jobs", "Rollback"): once step 8 has installed the new
`jobs.conf`, a release with mytp-core < 0.18.0 cannot read it; re-provision from that
commit's `deploy/` before rolling back.

## Verification results

Local, 2026-11-09, after the review patches (throwaway PostgreSQL 17.11 cluster under
`$TMPDIR`, deleted afterwards):

| Suite | SQLite | PostgreSQL 17 |
|-------|--------|---------------|
| mytp-core | 1302 passed, 4 skipped (the e2e tests need PostgreSQL) | 1306 passed (4 e2e included) |
| Portal | 2336 passed, 8 skipped, 3 failed (the known PostgreSQL-only `accounts/tests/test_crypto.py` ones) | 2347 passed |
| Admin | 1054 passed, 8 skipped | 1062 passed |

- `manage.py check`: no issues on both sites (SQLite and PostgreSQL); `makemigrations
  --check`: no changes on both.
- Real end to end with `pg_dump`/`age`/`age-keygen`/`pg_restore` (Homebrew 17, age 1.3.2),
  only the bucket and the KMS faked: core's `test_restoretest_e2e.py` (4 tests; without
  `age` on PATH they error instead of skipping), and the sites' own migrated schemas
  through their settings: portal `restoretest: ok site=dev … migrations=88/88` (a newer `mytp-admin-…` object in the same prefix was not taken), admin `restoretest: ok site=admindev …
  migrations=59/59`; then a damaged dump gave exactly `code=decrypt` and the scratch
  database had 0 relations after both.
- 0.18.0 wheel rebuilt from the patched working tree, re-vendored in both sites,
  installed in both venvs.

## Plan Change Log

## Review Triage Log

Round 1 (coordinator review, 2026-11-09), all accepted and patched:

| # | Finding | Verdict | Fix |
|---|---------|---------|-----|
| 1 | pg_restore failing first reported as `decrypt` (age SIGPIPE) | real | `restore` when `load_rc != 0` and `age_rc == -SIGPIPE`; fake and real-tool tests |
| 2 | e2e skipped in CI | real | CI installs age + postgresql-client-17 (PGDG); e2e fails, not skips, on PostgreSQL |
| 3 | dump dated in the future | real | > 1 h ahead → `stale-dump`; tests |
| 4 | `$` matches before a trailing newline | real | `\Z` anchors (scratch name, table, dump name, basename, age keys); tests |
| 5 | powerful scratch role; PG* variables | real | runtime refusal of rolsuper/rolcreaterole/rolcreatedb; PG* hidden at connect; README: never avnadmin |
| 6 | boto3 `download_file` temp file left after a kill | real | `download_fileobj` into the open mkstemp file; test |
| 7 | docstrings implied wiping | real | "dropped (`del`), not zeroed" |
| 8 | deviation 2 overclaimed | real | reworded; user decision added to the frozen block |
| 9 | missing tests (symlink, KMS failure, pipe fd, SIGTERM) | real | added; the SIGTERM test found that `except Exception` swallowed the interrupt: now an `Interrupted(BaseException)` |
| 10 | rollback below core 0.18 breaks every job | real | runbook, Scheduled jobs and Rollback notes; step 8 checks the live core version first |
| 11 | two copies of the user steps | real | portal runbook is the one source; admin README points to it |
| 12 | alerts dark between steps | real | Sentry alerts (All Environments) moved before the environment change |
| 13 | lifecycle put could drop unknown rules / renamed dev-ae's rule | real | read first, stop on unknown rules; IDs kept |
| 14 | empty template lines before appended values | real | `sed -i` deletes empty lines first; counts must be 1 |
| 15 | combined count checks | real | one file at a time with its own value |
| 16 | restore-db.sh prefix as a regex; dry run did not list | real | prefix validated and stripped as text; `--latest` lists before the dry-run exit |
| 17 | provision sites field laxer than core | real | empty field/entry refused, spaces around commas allowed, SITE_RE per site, duplicates refused |
| 18 | missing provision/settings tests | real | bash-run job loops (duplicates, timer removal, admin jobs.conf), settings tests by reload with MonkeyPatch only |

## Verification

**Commands:**
- mytp-core, portal and admin suites — all pass; CI on PostgreSQL.

**Manual checks (staging, with the user):**
- New prefixes and rules (`--inspect`-style listing); a night later, three fresh dumps each under its prefix; `restoretest` run on each site succeeds; Sentry shows three environments (a test message from each site); UptimeRobot shows three monitors.
