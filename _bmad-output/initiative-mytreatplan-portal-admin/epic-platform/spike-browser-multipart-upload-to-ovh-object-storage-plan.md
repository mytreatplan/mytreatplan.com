---
title: 'Spike: browser multipart upload to OVH Object Storage'
type: 'feature'
ticket: '2'
created: '2026-10-29'
status: 'built'
baseline_revision: '47a300292c1417f7ba1cba1ea85d732a55cac46a'
route: 'full'
route_source: 'auto'
risk: 'high'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-mytreatplan-portal-admin/architecture-portal-admin/architecture-portal-admin.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** AD-9 relies on browsers uploading large patient files (CBCT, scans, 1 GB and more) straight to the region's bucket through presigned S3 multipart, but nothing confirms that OVHcloud Object Storage accepts presigned multipart operations (its docs list presigned URLs only for GET, PUT, HEAD, DELETE), a lifecycle abort of incomplete uploads, or default encryption at rest. Entry 11 builds the real storage service on whatever this spike proves.

**Approach:** A throwaway, staff-only upload page on dev uses Uppy 6 (`@uppy/aws-s3`, `signRequest`) against a dedicated staging bucket; one Django endpoint signs each S3 operation with boto3. A management command applies and prints the bucket's CORS, lifecycle and encryption. A person runs the browser tests and records the results.

## Decisions

