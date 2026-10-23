---
title: 'Fix R4: login code sends use up signup code-send block records'
type: 'bugfix'
ticket: ''
created: '2026-10-23'
status: 'done'
baseline_revision: '71aaaac4eb2c4404037bee7df47e7b1094c7d9c9'
route: 'oneshot'
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

**Problem:** Signup retrospective R4 (action item 10). Login code sends go through `signup.steps.send_code` with no attempt. When one hits the per-IP code-send limit, `log_ip_blocked` uses up the IP's "first block" marker for the hour (`signup.limits.first_block`), but `eventlog.record(None, …)` writes nothing. So the login block isn't recorded, and a later signup code-send block from that IP in the same hour isn't logged either.

**Approach:** The code-send counter stays shared, so one IP still has a single hourly budget of code sends across signup and login. Two changes:
- The first-block marker is kept per flow: signup (the signup and resume purposes) and login each get their own.
- A login code-send block is recorded in the login records: `record_activity(LOGIN)`, event `rejected`, field `ip`, reason `blocked: rate limit`, data `counter: code_sends`, with the Doctor's account and the login code page as the step.

Signup's logging stays as it is.

**Decision (user, 2026-10-23):** option a, both changes. Afterwards the signup spec's "logged once per IP, counter and hour" gets "and flow" through bmad-spec.

</frozen-after-approval>

## Implementation Notes

Oneshot: about 60 lines.
- `signup/limits.py`: `first_block(request, counter, flow='')` adds the flow to the marker's key. Signup's key is unchanged.
- `signup/steps.py`:
  - `send_code` takes `login_record`, the Doctor's (account id, step).
  - `_ip_blocked` records a login block through `eventlog.record_activity(Attempt.LOGIN, …)`; signup's stays in `log_ip_blocked`. Using eventlog avoids an import cycle with `accounts`.
  - A failing marker is logged and the send stays refused, as in `login_guard.reserve_ip_attempt`.
- `accounts/views.py`: the choice page records at `login-verify` and a resend at `login-code` (the login records' step names).
- Tests: in `accounts/tests/test_login_code.py`, next to the login code sends: one record per hour, the resend's step, a failing marker, a signup block still logged after a login block, and the per-flow keys.

## Review Triage Log

**Pass 1 (quick lens, 2026-10-23):** high 0, medium 1, low 3, false 1. Four patches.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | The login block was recorded at step `login` (the session key `CODE_PAGE`), which the login records use for the password page | medium | patch | `login_guard.LOGIN_STEP = 'login'`, `CODE_STEP`, `VERIFY_STEP`. Now `login-verify` from the choice and `login-code` from a resend; tests for both. |
| 2 | No test went through `send_code` for a signup block after a login block | low | patch | `test_after_a_login_block_a_signup_block_from_the_ip_is_still_logged`. |
| 3 | The tests aren't in the files the notes named | false | — | Not a defect. They sit with the login code-send tests, and the notes are corrected. |
| 4 | The new login branch called `first_block` unguarded, unlike `login_guard.reserve_ip_attempt` | low | patch | Wrapped: the error is logged and the send is still refused; `test_a_failing_block_marker_still_refuses_the_login_send`. |
| 5 | Irregular wrap of the `.verification` import | low | patch | Refilled. |

After the patches (2026-10-23), on SQLite: the full suite gives 1,459 passed, 3 failed (the known PostgreSQL-only tests).

## Staging

PR #46 merged as 4196eda (2026-10-23), together with the S2 notice and the S2/S4/R4 spec updates. CI on master (run 40032694230, PostgreSQL 17): 1464 passed; deploy-staging succeeded. The user confirmed the deploy and the no-JavaScript notice on /en/signup/ and /es/signup/. R4 itself has no manual check (covered by the tests).
