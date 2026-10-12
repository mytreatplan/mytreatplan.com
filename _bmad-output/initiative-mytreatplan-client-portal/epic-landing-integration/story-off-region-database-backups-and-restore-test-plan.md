---
title: 'Off-region database backups and restore test'
type: 'feature'
ticket: '14'
created: '2026-10-12'
status: 'built'
baseline_revision: '0d5835642b34a11be05ba4d52cfc0c5b2b0f84c0'
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

**Problem:** The only copies of the staging database are OVHcloud Managed PostgreSQL's own backups, kept in the same provider region as the database (GRA). If that region or service is lost, or a backup turns out not to restore, the data is gone, and nobody has ever proved a restore works. Production (1.12) will copy whatever we set up here.

**Approach:** Keep the managed backups and document them. Add a nightly scheduled job that dumps the database, encrypts the dump on the server and uploads it to OVHcloud Object Storage in another region. Add a documented, scripted restore that a person runs into a scratch database to prove the dumps are usable.

## Boundaries & Constraints

**Always:**
- **Nightly job:** `manage.py backupdb`, listed in `deploy/jobs.conf` as `backupdb | backupdb | *-*-* 02:30:00 UTC`. It runs through 1.9's `runjob`, so a failed or missed dump raises a Sentry alert.
- **The dump:**
  - Run `pg_dump --format=custom --no-owner --no-privileges` against `DATABASES['default']`, with TLS (`sslmode` and `sslrootcert` from the existing settings).
  - Pass the password to `pg_dump` through its environment only, never in arguments, logs or error messages.
  - Check the dump client's major version against the server's and fail clearly when it is older.
- **Encryption:** the dump is encrypted before it leaves the server; there is never an unencrypted copy on disk or in the bucket. Any temporary file goes in a `0700` directory owned by `mytp` and is deleted whether the run succeeds or fails.
- **Upload:**
  - Use the S3-compatible API, configured from `shared/settings.env` with `BACKUP_S3_ENDPOINT`, `BACKUP_S3_REGION`, `BACKUP_S3_BUCKET`, `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY`, `BACKUP_PREFIX` and `BACKUP_AGE_RECIPIENT` (e.g. `staging/`).
  - Name objects `<prefix>mytreatplan-<UTC yyyymmddThhmmssZ>.dump.<ext>`.
  - After uploading, check the object exists with the expected size; a missing or wrong-size object is a failure.
- **Missing settings:** when a required setting is missing, the command stops before dumping and names the missing key.
- **Restore:**
  - `deploy/restore-db.sh` downloads (or takes) one dump, decrypts it and runs `pg_restore` into an empty scratch database. It then prints a sanity check: the number of applied migrations and of users.
  - It refuses to restore into a database that isn't empty, and refuses any target whose name doesn't end in `_restore_test`.
- **Runbook (`deploy/README.md`):**
  - confirm the managed backups and their retention in the OVHcloud panel;
  - create the bucket, the S3 user and the encryption key;
  - fill in the settings and re-run `provision.sh` (packages and the new timer);
  - run a first backup by hand;
  - the quarterly restore test, and recording its date.
- **Packages:** `provision.sh` adds `postgresql-client` and `age`.
- **Tests:** these cover the command, with `pg_dump`, encryption and S3 mocked.
- Decision (user, 2026-10-11): OVHcloud managed backups plus a nightly encrypted `pg_dump` to Object Storage in another region, with a tested restore. KMS is 1.15; production is 1.12.
- Decision (user, 2026-10-12): the bucket is in OVHcloud Object Storage **SBG** (Strasbourg).
- Decision (user, 2026-10-12): client-side encryption with **`age`**. The server holds only the public recipient (`BACKUP_AGE_RECIPIENT`); the private identity is kept offline in the team password manager and never reaches the server. Objects end in `.dump.age`.
- Decision (user, 2026-10-12): keep **30 daily dumps**, expired by a bucket lifecycle rule set in the runbook. The server's S3 user can write and read but not delete; `backupdb` never deletes. If OVHcloud SBG turns out not to support lifecycle expiration, stop and ask.
- Decision (user, 2026-10-12): restore tests run on a team Mac, into a local empty database `mytp_restore_test`, with the private key from the password manager. `restore-db.sh` targets local PostgreSQL and needs `age`, `pg_restore` ≥ 17 and S3 download access (or a dump downloaded by hand).
- Decision (user, 2026-10-12): the managed service runs PostgreSQL **17**, so Debian 13's `postgresql-client` (17) is enough; no PGDG repository.

