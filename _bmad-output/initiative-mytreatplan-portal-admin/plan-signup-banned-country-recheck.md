---
title: 'Signup re-checks a banned country before creating the account'
type: 'bugfix'
ticket: ''
created: '2026-11-09'
status: 'done'
baseline_revision: ''
route: 'oneshot'
route_source: 'auto'
risk: 'low'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Since epic-catalogue entry 3 the Sysadmin can ban a country at any time, but signup checks the country only at step 1 (`AccountForm.clean_country`). A pending registration that passed step 1 before the ban (it stays active 24 h, and can be resumed) still creates an account for the banned country.

**Approach:** In `signup/views.py::_create_account`, inside its transaction, re-read the pending registration's country in the country map (`lookup_country(pending.country).banned`) before the user is created; when banned, create nothing and handle it as decided below, logging a `rejected` event with reason `country_banned`.

## Boundaries & Constraints

**Always:**
- The check runs inside `_create_account`'s atomic block, after the row lock and before `user.save()` (and before `client_sync.emit`), so nothing of the account or its admin copy is created.
- Same message as step 1 (`COUNTRY_BANNED_MESSAGE`), same reason code `country_banned`; logged without the typed data (the country code only).
- A blank or unknown country is not banned (as today's `lookup_country`).

- Decision (user, 2026-11-09): when banned at the last step, the doctor goes back to step 1 with the banned refusal under the country; the pending registration is kept (its later steps stay done).
- Decision (user, 2026-11-09): oneshot, one portal PR.

**Never:** no change to `lookup_country`, step 1's checks or the live check, the regions guard, or existing accounts of a now-banned country (a separate decision).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Banned after step 1 | pending with country X; X banned; last step submitted | no user, no `client.updated`; back to step 1 with the refusal; `rejected` / `country_banned` logged | — |
| Not banned | as today | account created | — |
| Resumed signup | resumed pending, X banned since | same as the first row | — |
| Ban lifted | X back to eu before the last step | account created | — |

</frozen-after-approval>

## Code Map

- `signup/views.py`: `_create_account` :399 (atomic block :405-440; exceptions pattern `_LegalNotAccepted` :376, handler :441-446 redirects to a step), `step_url` :141, `get_step`, `_render`; imports `lookup_country` :46 and `COUNTRY_BANNED_MESSAGE` :48 already present.
- `signup/forms.py` :62 message, :188 error code, :300 `clean_country` (unchanged).
- Tests: `signup/tests/test_country_check.py` (banned at step 1 :168, fixtures `eu_site`, `country`); the signup completion tests for building a pending registration at the last step.

## Tasks & Acceptance

**Execution:**
- [x] `_create_account`: `_CountryBanned` raised in the transaction; handler: pending.step = account, redirect to step 1, which shows the refusal; eventlog `rejected` with `field='country'`, `reason='country_banned'`, data the country code.
- [x] Tests: every matrix row; nothing created (no user, no outbox row); the step-1 page shows the refusal .

**Acceptance Criteria:**
- Given a pending registration whose country was banned after step 1, when its last step is submitted, then no account and no admin copy exist, and the doctor sees the banned refusal.

## Implementation Notes

- `signup/views.py::_create_account`: `_CountryBanned(code)` raised after the legal check, before `user.save()` and `client_sync.emit`; handler logs `rejected` (`field='country'`, `reason='country_banned'`, data the code), sets the pending registration's step to `account` and redirects to step 1. A blank country reads as `''` (eu, not banned).
- Tests in `signup/tests/test_country_check.py`: banned before the last step (no user, no outbox row, step back to `account`, the event, step 1 shows the refusal); an allowed country then finishes the same signup; a lifted ban after the refusal finishes it. With the check removed, two of them fail.
- Resumed signups go through step 1 again (`_take_over` copies the row at the first step) and this check anyway; no separate test.

## Plan Change Log

## Review Triage Log

Opus quick review (2026-11-09): no defects. Patched: the lifted-ban test now goes through the refusal first; the code is computed once. Accepted: a missing legal choice is caught before the country (nothing is created either way).

## Verification results (2026-11-09, local)

Signup tests 510 passed; portal full suite (SQLite) 2194 passed, 5 skipped, the 3 known PostgreSQL-only failures.

## Verification

**Commands:**
- Portal suite (local dev notes) — all pass; CI on PostgreSQL.

**Manual checks (staging):**
- On admindev ban AQ; on dev a signup that chose AQ at step 1 earlier (made before the ban, or with the test client) is refused at the last step; set AQ back to eu.

## Staging

2026-11-09: PR #79 merged after its checks passed (PostgreSQL CI green); deployed green to dev and dev-ae, the re-check present in both live releases. The behaviour is proved by the three tests (CI, PostgreSQL). The live browser check (a signup with AQ stopped at the mobile code, AQ banned, then the code submitted) needs real email and SMS codes and was not run; optional for the user.
