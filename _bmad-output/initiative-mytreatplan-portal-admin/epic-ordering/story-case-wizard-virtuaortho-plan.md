---
title: 'Case wizard: VirtuaOrtho'
type: 'feature'
ticket: '4'
created: '2026-11-23'
status: 'done'
baseline_revision: '7edc053e24b9bec8f6bdbcff84a57380a52ccc6f'
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

**Problem:** The case wizard (9.3) offers ICE and TPS only, so an Iberia Advanced or Premium doctor cannot put a VirtuaOrtho case in the cart. Also, the catalogue gives VirtuaOrtho One/DigitalTwin products no size, so short/medium/long and "Junior on medium" cannot be told apart.

**Approach:** On the admin site, let VirtuaOrtho One and DigitalTwin carry a size, as TPS branded and in-house already do; fix the seed (sizes, Junior on VO medium, no Byte Sync on ODB) and the same data on admindev. In the portal, add a VirtuaOrtho branch to step 2, shown only when `offers_for` returns a VirtuaOrtho-category product: One or DigitalTwin, then the product, offered add-ons, an aligner count for Spark on Demand, and a shipping address stored on the cart item.

## Decisions

- Decision (user, 2026-11-23): VO sizes live in the catalogue: admin `SIZED_CATEGORIES` = TPS branded, TPS in-house (already sized), VO One, VO DigitalTwin; the seed sizes the six VO products; admindev gets the same by a targeted script (never the full seed). mytp-core: docstring only, no release.
- Decision (user, 2026-11-23): Junior I and II (free) on medium VO One and medium DigitalTwin, as on branded TPS.
- Decision (user, 2026-11-23): ODB = Ormco Digital Bonding (indirect bonding of braces with a digitally designed template). Offered under One and DigitalTwin; under DigitalTwin the ODB-DT add-on is added automatically (not a choice); under One, no ODB-DT.
- Decision (user, 2026-11-23): Byte Sync is for plastic aligners only: never on ODB (or retainers). The seed's `BYTE_SYNC_CATEGORIES` and admindev's three Byte Sync add-ons drop `odb`.
- Decision (user, 2026-11-23): retainers need a "finished treatment": a work order of the patient in stage `approved` whose product is VO One, DigitalTwin or Spark on Demand; tested with a fixture until epic-case-lifecycle.
- Decision (user, 2026-11-23): the doctor picks the address in the wizard (default preselected, then the rest of `usable_addresses`); it is stored on the cart item (`SET_NULL`); 9.6 snapshots it and calls `mark_used`.
- Decision (user, 2026-11-23): no maker on a VirtuaOrtho item (MyTreatPlan makes it).
- Decision (user, 2026-11-23): a patient whose live work order is an ICE may get any VirtuaOrtho item; any other live work order refuses.
- Decision (user, 2026-11-23): plan kept whole (~2100 tokens). Approved and continued; merge, staging checks (SSH, incl. the admindev data script), pushing the story branches, the mytp-core release tag if needed, and the completion commits to master are DELEGATED for this story.

## Boundaries & Constraints

