---
title: 'Audit log'
type: 'feature'
ticket: '8'
created: '2026-11-02'
status: 'built'
baseline_revision: '012c92244774772d5e361ff7fd9ec508c80b0fd9'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: [] # code repo: mytp-admin (branch from origin/master after a fetch; master includes 3.7's lists app). Architecture: every write goes through the app's service.py (line 27); AD-19 never hard-delete; AD-23 no patient data in logs; conventions: admin actions logged with actor type and UUID.
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A6: every change made on the admin site must be traceable: who, what, on which object, the values before and after, and when. Sensitive values must never be revealed. Entry 9 (user management) and every later epic need this.

**Approach:** In mytp-admin, add an `audit` app:
- an append-only `AuditEntry` model;
- one service function, `audit.service.record(...)`, which every write calls inside its own transaction. If the audit row can't be written, the change fails.
- snapshot helpers that mask sensitive fields;
- a Sysadmin-only "Audit log" list on 3.7's list components.

The sample list gets create and edit views, and its status change becomes "deactivate". All three go through a `lists/service.py`. `create_sysadmin` is audited with a command actor.

## Boundaries & Constraints

**Always:**
- **The model.** `AuditEntry` has:
  - `id` UUID pk, `occurred_at` (UTC), `actor_type` (user or command), `actor_id` (UUID, nullable for a command), `actor_label` (the user's display name or the command name);
  - `action` (create, update, deactivate, delete, reset_password, … as a TextChoices that later epics extend), `target` (model label, e.g. `lists.samplerecord`), `object_id` (CharField), `object_repr` (a snapshot taken at the time);
  - `before` and `after` (JSON).
  - Indexes on `occurred_at`, `(target, object_id)` and `actor_id`.
- **Append-only.** Saving an existing entry, `delete()`, and the queryset `update()`/`delete()` all raise. There is no edit or delete view. Rows are kept for 5 years: a daily job (`pruneaudit` in `deploy/jobs.conf`, a management command) deletes entries older than that through a dedicated, explicitly allowed path. That's the only deletion.
- **`record(actor, action, obj, before, after)`:**
  - It must run inside the caller's `transaction.atomic()` (it checks `connection.in_atomic_block`), so the change and its audit row commit together or not at all.
  - `actor` is a staff user or `audit.service.command_actor('<name>')`.
  - An update with no changed field records nothing.
- **Snapshots.** `snapshot(obj)` holds the concrete fields plus the M2M ids (e.g. `regions`), as JSON-safe values. Masking:
  - every `mytp_core.crypto.encrypted_fields(model)` field, plus a per-model `AUDIT_MASKED` set, is stored only as `"set"`, `"empty"` or `"changed"`, never the value or a hash;
  - a per-model `AUDIT_EXCLUDED` set is left out entirely. For `staff.User` that's `password`, `email_index`, `phone_index` and `current_session_key`.
  - `display_name` and `title` are plain by the 3.1 decision.
- **The service layer** (architecture line 27): views call services, and services write through the ORM and call `record`.
  - `lists/service.py`: `create_sample`, `update_sample`, `deactivate_sample`.
  - `staff/service.py`: `create_staff_user_audited`, used by `create_sysadmin`, with `command_actor('create_sysadmin')`.
  - 3.7's `AdminListView.record_change` seam is removed, and its test is updated to assert the audit row instead.
- **A guard test** walks the URL resolver. Every view with `area_action == CHANGE` must be listed in an `AUDITED_VIEWS` registry kept next to the service, and a test exercises each one. A new change view without an audit fails CI.
- **The sample list:**
  - create and edit forms (name, status, home region within the user's scope) behind the 3.7 confirmation;
  - "deactivate" replaces the toggle (a row button for every role with change). Decision (user, 2026-11-03): only the Sysadmin sees and may change the status in the edit form, so a mistaken deactivation can be undone; for other roles the form has no status and a posted status is refused. A status change through the form is audited as `deactivate` or `reactivate`, not `update`;
  - `view more` shows the record plus its audit history to Sysadmins only.
- **The audit list** (`audit_log` area, an `AdminListView`, Sysadmin only):
  - columns occurred_at, actor, action, target and object, with before and after shown on its detail page;
  - search over actor_label, action, target and object_repr;
  - the matrix grant for Sysadmin narrows to view and export (an audit log has no change action). CSV export is on for the Sysadmin (`central_data=True`); masked values are never in it.
- **Logs** carry entry UUIDs only. No personal data goes to logs or Sentry.

- Decisions (user, 2026-11-02): audit entries are kept 5 years, then pruned by a daily job. Sign-in events are not audited. The audit log has a CSV export for the Sysadmin.

**Never:**
- No user-management UI (entry 9), no emergency reset (10), no inbox (11).
- No portal imports. No database triggers. No change to `area_required` or `scope_for`.
- No audit of reads or exports. No sign-in, sign-out, lockout or refused-action entries: those stay in the logs and Sentry.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Create | a CRM creates an eu sample | 1 entry: create, before {}, after the snapshot, actor = their UUID | a home region outside the scope is refused |
| Edit | change name and home region; the Sysadmin may also change the status | 1 entry: update (or deactivate/reactivate for a status change); before and after hold only the changed fields | no change: no entry; a non-Sysadmin posting a status is refused |
| Deactivate | confirm deactivate | 1 entry: deactivate | already inactive: no entry |
| Masked | edit a staff user's email (service test) | before/after show "changed", never the email | — |
| Excluded | a staff user is created | no password, email_index or session key in the entry | — |
| Command | `create_sysadmin` | 1 entry: actor_type command, actor_label create_sysadmin | — |
| Audit fails | `record` raises inside the write | the change is rolled back | the user sees an error |
| Outside a transaction | `record` called without atomic | raises (developer error) | — |
| Append-only | save an existing entry, delete, queryset update/delete | refused | — |
| List access | each role opens /audit-log/ | Sysadmin 200; every other role 403 | — |
| Guard | a CHANGE view missing from AUDITED_VIEWS | the guard test fails | — |

</frozen-after-approval>

## Code Map

All in `mytp-admin/`. Fetch, then branch from origin/master, which should hold 3.7's merge.
- `lists/views.py:262` `AdminListView.record_change` (no-op seam) and its caller `lists/sample.py:106-122` `sample_toggle_status`, which saves only on a real change. Capture `before` before the save. `lists/tests/test_lists.py:552-566` monkeypatches the seam; update it. README :200 documents it.
- `lists/models.py` `SampleRecord` (name, created, status, home_region). Only the toggle exists, so add create and edit views and forms in `lists/sample.py` and a `lists/service.py`.
- `permissions/decorators.py:25-26` tags `.area` and `.area_action`, which the guard test uses with `django.urls.get_resolver()`.
- `permissions/areas.py:46` `audit_log`; `permissions/matrix.py` (Sysadmin's ACTIONS grant gets an `audit_log` override to VIEW + EXPORT); `permissions/tests/test_matrix.py` `EXPECTED` :29 and :208-210; `permissions/urls.py` `IMPLEMENTED_AREAS`.
- `staff/managers.py:7-19` `HardDeleteRefused` is the append-only pattern. `staff/managers.py:42-57` `create_staff_user`; `staff/management/commands/create_sysadmin.py:46`.
- `staff/models.py` -- `email` and `phone` encrypted; `email_index`, `phone_index`, `password` and `current_session_key` excluded; `display_name` and `title` plain.
- mytp-core: `mytp_core.crypto.encrypted_fields(model)` (crypto.py:525).
- `mytp_admin/settings.py` (`INSTALLED_APPS`, LOGGING :332 "UUIDs only"), `mytp_admin/urls.py` (`audit/` before `permissions.urls`), `pytest.ini`, `conftest.py` (`make_staff_user`).
- Portal pattern, read-only: `accounts/admin.py:49` `_LoggedUser` (object_repr without personal data); `eventlog/retention.py` (the daily purge job pattern for `pruneaudit`).

## Tasks & Acceptance

**Execution:**
- [x] `audit/` app: the model and migration, the append-only guards, `service.record/command_actor/snapshot/diff`, the list and detail views, and the URLs
- [x] `lists/service.py` plus the sample create, edit and deactivate views; `staff/service.py` plus `create_sysadmin`; remove `record_change`
- [x] the matrix override (view and export), `IMPLEMENTED_AREAS`, the `pruneaudit` command and its `deploy/jobs.conf` line (daily; 5 years)
- [x] tests for every matrix row plus the guard test; the README "Audit log" section (how a later epic audits a change)

**Acceptance Criteria:**
- Given admindev after deploy (user), when the Sysadmin creates, edits and deactivates a demo record, then the Audit log lists three entries with before and after values. A Commercial Manager gets a 403 on the Audit log.

## Verification

**Commands:**
- mytp-admin suite (CI env), `check`, `makemigrations --check` -- pass. CI on PostgreSQL -- green.

**Manual checks (if no CLI):**
- admindev (user): the acceptance criterion.

## Implementation Notes

- Branch `story/3.8-audit-log` in mytp-admin, at `012c922` (origin/master with 3.7's merge; the sandbox blocked a fresh `git fetch`). Uncommitted at hand-off.
- `record` refuses to run unless the caller holds its own atomic block: `in_atomic_block`, and at least one block in `connection.atomic_blocks` not marked `_from_testcase` (Django's private test flag), so a service missing its atomic block also fails under pytest-django's wrapping transaction.
- Masked fields: `snapshot` returns a `Snapshot` dict whose stored values are "set" or "empty"; the real values stay on the side in memory only, so `diff` can show "changed" for set→set with a different value. `record` also masks any real value passed in a plain dict, and drops excluded keys.
- Retention: `AuditEntry.objects.delete_for_retention(cutoff)` is the only delete path and refuses a cutoff younger than 5 years; `audit/retention.prune` runs it in batches; `pruneaudit` at 04:50 UTC daily.
- `AUDITED_VIEWS` lives in `audit/registry.py` with `change_view_names()` / `unaudited_change_views()` (walks the URL resolver for `area_action == CHANGE`); the tests have one exercise per audited view.
- The audit list reuses the fixed columns: "Created" shows `occurred_at` ("Occurred (UTC)"), "Status" shows the action.
- Sample list: `/list-demo/new/`, `/<pk>/edit/` (htmx form with `hx-confirm`, HX-Redirect when saved), `/<pk>/deactivate/` (row button, active rows only). `AdminListView.create_url_name` adds a "New" button for roles with change. The create form has no status (a new record is active). Per the 2026-11-03 decision, the edit form shows `status` only to the Sysadmin (`lists.service.may_change_status`, a `Role.SYSADMIN` check); for other roles a posted status is ignored by the form, and `update_sample` raises PermissionDenied for any `status` from a non-Sysadmin. A status change through edit records one entry with the status action (`deactivate`, or the new `reactivate`) and every changed field; other edits record `update`. Edit is shown on every row; Deactivate on active rows for every role with change.
- `confirm.js` also shows an alert on a 5xx htmx response ("the change may not have been saved. Reload the page to check."), so a failed audit write is visible to the user.
- After review, append-only hardened: `bulk_create` refuses conflict options and stamps `occurred_at`; `save()` always sets `occurred_at` to now (tests backdate with a raw UPDATE); `Meta.base_manager_name = 'objects'`. `record`/`diff` take only Snapshots (or None for no side); `_stored` checks `isinstance(value, str)`; `object_repr` defaults to `<model label> <pk>` unless the model defines `audit_repr` (added to SampleRecord and staff.User). Migration 0001 regenerated (unreleased).
- Verified: 462 passed on SQLite and on a local PostgreSQL 17 (CI env: DEBUG off, fake provider allowed); `check` and `makemigrations --check` clean.
- After merge: re-run provisioning step 3 on admindev so the `pruneaudit` timer is installed (jobs.conf changed).

## Plan Change Log

## Review Triage Log

### Pass 1 (quick, 2026-11-02): high 0, medium 2, low 7, false 0, maybe-false 0

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | The edit form offered every status, so an inactive record could be reactivated (breaking "no reactivation"), and edit-form deactivations were audited as `update` | medium | patch | Real. Patched: no status on create/edit; `update_sample` edits name and home region only; status changes only through `deactivate_sample`. The frozen I/O row "Edit: change name and status" is exercised as name and home region, because the frozen "no reactivation" rule forbids a status edit (noted for the user). |
| 2 | Append-only bypasses: `bulk_create(update_conflicts=True)` rewrote a row; `_base_manager` (plain Manager) allowed update/delete | medium | patch | Real (probes). Patched: `bulk_create` refuses conflict handling; `Meta.base_manager_name = 'objects'`; tests. |
| 3 | A row could be created already backdated, so the 5-year prune could delete it early | low | patch | Patched: `occurred_at` always set to now on insert (save and bulk_create); tests backdate through a raw UPDATE helper. |
| 4 | `_stored` raised TypeError on a list/dict value for a masked field | low | patch | Patched (`isinstance(value, str)`); test. |
| 5 | Plain-dict before/after lost "changed" for set→set | low | patch | Patched: `record`/`diff` accept only `Snapshot` or None; test. |
| 6 | `object_repr` fell back to `str(obj)`, which a later model could fill with personal data | low | patch | Patched: default `"<model label> <pk>"`; `audit_repr` on SampleRecord and User keeps their names; test. |
| 7 | The 5xx alert said "nothing was changed" even after a committed change | low | patch | Reworded: "the change may not have been saved. Reload the page to check." |
| 8 | Direct ORM/manager writes in future code (e.g. `create_staff_user`) can skip the audit; the guard covers views only | low | rejected | The plan scopes the guard to change views; the service rule (architecture line 27) is enforced by review, and entry 9 uses `staff/service.py`. |
| 9 | `QuerySet._raw_delete` deletes rows | low | rejected | A private Django API; not a supported path. |

Verified after the patches: mytp-admin suite 468 passed (SQLite, CI env), `check` clean, `makemigrations --check` no changes (0001 regenerated before release).
