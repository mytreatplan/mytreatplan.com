---
title: 'Fresh-code check for sensitive actions'
type: 'feature'
ticket: '5'
created: '2026-11-03'
status: 'built'
baseline_revision: '2678de434936f7c5df0b1b30f9b77155c8c0e1f1'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: [] # code repo: mytp-admin (branch from origin/master 2678de4 or later). Reuses 3.4's sign-in building blocks in staff/signin.py, staff/forms.py and staff/views.py.
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A2: some admin actions are sensitive. The first is the patient-record download (epic-clinical-ops), and the next is the password reset (entry 9). They must ask for a new code even when the user is signed in on a remembered device.

**Approach:** In mytp-admin, add a `fresh_code_required(action)` decorator and a step-up code page. It reuses 3.4's code sending, code check and lockout counter, with its own purpose, ignores remembered devices, and grants one use of that one action. It's proven on a sample sensitive action in List demo.

## Boundaries & Constraints

**Always:**
- **Policy flag.** When `POLICY.fresh_code` is False, the decorator passes straight through. ADMIN sets it True. The mytp-core docstring that says "at every sign-in" is wrong, and is only noted in deferred-work (no core release now).
- **Step-up flow:**
  - The decorator stores `staff_step_up` in the session: the action name, the safe same-host `next`, the start time, the user's auth hash, the channel and the last send status. It never stores the code or the contact.
  - It sends a code with a new purpose, `PURPOSE_STEP_UP = 'step_up'`, by email by default, with "send it by SMS instead" when the user has a mobile, exactly as at sign-in. 3.4's send limits apply per purpose.
  - It redirects to `/confirm/` (`step_up_code`), a page inside the layout that says which action is being confirmed. It has no "remember" box, and remembered devices are never consulted.
  - The pending step-up expires after 10 minutes. It's dropped if the auth hash changes or the account locks.
- **Codes.** A right code creates a one-use grant (action, path, user UUID, session) and returns to `next`; running the action consumes it. A wrong code that isn't malformed counts on 3.4's CODE counter (`code_rejected`). If that wrong code locks the account, the user is signed out and sees the lock message on the login page (locked 10 min; the Sysadmin alert fires).
- **POST actions.** For a POST, the decorator shows a confirmation page after the code, which re-posts the action with a CSRF token. The original POST body is never stored in the session.
- **Sample action:** "Download record" on a List demo detail page (`/list-demo/<pk>/download/`), a one-record CSV built like 3.7's export. It mirrors the future patient-record download. It's guarded by `area_required('list_demo', EXPORT)` plus `fresh_code_required('download_sample')`, and it's removed with List demo.
- **Logs** carry the UUID, purpose and channel only.

- Decisions (user, 2026-11-03): a right code is good for one use of that one action; reaching the lock during the step-up signs the user out with the lock message.

**Never:**
- No change to mytp-core, the sign-in flow, the matrix, or the remembered-device behaviour at sign-in.
- No step-up for actions not marked with the decorator.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Remembered device | signed in through a remembered mark; open Download | step-up page; a code sent | — |
| Right code | enter the code | the download happens once | — |
| Grant used | open Download again | asked for a new code | — |
| Wrong code | wrong code | error; CODE counter +1 | 5th wrong code: signed out, lock message on the login page |
| Malformed | empty or "12ab56" | invalid_code; not counted | — |
| Expired | the step-up after 10 min | back to the action, which asks again | — |
| Flag off | `fresh_code=False` policy (test) | the action runs with no code | — |
| Other user's grant | a grant from another session or user | not accepted | — |
| Password changed meanwhile | hash differs | step-up dropped; asked again | — |

</frozen-after-approval>

## Code Map

All in `mytp-admin/`:
- `staff/views.py` -- `login` :194 (lock :205, password :209, remembered :217, send :219), `login_code` :245, `_pending_login_view` :140, `_account_unchanged` :111, `finish_login` :162, `PENDING_LOGIN_SESSION_KEY` :54, the malformed-not-counted rule :274. Reuse its patterns; keep the login behaviour.
- `staff/signin.py` -- `send_login_code(user, channel)` :230, with the purpose hardcoded :236 (add a `purpose=` parameter, default login). `AdminHooks` :189 keys its limits per purpose (:184). `code_rejected` :159, `account_locked` :107, `POLICY` :81 (the comment about entry 5).
- `staff/forms.py` `LoginCodeForm` :83, with the purpose hardcoded :114 (add `purpose=`, hide `remember` for step-up).
- mytp-core 0.5.0 -- `verification.py:49-53` (sites may add purposes; the fake builds codes from the purpose), `policy.py:13,47,74` (the `fresh_code` flag and its wrong docstring).
- `lists/sample.py` (the detail view, the 3.7 CSV helpers `csv_safe` and the BOM) for the sample download. `lists/urls.py`.
- Tests: the fixtures in `staff/tests/test_sign_in.py:1-65` (`clock`, `current_code`, `sign_in`) move to conftest or are imported. Add `staff/tests/test_step_up.py`. The sample download is an EXPORT view, not a CHANGE view, so the audit guard isn't affected.
- `deferred-work.md` (initiative) -- the mytp-core `fresh_code` docstring fix, for the next core release.

## Tasks & Acceptance

**Execution:**
- [x] `staff/stepup.py` (decorator, session state, grant) + `step_up_code` view + template + URL; `purpose=` on `send_login_code` and `LoginCodeForm`
- [x] the sample "Download record" action on List demo
- [x] tests for every matrix row; the README "Sensitive actions" section (how a later epic marks one); the deferred-work line

