---
title: 'Virus scanning of uploads'
type: 'feature'
ticket: '12'
created: '2026-11-07'
status: 'done'
baseline_revision: '78b82eb'
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

**Problem:** AD-9, E6: every upload lands in `quarantine/` (1.11) and nothing ever promotes it; real purposes may only serve scanned (`clean/`) files, and a file the scanner cannot fully check must never be served.

**Approach:** Install ClamAV with `clamd` and `freshclam` on the host through `provision.sh`, add a scan task enqueued when an upload is recorded that streams the object from the bucket to clamd (INSTREAM over the unix socket) and then promotes it to `clean/<uuid>` or rejects it; the hourly sweep re-enqueues scans that never finished; anything clamd cannot fully scan (size or archive limits, errors) is rejected.

## Boundaries & Constraints

**Always:**
- clamd limits ≥ the largest purpose `max_size` (1 GiB) for `StreamMaxLength`, `MaxFileSize`, `MaxScanSize`; archive/recursion limits at clamd's defaults; `AlertExceedsMax yes` and the encrypted and broken-executable alerts on (`AlertBrokenMedia` off, see decisions), so a limit hit is reported as FOUND (rejected), never as OK. A file above the limit is rejected.
- Scan task `scan_object_task(uuid)` (enqueued on commit in `views.complete`; idempotent, keyed by UUID): stream `quarantine/<uuid>` (S3 GetObject, inside `out_of_sentry()`) to clamd with a socket timeout; outside any DB transaction; then in `run_once('storage.scanned', …)`: `OK` → server-side copy to `clean/<uuid>`, delete the quarantine object, state `clean`; `FOUND` or a size limit exceeded → rejected (see decisions); any other clamd `ERROR` or an unknown answer, clamd unreachable or an S3 error → retried like the delete task (backoff, then one Sentry message by UUID and outcome code), the object staying in quarantine.
- `sweep_storage` also re-enqueues scans of rows still in quarantine after 1 hour.
- Logs and Sentry carry the object UUID and the outcome code only (signature names allowed in the log line, never file names, keys or URLs).
- `provision.sh`: packages `clamav-daemon clamav-freshclam` (host-wide), a clamd config drop-in (limits, socket group so each site user can connect), services enabled; `check` / runbook confirm the version and that signatures are current; system check `storage.E004` when storage is configured but `STORAGE_CLAMD_SOCKET` is missing.
- Runbook "Object storage" → "Scanning" (limits, socket, freshclam, staging checks).

- Decision (user, 2026-11-07): ClamAV from Debian 13's packages (1.4 LTS line, 1.4.3 today, kept current by apt and Debian security updates); the architecture's "1.4.6 or later" is amended to this; the option stays open to move to 1.4.6 when it reaches Debian (runbook note: check `apt-cache policy clamav` and upgrade then).
- Decision (user, 2026-11-07): a rejected file is deleted from the bucket and its row kept with a new state `rejected` (never downloadable), plus a `DeletionTrace` with actor `system`.
- Decision (user, 2026-11-07): scans run on a second worker per site for a `scans` queue (its own systemd unit from the worker template), so a long scan never holds the main worker.
- Decision (user, 2026-11-07): the sample purpose also downloads only `clean` objects from now on (`download_quarantine` removed from it).
- Decision (user, 2026-11-08): a clamd ERROR other than a size limit is retried, never decided.
- Decision (user, 2026-11-08): `AlertBrokenMedia` off: malformed images are scanned, not rejected for being malformed.

**Never:** no change to keys made by the server, file names in logs, the delete path or `DeletionTrace` semantics; no scanning on the admin site; no other antivirus; no user notification UI (real purposes come later).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Clean file | 1 GB random bytes | object at `clean/<uuid>`, quarantine copy gone, state `clean`, downloadable by its purpose | — |
| Virus | EICAR test file | rejected; no `clean/` object | — |
| Over the limit | file above clamd's limit | rejected (limit exceeded = not fully scanned) | — |
| Encrypted / broken archive | password-protected zip | rejected | — |
| clamd ERROR (not a size limit) | e.g. `Can't allocate memory ERROR`, or a garbled answer | stays quarantine (never downloadable); retried; Sentry (`scan_error`) after the last try | sweep re-enqueues; 6-hour count report |
| Malformed image | a truncated JPEG | scanned like any file (`AlertBrokenMedia` off): clean unless a signature matches | — |
| clamd down | socket refused | stays quarantine; retried; Sentry after the last try | sweep re-enqueues |
| Rerun | task runs twice (deploy restart) | one outcome, no error | idempotent |
| Download before scan | real purpose, quarantine object | refused | — |

