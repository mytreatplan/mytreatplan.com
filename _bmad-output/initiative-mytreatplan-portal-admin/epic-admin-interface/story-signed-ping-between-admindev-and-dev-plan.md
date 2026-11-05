---
title: 'Signed ping between admindev and dev'
type: 'feature'
ticket: '1'
created: '2026-11-05'
status: 'built'
baseline_revision: '29de64e657563b2ebdbab812e0b7d696eabdafb1'
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

**Problem:** I1/I2 (AD-4): the admin site and the regional portals cannot call each other; every later interface (outbox, snapshots, remote reads) needs authenticated calls both ways.

**Approach:** The smallest signed round trip, in three repos. mytp-core 0.6.0 adds Ed25519 request tokens (PyJWT EdDSA), a keyring of peers' public keys by kid, a jti replay cache, a signing-key generator wrapped by the site's KMS, and a signed HTTP client. The portal gets an `integration` app and the admin site an `interface` app, each serving `GET /api/v1/ping` (signed, answers `{"site": SITE, "region": …, "time": …}`), logging every call it receives, accepted or refused, and a `ping_peer <site>` command that calls the other side.

## Boundaries & Constraints

**Always:**
- Token (mytp-core `mytp_core.interface`): JWT EdDSA, header `kid`; claims `iss` (caller's SITE), `aud` (receiver's SITE), `iat`, `exp` = iat + 60 s at most, `jti` (uuid4), `req` = SHA-256 hex of `METHOD\nPATH?QUERY\nBODY`. Sent as `Authorization: MyTP-Signature <jwt>`. Verify: known `iss` and `kid` in the keyring, signature, `aud` == own SITE, `exp` not passed and ≤ iat + 60, `iat` ≤ now + 30 s skew, `req` matches the received request, `jti` unseen (`cache.add(f'mytp:jti:{iss}:{jti}', 1, timeout)` — atomic on the DB cache). Any failure → 401 with `{"error": {"code": "<reason>", "message": …}}` and nothing else revealed.
- Signing key: the 32-byte Ed25519 seed is a KMS data key (`kms.KMSClient.generate_data_key`), so no new KMS call. Setting `INTERFACE_SIGNING_KMS_KEY = "<kid>:<wrapped>"`, unwrapped once per process like `KMSKeyProvider`; tests and local dev use a plain `INTERFACE_SIGNING_KEY` (base64 seed) the way field keys do. Command `generate_interface_signing_key` prints the wrapped value and the public key (base64) with its kid, never the seed. `kmscheck` also unwraps it.
- Peers: portal settings `INTERFACE_PEERS = "<site>=<base URL>"` and `INTERFACE_PEER_KEYS = "<site>:<kid>:<public base64>,…"` (several kids per site for rotation). Admin: base URL from `Region.instance_url`, the site name on a new `Region.site` field, and public keys in a new `PeerKey(region, kid, public_key, created_at, retired_at)` model filled by a command `add_peer_key <region> <kid> <public>` (and `retire_peer_key <region> <kid>`).
- Views: `csrf_exempt`, `login_not_required` on the admin, outside `i18n_patterns` on the portal; GET only. Nginx: an `location /api/` block without basic auth (as `/healthz`), body ≤ 1 MB; the production IP allowlist belongs to epic-eu-golive.
- Call log: a new model on each side (`InterfaceCall`: received_at, iss, kid, method, path, accepted, reason, status, ip via `client_ip`), written for every request to `/api/`; refused calls also log a warning line (reason, no token).
- Decision (user, 2026-11-05): call logs are kept 90 days, deleted by a daily job (`purge_interface_calls` in each site's `deploy/jobs.conf`).
- Decision (user, 2026-11-05): every call, refused ones included, is written to the database (no per-IP cap); staging relies on the audience claim, production gets the IP allowlist (epic-eu-golive).
- Decision (user, 2026-11-05): the admin's peer public keys are added and retired by commands over SSH (`add_peer_key`, `retire_peer_key`); no UI in this story.
- Decision (user, 2026-11-05): one plan over three repos (plan above the size target accepted); built in order mytp-core (tag v0.6.0) → portal → admin, three PRs.
- Outbound: urllib with `EXTERNAL_CHECK_TIMEOUT`, as `accounts/http.py`; `ping_peer` prints the answer or the error and exits non-zero on failure.
- mytp-core release: version 0.6.0 (pyproject + `__init__`), PyJWT pinned, tag `v0.6.0`, wheel vendored into both sites (`scripts/vendor-mytp-core.sh`).

**Never:**
- No HMAC/shared secrets, no private key in the repo, a log, or an error. No other endpoints (contracts are entry 2). No UI for keys.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Ping admin → dev | valid token | 200 JSON; call logged accepted | — |
| Ping dev → admin | valid token | 200 JSON; logged | — |
| Replay | same token twice | second → 401 `replayed` | logged refused |
| Expired / too long | exp passed, or exp > iat+60 | 401 `expired` | logged |
| Wrong audience | aud = dev-ae on dev | 401 `audience` | logged |
| Tampered | path/query/body changed | 401 `request_hash` | logged |
| Unknown iss or kid | — | 401 `unknown_key` | logged |
| Bad signature / garbage / no header | — | 401 `signature` / `missing` | logged |
| Rotation | caller signs with the new kid, receiver has old + new | 200 | — |
| Retired kid | key retired on admin | 401 `unknown_key` | logged |
| Peer down | ping_peer, no answer | exit non-zero, message | no traceback |

</frozen-after-approval>

## Code Map

Repos and branches (all on `story/4.1-signed-ping`; do not push, tag or open PRs — the coordinator does that):
- mytp-core: `/Users/jadeandres/Library/CloudStorage/GoogleDrive-mytpdso@gmail.com/Mi unidad/Desarrollo Portal MyTreatplan/code/mytp-core` (baseline 20d4a6737e824e7eacc6a30df14ae891c99b7ca2).
- Portal: `.../code/mytreatplan.com` (this plan's repo; baseline in frontmatter).
- Admin: `.../code/mytp-admin` (baseline cdcc6b74114a778b40a2be69dbb371ffaf4cbb7e).
- Until the v0.6.0 tag exists, build the mytp-core wheel locally and install it into each site's test venv to run the sites' tests; leave `vendor/` and the requirements line pointing at 0.6.0 so that, after the tag, `scripts/vendor-mytp-core.sh v0.6.0` reproduces the same wheel. Note in Implementation Notes exactly what the coordinator must run.
- Test commands: portal — the local dev notes (`../.venv`, keys, SQLite, sandbox off); admin — `/private/tmp/claude-501/admin-venv/bin/python -m pytest -q -p no:cacheprovider` with `SECRET_KEY=ci-only-not-a-secret DEBUG=False SECURE_SSL_REDIRECT=False ALLOWED_HOSTS=localhost,127.0.0.1,testserver DATABASE_URL=sqlite:///$TMPDIR/adm.sqlite3 FIELD_ENCRYPTION_KEYS=test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE= BLIND_INDEX_KEY=dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU= ALLOW_FAKE_VERIFICATION=True`; mytp-core — its README.

mytp-core (`../mytp-core`):
- `mytp_core/kms.py` -- `KMSClient.from_settings` :82, `generate_data_key` :98, `decrypt_data_key` :107, `KMSError` :49.
- `mytp_core/crypto.py` -- `unwrap_data_key` :155 (32 bytes, `KeyUnavailableError`), `parse_wrapped_key_list` :107, `KMSKeyProvider` :234-306 (once per process, lock): the pattern for the signing key provider.
- `mytp_core/management/commands/generate_kms_field_key.py` (prints only the wrapped key), `kmscheck.py` -- precedents.
- `mytp_core/cache_mutex.py:22` -- `cache.add` is atomic on the DB cache.
- `pyproject.toml:7,12-16` (version, deps), `mytp_core/__init__.py` (version), `README.md:228-233` release steps, CI checks tag = version. Tests: `tests/settings.py` (LocMem cache, empty KMS settings), `tests/test_kms_provider.py:45-83` `FakeKMSClient` / `fake_kms`.

Portal (`mytreatplan.com`):
- `mytreatplan/settings.py` -- `config(...)` :21-29, `SITE` :82-91, `REGION_PROFILE` :44-50, caches :180-195 (DB cache), KMS settings :264-307, INSTALLED_APPS :311-335, `EXTERNAL_CHECK_*` :451-452, `MYTP_SENTRY_IGNORED_LOGGERS` :211, LOGGING :576-602.
- `mytreatplan/urls.py:28-40` -- root patterns outside i18n (healthz precedent).
- `accounts/http.py` -- `FETCH_ERRORS` :18, `_fetch_bytes` :25-35: the outbound pattern (add a header-capable GET).
- `eventlog/service.py:34` `client_ip`.
- `deploy/nginx/site.conf.template:46-48` (basic auth), `:63-71` `/healthz` block; `settings.env.example`, `deploy/staging.env.example`.
- Tests: `conftest.py:80-101` `no_network` (monkeypatch the fetch), `pytest.ini` testpaths (add `integration`), `requirements.txt:7,33` (cryptography, vendored wheel).

Admin (`mytp-admin`):
- `staff/models.py:55-69` `Region(code pk, name, instance_url)`; `staff/migrations/0002_seed_regions.py`; settings `REGION_INSTANCE_URL_EU/AE` :71-72.
- `mytp_admin/urls.py` (docstring :1-5 announces /api/v1/; `login_not_required(healthz)` :35), `LoginRequiredMiddleware` :200, CSRF :193.
- `audit/registry.py:53-70` only walks CHANGE views (an API view is outside it); `permissions/tests/test_matrix.py:339`, `layout/tests/test_pages.py:50` (noindex on every response) may need the API exempted or not.
- Settings KMS :51-66, `EXTERNAL_CHECK_TIMEOUT` :122, LOGGING :345-360, Sentry :88-91; `deploy/jobs.conf`.
- Tests: `conftest.py:12-56` (settings set in `pytest_configure`), `clear_cache` :59.

## Tasks & Acceptance

**Execution:**
- [x] mytp-core: `mytp_core/interface.py` (sign, verify, keyring, signing key provider, signed GET), `management/commands/generate_interface_signing_key.py`, `kmscheck` addition, tests, version 0.6.0, PyJWT dependency, README section.
- [x] Portal: `integration/` app (model + migration, ping view, middleware or decorator that logs every /api/ call, `ping_peer` command, purge), settings + examples, urls, nginx `/api/` block, tests; vendored wheel + requirements.
- [x] Admin: `interface/` app (same shape), `Region.site` + `PeerKey` + migrations, `add_peer_key`/`retire_peer_key`, settings, urls, tests; vendored wheel + requirements.
- [x] Runbook in each README: generate the key on the server, exchange public keys, `ping_peer`.

**Acceptance Criteria:**
- Given both sites configured with each other's public key, when `ping_peer` runs on either, then it prints the peer's answer and both sides' logs show the call.
- Given a wrapped signing key and the KMS, when a site starts, then the seed is unwrapped once and never logged; without a key, `/api/` answers 503 and `ping_peer` fails cleanly.

## Implementation Notes

Built 2026-11-05 on `story/4.1-signed-ping` in all three repos; nothing committed, tagged or pushed.

**Choices where the plan left room**
- `INTERFACE_SIGNING_KEY` is `<kid>:<base64 seed>` (the header needs a kid; same shape as `FIELD_ENCRYPTION_KEYS`). Setting both it and `INTERFACE_SIGNING_KMS_KEY` is a configuration error.
- 503 `unavailable`: no well-formed signing key setting, or a malformed `INTERFACE_PEER_KEYS`. The receiver only parses the setting (no KMS call). The KMS unwrap happens on the first signing (`ping_peer`), once per process, and a failure is not kept (like `KMSKeyProvider`). An empty keyring is not a 503: it answers 401 `unknown_key`, so retiring a region's last key matches the matrix.
- Extra log reasons: `unavailable` (503) and `not_checked` (a /api/ request that never reached a signed view: 404, 405).
- Token checks: `iat` more than 30 s ahead gives `expired`; malformed claims (wrong types, bad jti) give `signature`; `jti` is checked last, so a refused token does not use up its jti. `verify_request` returns `Claims` (a dict with `.kid`).
- `generate_interface_signing_key` prints `INTERFACE_SIGNING_KMS_KEY=<kid>:<wrapped>` and `INTERFACE_PUBLIC_KEY=<SITE>:<kid>:<public>`. Its extra `--public` flag prints the configured key's public key, which is also how local dev gets the public key of a plain seed.
- Admin `Region.site` (blank by default, unique when set) is set by `add_peer_key <region> <kid> <public> --site <site>`. `--site` is required while the region has none, and a different one is refused. `add_peer_key` and `retire_peer_key` go through `interface.service` with audit entries (CREATE PeerKey, UPDATE Region.site, DEACTIVATE PeerKey; command actor), following the admin rule that every write is audited. A kid is never reused, retired or not.
- `iss` and `kid` are stored and logged only when they look like identifiers (`[\w.:-]{1,64}`), else `?`. The IP is stored as text. The admin has its own `client_ip` (same rule as the portal's `eventlog.service.client_ip`).
- Jobs: `purgeinterfacecalls` runs daily, at 04:20 UTC on the portal and 04:25 UTC on the admin. The portal's middleware sits after `PageRecordMiddleware`; the admin's sits after `NoindexMiddleware`. Only requests are signed, not answers.
- PyJWT 2.15.1 (released 2026-09-28): `PyJWT>=2.15.1,<3` in mytp-core's pyproject, `PyJWT==2.15.1` pinned in both sites' requirements.txt and in mytp-core's requirements-test.txt.

**Verification done**
- mytp-core: 509 passed (SQLite), including 71 new tests in `tests/test_interface.py`.
- Portal: 1792 passed. The only failures are the 3 known PostgreSQL-only ones. The local Spanish `.mo` was stale, so I ran `compilemessages -l es` (the file is git-ignored).
- Admin: 715 passed. `makemigrations --check` is clean in both sites.
- End to end on localhost: both sites under runserver with fixed test seeds, real HTTP. `ping_peer admindev` on dev and `ping_peer dev` on admindev both printed the peer's answer and exited 0, and both call logs showed the accepted call. The same token sent twice gave 200 then 401 `replayed`, logged with the warning line, and the token never appeared in either server log. No header gave 401 `missing`. A peer that was down gave exit 1 with one line and no traceback.

**What the coordinator must run (order: mytp-core, then portal, then admin)**
1. mytp-core: commit, then fast-forward master and tag `v0.6.0` locally (README release steps). The user runs `git -C ../mytp-core push origin master v0.6.0` and waits for CI to confirm that the tag equals the version.
2. In each site, run `MYTP_CORE_REPO=../mytp-core scripts/vendor-mytp-core.sh v0.6.0` and commit `vendor/` and `requirements.txt`. The `vendor/mytp_core-0.6.0-py3-none-any.whl` there now is a pre-tag local build (SOURCE_DATE_EPOCH of the baseline commit), so it will differ in bytes from the tag's build; the script must be run again. The requirements line already reads `./vendor/mytp_core-0.6.0-py3-none-any.whl` and the 0.5.0 wheel is `git rm`'d.
3. After each site's merge deploy is green, re-provision dev, dev-ae and admindev (Nginx `/api/` block and the new job). admindev needs the portal's new `deploy/` with the admin's `jobs.conf`. Then disable the new Sentry monitors after their first run, and follow the runbooks (portal `deploy/README.md` "Interface with the admin site"; admin README "Interface with the portals").

**Review fixes (2026-11-05)**
- Outbound:
  - `signed_get` follows no redirect; a 3xx fails as `HTTP <code>`.
  - It refuses `http://` unless DEBUG is on.
- Claim and setting checks:
  - `req` must be 64 lowercase hex characters, otherwise `request_hash`.
  - `jti` must be a canonical uuid4.
  - Site and kid checks use `fullmatch`.
- Both sides hash the same canonical target (`request_target`: unquote, then `escape_uri_path`). The receiver passes `request.path` as already decoded.
- The signing-key change marker is a sha256 of the setting, not the seed.
- `kmscheck` checks the signing key in its own step, `signing-key`, which also fails when both signing settings are set.
- Sites:
  - A 503 is logged once as a warning and marked `_has_been_logged`, so Django no longer logs it as an error.
  - Unexpected errors are logged with reason `error`.
  - `ping_peer` prints only the CommandError on failure.
- Portal: the system checks `integration.E001` (signing key), `E002` (`INTERFACE_PEERS`) and `E003` (`INTERFACE_PEER_KEYS`).
- Admin: `add_peer_key` refuses a public key already stored for another region, and refuses `--site` equal to the admin's own SITE. The `kmscheck` comment in `jobs.conf` is updated.

**Risks / notes**
- Until a site has its signing key, every request to `/api/` (scanners included) gets a 503. It is now logged as a single warning, so it no longer creates Sentry events.
- Every refused call is a database row (decision: no per-IP cap). Staging relies on the audience claim; production's IP allowlist is in epic-eu-golive.
- Observed, not changed: the admin's `pytest.ini` testpaths omit `inbox` (its 29 tests pass when run directly).

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, opus, one reviewer per repo, security focus, 2026-11-05): 18 entries after merging the same finding from different reviewers. high 1, medium 4, low 10, false 3.

| # | Repo | Finding | Verdict | Route | Evidence / action |
|---|------|---------|---------|-------|-------------------|
| 1 | core + portal | `signed_get` follows redirects and resends `Authorization` to the new host (a live, unused token) | high | patch | Checked against CPython `redirect_request`. Redirects refused; a 3xx is PeerCallFailed. |
| 2 | portal + admin | `http://` peer URLs accepted on servers (token in cleartext, then redirected to https and accepted) | medium | patch | http only with DEBUG. |
| 3 | portal + admin | Each 503 `unavailable` before keys are set is a Sentry error (django.request logs 5xx at ERROR); `/api/` is open to scanners | medium | patch | Logged once at WARNING, response marked logged. |
| 4 | admin | `add_peer_key` accepts the same public key under two regions → one site's key signs as another iss | medium | patch | Refused across regions. |
| 5 | portal | Malformed `INTERFACE_PEERS` / `INTERFACE_PEER_KEYS` pass `manage.py check`, then every call is 503 | medium | patch | System check added. |
| 6 | core | `compare_digest` on a non-ASCII `req` → TypeError → 500 | low | patch | `req` must be 64 hex chars. |
| 7 | core | `re.match(...$)` accepts a trailing newline (jti, site) | low | patch | fullmatch; jti a uuid4. |
| 8 | core | Path canonical form differs between signer (raw) and Django (`get_full_path`) | low | patch | One shared canonicaliser; test with %7E/%2F. |
| 9 | core | Local-dev seed kept in a module global | low | patch | Digest as the change marker. |
| 10 | core | `kmscheck` checks the signing key inside the field-keys step | low | patch | Own step; both settings set reported. |
| 11 | admin | `add_peer_key --site` may be the admin's own SITE | low | patch | Refused. |
| 12 | admin | Stale `kmscheck` comment in jobs.conf | low | patch | Updated. |
| 13 | portal + admin | Other exceptions during verification logged as `not_checked` | low | patch | Reason `error`. |
| 14 | portal + admin | `ping_peer` prints two lines on failure | low | patch | One line. |
| 15 | admin | `pytest.ini` omitted `inbox` (3.11's tests never ran in CI) | low | patch | Fixed by the coordinator. |
| 16 | core + portal | AC "unwrapped once when a site starts" is met lazily (first signing) | false | rejected | Documented choice; the daily `kmscheck` unwraps the key; the fix would be editing this plan's AC. |
| 17 | portal | The calling side keeps no durable log row | false | rejected | The ticket asks for logging on the receiving site; the ping in both directions gives both logs. |
| 18 | core | AC wording on unwrap (same as 16) | false | rejected | As 16. |

## Verification

**Commands:**
- mytp-core: `pytest` (its README) -- all pass; the portal suite (local dev notes) and the admin suite (admin test venv) -- all pass.

**Manual checks (if no CLI):**
- On staging (user, SSH): generate keys on dev and admindev, exchange the public keys, then `ping_peer admindev` on dev and `ping_peer dev` on admindev; replay a captured token with curl → 401 logged.
