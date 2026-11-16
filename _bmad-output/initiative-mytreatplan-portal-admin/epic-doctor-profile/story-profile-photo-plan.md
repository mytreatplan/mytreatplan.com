---
title: 'Profile photo'
type: 'feature'
ticket: '4'
created: '2026-11-16'
status: 'ready-for-dev'
baseline_revision: 'b876169'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A doctor has no way to add a profile photo (D3). The avatar shows initials everywhere, and the personal-data page has an empty photo block left for this story.

**Approach:** Register the first real storage purpose, `profile_photo`. The doctor uploads one image through the existing storage service (1.11) and virus scan (1.12). Once the scan says clean, the image becomes their avatar. Each page render issues short-lived presigned links. The photo stays regional: it is not in `client.updated` and never reaches the admin site. Deleting it, replacing it or erasing the account removes the object through `storage.service.delete`, which leaves the deletion trace (no patient code).

**Decisions (user, 2026-11-16):**
- Only JPEG, PNG and WebP, checked in the widget and again at sign. No SVG, GIF or HEIC.
- At most 5 MiB per photo.
- After a clean scan the server decodes the file with Pillow, which is added for this. Anything that is not a real JPEG, PNG or WebP, or is larger than 6000×6000 px, is rejected as the scan rejects a file.
- No crop or resize: the photo is shown inside the round frame with CSS `object-cover`.
- One photo per doctor. A new upload waits as pending. Only once it is clean does it replace the current photo, and the old photo is then deleted with its trace.
- The photo lives in the existing photo block on "My details", with Upload/Replace and Delete (Delete asks for confirmation). No new tile.
- While the scan runs, initials show everywhere and "My details" says "Photo being checked".
- When a file is rejected (by the scan or the image check), initials stay and the doctor sees "The photo could not be accepted; try another file". No reason is given.
- A doctor viewing their own avatar writes no `RemoteAccess` row. One presigned link per request serves the header, the card and the photo block. This is recorded as an AD-9 exception for one's own photo.
- No staff view of the photo for now; only its owner can download it. Later the photo will be viewable by staff in a doctor's file (the rest of the doctor's personal and business data) and when a support ticket is opened. Those views belong to the stories that build them and use the logged download path. Handoff notes go in epic-support and deferred-work.md.
- Account erasure deletes the photo. `purge_erased` handles owned objects, which closes the deferred `StoredObject.owner` PROTECT item.
- The staging-only `sample` purpose and its page are removed.
- Merge and staging are delegated. Staging runs server-side on dev: a clean upload, EICAR rejected, a non-image renamed `.jpg` rejected, delete with trace, and no photo field in the admindev client record. The user checks the avatar by eye in a browser.

## Boundaries & Constraints

**Always:** Uploads go through `/storage/sign/` and `complete`. A separate portal view attaches the object: it checks `uploaded_by == request.user` and the purpose, sets `owner`, and links it on `Profile` (FK, SET_NULL, migration `accounts` 0018). The avatar reads `state` at render time, and only `CLEAN` is shown. Deletes always go through `storage.service.delete` (trace with a blank `patient_code`). Every new string gets its Spanish translation (formal "usted"). The photo stays out of `Profile.SYNCED_FIELDS`, and a photo change does not emit `client.updated`.

**Never:** No core release and no admin-site change. No SVG or other active content. Neither the file name nor the object key goes in logs, page records or the event log. No staff Django-admin inline for the photo.

## I/O & Edge-Case Matrix

