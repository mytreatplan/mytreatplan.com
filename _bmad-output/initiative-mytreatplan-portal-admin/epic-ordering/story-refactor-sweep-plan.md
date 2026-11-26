---
title: 'Refactor sweep'
type: 'refactor'
ticket: '11'
created: '2026-11-26'
status: 'done'
baseline_revision: 'b4306b3528133f14b45dd9d980061019494924f2'
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

**Problem:** Epic-ordering's finished entries (9.1–9.9, 9.12's code, 9.14–9.20) left small debts: unused imports, private helpers imported across apps, the patient pre-checks repeated in three cart functions, two copies of the no-JS add-patient page, stale comments after the 9.16 wizard, and deferred-work entries whose status is out of date.

**Approach:** Cleanup only, with no behaviour change: the same pages, refusal codes, messages and order of checks. Scope decision (user, 2026-11-26): the sweep starts once 9.9 and 9.19 are done and covers finished entries only; 9.10, 9.13 and their findings go to epic 10's sweep.

**Decisions (user, 2026-11-26):**
- The shared pre-check helper is in scope (Open Question 1 → a); risk becomes medium.
- No 9.8 backfill: only staging data predates 9.8 and production starts empty. The stale staging rows are removed instead (Open Question 2, user's answer).
- The plan stays whole (~1900 tokens).

## Boundaries & Constraints

**Always:**
- Every existing test passes unchanged, except imports of a renamed helper.
- Refusal codes and the order in which the cart and payments checks refuse stay exactly as they are.
- Both English texts of the add-patient back links stay (no msgid change, no new translation).

**Never:**
- Behaviour changes or product decisions. These stay in deferred-work.md: the portrait trace without a code; what `inactive` means for ordering; the Education page; the purge that sends no work-order event; the 8.13 included-case items; the triage rows rejected in 9.1, 9.3, 9.6 and 9.17.
- mytp-core changes, migrations, URL changes, or removing the `start-a-case/<code>/` redirect (9.16 kept it on purpose).
- Touching 9.10, 9.13 or epic-payments code beyond one unused import.

</frozen-after-approval>

## Code Map

- `accounts/erasure.py:55` -- imports `fiscal` and `shipping`, unused since 9.12 dropped `fiscal.erase`/`shipping.erase`.
- Unused names: `cart/emails.py:15` `Payment`; `patients/portrait.py:38` `client_sync`; `invoices/service.py:24` `Order`; `invoices/service.py:34` `_ACTOR_TYPES`; `payments/revolut.py:41` `InvalidOperation`. Keep `patients/service.py:54` `InvalidStageTransition` (a deliberate `noqa` re-export).
- Private names imported across modules: `cart/views.py:26` `patients.views._create`; `cart/views.py:28` `payments.views._refusal_message`; `cart/wizard.py:42`, `cart/pages.py:18`, `cart/forms.py:92` (a local import, to avoid a circular import) import `_doctor_only`, `_fiscal_block`, `_own_patient`, `_refusal_texts` from `cart.views`; `payments/transfers.py:31` `service._lock`.
- Repeated pre-checks: `cart/service.py` `add_tps` (~:192-208), `add_virtuaortho` (~:270-281) and `add_work_order` (~:360-372) each check the doctor (`NOT_A_DOCTOR`), `patient_of` (`NO_PATIENT`), `fiscal.has_usable_entity` (`NO_ENTITY`), `blocking_work_order` with the live-ICE exception (`LIVE_WORK_ORDER`), and the item already in the cart (`IN_CART`). Read each one before extracting: they differ in order (`_maker` runs between the entity and work-order checks in `add_tps`) and in the ICE rule. `payments/service.py:349` `_patient_free` holds the same ICE rule as the cart.
- `cart/templates/cart/new_patient.html` and `patients/templates/patients/new.html` -- the same page except the form action, the back-link URL and its text ("Back to the patients" / "Back to patients").
- Stale "step 2's size choice" comments: `cart/service.py:76`, `:88`; `cart/views.py:121`, `:133` (since 9.16 the size is chosen on the options step).
- `_bmad-output/initiative-mytreatplan-portal-admin/deferred-work.md:252` -- "PARTLY DONE"; its remaining items were delivered by 9.9 (pending-payment work orders) and 9.18 (the admin confirm button).
- Tests that cover the pre-checks: `cart/tests/test_service.py`, `cart/tests/test_checkout.py`, `cart/tests/test_wizard*.py`, `patients/tests/test_work_orders.py`, `patients/tests/test_manual_work_orders.py`, `payments/tests/`.

## Tasks & Acceptance

**Execution:**
- [ ] `accounts/erasure.py`, `cart/emails.py`, `patients/portrait.py`, `invoices/service.py`, `payments/revolut.py` -- remove the unused imports and `_ACTOR_TYPES` -- dead code.
- [ ] `cart/common.py` (new) or public names -- move `_doctor_only`, `_fiscal_block`, `_own_patient`, `_refusal_texts` out of `cart/views.py` into a shared module with public names, and update `cart/views.py`, `cart/wizard.py`, `cart/pages.py`, `cart/forms.py` (which can then import at module level if the cycle is gone); rename `patients.views._create` → `create_patient_from_form` (or similar), `payments.views._refusal_message` → `refusal_message`, `payments.service._lock` → `lock_order`, keeping a private alias only where a test imports the old name -- no private names cross module boundaries.
- [ ] `cart/service.py` -- one private helper for the shared pre-checks (doctor, patient, entity, blocking work order with the ICE flag, in cart) used by the three add functions; `add_tps` keeps `_maker` at its current point in the order; `payments/service.py` `_patient_free` and the cart helper share one `patients.service` predicate for "may this patient take this line" (`blocking is None or (not is_ice and is_live_ice(blocking))`) -- one place for the ICE rule.
- [ ] `patients/templates/patients/new.html`, `cart/templates/cart/new_patient.html` -- one template with the action URL, back URL and back text passed from each view; both msgids still rendered by their own page.
- [ ] `cart/service.py`, `cart/views.py` -- comments name the options step instead of "step 2".
- [ ] `deferred-work.md` -- add "DONE by 9.9 and 9.18" to the :252 entry's summary.

**Acceptance Criteria:**
- Given the full suite, when it runs after the sweep, then the same tests pass as on the baseline (the known local WeasyPrint failures aside), with no test logic changed.
- Given a grep for `from cart.views import _`, `views import _create`, `views import _refusal_message` and `import _lock`, when it runs, then it finds nothing.
- Given each add function in `cart/service.py`, when a doctor without an entity, a foreign patient, a blocked patient or a patient already in the cart is added, then it refuses with the same code as before.

## Implementation Notes

- The plan's "add_work_order" pre-checks live in `check_work_order` (which `add_work_order` calls); its `WORK_ORDER` refusal stays between the doctor and patient checks.
- `accounts/views.py` also imported `cart.views._refusal_texts` (missed by the Code Map); it now imports `cart.common.refusal_texts`.
- The renames reached `payments/refunds.py` (`lock_order`) besides the files listed; no test imported the old names, so no aliases were kept.
- `cart/tests/test_refusal_order.py` (13 tests) pins the refusal order of the three add paths; it passes on the baseline code too.

## Plan Change Log

## Review Triage Log

| # | Finding | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | `deferred-work.md` :253 says DONE and then "…remain" for the same items | low | patch (done) | The kept PARTLY DONE text still listed them as remaining; reworded to "were still to come then". |
| 2 | Payments renames and `_patient_free` go past the Never line "epic-payments code beyond one unused import" | false | reject | The approved task list asks for exactly these renames and the shared ICE predicate; mechanical, payments tests pass. Fix would be a plan edit. |
| 3 | `accounts/views.py:429` changed though not listed | false | reject | Required by the grep acceptance criterion; recorded in Implementation Notes. |
| 4 | `cart/views.py:5` docstring line 103 chars | low | patch (done) | Reflowed. |
| 5 | (triage) Decision 1(a) promised refusal-order tests; none were added | medium | patch (done) | Added `cart/tests/test_refusal_order.py` via the implementer. |

## Verification

**Commands:**
- `../.venv/bin/python -m pytest -q -p no:cacheprovider --tb=line` (env as in the suite command) -- expected: 0 failures besides the 11 known local WeasyPrint ones in `invoices/tests/test_pdf.py` and `test_pending_documents.py`.
- Staging data (after merge, over SSH): count on dev and dev-ae the work orders created before 9.8 and the `pending_payment` bank-transfer orders with no `pending_payment` work order; show the counts and remove those rows only after the user confirms.
- `../.venv/bin/python -m pyflakes cart patients payments invoices accounts/erasure.py` (if installed) -- expected: no unused-import warnings in the touched files.

## Staging

- Merged as PR #150 (1cf9065, 2026-11-26); CI 4536+ tests green on the PR. Deployed with add87c6 (the 1cf9065 and 7179d57 runs skipped their release because master had moved on; add87c6's test job was re-run once after a flaky `accounts/tests/test_makers.py:218`, where the 2-character secret "p2" occurred by chance in the page).
- Dev checks (rolled back, no names printed): the patients page's add-patient page shows "Back to patients" and the wizard's shows "Back to the patients", each form posting to its own page; a TPS added to the cart, a second TPS and a VirtuaOrtho for the same patient refused with `in_cart`.
- Pre-9.8 data: dev-ae none; dev one work order (the `SEED-ICE-EUR` seed fixture, a live ICE kept for the ICE tests) and no pending transfer order without its hold. Nothing deleted.
