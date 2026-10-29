---
review: currency
target: architecture-portal-admin.md (Stack table and named technology in AD-4, AD-8, AD-9, AD-11, AD-14)
date: '2026-10-29'
method: PyPI JSON API and npm registry queried live; official docs fetched (Django, django-tables2, django-tasks-db, django-ox, Uppy, OVHcloud, ClamAV, Debian manpages, MaxMind, DB-IP, PyJWT, PostgreSQL); compared with the repo's requirements.txt, package.json, .github/workflows/ci.yml and deploy/provision.sh
verdict: 'Mostly grounded; two Stack rows are out of date (django-tables2, AdminLTE), the django.tasks row hides a retry gap that AD-3 depends on, and ClamAV and geo-IP need constraints the spine does not state.'
---

# Currency review: architecture spine, portal and admin site

## Verdict

The rows that come from the part-1 project are current and match the repo. Python 3.13, Django 6.1.x, psycopg 3.3.6, cryptography 50.0.2, Gunicorn 26.2.0, Tailwind 4.3.3, htmx 2.0.11 and PostgreSQL 17 all check out against PyPI, npm and requirements.txt. The admin-side rows are new, and they were written from memory without being checked. Two are out of date. **django-tables2 "2.8.0" is two majors behind** (3.0.1 is current, and 2.8.0 does not support Django 6.1). **AdminLTE "4.0.x" is ten minors behind** (4.10.0). The django.tasks row is left open on purpose, but AD-3 and AD-8 rely on a retry behaviour that Django's API does not provide. ClamAV and geo-IP need constraints the spine does not state.

## Findings

### F1 — django-tables2 2.8.0 does not support Django 6.1. Use 3.0.x. (Stack row "django-tables2"; AD-11) — HIGH

- PyPI shows these releases: 2.8.0 (2025-11-21, declares Django 4.2/5.1/5.2/6.0), 2.9.0 (yanked), 3.0.0 (2026-04-13) and **3.0.1 (2026-08-30), the first release with Django 6.1 support**. 3.0.1 also drops Python 3.10.
- 3.0 has breaking changes: `RelatedLinkColumn` is removed (use `Column(linkify=True)`), and **the `querystring` template tag is renamed `querystring_replace`**. Any AdminLTE table template copied from older examples will break.
- CSV export through tablib is still supported. It needs the `django-tables2[tablib]` extra (tablib 3.10.0, 2026-07-31). Since 2.9/3.0, date and time columns export in ISO format.
- Change: set the Stack row to `django-tables2 3.0.1 (+[tablib] extra; tablib 3.10.0)`. Mention `querystring_replace` in the epic-admin-access inception note.
- Sources: https://pypi.org/project/django-tables2/ , https://github.com/jieter/django-tables2/blob/master/CHANGELOG.md

### F2 — AdminLTE is at 4.10.0, not 4.0.x, and has an official Django integration (Stack row "AdminLTE"; AD-11) — MEDIUM

- The npm `admin-lte` `latest` tag is **4.10.0 (2026-10-01)**. The 4.x line reached stable around 4.0.0 and went through 4.1 (ESM bundle and TypeScript types, 2026-07) up to 4.10. Pinning "4.0.x" would start an old line.
- There is now an official `adminlte-django` package (0.3.2). It requires Django >= 6.0, django-components and django-vite. Its `tables` extra pulls in django-tables2 >= 3.0 and django-filter >= 25.2. That adds a Vite build and a component framework to a project that today vendors static files through npm scripts. Inception should either adopt it or reject it in writing. Rejecting it and vendoring the dist CSS/JS the way htmx is vendored fits the repo's current pattern better.
- Change: set the Stack row to `AdminLTE 4.10.x (vendored dist via npm, as htmx is)`. Add one line recording that `adminlte-django` was considered and rejected, or adopted.
- Sources: https://registry.npmjs.org/admin-lte , https://unpkg.com/admin-lte@4.1.0/CHANGELOG.md , https://pypi.org/project/adminlte-django/

### F3 — The django.tasks backend choice is not "invisible to task code". Django has no retries, and AD-3 needs "retry until acknowledged" (AD-8, AD-3, Deferred item, Stack row "django.tasks database backend") — HIGH

- Django 6.1 ships only `ImmediateBackend` and `DummyBackend`. Its docs say production needs a third-party backend with a worker and a durable queue. The core API documents **no retry mechanism**. The only related part is `TaskContext.attempt`. The docs also warn that you must enqueue through `transaction.on_commit()`, because otherwise the worker may run before the commit.
- Django 6.1 adds `@task(**kwargs)`, which is forwarded to the backend's `task_class`. Per-task retry policy (`max_attempts`, `backoff`, `timeout`) would therefore be backend-specific arguments in the task code. That breaks the claim in AD-8 that "task code never imports a backend" in practice, if not in the literal sense.
- The live candidates:
  - **django-tasks-db 0.13.0** (2026-08-28). This is the reference DB backend by the author of the Django Tasks DEP, split out of `django-tasks` at 0.12. Its worker is `manage.py db_worker`. Its classifiers stop at Django 6.0, and the release notes do not mention 6.1. No automatic retries are documented.
  - **django-ox 1.9.1** (2026-10-16). Declares Django 6.1 and is "Production/Stable". Uses `SKIP LOCKED` claims, a reaper for dead workers, per-task retries with backoff, recurring schedules and an `ox_worker` command. **Its first release was 2026-08-16, about 10 weeks ago, from a single vendor (Oxpull).** Maturity is the risk.
  - `django-tasks` 0.12.0 (the backport, last released 2026-02) is not needed on Django 6.1. `django-tasks-rq` needs Redis, which AD-8 rules out. `procrastinate` is PostgreSQL-native but has its own API, not a django.tasks backend.
