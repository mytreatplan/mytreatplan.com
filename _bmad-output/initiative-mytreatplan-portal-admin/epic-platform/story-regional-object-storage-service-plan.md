---
title: 'Regional object storage service'
type: 'feature'
ticket: '11'
created: '2026-11-06'
status: 'built'
baseline_revision: 'e8ba2d25506c8ed2a60f26b15f27f4ab616ea389'
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

**Problem:** AD-9 (and AD-2, AD-13, AD-19, AD-23): doctors will upload records, STL files and photos of up to ~1 GB to their region's object storage; only a throwaway spike page exists, with no ownership, download logging or deletion trace.

**Approach:** Turn the spike (entry 2, `upload_spike/`) into a portal `storage` app: settings named `STORAGE_*`, server-made UUID keys under `quarantine/` and `clean/`, one signed-operation endpoint for Uppy's multipart upload, a `StoredObject` row per completed upload, a download function (permission hook, log first, presigned GET ≤ 5 minutes) and an idempotent delete task that leaves only a trace; bucket settings by command and runbook; the spike app, page and settings removed.

## Boundaries & Constraints

**Always:**
- Keys made by the server only: `quarantine/<uuid4>` on upload; `clean/<uuid4>` is written by entry 12's scan (this story only defines it). The browser's key and file name are never used in keys; the original file name is stored field-encrypted (AD-2) and never logged.
- `StoredObject(uuid pk, purpose, owner (User FK, nullable), uploaded_by, size, content_type, original_name (encrypted), state quarantine/clean, created_at)`; created only when the multipart upload completes and HeadObject confirms the size.
- Upload endpoint: `POST /storage/sign` (as the spike's `sign`): login required, a per-purpose permission hook `storage.purposes.register(name, can_upload, can_download)`; presigned URLs live 15 minutes; size limit per purpose (default 1 GiB); 403 on a part is retried once by the widget.
- Download: `storage.service.download_url(obj, user, reason)` — hook check → access record written first (eventlog activity kind `file`, object UUID and reason, never the name; if it cannot be written, no URL) → presigned GET of at most 5 minutes.
- Delete: `storage.service.delete(obj, actor)` enqueues `delete_object_task(uuid)` on commit; the task deletes the S3 object (both prefixes), the row, and writes `DeletionTrace(object uuid, patient_code (blank until patients exist), actor type, actor uuid, deleted_at)`; idempotent (keyed by UUID, `run_once`).
- No keys, URLs, file names or credentials in task arguments, logs or Sentry (AD-23).
- Bucket: command `storage_bucket --apply/--inspect` (CORS per site origin exposing ETag, lifecycle abort of incomplete uploads after `STORAGE_ABORT_INCOMPLETE_DAYS` default 7, default encryption SSE-S3), run with the bucket owner's keys passed in the environment for that one command; runbook section "Object storage".
- Uppy stays `uppy` 6.0.5 (vendored); the widget is a template include + JS (`storage/upload.js`) seeded from the spike's.
- Removed: `upload_spike` app, its URLs, templates, JS, tests and `UPLOAD_SPIKE_*` settings (dev's settings.env keys are renamed `STORAGE_*` during staging).
- The spike's open tests become this story's staging checks: T3 (network drop → retry resumes; record resumed/restarted), T5 (abandoned upload gone after the lifecycle period), T7 (404 for anonymous / non-staff / the sample setting off).

- Decision (user, 2026-11-06): until real owners exist, uploads use a staging-only "sample" purpose for staff behind `STORAGE_SAMPLE_UPLOADS=True` (a system check refuses it on production sites, as `INTERFACE_SAMPLE_EVENTS`), on a page replacing the spike's; removed when the first real purpose arrives.
- Decision (user, 2026-11-06): before entry 12 the sample purpose may download from `quarantine/` (staging only); every real purpose downloads only `clean/` objects.
- Decision (user, 2026-11-06): uploads use 16 MiB parts, 3 parts in parallel per file (Uppy `getChunkSize` / `limit`), measured again in the staging T1.
- Decision (user, 2026-11-06): dev only now; dev-ae gets its own EU bucket later (the user creates it and its S3 user in OVH).
- Decision (user, 2026-11-06): the deletion trace is kept for good (no purge).
- Decision (user, 2026-11-06): the plan is kept whole (~2300 tokens, above the size target).

- Decision (user, 2026-11-06, after review): Uppy 6.0.5 sends one file's parts one after another; keep it, with 16 MiB parts and `limit: 3` across files, measured in the staging T1 (replaces "3 parts in parallel per file").
- Decision (user, 2026-11-06, after review): an hourly `sweepstorage` job deletes `quarantine/` objects older than a day with no `StoredObject` row (also any over the purpose's limit) and expired `PendingUpload` rows; `StoredObject.delete_requested_at` is set when a deletion is asked (no download URL after that), and the job re-enqueues pending deletions until they succeed.

**Never:** no virus scanning or quarantine→clean promotion (entry 12); no real owner models (patients, orders: later epics); no change to `BACKUP_*` settings, the backup bucket, the `regions` guard rule or mytp-core's contract checks; no admin-site access to files (AD-23).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Upload 1 GB | staff on dev, sample purpose | object at `quarantine/<uuid>`; `StoredObject` row with encrypted name | — |
| Not allowed | anonymous / user without the purpose's permission | sign refused 403 (404 for the sample page) | — |
| Too large | file over the purpose's limit | refused before any upload | error body |
| Foreign key | browser sends another object's key or upload id | refused | 400 |
| Download | allowed user | access record, then URL valid ≤ 5 min | log fails → no URL |
| Download refused | other user | 403, refusal recorded | — |
| Delete | object deleted | S3 object gone, row gone, trace (uuid, actor, time) | task retried, idempotent |
| Abandoned upload | tab closed mid-upload | no row; lifecycle removes the parts | — |

</frozen-after-approval>

## Code Map

Work ONLY in the worktree `/Users/jadeandres/Library/CloudStorage/GoogleDrive-mytpdso@gmail.com/Mi unidad/Desarrollo Portal MyTreatplan/code/mytreatplan-1.11` (branch `story/1.11-object-storage`, already created from e8ba2d2; do not push or open PRs). The main portal checkout is busy with another story: do not touch it. This plan file lives in the planning worktree `.../code/mytreatplan-planning` (write Implementation Notes there). Tests: create your own venv `/private/tmp/claude-501/portal-1.11-venv` (`uv venv --python 3.13` + `uv pip install --no-cache -r requirements-dev.txt` from the worktree; do NOT install into the shared `../.venv`) and run the suite with the local-dev-notes environment (`FIELD_ENCRYPTION_KEYS`, `BLIND_INDEX_KEY` test values, `DATABASE_URL=sqlite:///$TMPDIR/p111.sqlite3`); 3 known PostgreSQL-only failures in `accounts/tests/test_crypto.py`. Sandbox: commands need it disabled. Never call real OVH: tests use fakes/stubs (as the spike's conftest).

Portal paths (repo root):
- Spike to reuse: `upload_spike/s3.py` — `make_client()` :56, `operation_for()` :124, `HTTP_METHODS` :160, `presign_operation()` :170, `presign()` :189, validators `_key`/`_upload_id`/`_part_number`/`_content_type`, `new_key()`/`KEY_PREFIX`/`KEY_RE` :24-25, expiries :27-29, `describe()` :194 (HeadObject + presigned GET); `upload_spike/views.py` `sign` (shape), `_error`; `management/commands/upload_spike_bucket.py` (CORS :22-25, `cors_configuration` :36, `lifecycle_configuration` :46, `encryption_configuration` :55, `_scrub`, `describe_error`); `static/upload_spike/upload-spike.js` (`signRequest` :102, multipart over 5 MiB :98, offline/retry :145-150); tests `upload_spike/tests/` (conftest fake keys, `test_views.py`, `test_command.py`), `tests/browser/test_upload.py`.
- To remove: `upload_spike/` (app, urls mounted at `mytreatplan/urls.py:60-77`, templates, static page), settings `mytreatplan/settings.py:249-262` and `INSTALLED_APPS` :361-362; `deploy/staging.env.example:118-133` and the dev-ae strip notes (`staging.env.example:23`, `deploy/README.md:1541,1550`).
- Keep: `static/js/uppy.min.js`, `static/css/uppy.min.css`, `package.json` `vendor:uppy` pin 6.0.5; `BACKUP_*` (settings :234-243) untouched.
- Region profile: `regions/profiles.py` `RegionProfile` :35, `storage_endpoint` :47 (eu :77, ae '' :90); settings `STORAGE_ENDPOINT` :79 (already read, unused); bucket and keys stay settings.env-only (profile docstring :9-12); guard `regions/tests/test_guard.py`.
- Tasks: `mytp_core.tasks` `enqueue_on_commit` :54, `run_once` :72; example `mytp_publicsite/tasks.py`.
- Logging: `eventlog.service.record_activity` :112 (never raises — the download needs a variant that reports failure), `eventlog/retention.py` `ACTIVITY_KINDS` :27 (add `file`); `accounts.User` (`uid`, `is_staff`).
- Encryption: `mytp_core.crypto` `EncryptedCharField` (as `accounts/models.py`).
- Runbook model: backup-bucket sections `deploy/README.md` ~262-316, ~1600-1618 (owner-only lifecycle).
- Do not touch `accounts/`, `integration/`, `signup/` beyond what is listed (4.8 is in flight there; build this in a separate worktree, rebased after 4.8 merges).

## Tasks & Acceptance

**Execution:**
- [x] `storage/` app: models + migration, `s3.py` (from the spike), `purposes.py`, `service.py` (download_url, delete), `tasks.py`, `views.py` (`sign`, `complete`), widget include + `upload.js`, sample purpose and page, `storage_bucket` command, checks (settings present when enabled).
- [x] Settings `STORAGE_*`, `staging.env.example`, eventlog kind `file`, remove `upload_spike`.
- [x] Runbook "Object storage" (bucket, user policy, CORS, lifecycle, encryption, rename of dev's keys).
- [x] Tests: every matrix row; access record before URL (failure → none); task idempotent; nothing sensitive logged; browser test ported.

**Acceptance Criteria:**
- Given a download URL issued, when 5 minutes and 1 second have passed, then OVH refuses it (staging check).
- Given the delete task runs twice for one object, when it finishes, then there is one trace and no error.

## Implementation Notes

2026-11-06, worktree `mytreatplan-1.11` (branch `story/1.11-object-storage`, uncommitted):

- **App `storage/`**: `models.py` (`StoredObject`, `PendingUpload`, `DeletionTrace`; migration `0001_initial`), `s3.py` (from the spike), `purposes.py` (`register(name, can_upload, can_download, *, max_size=1 GiB, download_quarantine=False)`), `service.py` (`download_url`, `delete`), `tasks.py` (`delete_object_task`), `views.py` (`sign`, `complete`), `sample.py` (sample purpose, page, download and delete buttons), `checks.py` (E001 setting missing when the bucket or the sample is on, E002 sample on a public site, E003 abort days < 1), `management/commands/storage_bucket.py`, `templates/storage/_upload_widget.html` + `sample.html`, `static/storage/upload.js`.
- **Routes**: `POST /storage/sign/` and `/storage/complete/` at the root (outside the language prefixes, always routed; 403 JSON for anonymous/inactive/no permission). The sample page `/<lang>/storage/sample/` (+ `<uuid>/download/`, `<uuid>/delete/`, POST) is routed only with `STORAGE_SAMPLE_UPLOADS` and answers 404 otherwise.
- **Added beyond the plan's model list: `PendingUpload`** (uuid = future object id, user, purpose, size, content type, bound S3 upload id). It is how "Foreign key → 400" is enforced: every later request must name the user's own pending key, and the first request carrying an `uploadId` binds it; another id is 400. It also carries the declared size so `complete` can compare HeadObject's size without trusting the browser twice. Rows older than `STORAGE_ABORT_INCOMPLETE_DAYS` are purged when the next upload starts (no new job).
- **Multipart only**: single PutObject is never signed (one code path; the size check happens at `complete`). Zero-byte files are refused by the widget (`minFileSize: 1`). The browser declares purpose, size and type at CreateMultipartUpload (via a local key → file map in `generateObjectKey`); the file name goes only to `complete` (stored encrypted). Part numbers are bounded by the declared size; a wrong stored size at `complete` deletes the object (400 `size_mismatch`).
- **Parallelism (decision 3)**: Uppy 6.0.5's `@uppy/aws-s3` 6.3.0 uploads one file's parts **one after another** (seen in the bundle and in the spike findings); `limit` is the number of requests at once across files. So the widget sets `getChunkSize` 16 MiB and `limit: 3`, but "3 parts in parallel per file" is not achievable without changing Uppy; T1 should measure 16 MiB sequential parts.
- **403 retry**: on `upload-error` with S3 status 403 the widget calls `uppy.retryUpload` once per file after 2 s; the browser test shows it resumes through ListParts (no second Create).
- **Download**: hook → eventlog `record_activity(kind 'file', 'download' | 'download_refused', reason=<code>, data={object, purpose} (+ IP/UA with a request))`; `record_activity` already returns None on failure, which `download_url` turns into `AccessRecordFailed` (no URL). Presigned GET 300 s, `Content-Disposition: attachment; filename="<uuid>.<ext>"` (extension only, never the name). Real purposes only `clean`; `download_quarantine=True` only for `sample`. `eventlog.Attempt.FILE` added to `ACTIVITY_KINDS` (5-year activity retention, no migration: `kind` has no choices).
- **Delete task retries**: `run_once('storage.deleted', uuid)` around S3 delete (both prefixes) + row delete + `DeletionTrace` (get_or_create). On failure nothing is kept and the task re-enqueues itself with `run_after` (1, 2, 4, 8, 16 min; 6 attempts), then reports to Sentry by UUID only. No `delete_requested_at` flag on the row (not in the plan): an object stays downloadable until the task succeeds, and after the 6th failure only Sentry knows.
- **Sentry (AD-23)**: Sentry's default boto3/stdlib integrations add HTTP breadcrumbs with `aws.request.url` (bucket host + object key); mytp-core's `scrub_event` strips only query strings. Without touching mytp-core, every storage S3 call runs inside `s3.out_of_sentry()` (a forked `isolation_scope`), so those breadcrumbs are dropped; tested with the real `sentry_capture` client. **Other S3 users (backupdb in mytp-core) still leave such breadcrumbs** — worth a mytp-core change (scrub `aws.request.url`) later.
- **Settings**: `STORAGE_ENDPOINT` (existing), `STORAGE_REGION` (default: parsed from an OVH endpoint), `STORAGE_BUCKET`, `STORAGE_ACCESS_KEY`, `STORAGE_SECRET_KEY`, `STORAGE_CORS_ORIGINS`, `STORAGE_ABORT_INCOMPLETE_DAYS` (7), `STORAGE_SAMPLE_UPLOADS` (False). `storage_bucket --apply` takes the owner's keys from the environment (python-decouple reads the environment before settings.env); the runbook types them with `read -rs`.
- **`StoredObject.owner`** is `PROTECT` (a user with files cannot be purged until the files are deleted with a trace); `uploaded_by` is `SET_NULL`. The sample purpose sets no owner. Real purposes will need an owner hook (not in this story).
- **Removed**: `upload_spike/` (app, URLs, templates, JS, tests), `UPLOAD_SPIKE_*` settings and env example lines; dev-ae strip notes now strip `STORAGE_*` and set `STORAGE_SAMPLE_UPLOADS=False`. Browser conftest moved to `storage/tests/browser/`.
- **Runbook**: `deploy/README.md` "Object storage" (bucket and owner, site user policy without bucket-config actions, in-place rename of dev's keys without printing values, `--apply` with owner keys, `--inspect`, staging checks T1/T3/T4/T5/T7, URL expiry, delete) and a row in the top table.
- **Verification (local, SQLite, sandbox off)**: `storage/` 143 tests pass, including both browser tests in Chromium (small + 20 MiB two-part upload stored with name; a part refused with 403 retried once and resumed via ListParts). Full suite after `compilemessages`: 1952 passed, 4 skipped, 8 failed, none from this story: the 3 known PostgreSQL-only `test_crypto` ones; `test_ticked_right_code_logs_in_and_sets_the_mark` (fails on the untouched baseline e8ba2d2 too on SQLite: pk `1` vs a random hex cookie key); 2 `signup` browser `site.css` checks (Tailwind CSS not built in this worktree); `test_request_error_reaches_sentry_scrubbed` (asserts no `private` in the event, and this venv lives under `/private/tmp`); and the storage Sentry test, since fixed (it now ignores other tests' SQL breadcrumbs) and passing. `makemigrations --check` clean, `manage.py check` clean.
- **Owed**: the staging checks (Verification below, runbook "Object storage" → Staging checks), with the user: rename dev's keys, `--apply` with the owner's keys, T1/T3/T4/T5/T7, URL refused after 5 min 1 s, delete → trace.
- **Review fixes (2026-11-06)**: (1) hourly job `sweepstorage` (`storage/sweep.py`, command `sweep_storage`, `deploy/jobs.conf` `*-*-* *:20:00 UTC`): deletes row-less `quarantine/` objects older than a day or over their purpose's limit (pending upload's purpose, else the largest registered limit), forgets expired `PendingUpload` rows, prints counts only; skips the bucket step without the bucket settings (dev-ae). (2) `StoredObject.delete_requested_at` (migration `0002`): set by `service.delete` in the caller's transaction (only if unset); `download_url` refuses (recorded refusal) once set; the sample page no longer lists such rows; the sweep enqueues `delete_object_task` again (actor `system`) for rows marked over 45 minutes ago, i.e. past the task's own retries — so a deletion retried by the sweep leaves a trace with actor `system`, not the user (no actor field on the row). (3) `upload.js` unchanged (16 MiB parts, `limit: 3` across files, per the decision after review); runbook T1 now says to record the measured time and MiB/s. (4) `complete`: a duplicate primary key from a concurrent call is caught and answered with the stored object (no 500). Tests: `storage/tests/test_sweep.py`, plus the concurrent-complete and not-listed cases; `storage` 152 pass.
- **Local test env**: the worktree has no settings.env, so the suite also needs CI's `SECRET_KEY=ci-only-not-a-secret DEBUG=False SECURE_SSL_REDIRECT=False ALLOWED_HOSTS=localhost,127.0.0.1,testserver`.

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, opus, 2026-11-06): 5 findings. intent_gap 3 (resolved by the user), low 1 patch, deferred 1.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | Completed uploads never recorded stay in the bucket for good; part size unsigned so a client can store more than the limit and skip `/complete/` | medium | intent_gap → resolved | User decision "after review": `sweepstorage` job deletes row-less quarantine objects after a day. |
| 2 | "3 parts in parallel per file" impossible with Uppy 6.0.5 | medium | intent_gap → resolved | User decision: sequential 16 MiB parts, measured in T1. |
| 3 | Delete retries only in the task queue; object downloadable until deleted | medium | intent_gap → resolved | User decision: `delete_requested_at` + the sweep re-enqueues. |
| 4 | Two concurrent `/complete/` calls → IntegrityError 500 | low | patch | Catch the duplicate and answer as the first. |
| 5 | `StoredObject.owner` PROTECT would block `purge_erased` batches once a purpose sets an owner | medium (latent) | defer | No purpose sets `owner` yet; deferred-work entry for the first real purpose. |

## Verification

**Commands:**
- Portal suite (local dev notes) — all pass; the browser test runs in CI.

**Manual checks (staging, dev; with the user):**
- Rename dev's `UPLOAD_SPIKE_*` keys to `STORAGE_*`; `storage_bucket --apply` with the owner's keys (lifecycle 7 days); `--inspect`.
- T1 (Chrome 1 GB, speed), T3 (network drop → retry), T4 (checksum of the download), T5 (abandoned upload gone after the lifecycle period), T7 (404 anonymous / non-staff / setting off); a download URL refused after 5 minutes and its access record present; delete → object gone, trace present.