</frozen-after-approval>

## Code Map

Portal (repo root):
- `storage/models.py` `StoredObject` (`state` QUARANTINE/CLEAN, `key` property → `clean_key` when CLEAN, `delete_requested_at`), `DeletionTrace` (actor SYSTEM available); `storage/s3.py` prefixes :31-33, `quarantine_key` :104, `clean_key` :108, `object_keys` :112, `make_client` :70, `out_of_sentry` :96, `head_size` :218, `delete_keys` :226, `describe_error` :258, `is_not_found` :272 — add `open_stream(key)` and `copy_object(src, dst)` in the same style; `storage/service.py` `download_url` :56, `delete` :81; `storage/purposes.py` `may_download` :80-85, `register(..., max_size, download_quarantine)`; `storage/tasks.py` `delete_object_task` :43 (pattern: `run_once`, re-enqueue with `.using(run_after=…)`, `MAX_ATTEMPTS`, Sentry `new_scope` tag `storage.object`); `storage/views.py` `complete` :168-224 (enqueue at the atomic block :214-218, not in the IntegrityError branch :219); `storage/sweep.py` `requeue_deletions` :73 (pattern), `sweep()` :84; `storage/sample.py` :33 (`download_quarantine=True`), template `storage/sample.html` :33, :54; `storage/checks.py`.
- `mytp_core.tasks` `enqueue_on_commit` :54, `run_once` :72 (wraps in a transaction: keep streaming outside it).
- Worker: one `db_worker` per site (`deploy/systemd/worker.service.template`), tasks one at a time, `TimeoutStopSec=60`, `requeue_interrupted_tasks` on start; `worker_heartbeat` alerts if queued > 5 min (a long scan on the same worker would trigger it — see Open Questions).
- Deploy: `deploy/provision.sh` `PACKAGES` :88-89, apt install :132-143, `install_file` :105, header :10-17, worker block :264-298; `deploy/README.md` :32, "Object storage" :1207.
- Tests: `storage/tests/conftest.py` (`storage_settings`, `test_purpose`, `stubber`), `test_delete.py` (task + Sentry patterns), `test_sweep.py`; a fake clamd (thread on a unix socket) or a patched scanner.
- No clamd library: implement INSTREAM (`zINSTREAM\0`, 4-byte big-endian length chunks, zero chunk; replies `stream: OK`, `stream: <sig> FOUND`, `... ERROR`).
- Host: 6.7 GB RAM, ~5.4 GB available with three sites; clamd ~1–1.5 GB (set `ConcurrentDatabaseReload no`). Debian 13's candidate is **1.4.3** (architecture says 1.4.6 or later — see Open Questions).

## Tasks & Acceptance

**Execution:**
- [x] `storage/clamd.py` (INSTREAM client), `s3.py` helpers, `tasks.py` scan task, `views.complete` enqueue, `sweep.py` requeue, rejection handling, `purposes`/`sample` per decisions, checks, settings `STORAGE_CLAMD_SOCKET`.
- [x] `deploy/provision.sh` (packages, clamd drop-in, socket access, services), `staging.env.example`, runbook "Scanning".
- [x] Tests: every matrix row with a fake clamd; INSTREAM framing; nothing sensitive logged.

**Acceptance Criteria:**
- Given a scan interrupted after the copy to `clean/` but before the state update, when it runs again, then it ends `clean` with no duplicate and no error.
- Given clamd answers anything other than `OK` or `FOUND`, when the task handles it, then the file is not promoted.

## Implementation Notes

Portal only; no mytp-core change. Files:

