---
title: 'Products: core'
type: 'feature'
ticket: '5'
created: '2026-11-09'
status: 'built'
baseline_revision: '87cbc304078f0f07e662a46cfd295eaac57f876b'
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

**Problem:** C5 (AD-6): our products are a tracer (name, two categories, one net price); the company sells ten kinds of product and needs public texts per language, an internal code and notes, validity and status, without ever deleting a product (past orders refer to it).

**Approach:** On the admin site, extend `catalogue.Product` with: public texts per language (name and short description; EN required, ES optional with English fallback), internal code (unique) and internal notes, the ten categories, `valid_from` / `valid_to`, active, net price per currency (one currency per product; other currencies come from 5.7's clone). mytp-core 0.20.0 adds optional product fields to the catalogue section (texts, code, validity; never notes); the portal stores and applies them, with a fallback-to-English text property, and keeps deactivated products readable.

## Boundaries & Constraints

**Always:**
- Categories (codes): `tps_branded`, `tps_in_house`, `initial_case_evaluation`, `virtuaortho_one`, `virtuaortho_digitaltwin`, `odb`, `retainers`, `spark_on_demand`, `subscription`, `education` (labels as in the ticket); a fixed list in code.
- Texts: per language map (`{"en": …, "es": …}`) in models and contract; English required; fallback to English for any language without a text.
- Never hard-deleted: deactivate/reactivate only (as today); a deactivated product stays in the snapshot marked inactive, and on the portal stays readable for past orders.
- Internal notes never leave the admin site.
- Contract compatibility: new fields optional, absent values not dumped (the signed object of an old snapshot unchanged), `min_reader` unchanged; the portal is updated in the same release because today's applier drops unknown categories.
- Every change audited and rebuilding the snapshot.

**Never:** no add-ons, quantities, calls or cost links (5.6), no clone (5.7), no subscription texts and no discounts per category beyond the two TPS discounts decided below (5.8), no billing rules (5.9).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Product per type | each of the ten categories, EN + ES texts, a price | saved, in the snapshot, on dev with texts | — |
| ES missing | EN only | ES reads fall back to EN | — |
| EN missing | ES only | form error | — |
| Duplicate code | an existing code | form error | — |
| Deactivate | an active product | inactive on dev, still readable by id | — |
| Validity | today (Madrid) outside `valid_from`/`valid_to` | not offered for new orders; still readable by id | — |
| TPS discounts | subscription with TPS branded 10 %, in-house 0 % | a `tps_branded` product is priced 10 % less, a `tps_in_house` one at its net price | — |
| In-house discount set | in-house discount edited to 5 % | `tps_in_house` products 5 % less; `tps_branded` unchanged | outside 0–100 refused |
| Legacy categories | existing `tps` / `other` products | `tps` becomes `tps_branded`; `other` becomes `uncategorised`, shown as "Uncategorised" until edited, never discounted | `uncategorised` cannot be chosen in the form |
| Old snapshot | an admin before this release | dev keeps working; an absent in-house discount reads as 0 | — |

## Decisions

- Decision (user, 2026-11-10): `tps_branded` and `tps_in_house` have different, separate subscription discounts. The existing `tps_discount_percent` is the TPS branded discount; a subscription gains a second one, `tps_in_house_discount_percent` (0 to 100, 2 places, editable on the admin site), which starts at 0 for existing and new subscriptions. Every other category gets no discount. Existing `tps` products migrate to `tps_branded`; existing `other` products become `uncategorised` until an editor recategorises them.
- Decision (user, 2026-11-10): texts are a public name (≤ 150) and a short description (≤ 1000), plain text with line breaks.
- Decision (user, 2026-11-10): the code is unique, letters/digits and `._/-`, ≤ 64, stored uppercase, and sent in the snapshot.
- Decision (user, 2026-11-10): validity dates are optional and inclusive, in Madrid time; outside them the product is not offered for new orders on the portal (the resolver skips it) but stays readable.
- Decision (user, 2026-11-10): `education` stays in the category list.
- Decision (user, 2026-11-10): release order mytp-core 0.20.0 → admin → portal; one plan, kept whole.

</frozen-after-approval>

## Code Map

- Repositories: this plan is in the portal (`mytreatplan.com`); mytp-core is `../mytp-core` (baseline 5af651f7bb89b9776bc3d71119fc71bc3e05c3f6) and the admin site `../mytp-admin` (baseline 3a6244a8340947137437ca6bb23c67646d127596). The branch `story/5.5-products-core` is checked out in all three: work there, commit nothing, push nothing, tag nothing. Both sites vendor a provisional 0.20.0 wheel built from the core branch, the way 0.19.0 is vendored (the release rebuilds it from the tag). Run each repository's full suite as its README says (the venv is `../.venv`).

- Admin: `catalogue/models.py` `Product` :100-141 (categories :105-107), `StatusMixin` :30; `Subscription.tps_discount_percent` :158 and its 0–100 constraint :171; `service.py` `PRODUCT_FIELDS` :37, `SUBSCRIPTION_FIELDS` :38, `DECIMAL_FIELDS` :46, `create_subscription` :210, `_create/_update/_set_active` :136-185, product functions :187-208; `views.py` `ProductFilter` :65, `ProductTable` :120, `ProductForm` :217, subscription table :138-143, form :235 and details :334 (TPS discount), `_product_details` :326, `Kind PRODUCTS` :341; `snapshot.py` :21-29 (subscription discount :29); `audit/registry.py` :33-36; matrix `permissions/matrix.py` :81, :106, :120 (unchanged).
- mytp-core: `contracts/catalogue.py` `CatalogueProduct` :95-108, `CatalogueSubscription` :111-117 (`tps_discount_percent: Percent`), `schemas/config_snapshot.json` (category open code :17-19), compatibility :27-33; `contracts/base.py:16`; `contracts/config.py` :29, :51-56; 5.2's serializer note on absent fields.
- Portal: `catalogue/models.py` `Product` :49-71 (`CATEGORIES`), `ReadOnlyModel` :18-29; `Subscription.tps_discount_percent` :83; `applier.py` :14-38 (drops unknown categories :31; subscription discount :35); `resolver.py` `price` :55-60 (TPS discount, category `tps`) and its module docstring, `offers` :63-70; `price_catalogue`; per-language pattern `legal/models.py` (~:59-96), `regions/profiles.py` languages :75, :88.
- Architecture: conventions :248, :252; AD-6 :128.

## Tasks & Acceptance

**Execution:**
- [x] mytp-core 0.20.0 (`mytp_core/contracts/catalogue.py`, `schemas/config_snapshot.json`, version in `pyproject.toml` and `__init__`): optional product fields (texts map, code, valid_from/to) and the optional subscription field `tps_in_house_discount_percent` (absent reads as 0; not dumped when absent), category list unchanged as open codes, tests, schemas, README.
- [x] Admin (`catalogue/models.py`, `service.py`, `views.py`, `snapshot.py`, `audit/registry.py`, a migration): product model + data migration (`tps` → `tps_branded`, `other` → `uncategorised`), service, form (texts per language, code, notes, validity; `uncategorised` not selectable), list/detail, snapshot entry (no notes); subscription `tps_in_house_discount_percent` (default 0, 0–100 constraint, form, table, details, audit, snapshot); README, tests.
- [x] Portal (`catalogue/models.py`, `applier.py`, `resolver.py`, `management/commands/price_catalogue.py`, a migration): product model + migration (same category mapping), subscription `tps_in_house_discount_percent` (default 0), applier (new categories, texts, code, validity, in-house discount), text fallback property, resolver (validity in Madrid time; branded discount on `tps_branded`, in-house discount on `tps_in_house`, none elsewhere), `price_catalogue`, tests.
- [x] Both sites: vendor the new core.

**Acceptance Criteria:**
- Given one product of each category with EN and ES texts, when dev applies the snapshot, then each is readable with its texts in both languages.
- Given a product deactivated after an order, when the portal reads it by id, then it is returned marked inactive.
- Given a subscription with a TPS branded discount of 10 % and an in-house discount of 0 %, when the resolver prices a `tps_branded` and a `tps_in_house` product of 100.00, then it returns 90.00 and 100.00.
- Given a product whose `valid_to` was yesterday (Madrid), when the resolver lists offers, then the product is absent and still readable by id.

## Implementation Notes

- Released as mytp-core v0.20.0 (a8f0189); both sites vendor the wheel built from the tag. Suites on SQLite: core 1364, admin 1188, portal 2452 (+ the 3 known PostgreSQL-only failures).
- `name` is kept in both sites and in the contract as the English public name, equal to the English text name, so search, sorting and readers before 0.20.0 keep working.
- An English description is required only when a Spanish one is given; each text falls back to English on its own.
- Existing products get no code from the migration (the column allows none); the form and the service require one on create and on the next edit, and the snapshot leaves it out until then.
- The admin migration does not rebuild the stored snapshot and writes no audit rows: the snapshot keeps `tps` / `other` until the first write of any section. The portal's applier reads those two codes as `tps_branded` and `uncategorised`.
- The portal's new columns have database defaults, so the previous release can still apply a snapshot during a deploy. The portal migration clears the applied snapshot version, so the first pull after the deploy applies the current snapshot again (review finding 7).
- Language keys of the texts are lowercase language tags (`en`, `es`, `pt-br`), not only two letters, so a later language does not need a new reader.
- `price_catalogue` prints the code and takes `--language` and `--product <id>`.

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, one reviewer per repository, 2026-11-10): 13 findings — 4 medium, 9 low (4 of them rejected), 0 false.