- Change: (a) state in AD-3 that **outbox delivery retries come from the outbox row** (an `attempts`/`next_attempt_at` column, swept by a `runjob` timer or a re-enqueue). Retries must not depend on the backend's retry feature. That keeps AD-8's swap claim true. (b) Make enqueue-via-`on_commit` (or a backend that is transactional with the data) a rule in AD-8. (c) In the Stack row, name the shortlist (django-tasks-db 0.13.x vs django-ox 1.9.x), and add "confirm Django 6.1 in CI" as the inception gate.
- Sources: https://docs.djangoproject.com/en/6.1/ref/tasks/ , https://docs.djangoproject.com/en/6.1/topics/tasks/ , https://docs.djangoproject.com/en/6.1/releases/6.1/ , https://pypi.org/project/django-tasks-db/ , https://github.com/RealOrangeOne/django-tasks-db/releases , https://pypi.org/project/django-ox/

### F4 — ClamAV "Debian 13 package" is 1.4.3, behind upstream 1.4.6. Default limits silently skip files over 100 MB, such as STL files and records. (Stack row "ClamAV"; AD-9) — HIGH

- Debian trixie ships `clamav 1.4.3+dfsg-1`. Upstream 1.4 LTS is at **1.4.6**, with EOL on 2027-08-15 and signature downloads until 2028-08-15. ClamAV may block old patch versions from downloading signatures four months after a newer patch ships. Check `apt policy clamav` on the staging host. If trixie-updates or security does not carry a current 1.4.x, use the upstream .deb.
- `clamd.conf` defaults are `StreamMaxLength 100M`, `MaxFileSize 100M`, `MaxScanSize 400M` and `AlertExceedsMax no`. **A file over the limit is not scanned and is not flagged.** As AD-9 is written, an unscanned large file would move from quarantine to clean. Uppy defaults to multipart only above 100 MiB, so the multipart files are exactly the ones in this range.
- Change: AD-9 should state that an object over the scan limit is either rejected or held for review (set `AlertExceedsMax yes`, or have the scan task compare the size first). It should also state how the task feeds the object (clamd `INSTREAM` vs a temp file), and that clamd needs about 1 GB or more of RAM on each site. Change the Stack row to `ClamAV 1.4 LTS (≥ 1.4.6; Debian 13 package if current, else upstream .deb)`.
- Sources: https://docs.clamav.net/faq/faq-eol.html , https://tracker.debian.org/media/packages/c/clamav/control-1.4.3dfsg-1 , https://manpages.debian.org/trixie/clamav-daemon/clamd.conf.5.en.html , https://uppy.io/docs/aws-s3/

### F5 — Uppy is at 6.x, and the multipart plugin is now `@uppy/aws-s3` with one `signRequest` (Stack row "Uppy"; AD-9) — MEDIUM

- The npm `uppy` package is at 6.0.5, and `@uppy/aws-s3` and `@uppy/core` are at 6.3.0 (2026-10-16). The separate `@uppy/aws-s3-multipart` package no longer exists. Multipart is a mode of `@uppy/aws-s3`, set by `shouldUseMultipart` (default: files over 100 MiB). In 6.x the server side is **a single `signRequest` endpoint** that presigns PutObject, CreateMultipartUpload, UploadPart, ListParts, CompleteMultipartUpload and AbortMultipartUpload. Companion is not required. Older v3 and v4 examples with five separate callbacks are out of date.
- OVHcloud Object Storage supports PutBucketCors, presigned GET/PUT/HEAD/DELETE, all multipart operations including ListParts and Abort, and lifecycle configuration. **The bucket CORS rule must expose `ETag`** (Uppy states that multipart cannot complete otherwise) and must allow PUT/POST/DELETE from the exact portal origin.
- Change: Stack row `Uppy 6.x (@uppy/core + @uppy/aws-s3, signRequest mode, no Companion)`. Add to AD-9: per-region bucket CORS (origin pinned, `ExposeHeaders: ETag`), and a lifecycle rule that aborts incomplete multipart uploads and expires the quarantine prefix. OVH's compliance page lists lifecycle configuration but not the `AbortIncompleteMultipartUpload` action. Verify that on a real bucket, or have a sweep job call AbortMultipartUpload. The UAE provider check (Deferred) must test the same S3 operations, not only "S3-compatible".
- Sources: https://uppy.io/docs/aws-s3/ , https://registry.npmjs.org/@uppy/aws-s3 , https://docs.ovhcloud.com/en/guides/storage-and-backup/object-storage/s3-s3-compliancy