- `storage/clamd.py` (new): INSTREAM client over the unix socket (`zINSTREAM\0`, 4-byte big-endian chunks of at most 1 MiB, zero chunk). `Verdict` with outcome codes `clean`, `infected`, `limit_exceeded` (`Heuristics.Limits.Exceeded.*` FOUND or `INSTREAM size limit exceeded. ERROR`), `unscannable` (`Heuristics.Encrypted.*`, `Heuristics.Broken.*`), `scan_error` (any other ERROR or unknown answer: never OK). Only the exact `stream: OK` passes. No answer (refused, timeout, closed silently) raises `ClamdUnavailable`. When clamd closes mid-stream (StreamMaxLength), its reply is still read; it also checks for an early reply before each chunk. Signature names are kept only if they match a safe pattern, otherwise `unknown`. `version()` and `signature_date()` serve `clamd_check`.
- `storage/s3.py`: `open_stream(key)` (GetObject, each read inside `out_of_sentry()`), `exists(key)` (HeadObject), `copy_object(src, dst)` (CopyObject, with a 15-minute read timeout; `make_client(read_timeout=…)` is now keyword-configurable).
- `storage/models.py` + migration `0003_rejected_state`: state `rejected`.
- `storage/tasks.py`: `scan_object_task(uuid, attempt=1)` on backend/queue `scans`. It skips rows that are gone or being deleted (`skipped`) and rows that are not in quarantine (`already scanned`). It streams outside any transaction, then runs `run_once('storage.scanned')`, which locks the row (`select_for_update`; it gives up if the row was deleted or its deletion was asked meanwhile). OK promotes: HEAD `clean/` (copy only if missing), copy, delete quarantine, state `clean`. A FOUND or a size-limit ERROR rejects: deletes both keys, state `rejected`, `DeletionTrace` actor `system`. Any other ERROR or unknown answer is undecided (`scan_error`) and retried (decision 2026-11-08). A missing quarantine object with `clean/` present finishes the promotion without rescanning. clamd unavailable or an S3/other error is retried 1/2/4/8/16 min, as the delete task does, then one Sentry message `… <uuid> (<code>)`. Logs: UUID + code (+ signature name for FOUND).
- `storage/views.complete`: `enqueue_on_commit(scan_object_task, str(uuid))` inside the atomic block (not in the IntegrityError branch).
- `storage/sweep.py`: step 4 `requeue_scans` (quarantine, not being deleted, recorded more than 1 hour ago); `sweep_storage` prints `<n> scans enqueued again`.
- `storage/purposes.py`, `storage/sample.py`, template: only `clean` objects are downloadable for every purpose. The sample page shows the Download button only for clean objects, and its text mentions the scan.
- `storage/checks.py`: `storage.E004` (storage in use, `STORAGE_CLAMD_SOCKET` empty). `mytreatplan/settings.py`: `STORAGE_CLAMD_SOCKET` (default empty) and a second TASKS backend `scans` (`QUEUES: ['scans']`).
- `storage/management/commands/clamd_check.py` (new): prints clamd's VERSION line and fails when clamd is unreachable or the signatures are more than 3 days old. It is the "`check`" of the Always list, run as the site user (which also proves socket access).
- Deploy: `deploy/clamav/clamd.conf` is installed as `/etc/clamav/clamd-mytp.conf`. It sets `StreamMaxLength`, `MaxFileSize` and `MaxScanSize` to 1025M, `MaxScanTime` to 10 min, the `AlertExceedsMax`, `AlertEncrypted*` and `AlertBrokenExecutables` alerts on (`AlertBrokenMedia no`, decision 2026-11-08), archive/recursion limits at their defaults, `ConcurrentDatabaseReload no`, the socket `/run/clamav/clamd.ctl` as 0660 group clamav, and `TemporaryDirectory /var/lib/clamav/tmp` (changed in the review round, see below). `clamav-daemon.service.conf` is a systemd drop-in pointing clamd at that file. `clamav-daemon.socket.conf` is a drop-in that sets Debian's socket unit to 0660 clamav:clamav. `deploy/systemd/scan-worker.service.template`: `db_worker --backend scans --queue-name scans`, `requeue_interrupted_tasks --backend scans`, `SupplementaryGroups=clamav`, `PartOf=` and `WantedBy=mytp-<site>-worker.service`. `provision.sh` gets packages `clamav-daemon clamav-freshclam` and a new host-wide "Virus scanner (ClamAV)" step after Packages: drop-ins via `install_file`, a first `freshclam` only when `main.c[vl]d` or `daily.c[vl]d` is missing, units enabled/started only when not, clamd restarted only when its files changed, and a warning instead of an exit on failure. Its new "Scan worker ($SITE)" step is gated on the live release having `storage/clamd.py` and the main worker unit, and adds the site user to the group `clamav`. `jobs.conf` gets the job `prunescans`, `staging.env.example` gets `STORAGE_CLAMD_SOCKET=/run/clamav/clamd.ctl`, and the README gets a new "Object storage" → "Scanning" section (table, setup order, staging checks) plus rows for the overview, Background tasks and the sweep.

Deviations from the plan (for review):

