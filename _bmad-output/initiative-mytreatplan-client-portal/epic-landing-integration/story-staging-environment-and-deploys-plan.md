---
title: 'Staging-ready app settings'
type: 'feature'
ticket: '8'
created: '2026-10-11'
status: done
baseline_revision: '8228122cdce1917abea929e5e22d181e33b73d51'
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

**Problem:** The app only knows how to run on a developer's machine. Turned on with `DEBUG` off behind HTTPS at https://dev.mytpdso.com, it would send insecure cookies, could reach the database without TLS, would let staging be indexed, and has no way to turn portal features on only in staging.

**Approach:** Add environment-driven settings for running behind HTTPS with `DEBUG` off: a deploy check that refuses a non-TLS database, a noindex switch, the `PORTAL_ENABLED` switch, and a pinned Gunicorn. Story 1.13 then deploys it.

## Boundaries & Constraints

**Always:**
- Everything is driven by environment variables, through the existing decouple `config`, and documented in `settings.env.example` with safe local defaults. Local development with `DEBUG=True` behaves as today.
- **When `DEBUG` is off:**
  - `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` and `SESSION_COOKIE_HTTPONLY` are on;
  - `SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')`;
  - `SECURE_SSL_REDIRECT` is on unless overridden by env;
  - `SECURE_HSTS_SECONDS` comes from env (staging uses a short value, e.g. 3600; no preload);
  - `SECURE_CONTENT_TYPE_NOSNIFF` and `X_FRAME_OPTIONS = 'DENY'`.
- `CSRF_TRUSTED_ORIGINS` comes from env (CSV). `ALLOWED_HOSTS` stays env-driven.
- **Database TLS:** `DB_SSLMODE` and `DB_SSLROOTCERT` (env) feed the PostgreSQL connection options. A deploy-tagged system check errors when `DEBUG` is off and `DB_SSLMODE` isn't `verify-full` or `verify-ca`.
- **`ROBOTS_NOINDEX`** (env, default off): when on, every response carries `X-Robots-Tag: noindex, nofollow` and `robots.txt` returns `User-agent: *` / `Disallow: /`, with no sitemap line.
- **`PORTAL_ENABLED`** (env, default off): portal URL patterns are added to the language-prefixed patterns only when it's on. For now the list is empty and documented as the place signup 2.1 registers its routes. The 1.4 nav links keep appearing only when `signup` and `login` resolve.
- Pinned `gunicorn` in `requirements.txt`.
- Decision (user, 2026-10-11): staging URL `https://dev.mytpdso.com`, basic auth plus noindex, systemd, Gunicorn and Nginx, DB on its public endpoint with TLS and an IP allowlist. The server side is story 1.13.
- Decision (user, 2026-10-11): staging may run without confirmed disk encryption (test data only); production (1.12) must have it confirmed or added.

**Never:**
- Server provisioning, Nginx, systemd, certificates, the CI deploy job or the runbook (story 1.13). Production (1.12). Email (1.5).
- Defaults that weaken local development, or secrets in the repo.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Secure settings | `DEBUG=False` | Secure cookies, proxy SSL header, SSL redirect, HSTS from env, nosniff, `X-Frame-Options: DENY` | — |
| Local dev | `DEBUG=True`, no new env vars | Same behaviour as before this story | — |
| Noindex on | `ROBOTS_NOINDEX=True` | Every response has `X-Robots-Tag: noindex, nofollow`; `robots.txt` disallows all, no sitemap line | — |
| Noindex off | default | No `X-Robots-Tag`; `robots.txt` as in 1.4 | — |
| Insecure DB | `DEBUG=False`, `DB_SSLMODE` unset or `require`/`disable` | `check --deploy` errors naming the setting | System check error |
| Secure DB | `DEBUG=False`, `DB_SSLMODE=verify-full`, `DB_SSLROOTCERT` set | No DB TLS error; the connection options carry both | — |
| Portal switch | `PORTAL_ENABLED` on / off | The portal patterns hook is included / not included | — |

</frozen-after-approval>

## Code Map