**Never:**
- KMS (1.15), production buckets or keys (1.12), backing up media files (there are none yet).
- Credentials, keys or bucket secrets in the repo, in logs or in Sentry events.
- Restoring over `mytreatplan` or any database not named `*_restore_test`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Nightly backup | Timer fires, all settings present | One new encrypted object in the bucket; Sentry check-in ok; no files left in the temp directory | — |
| Settings missing | e.g. `BACKUP_S3_BUCKET` empty | Nothing dumped or uploaded | Exits non-zero, naming the key; Sentry error |
| Dump fails | `pg_dump` exits non-zero | No upload; temp file removed | Non-zero exit; error with no password or URL |
| Old client | `pg_dump` major version < server's | No dump | Non-zero exit naming both versions |
| Upload fails | S3 error or size mismatch | Temp file removed | Non-zero exit; Sentry error with no credentials |
| Restore | A dump, the private key, an empty `mytp_restore_test` | Tables restored; migrations and user counts printed | — |
| Restore guard | Target not empty, or not `*_restore_test` | Nothing restored | Exits non-zero with the reason |

</frozen-after-approval>

## Code Map

- `deploy/jobs.conf` -- add the `backupdb` line. Its schedule must match `mytp_publicsite/jobs.py`'s format; `test_monitoring.py` already checks every line.
- `mytp_publicsite/management/commands/runjob.py` -- runs `call_command(job.command)`; the Sentry check-ins come for free. Don't change it.
- `mytp_publicsite/management/commands/` -- new `backupdb.py`. Read the connection details from `settings.DATABASES['default']` (`NAME`, `USER`, `PASSWORD`, `HOST`, `PORT`, `OPTIONS.sslmode`, `OPTIONS.sslrootcert`), not from `DATABASE_URL`.
- `mytreatplan/settings.py` -- decouple `config()`. The `SENTRY_*` block (lines ~61–72) shows the pattern for the new `BACKUP_*` settings (default `''`). Sentry's `scrub_event` / `redact` in `mytreatplan/sentry.py` already removes emails and IPs; error messages must not contain secrets in the first place.
- `deploy/provision.sh` -- `PACKAGES=(...)` (line 38) is installed idempotently. Add `postgresql-client` and `age` there. Add an `ensure_dir` for the temp directory (`/srv/mytp/shared/backup-tmp`, `0700`, owner `mytp`).
- `deploy/staging.env.example` -- add the `BACKUP_*` keys with comments (values on the server only).
- `deploy/README.md` -- add a new runbook step 10 (backups) after `### 9. UptimeRobot`, plus `## Backups and restore` after `## Scheduled jobs`.
- `requirements.txt` -- pinned. Add the S3 client library, pinned.
- Tests: `mytp_publicsite/tests/` (no DB). New `test_backups.py`. `test_deploy.py` has the `bash -n` pattern for `deploy/*.sh`.

## Tasks & Acceptance

**Execution:**
- [x] `requirements.txt`, `mytreatplan/settings.py`, `deploy/staging.env.example` -- the `BACKUP_*` settings, plus the pinned S3 library.
- [x] `mytp_publicsite/management/commands/backupdb.py` -- check settings and versions, dump, encrypt with `age -r`, upload, verify, clean up. Never deletes objects.
- [x] `deploy/jobs.conf` -- the `backupdb` line.
- [x] `deploy/provision.sh` -- packages and the temp directory.
- [x] `deploy/restore-db.sh` -- the guarded restore and sanity check.
- [x] `deploy/README.md` -- step 10 and the "Backups and restore" section.
- [x] `mytp_publicsite/tests/test_backups.py` -- every matrix row except the restore itself. For the restore, test the guards via `bash -n` and a dry run if feasible. Also check that no secret appears in output or in exception text.

**Acceptance Criteria:**
- Given the runbook is done, when the `backupdb` timer fires, then a new encrypted object appears in the other region's bucket and Sentry shows an ok check-in.
- Given a downloaded dump and the private key, when `restore-db.sh` runs into an empty `mytp_restore_test`, then it finishes and prints migration and user counts matching staging.
- Given the bucket credentials are wrong, when the job runs, then it fails and devops@mytreatplan.com gets the Sentry alert.

## Implementation Notes