### F6 — Geo-IP database: GeoLite terms add a 30-day replace rule, attribution and a secret key. DB-IP Lite is simpler. (AD-14) — MEDIUM

- MaxMind GeoLite EULA (updated 2026-02-12): an account and license key are required. The key is a credential, so under the security rules it goes in settings.env, typed by a person. The license is for "internal business purposes". **Attribution is required**, and **old databases must be destroyed within 30 days of a new release**, which means a weekly `geoipupdate` timer on each site is mandatory, not optional. Using the data to locate individuals is prohibited, which a "suggest region" use does not do.
- DB-IP "IP to Country Lite": CC BY 4.0, updated monthly, **MMDB format**, no account. It needs attribution and a link to DB-IP.com on pages that use the result. It reads through the same `geoip2`/`maxminddb` reader (geoip2 5.3.0), so the choice can be swapped later.
- Change: AD-14 or the Stack should name the database (DB-IP Lite country MMDB is recommended: no key, no 30-day destroy rule), the reader (`geoip2` 5.3.x), the refresh job, and where the attribution goes (signup page footer).
- Sources: https://www.maxmind.com/en/geolite2/eula , https://db-ip.com/db/download/ip-to-country-lite , https://pypi.org/project/geoip2/

### F7 — Django 6.1 is not LTS: patch it now and plan for 6.2 LTS (Stack row "Django") — LOW/MEDIUM

- Django **6.1.2 (2026-10-06) is a security release** with three moderate CVEs and one low. One of them (CVE-2026-87975, model formsets with editable or UUID primary keys) is relevant to AD-5's new UUID-PK tables. requirements.txt and the Stack pin 6.1.1.
- 6.1 mainstream support ends April 2027 and extended support December 2027. **6.2 LTS ships April 2027**, with support to April 2030. The initiative will run past that.
- Change: Stack `Django 6.1.2 (move to 6.2 LTS after its release, before the UAE launch)`. Bump requirements.txt in the next story.
- Sources: https://docs.djangoproject.com/en/6.1/releases/6.1.2/ , https://www.djangoproject.com/download/

### F8 — Ed25519 tokens: PyJWT supports them but the spine names no library (AD-4, AD-10) — LOW

- PyJWT 2.15.1 (2026-09-28) supports `EdDSA` (Ed25519/Ed448) through `cryptography`, which is already pinned at 50.0.2, the latest. 2.15.0 hardened OKP JWK validation. PyJWT does not mention RFC 9864's fully specified `Ed25519` alg name. Use `alg: EdDSA` and pin `algorithms=["EdDSA"]` on decode.
- Change: add a Stack row `PyJWT 2.15.x (EdDSA) on cryptography 50.0.2` in mytp-core, or state that tokens are hand-built on `cryptography`'s `Ed25519PrivateKey`. Either way, name the choice so it is not left implicit.
- Sources: https://pyjwt.readthedocs.io/en/stable/algorithms.html , https://pyjwt.readthedocs.io/en/stable/changelog.html , https://pypi.org/project/PyJWT/

## Confirmed current (no change)

| Row | Spine | Checked against | Status |
| --- | --- | --- | --- |
| Python | 3.13 | Debian 13 system `python3.13` 3.13.5 (release.sh builds the venv from system python3); CI `python-version: '3.13'`; Django 6.1 supports 3.12–3.14 | Correct. 3.14 would need a non-system build, so stay on 3.13. Add to the UAE Deferred item that the provider must offer **Debian 13**, not only "a Debian VM", so the system Python matches. |
| PostgreSQL | 17 | CI `postgres:17`; upstream 17.11, EOL Nov 2029; Django 6.1 minimum is 15; OVH managed offers 14–18 | Current. Moving to 18 is optional. |
| psycopg | 3.3.6 | PyPI latest 3.3.6 | Current |
| cryptography | 50.0.2 | PyPI latest 50.0.2 | Current |
| Gunicorn | 26.2.0 | PyPI latest 26.2.0 | Current |
| Tailwind CSS | 4.3.3 | npm latest 4.3.3; package.json | Current |
| htmx | 2.0.11 | npm `latest` 2.0.11; `next` is 4.0.0 | Current. Keep the admin on 2.x, and do not pick up the `next` tag. |
| django-filter | "current release" | PyPI 26.2 (2026-10-03), declares Django 6.1, Python ≥ 3.11 | Fits. Pin **26.2**. |
| sentry-sdk | 2.71.0 | PyPI latest 2.73.0 (2026-10-22) | Minor drift only. Bump when convenient. |
| boto3 (implied by AD-9 presigning) | 1.43.90 in requirements | PyPI 1.43.122 | Fine. Not in the Stack, but add it, since AD-9 relies on it for presigned multipart. |

## Not verifiable from here

- Whether OVH Object Storage honours the lifecycle action `AbortIncompleteMultipartUpload` (F5). Test it on the staging bucket.
- Which ClamAV build trixie-updates or security carries today (F4). Run `apt policy clamav` on the staging host.
- Whether django-tasks-db runs on Django 6.1. No classifier or release note says so (F3). Check it with the project's own CI.
