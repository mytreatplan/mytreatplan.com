---
title: 'Providers and provider products'
type: 'feature'
ticket: '4'
created: '2026-11-09'
status: 'built'
baseline_revision: 'portal 3726f5b, core 7cedb5e, admin 5763467'
route: 'full'
route_source: 'auto'
risk: 'low'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** C4: the company buys from providers (labs, aligner makers, services like Dental Monitoring), and nothing records who they are or what each of their products costs over time; later stories (5.6 links, epic-payments' cost at order time, margins) need a cost for any date.

**Approach:** On the admin site, `Provider` (code, name, type, contact, validity, comments; fiscal and payment details field-encrypted) and `ProviderProduct` (code, name, currency, active) with dated `ProviderPrice` rows (face and discounted, valid from / to); list, detail and edit pages; logical deletion; a cost-at-date lookup; and a new optional `provider_costs` snapshot section in mytp-core carrying only provider-product ids, codes, currency, active flag and dated costs (never provider details).

## Boundaries & Constraints

**Always:**
- Fiscal and payment details (fiscal id, legal address, bank account/IBAN, payment terms) are encrypted with the admin's keys; never in the snapshot, logs, CSV exports or audit values (audit masks them).
- Prices: `valid_from` required, `valid_to` inclusive and optional (open-ended); ranges of one provider product never overlap (checked under a row lock); gaps allowed (no cost on those dates); face price required, discounted optional and ≤ face (defaults to face); Decimal 12.2, non-negative, currency fixed per provider product.
- `cost_at(provider_product, date)` returns the discounted (else face) price of the range containing the date, or None.
- Logical deletion only: deactivating a provider hides it and its products from `available_for_links()` (for 5.6) but keeps every row and price.
- Snapshot: new optional section `provider_costs` (mytp-core 0.18.0 or later, see decisions); a snapshot without it signs as before; `min_reader` unchanged.
- Every change audited and rebuilding the snapshot, as the catalogue does.

- Decision (user, 2026-11-09): a new `providers` area: Financial Manager and Sysadmin change; Commercial Manager view without the fiscal and payment fields.
- Decision (user, 2026-11-09): provider types `lab`, `aligner_maker`, `service`, `other` (fixed list in code); status active/inactive plus optional contract validity dates (informative).
- Decision (user, 2026-11-09): no portal side yet: the section is published and the portal ignores it until 5.6 / epic-payments needs costs.
- Decision (user, 2026-11-09): released after 1.13: this story ships mytp-core 0.19.0.
- Decision (user, 2026-11-09): one plan, PRs core → admin (→ portal for vendoring only), kept whole.

**Never:** no links to our products (5.6), no portal use of costs beyond what is decided, no physical deletion, no provider name or details sent to regions.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Dated cost | 100 until 30 June, 90 from 1 July | cost 30 June = 100, 1 July = 90 | — |
| Overlap | a new range overlapping an existing one | form error, nothing saved | — |
| Gap | no range on a date | cost None | — |
| Discount > face | discounted 120, face 100 | form error | — |
| Deactivate provider | provider off | hidden from `available_for_links()`; history and prices kept; snapshot marks its products inactive | — |
| Snapshot | after a change | regions get ids, codes, currency, active, dated costs only | — |

</frozen-after-approval>

## Code Map

- Admin `catalogue/`: `models.py` `StatusMixin` :30, `Product` :100 (amount + currency pattern :110-138), `service.py` `_with_cents` :49/`DECIMAL_FIELDS` :46, `_validate` :111, `_save` :120, `_create` :136, `_update` :149, `_set_active` :169, `_lock_groups` (row-lock pattern); `views.py` `TABS` :22, `Kind` :310, generic views :351-460, bindings :466-491; `urls.py`; `snapshot.py` :15-33, `apps.py:18`; `audit/registry.py:32-44`; README "Catalogue" :1100-1135. Encryption: `mytp_core/crypto.py` `EncryptedCharField` :489, `EncryptedJSONField` :497, example `clients/models.py:29-67`. Area pattern: `permissions/areas.py:55-58`, `matrix.py` :76-118, `permissions/urls.py`.
- mytp-core: `contracts/catalogue.py` (section model pattern), `contracts/config.py` `OPTIONAL_SECTIONS` :51, `ConfigSnapshot` :63-72, `_without_absent_sections` :74-85; `money.py`; `contracts/amounts.py` `Amount`; exports, `SCHEMA_MODELS`, drift test.
- Later users: 5.6 links (`tickets.toml:58-66`), AD-6 cost at order time (`architecture-portal-admin.md:128`), `review-adversary.md:165-174` (effective-dated costs).

## Tasks & Acceptance

**Execution:**
- [x] mytp-core: `provider_costs` section model (closed per reader rules), config field, absent handling, schema, tests, version, README.
- [x] Admin: `providers` models + migration, service (locks, overlap check, `cost_at`, `available_for_links`), area and matrix, views and forms (encrypted fields shown only to roles allowed), audit, snapshot section, README, tests.
- [x] Portal: vendor the new core only (decision 3a).

**Acceptance Criteria:**
- Given two Financial Managers add overlapping ranges at once, when both save, then at most one is saved.
- Given a snapshot, when inspected, then no provider name, fiscal or payment detail is in it.

## Implementation Notes

**mytp-core 0.19.0** (`../mytp-core`, uncommitted on `story/5.4-providers`):
- `contracts/provider_costs.py`: `ProviderCostsSection {products}` → `ProviderCostProduct {id, code, currency, active, costs}` → `ProviderCost {valid_from, valid_to (null = open-ended, inclusive), face, discounted}`. Closed reader rules (a break refuses the snapshot whole): ids once; `code` 1–64 chars `^[A-Za-z0-9][A-Za-z0-9._/-]*$` (not unique across providers; readers key on the id); currency `^[A-Z]{3}$`; dates strictly `YYYY-MM-DD` (a datetime is refused); `valid_to >= valid_from`; costs listed by `valid_from`, never overlapping; amounts the `DecimalAmount` form, never negative, `discounted <= face`. Helpers `ProviderCostProduct.cost_at(day)` (discounted Decimal or None) and `ProviderCostsSection.product(id)` for the later readers.
- `contracts/config.py`: optional `provider_costs` field, added to `OPTIONAL_SECTIONS` (absent ⇒ dumped/signed as before); `SNAPSHOT_READER`/`min_reader` unchanged. Exports in `contracts/__init__.py`; `config_snapshot.json` re-exported; `tests/test_provider_costs.py`; version 0.19.0 in `pyproject.toml` and `__init__.py` (the version test in `tests/test_hub.py` renamed to `..._0_19_0`); README ("Provider costs section", the compatibility note, the schema list).

**Admin** (`../mytp-admin`, new app `providers`):
- Models: `Provider` (code unique ≤32, name, type `lab`/`aligner_maker`/`service`/`other`, contact name/email/phone, `contract_valid_from/to` informative, comments; `fiscal_id`, `legal_address`, `bank_account`, `payment_terms` as `EncryptedCharField`, so `audit.service` masks them automatically), `ProviderProduct` (provider FK PROTECT, code unique per provider ≤64 with the core's pattern, name, currency fixed after create), `ProviderPrice` (dates, `face_amount`, `discounted_amount` Decimal 12.2; DB check constraints: non-negative, discounted ≤ face, dates in order; unique (product, valid_from)). Migration `providers/0001_initial.py`.
- `service.py`: the catalogue's `_create/_update/_set_active` pattern (audit + `config.rebuild()` in one transaction, no-op edits write nothing). Every price write takes `select_for_update` on the product row first, then `overlap_errors` (inclusive ranges, None = open-ended, excluding the price itself on edit). `cost_at(product, day)` → `Money` (the stored discounted price, which defaults to the face price when not given) or None; `price_at`; `available_for_links()` = active products of active providers.
- `snapshot.py`: `provider_costs` registered by `ProvidersConfig.ready()` (fourth section). `active` = product.active and provider.active; costs ordered by `valid_from`; no provider name, id or detail.
- Area `providers` in the Financial menu group; matrix: Sysadmin all, Financial Manager view/change/export, Commercial Manager view only; `views.can_see_fiscal(user)` = CHANGE on the area, so the CM's pages and forms never carry the four fields. Lists (providers, provider products) with tabs, search, type/currency/status filters and CSV (no fiscal column). Provider page (details, its products), product page (prices table, "Cost on" lookup posted by htmx, read-only, open to viewers), create/edit/deactivate/reactivate for providers and products, add/edit for prices. Ten change views in `audit/registry.py` with exercises in `audit/tests/test_audit.py`.
- `providers/tests/test_providers.py` (45 tests) includes the plan's matrix, the PostgreSQL race (two overlapping adds at once ⇒ exactly one saved; checked to fail when the lock is patched out), ciphertext in the raw column, masked audit values, the snapshot holding no name/fiscal value, CSV and log checks, and per-role page access.
- Vendored `vendor/mytp_core-0.19.0-py3-none-any.whl` (built from the working tree), `requirements.txt`, `layout/tests/test_deploy.py`, `interface/tests/test_vendored_core.py`, README (the app table, the mytp-core paragraph, the snapshot sections, a "Providers" section with the staging check); `interface/config.py` docstring.

**Portal**: the 0.19.0 wheel in `vendor/`, `requirements.txt`, and `integration/tests/test_vendored_core.py` (version and a provider costs section check). No other portal change: the section is published and ignored (decision 3).

**Deviations and choices not spelled out in the plan:**
- The snapshot carries both `face` and `discounted` per range (the plan says "dated costs"); `discounted` is always present (it equals `face` without a discount), so the cost on a date is simply `discounted`. No provider id is sent either.
- Product codes are unique per provider, not globally; the snapshot does not require unique codes.
- `cost_at` returns `Money` (not a bare Decimal); the core helper returns a Decimal (no currency arithmetic there).
- Prices have add and edit but no deactivation (logical deletion was specified for providers and products; to change a price from a date, the open range is ended and a new one added). An edit is re-checked for overlap under the same lock.
- Who sees fiscal details is tied to CHANGE on the area rather than a new named action; the Commercial Regional Manager gets no access to the area (the decision named only the three roles).
- Admin `pytest.ini` `testpaths` gains `providers` and also `country_map`, which 5.3 had left out, so CI did not run the country map tests (they pass).
- `country_map/tests/test_country_map.py` and `catalogue/tests/test_catalogue.py` updated for the fourth registered section.

**Review patches (2026-11-09):**
1. A range without a discount follows its face price: `price_edit` leaves the discounted field empty when it equals the face; `service.update_price` sets the discounted to the new face when the face changes, no discounted is given and the stored discounted equals the stored face (a real discount is kept). Tests: 100 → 120 costs 120, lowering to 80 saves (service and form).
2. "The provider was saved." also for a fiscal-only edit: `views._saved` compares the snapshots' in-memory `masked_values` too. The saved-message test gains an IBAN-only edit.
3. Core: `ProviderCostProduct.cost_money_at(day)` → `Money`, or None for a gap or a currency outside `mytp_core.money.CURRENCIES` (lazy import: `money` imports the contracts package). The module docstring and the core README name it as the helper for 5.6 and epic-payments. Tested. The schema is unchanged (a method only). The wheel was rebuilt and re-vendored in both sites (same file in both) and reinstalled in both venvs.
4. Tests: a PostgreSQL race between an edit that opens a range (`valid_to=None`) and an add of the next range (exactly one saved, no overlap left); the snapshot test names the product "Monitoring Scan Kit" and asserts the name is absent.
5. `ProviderListView` defers `Provider.FISCAL_FIELDS`, and `ProviderProductListView` defers `provider__<field>`, so lists and CSVs never select the fiscal columns (a test checks the SQL of both lists and both exports). `_get` clears the deferral (`defer(None)`) for the detail and change pages.

## Plan Change Log

## Review Triage Log

Opus review of core + admin (2026-11-09); the portal only vendors the core. No blocking defects; confidentiality, snapshot shape, overlap under the lock, date boundaries, contract rules, permissions and logical deletion checked.
- patch (admin, medium): editing the face price of a range without a discount kept the old amount as a discount (the form pre-filled it) → the edit leaves the discount empty when equal to face, and the service follows the face when no discount was set; tests.
- patch (admin, low): no "saved" message for a fiscal-only edit (masked values compared equal) → masked values compared too; test.
- patch (core, low): the docstring promised readers skip unknown currencies → `cost_money_at(day)` returns Money or None for a gap or unknown currency; README note for 5.6 / epic-payments.
- patch (admin, low): race test for an edit opening a range vs an add; the snapshot test asserts the product name is absent.
- patch (admin, defence in depth): fiscal fields deferred in lists and CSVs; a test checks the SQL never selects them.

## Verification

**Commands:**
- mytp-core, admin and portal suites — all pass; CI on PostgreSQL.

**Verification results (2026-11-09, local, nothing committed):**
- mytp-core: SQLite 1333 passed, 4 skipped (the PostgreSQL-only restore e2e); PostgreSQL 17 1337 passed.
- Admin: SQLite 1133 passed, 9 skipped (row-lock tests); PostgreSQL 17 1142 passed (the providers race test also run 5 times in a row, and confirmed to fail with the lock patched out). `manage.py check` clean; `makemigrations --check` no changes.
- Portal: SQLite 2337 passed, 8 skipped, 3 failed (the 3 known PostgreSQL-only tests in `accounts/tests/test_crypto.py`); PostgreSQL 17 2348 passed. `manage.py check` clean; `makemigrations --check` no changes.
- PostgreSQL runs on a throwaway 17.11 cluster under `$TMPDIR`, stopped and deleted afterwards.
- After the review patches: mytp-core SQLite 1334 passed, 4 skipped; PostgreSQL 17 1338 passed. Admin SQLite 1136 passed, 10 skipped (row-lock tests); PostgreSQL 17 1146 passed (both providers race tests 5 runs in a row). `check` clean, `makemigrations --check` no changes. Portal: the rebuilt wheel's `integration/tests/test_vendored_core.py` 10 passed (the full portal suite was not re-run; only the vendored wheel changed there). Fresh throwaway cluster, deleted afterwards.

**Manual checks (staging, admindev):**
- Create a provider with fiscal details and a product priced 100 until 30 June, 90 from 1 July; cost lookups either side; an overlapping range refused; the raw database shows ciphertext for fiscal fields; deactivate the provider → hidden from the available list, history kept; dev's applied snapshot holds the costs section without provider details.
