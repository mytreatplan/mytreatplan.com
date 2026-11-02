---
title: 'List conventions'
type: 'feature'
ticket: '7'
created: '2026-11-02'
status: 'built'
baseline_revision: '854f337ba6ab3de38a71af7c8663fd190114f282'
route: 'full'
route_source: 'auto'
risk: 'low'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: [] # code repo: mytp-admin (branch from origin/master; the local checkout may be on an old story branch). Requirements docx "The Admin Site": every list has CSV export, column ordering, a search field and fixed fields (id, creation date, status, view more); every change needs confirmation.
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A5 / AD-11: every admin list later epics build must behave the same way: sorting, search, CSV of central data, the fixed columns, region limits, and a confirmation before every change. There are no shared list components yet.

**Approach:** In mytp-admin, add a `lists` app holding the shared building blocks on django-tables2 3.0.1 (with tablib) and django-filter 26.2:
- a base `AdminTable` with the fixed columns;
- a base `AdminListView` that applies the permission matrix and the role's scope, searches through POST and exports CSV;
- an htmx confirmation dialog for every change.

They are proven on one sample list: a demo model `lists.SampleRecord` under a "List demo" area.

## Boundaries & Constraints

**Always:**
- **Access.** Every list view is guarded by `area_required(area, VIEW)`, and its rows are filtered by `scope_for(user, area)`:
  - `regions` keeps only rows whose home region is in the user's `Region.code` set;
  - `assigned` keeps only rows whose assignee is the user's UUID, applied when a model declares an assignee;
  - `all` keeps everything.
  - CSV needs `EXPORT`, and a change needs `CHANGE`. These are the existing matrix helpers, unchanged.
- **Search and filters travel only in POST bodies, never in URLs (AD-11, AD-23).** Sorting, paging, filtering and CSV export are htmx POSTs that carry the form state (`hx-post` + `hx-include`). The table is re-rendered as a partial. A GET shows the list unfiltered. Nothing personal goes in a querystring, a log or a flash message.
- **Fixed columns** on every `AdminTable`: internal id, created, status and "view more". A model without created or status shows "—" ("when available", docx). The "view more" link goes to the record's detail URL, or is hidden when there is none.
- **Sorting:** on every column that maps to a field, by POSTing the sort key. The page size is 25.
- **CSV:**
  - Only for lists the view marks `central_data=True`. AD-11 allows no CSV of regional-only data.
  - It holds the same filtered, scoped rows and the same columns minus "view more".
  - The file is UTF-8 with a BOM (for Excel), named `<area>-<YYYYMMDD>-UTC.csv`.
  - Cells starting with `= + - @`, tab or CR get a leading `'` (formula injection).
  - It's capped at 10,000 rows, beyond which the export is refused with a message.
- **Confirmation:** every change form or button uses `hx-confirm`, shown as a Bootstrap 5 modal by a small static script (`static/lists/confirm.js`, no inline JS). `<body>` carries `hx-headers` with the CSRF token. The change only POSTs after the user confirms.
- **Libraries:** `django-tables2[tablib]==3.0.1`, `django-filter==26.2` and their pinned dependencies. django-tables2 3.0.1 declares Django 6.0, not 6.1, so the suite must prove it on 6.1.2.
- **Column ordering** means sorting by any column, ascending or descending. Column order and visibility are fixed per list.
- Decisions (user, 2026-11-02):
  - The sample list is a demo model `lists.SampleRecord` (name, created, status, home region FK to `staff.Region`), seeded by a data migration with a few eu and ae rows.
  - It sits under a new "List demo" area (code `list_demo`) in the Admin group, with view, change and export for the Sysadmin and the Commercial and Commercial Regional Managers (scope regions for the latter).
  - It is removed when the first real list (epic-sales-finance) ships.
  - "Column ordering" = sorting only.

**Never:**
- No audit-log writing (entry 8; the confirmation hook leaves the seam). No real area's list (later epics). No cross-region search.
- No change to the matrix data, `area_required`, `access_scope`, or the vendored asset pins.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Sort | POST sort=created / -created | rows ordered; the search kept | an unknown sort key is ignored |
| POST search | POST q=term | only matching rows; the URL has no term | — |
| GET | GET the list | unfiltered first page | — |
| Paging | POST page=2 with q | page 2 of the filtered rows | an out-of-range page gives the last page |
| Region scope | CRM limited to eu | only eu rows, in the list and in the CSV | — |
| Unlimited | Sysadmin | eu and ae rows | — |
| No access | a role without VIEW | 403 | — |
| CSV | a user with EXPORT, POST export with q | a BOM-prefixed UTF-8 CSV of the filtered rows with the fixed columns; filename pattern | without EXPORT: 403; over 10,000 rows: refused, with a message |
| Formula cell | a value "=1+1" | exported as "'=1+1" | — |
| Change | click a change button | modal asks; Cancel sends nothing; Confirm POSTs with CSRF | a role without CHANGE gets 403 |

