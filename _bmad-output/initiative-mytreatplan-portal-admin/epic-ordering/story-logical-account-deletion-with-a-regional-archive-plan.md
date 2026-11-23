---
title: 'Logical account deletion with a regional archive'
type: 'feature'
ticket: '12'
created: '2026-11-23'
status: 'built'
baseline_revision: '30b005596d209c5faa9a39bdbc0243836684a8ad'
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

**Problem:** Erasing a doctor today strips the account at once (fiscal entities, shipping, maker logins, photo deleted) and the daily purge then hard-deletes it; accounts with patients are only kept by 9.1's stopgap (Patient.doctor PROTECT, purge skip). The user decided (2026-11-22) that an account and its patients are deleted logically only, kept in a per-region encrypted write-once archive for the legal retention period.

**Approach:** Erasure hides the account and its patients; the daily purge, once `purge_after` passes, writes one encrypted write-once archive object per account to an archive bucket in the same region, verifies it, and only then deletes the live rows. The superuser's report is 9.13.

## Boundaries & Constraints

**Always:** The archive never leaves the region. No patient name, email or file name in logs, Sentry, task arguments or object keys (key = `accounts/<user uid>.json.age`). An archive failure keeps every live row and is retried by the next run. Invoices, credit notes, payments, refunds, the admin accounting copy, `client.erased` and its admin handling are unchanged.

**Never:** A separate database; restoring an archive into live tables; archiving maker logins or maker names (the maker travels inside work orders) or image bytes; the report (9.13).

## Decisions

- Decision (user, 2026-11-23): split; this story is the portal side only, and new entry 9.13 "Archive report on the admin site" adds the superuser's read-only report behind a fresh code.
- Decision (user, 2026-11-23): erasure only hides (user inactive, patients `erased`, maker logins and the photo still deleted at once as today, since neither is archived); the purge writes and verifies the archive, then deletes the live rows. Every erased account is archived, with or without patients; the 9.1 purge skip goes.
- Decision (user, 2026-11-23): the archive holds account and profile, consents, fiscal entities, shipping addresses, patients (names, reference, comments, status, code), work orders with their fields and stages, order and invoice summaries (numbers, dates, amounts, patient codes) and deletion traces. No maker names, no maker logins, no images.
- Decision (user, 2026-11-23): one expiry per object from `ARCHIVE_RETENTION_YEARS` (no default; the purge refuses to run unset); 25 on dev and dev-ae until the lawyer answers for the EU.
- Decision (user, 2026-11-23): a second bucket per site in the site's region, created by the user with S3 Object Lock; objects written in COMPLIANCE mode with retain-until = now + retention; a lifecycle Expiration rule removes them after. Storage class OVH Standard Infrequent Access.
- Decision (user, 2026-11-23): the archive is encrypted with `age` to `ARCHIVE_AGE_RECIPIENT` (identity made with `generate_restore_identity` into its own KMS-wrapped file, kept for 9.13), plus bucket SSE.
- Decision (user, 2026-11-23): AD-19 becomes "an erased account and its patients are archived per region, then removed from live tables; the 48 h deletion applies only to a single patient the doctor deletes (epic-case-lifecycle)".
- Decision (user, 2026-11-23): plan kept whole (slightly over 1600 tokens). Approved and continued; merge, staging checks (SSH), pushing the story branch, the mytp-core release tag if needed, and the completion commit to master are DELEGATED for this story.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Erase | doctor with patients | user inactive, patients `erased`, gone from every portal view; fiscal, shipping, profile still in DB | — |
| Purge due | `purge_after` passed | archive written, verified, `AccountArchive` row; live account, patients, work orders, fiscal, shipping deleted; orders keep `user=NULL`, invoices unchanged | — |
| Bucket down | put or head fails | nothing deleted, count of failures printed, Sentry error without personal data | retried next run |
| Settings unset | any ARCHIVE_* missing | purge refuses, deletes nothing | non-zero exit |
| Already archived | row exists from a run that failed after the put | no second object; verify the existing one, then delete live rows | — |
| Expired | `retain_until` passed | `AccountArchive` row deleted by the purge | — |

</frozen-after-approval>

## Code Map

- `accounts/erasure.py:80-120` `erase_user` -- today deletes fiscal/shipping/maker rows and the photo at once; `:156-190` `purge_erased` with the 9.1 skip `patients__isnull=True`; `_record_purge` (5-year purge record).
- `accounts/fiscal.py:475`, `accounts/shipping.py:299`, `accounts/makers.py:167`, `accounts/photo.py:238` -- the per-area `erase` helpers.
- `patients/models.py` -- `Patient.status` has `erased` (nothing sets it); `doctor` PROTECT; `WorkOrder.patient` PROTECT.
- `payments/models.py:59` `Order.user` SET_NULL; `InvoiceLine` append-only with `patient_code`; `invoices/patients.py` shows the code only for a non-active patient -- keep.
- `storage/s3.py` (`make_client`, fixed `quarantine/`/`clean/` prefixes), `storage/management/commands/storage_bucket.py` (`--apply` sets CORS, abort-multipart lifecycle, SSE) -- no object lock or expiration support anywhere.
- `../mytp-core/mytp_core/management/commands/backupdb.py` -- precedent: `age` encryption to a public recipient, identity wrapped by the site KMS (`generate_restore_identity`).
- `deploy/jobs.conf:29` `purgeerased` 04:15 UTC; `accounts/tests/test_erasure.py`, `accounts/tests/test_client_sync.py`, `patients/tests/test_service.py:158-182` -- tests that pin today's erase/purge and will change.
- `architecture-portal-admin/architecture-portal-admin.md` AD-18, AD-19 -- update with the decision.