- S3 client: `boto3==1.43.90` / `botocore==1.43.90` with their transitive pins in `requirements.txt`. The client sets `request_checksum_calculation` / `response_checksum_validation` to `when_required`, since newer botocore's default CRC checksums are not accepted by every S3-compatible store.
- `backupdb` pipes `pg_dump` into `age --encrypt --recipient`; only ciphertext is written (`mkstemp` in `BACKUP_TMP_DIR`, default `/srv/mytp/shared/backup-tmp`, which must be 0700 and owned by the running user). Connection details go through libpq `PG*` environment variables (inherited `PG*` dropped). Errors are scrubbed of the DB password and S3 keys and raised `from None`, so Sentry sees no chained botocore/psycopg exception. SIGTERM (systemd timeout) is turned into an exception so the temp file is removed; stale `.part` files from a killed run are removed at start.
- Added setting `BACKUP_TMP_DIR` (not in the plan's list; defaults to the provisioned path, so `settings.env` needs nothing).
- `restore-db.sh`: `--identity` plus one of `--file` / `--object` / `--latest` (aws CLI, keys from env), `--db` (default `mytp_restore_test`), `--dry-run`. Extra guard: refuses a non-local `PGHOST`/`PGHOSTADDR`, unsets `PGSERVICE`. Restores with `--single-transaction` so a failed restore leaves the target empty.
- Verified end to end locally against the dev PostgreSQL 17 with a pass-through stand-in for `age` (not installed): real `pg_dump` -> fake S3 -> `restore-db.sh` into a scratch `*_restore_test` DB printed the same migration and user counts as the source (19 / 1); a second run was refused as not empty.

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, 2026-10-12): high 0, medium 3, low 4, false 0, maybe-false 0; 1 rejected.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | `BACKUP_PREFIX` without a trailing `/` gives keys the lifecycle rule (`Prefix: "staging/"`) never matches, so dumps never expire | medium | patch | Confirmed in `object_key()` and README 10.3. Patch: `check_settings` requires a trailing `/`, plus a test. |
| 2 | The server key's `s3:PutObject` can overwrite existing dumps; the runbook implied old dumps were safe from the server | medium | patch | Confirmed: no versioning or object lock. Patch: README states it plainly. Versioning/object lock deferred to production (1.12). |
| 3 | The stale `.part` cleanup deletes a concurrent run's in-progress file, which then crashes | low | patch | Confirmed (manual run overlapping the timer). Patch: only files older than 2 h, missing files ignored, plus a test. |
| 4 | `--latest` on an empty prefix fails in JMESPath before the friendly error | low | patch | Confirmed (`sort_by` on null). Patch: ``Contents || `[]` ``. |
| 5 | Mac-side `aws` calls set no region, so the CLI signs for us-east-1 against the `sbg` endpoint | medium | patch | Confirmed: script and README pass only `--endpoint-url`. Patch: require `BACKUP_S3_REGION`, pass `--region`. |
| 6 | Restore setup misses the keg-only `postgresql@17` `PATH` and starting the local server | low | patch | Confirmed in README. Patch: `PATH` export and `brew services start`. |
| 7 | Emptiness guard counts relations only | low | rejected | Rare (a scratch DB with functions but no tables), and `--single-transaction --exit-on-error` leaves nothing half-restored; a wider check adds complexity. |
| 8 | SIGTERM handler installed outside the `try/finally` that restores it | low | patch | Confirmed. Patch: temp file created first, handler installed inside the `try`. |

## Verification

Done locally (2026-10-12) after the review patches: `pytest` 209 passed, 6 skipped (PyYAML workflow tests, run in CI); `python manage.py check` no issues; `bash -n deploy/*.sh` ok. Implementer's end-to-end run: real `pg_dump` from the local PostgreSQL 17 dev DB, a stand-in `age`, a fake S3, then `restore-db.sh` into a scratch `*_restore_test` DB: counts matched (19 migrations, 1 user). Staging checks pending (need the merge and runbook step 10).

**Commands:**
- `pytest` -- expected: all pass
- `python manage.py check` -- expected: no issues
- `bash -n deploy/*.sh` -- expected: no errors

**Manual checks (if no CLI):**
- On staging after the runbook: `sudo systemctl start mytp-job@backupdb` succeeds; the object appears in the SBG bucket and is unreadable without the key; Sentry shows the check-in; a restore on a team Mac into `mytp_restore_test` prints counts matching staging.
