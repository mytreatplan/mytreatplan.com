---
title: 'Emergency Sysadmin reset over SSH'
type: 'feature'
ticket: '10'
created: '2026-11-04'
status: 'built'
baseline_revision: '6dc2ddf619e1570903564f58bcb2465a42a29cf2'
route: 'oneshot'
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

**Problem:** A8: if every Sysadmin is locked out or has lost their password, nobody can use the web reset (3.9), and the admin site has no web password reset.

**Approach:** A management command in mytp-admin, `reset_sysadmin_password --email <email>`, run over SSH on the server. It calls the existing `staff.service.reset_password` with `audit.service.command_actor('reset_sysadmin_password')` as the actor, so the reset behaves exactly like the web one: a 16-character temporary password, `must_change_password`, all sessions ended, the lockout cleared after commit, remembered devices invalidated by the new password hash, and one audit entry whose actor is the command. The temporary password is printed once to the terminal (`self.stdout`), never logged. The README gets a runbook next to the `create_sysadmin` one (`cd /srv/mytp-<site>/current`, `sudo -u mytp-<site> ./venv/bin/python manage.py reset_sysadmin_password --email …`, then sign in with the temporary password and a code, then choose a new password).

- Refused with a `CommandError` and nothing changed: no user with that email (`User.objects.by_email`), a deleted user, and the service's `UserChangeRefused` (its message, never the password).
- Decision (user, 2026-11-04): only a Sysadmin can be reset; any other role is refused (a Sysadmin resets them on the web).
- Decision (user, 2026-11-04): a deactivated Sysadmin is refused, with a message to reactivate them first.
- Decision (user, 2026-11-04): a y/N prompt names the user found (display name and role) before anything changes; `--yes` skips it; anything but y/yes aborts with nothing changed.
- The command copies `staff/management/commands/create_sysadmin.py`'s conventions; tests mirror `staff/tests/test_create_sysadmin.py` and the reset tests in `staff/tests/test_admin_users.py:222-380` (sign in with the temporary password, forced change, the lock lifted, the audit entry's `actor_type='command'` and `actor_label`).

</frozen-after-approval>

## Implementation Notes

Oneshot route: the reset itself is `staff.service.reset_password` (3.9), and the command actor already exists for `create_sysadmin`; this adds the command, its checks, tests and the README runbook.

- Files: `staff/management/commands/reset_sysadmin_password.py` (new), `staff/tests/test_reset_sysadmin_password.py` (new, 24 tests), `README.md` ("Emergency Sysadmin reset (over SSH)" after the `create_sysadmin` block).
- The checks (deleted, not a Sysadmin, deactivated) are `_check(user)`, run before the question and again after it (`refresh_from_db`), so a web change while the question waits is caught; the service re-checks "deleted" under its row lock.
- No answer (EOF, Ctrl-C) cancels like "N".
- Test note: signing in twice within 60 s hits the code cooldown; the end-to-end test moves the `clock` fixture.

## Review Triage Log

Pass 1 (quick lens, opus, 2026-11-04): 5 findings. high 0, medium 0, low 4, false 0, rejected-low 1.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | Role/active checked before the `[y/N]` prompt only; the service re-checks only "deleted", so a web change during the prompt is missed | low | patch | Real (narrow window; an emergency command rarely races a web Sysadmin). Patched: `_check` runs again after the answer; test `test_a_change_while_the_question_waits_is_checked_again`. |
| 2 | `input()` raises EOFError/KeyboardInterrupt → traceback | low | patch | Real, nothing changed either way. Patched: both read as no answer → "Cancelled"; test added. |
| 3 | No test of the `UserChangeRefused` → CommandError path | low | patch | Real gap. Test added (monkeypatched service). |
| 4 | Only 'y' tested as yes | low | patch | Test added for 'yes', 'Y', ' YES '. |
| 5 | No test that remembered devices are forgotten via the command | low | rejected | Same `reset_password` call as the web reset, whose test covers it (`test_first_sign_in_on_a_remembered_device_also_asks`); a command test would add setup only. |

Verification after the patches: admin suite 648 passed (SQLite).

## Verification

**Commands:**
- the admin suite (`../.venv-admin` or the admin test venv, see staging-ops notes) -- expected: all pass.

**Manual checks (if no CLI):**
- On admindev over SSH: run the command for a test Sysadmin; sign in with the temporary password and a code; the forced password change follows; the Audit log entry's actor is the command.