| # | Where | Finding | Verdict | Route | Evidence / action |
|---|-------|---------|---------|-------|-------------------|
| 1 | core `contracts/catalogue.py` `LanguageCode` | a language key such as `pt-br` refuses the whole snapshot | medium | patch | the pattern was `^[a-z]{2}$`, a reader rule that cannot be relaxed later; now lowercase language tags |
| 2 | core `CatalogueSection._consistent` | "code unique among the products" is not checked | medium | patch | `_unique` compared ids only; a section with two equal codes is refused |
| 3 | core `CatalogueProduct._consistent` | a blank English name validates | low | patch | the check was truthiness; now a non-blank character |
| 4 | core `CatalogueProduct` | `name` and the English text name may differ | low | patch | nothing compared them; refused when `texts` is sent |
| 5 | core `CatalogueProduct.texts` | a product with texts is not hashable | low | rejected | no code in the three repositories hashes a contract row; a fix needs a frozen mapping type |
| 6 | core `offered_on` | a datetime argument raises | low | rejected | the argument is documented as a date; it fails loudly on a call nothing makes |
| 7 | portal migration 0002, applier, README | an admin write between the two deploys leaves the portal without products until the next admin write | medium | patch | `integration/config.py` `_apply` answers UNCHANGED for the applied version; the migration clears the applied version so the first pull re-applies |
| 8 | admin `views.py` `ProductForm` | a text within its limit is refused when it has line breaks | medium | patch | `max_length` counted the posted `\r\n` as two; normalised before the check |
| 9 | admin migration 0003, README | the migration's mapping is not audited and not published until the next write; one README sentence is false | low | patch | documented in the migration and the README; a migration writes no audit rows in this project |
| 10 | admin `check_texts` | a name takes U+2028, tabs and other control characters | low | patch | only `\n` and `\r` were refused; categories Cc, Zl, Zp refused |
| 11 | admin `service._save` | every IntegrityError on a product is reported as a duplicate code | low | rejected | `full_clean` catches the other rules first; reachable only by a race, and the fix adds branches |
| 12 | admin `service.normal_texts` | non-string texts are coerced | low | rejected | the form always passes strings; no other caller |
| 13 | admin `views.py:13` | a 107-character docstring line | low | patch | re-wrapped |

## Verification

**Commands:**
- mytp-core, admin and portal suites — all pass; CI on PostgreSQL.

**Manual checks (staging):**
- On admindev: one product per category with EN + ES texts and a price; one with EN only; a duplicate code refused; deactivate one; on dev `price_catalogue` and a shell read show texts (ES and fallback), the inactive product readable, validity respected; a subscription's in-house discount set to 5 % reprices only the in-house product, then set back to 0.
