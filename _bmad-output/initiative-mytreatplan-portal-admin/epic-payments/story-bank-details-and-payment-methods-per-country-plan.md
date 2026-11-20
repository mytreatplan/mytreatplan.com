---
title: 'Bank details and payment methods per country'
type: 'feature'
ticket: '14'
created: '2026-11-20'
status: 'ready-for-dev'
baseline_revision: ''
route: 'full'
route_source: 'auto'
risk: 'high'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Bank transfer (entry 5) needs the billing company's bank details, SEPA Direct Debit (entry 15) its creditor identifier, and accounting wants each payment method switched on per country (bank transfer in Spain only at launch; some countries never). Today a billing company has no bank fields and a billing rule's gateways apply to its whole country group (Iberia = ES + PT).

**Approach:** One mytp-core minor (0.29.0) carried through the admin site and the portal: optional bank details and SEPA creditor id on a billing company; an optional per-gateway country limit on a billing rule. The portal's pricing keeps a gateway only for a billing country it allows. Configuration only: no new payment flow.

## Boundaries & Constraints

**Always:**
- All new contract fields optional and left out of a dump when absent, so a 0.28.0 snapshot stays valid (the 0.25.0 `region` pattern).
- Bank details all four or none. IBAN stored uppercase without spaces, 15-34 characters, ISO 13616 mod-97 valid; BIC 8 or 11 `[A-Z0-9]`; holder and bank name one line, 1-140. SEPA creditor id uppercase without spaces, 8-35 `[A-Z0-9]`, mod-97 valid per the EPC rule (the 3 business-code characters skipped).
- A rule's country limit names only gateways of that rule and countries of its group (a catch-all group: any country of its region); a gateway without a limit is offered in the whole group, as today.
- Bank details and creditor id never in logs or Sentry; admin audit entries name the changed fields, not their values (the 6.9 pattern for personal data is not needed: these are company data, but IBANs stay out of logs).

**Never:** a payment flow (entries 5 and 15); per-country prices or VAT; changes to the UAE profile's gateways.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Company with full bank details | admin form, 4 valid fields | saved, in the snapshot, stored by the portal | No error expected |
| Partial bank details | 3 of 4 | form error on the missing field | contract refuses too |
| Bad IBAN / BIC / creditor id | checksum or shape wrong | field error | contract refuses too |
| IBAN with spaces, lower case | `es91 2100 0418 4502 0005 1332` | stored `ES9121000418450200051332` | No error expected |
| Rule limit | Iberia rule: bank_transfer only ES | ES order offers it; PT order does not; redsys in both | No error expected |
| No limit | rule without limits | every gateway in the whole group (today's behaviour) | No error expected |
| Limit on an unknown gateway or a foreign country | `revolut` not in the rule, or `FR` not in the group | form error | contract/snapshot check refuses too |
| Old snapshot | 0.28.0 body | applies; fields empty, no limits | No error expected |

## Decisions (user, 2026-11-20)

1. Split from entry 5; carries the SEPA creditor identifier too.
2. Payment methods per country; bank transfer in Spain only at launch, some countries never (financial risk assessment). Limits are typed in the admin by staff.

</frozen-after-approval>

## Code Map

- mytp-core `mytp_core/contracts/catalogue.py`: `COMPANY_OPTIONAL` :1089, `CatalogueBillingCompany` :1115 (validator :1139), `CatalogueBillingRule` ~:1145 (`gateways: list[Code]`), section docstring :182-250 ("No bank details" :202), snapshot-level checks of rules vs groups/companies (search `country_group` checks in the section validator). Version 0.28.0 in `pyproject.toml`, `mytp_core/__init__.py:52`; changelog in the module docstring list (`# 0.25.0:` style comments :358).
- mytp-admin `billing/models.py:73` `BillingCompany` (+ `clean`), `BillingRule` (`gateways` :186, `clean` :225), `billing/views.py:205` `CompanyForm`, `RuleForm` :265 (per-position gateway fields :332 `_gateway_field`), `billing/snapshot.py` `company_entry`/`rule_entry`; next migration `billing` 0004; vendored wheel test `interface/tests/test_vendored_core.py`; `billing/tests/test_billing.py`.
- Portal `catalogue/models.py:389` `BillingCompany`, `BillingRule` (`known_gateways` :469); `catalogue/applier.py:235-250`; next migration `catalogue` 0010; `catalogue/pricing.py:_price` (country :~336, gateways :~359-366); vendored wheel path and pin (follow 6.9: `vendor/` + requirements hash).

## Tasks & Acceptance

**Execution (in this order; one branch `story/7.14-bank-details-per-country` in each repo):**
1. mytp-core: `CatalogueBillingCompany` + `bank_account_holder`, `iban`, `bic`, `bank_name`, `sepa_creditor_id` (optional, normalised before validation, all-four-or-none for the bank fields); `CatalogueBillingRule` + `gateway_countries: dict[Code, list[CountryCode]] | None` (keys in `gateways`, lists non-empty, no duplicates); the section check refuses a country outside the rule's group (catch-all: outside its region's countries). Public helpers `normalise_iban`, `valid_iban`, `valid_bic`, `valid_creditor_id` for the sites. Docstrings, `0.29.0` changelog line, version bump, tests (valid/invalid each field, old body still valid, round trip). Commit + tag `v0.29.0` locally.
2. mytp-admin: model fields (`blank=True`) + `gateway_countries` JSON (`default=dict`) + migration 0004; `clean()` uses core's helpers; `CompanyForm` "Bank details" + "SEPA creditor id" fields; `RuleForm` one optional "Only in countries" multi-select per gateway position, choices from the chosen group's countries; `company_entry`/`rule_entry` add fields only when set; detail pages show them (IBAN grouped by 4); vendor 0.29.0; tests.
3. Portal: vendor 0.29.0; `BillingCompany` five fields (`default=''`), `BillingRule.gateway_countries` (`default=dict`), migration 0010; applier copies them; `BillingCompany.has_bank_details`; `BillingRule.gateways_for(country)` (known gateways, limited per country); `_price` uses it for every line; docstrings; tests (matrix rows "Rule limit", "No limit", "Old snapshot").

**Acceptance Criteria:**
- Given the ES company with valid bank details and creditor id typed on the admin, when the snapshot is applied on the portal, then the portal's company has them.
- Given three of four bank fields, or an IBAN with a wrong checksum, when staff save, then the form shows a field error and nothing is saved; the contract refuses the same body.
- Given the Iberia rule limits bank_transfer to ES, when an ES and a PT doctor price the same product, then only the ES order's gateways include bank_transfer.
- Given a 0.28.0 snapshot, when the portal applies it, then it applies with empty bank fields and no limits.
- Given the three repos' full suites, then 0 failures and no missing migrations.

## Implementation Notes

## Plan Change Log

## Review Triage Log

## Verification

**Commands:** core, admin and portal full suites (README commands); `makemigrations --check` in admin and portal.

**Manual checks (staging):**
1. admindev: type the ES company's bank details (staging test IBAN `ES9121000418450200051332`, BIC `CAIXESBBXXX`) and a test creditor id; limit bank_transfer to ES on the Iberia rule(s); publish.
2. dev: snapshot applied (company fields present, rule limit stored); an ES test doctor's checkout lists Bank transfer only after entry 5 ships (until then: `pricing` gateways include `bank_transfer` for ES, not for PT, checked in a shell script); dev-ae unchanged.
