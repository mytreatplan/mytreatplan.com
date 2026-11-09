---
title: 'Hub lookups'
type: 'feature'
ticket: '7'
created: '2026-11-09'
status: 'built'
baseline_revision: 'portal 4aae9a8, core 3f0974a, admin 3cac370'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** I10 (AD-3 channel 4, AD-21): regions will need live reads of admin-owned data (specialist availability and slot holds, epic-appointments), and nothing carries them yet; when the admin site is down such a read must fail cleanly without affecting anything else.

**Approach:** Three repos (mytp-core → admin → portal). **mytp-core:** a closed answer contract for a staging-only sample lookup and the lookup error codes. **Admin:** a signed `GET /api/v1/hub/sample-availability` (behind a staging-only setting and system check) that checks the caller's region and answers a small fixed sample; logged by the existing `InterfaceCall` middleware. **Portal:** `integration/hub.py` `lookup(path, reader)` that signs, bounds the wait, validates the answer and turns every failure into `HubUnavailable` or `HubRefused` (codes only, nothing cached); a `hub_lookup` command shows it on dev.

## Boundaries & Constraints

**Always:**
- Only what AD-3 allows: the sample stands in for availability; no catalogue, directory or other snapshot data over this channel.
- Admin endpoint: keyring as every `/api/` view, plus the issuer's region (as the event ingest's `region_mismatch`); answer `never_cache`; nothing of the caller stored beyond `InterfaceCall`.
- Portal: no cache of answers; `max_bytes` small (64 KB); "unavailable" = no answer, timeout, 5xx or 401 (as `outbox._unavailable`); other 4xx = refused; one log line (code only); no Sentry per failed lookup; the rest of dev unaffected (no exception escapes `lookup`).
- Staging-only setting on the admin site, refused on a public deploy by a new admin system check; removed when epic-appointments' real lookups exist.

- Decision (user, 2026-11-09): the sample is a fixed "sample availability" (a few made-up slots for the caller's region, the admin's time), behind a new admin setting `INTERFACE_SAMPLE_LOOKUPS` refused on a public deploy by a new admin system check.
- Decision (user, 2026-11-09): dev shows it through a `hub_lookup sample` command only (no page).
- Decision (user, 2026-11-09): the admin endpoint checks the keyring and the issuer's region; logging by `InterfaceCall` only.
- Decision (user, 2026-11-09): a setting `HUB_LOOKUP_TIMEOUT` = 3 s per socket operation plus a 10 s overall cap.
- Decision (user, 2026-11-09): no caller-side cache (hub data is live).
- Decision (user, 2026-11-09): built after 4.6 → mytp-core 0.17.0; three PRs (core → admin, deployed first → portal), kept whole.

**Never:** no change to `signed_get`'s defaults for other callers, the config pull, the outbox, the ping, or the snapshot; no slot holds (epic-appointments).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Lookup | `hub_lookup sample` on dev | the sample answer; one accepted `InterfaceCall` on admindev | — |
| Admin down | admindev stopped | `unavailable` within the timeout; dev's pages keep working | — |
| Region mismatch | a site signs for another region | 403 `region_mismatch`, logged refused on admindev | — |
| Setting off | sample setting off on admindev | 404 → `refused` on dev | — |
| Bad answer | invalid or oversized body | `unavailable` (`bad_answer`) | — |
| dev-ae | same from dev-ae | its own answer, logged with iss dev-ae | — |

</frozen-after-approval>

## Code Map

Paths relative to `.../code/`.
- Portal: `integration/management/commands/ping_peer.py` :28-35 (pattern), `integration/config.py` :121-140 (signed pull), `integration/outbox.py` `admin_site_url` :180, `_error_code` :215, `_unavailable` :226-229; `integration/checks.py` (E006; next free E007); settings `EXTERNAL_CHECK_TIMEOUT` :515.
- mytp-core: `interface.py` `_signed_call` :619-664, `signed_get` :667, `PeerCallFailed` :132, `MAX_RESPONSE_BYTES` :80; `contracts/errors.py`; `contracts/remote.py` (pattern for a closed answer).
- Admin: `interface/calls.py` `signed_endpoint` :78-106, `keyring` :54-58, `InterfaceCallMiddleware` :130; `interface/views.py` ping :22, config :40; `interface/events.py:16` region check; `interface/urls.py`; settings (new flag) and a new check; README.
- Architecture: AD-3 channel 4 (:109), AD-21 (:223), AD-4 (:115).

