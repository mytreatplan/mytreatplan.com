---
title: 'Spike findings: browser multipart upload to OVH Object Storage'
plan: 'spike-browser-multipart-upload-to-ovh-object-storage-plan.md'
ticket: '2'
status: 'awaiting human tests'
cited_by: 'epic-platform entry 11 (the real storage service)'
---

# Spike findings: browser multipart upload to OVH Object Storage

This file has two parts. The **runbook** says how a person runs the tests on dev. The
**results** part is filled in as the tests are run. Entry 11 builds the real storage
service from the results (AD-9).

What was built (plan entry 2): the `upload_spike` app. It is a staff-only page at
`/en/upload-spike/` that uses Uppy 6 with `@uppy/aws-s3` and `signRequest`. There is one
endpoint that signs each S3 request with boto3 (`/en/upload-spike/sign/`), one that reads
an object back (`/en/upload-spike/object/`), and the command
`manage.py upload_spike_bucket` (`--apply` / `--inspect`). All of it is off unless
`UPLOAD_SPIKE_ENABLED=True`.

## Versions under test

| Item | Value |
| --- | --- |
| Uppy bundle | `uppy` 6.0.5 (npm), vendored as `static/js/uppy.min.js` and `static/css/uppy.min.css` |
| S3 plugin inside it | `@uppy/aws-s3` 6.3.0 (its built-in S3 client, "S3mini"), `@uppy/core` 6.3.0, `@uppy/dashboard` 6.0.2 |
| Signing | boto3 / botocore 1.43.90, SigV4 query presigning, virtual-hosted URLs, URLs valid 15 min (download links 5 min) |
| Multipart | used for files over 5 MiB. The part size is Uppy's default, max(5 MiB, size / 10 000), so a 1 GiB file has 205 parts of 5 MiB. Each file sends its parts **one after another**; up to 6 files upload in parallel |
| Object keys | `spike/<random UUID v4>`, made by the server (the browser's key and the file name are ignored and never sent) |

## Runbook

### 1. Bucket and S3 user (OVHcloud panel, done by a person)

1. Create a bucket in **GRA**, e.g. `mytp-dev-files` (Object Storage, S3 API, standard
   class). Do **not** reuse or touch the backup bucket or its user.
2. Create an S3 user for it and give it access to this bucket only. If the panel accepts
   a JSON policy, use one like this (same shape as `s3-bckp-server_Policy.json`):

   ```json
   {
     "Statement": [{
       "Sid": "DevFilesSpike",
       "Effect": "Allow",
       "Action": [
         "s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:ListBucket",
         "s3:GetBucketLocation", "s3:ListBucketMultipartUploads",
         "s3:ListMultipartUploadParts", "s3:AbortMultipartUpload",
         "s3:PutBucketCORS", "s3:GetBucketCORS",
         "s3:PutLifecycleConfiguration", "s3:GetLifecycleConfiguration",
         "s3:PutEncryptionConfiguration", "s3:GetEncryptionConfiguration"
       ],
       "Resource": ["arn:aws:s3:::mytp-dev-files", "arn:aws:s3:::mytp-dev-files/*"]
     }]
   }
   ```

   Record here whether OVH accepted the bucket-configuration actions in the policy. If
   it did not, run step 3 with an admin user's keys once, then switch back to this user.
3. Put the keys in the team password manager. Never paste them in chat.

### 2. Settings on dev (server, done by a person)

```sh
sudo -u mytp nano /srv/mytp/shared/settings.env
```

Add these lines (the keys are typed by hand):

```
UPLOAD_SPIKE_ENABLED=True
UPLOAD_SPIKE_S3_ENDPOINT=https://s3.gra.io.cloud.ovh.net
UPLOAD_SPIKE_S3_REGION=gra
UPLOAD_SPIKE_S3_BUCKET=mytp-dev-files
UPLOAD_SPIKE_S3_ACCESS_KEY=<typed by hand>
UPLOAD_SPIKE_S3_SECRET_KEY=<typed by hand>
UPLOAD_SPIKE_CORS_ORIGINS=https://dev.mytpdso.com
```

Then:

```sh
cd /srv/mytp/current
sudo -u mytp ./venv/bin/python manage.py check --deploy
sudo systemctl restart mytp-gunicorn
```

### 3. Bucket settings

```sh
cd /srv/mytp/current
sudo -u mytp ./venv/bin/python manage.py upload_spike_bucket --apply
```

This sets three things, one at a time:

- CORS: origin `https://dev.mytpdso.com`; methods GET, PUT, POST, DELETE; request
  header `Content-Type`; `ETag` exposed; max-age 3000.
- A lifecycle rule `abort-incomplete-multipart-uploads`: uploads not finished 1 day
  after they started are aborted.
- Default encryption: SSE-S3 (AES256).

It then reads each one back. If OVH refuses something, the command prints
`set <name>: REFUSED <code> (HTTP <status>): <message>` and exits with
`OVHcloud refused: ...`. Paste the full output (it has no keys) under **Results, T0**.

When a line says REFUSED, copy its **exact** error code and message into the results.
botocore adds an `x-amz-checksum-*` header to PutBucketCors,
PutBucketLifecycleConfiguration and PutBucketEncryption, even with
`when_required`. So a checksum, `InvalidArgument` or header error may be OVH rejecting
that header, not the feature. Record it as "retry with the header disabled"
(entry 11 settles it), **not** as "feature unsupported".

### 4. Browser tests (on dev, as a staff user)

Log in at `https://dev.mytpdso.com/admin/` with a staff account, then open
`https://dev.mytpdso.com/en/upload-spike/`. The page shows the event log. After each
test, copy the log into the results. **Use test files only, never patient data.**

Make the test file once on your Mac:

```sh
mkfile -n 1g ~/Desktop/spike-1g.bin   # or: dd if=/dev/urandom of=~/Desktop/spike-1g.bin bs=1m count=1024
shasum -a 256 ~/Desktop/spike-1g.bin
```

Random data (`dd if=/dev/urandom`) is better than zeros for the checksum test.

- **T1 Chrome, 1 GB:** add `spike-1g.bin`, then press Upload. When it finishes, the
  "Stored objects" box shows the key, size (with "matches"), ETag and encryption. Write
  down the time and MiB/s.
- **T2 Safari, 1 GB:** the same test in Safari.
- **T3 Network drop:** in either browser, start a 1 GB upload. At about 30 %, turn
  Wi-Fi off for about 30 s, then turn it back on. Uppy 6.3.0 does **not** retry a part
  request that was cut off while the browser was offline; it raises an upload error
  (the log shows `browser went offline`, then `upload of ... failed`). After Wi-Fi
  returns (`browser is back online`), press Uppy's **retry** button. Record:
  - whether the upload **resumed**: the log shows a `signed ListParts` line and **no
    new** `signed CreateMultipartUpload`, and the part numbers carry on from where they
    stopped; then it completes and the T4 checksum matches;
  - or whether it **restarted** (a new CreateMultipartUpload, parts from 1 again) or
    **failed**.
- **T4 Checksum:** for the T3 object (and for T1), click "Download (for shasum -a 256)"
  within 5 minutes, then:

  ```sh
  shasum -a 256 ~/Downloads/<downloaded file> ~/Desktop/spike-1g.bin
  ```

  The two hashes must match.
- **T5 Abandoned upload:** start a 1 GB upload and close the tab at about 20 %. Then on
  the server:

  ```sh
  sudo -u mytp ./venv/bin/python manage.py upload_spike_bucket --inspect
  ```

  It should list the incomplete upload with its start time. Run the same command
  **more than 24 h later** (OVH may apply lifecycle rules once a day, so allow up to
  48 h). Expected: `incomplete uploads: 0`.
- **T6 Encryption:** the "Encryption" line in T1 shows `AES256`, and `--inspect` shows
  `encryption: AES256`. If the bucket refused default encryption, record that and whether
  OVH encrypts at rest anyway (panel or documentation).
- **T7 Gates:** in a private window (not logged in), `/en/upload-spike/` returns 404.
  The same for a doctor account. On another site with the setting off, it also returns 404.

### 5. Clean-up after the spike

- Delete the test objects under `spike/` (OVH panel). Entry 11 keeps the bucket as dev's
  real bucket.
- Set `UPLOAD_SPIKE_ENABLED=False` (or remove the lines) once the results are in.

## Results

Fill in each row with **works**, **works with a workaround** (say which), or **fails**,
plus the date, browser version and notes.

| # | Test | Result | Date / browser | Notes (paste the log or command output below) |
| --- | --- | --- | --- | --- |
| T0 | `--apply`: CORS | | | |
| T0 | `--apply`: lifecycle (abort incomplete after 1 day) | | | |
| T0 | `--apply`: default encryption (SSE-S3) | | | |
| T1 | Chrome, 1 GB multipart (presigned create, parts, complete) | | | time, MiB/s |
| T2 | Safari, 1 GB multipart | | | time, MiB/s |
| T3 | Network drop of about 30 s mid-upload, then Uppy retry | | | resumed (ListParts, no new Create)? restarted? failed? |
| T4 | Checksum of the downloaded object matches the local file | | | |
| T5 | Abandoned upload listed, then gone after the lifecycle period | | | when it disappeared |
| T6 | Encryption at rest (default SSE reported on objects) | | | |
| T7 | 404 for anonymous, non-staff and setting off | | | |

### Logs and command output

<!-- Paste here: T0 output, each test's event log, the T5 --inspect outputs with dates. -->

## For entry 11

To be completed from the results:

- **Presigned multipart on OVH:**
- **Uppy pin:** `uppy` 6.0.5 (`@uppy/aws-s3` 6.3.0), unless the tests show a reason to
  change it.
- **Bucket settings to keep:** the CORS rule above (origin per site); the lifecycle rule
  (AD-9 says 7 days in production; the spike used 1); default encryption.
- **Part size and parallelism:** do 5 MiB parts that go one after another per file give
  acceptable speed for 1 GB? If not, set `getChunkSize` (e.g. 16–64 MiB) in entry 11.
- **Resume behaviour:** from T3 (Uppy needs a manual retry after an offline cut; does it resume via ListParts?).
- **Workarounds needed:**
- **Known limits of the spike code:** each sign request updates the session (the
  session idle limit). This is fine on PostgreSQL. On SQLite it locks under parallel
  uploads (seen only in the local browser test). The object read-back is a server-side
  HeadObject.
