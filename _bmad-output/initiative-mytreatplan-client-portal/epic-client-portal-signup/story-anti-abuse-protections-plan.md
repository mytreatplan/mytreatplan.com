---
title: 'Anti-abuse protections'
type: 'feature'
ticket: '9'
created: '2026-10-14'
status: 'built'
baseline_revision: '3c1a871493871741f5fd568eab5bcaa374c3790d'
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

**Problem:** Nothing stops a script from:
- starting signups in bulk;
- probing which emails are registered (the live email check answers at once);
- triggering code sends from many contacts, each of which will cost money once Twilio is live.

The per-contact limits of entry 7 don't help against one source cycling through contacts (CAP-11, policy "CAPTCHA or rate limit blocks the attempt").

**Approach:** Two protections:
- Cloudflare Turnstile, invisible to most people, on step 1;
- per-IP limits on signup starts, live email checks and code sends, kept in entry 7's database cache.

A blocked request gets a generic error that reveals nothing, and the reason is logged.

## Boundaries & Constraints

**Always:**
- Decision (user, 2026-10-14): the CAPTCHA is Cloudflare Turnstile.
- **Turnstile:**
  - The widget (managed mode, so most people never see a challenge) is on step 1. Every step 1 POST is verified server side with Cloudflare's `siteverify`, sending the token, the secret and the client IP.
  - Settings: `TURNSTILE_SITE_KEY` (public) and `TURNSTILE_SECRET_KEY` (secret, `settings.env` only), plus `TURNSTILE_VERIFY_URL`.
  - Development, CI and tests default to Cloudflare's public test keys (always pass). Tests use the always-fail secret for the blocked case.
  - The widget renders after a boosted (htmx) swap as after a full load. Each render gets a new token, since tokens are single use.
- **Failed CAPTCHA:**
  - The step is not saved.
  - The person sees the generic error: "We couldn't process your request. Please try again later."
  - It is logged as `rejected`, field `captcha`, reason `blocked: captcha`.
- **Per-IP limits:**
  - Counted on `eventlog.service.client_ip` in fixed one-hour windows, in `CACHES['default']`.
  - Keyed by a hash of the IP with `SECRET_KEY`, never the plain IP.
  - Three counters: signup starts (a step 1 POST that would create a new pending registration), live email checks, and code sends of every purpose (successful sends only, as in entry 7).
  - Decision (user, 2026-10-14): 60 signup starts, 180 live email checks and 120 code sends per IP per hour. They are sized for demand peaks such as live conferences, congresses, courses and webinars, where many attendees share one venue IP. The three values are settings, read from the environment (`settings.env`), so they can be changed with a restart and no deploy.
- **Blocked by a limit:**
  - Step 1 and the resume or code pages show the generic error, and the live email check returns an empty partial.
  - It is logged as `rejected`, reason `blocked: rate limit`, with field `ip` and the counter's name in `data`.
  - The page doesn't say which limit was hit or when it resets.
- Decision (user, 2026-10-14): when Turnstile can't be reached (timeout or error from `siteverify`), the step goes through and `check_unavailable` (field `captcha`) is logged, as for the other external checks. The IP limits still apply.
- Decision (user, 2026-10-14): the plan is kept whole, above the size guideline.
- Nothing about the existing per-contact limits of entry 7 changes. A send must pass both the contact and the IP limits.
- English and Spanish for every new text.

**Never:**
- Turnstile on any other page (the IP limits cover the rest). Blocking by country or IP lists.
- Storing or logging the Turnstile token or the secret.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| CAPTCHA passes | Step 1 with a valid token (test pass keys) | Saved as before | No error expected |
| CAPTCHA fails | Step 1 with the always-fail secret | Not saved; generic error; `rejected` `blocked: captcha` | No error expected |
| No token | Step 1 POST without `cf-turnstile-response` | Same as failed | No error expected |
| Turnstile down | `siteverify` times out or errors | Step goes through; `check_unavailable` (field `captcha`) logged; IP limits still apply | No error shown |
| Starts limit | Signup starts beyond the limit from one IP within an hour | Generic error; nothing saved; `blocked: rate limit` | No error expected |
| Starts other IP | Same count, another IP | Allowed | No error expected |
| Existing signup | Step 1 resaved for a pending registration already in the session | Not counted as a start | No error expected |
| Email checks limit | Live checks beyond the limit from one IP | Empty partial; logged | No error expected |
| Sends limit | Code sends beyond the limit from one IP, across contacts | Not sent; generic error; logged | No error expected |
| Window resets | Next hour | Allowed again | No error expected |
| Key | Cache keys | No plain IP | No error expected |
| Spanish | `/es/` | Generic error in Spanish | No error expected |

</frozen-after-approval>