**Always:** Products chosen by category and, for One/DigitalTwin, size, never by code. One → short, medium, long, ODB, retainers, Spark on Demand (one arch or two arches = the two Spark products, 4–9 aligners from the product's `per_unit` range); DigitalTwin → short, medium, long, ODB (+ODB-DT), retainers. Add-ons only those `resolver.add_ons` offers; prices by `resolver.price` with the quantity. A usable shipping address is required; without one, a block links to `settings_shipping_add?next=<wizard>` (quoted) and items are refused. One cart item per patient; fiscal block as in 9.3; doctor-only views; no patient name in URLs/logs. EN and ES texts on both sites.

**Never:** Checkout, the order's address snapshot and `mark_used` (9.6), vouchers, ICE deduction, included cases, calls display. No `OrderLine`/`WorkOrder` field. No change to 9.3's TPS/ICE behaviour. No full `seed_staging_catalogue` run on admindev.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy | Iberia Advanced, address, DigitalTwin medium + Byte Sync | item: VODT medium, BYTE-SYNC, address | — |
| Junior | One medium + Junior I | added | Junior on short/long refused |
| ODB under DigitalTwin | DigitalTwin, ODB | item with ODB-DT | — |
| ODB + Byte Sync | ODB with BYTE-SYNC | — | refused (not offered) |
| Spark | One, two arches, 6 aligners | quantity 6, price 6 × unit | 3 or 10 refused |
| Spark under DigitalTwin | tampered POST | — | refused |
| No address | none usable | block + link | refused |
| Another doctor's / archived address | tampered id | — | refused |
| Not offered | rest-of-EU or Basic doctor | no VO branch | tampered POST refused |
| Retainers | no approved VO/Spark work order | not listed | refused |
| Retainers, finished | approved VODT work order (fixture) | added | cancelled one does not count |
| Live ICE | live ICE work order, VO item | added | other live work order refused |

</frozen-after-approval>

## Code Map

- `../mytp-admin/catalogue/models.py:309` `SIZED_CATEGORIES`, `:391` validation, `:78` `SIZE_MESSAGE` (update wording + admin ES .po).
- `../mytp-admin/catalogue/seed_staging.py:68-75` (VO without size; `SIZES` loop already gives `_size`), `:129` `BYTE_SYNC_CATEGORIES` (drop `C.ODB`), `:146-149` JUNIOR-1/2 (`[C.TPS_BRANDED]` → add both VO categories), seed tests.
- `../mytp-core/mytp_core/contracts/catalogue.py:69` -- docstring "TPS products only"; `add_on_applies:499` unchanged.
- `catalogue/models.py:108-141` -- portal docstring on `size`; `catalogue/resolver.py:399` `add_ons`, `:410-431` `price(..., quantity)` → `QuantityRefused`.
- `subscriptions/service.py:225` `offers_for` -- gate.
- `cart/models.py:28-48` `CartItem`; `cart/service.py` `add_tps`, `CartRefused`, `SIZE_ORDER`, `offered_tps`, `offered_ice`; `cart/views.py` `_options`, `SIZE_LABELS`, `start_a_case_tps`, `_refusal_texts`, `_line`, `_fiscal_block` (precedent for the shipping block); `cart/templates/cart/choose.html`, `cart/templates/cart/cart.html`, `static/js/case-wizard.js`.
- `accounts/shipping.py:313` `usable_addresses`, `:322` `has_usable_address`, `:326` `default_address`; `settings_shipping_add`.
- `patients/service.py:115` `live_work_order`, `:121` `live_is_ice`; core `stages.py` (`approved`, `cancelled`).
- `patients/tests/conftest.py`, `integration/tests/snapshot_rows.py`, `cart/tests/test_service.py:38` `live_order`.

## Tasks & Acceptance

**Execution:**
- [ ] `../mytp-admin/catalogue/models.py`, seed, locale, tests -- sized VO categories; seed sizes, Junior on VO, no Byte Sync on ODB; tests for each (own branch and PR in mytp-admin).
- [ ] `../mytp-core/mytp_core/contracts/catalogue.py` -- docstring (commit on core master, no tag).
- [ ] `cart/models.py` + migration -- `quantity` (positive, default 1), `shipping_address` (FK, null, `SET_NULL`).
- [ ] `cart/service.py` -- `offered_virtuaortho(doctor)` and `add_virtuaortho(doctor, patient, *, variant, choice, size, add_on_codes, quantity, address_id)` raising `CartRefused` for every matrix row; ODB-DT forced under DigitalTwin; retainers rule; live-ICE rule.
- [ ] `cart/views.py`, `cart/urls.py`, templates, `static/js/case-wizard.js` -- the VO branch in step 2 (variant, product rows with prices, add-ons, aligner count for Spark, address radios, shipping block); cart page shows aligners and the address label; `_line` prices with the quantity. Plain POST works without JS.
- [ ] `locale/es/LC_MESSAGES/django.po` -- Spanish for every new string.
- [ ] `patients/tests/conftest.py`, `cart/tests/` -- VO catalogue (sized One/DT, ODB, retainers, Spark per-unit, BYTE-SYNC, ODB-DT, Junior on VO medium, Iberia rules) and one test per matrix row; views: branch hidden when not offered, block when no address.

**Acceptance Criteria:**
- Given an Iberia Advanced doctor with a saved address, when the wizard adds a DigitalTwin medium with Byte Sync, then the cart shows it under the patient with its price and address.
- Given no usable address, when the doctor follows the block's link and saves one, then they land back on step 2 for the same patient.
- Given admindev after the script, then VO One/DT products show their sizes, Junior is offered on VO medium, Byte Sync is not offered on ODB, and the portal's snapshot reflects it.

## Implementation Notes

- Portal 27522e3 + c007533; mytp-admin c4b4183 + 43c2f37; mytp-core 55015e6 (docstring, no tag). Row values carry the variant (e.g. `one:case:medium`, `digitaltwin:odb:<product id>`): ODB and Spark arches are told apart by product id. ODB-DT is the one add-on referenced by code. Unsized One/DigitalTwin rows are not listed (admindev before its script). The admin site has no locale catalogue, so its message stays English.
- Local suites: portal 4307 passed, 17 skipped, 8 failed — all `invoices/tests/test_pdf.py` (WeasyPrint cannot load libgobject on this Mac; same at the baseline); admin 1684 passed, 12 skipped.

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, opus): high 0, medium 1, low 5, false 0, maybe-false 0; all 6 patched (c007533, admin 43c2f37). The reviewer's note on the admindev script (no transaction) was fixed in the script.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | Without JS both variants' rows share one radio group with equal values; the variant radio decides the product, so the ticked row's price can differ from what is added | medium | patch | Rows grouped per variant; each value carries its variant and the POST derives it; test of a no-JS DigitalTwin POST. |
| 2 | A refused VO POST pre-ticks the TPS form's add-ons (and back) | low | patch | Add-ons ticked only in the posted form; test. |
| 3 | Tampered `case:` with an empty size matches an unsized product | low | patch | Empty size refused with not_offered; test. |
| 4 | DigitalTwin ODB row listed when ODB-DT is unavailable, then always refused | low | patch | Row dropped when ODB-DT is not offered on it; test. |
| 5 | Admin size help text and docstrings still say TPS only | low | patch | Wording updated (admin 43c2f37). |
| 6 | Test Byte Sync offered on the ICE, unlike the seed | low | patch | Fixture categories match the seed. |