## Tasks & Acceptance

**Execution:**
- [x] mytp-core: sample answer model, error codes, schemas, tests, version, README.
- [x] Admin: setting + check, endpoint with region check, README, tests.
- [x] Portal: `integration/hub.py`, `hub_lookup` command, runbook, tests (every matrix row with a fake peer).
- [x] Both sites: vendor the new core.

**Acceptance Criteria:**
- Given admindev is stopped, when dev runs a lookup, then it reports `unavailable` within the cap and a normal dev page still answers 200.
- Given a lookup answer, when it is read, then nothing of it is stored on dev.

## Implementation Notes

- mytp-core 0.17.0: new `contracts.hub`: `SampleAvailability` (closed `WriterModel`: `lookup` = `sample_availability`, `region` `^[a-z]{2}$`, `generated_at` UTC "Z", `slots` ≤ 20 sorted by start) of `SampleSlot` (closed: `starts_at`, `ends_at` > start, `specialist` a made-up label `^[A-Za-z0-9][A-Za-z0-9 .-]*$` ≤ 64), `sample_availability(region, generated_at, slots)` (aware datetimes converted to UTC, naive refused), `LOOKUP_READERS`, `HUB_PREFIX` `/api/v1/hub/`, `SAMPLE_AVAILABILITY_PATH` `/api/v1/hub/sample-availability`, `MAX_LOOKUP_BYTES` 64 KB. `contracts.errors`: `INVALID_REQUEST` (400), `REGION_MISMATCH` (403), `NOT_FOUND` (404) in `HUB_LOOKUP_STATUS`/`HUB_LOOKUP_MESSAGES`/`hub_lookup_error(code)`; caller outcome names `HUB_UNAVAILABLE`, `HUB_REFUSED`, `BAD_ANSWER`. Schema file `hub_sample_availability.json`. `interface.py` untouched (no change to `signed_get`'s defaults). README: "Hub lookups" section, module list, schema list, compatibility rule (closed answer: admin adds a field only after the portals read it). Tests `tests/test_hub.py`.
- Admin: `INTERFACE_SAMPLE_LOOKUPS` (default False). New `interface/checks.py` with `interface.E001` (registered in `InterfaceConfig.ready()`): the setting on a public deploy is an error. New `interface/hub.py`: `GET /api/v1/hub/sample-availability` (`interface_hub_sample_availability`, `require_GET` + `signed_endpoint`, so keyring and `never_cache`). Order: signature → setting off → 404 `not_found` → `region` (exactly one value, `^[a-z]{2}$`, else 400 `invalid_request`) → `Region.objects.filter(site=iss).code` must equal it, else 403 `region_mismatch` → 200 with four made-up 30-minute slots (tomorrow 09:00 and 11:00 UTC, the day after 09:00 and 14:00), labelled `Sample specialist <REGION>-<n>`, `generated_at` the admin's now. Refusals mark the InterfaceCall row refused with their code (as `calls._outcome`), so the middleware logs its usual warning; the view logs nothing else and writes nothing. README "Hub lookups". Tests `interface/tests/test_hub.py`.
- Portal: `integration/hub.py` `lookup(path, reader)`: path must match `/api/v1/hub/<name>` (no query), else `HubRefused('bad_path')` with nothing sent; admin site from `outbox.admin_site_url()`; `?region=<settings.REGION>` added by `lookup` itself (signed, covered by `req`); `interface.signed_get(..., timeout=HUB_LOOKUP_TIMEOUT, max_bytes=MAX_LOOKUP_BYTES)` runs in a daemon thread joined for `OVERALL_CAP` = 10 s (urllib's timeout is per socket operation only); then `reader.model_validate` and the answer's `region` must be ours. Failures: `HubUnavailable` (`network`, `timeout`, `http_5xx…`, `http_401_<code>`, `bad_answer`, `not_configured`, `signing_key`, `error` for anything unexpected) or `HubRefused` (other 4xx as `http_<status>_<code>`, `bad_path`), both subclasses of `HubLookupFailed` with `.outcome` and `.code`. Codes use the outbox's `_error_code`; the split uses `outbox._unavailable`. A catch-all turns any other exception into `HubUnavailable('error')`, so nothing else leaves `lookup`. One log line per lookup on logger `integration.hub` (INFO answered / WARNING `<outcome> (<code>)`), never the answer, no Sentry call. Nothing is cached or stored (the answer test runs under `django_assert_num_queries(0)`). Setting `HUB_LOOKUP_TIMEOUT` (default 3, float) and the new check `integration.E007` (it must be a finite number with 0 < value ≤ 10). Command `hub_lookup sample` (prints sorted JSON; a failure is one `CommandError` line `hub_lookup sample: unavailable (network)`). Runbook `deploy/README.md` "Hub lookups". Tests `integration/tests/test_hub.py` (fake admin at `interface._open`, verifying the signature with an in-memory replay cache since the call runs in a thread).
- Vendoring: wheel `mytp_core-0.17.0-py3-none-any.whl` built from ../mytp-core's working tree (`pip wheel`, SOURCE_DATE_EPOCH of core HEAD; sha256 d728306e…3d81a92), replacing 0.16.0 in both `vendor/`, the `requirements.txt` lines updated, installed in `../.venv` and the admin venv (`uv pip install --python`). Version tests bumped in both `test_vendored_core.py` (plus a hub lookup test each), admin `layout/tests/test_deploy.py` wheel name, admin README wheel name. The release must rebuild it from the `v0.17.0` tag.
- Deviations / choices not spelled out in the plan:
  - How the caller names its region: the plan says the admin "checks the caller's region"; the portal sends it as `?region=<REGION>` (signed), and the admin compares it with the issuer's Region. A missing or malformed region is 400 `invalid_request` (a code added for that).
  - "Public deploy" on the admin site: it has no ROBOTS_NOINDEX switch. After review it fails closed: public unless SITE is in `interface.checks.STAGING_SITES` (`{'admindev'}`, also the default and test SITE), and public anyway when ALLOWED_HOSTS names `admin.mytpdso.com` (also the default) or `*`. `interface.E001` refuses the setting there, and the view answers 404 there at each request too.
  - Signature first, then the setting: with the setting off a signed call gets 404 `not_found`, while an unsigned one still gets 401.
  - Admin refusals (400/403/404) are logged refused in InterfaceCall with their code, which the matrix asks for `region_mismatch`; applied to all three for consistency.
  - Closed answer model (`WriterModel`, unknown fields refused), as "closed answer contract" says; this departs from the readers-ignore-unknown-fields rule, so the core README notes the deploy order for a future field.
  - Extra caller-side codes: `bad_path`, `not_configured`, `signing_key`, `timeout`, `error`. A 401 is reported with the peer's code (`http_401_signature`); a 2xx `PeerCallFailed` (too large / not JSON) is `bad_answer`.
  - New portal check `integration.E007` for `HUB_LOOKUP_TIMEOUT` (the Code Map pointed at the next free id).
  - The overall cap is a module constant (`hub.OVERALL_CAP`), not a setting; a lookup past it leaves its daemon thread to finish on its own, its result dropped (after review: bounded, see below).

- Review patches (2026-11-09), applied in all three checkouts:
  - Admin: request-time guard: the sample answers 404 `not_found` when the setting is off or `checks.public_deploy()` (E001 runs only at `check`/release; a restart could switch it on). `public_deploy()` fails closed (`STAGING_SITES = {'admindev'}` plus the admin-host and `*` rules); E001's text, its tests, the settings comment and README updated. Tests: `SITE='admin'` (and the host rules) with the setting on → 404 (signed for that SITE as audience); setting off plus a missing, malformed or other region → 404, not 400 (order pinned); "off by default" now checks the code (the `config(..., default=False, cast=bool)` line in settings.py, and settings imported afresh in a subprocess without the variable, skipped only when a local settings.env exists). The test fixture pins `ALLOWED_HOSTS` to test hosts so the environment cannot make the deploy public.
  - Core: README compatibility rule for closed lookup answers: a new field is added as optional first (core release), the portals deployed, then the admin site sends it; removing or narrowing a field: first a core release that accepts both shapes on the portals, then the admin site changes, and a tightened model reaches the portals last. Wheel rebuilt and re-vendored (the README is the wheel's long description; sha256 438ab7c8…f1395a1826).
  - Portal `integration/hub.py`: module-level `_slots = threading.BoundedSemaphore(MAX_RUNNING=4)`, acquired without blocking before the thread starts and released in the thread's `finally` (or at once if the thread cannot start); none free → `HubUnavailable('busy')`, no thread, no call. Docstring corrected: a slow-drip answer keeps the thread past its socket timeout (up to the 64 KB read); a hung peer sending nothing is `network` after HUB_LOOKUP_TIMEOUT; the first lookup in a worker may unwrap the signing key through the KMS inside the thread, so a hung KMS shows as `timeout` (the call was not moved).
  - Portal tests: the stores-nothing test now patches `BaseDatabaseWrapper.ensure_connection` and `CursorWrapper.execute`/`executemany` at class level (every thread) to record and fail; the decorative `client.get('/en/')` 200 asserts and the always-true elapsed assert are dropped (the "admin down" test is renamed); the cap test uses a 0.1 s cap with a 0.4 s fake delay and joins leftover `hub-lookup` threads at the end; new tests for `busy`, every outcome giving its slot back, and a thread that cannot start giving its slot back.
  - Runbook: outcome rows for `busy`, `peer_url` and `error`; a hung admin shows as `unavailable (network)` after HUB_LOOKUP_TIMEOUT, a slow drip as `timeout`; the KMS-in-thread note; the command's failure output is the warning log line, then one error line, exit 1; a non-numeric HUB_LOOKUP_TIMEOUT fails at settings import.

## Verification results

After the review patches (2026-11-09, sandbox off, wheel rebuilt and re-installed in both venvs, fresh PostgreSQL 17.11 cluster under `$TMPDIR`, since stopped and deleted):
- mytp-core: 1222 passed (SQLite); 1222 passed (PostgreSQL 17). `export_contract_schemas --check`: up to date.
- Portal, SQLite: 2304 passed, 8 skipped, 3 failed (the 3 known PostgreSQL-only failures in `accounts/tests/test_crypto.py`). Portal, PostgreSQL 17: 2315 passed, 0 skipped.
- Admin, SQLite: 1051 passed, 8 skipped. Admin, PostgreSQL 17: 1059 passed.
- `manage.py check` and `makemigrations --check`: clean on both sites.
- Not done: the manual staging checks.

First run, before the review patches:

Run 2026-11-09, sandbox off, wheel `mytp_core-0.17.0-py3-none-any.whl` built from ../mytp-core's working tree and installed in both venvs; throwaway PostgreSQL 17.11 cluster under `$TMPDIR` (since stopped and deleted).
- mytp-core: 1222 passed (SQLite); 1222 passed (PostgreSQL 17). `export_contract_schemas --check`: up to date.
- Portal, SQLite: 2301 passed, 8 skipped, 3 failed (the 3 known PostgreSQL-only failures in `accounts/tests/test_crypto.py`). Portal, PostgreSQL 17: 2312 passed, 0 skipped.
- Admin, SQLite: 1039 passed, 8 skipped. Admin, PostgreSQL 17: 1047 passed.
- `manage.py check` and `makemigrations --check`: clean on both sites (no migration in this story).
- Not done: the manual staging checks (they need the deploys and admindev's setting).

## Plan Change Log

## Review Triage Log

Review (2026-11-09), all applied as patches (see Implementation Notes, "Review patches"):
- patch (admin, medium): no request-time guard, so a production host could serve the sample after a restart → 404 when the deploy is public, at each request.
- patch (admin): the public-deploy test failed open → public unless SITE is in `STAGING_SITES`, plus the host rules; check order (setting before region) pinned by a test; the "off by default" test checks the code default.
- patch (core): compatibility rule for adding, removing and narrowing fields of a closed lookup answer.
- patch (portal): leftover threads were unbounded → `BoundedSemaphore(4)`, `busy`; docstring on slow drips and the KMS unwrap in the thread; the no-database check covers every thread; decorative asserts removed; the cap test joins its leftover thread; runbook rows and notes.

## Verification

**Commands:**
- mytp-core, admin and portal suites — all pass; CI on PostgreSQL.

**Manual checks (staging):**
- `INTERFACE_SAMPLE_LOOKUPS=True` on admindev; `hub_lookup sample` on dev and dev-ae → answers, accepted `InterfaceCall` rows on admindev with each issuer; admindev stopped → `unavailable` in ≤ 10 s and dev's home page 200; setting off → refused.
