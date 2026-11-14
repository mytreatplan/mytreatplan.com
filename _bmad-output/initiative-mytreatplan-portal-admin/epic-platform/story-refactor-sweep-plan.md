---
title: 'Refactor sweep (epic-platform)'
type: 'refactor'
ticket: '15'
created: '2026-11-14'
status: 'built'
baseline_revision: '7839a25736fd1e0b5b19f7b3fddad663201297f0'
route: 'full'
route_source: 'auto'
risk: 'low'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** epic-platform's entries left stale text, a dead shim, stray files and test helpers imported across apps, and its deferred review findings are neither closed nor recorded with their state today.

**Approach:** Cleanup only, in the portal and the admin repo: remove what is stale or dead, move one shared test fake to the root `conftest.py`, and bring `deferred-work.md` up to date (closed, still open, or recorded and left). Everything that changes behaviour, CI infrastructure, the servers or mytp-core is recorded and left.

## Boundaries & Constraints

**Always:**
- No behaviour, wire, migration or template change; the suites pass with the same counts as the baselines, minus tests named in the Implementation Notes.
- `deferred-work.md` entries are append-only: "closed by …" or "recorded and left".

**Never:**
- No mytp-core change (no release, no re-vendoring).
- No change to `deploy/*.sh`, systemd units, nginx, sudoers or `jobs.conf` (no re-provision).
- No CI behaviour change (image, steps).
- No fix outside epic-platform's subject.

## Decisions

