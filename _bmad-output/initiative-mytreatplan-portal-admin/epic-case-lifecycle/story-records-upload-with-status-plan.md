---
title: 'Records upload with status'
type: 'feature'
ticket: '5'
created: '2026-11-28'
status: 'in-progress'
baseline_revision: '7de40ea5f2f214db7dc2579b0cb944ca55c32ec0'
route: 'full'
route_source: 'auto'
risk: 'high'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-mytreatplan-portal-admin/architecture-portal-admin/architecture-portal-admin.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** When staff request records (stage `pending_registers_upload`), the doctor has no way to upload them or to say they are complete (L4, L3). The case cannot move on to Processing prescription from the portal.

**Approach:** Add a `case_records` storage purpose and a row linking one scanned file to a work order. The patient file's "case actions" slot shows an upload section while the case waits for records. It shows the file's status, and a "Records complete" button that applies the existing doctor move `records_complete`.

## Boundaries & Constraints

**Always:**
- Only the patient's own doctor uploads, sees the status and presses Records complete. Another doctor's patient or an erased patient gives a 404.
- Uploads happen only while the live work order is in `pending_registers_upload`. Staff choose which cases get there with `request_records`, so no category check is added.
- One file per work order, any type, up to 1 GiB (`MAX_SCANNED_SIZE`). The page recommends a single compressed file.
- The upload goes direct to the bucket with the existing widget (`single_file`). The page posts only the object id, as the portrait does. Attaching locks the work order. A file of another purpose, another uploader or already owned is refused.
- Status on the file, from then on, for every service that has a records row:
  - "Being checked": the scan is still running.
  - "Uploaded, waiting": the file is clean and the case has not reached `prescription_done`.
  - "Processed": the case has a `StageChange` to `prescription_done`.
  - "Refused": the file was rejected, with the reason from the decision below.
- Records complete calls `patients.service.apply_move(wo, 'records_complete', version, doctor_actor(user))`. It is refused unless a clean file is attached. A stale version gives the usual "changed meanwhile, reload" message.
- Writes are a POST with CSRF, then a redirect with a message. No patient name or file name in logs, task arguments or URLs (AD-23). The file name is stored only encrypted, as `StoredObject.original_name` already is.
- An uploaded file that never gets attached is removed by the sweep (`unclaimed_after`). An attached file stays in use.
- The page's two upload widgets (portrait and records) do not catch each other's `storage:uploaded` events.
- New strings exist in EN and ES.

**Never:**
- Staff download, the "uploaded to the maker" mark or record deletion (10.6).
- Changes to the `/storage/sign/` and `/storage/complete/` contracts, the key layout, the clamd configuration or `StoredObject` state meanings.
- A doctor download of the records.
- New stages or moves in mytp-core.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Upload | case in `pending_registers_upload`, 1 GB file | linked, "Being checked", then "Uploaded, waiting" once clean | — |
| Encrypted archive | password-protected zip | rejected by the scan, shown as "Refused" with the explanation | message on the page |
| Infected file | scan reports a virus | "Refused" | message on the page |
| Records complete | clean file, current version | case moves to `processing_prescription`, StageChange `records_complete`, status "Uploaded, waiting" | — |
| Complete too early | no file, file still being checked, or refused | nothing moves | message "upload your records first" |
| Wrong stage | case in `assigned` | no upload section; POST refused | 404 or message |
| Processed | staff move to `prescription_done` | status "Processed" | — |
| Other doctor / erased | POST attach or complete | 404, nothing changed | — |
| Foreign object id | another purpose or uploader | refused, nothing linked | message |
| Stale | version moved meanwhile | nothing moves | "reload" message |
| Second upload | a file is already attached, before Records complete | the new file replaces it; the old one is deleted, leaving its trace | — |
| Network cut | connection drops mid-upload, the page stays open | the upload resumes by itself when the browser is back online | — |

## Decisions

- Decision (user, 2026-11-28): resume stays within the open page. The widget retries by itself when the browser comes back online, and the page asks the doctor to keep it open. Resuming after a page reload goes to deferred-work.
- Decision (user, 2026-11-28): the scan stores a short reject reason on `StoredObject` (`unscannable` or `infected`), and the page explains it. `unscannable` says the archive may be password-protected or too complex to scan, so upload it without a password.
- Decision (user, 2026-11-28): a second upload before Records complete replaces the first. The old file is deleted through `storage.service.delete`, which keeps the trace.
- Decision (user, 2026-11-28): the plan is kept whole (about 1,800 tokens).
- Delegation (user, 2026-11-28): as for the earlier stories of this epic — push the branch and open the PR, merge after the user says "merge", run the staging checks on dev over SSH, push the completion commit to master.

