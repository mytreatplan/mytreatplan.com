---
title: 'Page-request records'
type: 'feature'
ticket: '7'
created: '2026-10-21'
status: 'done'
baseline_revision: '1acf9e755d016ed39e51461dfc94314878d3aab9'
route: 'full'
route_source: 'auto'
risk: 'low'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The spec (CAP-4) asks that every full page shown to a logged-in Doctor is recorded with the page and its time, for security, support and debugging, and usage reporting. htmx partial updates and static files aren't recorded. Nothing records page views today.

**Approach:** A middleware after 3.6's `SessionLimitMiddleware` records, through 3.4's `eventlog.service.record_activity`, each response that is a full HTML page shown to an authenticated user. Each record holds the page and the time, under a new attempt kind `page`, tied to the account's pk. Like login records, page records are kept intact by the current retention job until entry 8.

## Boundaries & Constraints

**Always:**
- Recorded: a GET by an authenticated user that ends in a `text/html` response with status 200 or an error status (4xx/5xx), not an htmx partial update (decision 4) and not a static or media file.
- Not recorded: redirects (3xx), other non-error statuses, POSTs, anonymous visitors, and requests whose session 3.6 has just ended.
- A record: kind `page` (a new `Attempt.PAGE`), `account_id` = the user pk, event `page_view`. The URL name, where it resolves, goes in `step` (it isn't personal). The status goes in `reason`. The full path with its query string (decision 2), the IP and the User-Agent go in the encrypted data.
- The retention job (`eventlog/retention.py` `EXCLUDED_KINDS`) also skips `page`, as 3.4's decision 1 does for `login`, until entry 8.
- Recording never breaks a page or changes its response. A failure is reported as `record_activity` already does, without values.

**Never:**
- Usage reports or dashboards. This story only records.
- Recording request bodies.
- Changes to 3.6's session handling, or to login records.

**Decisions (2026-10-21):**
1. Admin pages are recorded too: every page shown to any logged-in user, staff in `/admin/` included, under the same rules.
2. The full path with its query string (`request.get_full_path()`) is recorded, in the encrypted `Event.data` only, never in a plain column.
3. Error pages shown to a logged-in user are recorded too: GETs answered with a 4xx or 5xx `text/html` page, with the status in the record (`reason`, e.g. `404`). Successful pages record `200`.
4. (2026-10-21, at review) htmx full-page loads are recorded too. A request with `HX-Request` is skipped only when it's a partial update: neither `HX-Boosted` nor `HX-History-Restore-Request` is set. Boosted navigation (signup's wizard, which lands on welcome) and history restores replace the whole page, so they count as full pages.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Welcome | Logged-in GET `/en/welcome/` | One `page` record: account pk, `step=welcome`, `reason=200`, path in data | — |
| Query string | GET `/en/welcome/?x=1` | The data holds `/en/welcome/?x=1`; no plain column holds it | — |
| htmx partial | GET with `HX-Request`, no `HX-Boosted` / `HX-History-Restore-Request` | No record | — |
| htmx full page | Boosted GET (`HX-Boosted`) or a history restore | One record (decision 4) | — |
| Static | GET `/static/…` | No record | — |
| Redirect | GET answered with 302 | No record | — |
| POST | Logged-in POST | No record | — |
| Anonymous | GET of a public page | No record | — |
| Session ended | 3.6 ends the session on this request | No record | — |
| Non-HTML | Logged-in GET returning JSON or a file | No record | — |
| Error status | Logged-in GET → 404 / 403 / 500 HTML | One record with `reason` 404 / 403 / 500 | — |
| Admin | Staff GET of an `/admin/` page | One record (`step` the admin URL name) | — |
| Retention | The job runs past 30 days and 2 years | Page records untouched | — |
| Log fails | `record_activity` fails | Page served unchanged | Sentry without values |

</frozen-after-approval>

## Code Map

