---
title: 'Billing companies and billing rules'
type: 'feature'
ticket: '9'
created: '2026-11-11'
status: 'done'
baseline_revision: '8dee5c4254baa12043c20816fc6c01979ac1c9a9'
route: 'full'
route_source: 'auto'
risk: 'high'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** C8 (AD-6, AD-7, AD-16): the portals price products but do not know which of the three companies (UAE, Ireland, Spain) bills a doctor, with what VAT or exemption text, through which payment gateway, or whether a product can be sold in the doctor's country at all.

**Approach:** On the admin site, add the billing companies and the billing rules (one per country group and product category), published in the catalogue section (mytp-core 0.23.0, optional parts); the portal stores them and the resolver, for a doctor's country and a product, gives the rule (company, VAT rate or exemption text, gateways) and stops offering a product whose pair has no rule or is marked unavailable.

## Boundaries & Constraints

**Always:**
- The resolver stays the only code that decides company, VAT and gateway (AD-16). A 0 rate always carries its exemption text (AD-7).
- Rates and texts are data typed by staff (the tax advisor's wording); nothing about a rate is in the code.
- Contract compatibility as in entries 5 to 8: new parts optional, absent ones not dumped; a snapshot without rules keeps today's offers.
- Never hard-deleted: companies and rules are deactivated.
- Every change audited and rebuilding the snapshot.

**Never:** no VAT amounts, VIES decision, cart or one-company-per-order refusal (entry 12 applies what this entry stores); no invoice series, numbers or bank details (epic-payments, AD-7); no VIES lookup (epic-doctor-profile); no per-region payloads (entry 11).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Company | code `ES`, legal name, address, country ES, VAT number, time zone Europe/Madrid | saved, in the snapshot, readable on dev | a taken code, an unknown time zone or country, or no identifier at all refused |
| UAE company | company number / license, VAT registration and TAX registration | the three identifiers stored and sent | — |
| Rule | Iberia × `tps_branded` → the Spanish company, rate 0 with its exemption text, `redsys` then `bank_transfer`, EUR, available | ES and PT resolve to it | a rate outside 0–100 refused; a rate of 0 without an exemption text refused |
| Domestic and reverse charge | Rest of EU × `tps_branded` → the Irish company, 23 %, a reverse-charge text, `revolut`, EUR | IE and DE both resolve to it, with the rate and the reverse-charge text available to entry 12 | — |
| Export | Rest of world × `tps_branded` → the Irish company, rate 0 with its export text | US resolves to it | — |
| UAE rule | UAE × a category → the UAE company, 5 %, no gateway, AED, available | AE resolves to it with an empty gateway list; the products are offered | — |
| One rule per pair | a second active rule for the same country group and category | refused | a deactivated rule does not count |
| No rule | rules exist; a group × category pair has none | the products of that category are not offered to that group's doctors; the admin site lists the pair as missing | — |
| Unavailable | a rule marked not available | its products are not offered there | — |
| Currency | Iberia × `tps_branded` sold in EUR; a USD clone linked to the subscription | an ES doctor is offered the EUR product, not the USD or AED clone | — |
| Subscription price | the `subscription` category rule of a subscription's country group | readable on dev as the rule of that subscription's own price | none: the price has no rule |
| Gateways | `redsys`, `revolut`, `bank_transfer` in a chosen order | kept in that order | an unknown gateway or a repeated one refused |
| Who edits | Financial Manager or Sysadmin | can add, change, deactivate companies and rules | Commercial Manager and Commercial Regional Manager see them, cannot change them |
| Profile mismatch | the snapshot's `IE` company has another legal name or registration than the eu profile's | a warning line at apply and a line in `price_catalogue`; nothing refused | — |
| Old snapshot | an admin before this release (no rules list) | dev keeps working: every linked product offered, in every currency, no billing data | — |
| Old portal | a portal on mytp-core 0.22 and a snapshot with companies and rules | it applies the snapshot and ignores them | — |

## Decisions

- Decision (user, 2026-11-11): a rule holds the VAT rate, the exemption text shown when the rate is 0, and an optional reverse-charge text. Entry 12 uses the reverse-charge text (rate 0) when the doctor's VIES number is valid and their country is not the company's own; otherwise the rate.
- Decision (user, 2026-11-11): a rule has zero or more gateways, in order of preference, from a fixed list in code (`redsys`, `revolut`, `bank_transfer`). The UAE has no bank transfer at all: its rules stay without a gateway until the Wio gateway works (it joins the list with its adapter, epic-payments).
- Decision (user, 2026-11-11): a rule names the currency its pair is sold in (EUR, AED or USD); a doctor is offered only the products in that currency.
- Decision (user, 2026-11-11): a subscription's own price is billed under the `subscription` category rule of the subscription's country group.
- Decision (user, 2026-11-11): companies and rules are edited by the Financial Manager and the Sysadmin (a new `billing` area in the financial group); the Commercial Manager sees them.
- Decision (user, 2026-11-11): a company has a short code (ES, IE, AE), legal name, address, country, legal time zone and three identifiers: company number or license, VAT registration, and TAX registration (the UAE company has the three). Bank details wait for epic-payments.
- Decision (user, 2026-11-11): the Spanish company is MyTPDSO Spain S.L., Avda. Labradores, 1, Edificio COS, Planta 4, Oficina 6, TRES CANTOS 28760 (MADRID), VAT / TAX ID B93858371.
- Decision (user, 2026-11-11): when the snapshot's company for the region has another legal name or registration than the region profile's, the portal logs a warning at apply and `price_catalogue` says so; nothing is refused.
- Decision (user, 2026-11-11): once rules exist, a country group and category without a rule is not offered, as a rule marked unavailable; the admin site lists the missing pairs.
- Decision (user, 2026-11-11): one plan, kept whole; mytp-core 0.23.0 → admin → portal.

</frozen-after-approval>

## Code Map

- Repositories: this plan is in the portal (`mytreatplan.com`); mytp-core is `../mytp-core` (baseline 997ec60a21000e76068a76e4557b8080cf531218) and the admin site `../mytp-admin` (baseline ed1f2a8952a3687baeb6e7a34eca8b2f3fb876bb). The branch `story/5.9-billing-rules` is checked out in all three: work there, commit nothing, push nothing, tag nothing. Do not build or copy a wheel: the release rebuilds it from the tag in both sites; point both `requirements.txt` at `./vendor/mytp_core-0.23.0-py3-none-any.whl`, update the version tests (the admin site's `layout/tests/test_deploy.py` pins the wheel name), and run the two site suites with `PYTHONPATH=../mytp-core` until then. Run each repository's full suite as its README says (the portal's venv is `../.venv`; the admin site's test venv is `/private/tmp/claude-501/admin-venv`). The line numbers below predate small edits on master; search by name when one is off.

- Decisions already made: initiative :71 (UAE company bills the UAE; Ireland the rest; Spain bills Spain and Portugal), :76 (Redsys for Spain, Revolut for Ireland, the UAE gateway open), :80, :92 (rates are configuration; the tax advisor's texts pending); epic Notes :64 (VIES is an input of the resolver); AD-6 :128, AD-7 :134, AD-16 :193; epic-payments tickets :18 (gateway ids incl. `bank_transfer`), :63 (company short code). Requirements: TPS VAT-exempt in Spain; Ireland 23 %; UAE 5 %; EU B2B with VIES and buyers outside the EU exempt.
- Portal 1.6: `regions/profiles.py:28-31` `RegionCompany` (name, registration): eu "MyTPDSO Limited", "Company number 815526 (Ireland)", gateways `('redsys', 'revolut', 'bank_transfer')`; ae "MyTPDSO L.L.C-FZ", "License 2542361.01 (Dubai)", gateways `()`; pinned by `regions/tests/test_profiles.py:14-38`: unchanged.
- Admin: `catalogue/models.py` `CountryGroup` :168, `Product.Category` :241, `SUBSCRIPTION_CATEGORIES` :622 (the categories a rule can name); `views.py` `Kind` :753, :860-869, generic `_create/_edit/_state_change` :1004-1048; `service.py` `_create/_update/_set_active` :233-300, `_lock_groups` :146-160 (pattern for a cross-row check under a lock); `snapshot.py` `section` :123; `audit/registry.py` `AUDITED_VIEWS` :15-53; `permissions/matrix.py:100-123`, `permissions/areas.py:21,65-70` (`providers`: the precedent for an area in the financial group); `providers/models.py:34,63-80` (encrypted fields: not needed here, company data is printed on invoices).
- mytp-core: `contracts/catalogue.py` `CatalogueSection` :923-995 (optional lists :930, :933; cross-row rules in `_consistent` are reader rules); `contracts/config.py:29-30,56`; `money.py`.
- Portal: `catalogue/models.py`, `applier.py:102-146` (delete then recreate; rows with a foreign key first), `resolver.py` :74 ("VAT … and the billing company come with later entries"), `country_group` :123, `offers_under` :286, `offers` :302 (`[(product, Money)]`, pinned by `tests/test_catalogue.py:85,343-353,401`, `test_add_ons_costs.py:232,328`, `test_subscriptions.py:202,249,398`); `price_catalogue`.

## Tasks & Acceptance

**Execution:**
- [x] mytp-core 0.23.0 (`mytp_core/contracts/catalogue.py`, `schemas/config_snapshot.json`, version, README, tests): the section's optional `billing_companies` (id, code, legal name, address, country, time zone, the three optional identifiers, active) and `billing_rules` (id, country group, category, company, VAT percent, exemption text, optional reverse-charge text, gateways in order, currency, available, active); cross-row rules on the new parts only (a rule's group and company are listed; at most one active rule per group and category; a 0 rate has its text; a company code is unique); absent parts not dumped; helpers: the rule of a group and category, whether a pair is offered (no rules list: yes; a list: an active, available rule).
- [x] Admin (`catalogue/` or a new `billing` app beside it, `permissions/areas.py`, `permissions/matrix.py`, `audit/registry.py`, `catalogue/snapshot.py`, migrations, README, tests): `BillingCompany` and `BillingRule` with list, form, detail, deactivate and reactivate (the `billing` area: change for Financial Manager and Sysadmin, view for the Commercial Manager and the Commercial Regional Manager); one active rule per pair under a lock; the list of group × category pairs without an active rule; the fixed gateway list; snapshot entries.
- [x] Portal (`catalogue/models.py`, `applier.py`, `resolver.py`, `management/commands/price_catalogue.py`, a migration, `deploy/README.md`, tests): store companies and rules; resolver: `billing_rule(country, category)`, `offers_under` and `offers` leave out products whose pair has no available rule or whose currency is not the rule's (only when the snapshot has rules), `subscription_rule(subscription)`; the profile mismatch warning; `price_catalogue` prints each offer's company, VAT or text, gateways, and takes a country and category to print one rule.
- [x] Both sites: vendor the new core (and the pinned wheel name in the admin site's `layout/tests/test_deploy.py`).
- [x] Tests for every row of the matrix; the tests that pin the offers of a snapshot without rules stay as they are.

**Acceptance Criteria:**
- Given rules for the test countries, when dev resolves ES, PT, IE, DE, US and AE for each category, then each gives the expected company, VAT rate or exemption text and gateways.
- Given a country group and category without a rule, when dev lists a doctor's offers there, then the products of that category are absent.
- Given two active rules for one country group and category, when the second is saved, then it is refused.

## Implementation Notes

- mytp-core: "no rules" is an absent `billing_rules` or an empty list (the decision says "once rules exist"); `BILLING_GATEWAYS`, `known_gateways` and the plain helper `billing_offers` are shared by both sites. "No identifier at all" is the admin site's rule, not the contract's.
- Admin: a new `billing` app (area `billing`, Financial group). The snapshot lists are sent only once they have a row, so the section is unchanged until staff type something, and the first rule is what switches the portals to "only what has a rule". Added beyond the matrix: an active rule names an active company (a company with active rules is not deactivated). Gateways are one select per position of the order of preference.
- Portal: every rule is stored as sent (a rule in an unknown currency or category offers nothing); `BillingRule.country_group` has no database constraint so the release before can still replace the country groups; `offers_under` takes an optional `country` (the doctor's group; default: the subscription's). The region to company code map (eu: IE, ae: AE) is in `regions/profiles.py` (`BILLING_COMPANY_CODES`; `RegionCompany` unchanged). The registration matches when the company number or license is the profile's registration text or is found in it. `price` is unchanged (entry 12 applies the rule to a line).
- Both sites point at `./vendor/mytp_core-0.23.0-py3-none-any.whl`, which the release builds: until then the admin site's `layout/tests/test_deploy.py::test_requirements_ship_the_worker_and_the_vendored_mytp_core` (wheel file) and `staff/tests/test_users.py::test_region_urls_default_to_production` (a subprocess without `PYTHONPATH`) fail locally.

## Plan Change Log

## Review Triage Log

Quick lens, one reviewer per repository (2026-11-12). 17 findings: 9 patched, 8 rejected.

| # | Repository | Finding | Verdict | Evidence | Route |
|---|-----------|---------|---------|----------|-------|
| 1 | core | `billing_rule` and the one-rule-per-pair check read the old `tps` code on one side only | low | `_consistent` keyed the pair on the raw category and `billing_rule` compared the raw one: a `tps` rule was never found and escaped the pair check. The admin site never sends `tps` | patch: both read `tps` as `tps_branded`; test |
| 2 | core | a blank `reverse_charge_text` is accepted | low | no blank check, while a blank exemption text on a 0 rate is refused; the admin site strips and leaves it out | patch: refused when present and blank; tests |
| 3 | core | a whitespace-only identifier is accepted | low | only `min_length=1`; the admin site strips | patch: refused; test |
| 4 | core | "rules exist" does not say that deactivated rules count | low | `billing_offered` and both sites count any rule; the docstring and README did not say so | patch: docstring and README |
| 5 | core | `billing_offered` ignores the currency | false | the plan's helper is "whether a pair is offered"; its docstring points to `CatalogueBillingRule.offers` for the currency and the portal applies it (`billing_offers`) | rejected |
| 6 | portal | a rule under an inactive company still bills and offers | low | the admin site refuses that state under its locks, but the contract did not, so a portal trusted it unchecked | patch (core): an active rule names an active company; test |
| 7 | portal | an uncategorised product is never offered once a rule exists | low | true, and wanted: a pair without a rule is not offered, and only a product of the former "Other" category is still uncategorised (the admin form makes the editor choose a category) | patch: said in both READMEs |
| 8 | portal | two active rules of one pair after `tps` is folded; two code paths pick different ones | low | same cause as 1: the contract now refuses that section | patch (with 1) |
| 9 | portal | the registration match is a substring test | medium | "81552", "5" or "ireland" matched "Company number 815526 (Ireland)": the check missed the likeliest typing mistake | patch: the whole text, or one of its words with a digit; tests |
| 10 | portal | `price_catalogue --category` does not refuse a malformed country | low | it prints "no billing rule for ESP …", as the `--tier` path prints "No active … subscription applies"; an operator's command | rejected: unlikely to hurt, the fix adds a branch |
| 11 | portal | `price_catalogue` runs 2 queries per offer | low | the command only; `offers` adds 3 fixed queries | rejected |
| 12 | portal | the mismatch check reads the company number only, not the VAT or TAX registration | false | the profile's registration is the company number or license ("Company number 815526 (Ireland)", "License 2542361.01 (Dubai)"); a company typed without it is a difference worth the warning | rejected |
| 13 | portal | `BillingRule.vat_text` and `resolver.billing_company` are used by tests only; the VAT text is built twice | low | no wrong output; entry 12 reads the rule's fields | rejected |
| 14 | admin | the switch to "only what has a rule" cannot be undone; a list of deactivated rules offers nothing | low | as decided ("once rules exist"), and closed is the safe side for billing; rules are never deleted. The deactivate note did not say it | patch: the note and the README say it. Told to the user at the checkpoint |
| 15 | admin | the "no identifier" error is hidden while another field has an error | low | `if not self.errors and …` in `CompanyForm.clean`; nothing wrong is saved | patch: the guard removed; test |
| 16 | admin | the service accepts an inactive country group | low | the form offers active groups only; such a rule applies to nobody and the snapshot stays valid | rejected: the fix adds a guard for a state only a script reaches |
| 17 | admin | `_save` turns every integrity error into "taken" | false | `full_clean` and `clean()` refuse the same conditions first; `catalogue.service._save` does the same for a product | rejected |

## Verification

**Commands:**
- mytp-core, admin and portal suites — all pass; CI on PostgreSQL.

**Manual checks (staging):**
- On admindev: the three companies (ES with the Spanish company's data above; IE and AE with the region profiles' names and registrations, AE with its three identifiers as made-up staging values); rules for Iberia, Rest of EU, Rest of world and UAE on the TPS and VirtuaOrtho categories and `subscription`, with staging texts; a second rule for one pair refused. On dev: `price_catalogue` for ES, PT, IE, DE, US and AE shows the expected company, VAT or text and gateways; a category without a rule is no longer offered; an ES doctor is not offered the USD and AED clones; no profile mismatch warning for IE.

## Staging

Checked 2026-11-12 (merge and staging delegated by the user).

- Release: mytp-core v0.23.0 (92ab671; CI green on master and the tag). Admin PR #33 (merge 56c6a6d; master run 43234116605, tests and deploy green). Portal PR #91 (merge dcc3334; master run 43235142960, tests and both deploys green). Suites against the tag's wheel: core 1501, admin 1458, portal 2540 plus the 3 known PostgreSQL-only failures. The first CI run of the admin PR failed on the two-writers test (it relied on rows the migrations seed); the test now creates its own country and group, and passed on PostgreSQL.
- After the portal deploy, before any rule: the first `pull_config` on dev and dev-ae printed `applied` (v62), the second `unchanged`; ES basic listed 18 offers, as before.
- admindev, as `staging_check_5_9` through the service, in one transaction: the companies ES (MyTPDSO Spain S.L., VAT registration B93858371, Europe/Madrid), IE (MyTPDSO Limited, company number 815526) and AE (MyTPDSO L.L.C-FZ, license 2542361.01, made-up VAT and TAX registrations), and 32 rules with staging texts: Iberia, Rest of EU, Rest of world and UAE × `tps_branded`, `tps_in_house`, `virtuaortho_one`, `virtuaortho_digitaltwin`, `odb`, `spark_on_demand`, `initial_case_evaluation`, `subscription` (Iberia: ES, 0 % with its text, `redsys` then `bank_transfer`, EUR; Rest of EU: IE, 23 % with a reverse-charge text, `revolut`, EUR; Rest of world: IE, 0 % with an export text, `revolut`, USD for `tps_branded` and EUR for the rest; UAE: AE, 5 %, no gateway, AED). Snapshot v62 → v97, 3 companies and 32 rules in it.
- Refused on admindev: a taken company code, a company without any identifier, a second active rule of Iberia × `tps_branded`, a rate of 0 without its text, a repeated gateway, deactivating a company with active rules. The missing pairs: 8 (`retainers`, left without a rule on purpose, and `education`, in the four groups).
- dev (v97): `price_catalogue --country <C> --category tps_branded` gave ES and PT the Spanish company at 0 % with its text, `redsys, bank_transfer`, EUR; IE and DE the Irish company at 23 % with the reverse-charge text, `revolut`, EUR; US the Irish company at 0 % with the export text, USD; AE the UAE company with its three identifiers, 5 %, no gateway, AED. ES basic: 15 offers, each with its billing line: the Retainers product and the USD and AED clones are gone. ES × `retainers`: "no billing rule". The subscription's own price shows the `subscription` rule; the manual subscription STG58-ORMCO-PREM shows its group's rule. No profile mismatch warning.
- dev-ae (v97): AE × `tps_in_house` gave the UAE company, 5 %, no gateway, AED; no profile mismatch warning for the AE company.
- Not checked: the admin pages in a browser (2FA); the same service calls and the page tests cover them.
- Staging data left on admindev: the three companies and the 32 rules. `retainers` and `education` have no rule, so they are not offered on dev and dev-ae.