</frozen-after-approval>

## Code Map

All in `mytp-admin/`. Branch from origin/master.
- `permissions/matrix.py` -- `VIEW/CHANGE/EXPORT` :30-33, `ALL/REGIONS/ASSIGNED` :35-38, `has_access` :159, `scope_for` :166 (the docstring at :12-16 defines `regions`). Reuse; don't change.
- `permissions/decorators.py:8` `area_required` -- reuse for view, export and change.
- `permissions/urls.py:12` `IMPLEMENTED_AREAS` and `permissions/areas.py:43-69` the registry -- add the `list_demo` area (Admin group; label "List demo"; epic "epic-admin-access"), its matrix rows, and its code in `IMPLEMENTED_AREAS` with a real URL; update the 3.6 hand-written matrix and menu test tables for it.
- `staff/models.py` -- `Region` :26 (code pk eu/ae), `User` :59 (no created or status, only `is_active`), `User.regions` :88 (the CRM's access, not a row's home region).
- `layout/templates/layout/base.html:85-87` -- bootstrap, adminlte and htmx (`static/vendor/htmx/htmx.min.js`, 2.0.11, pinned and tested in `layout/tests/test_pages.py:93-108`). Add the `hx-headers` CSRF on `<body>`, a modal container, and the `confirm.js` script tag.
- `mytp_admin/settings.py` -- `INSTALLED_APPS` :147-166 (add `django_tables2`, `django_filters`, `lists`); `CsrfViewMiddleware` :182; no CSP.
- `requirements.txt` -- add the pins. `pytest.ini` testpaths gets `lists`.
- `conftest.py` -- `make_staff_user(role, regions=…)` :68, `staff_user` parametrised :82.
- tables2 sorts and pages through GET links by default, so render the sort headers and the pager as htmx POST controls (a custom template, `lists/table.html`).

## Tasks & Acceptance

**Execution:**
- [ ] `lists/` app: `AdminTable`, `AdminFilterSet` base, `AdminListView` (scope, POST search, sort, paging, CSV), the templates (table partial, pager, sort headers, the confirmation modal), `static/lists/confirm.js`
- [ ] the sample list (`SampleRecord`, data migration seed, `list_demo` area with a change action, e.g. toggling status, behind the confirmation); settings, requirements, base.html CSRF and modal hooks
- [ ] tests for every matrix row; the README "Lists" section (how a later epic builds a list)

**Acceptance Criteria:**
- Given admindev after deploy (user), when the Sysadmin and a Commercial Regional Manager limited to eu open the sample list, then the Sysadmin sees eu and ae rows and the CRM only eu, both in the list and in the CSV. A search term never appears in the address bar. A change asks for confirmation first.

## Verification

**Commands:**
- mytp-admin suite (CI env), `check`, `makemigrations --check` -- pass. CI on PostgreSQL -- green.

**Manual checks (if no CLI):**
- admindev (user): the acceptance criterion; open the CSV in Excel or Numbers (accents shown correctly).

## Implementation Notes

## Plan Change Log

## Review Triage Log

### Pass 1 (quick, 2026-11-02): high 0, medium 1, low 6, false 0, maybe-false 0

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | An invalid search or filter (q > 100 chars, status=bogus) was dropped by django-filter, returning every scoped row in the list and the CSV without warning | medium | patch | Real (reproduced). Patched: an invalid form shows no rows plus a warning naming only the field labels; the export is refused the same way; tests. |
| 2 | The status toggle flipped the current status instead of applying the confirmed one | low | patch | Patched: posts `new_status`, validated, applied exactly; repeat is a no-op; test. |
| 3 | `FIXED_COLUMNS` unused: a subclass could drop a fixed column | low | patch | Patched: `AdminTable.__init__` raises ImproperlyConfigured; tests (a `sequence` cannot drop one, tables2 re-adds it). |
| 4 | A refused export left the browser on the POST-only `/list-demo/export/` | low | patch | Patched: 303 to the list with a term-free warning; test. |
| 5 | After a session ended, htmx swapped the login page into the table; a 403 showed nothing | low | patch | Patched: `HtmxLoginRedirectMiddleware` answers `HX-Redirect` to login; confirm.js shows an alert on a 403; tests for the server part. |
| 6 | No browser test exercises the modal and the POST-only flow | low | defer | mytp-admin has no browser-test setup; logged in deferred-work.md; verified once by the implementer's Chromium run. |
| 7 | `csv_safe` let LF-leading and space-prefixed formula cells through | low | patch | Patched: prefix on leading tab/CR/LF or first non-blank `= + - @`; tests. |

Verified after the patches: mytp-admin suite 409 passed (SQLite, CI env), `check` clean, `makemigrations --check` no changes.