**Acceptance Criteria:**
- Given admindev after deploy (user), when the Sysadmin, signed in on a remembered device, clicks Download record, then a code is asked and sent, the right code downloads the record, and a wrong code counts toward the lockout.

## Verification

**Commands:**
- mytp-admin suite (CI env), `check`, `makemigrations --check` -- pass. CI on PostgreSQL -- green.

**Manual checks (if no CLI):**
- admindev (user): the acceptance criterion.

## Implementation Notes

- Built on branch `story/3.5-fresh-code` of mytp-admin (from origin/master 2678de4). New: `staff/stepup.py` (the decorator `fresh_code_required(action, label=None)`, the pending step-up `staff_step_up`, the one-use grant `staff_step_up_grant`, and the `step_up_code` view at `/confirm/`), `staff/templates/staff/step_up_code.html` and `step_up_confirm.html`. `send_login_code(..., purpose=)` and `LoginCodeForm(..., purpose=)` (no `remember` field for any purpose but login); `PURPOSE_STEP_UP = 'step_up'` lives in `staff.signin`.
- The view lives in `staff/stepup.py` (not `staff/views.py`) so `views` does not import `stepup` (stepup reuses `safe_next` and the send-action names from views).
- `label` is a lower-case phrase read after "To" on the code page; each action name has one label (a second label raises ImproperlyConfigured at import).
- POST actions: after the right code, `/confirm/` redirects to itself and, with no pending step-up but a valid POST grant, renders the re-post page (form to the action's path with a CSRF token). The decorated POST view therefore never receives a GET. Proven with a test-only URLconf (`staff/tests/step_up_urls.py`).
- Any request to a marked action pops the session's grant: it runs once if it matches (action, path, method, user UUID, session key, under 10 minutes), else it is thrown away. A grant also expires after 10 minutes (not stated in the plan; chosen so an unused grant cannot linger).
- Expired step-up on `/confirm/`: a GET action goes back to its URL with "Your confirmation has expired" (which starts a new step-up); a POST action goes home with the same message (it cannot be replayed).
- An account already locked when the action is opened is signed out with the lock message, and no code is sent.
- A right step-up code does not reset the failure counters (only a completed sign-in does, as in 3.4).
- Sends within 60 s of the last step-up send show the "less than a minute ago" cooldown text, as at sign-in, even though the last code was used (3.4's behaviour, unchanged).
- Fixtures `clock`, `sysadmin`, `sentry_messages` moved to `staff/tests/conftest.py`; helpers are imported from `test_sign_in`.
- Verification (local, SQLite, CI env vars): `check` OK, `makemigrations --check` no changes, suite 504 passed (473 before + 31 in `test_step_up.py`). CI on PostgreSQL not run yet (needs the push). Review lens not run.

- Review fixes (coordinator, 2026-11-03): the single use is a nonce in the grant plus `cache.add`/`cache.delete` (atomic); the grant matches `request.get_full_path()` exactly; a waiting grant never runs on a locked account (signed out with the lock message); marked actions are POST only (any other method 405, the sample button is a CSRF form; an expired step-up goes back to the same-host Referer, else home); "no valid code" counts only while a code was waiting (the fake and Twilio drop a code at its 5th wrong guess, so that guess must still count for the 5th-wrong lock), and the page says "wait a minute, then send a new code" in a cooldown right after a used code; purpose separation is documented as fake-only (Twilio Verify: one service for all purposes).

## Plan Change Log

## Review Triage Log

### Pass 1 (quick, security focus, 2026-11-03): high 0, medium 3, low 5, false 1, maybe-false 0

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | Purpose separation holds only in the fake provider; Twilio Verify serves every purpose from one service, so the claim "a sign-in code never confirms an action" is false on admindev | medium | patch + defer | Real (twilio_verify.py docstring). Practical exposure small (a signed-in user's sign-in code is already used). Patched the claim in comments, docstring, README and the test name; a separate Verify service for step-up is in deferred-work. |
| 2 | The one-use grant could run twice under concurrent requests (session pop only) | medium | patch | Patched: a nonce in the grant backed by `cache.add`/`cache.delete` (atomic); test. |
| 3 | A GET to a marked action (sample download was `@require_GET`) could be triggered cross-site: sends a code, replaces the pending step-up, destroys a waiting grant | medium | patch | Patched: marked actions are POST only (405 otherwise); the sample download is a CSRF-protected form button; README rewritten. |
| 4 | A used code shown as "enter it below" in the cooldown was counted (code_expired) toward the lockout | low | patch | Patched: "no valid code" counts only while a code was waiting (the 5th-wrong-code lock still holds); the page says to wait and send a new code; tests. |
| 5 | The grant was bound to the path only (a different query string ran) | low | patch | Patched: exact `get_full_path()` match; test. |
| 6 | Percent-encoded paths never matched their grant (loop) | low | patch | Patched with 5; test with `/test-sensitive/caf%C3%A9/`. |
| 7 | A stored grant still ran after the account locked | low | patch | Patched: lock checked before consuming; signed out; test. |
| 8 | AC and CI not yet verified | false | rejected | Manual post-deploy check (user) and the PR's CI. |

Verified after the patches: mytp-admin suite 515 passed (SQLite, CI env), `check` clean, `makemigrations --check` no changes.