- `mytreatplan/settings.py` -- decouple `config` with an optional `settings.env`. Already has `DEBUG` (default False), `ALLOWED_HOSTS` (Csv), `DATABASES = dj_database_url.parse(config('DATABASE_URL'))`, `SITE_URL`, `LEGAL_TEXTS_FINAL`, `MIDDLEWARE` (with `LocaleMiddleware`) and `STATIC_ROOT`. Add the new settings and the middleware.
- `mytreatplan/urls.py` -- `admin/`, `robots.txt`, `sitemap.xml`, plus `i18n_patterns(include('mytp_publicsite.urls'), prefix_default_language=True)`. Add the `PORTAL_ENABLED` hook inside the `i18n_patterns`.
- `mytp_publicsite/views.py` -- `robots_txt` (`require_safe`, builds the `Sitemap:` line from `SITE_URL`). Branch on `ROBOTS_NOINDEX`.
- `mytp_publicsite/checks.py` -- `check_legal_texts_final` (deploy-tagged, `E001`). Add `E002` for DB TLS.
- `settings.env.example` -- keys `SECRET_KEY`, `DEBUG`, `DATABASE_URL`, `ALLOWED_HOSTS`, `SITE_URL`. Add the new keys, commented with staging examples.
- Tests: `mytp_publicsite/tests/` (no DB). Use `override_settings`; for URL-level switches, reload the URLconf or use a test URLconf.

## Tasks & Acceptance

**Execution:**
- [x] `mytreatplan/settings.py`, `settings.env.example` -- the security block when `DEBUG` is off, `CSRF_TRUSTED_ORIGINS`, `ROBOTS_NOINDEX`, `PORTAL_ENABLED`, and `DB_SSLMODE`/`DB_SSLROOTCERT` into `DATABASES['default']['OPTIONS']`.
- [x] `mytp_publicsite/middleware.py` (+ `MIDDLEWARE`) -- add `X-Robots-Tag` when `ROBOTS_NOINDEX` is on.
- [x] `mytp_publicsite/views.py` -- the noindex `robots.txt`.
- [x] `mytp_publicsite/checks.py` -- `E002`, the DB TLS deploy check.
- [x] `mytreatplan/urls.py` -- the `PORTAL_ENABLED` hook with a documented empty `portal_patterns` list.
- [x] `requirements.txt` -- pin `gunicorn`.
- [x] `mytp_publicsite/tests/test_settings_staging.py` -- tests for every matrix row.

**Acceptance Criteria:**
- Given staging's env (`DEBUG=False`, `ROBOTS_NOINDEX=True`, `PORTAL_ENABLED=True`, TLS DB), when `check --deploy` runs, then the only errors left are the known draft-legal (`E001`) and console-mail ones.

## Implementation Notes

- `SECURE_HSTS_SECONDS` defaults to 0 (HSTS off) when unset; staging sets e.g. 3600. `check --deploy` then shows only warnings for HSTS subdomains/preload.
- `RobotsNoindexMiddleware` sits first in `MIDDLEWARE` so the header also reaches `SecurityMiddleware`'s HTTPS redirects.
- The DB TLS check is tagged `security` (deploy): Django skips `database`-tagged checks unless databases are passed.
- The portal hook is `portal_urlpatterns()` in `mytreatplan/urls.py`, returning `portal_patterns` only when `PORTAL_ENABLED` is on, so it can be tested without a DB or a second URLconf.
- CI runs tests with `DEBUG=False`, so the test job now sets `SECURE_SSL_REDIRECT=False`; otherwise the HTTP test client gets 301s.
- `gunicorn==26.2.0` (latest on PyPI, released 2026-08-24); not installed in the local venv.

## Plan Change Log

- 2026-10-11 (planning): the original 1.8 plan (staging environment and deploys) was split at the size gate. Provisioning, Nginx/systemd, the runbook and the CI deploy job moved to ticket 1.13 (logged in `deferred-work.md`).

## Review Triage Log

Pass 1 (quick lens, 2026-10-11): high 0, medium 1, low 2, false 0, maybe-false 0. The AC is confirmed met by the reviewer.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | `reload_urlconf` teardown reloads the URLconf before `PORTAL_ENABLED` is restored | medium | patch | Confirmed fixture order. Harmless now (`portal_patterns` empty), but would leave signup routes live for later tests once 2.1 lands. Patch: the fixture depends on `settings`. |
| 2 | `load_settings` not isolated from the developer's `settings.env` | low | patch | Real on machines that set the new keys, as `settings.env.example` suggests. Patch: set the keys explicitly in env. |
| 3 | No test proves `*portal_urlpatterns()` is wired into `i18n_patterns` | low | patch | Confirmed: removing it fails nothing. Patch: a stub-route test under `/en/` and `/es/`, on and off. |

## Verification

**Commands:**
- `pytest` -- expected: all pass
- `python manage.py check` -- expected: no issues
- `DEBUG=False DB_SSLMODE=verify-full DB_SSLROOTCERT=/tmp/x ALLOWED_HOSTS=dev.mytpdso.com python manage.py check --deploy` -- expected: only `E001` (draft legal) and the mail backend errors
