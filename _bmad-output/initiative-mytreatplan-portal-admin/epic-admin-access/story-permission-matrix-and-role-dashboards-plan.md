---
title: 'Permission matrix and role dashboards'
type: 'feature'
ticket: '6'
created: '2026-11-02'
status: 'built'
baseline_revision: '30c9392b98257911db37097b93debe6dfd63ea63'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: [] # code repo: mytp-admin (sibling of mytreatplan.com); baseline_revision is a mytp-admin commit. Requirements: ../MyTP_Reqs_Def_V1_21Oct2026_v3.docx "The Admin Site" (paragraphs on roles and areas).
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A3 and A4 / AD-17: admin staff must reach only what their role allows. That needs one permission matrix (role × area × action) used by every view and menu, with region limits and an "assigned only" scope that later epics and the remote-access token rely on, plus a dashboard frame per role.

**Approach:** In mytp-admin, add a `permissions` module that holds the matrix as plain data (strings and frozensets) plus the check functions. A view decorator returns 403 when a role lacks an area or action. The sidebar menu is built from the matrix and an area registry. `access_scope(user)` returns a token-serialisable `{"sub", "role", "regions"}` shape. Each role gets a dashboard frame with a widget slot. Every area in the matrix has a placeholder page now, which the later epic replaces.

## Boundaries & Constraints

**Always:**
- **One source.** Views, menus and later the token read only `permissions`. Nothing uses `user.has_perm` or groups, and staff.User stays without PermissionsMixin.
- **Actions:** `view`, `change`, `export`. Later epics may add named actions (for example `assign`, `mark_paid`, `download_records`) to the same matrix. A role has an action only if the matrix lists it, so the default is deny.
- **Areas now** (the code name is its menu label key; parked areas get no row):
  - common to all roles: `profile`, `inbox`, `my_calendar`, `my_tickets`;
  - Admin: `admin_users`, `audit_log`, `support`;
  - Sales: `catalogue`, `clients`, `sales_orders`, `vouchers`, `contact_messages`, `sales_reports`;
  - Financial: `accounting`, `financial_reports`;
  - Clinical: `patients`, `work_orders`, `calendars`, `team_calendar`, `clinical_reports`.
- **Matrix, as stated in the docs** (docx "The Admin Site"; epic notes):
  - Sysadmin: every area, all actions.
  - Financial Manager: clients, sales_orders, vouchers, sales_reports, accounting and financial_reports with view, change and export, and catalogue view only. All regions, and no clinical or admin areas.
  - Commercial Manager: catalogue, clients, vouchers, contact_messages and sales_reports with view, change and export; sales_orders view and export only (marking paid and cancelling stay with the Financial Manager and Sysadmin). No financial, clinical or admin areas.
  - Commercial Regional Manager: the Commercial Manager's rows, limited to their regions, except catalogue view only (global rows have no region).
  - Clinical Manager: clients (the clinical view), patients, work_orders, calendars, team_calendar and clinical_reports with view, change and export. No sales, financial or admin areas.
  - Clinical User: work_orders, clients and patients with view and change, scope assigned only (export is never on patient data, AD-23).
  - Every role: the common areas. `support` is Sysadmin only for now; epic-support may widen it.