- `eventlog/models.py` -- `Attempt` kinds (`SIGNUP`, `LOGIN`). Add `PAGE = 'page'`. No migration (`kind` is a free CharField).
- `eventlog/service.py:112-132` -- `record_activity(kind, event, *, request, account_id, step, field, reason, data)`: one closed attempt with its event; never raises; a failed write leaves nothing.
- `eventlog/retention.py` -- `EXCLUDED_KINDS = (Attempt.LOGIN,)`; add `Attempt.PAGE`.
- `accounts/sessions.py:171-` -- `SessionLimitMiddleware`. The new middleware is placed outside it, after `RobotsNoindexMiddleware` in `MIDDLEWARE`, so on the way out it sees the final response and the user as 3.6 left it (anonymous after an end); see the Plan Change Log.
- `django.urls.resolve` / `request.resolver_match` -- for the URL name (`url_name`, with its namespace for the admin).
- Tests:
  - `accounts/tests/test_sessions.py` helpers (two clients, `force_login`), and `eventlog/tests/test_service.py`;
  - the root conftest's `sentry_capture`;
  - 3.4's `accounts/tests/test_login_records.py` for reading records (`Attempt.objects.filter(kind=...)`, the decrypted `Event.data`).

## Tasks & Acceptance

**Execution:**
- [x] `eventlog/models.py`, `eventlog/retention.py` -- `PAGE`; retention skips it.
- [x] `accounts/page_records.py` (new) -- `PageRecordMiddleware`.
- [x] `mytreatplan/settings.py` -- add it as the outermost middleware but one (after `RobotsNoindexMiddleware`), so it sees the final response and the user as 3.6 left it (see Plan Change Log).
- [x] `accounts/tests/test_page_records.py` (new) -- every matrix row.

**Acceptance Criteria:**
- Given a logged-in Doctor, when they open the welcome page twice and an htmx request once, then there are exactly two `page` records for their account.
- Given the full suite, when it runs, then the 3.1–3.6 tests pass.

## Implementation Notes

- `step` is the URL name with its namespaces (`welcome`, `admin:index`), cut to 50 characters; empty when nothing resolves (a 404). `reason` is the status as text (`200`, `404`, ...). `data` is `{'path': get_full_path()}` plus `record_activity`'s IP and user agent.
- Static files: paths under STATIC_URL (and MEDIA_URL when it is set to more than `/`; unset, Django reads it as `/`, which would match every path) are skipped. Streamed responses (files) and non-`text/html` responses are skipped too.
- Any failure around the write (resolving the name, reading the user) is caught and reported with `report_failure('record_page', error, 'page_view')`: action, event and error class only.
- Test-only views (JSON, PDF, 403, 500, a 204 HTML answer) live in the test module, used as its own URLconf over the portal routes.

## Plan Change Log

- 2026-10-21 (implementation): the middleware is second in `MIDDLEWARE` (right after `RobotsNoindexMiddleware`), not right after `SessionLimitMiddleware` as the Code Map said. Placed inside the session limit, it would record (1) a page whose session 3.6 ends during the view (`_ended_meanwhile`), whose response the session limit then replaces with a redirect to login, against the frozen "Session ended → no record" row; and (2) the 404 behind every LocaleMiddleware / CommonMiddleware redirect (`/` → `/en/`, a missing slash), against "Redirect → no record". Wrapping them all, it sees the final response and the user as 3.6 left it (anonymous after an end). Intent unchanged; both cases are tested.

## Review Triage Log

**Pass 1 (quick lens, 2026-10-21):** high 0, medium 1, low 1, false 0, maybe-false 0. One patch; one rejected.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | Every `HX-Request` was skipped, so htmx full-page loads (boosted navigation, as signup's wizard landing on welcome, and history restores) went unrecorded, against CAP-4's "every full page" | medium | patch (after a user decision) | The frozen rule said "not an htmx request (`HX-Request`)". The user chose to record full-page loads, added as decision 4. `_is_partial` now skips only `HX-Request` without `HX-Boosted` / `HX-History-Restore-Request`. Test `test_htmx_full_page_load_is_recorded` covers both variants; plain htmx requests stay unrecorded. |
| 2 | The ticked task and the Code Map described the middleware "after `SessionLimitMiddleware`", not the outer position built | low | rejected | The fix is an edit to this plan (done as bookkeeping: the task and Code Map lines now describe the outer position, as the Plan Change Log explains). |

After the patch (2026-10-21), on SQLite with Chromium (sandbox lifted): the full suite gives 1,196 passed, 3 failed (the known PostgreSQL-only tests).

## Verification

**Commands:**
- The local suite command (test keys, SQLite), outside the sandbox for the browser tests. Expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py makemigrations --check`. Expected: no changes.

CI on PostgreSQL 17 (PR #34): 1199 passed. Merged as `343deb0`. On staging the user reported (2026-10-21) these checks passed in the admin: two welcome visits leave two `page` records (`welcome`, `200`); a missing page leaves a `page` record with `404`; admin pages appear as `page` records with `admin:…` steps.
