---
title: 'ICE in the case wizard'
type: 'bugfix'
ticket: ''
created: '2026-11-23'
status: 'in-progress'
baseline_revision: 'b0612812e76b8b481dd93b32214c61f90c33bff6'
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

**Problem:** 9.3's step 2 offers short, medium and long only. The catalogue's ICE is its own category (`initial_case_evaluation`, no size), so the ticket's "ICE, short, medium or long" choice has no ICE (found at 9.3's staging).

**Approach:** Add ICE as the first choice of step 2 in both branches. It picks the doctor's offered ICE product; the cart item keeps the maker exactly as for a TPS, so staff can later work from records the doctor already uploaded to that maker's platform.

## Decisions

- Decision (user, 2026-11-23): ICE is offered under a maker and under in-house printing; under in-house it has no maker.
- Decision (user, 2026-11-23): an ICE under a maker follows the TPS maker rules: a named maker needs a saved login (warning + link), "other" needs a name, "not decided yet" passes.
- Decision (user, 2026-11-23): the planning bought after an ICE may use any maker (no link to the ICE's maker).
- Decision (user, 2026-11-23): asked for directly ("add the ICE to step 2"); merge, staging over SSH, branch push and completion commit delegated as for 9.3.

## Boundaries & Constraints

**Always:** One cart item per patient, as today. A patient with any live work order cannot get an ICE (the planning-after-an-ICE exception is for a TPS only). Add-ons for an ICE are only those `resolver.add_ons` offers on the ICE product. EN and ES texts.

**Never:** Changing the catalogue, orders, work orders or 9.1's test-order page.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| ICE with a maker | Invisalign with saved login, ICE | cart item: ICE product, maker invisalign | — |
| ICE in-house | in-house, ICE | cart item: ICE product, no maker | — |
| ICE, missing login | Smartee, no login, ICE | warning + link | refused |
| ICE, not decided / other | undecided, or other + name | added | empty other name refused |
| Live ICE work order | patient's live work order is an ICE, new ICE | — | refused (live work order) |
| ICE not offered | no ICE product offered to the doctor | ICE not listed | tampered POST refused |

</frozen-after-approval>

## Code Map

- `cart/service.py` -- `SIZE_ORDER` already starts with `ice`; `offered_tps` keeps only `tps_*` with a size; `add_tps` picks the product by category and size; `_maker` applies the maker rules. Make size `ice` resolve to the offered `Product.ICE` product in both branches (maker stored per branch as today), and refuse an ICE when the patient has any live work order.
- `cart/views.py` `_options`, `size_label`, `_line` -- the size rows and their prices; add the ICE row (one price for both branches) when an ICE is offered.
- `cart/templates/cart/choose.html`, `static/js/case-wizard.js` -- ICE row first; the JS must not hide it in either branch.
- `patients/tests/conftest.py` -- already has the ICE product (9.1); `cart/tests/` -- add the matrix rows.
- `locale/es/LC_MESSAGES/django.po` -- any new string.

## Tasks & Acceptance

**Execution:**
- [ ] `cart/service.py` -- ICE resolution and the live-work-order rule for an ICE.
- [ ] `cart/views.py`, `cart/templates/cart/choose.html`, `static/js/case-wizard.js` -- the ICE row in both branches, with its price.
- [ ] `cart/tests/test_service.py`, `cart/tests/test_views.py` -- one test per matrix row; the cart page shows "ICE" for the item (EN and ES).

**Acceptance Criteria:**
- Given a doctor offered an ICE, when step 2 opens, then ICE is the first choice under both a maker and in-house printing, with its price.

## Verification

**Commands:**
- From the repo root, sandbox disabled: `export FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU=" DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib; export DATABASE_URL="sqlite:///$TMPDIR/ice.sqlite3"; ../.venv/bin/python -m pytest -q -p no:cacheprovider --tb=line cart patients` -- expected: 0 failures (I run the full suite).