- Bucket (user, 2026-10-30): a new staging-only bucket in OVH GRA (e.g. `mytp-dev-files`) with an S3 user limited to that bucket; it becomes dev's real bucket in entry 11.
- Spike code (user, 2026-10-30): merged to master behind `UPLOAD_SPIKE_ENABLED` (off by default, on only in dev's settings.env); entry 11 deletes the app.
- Findings (user, 2026-10-30): a findings file beside this plan, `spike-browser-multipart-upload-to-ovh-object-storage-findings.md`, cited by entry 11.

## Boundaries & Constraints

**Always:** signing happens server-side with the bucket credentials from settings.env; the browser never sees them. Object keys are random UUIDs (AD-5). Only staff users reach the page and endpoint. The page and endpoint are off unless the spike setting is on. Bucket credentials are typed into settings.env by a person, never pasted in chat.

**Never:** no models, no migrations, no production use, no quarantine or scanning (entries 11 and 12), no change to the backup bucket or its credentials, no Companion server.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | Staff uploads a 1 GB file in Chrome or Safari | Object stored under a UUID key; page shows key, size, ETag | — |
| Network drop | Wi-Fi off for ~30 s mid-upload | Upload resumes or retries the failed parts and completes; object checksum matches the local file | If Uppy does not recover, the finding records it |
| Abandoned upload | Tab closed mid-upload | Incomplete upload listed by the command, then gone after the lifecycle period | Finding records whether OVH applies the rule |
| Not staff / spike off | Anonymous user, or setting off | 404 on page and endpoint | — |
| Bad sign request | Unknown method or missing key/uploadId | 400 JSON error, nothing signed | — |

</frozen-after-approval>

## Code Map

- `mytreatplan/settings.py` -- env-driven settings via `config(...)`; add `UPLOAD_SPIKE_*` beside `BACKUP_S3_*` (pattern at the BACKUP block).
- `deploy/staging.env.example` -- document the new variables, blank values (lines 76–80 show the BACKUP_S3 pattern).
- `mytreatplan/urls.py` -- include the spike app's URLs only when the setting is on.
- `package.json` -- add `uppy` 6.0.5 and a `vendor:uppy` script copying `dist/uppy.min.js` and `dist/uppy.min.css` into `static/` (same pattern as `vendor:htmx`).
- `templates/portal_base.html` -- base for the page (noindex already set).
- `boto3` (already pinned) -- `generate_presigned_url` for `create_multipart_upload`, `upload_part`, `list_parts`, `complete_multipart_upload`, `abort_multipart_upload`, `put_object`; `put_bucket_cors`, `put_bucket_lifecycle_configuration`, `put_bucket_encryption`, `list_multipart_uploads` for the command.
- Do not touch `mytp_publicsite/backup*` or the BACKUP_S3 settings.

## Tasks & Acceptance

**Execution:**
- [x] `upload_spike/` (new app: views, urls, template, management command `upload_spike_bucket`) -- page, signing endpoint, bucket setup/inspection -- the spike's whole surface in one removable app.
- [x] `mytreatplan/settings.py`, `deploy/staging.env.example`, `mytreatplan/urls.py` -- wire settings and URLs behind `UPLOAD_SPIKE_ENABLED` (default False).
- [x] `package.json`, `static/js/uppy.min.js`, `static/css/uppy.min.css` -- vendor Uppy 6.0.5.
- [x] `upload_spike/tests/` -- staff gate, setting gate, each signed operation's method/params, 400 on bad input (boto3 stubbed, no network).
- [x] `_bmad-output/.../epic-platform/spike-browser-multipart-upload-to-ovh-object-storage-findings.md` -- runbook for the human tests and the results template.

**Acceptance Criteria:**
- Given the spike setting on and a staff session on dev, when the page loads, then Uppy uploads a file to the staging bucket and shows its key, size and ETag.
- Given the bucket credentials in settings.env, when `manage.py upload_spike_bucket --apply` runs, then CORS (GET, PUT, POST, DELETE; content-type; ETag exposed), the abort-incomplete lifecycle rule (1 day) and default encryption are set or the command reports exactly which OVH refused.
- Given the human test runs, when they finish, then the findings file states for Chrome, Safari, network drop, checksum, lifecycle and encryption: works, works with a workaround, or fails, plus the Uppy version and bucket settings for entry 11.

## Spike findings

## Implementation Notes

- Uppy 6's `@uppy/aws-s3` (6.3.0, in the `uppy` 6.0.5 bundle) calls `signRequest({method, key, uploadId?, partNumber?, contentType?})` once per S3 request. `upload_spike/s3.py` maps each request to exactly one operation (PUT → PutObject, POST → CreateMultipartUpload, PUT+uploadId+partNumber → UploadPart, GET+uploadId → ListParts, POST+uploadId → CompleteMultipartUpload, DELETE+uploadId → AbortMultipartUpload). Anything else gets 400 `{"error": {"code": "bad_request", ...}}`, including plain GET/DELETE of objects. The presigned URL is signed for that HTTP method only.
- Keys: on PutObject and CreateMultipartUpload the server makes the key `spike/<uuid4>` and returns it as `key` (Uppy honours it). Later requests must name such a key, so the endpoint cannot sign any other key in the bucket. The page also sets `generateObjectKey` to a random UUID and `allowedMetaFields: false`, so no file name leaves the browser. The `spike/` prefix lets entry 11 delete the test objects.
- Gates: the routes sit under the language prefixes (`/<lang>/upload-spike/`, `sign/`, `object/`) and are included only when `UPLOAD_SPIKE_ENABLED` is on (`mytreatplan.urls.upload_spike_urlpatterns`, the same pattern as PORTAL_ENABLED). The views also return 404 unless the setting is on and the user is active staff. The sign and object endpoints are POST, CSRF-protected and `never_cache`. Missing bucket settings → 503 naming the setting.
- Added a read-back endpoint (`object/`). It does a server-side HeadObject, so the page can show key, size, ETag and SSE, as the I/O matrix requires; the ETag is not in Uppy's success event. It also returns a 5-minute presigned GET for the checksum test. This needs no extra CORS method.
- boto3 client: SigV4, virtual-hosted addressing (the OVH documented style), `request_checksum_calculation='when_required'` (as in backupdb), so presigned URLs carry no CRC parameters the browser could not match.
- `upload_spike_bucket`: `--apply` sets CORS (origins from `UPLOAD_SPIKE_CORS_ORIGINS`, default SITE_URL), the 1-day abort-incomplete lifecycle rule and SSE-S3 default encryption, each separately. It reports every refusal by name with OVH's code and exits with `OVHcloud refused: ...`. It always reads back CORS, lifecycle, encryption and the incomplete uploads (`--inspect` or no flag does only that). Messages have no keys.
- Page styles are inline, and the app's JS lives in `upload_spike/static/` (Tailwind does not scan the app), so entry 11 can delete the app, the setting block, the URL hook, the env-example block and the Uppy vendoring in one go. Strings are English only and stay out of the translation catalogue (the app is not in TRANSLATABLE_FOLDERS).
- Added `upload_spike` to `pytest.ini` testpaths. The browser test (`upload_spike/tests/browser/test_upload.py`) emulates the bucket with Playwright routes. It covers the vendored Uppy loading, a small PutObject, and a 12 MiB multipart upload (create, parts 1–3, complete), with no file name reaching the site or the bucket. It uploads one file per page load because SQLite locks on parallel session writes (each request updates the session); PostgreSQL on dev does not.
- Vendoring ran in the sandbox with `npm install --cache $TMPDIR/npmcache` and `npm run vendor:uppy`. The sandbox cannot write to `~/.npm`; npm reports this as "root-owned files". It is a sandbox limit, not a repo problem. `package-lock.json` was updated by the install.
- Local verification (2026-10-30): full suite 1536 passed; the only failures were the 3 known PostgreSQL-only tests. `upload_spike` has 75 tests, including the browser test. `manage.py check` reported no issues. The OVH-side behaviour has not been tested yet: that is the human runbook in the findings file.

## Plan Change Log

## Review Triage Log

- Pass 1 (quick): 5 findings; medium 3, low 1, false 1. Patched 4.
  - low, patch: `upload_spike/tests/test_views.py` `reload_urlconf` captured `UPLOAD_SPIKE_ENABLED` after the autouse fixture set it True, so teardown left spike routes in the real URLconf for later tests (verified in the fixture). Teardown now reloads with False.
  - medium, patch: `upload-spike.js` `postJson` treated a 2xx non-JSON reply as success; after the 15-minute idle redirect (accounts/sessions.py) Uppy got no `url` and posted to `/en/upload-spike/undefined`, which would be recorded as an OVH refusal. Redirects, non-JSON bodies and replies without `url` now fail with "session expired" in the event log.
  - medium, patch: findings runbook T3 expected Uppy to resume by itself after a network drop; Uppy 6.3.0 does not retry a part cut off while offline. T3 now has the tester press retry and record resumed / restarted / failed.
  - medium, patch: botocore adds an `x-amz-checksum-*` header to the three bucket-configuration calls even with `when_required`, so a REFUSED line could be the header, not the feature. The runbook now asks for the exact error and records header errors as "retry without the header", settled in entry 11.
  - false: the matrix row "missing key/uploadId → 400" covers requests that need an existing object; new uploads have no key by design (the server makes `spike/<uuid>`, per Always and AD-5). Every request that needs a key or uploadId is refused without it (tests in test_views.py).
- After patches: full suite 1536 passed, 3 known PostgreSQL-only failures; `manage.py check` clean.

## Verification

**Commands:**
- Full suite, from the repo root (non-secret test keys; the venv is `../.venv`; browser tests need the sandbox off): `export FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU="; export DATABASE_URL="sqlite:///$TMPDIR/x.sqlite3"; ../.venv/bin/python -m pytest -q -p no:cacheprovider --tb=line` -- expected: all pass except the 3 known PostgreSQL-only tests in `accounts/tests/test_crypto.py` (`test_pending_registration_row`, `test_rotation_with_reencrypt_fields`, `test_data_migration_encrypts_existing_rows_and_reverses`).
- Vendoring: `npm install` then `npm run vendor:uppy` (needs network; run with the sandbox off if the npm registry is blocked).
- `python manage.py check` -- expected: no issues.

**Manual checks (if no CLI):**
- On dev, as staff: 1 GB upload in Chrome and in Safari, one with Wi-Fi off for ~30 s; compare `shasum -a 256` of the local file with the downloaded object.
- `manage.py upload_spike_bucket --inspect` the next day shows no incomplete upload left.

## Staging

- Merged in PR #48 as merge commit c80c774ba63f8c6838eb3821667fd6fb568e4924 (2026-10-30).
- CI run 41020471632 on master: 1539 passed on PostgreSQL 17; deploy-staging succeeded.
- Check 1 (deploy ok, `/en/upload-spike/` 404 with the setting off): confirmed by the user.
- Checks 2–7 (bucket, settings on dev, `--apply`, browser tests T0–T7, results table): pending, run by the user; the plan stays `built` until the findings are recorded.