- **Scope per role and area:** `all`, `regions` (the Commercial Regional Manager: the client's home region must be one of the user's `Region.code` values), or `assigned` (the Clinical User: assignee == the user's UUID). The `scope_for(user, area)` helper returns it. epic-clinical-ops and epic-admin-interface apply it.
- **`access_scope(user)`** returns `{"sub": str(uuid), "role": value, "regions": [codes]}`, where unlimited roles get every registry code, so the instance never needs an "all" special case.
- A 403 uses a plain AdminLTE page inside the layout, and `NoindexMiddleware` still adds X-Robots-Tag.
- **Dashboard:** `home` renders the role's frame with the role heading (keep the `<h1>` the tests read) and a widget slot. Widgets register per role through a small registry (`register_widget(role, template, order)`), and it's empty now.
- **Placeholder pages:** every area is in the menu now, with one URL per area, guarded by the decorator, saying "This area arrives with <epic>".

- Decision (user, 2026-11-02): Commercial Manager on sales_orders view and export only; Commercial Regional Manager on catalogue view only; Financial Manager on catalogue view only; support area Sysadmin only for now; areas without content are shown in the menu with a placeholder page.

**Never:**
- No real area content, lists (entry 7), audit (8), user management UI (9), or token issuing (epic-admin-interface).
- No field-omission rules (applied by the instance from the token, a later epic). No change to `Role` values or `validate_access`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Menu | each of the six roles signed in | the sidebar lists exactly the areas the matrix gives that role | — |
| Allowed area | a role opens one of its areas | 200, the placeholder page | — |
| Other area | a role opens an area it lacks | 403 page with X-Robots-Tag | — |
| Anonymous | any area URL | 302 to login | — |
| Inactive user | `is_active=False` | cannot sign in (unchanged) | — |
| Missing action | `has_access(user, area, 'export')` where not listed | False | unknown area or action raises in tests (typo guard) |
| CRM scope | CRM with eu | `scope_for` → regions; `access_scope` regions == ["eu"] | — |
| Sysadmin scope | Sysadmin | `access_scope` regions == all registry codes | — |
| Clinical User scope | `scope_for(user, 'work_orders')` | assigned | — |
| Dashboard | each role on `/` | frame with its heading and an empty widget slot | — |

</frozen-after-approval>

## Code Map

All in `mytp-admin/`:
- `staff/models.py` -- `Role` :41–50 (its docstring points to entry 6), `REGIONAL_ROLES` :55, `Region` :25–38 (code pk, `eu`/`ae`), `User.role` :~80, `regions` M2M :84, `validate_access` :110. Read these; don't change them.
- New `permissions/` app or `staff/permissions.py` -- the matrix data, `has_access`, `scope_for`, `access_scope`, the `area_required(area, action='view')` decorator, the area registry (code, label, URL name, menu group, epic), and the widget registry.
- `layout/views.py:9–13` `home` -- the dashboard frame. `layout/templates/layout/home.html:3,6,8–13` -- keep the `<h1>{{ user.get_role_display }}`, and add the widget slot.
- `layout/templates/layout/base.html:46–51` `{% block menu %}` -- loop over the menu items from a context processor (`mytp_admin/settings.py:166–170`), grouped Admin / Sales / Financial / Clinical / Mine.
- `mytp_admin/urls.py:11–27` -- add the area placeholder URLs (`areas/<code>/` or per-area paths) and a `handler403`, or a 403 template.
- Tests: the `sysadmin` fixtures in `layout/tests/test_pages.py:17–20` and `staff/tests/test_sign_in.py:11`, and `make_user` in `staff/tests/test_users.py:20`. Add a shared per-role fixture in `conftest.py`, parametrised over `Role`. The matrix test must cover every role × area. Keep `test_pages.py:41,85` and `test_users.py:28,36` passing.

## Tasks & Acceptance

**Execution:**
- [x] permissions module, registries, decorator, context processor, placeholder views and URLs, 403 page -- the matrix as decided
- [x] `home` dashboard frame with widget slot
- [x] tests: every role × area (menu and URL status), the scope helpers, typo guard, dashboard per role

**Acceptance Criteria:**
- Given admindev after deploy (user, with the 3.1 Sysadmin plus one test user per role created by `create_staff_user` in the shell), when each signs in, then the menu shows only its areas, and another area's URL gives 403.

## Verification

**Commands:**
- mytp-admin suite (CI env), `check`, `makemigrations --check` -- pass, no changes

**Manual checks (if no CLI):**
- admindev (user): sign in as two different roles; compare the menus; open a forbidden URL and get 403.

## Implementation Notes

- New app `permissions/` (mytp-admin): `areas.py` (registry: code, label, menu group, epic; URL name = code, path `/<code-with-hyphens>/`), `matrix.py` (MATRIX as read-only plain data, `Grant(actions, scope)`, validated at import; `has_access`, `scope_for`, `grant_for`, `areas_for`, `access_scope`, `UnknownPermission`), `decorators.py` (`area_required`, checks area/action at decoration time), `context_processors.py` (`menu`), `widgets.py` (`register_widget`, `widgets_for`, `unregister_widget`), `views.py` + `urls.py` (one placeholder per area, `require_safe`), template `permissions/placeholder.html`.
- 403: `layout/templates/403.html`, rendered by Django's default `permission_denied` view (it renders with the request, so the menu shows); no `handler403` needed.
- Judgement calls (not stated in the plan): the common areas get view and change, scope `all` for every role (Sysadmin gets all three actions everywhere, as stated); the typo guard raises always, not only in tests; area epics: profile/inbox/admin_users/audit_log epic-admin-access, my_calendar epic-appointments, my_tickets/support epic-support, catalogue/vouchers epic-catalogue, the other Sales and Financial areas epic-sales-finance, the Clinical areas epic-clinical-ops; `clients` sits in the Sales menu group, so Clinical Manager and Clinical User see a Sales header with Clients only.
- `access_scope` decides "limited" by `staff.models.REGIONAL_ROLES`.
- Decision (user, 2026-11-02): "Clients are under sales but also shown under clinical." An area may belong to several menu groups (`Area.groups`); `clients` is in Sales and Clinical. The menu shows a multi-group area in each of its groups where the role has another area too (else its first group): Financial, Commercial and Commercial Regional Managers see it under Sales, the Clinical Manager and Clinical User under Clinical (no Sales group), the Sysadmin under both. URL, matrix rows and scope unchanged. This replaces the note above about a lone Sales header.
- Tests: `conftest.py` gains `make_staff_user` and `staff_user` (parametrised over Role); `permissions/tests/test_matrix.py` checks every role x area against a hand-written table, every role x area URL (200 or 403 with X-Robots-Tag), the menu per role, scopes, the typo guard and the dashboard per role. Local suite (SQLite): 270 passed (86 before); `check` and `makemigrations --check` clean.

## Plan Change Log

## Review Triage Log

### Pass 1 (quick, 2026-11-02): high 0, medium 1, low 2, false 1, maybe-false 0

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | `access_scope()` skipped the anonymous/inactive check the other helpers make: an inactive user got a full scope, AnonymousUser raised AttributeError | medium | patch | Real: it read `user.role` directly; it is the token's future source. Patched: returns None like the others; test added. |
| 2 | README per-role shell snippet set passwords without the manager's documented validation | low | patch | Patched: asks twice, compares, runs `validate_password` first. |
| 3 | `permissions/urls.py` told epics to drop areas from `PLACEHOLDER_AREAS`, which was the registry itself | low | patch | Patched: explicit `IMPLEMENTED_AREAS = frozenset()`; placeholders are AREAS minus it; test added. |
| 4 | The admindev acceptance check is not recorded | false | rejected | It is the manual post-deploy step (user), recorded at completion as for 3.1. |

Verified after the patches: mytp-admin suite 272 passed (SQLite, CI env), `check` clean, `makemigrations --check` no changes.