- Decision (user, 2026-11-14): the completed one-off runbooks in `deploy/README.md` are deleted (git keeps them); the admindev restore test and 1.13's runbook stay.
- Decision (user, 2026-11-14): `s3AdminPolicy.json` and `s3-bckp-server_Policy.json` in the repo root are deleted.
- Decision (user, 2026-11-14): the 3 PostgreSQL-only tests in `accounts/tests/test_crypto.py` are skipped on SQLite; CI still runs them.
- Decision (user, 2026-11-14): a note pointing to the 2026-11-07 decision (Debian's ClamAV 1.4.3 until Debian ships 1.4.6) is added next to "1.4.6 or later" in the architecture and the epic.
- Decision (user, 2026-11-14): code copied between the two sites that only mytp-core could share is one "recorded and left" entry, no ticket.
- Decision (user, 2026-11-14): the sweep runs now although entries 11 and 13 are still `built`.
- Decision (user, 2026-11-14): merge and staging for this entry are delegated.

</frozen-after-approval>

## Code Map

Portal:
- `.github/workflows/ci.yml:101-102`, `deploy/README.md:6-7, 740-741` -- stale "the dev-ae leg fails until Add dev-ae is done".
- `deploy/README.md:794-811, 944-961, 1707-1733, 2153-2162, 2426-2580` -- completed one-offs (question 1). Keep `:1246-1279` (admindev restore test, owed) and `:1344-1594` (1.13's runbook). Pinned by tests: §12 and the KMS sections only.
- `s3AdminPolicy.json`, `s3-bckp-server_Policy.json` -- stray root files (question 2).
- `mytp_publicsite/tests/test_monitoring.py:487-503` -- `_FakeSentry`, imported by `eventlog/tests/test_retention.py:19`, `mytp_publicsite/tests/test_backups.py:23`, `test_kms.py:22`: move to the root `conftest.py` beside `_runjob_with` (`:235`).
- `mytp_publicsite/kms.py` -- re-export shim only tests use (`accounts/tests/test_kms_provider.py:24,212`, `mytp_publicsite/tests/test_kms.py:276-279`): delete, tests import `mytp_core.kms`, the re-export test goes.
- `mytp_publicsite/tests/test_monitoring.py:217,259` -- the secret marker `'private'` collides with a venv under `/private/tmp`: a unique marker.
- `accounts/tests/test_crypto.py` -- the 3 raw-SQL tests (question 3).

Admin:
- `.github/workflows/ci.yml:16` -- the comment says staging runs 17.x; admindev runs 18. Comment only.

Planning:
- `deferred-work.md` -- closed: the allowlist README deferral (admin `README.md:943-946`), `MIN_PG_MAJOR` (`deploy/restore-db.sh:39`). Still open: the row-5 flaky browser test, the CI fail-fast on a stale host release command, `StoredObject.owner` PROTECT against the purge. Recorded and left: CI in the Playwright image, the PGDG client workaround, the admin copy of `run-job.sh` and `~/deploy-admindev`, ClamAV 1.4.3, the `staging/` lifecycle rule (after 2026-12-09), the disabled Sentry monitors, the core copies (question 5).
- `architecture-portal-admin/architecture-portal-admin.md:275`, `epic-platform/epic-platform.md:30` -- ClamAV text (question 4).

## Tasks & Acceptance

**Execution:**
- [x] Portal `.github/workflows/ci.yml`, `deploy/README.md` -- remove the three stale "dev-ae leg fails until…" sentences.
- [x] Portal `deploy/README.md` -- delete the completed one-offs listed in the Code Map; keep the admindev restore test and 1.13's runbook; fix any contents line, cross-reference or section number that pointed at a removed part; the sections tests pin (§12, the KMS sections) keep the text the tests read.
- [x] Portal `s3AdminPolicy.json`, `s3-bckp-server_Policy.json` -- delete; fix the one mention in the spike findings if it reads as a live path.
- [x] Portal root `conftest.py`, `mytp_publicsite/tests/test_monitoring.py`, `test_backups.py`, `test_kms.py`, `eventlog/tests/test_retention.py` -- the runjob Sentry fake lives in the root `conftest.py`; no test module imports it from another test module.
- [x] Portal `mytp_publicsite/kms.py`, `accounts/tests/test_kms_provider.py`, `mytp_publicsite/tests/test_kms.py` -- delete the shim; the tests import `mytp_core.kms`; remove `test_kms_reexport_is_the_package_module` and name it in the notes.
- [x] Portal `mytp_publicsite/tests/test_monitoring.py:217,259` -- a secret marker that cannot appear in a path.
- [x] Portal `accounts/tests/test_crypto.py` -- skip `test_pending_registration_row`, `test_rotation_with_reencrypt_fields` and `test_data_migration_encrypts_existing_rows_and_reverses` when the database is not PostgreSQL, with the reason in the marker.
- [x] Admin `.github/workflows/ci.yml:16` -- the comment says which PostgreSQL the image is and that admindev runs 18; the image is not changed.
- [x] Portal `_bmad-output/…/deferred-work.md` -- append the closed, still-open and recorded-and-left entries of the Code Map (one entry for the core copies: the sample task, the SQLite test patch and restore settings block, the site-name pattern, the S3 address in `backupdb`'s Sentry breadcrumbs).
- [x] Portal `_bmad-output/…/architecture-portal-admin/architecture-portal-admin.md:275`, `epic-platform/epic-platform.md:30` -- the ClamAV note.
- [x] Portal `README.md`, `local` docs -- wherever "3 known PostgreSQL-only failures" is described as expected locally, say they are skipped on SQLite.

**Acceptance Criteria:**
- Given the portal suite on SQLite, when it runs, then nothing fails: 3 tests fewer fail and 3 more are skipped than the baseline, and one test fewer exists (the re-export test).
- Given the admin suite, when it runs, then 1582 passed, 12 skipped.
- Given both diffs, when `git diff --stat` is read, then no migration, template, `vendor/`, `deploy/*.sh`, systemd, nginx, `jobs.conf` or CI step changed (comments only in CI files).
- Given the test tree, when grepped, then no test module imports `_FakeSentry` (or its new name) from another test module and nothing imports `mytp_publicsite.kms`.
- Given `deferred-work.md`, when read, then every epic-platform finding is closed or recorded with its state on 2026-11-14.

## Implementation Notes

2026-11-14, nothing committed.

- Stale dev-ae text: the three "dev-ae leg fails until Add dev-ae…" sentences removed (`deploy/README.md` intro and "How a deploy works", portal `ci.yml` comment). The "Add dev-ae" runbook itself stays (not in the Code Map's list).
- `deploy/README.md` one-offs deleted, each read first and confirmed completed: "Scheduled jobs" one-off after entry 5 (monitor rename, old `mytp-staging-*` monitors deleted 2026-11-01); "Background tasks" one-off after entry 9 (worker re-provision); "Object storage" → "Settings (rename of dev's spike keys)" (entry 11); "Sites on the staging host" one-off after entry 10 (generic release check); the whole "Move dev to the per-site layout" section (entry 1, done 2026-10-30). Kept: entry 14's one-off and admindev restore test, 1.13's per-site runbook, the KMS sections and step 12. Its one general sentence (`STORAGE_ABORT_INCOMPLETE_DAYS` defaults to 7; set it only to change the period, then `--apply` again) moved into "CORS, lifecycle and encryption". No contents list, anchor or other file pointed at a removed part; no test reads the removed text.
- Root policy files deleted. `s3AdminPolicy.json`: Sid `BackupBucketAdmin`, `s3:*` on `mytp-backups-staging` and its objects. `s3-bckp-server_Policy.json`: Sid `BackupWriteReadNoDelete`, put/get/list/multipart actions (no delete) on the same bucket, the same policy as `deploy/README.md` step 10.4. No credential in either. The spike findings now point to step 10.4 instead of the file.
- `_FakeSentry` moved to the root `conftest.py` beside `_runjob_with` (name kept, docstring added); `test_monitoring.py`, `test_backups.py`, `test_kms.py` and `eventlog/tests/test_retention.py` import it from `conftest`, as they already import `_runjob_with`.
- `mytp_publicsite/kms.py` deleted. `accounts/tests/test_kms_provider.py` imports `kms` from `mytp_core` (its `import mytp_core.kms` and the now self-evident `kms.KMSClient is mytp_core.kms.KMSClient` assert in `test_migrations_import_the_package_classes` removed). Test removed: `mytp_publicsite/tests/test_kms.py::test_kms_reexport_is_the_package_module`.
- `test_request_error_reaches_sentry_scrubbed`: the body marker `private` became `body-marker-q7x2` (it matched `/private/tmp` in paths).
- `accounts/tests/test_crypto.py`: a module-level `POSTGRESQL_ONLY` skipif marker (`connection.vendor != 'postgresql'`, reason "raw SQL for PostgreSQL (now(), UUID parameters); CI runs it, skipped on SQLite") on `test_pending_registration_row`, `test_rotation_with_reencrypt_fields`, `test_data_migration_encrypts_existing_rows_and_reverses`.
- Admin `ci.yml:16`: comment only: "PostgreSQL 17, the whole suite runs against it. admindev on staging runs 18." Image unchanged.
- `deferred-work.md`: 12 entries appended (2 closed, 3 still open, 7 recorded and left, the core copies as one). Each reference verified: admin README:943-946 (commit b453016), `restore-db.sh:39` (commit e0b6f81), the sample task in both repos, `supports_json_field` in both `conftest.py`, the restore settings block in both `settings.py`, the site-name pattern in 7 places, `mytp_core/sentry.py:193-204`.
- ClamAV note added to the architecture (line 275) and the epic (line 30).
- Local docs: no tracked doc (README.md, deploy/README.md, CI comments, either repo) describes the 3 known PostgreSQL-only failures, so nothing to change. Old plan files left as they are.
- Counts. Portal (SQLite, sandbox off, browser tests included): baseline 2894 passed, 8 skipped, 3 failed; after 2893 passed, 11 skipped, 0 failed. Admin: 1582 passed, 12 skipped (unchanged).
- Local run note: the worktree has no `settings.env`, compiled `.mo` files or `static/css/site.css`; the run used CI's environment values (with a non-CI `SECRET_KEY`: CI's makes the known `test_remembered_device` pk-1 failure show on SQLite) after `manage.py compilemessages` and the Tailwind build (git-ignored outputs).
- Nothing left from the task list.

## Plan Change Log

## Review Triage Log

Quick lens (Opus), 2026-11-14: 7 findings, 6 patched, 1 rejected.

| # | Severity | Finding | Verdict |
|---|----------|---------|---------|
| 1 | high | `deploy/README.md`: the "**Remove a site**" label line went with the entry-10 one-off; the procedure started mid-sentence and "see Remove a site above" pointed at nothing | patched: line restored |
| 2 | high | `deploy/README.md`: the heading `## Interface with the admin site` went with "Move dev to the per-site layout"; settings, the index table and the app name refer to it | patched: heading restored |
| 3 | low | The entry-10 one-off held the only note that a release built before the `release-ready` marker cannot be redeployed by re-running its old job | patched: one paragraph at the top of "Rollback" |
| 4 | low | The bucket-owner policy (`s3:*`) is no longer written as JSON anywhere | rejected: step 10.2 says ownership, not that policy, is what counts |
| 5 | low | `deferred-work.md`: the allowlist refusal applies only to a site that already has an allowlist | patched |
| 6 | low | `deferred-work.md`: the PGDG client 17 install is mytp-core's CI, not the portal's | patched |
| 7 | low | `deferred-work.md`: core has a second copy of the site-name pattern (`interface.SITE_RE`) | patched |

## Design Notes

- Left to their epics: provider recognised by class (`signup/checks.py:12`), the remembered-device pk test, `'cdn'` in `test_home.py:39`.
- Working notes for the implementer: the branch is `story/1.15-refactor-sweep`, already created in the portal worktree `../mytreatplan-1.15` (NOT the main portal checkout, where another story is being built) and in `../mytp-admin`; do not create another branch and do not commit. Run the portal suite from the worktree with its own SQLite file (`DATABASE_URL=sqlite:///$TMPDIR/sweep.sqlite3`) and `../.venv`. Do not touch `../mytp-core`.
- Baselines (5.15): portal 2894 passed + 3 known; admin 1582 passed, 12 skipped.

## Verification

**Commands:**
- the portal's full suite command (SQLite, `../.venv`) -- expected: baseline count minus the tests named in the notes
- the admin suite (admin venv) -- expected: 1582 passed, 12 skipped
- `git diff --stat` in both repos -- expected: no migration, template, `vendor/`, `deploy/*.sh`, systemd, nginx or core file