1. **Separate task backend alias `scans`, not only a queue.** mytp-core's `requeue_interrupted_tasks` requeues every RUNNING task of a backend, so with one backend each worker's ExecStartPre would requeue the other worker's running task. A second `DatabaseBackend` alias (same table) lets each unit requeue only its own (`--backend scans`). The main worker unit is unchanged; it only picks the `default` backend. This needed the extra `prunescans` job, because `prunetasks` prunes only `default`.
2. **The scan worker follows the main worker through `PartOf=` / `WantedBy=`** instead of new release.sh/sudoers lines: release.sh's stop before `migrate` and restart after the switch apply to it unchanged. Its unit is installed only once a live release ships `storage/clamd.py` (never on admindev).
3. **clamd configuration is a full file plus a systemd drop-in**, because clamd.conf has no include directive. Debian's `/etc/clamav/clamd.conf` is left alone (same socket path, so `clamdscan`/freshclam's NotifyClamd still work). Also a socket-unit drop-in (0660), because Debian's `clamav-daemon.socket` otherwise creates the socket 0666.
4. **Limits are 1025M** (not exactly 1G), so a file of exactly the 1 GiB purpose limit is fully scanned. **`MaxScanTime` is raised to 10 min**: the default of 2 min would reject a 1 GB clean file as `Heuristics.Limits.Exceeded.MaxScanTime`. It is not an archive/recursion limit, which all stay at their defaults. **`TemporaryDirectory /var/lib/clamav/tmp`**: clamd spools INSTREAM data, and Debian 13's `/tmp` is a RAM tmpfs.
5. **`download_quarantine` removed from `purposes.register` entirely**, not just from the sample purpose: no purpose used it any more. **`register` now refuses `max_size` > 1 GiB** (`MAX_SCANNED_SIZE`), so a purpose can never exceed clamd's limits.
6. **`manage.py clamd_check`** (new command) is my reading of "`check` / runbook confirm the version and that signatures are current". A Django system check that opens a socket at every `check`/`migrate` seemed wrong.
7. **Unknown non-empty answers count as `scan_error` and are rejected.** An empty answer, timeout or refused connection counts as `clamd_unavailable` and is retried. Neither ever promotes.
8. **S3 error detail is left out of logs and Sentry** (only `s3_error`, unlike the delete task's `AccessDenied (HTTP 403)`), to follow "UUID and outcome code only" strictly.
9. **Test changes to existing tests:** `SITE_TEMPLATES` in `mytp_publicsite/tests/test_deploy.py` gains the new template (its folder test enumerates templates). The sample/service tests now expect clean-only downloads. The storage `stubber` fixture accepts `make_client(**kwargs)`.

Review round 1 (coordinator's patches, 2026-11-07):

1. **Missing objects:** an object found under neither `quarantine/` nor `clean/` is now rejected with outcome code `missing`. That is the rerun after an interrupted rejection, which used to retry and send a Sentry message every hour. The rejection is idempotent: S3 deletes of gone keys are no-ops, the state becomes `rejected` and the system trace is created with `get_or_create`. The tests `test_an_object_under_neither_key_is_rejected_as_missing` and `test_an_interrupted_rejection_ends_rejected_on_the_rerun` replace `test_neither_object_left_is_retried`.
2. **AppArmor:**
   - The spool moved to `TemporaryDirectory /var/lib/clamav/tmp`, created by provision as clamav, 0700.
   - New `deploy/clamav/apparmor-usr.sbin.clamd` is installed as `/etc/apparmor.d/local/usr.sbin.clamd`. It grants `r` on `/etc/clamav/clamd-mytp.conf` and `rw`/`rwk` on the spool.
   - provision runs `apparmor_parser -r /etc/apparmor.d/usr.sbin.clamd` only when that file changed, `apparmor.service` is active and the profile exists. A changed file also restarts clamd.
   - The runbook adds `aa-status | grep clamd` and a `journalctl -k | grep -i denied` check after the 1 GB scan. The 1 GB clean check now comes before EICAR.
3. **Sweep:**
   - It does not re-enqueue a scan when a READY or RUNNING `scan_object_task` for that UUID is in `DBTaskResult` on the `scans` backend, retries included. The sweep reads django-tasks-db's table; it is not task code.
   - One Sentry message per run gives the count of quarantine objects (not being deleted) waiting more than 6 hours. It carries no UUID or name. `sweep_storage` prints `<n> waiting for a scan over 6 hours`.
4. **Copy before the lock:**
   - The server-side copy to `clean/` (HEAD, then copy if missing) runs before `run_once`, outside any row lock.
   - Under the lock, `_finish_promotion` re-checks the row. If the row is gone or its deletion was asked, it deletes `clean/<uuid>` (the deletion wins) and returns `skipped`. Otherwise it deletes the quarantine object and sets `clean`.
   - Tested for a deletion during the scan, and during the copy (both deletion asked and row deleted).
5. **provision:**
   - Signatures are fetched when either `main.c[vl]d` or `daily.c[vl]d` is missing.
   - After a start or restart, clamd counts as started only if `systemctl is-active` confirms it. Otherwise provision warns: `clamav-daemon is not running after start (no signatures yet, or a configuration or AppArmor error)`.
   - The provision harness tests cover the first install, a second run with 0 changes, a config change, an AppArmor reload only when AppArmor runs, a missing `main` database, and clamd not coming up.
6. **clamd client:** a reply that arrives before the zero chunk can only reject. A FOUND or an ERROR stands; anything else, including `stream: OK`, is `scan_error`. The client polls with `select.poll` instead of `select.select`. Tested.
7. **Runbook:** a note on freshclam's `TestDatabases` (about 1 GB extra for a few seconds) and how to set `TestDatabases no`.
8. **Tests:**
   - The retry tests use a `reports` fixture (patched `capture_message`): no report before the last attempt, exactly one at it.
   - `test_a_rolled_back_completion_enqueues_no_scan` is replaced by `test_the_scan_is_enqueued_only_when_the_completion_commits`. It captures the on-commit callback without running it (nothing enqueued), then runs it (the scan row appears).
9. **Runbook "Scanning":** opens with a callout that `STORAGE_CLAMD_SOCKET` must be in `settings.env` before the first deploy, linking to Setup (dev) step 1, now marked "FIRST".

Review round 2 (user's decisions, 2026-11-08):

1. **Only a FOUND or a size limit rejects.** `clamd.Verdict.decided` is true for OK, FOUND, and the size-limit ERROR (`INSTREAM size limit exceeded`, `limit_exceeded`). AlertExceedsMax's `Heuristics.Limits.Exceeded.*` and the encrypted or broken-executable FOUND alerts still reject. Any other ERROR, an unknown or garbled answer, and an early reply that is not a FOUND or an ERROR (it was already never OK) is undecided. The task raises `_Undecided('scan_error')`: the object stays in quarantine and is retried like `clamd_unavailable`, with one Sentry message `… <uuid> (scan_error)` after the last attempt. The sweep's hourly re-enqueue and its 6-hour count report cover these objects too.
   - Tests: `test_any_answer_but_ok_never_promotes` became `test_other_errors_and_unknown_answers_are_retried_never_promoted` (6 answers; no S3 change, retry enqueued, no report). New `test_a_scan_error_at_the_last_attempt_is_reported_once` and `test_an_early_ok_is_retried_not_promoted`. Size-limit and encrypted/broken-executable rejections are still tested.
2. **`AlertBrokenMedia no`** in `deploy/clamav/clamd.conf`; `AlertEncrypted*`, `AlertBrokenExecutables` and `AlertExceedsMax` stay `yes`. The deploy test asserts both.
3. **Runbook "Scanning":** the outcome table separates size-limit ERRORs (rejected) from other ERRORs and unknown answers (undecided, retried). The Sentry example lists `(scan_error)`. The Limits row notes `AlertBrokenMedia` off.
4. **Plan:** the frozen block has the two 2026-11-08 decision lines, the first Always bullet and the scan-task bullet adjusted, and two new matrix rows. The Plan Change Log records the renegotiation.

Open for staging (cannot be verified locally): the clamd option names against 1.4.3 (an unknown option stops clamd; provision warns), that Debian's `clamav-daemon.socket` exists and takes the drop-in, that the AppArmor local include is enough (no `DENIED` lines), and real EICAR/zip/1 GB behaviour. These are the plan's manual checks.

## Verification results

The local run (2026-11-08, after review round 2) used SQLite, the CI env (`SECRET_KEY=ci-only-not-a-secret`, `DEBUG=False`, `SECURE_SSL_REDIRECT=False`, `ALLOWED_HOSTS`, test field keys) and the sandbox off. The worktree has no `settings.env`. The git-ignored `.mo` files and `static/css/site.css` were built (`compilemessages`, `npm ci && npm run build:css`).

- Storage, deploy and tasks tests (`storage`, `mytp_publicsite/tests/test_deploy.py`, `test_tasks.py`, `test_backups.py`): **383 passed**, run twice, with thread exceptions as errors.
- Full suite: **2136 passed, 6 failed, 5 skipped**. None of the failures come from this story:
  - 3 known PostgreSQL-only failures in `accounts/tests/test_crypto.py`.
  - 2 in `integration/tests/test_vendored_core.py`: the shared `code/.venv` has mytp-core **0.14.0** installed (story 4.9's work), while this branch vendors 0.13.0.
  - 1 in `accounts/tests/test_remembered_device.py::test_ticked_right_code_logs_in_and_sets_the_mark`, every time with this env's `SECRET_KEY`. The derived cookie name happens to contain `1`, the doctor's pk on SQLite. It is unrelated to storage.
  - The Chromium flake of the first run (`test_failure_policies` row 5) did not recur.
- `manage.py check`: no issues. With `STORAGE_BUCKET` set and no socket it reports `storage.E001` and `storage.E004`.
- `makemigrations --check --dry-run`: no changes.

## Plan Change Log

- 2026-11-08: the user renegotiated the frozen intent. A clamd ERROR other than a size limit (and any unknown answer) is now retried, never decided (it was rejected). `AlertBrokenMedia` is off, so malformed images are scanned, not rejected for being malformed. Frozen block edits: two decision lines, the alerts bullet, the scan-task bullet ("FOUND or a size limit exceeded → rejected; any other clamd ERROR … retried"), and two matrix rows (clamd ERROR, malformed image).

## Review Triage Log

Opus review (2026-11-07):
- patch (medium): an interrupted rejection or a lost quarantine object was retried forever → rejected as `missing`, idempotent.
- patch (medium): AppArmor profile for clamd → spool under `/var/lib/clamav/tmp`, local profile include, staging check.
- patch (medium): no liveness signal for the scan worker → sweep skips already-queued scans and sends one count-only Sentry message for quarantine rows older than 6 hours.
- patch (low/medium): row lock held through a 1 GiB copy → copy before the lock, re-check under it (deletion wins).
- patch (low): provision checks both `main` and `daily` signatures and confirms clamd is active; early non-verdict replies never OK, `poll`; runbook RAM note on freshclam; Sentry-once assertions; rollback test made able to fail.
- intent_gap → resolved by the user (2026-11-08): non-limit clamd ERRORs are retried, not rejected; `AlertBrokenMedia` off.

## Verification

**Commands:**
- Portal suite (local dev notes) — all pass.

**Manual checks (staging, dev):**
- Re-provision dev; `clamdscan --version` shows the installed version, freshclam has signatures; `check` clean.
- EICAR uploaded through the sample page → rejected; a 1 GB random file → `clean/`, downloadable; a file above the limit → rejected; `sweep_storage` reports re-enqueued scans when clamd was stopped.

## Staging

2026-11-08, run by Claude with the user's delegation; the user's browser check below. Portal PR #77 merged, CI and deploy green on dev and dev-ae. `STORAGE_CLAMD_SOCKET` added to dev's settings before the deploy; dev and dev-ae re-provisioned (ClamAV 1.4.3 from Debian, signatures current, AppArmor include loaded, scan workers and `prunescans` timers running). clamd resident memory about 1 GB.
- `clamd_check` as the dev site user: version and signatures current; `check` clean.
- Server-side 6 MiB random upload through sign, OVH and complete: download refused while in quarantine; scanned `clean`; downloaded with a matching checksum.
- EICAR: `rejected (infected, Eicar-Test-Signature)`, one system trace, download refused.
- No AppArmor denials in the kernel log after the scans.
- clamd stopped: the upload stayed in quarantine with "undecided (clamd_unavailable)" retries at 1 and 2 minutes; after clamd started, the next retry made it `clean`. `sweep_storage` reported nothing to re-enqueue (object under an hour old, scan already queued).
- Test objects deleted afterwards.
- 2026-11-08, the user: a 1 GB random file uploaded in Chrome through the sample page (161.2 s, 6.4 MiB/s); scanned `clean` in 2 min 35 s (no AppArmor denials, clamd about 1 GB resident); downloaded with a matching SHA-256. Sentry monitor `prunescans` disabled. Not run: a file above the limit (the purpose refuses anything over 1 GiB at signing, so only a browser upload near 1 GiB exercises clamd's limit).