## Tasks & Acceptance

**Execution:**
- [ ] `archive/` (new app: models, service, migration) -- `AccountArchive` (user uid, object key, sha256, size, written_at, retain_until; no personal data); `build(user)` returns the versioned JSON (`"schema": 1`) per the Decisions; `write(user)` encrypts with the `age` binary to `ARCHIVE_AGE_RECIPIENT` (as `backupdb` does), puts with `ObjectLockMode=COMPLIANCE`, `ObjectLockRetainUntilDate`, `StorageClass` from `ARCHIVE_STORAGE_CLASS` (default `STANDARD_IA`) and a checksum, then `head_object` checks size, checksum and retain-until -- the archive itself.
- [ ] `storage/s3.py` -- `make_archive_client()` on `STORAGE_ENDPOINT` with `ARCHIVE_BUCKET`, `ARCHIVE_ACCESS_KEY`, `ARCHIVE_SECRET_KEY`; `missing_archive_setting()` -- separate credentials from the upload bucket.
- [ ] `storage/management/commands/storage_bucket.py` -- `--archive --apply|--inspect`: lifecycle Expiration of ceil(years × 365.25) + 1 days and SSE on the archive bucket; inspect also shows object-lock configuration -- bucket owner runs it.
- [ ] `accounts/erasure.py` -- `erase_user`: set patients `erased`, keep fiscal, shipping and profile; keep maker and photo erase. `purge_erased`: drop the patient skip; per account `archive.write` (or verify an existing row), then delete work orders, patients, fiscal, shipping, then today's steps; refuse when `ARCHIVE_*` or `ARCHIVE_RETENTION_YEARS` is unset; delete expired `AccountArchive` rows.
- [ ] `mytreatplan/settings.py`, `deploy/README.md` -- the settings and a runbook: create the object-lock bucket and keys (user), `generate_restore_identity` into `archive-identity.json`, set the recipient, `storage_bucket --archive --apply`.
- [ ] `architecture-portal-admin/architecture-portal-admin.md` -- AD-19 and AD-2 rows per the Decisions.
- [ ] tests (`archive/tests/`, `accounts/tests/test_erasure.py`, `test_client_sync.py`, `patients/tests/test_service.py`) -- every matrix row with a stubbed S3 client and `age`; update the tests that pin the old erase/purge.
- [ ] `pytest.ini` -- add `archive` to `testpaths` (a new app is not collected otherwise).

**Acceptance Criteria:**
- Given an erased doctor, when the doctor's patients, orders or invoices are viewed by anyone on the portal, then no patient name appears; invoices show the patient code only.
- Given the archive written on dev, when the object is overwritten or deleted with the archive keys, then OVH refuses both.
- Given the archive decrypted with the archive identity, then it holds every item of the Decisions and no maker login or name.

## Review Triage Log

Pass 1 (quick lens, opus, with a data-loss/privacy/S3 focus): high 1, medium 6, low 4, false 0, maybe-false 0; one finding (AC wording) rejected because its fix edits this plan.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | Lifecycle Expiration on a versioned (Object Lock) bucket only adds a delete marker; the locked version stays forever | high | patch | Object Lock implies versioning. Added `NoncurrentVersionExpiration` 1 day and an `ExpiredObjectDeleteMarker` rule; README and AD-19 updated. |
| 2 | Runbook gives the archive user no retention rights, so every locked put/head would fail | medium | patch | Locked PUT needs `s3:PutObjectRetention`, head lock fields need `s3:GetObjectRetention`. Runbook fixed. |
| 3 | AC "an overwrite is refused" cannot pass: a PUT adds a new version | medium | reject (plan edit) | Object Lock protects versions, not keys. Runbook and staging check now say "the original version cannot be deleted or overwritten"; the user is told. |
| 4 | Put ok + head/row failure → next run puts a second locked version | medium | patch | No row → HEAD first, adopt an existing locked object; test added. |
| 5 | No `Content-MD5`; verification needs `ChecksumSHA256` OVH may not return | medium | patch | Put sends `ContentMD5`; verify accepts SHA-256 or the ETag MD5; both head shapes tested. To confirm on dev. |
| 6 | Existing-row verify passes `retain=None` | low | patch | Now `retain=existing.retain_until`. |
| 7 | Sentry/command output lose the `ArchiveError` text | medium | patch | `report_error(detail=…)` for `ArchiveError` only; test checks no personal data. |
| 8 | Runbook puts bucket-owner keys on the command line, no `sudo -u` | medium | patch | Now the `read -rs` / `sudo -u mytp-<site> env` / `unset` pattern. |
| 9 | `erasure.py` module docstring contradicts the code | low | patch | Corrected. |
| 10 | `patients/models.py` "never hard-deleted / never reused" now false | low | patch | Corrected (reuse possible but negligible). |
| 11 | `clear_profile`, `fiscal.erase`, `shipping.erase` dead | low | patch | Deleted. |
| 12 | Stale test docstring in `test_erasure.py` | low | patch | Corrected. |

## Verification

**Commands:**
- From the repo root, sandbox disabled: `export FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU="; export DATABASE_URL="sqlite:///$TMPDIR/s912.sqlite3"; ../.venv/bin/python -m pytest -q -p no:cacheprovider --tb=line` -- expected: 0 failures.

**Manual checks (staging, after the user's bucket and settings steps):**
- On dev: erase a test doctor with one patient, run the purge job after `purge_after`, see one archive object under the account uid with its retain-until date; an overwrite and a delete are refused; the doctor and patient are gone from the portal and from the live tables; invoices still show the patient code.
