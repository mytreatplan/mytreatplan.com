---
title: 'Patient search and paging'
type: 'feature'
ticket: '15'
created: '2026-11-24'
status: 'in-progress'
baseline_revision: '4a14f047cc607f16b65bbf79e95abd13708686f8'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A doctor with many patients cannot find one: the patient list (9.2) pages but has no search, and the case wizard's patient step (`cart/start.html`) dumps every active patient into one unpaged list.

**Approach:** One reusable patient-chooser fragment, used by the patient list and by the wizard's patient step: the server renders all of the doctor's rows into one plain table, and a small vanilla script searches (code, first name, surname, reference; partial, case- and accent-insensitive) and pages them in the browser, as the DataTables plugin does in its client-side mode, so the search term never leaves the browser.

## Boundaries & Constraints

**Always:**
- A doctor sees only their own patients; erased patients show no name and never match by name.
- No patient name or search term in any URL, query string, log line, flash message or cache key (AD-5, AD-23); names travel only in the page body, as today.
- Paging behaviour as 9.2 shows it: sizes 10/25/50/100 (a select), default 10, newest first, "Showing X to Y of N" (of the matches while searching), page links with gaps; keep the `data-*` hooks 9.2's tests pin.
- Without JS every row shows, unpaged; the search box is hidden.
- Portrait thumbnails load lazily (`loading="lazy"`), so a long list stays light.
- In the wizard step only active patients are listed, with the existing "Choose" link and in-cart state.
- Markup stays a plain `<table>` with a `<thead>`, so the DataTables plugin can replace the script later without template changes.
- EN and ES strings for everything new.

**Never:**
- No jQuery or DataTables plugin now, no server-side search endpoint, no htmx for this.
- No name sort, no CSV export, no search on comments.
- No change to the wizard's later steps or flow (entry 16 redesigns it).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Code search | `7k3q` or `EU-7K3Q` | Rows whose code contains it | No error expected |
| Name search | `garc` | Rows whose first name, surname or reference contains it; matches "García" | No error expected |
| No match | `zzz` | "No patients match your search." row; pagination hidden | No error expected |
| Paging a search | 30 matches, size 10, page 2 | Rows 11–20 of the matches; "Showing 11 to 20 of 30"; URL unchanged | No error expected |
| Clear | term emptied | Full list, page 1 | No error expected |
| No JS | script not run | All rows visible, no search box | No error expected |

- Decision (user, 2026-11-24): search and paging run in the browser over the rendered rows, like the DataTables plugin's client-side mode, with our own small script for now; the DataTables plugin may come in a later evolution. This replaces 9.2's server-side `Paginator` paging and lifts 9.2's "no name search" boundary for the doctor's own list (names still never in a URL or log).
- Decision (user, 2026-11-24): the wizard's patient step switches to the chooser in this story.

</frozen-after-approval>

## Code Map

- `patients/models.py:37-70` -- `Patient`: `code` plain; `first_name`/`surname`/`reference` `EncryptedCharField` (no DB lookups); `status` active/inactive/erased; ordering `['-created_at','code']`; `display_name` ('' when erased).
- `patients/service.py:104` -- `patients_of(user)`; reuse as the row source.
- `patients/views.py:37` `page_links`, `:71-97` `patient_list` (server `Paginator`, `PAGE_SIZES`, `?page=&size=`) -- server paging is replaced: render all rows (`select_related` as today); drop `page_links`/`PAGE_SIZES` if nothing else uses them.
- `patients/templates/patients/list.html` -- size form, `data-patients` table, `data-pagination`, empty state `data-no-patients`, `data-add-patient-empty` -- extract table + controls into `patients/_patient_chooser.html` with a `mode` of `list` (code links to the patient page) or `choose` (Choose link / in-cart badge). Each row carries `data-search="<code> <first> <surname> <reference>"`, lower-cased and accent-folded on the server (`unicodedata` NFKD, drop combining marks) so the script compares plain strings; erased rows carry the code only.
- `static/js/patient-list.js` -- today only auto-submits the size form; becomes the chooser script (vanilla, no dependency, ~100 lines): reads `[data-patient-chooser]`, shows the search box, filters on `input`, pages, renders "Showing…" and page links into the existing hooks, folds the term the same way (`normalize('NFD')` + strip `\p{M}`).
- `cart/views.py:110-118` `_step_one` and `cart/templates/cart/start.html` -- use the chooser in `choose` mode with active patients and the `in_cart` flag.
- `cart/views.py:2-4` -- module rule: no patient name in URL, log or message.
- Tests: `patients/tests/test_list.py:42-105` pin server paging (`test_pages_of_ten_by_default`, `test_a_size_of_25_gives_two_pages`, `test_invalid_page_or_size_falls_back`) -- rewrite them for all-rows rendering; keep ownership, erased, empty-state and hook tests. Browser conftest in `accounts/tests/browser/`.

## Tasks & Acceptance

**Execution:**
- [ ] `patients/views.py` -- render all of the doctor's rows with a folded `search_text` per row; remove server paging -- the browser pages.
- [ ] `patients/templates/patients/_patient_chooser.html` (new), `list.html` -- search box (`type="search"`, label, hidden until JS), table, "no match" row, size select, pagination hooks; `loading="lazy"` on thumbnails.
- [ ] `static/js/patient-list.js` -- search + paging as in the Code Map.
- [ ] `cart/views.py`, `cart/templates/cart/start.html` -- step 1 uses the chooser in `choose` mode.
- [ ] `locale/es/LC_MESSAGES/django.po` -- Spanish strings.
- [ ] `patients/tests/test_list.py` -- server tests: all rows rendered, `data-search` folded, erased row has no name in it, another doctor's patients absent.
- [ ] `patients/tests/browser/test_chooser.py` (new) -- Playwright: every matrix row on the patient list (no-JS via `java_script_enabled=False`), one search + Choose on the wizard step; `page.url` unchanged after searching.
- [ ] `cart/tests/` -- wizard step renders the chooser with active patients only.

**Acceptance Criteria:**
- Given a doctor with 60 patients, when they type part of a surname, then only matching patients show, paged, and the URL holds no part of the name.
- Given the wizard's patient step with 30 active patients, when it loads, then it shows 10 rows with page links and a search box, and "Choose" still leads to the TPS step.

## Design Notes

Rendering every row trades one decryption per patient per page load for zero search requests. Fine up to a few thousand patients per doctor; beyond that, switch to server-side search (deferred-work if it ever shows in timings).

## Verification

**Commands:**
- the portal suite command (local-dev-notes) -- expected: 0 failures besides the known WeasyPrint `test_pdf.py` on this Mac

**Manual checks (if no CLI):**
- On dev, EN/ES: search by code and by name on the patient list and the wizard's first step; the address bar never changes while searching.
