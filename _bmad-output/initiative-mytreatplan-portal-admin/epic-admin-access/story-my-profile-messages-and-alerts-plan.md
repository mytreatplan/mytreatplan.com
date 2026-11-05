---
title: 'My profile, messages and alerts'
type: 'feature'
ticket: '11'
created: '2026-11-04'
status: 'built'
baseline_revision: '6dc2ddf619e1570903564f58bcb2465a42a29cf2'
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

**Problem:** A9: admin users cannot see or edit their own data or change their own password, and the lockout alert to Sysadmins (entry 4) only reaches Sentry; the "My profile" and "Inbox" menu items are placeholders.

**Approach:** Two pages for every role. My profile shows the user's data, lets them edit their own fields and time zone, and change their password with entry 9's form. The Inbox is a list of in-app messages with an unread badge in the top bar; a small `inbox.post(recipients, subject, body)` API lets other code post to it. The first poster is the lockout notifier: when an admin account is locked, each active Sysadmin gets an unread alert.

## Boundaries & Constraints

**Always:**
- Role and regions stay Sysadmin-only (3.9's form). Profile edits go through a new service function in `staff/service.py` that accepts only the profile fields, under the row lock, audited with `audit.service.record` (action per the existing `Action` choices; a new choice only if none fits, with its migration).
- The own password change reuses `PasswordChangeForm` (`staff/forms.py:138`) plus a current-password check; a new service function (not `change_own_password`, which requires `must_change_password`). On success: the password is set, the user's other sessions end (`signin.single_session`), this session stays signed in (`update_session_auth_hash`), audited without the password.
- Inbox: a new model (UUID pk, recipient FK to the staff user, subject, body, created_at, read_at null). Opening a message marks it read. "Mark all as read" with a confirmation. A user sees only their own messages. The list follows `AdminListView` conventions (newest first, `date_field` filter).
- Badge: the unread count in the top bar user block (`layout/templates/layout/base.html:23-33`), from a context processor, one COUNT query, hidden at 0.
- Lockout alert: posted from `staff.signin.alert` (account branch, :72-77) in its own transaction, after the existing Sentry warning, to every active, non-deleted Sysadmin (the locked user included if a Sysadmin). Subject "Account locked: {display name}", body with the time (UTC) and the 10-minute lock. A failure to post is reported to Sentry and never breaks sign-in.
- `profile` and `inbox` join `IMPLEMENTED_AREAS` (`permissions/urls.py`) with URLs named `profile` and `inbox`; every view is listed in `audit/registry.py` (`AUDITED_VIEWS` or `NOT_AUDITED`).

- Decision (user, 2026-11-04): the user edits only display name, title and time zone; email and mobile are shown read-only (a Sysadmin changes them in 3.9's form).
- Decision (user, 2026-11-04): the own password change asks for the current password, then the new one twice.
- Decision (user, 2026-11-04): pages show dates in the user's time zone (a middleware activates `user.time_zone` for signed-in users; lists' `CreatedColumn` and other date columns follow it); the Audit log keeps UTC explicitly (its `|utc` templates and labels stay).
- Decision (user, 2026-11-04): a wrong current password is only a form error; it does not count toward the sign-in lockout.

- Decision (user, 2026-11-05, after review): the lock alert names the locked user by display name with a link to their Admin users page; it never contains the email.
- Decision (user, 2026-11-05, after review): a demoted Sysadmin keeps their past alerts (they hold only a name and a time).
- Decision (user, 2026-11-05, after review): 5 wrong current passwords in a row in the own password change sign the session out (counted per session, reset by a right one; still never counted toward the sign-in lockout); the "not your current one" check runs only when the current password is right.

**Never:**
- No email/SMS/push notifications (decision 2026-10-30: in-app only). No message deletion by users. No change to mytp-core.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Edit profile | valid fields | saved, audited, message shown | form errors re-render |
| Wrong current password | password change | refused, nothing changed | form error; not counted toward the lockout |
| New password = current / weak | password change | refused | entry 9's form errors |
| Lockout of a non-Sysadmin | 5th failure | one unread alert per active Sysadmin; badge shows count | post failure → Sentry only |
| Lockout of a Sysadmin | 5th failure | the alert reaches every active Sysadmin, the locked one included | — |
| Open a message | unread | marked read; badge drops by 1 | another user's message → 404 |
| Mark all as read | confirm | all own messages read; badge hidden | — |

</frozen-after-approval>

## Code Map

- Repository: the mytp-admin worktree `/Users/jadeandres/Library/CloudStorage/GoogleDrive-mytpdso@gmail.com/Mi unidad/Desarrollo Portal MyTreatplan/code/mytp-admin-3.11` (branch `story/3.11-profile-inbox`); all paths below are relative to it. Do not touch the `mytp-admin` checkout (story 3.10 is in progress there). Test venv: `/private/tmp/claude-501/admin-venv/bin/python -m pytest -q -p no:cacheprovider` with `SECRET_KEY=ci-only-not-a-secret DEBUG=False SECURE_SSL_REDIRECT=False ALLOWED_HOSTS=localhost,127.0.0.1,testserver DATABASE_URL=sqlite:///$TMPDIR/adm311.sqlite3 FIELD_ENCRYPTION_KEYS=test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE= BLIND_INDEX_KEY=dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU= ALLOW_FAKE_VERIFICATION=True` (sandbox off). This plan file stays where it is (the planning worktree); record Implementation Notes here.
- `staff/models.py` -- `User`: `display_name`, `title` :121-122, `phone` (encrypted, `phone_index` unique when set) :115-120, `time_zone` (default Europe/Madrid, validator) :124-125, `email`/`email_index` :111-114, `role` :126, `must_change_password` :130; audit-excluded fields :139-140.
- `staff/service.py` -- `_locked` :86, `update_user` :140 (`EDITABLE_FIELDS` :42 includes role/regions — do not reuse for the profile), `change_own_password` :259 (forced change only). Add `update_profile(user, **values)` and `change_password(user, current, new)`.
- `staff/admin_users.py:201-260` -- `AdminUserForm`: field widgets and the mobile normalisation to mirror in a `ProfileForm`.
- `staff/forms.py:138-183` -- `PasswordChangeForm` (new twice, refuses the current one).
- `staff/views.py:310-338` -- `login_password` view and `layout/login_password.html`, the pattern for the change page.
- `staff/signin.py` -- `alert(subject, counter, count)` :67-79 (account branch :72-77), `POLICY = ADMIN.with_notifier(alert)` :86; `single_session` helpers.
- `audit/service.py` -- `record(actor, action, obj, before, after, always=False)` :238, `snapshot` :123; must run inside `transaction.atomic()`. `audit/models.py:90-99` Action choices. `audit/registry.py:14-33` -- CI fails if a CHANGE view is not listed.
- `permissions/areas.py` (Mine: `profile`, `inbox`), `permissions/matrix.py:58-61` (`_COMMON`), `permissions/urls.py` `IMPLEMENTED_AREAS`, `permissions/context_processors.menu`, `area_required`.
- `lists/views.py:95-299` `AdminListView` (attributes documented :96-120), `lists/tables.py:17-25,70`, `lists/filters.py:43,56-82`; example list `staff/admin_users.py:74-183`; confirmation via `hx-confirm` (`staff/templates/staff/_admin_users_form.html:8`).
- `layout/templates/layout/base.html` -- top bar user block :23-33, sidebar :47-61.
- `mytp_admin/settings.py` -- TEMPLATES context processors :214-219; `TIME_ZONE='UTC'` :305-310.
- Tests: `conftest.py` `make_staff_user` :68, `staff_user` :83; `staff/tests/conftest.py` `clock`, `sysadmin`, `sentry_messages`; lockout tests `staff/tests/test_sign_in.py`. The site is English only (no i18n).

## Tasks & Acceptance

**Execution:**
- [x] New app `inbox/` (model, migration, `post()` API, list + detail + mark-all views, templates, context processor) -- the inbox.
- [x] `staff/service.py`, `staff/forms.py`, `staff/views.py` (or `staff/profile.py`), templates -- My profile and the own password change.
- [x] `staff/signin.py` -- post the lockout alert.
- [x] `permissions/urls.py`, `mytp_admin/urls.py`, `mytp_admin/settings.py`, `audit/registry.py`, `layout/templates/layout/base.html` -- wiring, badge.
- [x] Tests for every matrix row, in `inbox/tests/` and `staff/tests/test_profile.py`.

**Acceptance Criteria:**
- Given any role, when it opens My profile and Inbox from the menu, then both pages load (no placeholder).
- Given a profile edit, when saved, then the audit log has one entry for it with before/after of the changed fields only.

## Implementation Notes

Implemented 2026-11-04 in `mytp-admin-3.11` (branch `story/3.11-profile-inbox`, uncommitted). Suite: 656 passed (624 before + 32 new), `manage.py check` clean, `makemigrations --check` clean.

- **Inbox app** (`inbox/`): `Message` (UUID pk, `recipient` FK PROTECT, `subject` 200, `body`, `created_at`, `read_at`; indexes on (recipient, read_at) and (recipient, -created_at)), migration `0001_initial`. `inbox.post(recipients, subject, body)` is a lazy wrapper in `inbox/__init__.py` over `inbox.service.post` (de-duplicates recipients, cuts the subject, one transaction, bulk create). `service.mark_read` keeps the first read time; `mark_all_read` touches only the user's own unread rows.
- **Views/URLs**: `inbox` (AdminListView, VIEW; own rows only via `get_base_queryset`, newest first, `date_field='created_at'`, search on subject, Unread/Read filter; template `inbox/list.html` extends `lists/list.html` and adds "Mark all as read" with `hx-confirm`, shown only while unread > 0), `inbox_message` (`inbox/<uuid>/`, GET only, CHANGE, 404 for another user's message, marks read), `inbox_mark_all_read` (POST, CHANGE, HX-Redirect/303 to the list).
- **Badge**: `inbox.context_processors.unread` returns a memoized callable, so the COUNT runs once per full-page render and never on an htmx partial; `base.html` top bar has an "Inbox" link with the badge (hidden at 0), and the display name now links to My profile.
- **My profile** (`staff/profile.py`): `profile` (VIEW, read-only page with full own email/mobile, role, regions, zone, created), `profile_edit` (CHANGE, `ProfileForm` with display name/title/time zone only, htmx + `hx-confirm` like the admin user form), `profile_password` (CHANGE, `OwnPasswordChangeForm` = `PasswordChangeForm` + `current_password` first, `never_cache`, `sensitive_post_parameters`). Service: `update_profile(user, **values)` (PROFILE_FIELDS only, TypeError otherwise; row lock; refuses deleted/inactive; `full_clean`; one `update` entry with changed fields only, none when unchanged) and `change_password(user, current, new)` (row lock; current re-checked; refuses deleted/inactive; `update` entry with `always=True`, empty before/after).
- **Sessions after the own password change**: the view calls `update_session_auth_hash`; mytp-core's `SingleSession._ended_meanwhile` makes the cycled key the account's current one, so this session stays and any other session (and the old key) is ended; remembered devices are forgotten through the changed auth hash. I did NOT call `single_session.end_all_sessions` — it would delete the current session and clear the current key, which signs this session out too (core's middleware then refuses the cycled key). Tested: old cookie → login page, new key == `current_session_key`.
- **Wrong current password**: form error only (`clean_current_password`); nothing touches `signin.failures` (test: 6 wrong attempts, no lock).
- **Lock alert** (`staff.signin.post_lock_alert`, called from `alert`'s account branch after the Sentry warning): own `transaction.atomic()`, recipients = active, non-deleted Sysadmins (locked user included if one); subject `Account locked: {display name}`; body names the email, the time `YYYY-MM-DD HH:MM UTC` (from `signin._now()`), and `POLICY.lock_seconds // 60` minutes. Any exception → `report_error('lock_alert', …)` (Sentry error "Admin sign-in counter error: lock_alert (<Class>)", no personal data); sign-in continues. Unknown-email locks post nothing.
- **Time zones**: `layout.middleware.UserTimeZoneMiddleware` (after `SessionLimitMiddleware`) activates `user.time_zone`, UTC otherwise. `CreatedColumn` renders in the active zone, `CreatedColumn(utc=True)` for the audit log; the CSV `value()` stays UTC with seconds (the export file names say `-UTC.csv` and the existing CSV tests expect UTC) — a deliberate reading of "other date columns follow it" as on-page only. Detail pages (admin user, list demo, inbox message) show `YYYY-MM-DD HH:MM (Zone)` via `lists.tables.local_time_with_zone`. The date-range help is now "Whole days in your time zone, as the times in the table."; `AdminListView.times_in_utc = True` (audit log) keeps the UTC wording. Audit `_history.html` (`|utc`) and entry detail stay UTC.
- **Audit registry**: `profile_edit`, `profile_password` in AUDITED_VIEWS (with exercises in `audit/tests/test_audit.py`); `inbox_message`, `inbox_mark_all_read` in NOT_AUDITED (read receipts, no domain row). The read-only views (`profile`, `inbox`) are VIEW-guarded, and the registry by design lists CHANGE views only (`test_every_change_view_is_audited` asserts equality), so they are in neither set; the registry docstring says so.
- **Tests updated**: `permissions/tests/test_matrix.py` (IMPLEMENTED_AREAS), `lists/tests/test_lists.py` (help text), `audit/tests/test_audit.py` (exemptions + exercises). New: `inbox/tests/test_inbox.py`, `staff/tests/test_profile.py` (incl. every role opens both pages from the menu).
- **Risks / follow-ups**: Not yet run on PostgreSQL locally (CI will). Manual admindev checks still owed.
- **Review pass 1 fixes (2026-11-05)**: (1) The lock alert no longer has the email. It names the user by display name and points to their Admin users page through a new optional `Message.link` field: `inbox.post(..., link='')` accepts only an absolute path on this site, else ValueError. The field was added to migration `0001_initial` in place, since it was never released. The message page shows it as a link. (2) `PasswordChangeForm.may_compare_with_current()` is a hook that is always True for the forced change. `OwnPasswordChangeForm` returns True only when the current password was right, so a wrong-current POST never runs `check_password` on the new password. (3) `staff/profile.password` counts wrong current passwords in the session key `staff_profile_wrong_current`. The 5th in a row logs out and redirects to `/login/` with a message. A right current password resets the count, even when the new password is refused. The sign-in lockout counters are untouched. (4) `inbox_message` is `require_GET`, so HEAD gets 405 and does not mark the message read. Tests were added or updated in `inbox/tests/test_inbox.py` and `staff/tests/test_profile.py`.

## Plan Change Log

- 2026-11-05, review pass 1: findings 3 and 5 were intent gaps; the user decided (frozen block, "after review" decisions) and the frozen lock-alert text no longer mentions the email. Patched in place, no re-derivation: KEEP everything else as built (sessions via the auth-hash change, the registry covering CHANGE views only).

## Review Triage Log

Pass 1 (quick lens, opus, 2026-11-05): 6 findings. high 0, medium 2, low 2, false 2, maybe-false 0.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | `profile` and `inbox` (read-only) are in neither `AUDITED_VIEWS` nor `NOT_AUDITED`, against the plan's "every view is listed" | false | rejected | The registry covers CHANGE views only and its test asserts that set exactly; the plan's wording overreached. Every CHANGE view of the story is listed. |
| 2 | Other sessions are not ended through `signin.single_session` as the plan says | false | rejected | The outcome holds: `update_session_auth_hash` cycles this session's key and the new password hash fails every other session's check (mytp-core 0.5.0 `sessions.py`); `end_all_sessions` would also end this one. Tests cover both. |
| 3 | The lock alert stores the locked user's email in plain text in `inbox_message.body`, kept forever | medium | intent_gap → resolved | User decision 2026-11-05: the alert names the display name with a link to the user's Admin users page, no email. Patched by the implementer. |
| 4 | A demoted Sysadmin keeps past lock alerts | low | intent_gap → resolved | User decision 2026-11-05: accepted, since with #3 an alert holds only a name and a time. No change. |
| 5 | Unlimited current-password guesses; the "not your current one" check tests a second guess per request | medium | intent_gap → resolved + patch | User decision 2026-11-05: 5 wrong current passwords in a row sign the session out (not counted toward the sign-in lockout). The unchanged-password check is skipped when the current password is wrong. Patched by the implementer. |
| 6 | `message_detail` uses `@require_safe`, so a HEAD request (prefetch) marks a message read | low | patch | Real. Patched: GET only. |

## Verification

**Commands:**
- the admin suite (admin test venv, see staging-ops notes) -- expected: all pass.

**Manual checks (if no CLI):**
- On admindev: edit the profile and time zone, change the password; lock a test account (5 wrong passwords) and see the badge and alert as a Sysadmin; opening it drops the badge.