## Code Map

- `signup/limits.py` (entry 7): the per-contact sends (`check_send`, `record_send`, `acquire_send_lock`). Add the per-IP counters next to them: `ip_allowed(request, counter)` and `ip_record(request, counter)`.
  - Use `cache.add` + `cache.incr` on a key of the counter, the hashed IP and the window number, with a timeout of a little over an hour.
  - Hash: HMAC-SHA256 of the IP with `SECRET_KEY`, truncated.
- `eventlog/service.py` `client_ip(request)`: the IP source (the last `X-Forwarded-For` entry from our Nginx).
- `signup/steps.py` `send_code`: check the IP counter (sends) together with the contact limits, under the lock. Record it on a successful send. When the IP limit blocks, return a new status `BLOCKED`, which the pages show as the generic error, distinct from cooldown or hourly.
- `signup/views.py`:
  - `signup()` index 0 POST: verify Turnstile first, and stop there with the generic error if it fails. The token is used up either way, and the re-rendered page gets a new widget. Run the starts limit when `pending` is None.
  - `check_email`: the email-checks limit.
  - `resume`: sends already go through `send_code`.
  - Log through `eventlog.record` as the other `rejected` events.
- New `signup/captcha.py`: `verify_turnstile(token, ip)` returns `PASSED`, `FAILED` or `UNAVAILABLE`, with a POST to `TURNSTILE_VERIFY_URL`.
  - `accounts/http.py` has only `get_text`. Add `post_form(url, data)` with the same timeout, User-Agent and `FETCH_ERRORS`.
  - Never log the token.
- `signup/forms.py` `FAILURE_REASONS`: add `captcha_failed` → `blocked: captcha` and `rate_limited_ip` → `blocked: rate limit`. The generic error is a non-field error.
- Templates:
  - `step_account.html`: the widget `<div class="cf-turnstile" data-sitekey=...>` inside the form.
  - `_scripts.html` or step 1: Cloudflare's `api.js?render=explicit` (`https://challenges.cloudflare.com/turnstile/v0/api.js`), with a small render call on load and on `htmx:load`, so a boosted swap gets a widget. Put it in `signup-account.js`, which already re-initializes per swap.
  - `_code_help.html` and `resume.html` show the generic error for `BLOCKED`.
- `mytreatplan/settings.py`: the three Turnstile settings (`config(...)` with the test-key defaults), and `SIGNUP_IP_LIMITS` (dict of the three values). `settings.env.example` and `deploy/staging.env.example`: comments for the real keys.
- `locale/es/LC_MESSAGES/django.po`.
- Tests:
  - new `signup/tests/test_abuse.py`. Patch `verify_turnstile` or the HTTP call, and use the `clock` fixture from entry 7's conftest for the window.
  - The existing flow tests post step 1 without a token. Make the helpers send the test token, or have Turnstile pass in tests by default (test keys plus a patched HTTP call). The browser test (entry 8) runs against `live_server`: there the always-pass test sitekey works, but `siteverify` must not be called over the network, so patch it in that test.

## Tasks & Acceptance

**Execution:**
- [x] `signup/captcha.py`, `accounts/http.py` -- Turnstile verification.
- [x] `signup/limits.py`, `signup/steps.py`, `signup/views.py`, `signup/forms.py` -- IP limits, generic error, logging.
- [x] Templates, `static/js/signup-account.js`, settings, env examples, `django.po`.
- [x] Tests -- every matrix row; existing helpers pass the CAPTCHA.

**Acceptance Criteria:**
- Given staging with real Turnstile keys in `settings.env`, when a person completes step 1 in a normal browser, then no challenge is usually shown and the step saves. When the token is removed (DevTools), the step is refused with the generic error.
- Given CI on PostgreSQL, when the suite runs, then every test passes.

## Implementation Notes

