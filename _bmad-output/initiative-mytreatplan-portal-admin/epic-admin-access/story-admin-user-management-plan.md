---
title: 'Admin user management'
type: 'feature'
ticket: '9'
created: '2026-11-03'
status: 'built'
baseline_revision: 'b080e4228086ba872725fbb97cb04a20357fadd1'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: [] # code repo: mytp-admin, built after entry 5 (fresh-code check) is merged. Patterns: lists/sample.py + lists/service.py (3.7/3.8), staff/service.py create_staff_user_audited (3.8), staff/views.py sign-in (3.4).
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A7: Sysadmins have no way to manage admin users on the site. They can't list, add, edit, deactivate or delete users, set roles and regions, or reset a forgotten password. Only `create_sysadmin` over SSH exists.

**Approach:** In mytp-admin, build the `admin_users` area on 3.7's list components and 3.8's audited services.
- **The list:** a list of admin users, with create, edit, deactivate, reactivate and logical delete.
- **Roles and regions:** role and regions are set through `validate_access`.
- **Password reset:** it sets a temporary password, shows it once to the Sysadmin and never emails it, and marks the account so the user must change it right after password and code. That change uses a shared password-change form, which entry 11 reuses.

Every step is audited.

## Boundaries & Constraints

**Always:**
- **Access:** the `admin_users` area is Sysadmin only (unchanged matrix). Writes are guarded by `area_required('admin_users', CHANGE)`, so 3.8's audit guard covers them. Every write goes through `staff/service.py` inside `transaction.atomic()` with `audit.record`.
- **The model:** `staff.User` gains `must_change_password` (bool, default False) and `deleted_at` (nullable datetime), in migration `0004`. Hard delete stays refused (`HardDeleteRefused`).
- **The list:** columns display name, email (masked as in sign-in, e.g. `a•••@domain`), role, regions, active, created and status (Active / Inactive / Deleted). Search covers display name and role, never email values in the URL (3.7 POST search). Deleted users are hidden by default and shown with a "Deleted" status filter.
- **Create:** email, display name, title, role, regions, mobile and time zone. A temporary password is set as for a reset, and `must_change_password=True`.
- **Edit:** display name, title, role, regions, mobile and time zone, and email (to fix a typo: audited as "changed"; it changes the email index, which ends the user's pending login, and the remembered devices end).
- **Deactivate:** `is_active=False`, and the user's sessions are ended at once (`single_session.end_all_sessions`). They can't sign in, because inactive users already get the generic error.
- **Reactivate:** the Sysadmin can reactivate a deactivated user, but not a deleted one.
- **Logical delete:** `deleted_at=now` and `is_active=False`, with sessions ended. It's final: no restore, and the email stays taken (`email_index` unique). The user stays in the directory as inactive, so old actor UUIDs resolve (AD-22).
- **Self-protection:** a Sysadmin can't deactivate, delete or demote themselves; the last active Sysadmin can't be deactivated, deleted or given another role.
- **Password reset:**
  - It (and creating a user) is guarded by entry 5's `fresh_code_required`: the Sysadmin enters a fresh code first.
  - It generates a 16-character temporary password with `secrets`, validated against `AUTH_PASSWORD_VALIDATORS`. It's shown once on the response page with `Cache-Control: no-store` and `sensitive_post_parameters`, never stored in plain text, logged, emailed, or put in messages or the session.
  - It sets `must_change_password=True` and clears the user's lockout (`reset_failures`). The password change already ends their sessions, remembered devices and pending login (auth hash).
  - It's audited as `reset_password`, even when the flag was already True.
- **Must change password:**
  - `finish_login` checks the flag after password plus code, or a remembered device. If it's set, the pending login is kept and the user goes to `/login/password/` (the shared `PasswordChangeForm`: new password twice, validated, not equal to the temporary one).
  - Saving sets the flag False, refreshes the pending hash, and calls `finish_login`. No other page can be reached until then.
  - It's audited as `update` by the user themself.
- **Masking:** `must_change_password` and `deleted_at` are plain. `email` and `phone` stay masked, and the password stays excluded.

- Decisions (user, 2026-11-03): reset and create need a fresh code (entry 5); the Sysadmin may edit a user's email; self- and last-Sysadmin protection as above.

**Never:**
- No self-service profile or own password change from the menu (entry 11 reuses the form). No emergency SSH reset (entry 10). No email of passwords.
- No change to sign-in apart from the must-change step. No matrix change.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Create | the Sysadmin creates a Clinical User | the user exists, must change password; the temporary password shown once; audit `create` | duplicate email (any case): form error |
| CRM without region | role CRM, no region | refused | `validate_access` error |
| Reset | the Sysadmin resets a user's password | a fresh code first, then the temporary password shown once; audit `reset_password`; their sessions ended; lockout cleared | — |
| First sign-in | the user signs in with the temporary password and a code | sent to `/login/password/`; nothing else reachable | — |
| Change | a valid new password twice | signed in; flag cleared; audit `update` | weak, mismatched or equal to the temporary one: form error |
| Deactivate | the Sysadmin deactivates a user | the user's next request is signed out; sign-in gives the generic error; audit `deactivate` | — |
| Reactivate | the Sysadmin reactivates them | can sign in again; audit `reactivate` | a deleted user: not offered, and refused |
| Delete | logical delete | hidden from the default list; can't sign in; audit `delete` | no restore |
| Self / last | a Sysadmin deactivates, deletes or demotes themselves, or the last active Sysadmin | refused | refused with a message |
| Non-Sysadmin | any role opens /admin-users/ | 403 | — |

</frozen-after-approval>

## Code Map

All in `mytp-admin/`:
- `staff/models.py` -- fields :76-89, `validate_access` :125, the docstring about the logical-delete UI :69-70, `AUDIT_EXCLUDED` :98, `audit_repr`. Add `must_change_password`, `deleted_at`, migration `0004`.
- `staff/managers.py:42` `create_staff_user` (validate the password first; validators in settings :283).
- `staff/service.py:12` `create_staff_user_audited` -- add `create_user`, `update_user`, `deactivate_user`, `reactivate_user`, `delete_user`, `reset_password`, following `lists/service.py` (`select_for_update`, snapshots, `record`).
- `staff/views.py` -- `finish_login` :162 (the must-change check), `_pending_login_view` :140, `_account_unchanged` :111 (refresh the pending hash after the change).
- `staff/signin.py` -- `reset_failures` :165, `single_session` :262 (`end_all_sessions`).
- `staff/backends.py:11` -- inactive users are already refused.
- `lists/sample.py` (list :80, form :93, create :166, edit :179, deactivate :202, `ChangeColumn` :46) -- the template for the users views. `lists/views.py` AdminListView.
- `permissions/urls.py:12` `IMPLEMENTED_AREAS` (+ `admin_users`), `permissions/tests/test_matrix.py:210,309`.
- `audit/registry.py` `AUDITED_VIEWS` and `audit/tests/test_audit.py:700,746` `EXERCISES` for the new views.
- Entry 5's `fresh_code_required` on create and reset.
- `staff/tests/test_users.py:163` (never hard-deleted stays), :183 (migrations complete).

## Tasks & Acceptance

**Execution:**
- [ ] the model fields plus migration; the `staff/service.py` functions
- [ ] the `admin_users` list, the create/edit/deactivate/reactivate/delete/reset views, and the one-time temporary password page
- [ ] the must-change step in sign-in, plus the shared `PasswordChangeForm`
- [ ] tests for every matrix row; the audit guard registry; the README "Admin users" section

**Acceptance Criteria:**
- Given admindev after deploy (user), when the Sysadmin creates a Clinical User, resets their password and gives it to them out of band, then the user signs in with password and code, must set a new password, and lands on their dashboard. Deactivating them blocks sign-in, and each step is in the Audit log.

## Verification

**Commands:**
- mytp-admin suite (CI env), `check`, `makemigrations --check` -- pass. CI on PostgreSQL -- green.

**Manual checks (if no CLI):**
- admindev (user): the acceptance criterion.

## Implementation Notes

- Remembered devices (deliberate change): the mark is now bound to the password hash and the email's blind index (`staff.signin.AdminRememberedDevices`). Without this, editing the email can't end the remembered devices, as the email-edit decision requires. Side effect: every mark given before this deploy stops working once (each user enters one more code). Documented in the README sign-in section.

## Plan Change Log

## Review Triage Log

### Pass 1 (quick, security focus, 2026-11-03): high 0, medium 2, low 5, false 0, maybe-false 0

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | A reset cleared the counters but not mytp-core's separate lock key, so a locked-out user stayed locked up to 10 min and couldn't use the temporary password | medium | patch | Real (lockout.reset vs unlock). Patched: `signin.clear_lockout` also unlocks by account and email; test signs in with the temporary password right after. |
| 2 | The must-change save didn't re-check under the row lock, so it could overwrite a newer reset or change a deleted user's password | medium | patch | Patched: `change_own_password(..., expected_hash)` re-checks hash, flag, active and not deleted; refusal drops the pending login; tests. |
| 3 | Two Sysadmins changing each other could deadlock (opposite lock order) → 500 | low | patch | Patched: target and all active Sysadmins locked in one `select_for_update` ordered by pk. |
| 4 | Concurrent same-email create/edit raised IntegrityError → 500 | low | patch | Patched: caught and shown as the duplicate-email error; a failed create gives the grant back; tests. |
| 5 | Every existing remembered device stops working once at deploy, undocumented | low | patch | Documented in README and Implementation Notes as the deliberate cost of the email-edit decision. |
| 6 | `admin_users_new` (VIEW + custom check) escaped the CHANGE-view audit guard | low | patch | Patched: `area_required(AREA, CHANGE)` plus an explained `NOT_AUDITED` exemption the guard test checks. |
| 7 | `create` lacked `@sensitive_variables('password')` | low | patch | Added. |

Verified after the patches: mytp-admin suite 562 passed (SQLite, CI env), `check` clean, `makemigrations --check` no changes.
