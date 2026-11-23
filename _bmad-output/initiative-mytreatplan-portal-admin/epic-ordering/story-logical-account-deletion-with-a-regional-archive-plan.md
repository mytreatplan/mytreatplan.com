---
title: 'Logical account deletion with a regional archive'
type: 'feature'
ticket: '12'
created: '2026-11-23'
status: 'draft'
baseline_revision: ''
route: 'full'
route_source: 'auto'
risk: 'high'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Erasing a doctor today strips the account at once (fiscal entities, shipping, maker logins, photo deleted) and the daily purge then hard-deletes it; accounts with patients are only kept by 9.1's stopgap (Patient.doctor PROTECT, purge skip). The user decided (2026-11-22) that an account and its patients are deleted logically only, kept in a per-region encrypted write-once archive for the legal retention period, and readable by the superuser behind a fresh code.

**Approach:** Erasure hides the account and its patients from every view; the region then writes one encrypted, write-once archive object per account to an archive bucket in the same region, expired by a lifecycle rule, and only after the object is verified are the live rows removed. Invoices, credit notes and the accounting copy stay as they are.

## Boundaries & Constraints

**Always:** The archive never leaves the region and is never readable without the region's key. No patient name, email or file name in logs, Sentry, task arguments or object keys (key = account uid). An archive failure keeps the live data and retries; nothing is deleted before the archive is verified. Maker-login secrets are never archived. The `client.erased` event and the admin's handling stay as they are.

**Never:** Changing issued invoices, credit notes, payments, refunds or the admin accounting copy. A separate database. Restoring an archive into live tables.

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

(filled after the Open Questions)

## Open Questions

1. **Split.** This needs the portal (erase, archive, bucket), mytp-core (a new remote resource and contract) and the admin site (request page, fresh code). (a) Recommended: this story writes the archive and removes live data (portal only); a new entry 9.13 "Archive report on the admin site" adds the superuser's read-only report behind a fresh code. (b) One story across three repos (well over the plan size limit).
2. **When the archive is written.** (a) Recommended: erasure only hides (user inactive, patients `erased`, nothing deleted); the daily purge, once `purge_after` passes, writes and verifies the archive, then deletes the live rows — one place, retries for free, every erased account (with or without patients) archived. (b) `erase_user` writes the archive synchronously before clearing — an S3 failure blocks the erasure in Django admin, and a retry can leave a second locked object.
3. **What is archived.** (a) Recommended: account and profile, consents, fiscal entities, shipping addresses, maker names (no logins), patients (names, reference, comments, status), work orders with stages, order and invoice summaries (numbers, dates, amounts, patient codes), deletion traces; no image bytes (portraits and photo are deleted as today, their traces kept). (b) Same plus portrait and photo files.
4. **Retention.** One expiry per archive object, set from a per-site setting `ARCHIVE_RETENTION_YEARS` (no default; the purge refuses to run unset, like `ACCOUNT_ERASURE_RETENTION_DAYS`). (a) Recommended: dev and dev-ae 25 years until the lawyer answers for the EU; fiscal data needs no shorter clock because invoices stay in the database and the accounting copy. (b) Two objects per account (clinical 25 years, fiscal 6 years).
5. **Write-once mechanism.** (a) Recommended: a second bucket per site, created with S3 Object Lock in compliance mode, each object written with retain-until = now + retention, and a lifecycle Expiration rule; the bucket owner (you) creates it, since object lock is only set at bucket creation. The first task checks on dev that OVH refuses an overwrite and a delete. (b) The current bucket under an `archive/` prefix without object lock (write-once by convention only).
6. **Storage tier.** (a) Recommended: OVH Standard Infrequent Access in the same region (readable in seconds, needed for 9.13's report). (b) OVH Cold Archive (cheapest, hours to restore, separate API, object lock support unverified).
7. **Encryption of the archive.** (a) Recommended: `age` to a per-site public recipient (setting `ARCHIVE_AGE_RECIPIENT`), its identity wrapped by the site's KMS like the backup restore identity, plus bucket SSE; only the region decrypts. (b) The field-encryption key (`mytp_core.crypto`) on the whole JSON — rotating that key would then need archived objects re-encrypted, which object lock forbids.
8. **AD-19 conflict.** (a) Recommended: AD-19 changes to "an erased account and its patients are archived per region (this story), then removed from live tables; marked patient deletion at 48 h applies only to a single patient the doctor deletes (epic-case-lifecycle)". (b) Keep AD-19 as is and record an exception for whole-account erasure.

## Verification

**Commands:**
- `pytest -q` (suite command in local-dev-notes) -- expected: 0 failures.

**Manual checks:**
- On dev: erase a test doctor with one patient, run the purge job after `purge_after`, see one archive object under the account uid with its retain-until date; an overwrite and a delete are refused; the doctor and patient are gone from the portal and from the live tables; invoices still show the patient code.