- `verify_turnstile`: a missing or oversized (>2048) token fails without calling Cloudflare. siteverify answers `internal-error`, `missing-input-secret` or `invalid-input-secret` count as UNAVAILABLE (they say nothing about the person; a refused secret is logged at error level so Sentry alerts on a misconfiguration).
- Settings: an empty `TURNSTILE_*` value counts as unset (falls back to the test keys), since an empty site key would render no widget and refuse every step 1. Limits: `SIGNUP_IP_LIMIT_STARTS`, `SIGNUP_IP_LIMIT_EMAIL_CHECKS`, `SIGNUP_IP_LIMIT_CODE_SENDS`.
- A refused step 1 is not validated (`_refuse` seeds empty errors before `add_error`), so no external check runs and only the generic error shows; what was typed is kept (not the passwords).
- Starts are counted when step 1 actually creates a pending registration (not on invalid POSTs, resaves or the resume offer); the check runs before validation whenever the session has no pending registration. Email checks count every live check request.
- `ip_record` restores the window timeout with `cache.touch` after `cache.incr` (Django's base `incr` re-sets with the 5-minute default timeout).
- The view stays step-agnostic: `Step.get_context()` (AccountStep: site key and script URL).
- After review: IPv6 addresses are counted by their /64 (IPv4-mapped as IPv4, unparsable as is). Each IP check, action and count runs under a per-IP-and-counter `cache.add` lock (30 s timeout, released in `finally`; inside the contact lock for sends); a held lock answers as blocked. An IP-limit block is logged once per IP, counter and window (`first_block` flag); later blocks write nothing (step 1 starts its attempt lazily so they create none). Step 1 JS shows a note when Cloudflare's script cannot load, and holds a submit without a token ("Checking your browser…") until the widget's callback.
- Second review: reserve-then-refund. `ip_reserve` does the read-check-increment under the per-IP lock (held milliseconds, 5 s safety timeout; a held lock is retried every 50 ms for up to 1 s before answering blocked); the lock is not held during the send or the save. A failed send, or a step 1 that saves nothing, refunds its reservation (`ip_refund`, in the reservation's own window). JS: Turnstile `error-callback`, `timeout-callback` and a 15 s fallback end a held submit with the error note.
- Tests: an autouse `turnstile` fake of siteverify in the root conftest (test secrets semantics); `_step1` posts the test token. The browser test stubs `challenges.cloudflare.com` in Chromium with a script whose `render` adds the test token, so it never reaches the network.

- Added at the user's request after the review (2026-10-14): the deploy check `mytp_publicsite.E003` (`check_turnstile_keys` in `mytp_publicsite/checks.py`). It fails `check --deploy` while `DEBUG` is off and either Turnstile key is one of Cloudflare's test keys, which the settings default to. Like the other deploy checks it runs only with `check --deploy`, which no staging deploy runs today. It guards the production deploy (story 1.12). Tests are in `mytp_publicsite/tests/test_settings_staging.py`.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-14):** high 0, medium 4, low 0, false 0, maybe-false 0. Four patches. A follow-up corrected the orchestrator's own patch instruction (lock scope) and a gap the implementer flagged (a held submit with no token).

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | The full IPv6 address is the key, so a /64 holder gets a fresh counter per address (all three limits defeated) and floods the cache past `MAX_ENTRIES`, culling entry 7's live per-contact rows | medium | patch | Real: Nginx listens on `[::]`. Fix: IPv6 counted per /64, IPv4-mapped addresses as IPv4, unparsable values hashed as is. Tests added. |
| 2 | IP check and count are separate and unlocked, so parallel requests across contacts overshoot | medium | patch | Real: the only lock was per contact, and `incr` on DatabaseCache is read-then-write. Fix: reserve-then-refund. The per-IP lock covers only the counter (5 s safety timeout), and a held lock is retried for up to 1 s. A failed send or an unsaved step gives the reservation back. A first version that locked around the whole action would have blocked simultaneous attendees on one venue IP, and was corrected. Tests added. |
| 3 | Every blocked request from a cookieless script starts an Attempt and writes an event, so blocked traffic still grows the tables | medium | patch | Real: `_attempt` at index 0 starts one. Fix: an IP-limit block is logged once per IP, counter and window, and the attempt is created only when logged. Failed CAPTCHAs are still logged each time. Test added. |
| 4 | If Cloudflare's `api.js` is blocked, or a submit beats the token, the person only ever sees the generic error | medium | patch | Real: the load failure was swallowed and the submit wasn't held. Fix: a visible "security check could not load" note; a submit with no token is held ("Checking your browser…") and sent on the token callback; the error and timeout callbacks plus a 15 s timer end the wait with the error note. EN/ES. |

## Verification

**Commands:**
- `DATABASE_URL="sqlite:///$TMPDIR/x.sqlite3" ../.venv/bin/python -m pytest -q` (with test keys) -- expected: only the 3 known PostgreSQL-only tests fail; the browser test skips locally.
- `manage.py makemigrations --check`, `manage.py compilemessages` -- expected: no changes; compiles.
- Don't download Chromium, or install anything else outside the sandbox. The browser test runs in CI.

Done locally (2026-10-14) after the review patches, on SQLite: `pytest` 739 passed (745 with the deploy check), 7 skipped (PyYAML, and the browser test without Playwright), 3 failed only because those tests use PostgreSQL-only raw SQL. `makemigrations --check` reports no changes; `compilemessages` is up to date; `node --check` passes. The Turnstile JS has not run in a browser yet. Pending: the browser test in CI, CI on PostgreSQL, and the staging check with real Turnstile keys.
