---
title: 'Date and region filters on admin lists'
type: 'feature'
ticket: ''
created: '2026-11-03'
status: 'built'
baseline_revision: '29e5205fb8c48aad7f705400325cecf9b3404043'
route: 'full'
route_source: 'auto'
risk: 'low'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: [] # code repo: mytp-admin, branch from origin/master after 3.9 (PR #7) is merged. Builds on 3.7's lists app (lists/filters.py AdminFilterSet, lists/views.py AdminListView) and its users: lists/sample.py (List demo), audit/views.py (Audit log), staff/admin_users.py (Admin users).
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The user (2026-11-03, after the 3.8 check) wants to narrow admin lists by date and by region. The list search matches text fields only, so there's no way to find, for example, records created last week, or only the ae ones.

**Approach:** Extend 3.7's shared list components with two optional filters that every list can switch on with one attribute:
- a **date range** ("From"/"To") on the list's date column;
- a **region** choice on the list's region field, limited to the regions the user may see.

Turn them on for List demo (both), the Audit log (date), and Admin users (date and region).

## Boundaries & Constraints

**Always:**
- **Declared once on the list view:**
  - `date_field` is the ORM path of the column the "Created" fixed column shows: `created` on List demo and Admin users, `occurred_at` on the Audit log.
  - `region_filter_field` is the region path to filter on: `home_region` on List demo, and `regions` (the regions the user covers) on Admin users.
  - The Audit log gets the date range only.
  - A list without them shows no such filter.
- **Date range:**
  - Two optional dates, "From" and "To", both inclusive, read as whole days in the **signed-in user's time zone** (`staff.User.time_zone`), so "To 2026-11-03" includes the whole of that day.
  - "From" after "To" is a form error, which 3.7's invalid-filter rule turns into no rows plus a warning.
  - The widget is the browser's native date input.
- **Region:**
  - A single choice, "All regions" or one region, whose choices are only the regions in the user's scope. An eu-only Commercial Regional Manager sees no ae choice, and a posted `ae` is invalid, which gives no rows plus the warning.
  - It filters within the scope and never widens it.
- **Same rules as the search:** POST only, never in the URL. The filters apply to the CSV export too, and paging and sorting keep them.
- Tests for each list that uses them.

**Never:**
- No free-text date parsing in the search box, no saved filters, no other new filter types.
- No change to the matrix, the scope rules or the CSV safety rules.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| From only | From = 2026-11-02 | rows created on or after that day (user's time zone) | — |
| To only | To = 2026-11-02 | rows up to the end of that day | — |
| Range | From 11-01, To 11-02 | rows within both days | From > To: no rows plus a warning |
| Time zone | a user in Asia/Dubai; a row at 21:30 UTC on 11-02 | counted on 11-03 for that user | — |
| Region | Sysadmin picks ae on List demo | ae rows only | — |
| Region scope | eu-only CRM | only "All regions" and eu offered | posted ae: no rows plus a warning |
| Admin users region | Sysadmin picks eu | users whose regions include eu | — |
| Audit log | date range | entries in range; no region filter shown | — |
| CSV | export with the filters | the same rows as the list | — |
| URL | any filter | the address bar never changes | — |

</frozen-after-approval>

## Code Map

All in `mytp-admin/`. Branch from origin/master after 3.9's merge.
- `lists/filters.py` -- `AdminFilterSet` (the `q` search, the widget classes in `__init__`). Add the optional `date_from`/`date_to` and `region` filters, built from the view's `date_field`/`region_filter_field`. The region choices come from the scoped Region queryset.
- `lists/views.py` -- `AdminListView` (:94; `region_field`, `assignee_field` :118-119; `get_queryset` :153 via `scope_queryset`; the filterset built at :160-162). Pass the request user, time zone and the declared fields to the filterset. 3.7's invalid-filter rule gives no rows plus a warning.
- `lists/sample.py` (List demo: `created`, `home_region`), `audit/views.py` `AuditEntryFilter` :25 and `AuditLogListView` :53 (`occurred_at`), `staff/admin_users.py` `AdminUserFilter` :70 and its list view :170 (`created`, `regions`).
- `lists/templates/lists/list.html` / the filter form partial -- render the new fields in the filter bar.
- `staff.User.time_zone` -- the user's IANA zone, for the day boundaries (`zoneinfo`).
- Tests: `lists/tests/test_lists.py`, `audit/tests/test_audit.py`, `staff/tests/test_admin_users.py`.

## Tasks & Acceptance

**Execution:**
- [ ] the date-range and region filters in `AdminFilterSet` plus the `AdminListView` wiring and the template
- [ ] turned on for List demo, the Audit log and Admin users
- [ ] tests for every matrix row; the README "Lists" section (how a list turns them on)

**Acceptance Criteria:**
- Given admindev after deploy (user), when the Sysadmin filters List demo by a date range and by ae, then only matching rows show, and the CSV matches. An eu-only CRM is offered only eu, and the Audit log filters by date.

## Verification

**Commands:**
- mytp-admin suite (CI env), `check`, `makemigrations --check` -- pass; CI on PostgreSQL -- green.

## Implementation Notes

## Plan Change Log

## Review Triage Log

### Pass 1 (quick, 2026-11-03): high 0, medium 0, low 4, false 0, maybe-false 0

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | The table shows times in UTC but the date range reads days in the user's zone, with nothing telling the user (rows near midnight look misplaced) | low | patch | Patched: labels carry the zone ("From (Europe/Madrid)") and "To" has the help "Whole days in your time zone; times in the table are UTC."; tests. |
| 2 | No test crossed a DST change | low | patch | Added `test_daylight_saving_days` (Europe/Madrid 2026-03-29 and 2026-10-25, rows just inside and outside each boundary). The code was already correct. |
| 3 | An assigned-scope user would be offered every region | low | rejected | No list with the filter has an assigned role; rows stay limited to assignments; revisit when epic-clinical-ops turns the filter on. |
| 4 | `add_filter` silently replaced a list's own filter with the same name | low | patch | Now raises ImproperlyConfigured; test. |

Verified after the patches: mytp-admin suite 601 passed (SQLite, CI env), `check` clean, `makemigrations --check` no changes.
