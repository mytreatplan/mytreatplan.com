---
title: 'Geo-IP country database'
type: 'feature'
ticket: '4'
created: '2026-11-02'
status: 'built'
baseline_revision: '76d29c7454ab423790809811f0b49a410a21dff6'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** R4 / AD-14: the region suggestion bar (entry 5) needs the visitor's country, looked up locally so that no IP leaves the instance. The portal has no geo-IP data or lookup.

**Approach:** Each site keeps the DB-IP Lite country database (MMDB, CC BY 4.0) in its shared folder. A monthly job downloads it, validates it and swaps it atomically. `regions.geoip.request_country(request)` returns the visitor's ISO-2 country, or None. A staging-only override header lets the checks fake a country.

## Boundaries & Constraints

**Always:**
- **Library:** `maxminddb` only (pinned `==` in requirements.txt). Read `['country']['iso_code']`, uppercased.
- **Path:** the `GEOIP_DB_PATH` setting, defaulting to `SITE_HOME/shared/geoip/dbip-country-lite.mmdb`. provision.sh creates `shared/geoip`, owned by the site user, with `ensure_dir`.
- **Job:** `regions/management/commands/update_geoip.py`, scheduled in `deploy/jobs.conf` as `geoipupdate | update_geoip | *-*-03 04:45:00 UTC`.
  - It downloads `https://download.db-ip.com/free/dbip-country-lite-YYYY-MM.mmdb.gz` for the current UTC month. On a 404 it tries the previous month.
  - It uses a binary fetch with `EXTERNAL_CHECK_USER_AGENT`, a 60 s timeout, and `accounts.http.FETCH_ERRORS` handling.
  - It writes the file to a temp file in the same folder, then opens it with maxminddb. It must be a DB-IP country database, and it must answer the known lookup `8.8.8.8 → US`, and `2001:4860:4860::8888` must map to some country (any ISO-2 code; decision, user 2026-11-02, because DB-IP maps that block to CA). Only then does `os.replace` swap it in.
  - Any failure (network, gzip, a bad file, a failed lookup) deletes the temp file, keeps the current database, logs an error that reaches Sentry, and exits non-zero.
- **Lookup:**
  - `request_country(request)` uses `eventlog.service.client_ip(request)` (unchanged).
  - It returns None for a missing database, an unknown address, or a private, loopback or reserved address.
  - The reader is cached per process and reopened when the file's mtime or inode changes, so gunicorn picks up the swap without a restart.
- **Override:**
  - When `GEOIP_OVERRIDE_HEADER_ENABLED=True` (default False), a request header `X-MyTP-Geo-Country: SA` with a valid ISO-2 code wins over the lookup.
  - A system check errors when the override is enabled while `ROBOTS_NOINDEX` is False, which means on a public site. Staging sets both.
- **First install:** the lookup returns None until the first job run. The README server step runs the job once after provisioning (`run-job.sh geoipupdate` or `manage.py runjob geoipupdate`).
- **Tests:** they build a tiny MMDB at test time with `mmdb-writer` (dev requirement only) holding an ES range, an SA range and a US range for the validation lookups. No binary file is committed.
- No IP and no country is logged with a person. The job logs only the month, the size and the result.

**Never:**
- No suggestion bar, cookie or attribution text (entry 5). No change to `lookup_country`, the jobs.conf grammar or mytp-core.
- No IP sent to any service. The download is the only outbound call, and it carries no visitor data.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Spanish IP | client IP in the fixture's ES range | `request_country` → "ES" | — |
| Saudi IP | in the SA range | "SA" | — |
| Private or loopback | 10.0.0.1, 127.0.0.1, ::1 | None | — |
| No database | the path is missing | None | no exception |
| Override on | setting True, header `X-MyTP-Geo-Country: sa` | "SA" | an invalid value is ignored, so the lookup is used |
| Override off | setting False, header set | the lookup result | — |
| Job ok | a valid gz for this month | swapped; the old file replaced atomically | — |
| Month not out yet | this month 404, previous ok | the previous month installed | — |
| Corrupt download | bad gzip or a non-MMDB | previous database kept; temp file removed; non-zero exit | Sentry error |
| Wrong data | a valid MMDB failing the known lookups | previous database kept | Sentry error |
| Swap seen | the file replaced while a reader is cached | the next lookup uses the new file | — |
| Public with override | override True, ROBOTS_NOINDEX False | `check` error | — |

</frozen-after-approval>

## Code Map