| # | Situation | Expected |
|---|---|---|
| 1 | Doctor uploads a 2 MiB JPEG; the scan is clean; the image check passes | Becomes the photo; header, home card, settings card and photo block show it through one presigned link per request; no `RemoteAccess` row |
| 2 | Same, before the scan finishes | Initials everywhere; "Photo being checked" on My details |
| 3 | EICAR file named `x.jpg` | Rejected by the scan; initials stay; reject message; system trace; nothing served |
| 4 | A text file renamed `.jpg` with `image/jpeg` declared, passes the scan | Rejected by the image check (as a scan rejection: keys deleted, system trace); reject message |
| 5 | A real PNG of 8000×8000 px, or a decompression bomb | Rejected by the image check |
| 6 | Declared type `image/svg+xml`, `image/gif` or `text/html` | Refused at sign (400), and the widget does not offer it |
| 7 | File over 5 MiB | Refused by the widget and at sign |
| 8 | Two files selected | The widget takes one only |
| 9 | Doctor with a photo uploads a new one | Old photo stays shown until the new one is clean, then the old one is deleted with a trace (actor: the doctor) |
| 10 | New upload rejected while an old photo exists | Old photo stays; reject message |
| 11 | A second upload while one is still pending | The earlier pending object is deleted with a trace; the newest is the pending one |
| 12 | Delete (confirmed) | Photo and any pending upload unlinked at once (initials on the next page) and deleted through `service.delete`; trace with a blank `patient_code` |
| 13 | Attach POST with an object id uploaded by another user, of another purpose, already owned, rejected, or marked for deletion | Refused (404); nothing linked |
| 14 | Another doctor, staff, or anonymous asks for a download of the photo | Refused: the purpose allows the owner only |
| 15 | Account erased | Photo and pending upload deleted (trace, actor as the erasure's); `purge_erased` later removes the user without a PROTECT error, even with an object still owned |
| 16 | Photo uploaded, changed or deleted | No `client.updated`; the payload keys are unchanged (asserted) |
| 17 | Storage presign fails while rendering | The page still renders with initials; error logged without the key |
| 18 | ES interface | Every new string translated (formal "usted") |

## Code Map

- `storage/purposes.py:51` `register(name, can_upload, can_download, *, max_size)`; registration in `AppConfig.ready` (`storage/apps.py`, `storage/sample.py`). `may_download` (:86) serves only CLEAN.
- `storage/views.py:145` sign, `:78` `_start` (purpose/size/declared type), `:171` complete (creates `StoredObject` without an owner, `:219`; enqueues the scan, `:225`); the widget fires `storage:uploaded` (`static/.../upload.js:135`).
- `storage/models.py:20` `StoredObject` (owner PROTECT :44), `:69` `PendingUpload`, `:91` `DeletionTrace`.
- `storage/service.py:80` `download_url` (+ `_record` :52 → `RemoteAccess`, kept 5 years: `integration/models.py:22`); `:105` `delete`. `storage/s3.py:284` presign (300 s, attachment disposition: harmless for `<img>`); `:162` content-type format check.
- `storage/tasks.py:217` `scan_object_task`, `:163` `_finish_promotion`, `:180` `_reject`; no on-clean hook.
- Widget `storage/templates/storage/_upload_widget.html` + `upload.js:73` (only size restrictions; needs `allowedFileTypes` and `maxNumberOfFiles: 1` parameters).
- Avatar spots: `templates/doctor_base.html:49-52`, `accounts/templates/accounts/home.html:43`, `accounts/templates/accounts/settings_base.html:16`, `accounts/templates/accounts/personal_data.html:30-33` (`data-photo`). Initials: `accounts/doctor_area.py:16`, tag `accounts/templatetags/doctor_area.py:11`.
- `accounts/models.py:211` `Profile` (`SYNCED_FIELDS` :236); latest migration `accounts` 0017. Writer `accounts/profile.py:116`; `clear_profile` :166; `accounts/erasure.py:72` `erase_user` (:100), `:128` `purge_erased`.
- Sync builder `accounts/client_sync.py:46` `payload_of`, `:70` `profile_payload`; core `ClientUpdated` forbids extra fields.
- Tests to update: `accounts/tests/test_personal_data.py:110-113` (asserts no file input), `accounts/tests/test_doctor_area.py:122-143`. Fakes: `storage/tests/conftest.py` (stubber, `TEST_PURPOSE`), `storage/tests/test_scan.py:36` `FakeClamd` and the `expect_*` helpers (:245-280).
- Architecture: AD-2 table (`architecture-portal-admin.md:79-100`) gets the row "Doctor profile photo | Region (object storage) | No | Original file name; object encrypted at rest"; AD-9 (:147) gets the own-avatar note if Q9 is (a).
- `deferred-work.md:37,70` (owner PROTECT vs `purge_erased`), `:125` (avatar by-eye review waiting on 6.4).
- Pillow is not installed today (no ImageField anywhere). ClamAV runs `AlertBrokenMedia no`.

## Tasks & Acceptance

1. **Storage purpose options** (`storage/purposes.py`, `storage/views.py`): `register()` gains `content_types` (declared types allowed at sign; default: any, as today) plus two optional callbacks. `validate(obj, read)` runs in `scan_object_task` after a clean scan and before promotion, and a failure goes through the existing `_reject` path with the reason `invalid_content`. `on_clean(obj)` runs after promotion, on commit. Existing behaviour is unchanged for purposes that set neither. Tests cover both callbacks and the sign refusal.
2. **Widget parameters** (`_upload_widget.html`, `upload.js`): optional allowed types and a single-file mode (`maxNumberOfFiles: 1`). The `storage:uploaded` event stays as it is, and existing callers are unchanged.
3. **Pillow**: added to `requirements.txt` with the version pinned. The image check opens the clean object, verifies it, and accepts only JPEG, PNG or WebP of at most 6000×6000 px. It keeps Pillow's decompression-bomb guard on.
4. **Purpose `profile_photo`** (`accounts`): 5 MiB, the three types, upload by a signed-in doctor, download by the owner only. `validate` is the image check. `on_clean` promotes the photo: if the profile's pending object is this one, it becomes the photo, and the previous photo is deleted through `service.delete` with the uploader as the actor.
5. **Model**: `Profile.photo` and `Profile.photo_pending`, FKs to `StoredObject` with SET_NULL (migration `accounts` 0018). Neither goes in `SYNCED_FIELDS`. A photo change emits no event.
6. **Views** (under settings): attach (POST the object id; the checks in matrix row 13; sets `owner`; replaces and deletes an earlier pending upload) and delete (POST with confirmation; unlinks and deletes both). Both use CSRF, require sign-in and are doctor-only. Neither the file name nor the key is logged.
7. **Avatar link**: `storage.service` gains an owner-only inline link. It requires `obj.owner == user`, state `CLEAN` and not marked for deletion, presigns for at most 300 s, and writes no access row. It is cached per request, used by the avatar tag in the four spots (Code Map), and falls back to initials on any error (matrix row 17).
8. **My details block**: Upload/Replace and Delete, "Photo being checked", and the reject message. Templates and ES `.po` strings.
9. **Erasure**: `erase_user` deletes the photo and the pending upload. `purge_erased` detaches the owner and deletes any objects the user still owns, so it never hits PROTECT. Mark the deferred-work items at `deferred-work.md:37,70` resolved.
10. **Remove the `sample` purpose**: `storage/sample.py`, its page, URL, template and tests. The tests keep `TEST_PURPOSE`.
11. **Docs**: an AD-2 row "Doctor profile photo | Region (object storage) | No | Original file name; object encrypted at rest", an AD-9 note on the owner's own-avatar exception, and an architecture memlog entry. Update the README storage section.
12. **Tests**: matrix rows 1–18. Update `test_personal_data.py:110-113` and `test_doctor_area.py:122-143`. Add an assertion that the sync payload keys are unchanged. Move the storage fakes the accounts tests need into the root `conftest.py`. Run the full suite with 0 failures and check that `makemigrations --check` is clean.

**Acceptance:** the ticket's verify, plus matrix rows 1–18 covered by tests.

## Design Notes

## Verification

## Implementation Notes

## Review Triage Log

</frozen-after-approval>