## Verification

Branches already exist: portal `story/9-4-case-wizard-virtuaortho` (this checkout); mytp-admin `story/9.4-virtuaortho-sizes` in `../mytp-admin` (baseline 7b6c54d5d90bd6a3a57fb95b7abad0cec9f86578). mytp-core: edit the docstring on its master in `../mytp-core` and commit; do not tag or push. Commit on each branch (new commits); never push, tag or amend. Do not touch admindev or staging.

**Commands (sandbox disabled):**
- Portal, from the repo root: `export FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU="; export DATABASE_URL="sqlite:///$TMPDIR/s94.sqlite3"; ../.venv/bin/python -m pytest -q -p no:cacheprovider --tb=line` -- expected: 0 failures (run `../.venv/bin/python manage.py compilemessages` after editing the .po); `../.venv/bin/python manage.py makemigrations --check --dry-run` -- no changes.
- mytp-admin, from `../mytp-admin`: `SECRET_KEY=ci-only-not-a-secret DEBUG=False SECURE_SSL_REDIRECT=False ALLOWED_HOSTS=localhost,127.0.0.1,testserver DATABASE_URL=sqlite:///$TMPDIR/adm94.sqlite3 FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU=" ALLOW_FAKE_VERIFICATION=True /private/tmp/claude-501/admin-venv/bin/python -m pytest -q -p no:cacheprovider` -- expected: 0 failures; `makemigrations --check --dry-run` there too (a migration is fine if the validation change needs one; compile the admin .po after editing).
- Write the admindev data script (sizes on the six VO products, both VO categories added to JUNIOR-1/2, `odb` removed from BYTE-SYNC/-USD/-AED; under `audit.command_actor('staging-9.4')`; prints what it would change and only writes when `APPLY = True` at its top, default False) to `/private/tmp/claude-501/s94_admindev.py`. Do not run it anywhere.

## Staging

- Merged: admin PR #49 → eb5c47e (admindev deploy run 45358264652 green); portal PR #133 → 4191068 (deploy run 45360679426 green, dev and dev-ae). PR CI green on both (portal: one run hit the known flaky `test_makers.py` `p2` test, green on re-run). mytp-core 55015e6 (docstring) pushed to master, no release. Local suites: portal 4307 passed (8 local-only WeasyPrint PDF failures), admin 1684 passed.
- admindev data script (command actor `staging-9.4`): dry run listed the 11 intended changes (six VO sizes, VO categories on JUNIOR-1/2, `odb` removed from BYTE-SYNC/-USD/-AED); applied; a second run found nothing left.
- Checks on dev (server-side, test client, one transaction rolled back, nothing left behind), doctor of patient EU-0BNY91JQ switched to SEED-ADV-IBERIA inside the transaction:
  1. dev's snapshot has the six sized VO products. Passed.
  2. DigitalTwin medium + Byte Sync added with the chosen address; the cart shows the patient and the address in EN and ES. Passed.
  3. Spark on Demand: 3 and 10 aligners refused (`quantity`), 6 added at 50.00 per aligner. Passed.
  4. Without a usable address: block with the `settings/shipping/add/?next=` link; item refused (`no_address`). Passed.
  5. A DE doctor on SEED-ADV-ROEU: 0 VO products offered, no VirtuaOrtho branch; the same doctor in ES on SEED-ADV-IBERIA sees it. Passed.
- Owed by the user: by-eye check of step 2's VirtuaOrtho branch and the cart, EN/ES, desktop and phone.