Portal (`mytreatplan.com/`):
- `deploy/jobs.conf` -- one line per job, `name | command args | OnCalendar UTC`. mytp-core's `oncalendar_to_crontab` accepts a day of month of 1–28. After a jobs.conf change, re-provision (`provision.sh:353`; `release.sh:172-176` only warns).
- `deploy/systemd/job@.service.template` (TimeoutStartSec 60min, ProtectSystem=full, UMask 0027), `job.timer.template` (Persistent=true), `deploy/run-job.sh` → `manage.py runjob <name>`.
- `deploy/provision.sh:218-221` -- the `ensure_dir` calls; add `shared/geoip`.
- `mytreatplan/settings.py` -- `SITE_HOME` :91. The pattern is `BACKUP_TMP_DIR` :243. `ROBOTS_NOINDEX` :102 is the bool toggle model. `EXTERNAL_CHECK_*` :451-452.
- `deploy/staging.env.example:44`, `settings.env.example` -- add `GEOIP_OVERRIDE_HEADER_ENABLED`.
- `eventlog/service.py:34` `client_ip(request)` -- reuse; the last X-Forwarded-For entry (nginx appends it), else REMOTE_ADDR.
- `accounts/http.py` -- `FETCH_ERRORS` :18. `get_text` decodes text, so add a small binary fetch. Keep this behaviour.
- `regions/` -- `countries.py` (`lookup_country` :94, unchanged), `models.py`, `profiles.py`. The new modules are `regions/geoip.py`, `regions/checks.py` (or an addition to the existing checks) and `regions/management/commands/update_geoip.py`. `regions/tests/test_guard.py` forbids `'eu'`/`'ae'` literals outside regions/migrations/tests.
- Example command: `signup/management/commands/purge_pending_signups.py`.
- `requirements.txt` (`maxminddb==<current>`), `requirements-dev.txt` (`mmdb-writer==<current>`).
- `deploy/README.md` -- the jobs section and a short "Geo-IP database" step: re-provision, then run the job once, then check.
- The Sentry monitor `mytp-<site>-geoipupdate` stays disabled (the Developer-plan gap, epic-platform Notes).

## Tasks & Acceptance

**Execution:**
- [x] `regions/geoip.py` (lookup, reader cache with reopen, override), the check, the settings and env examples
- [x] `update_geoip` command, the jobs.conf line, the provision directory, the README
- [x] tests for every matrix row (fixture MMDB built in tests; the download stubbed); requirements

**Acceptance Criteria:**
- Given dev after deploy and re-provision (user), when the user runs the job once, then `shared/geoip/dbip-country-lite.mmdb` exists, and in the shell `request_country` with an override `SA` gives SA, and a Spanish IP gives ES.
- Given the next run with the network blocked, or a corrupt file, then the previous file is unchanged.

## Verification

**Commands:**
- the suite command -- all pass except the 3 known PostgreSQL-only failures; `check`; `makemigrations --check`

**Manual checks (if no CLI):**
- dev (user): re-provision (timer installed), run the job once, check in the shell.

## Implementation Notes

- **IPv6 known lookup (user decision, 2026-11-02).** The real DB-IP Lite files (2026-11, 2026-10; `database_type` "DBIP-Country-Lite") map `2001:4860:4860::8888` to CA, so `KNOWN_LOOKUPS` keeps `8.8.8.8 → US` strict and only requires the IPv6 address to map to some ISO-2 code (`None` in the tuple). Tests: a CA mapping installs; no mapping keeps the previous database. A live run installed the real 2026-11 file. The same file confirms `88.0.0.1 → ES` and `2.88.0.1 → SA` (the README shell check).
- A failure logs one ERROR (journal and Sentry) and exits with `SystemExit(1)`, not `CommandError`: runjob calls `capture_exception` on an exception, which would give each failure a second Sentry issue. It still marks the check-in and the unit as failed.
- The lookup also treats link-local, multicast, unspecified and any other non-global address as "reserved", and reads IPv4-mapped IPv6 as IPv4. A database that cannot be read is logged once (without the IP) and is not retried until the file changes.
- The job refuses downloads over 100 MB and databases over 200 MB (the real file is about 4 MB gzipped and 8.5 MB unpacked), and creates the folder when it is missing (local runs).
- `netaddr==1.3.0` is pinned in requirements-dev.txt because mmdb-writer depends on it.
- Locally, the macOS python.org build has no CA bundle, so the real download needs `SSL_CERT_FILE=/etc/ssl/cert.pem`. The server's Ubuntu Python uses the system store.

## Plan Change Log

## Review Triage Log

### Pass 1 (quick, 2026-11-02): high 0, medium 0, low 3, false 0, maybe-false 0

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | The 100 MB download limit was checked after the whole body was read into memory | low | patch | Patched: `get_bytes(max_bytes=)` reads at most limit+1 and raises `ResponseTooLarge` (a FETCH_ERROR); tests prove no unbounded read. |
| 2 | README said to leave `mytp-<site>-geoipupdate` disabled, but the first run creates it enabled (Developer plan: one cron monitor) | low | patch | Patched: install step 3 disables it in Sentry → Crons after the first run. |
| 3 | `accounts/http.py` docstring claimed one timeout for every helper; over-long line | low | patch | Patched and rewrapped. |

Before review the user decided (frozen block updated): `2001:4860:4860::8888` must map to some country, because the real DB-IP files map it to CA. A live run installed the real 2026-11 file (8.1 MB). After the patches: full portal suite all pass except the 3 known PostgreSQL-only failures; `makemigrations --check` clean.