</frozen-after-approval>

## Code Map

- `storage/purposes.py` -- `register(name, can_upload, can_download, *, max_size, content_types, validate, on_clean, unclaimed_after, in_use)`; `max_size` at most `MAX_SCANNED_SIZE` (1 GiB). Register it from an `AppConfig.ready`.
- `storage/models.py` -- `StoredObject` (states `quarantine`/`clean`/`rejected`, `purpose`, `owner`, `uploaded_by`, `delete_requested_at`); `PendingUpload` (upload in progress, `upload_id`); `DeletionTrace`. It has no reject reason field.
- `storage/tasks.py` and `storage/clamd.py` -- `scan_object_task`.
  - Encrypted archives come back as FOUND `Heuristics.Encrypted.*`, and `Verdict.code` maps that to `'unscannable'`. The row is then set `rejected` and the outcome is only logged.
  - `on_clean(obj)` runs after commit.
- `storage/templates/storage/_upload_widget.html` and `storage/static/storage/upload.js` -- Uppy multipart, 16 MiB parts. Resume works only within the page. `upload.js` starts every `.storage-upload`, and each fires `storage:uploaded {id, size, purpose}`.
- `storage/service.py` -- `delete(obj, actor, patient_code)`, used for a replaced file.
- `patients/portrait.py` -- the pattern to follow:
  - purpose hooks `:64-100`
  - `attach` `:126` (locks, purpose, uploader and owner checks)
  - `settle` `:180`
- `static/js/patient-portrait.js` -- matches `[data-portrait-widget]`. Its event listener must check `detail.purpose`.
- `patients/slots.py` -- `register(CASE_ACTIONS, name, template, context)`. `patient.html:32` includes each entry with `slot=`. Nothing is registered in production yet.
- `patients/service.py:362` -- `apply_move` raises `StaleVersion` or `MoveRefused`, and doctor moves check ownership. `patients/moves.py` -- receivers. `patients/tests/test_moves.py:67-72` -- the call pattern.
- `patients/stages.py:11` -- the label for `pending_registers_upload` is "Records requested". Keep it.
- `../mytp-core/mytp_core/moves.py` -- `records_complete` (doctor): `pending_registers_upload` → `processing_prescription`. Read only.
- Tests: `conftest.py` `fake_bucket`, `stubber`, `fake_clamd`, `s3_body`; `patients/tests/test_portrait.py` `uploaded(...)`, `scan(...)`.

## Tasks & Acceptance

**Execution:**
- [ ] `records/` (new app: `apps.py`, `models.py`, `migrations/`, `service.py`, `views.py`, `urls.py`, templates, `static/js/case-records.js`) -- three parts:
  - `CaseRecords`: a one-to-one link to `WorkOrder` plus the `StoredObject`, with `attached_at` and actor fields.
  - The `case_records` purpose, with its `attach` / `complete` / `status` service.
  - POST views for attach and complete, and the case-actions slot entry. Add the app to `INSTALLED_APPS`, `pytest.ini` and the urls.
- [ ] `storage/models.py` + migration, `storage/tasks.py` -- a nullable `reject_reason` (`unscannable` / `infected`), set where the row becomes `rejected`. Nothing else in the scan changes.
- [ ] `storage/static/storage/upload.js` -- on the browser's `online` event, retry the failed uploads of that widget (`uppy.retryAll`). The portrait gains this too, which does no harm.
- [ ] `static/js/patient-portrait.js` -- ignore events of other purposes.
- [ ] `deferred-work.md` -- resume after a page reload (source this plan).
- [ ] `locale/es/LC_MESSAGES/django.po` -- new strings, then `compilemessages`.
- [ ] `records/tests/` -- one test per matrix row, the sweep keeping an attached file, and no file name in logs.

**Acceptance Criteria:**
- Given a dev case moved to `pending_registers_upload`, when the doctor uploads a large archive and presses Records complete, then the file shows "Uploaded, waiting" and the case is in Processing prescription with a `records_complete` stage change.
- Given a password-protected archive, when its scan ends, then the file page shows it refused with the explanation and Records complete is refused.

## Verification

**Commands:**
- Portal suite: `../.venv/bin/python -m pytest -q -p no:cacheprovider --tb=line` -- expected: 0 failures besides the known WeasyPrint ones.
- `../.venv/bin/python manage.py makemigrations --check --dry-run` -- expected: no changes.

## Implementation Notes

## Plan Change Log

## Review Triage Log
