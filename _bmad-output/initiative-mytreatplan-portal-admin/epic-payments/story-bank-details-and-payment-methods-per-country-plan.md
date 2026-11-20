---
title: 'Bank details and payment methods per country'
type: 'feature'
ticket: '14'
created: '2026-11-20'
status: 'done'
baseline_revision: 'e4eed081d7b6354d690f7bb9a936ccf7cd71fbd6'
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

- **Build setup (as for 6.9):**
  - Branch `story/7.14-bank-details-per-country` is checked out in all three repos (portal = this repo, baseline e4eed08; `../mytp-core` baseline cf21dea; `../mytp-admin` baseline c6132c9). Commit nothing, push nothing, tag nothing: the release is the main session's.
  - Core venv `/private/tmp/claude-501/core-5.17-venv`, admin venv `/private/tmp/claude-501/admin-5.17-venv` (reinstall the core into them as needed). Portal: do not install the local wheel into `../.venv` (shared); use `/private/tmp/claude-501/portal-6.9-venv` (reinstall the portal requirements with the local wheel).
  - Local wheel: copy the core working tree's tracked files (`git ls-files | rsync --files-from=-`) to a temp dir, `SOURCE_DATE_EPOCH` = core HEAD commit time, `pip wheel --no-deps`, put it in both sites' `vendor/` and move each `requirements.txt` line to 0.29.0 (remove the 0.28.0 wheel). The main session re-vendors from the tag with `scripts/vendor-mytp-core.sh v0.29.0` before the PRs.
  - Portal suite (repo root, sandbox off): `FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU=" DATABASE_URL="sqlite:///$TMPDIR/p714.sqlite3" <venv>/bin/python -m pytest -q -p no:cacheprovider --tb=line` (0 failures expected; PostgreSQL-only tests skip).
  - Admin suite (in `../mytp-admin`): `SECRET_KEY=ci-only-not-a-secret DEBUG=False SECURE_SSL_REDIRECT=False ALLOWED_HOSTS=localhost,127.0.0.1,testserver DATABASE_URL=sqlite:///$TMPDIR/a714.sqlite3 FIELD_ENCRYPTION_KEYS=<portal test key above> BLIND_INDEX_KEY=<portal test key above> ALLOW_FAKE_VERIFICATION=True <venv>/bin/python -m pytest -q -p no:cacheprovider`.
  - Core suite (in `../mytp-core`): `<venv>/bin/python -m pytest -q -p no:cacheprovider`; regenerate the JSON schemas with the core's own script and the test that checks them.
  - Previous baselines: core 1606 / 4 skipped, admin 1623 / 12, portal 3794 / 13.

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

- Built by a Sonnet implementer; Opus quick review (6 findings: 5 patched, 1 rejected, table below).
- mytp-core 0.29.0 = d8ad580, tag v0.29.0; both sites vendor the tag's wheel (sha256 c7c6b2c8…9f49). Admin migration `billing` 0004, portal `catalogue` 0010 (with `db_default`s so the previous release can still insert during a deploy).
- Beyond the plan: admin audit entries mask the bank fields and creditor id (`AUDIT_MASKED`: set/empty/changed); the admin refuses a group change that drops a country a rule limit names; the rule form asks to re-check a limit when its gateway moves position; `price_catalogue` prints the gateways per country.
- BIC: `^[A-Z0-9]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$` (ISO 9362:2014). The JSON schema carries no pattern for the bank fields: checking is the contract's code.
- Release order: core, then the portal (a 0.28.0 portal would ignore the limits), then the admin.
- Suites (main session, before the tag re-vendor): core 1641 / 4 skipped, admin 1651 / 12, portal 3805 / 13; makemigrations clean in both sites; after the re-vendor the vendoring, billing and catalogue/payments tests pass on the tag wheel.

## Plan Change Log

## Review Triage Log

Quick lens (Opus), 2026-11-20:

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | admin `catalogue/service.py` group update: removing a country a rule limit names makes `snapshot_body` raise pydantic's error → 500 | medium | patch | the section check is new; group `_validate` checks only the group; views catch Django's `ValidationError` only |
| 2 | portal `price_catalogue` prints `known_gateways` per country | low | patch | `print_rule(country, …)` knows the country; direct correction |
| 3 | admin `RuleForm`: a limit follows the position, so swapping gateways moves "ES only" from bank_transfer to redsys silently | medium | patch | `clean` maps `gateway_countries_{n}` to whatever gateway is at n; no check against the stored rule |
| 4 | core `BIC_PATTERN` requires letters in the first 6 characters; plan and ISO 9362:2014 allow alphanumeric party prefix | low | patch | direct correction of the pattern |
| 5 | core JSON schema lacks patterns for iban/bic/creditor id and list constraints | low | reject | every consumer validates with the pydantic contract; adding schema extras is more than a direct correction |
| 6 | admin catch-all test can `pytest.skip`; group-country lookup duplicated in model and form | low | patch | deletion of the skip and of the duplicate (form uses `BillingRule.group_country_codes`) |

## Verification

**Commands:** core, admin and portal full suites (README commands); `makemigrations --check` in admin and portal.

**Manual checks (staging):**
1. admindev: type the ES company's bank details (staging test IBAN `ES9121000418450200051332`, BIC `CAIXESBBXXX`) and a test creditor id; limit bank_transfer to ES on the Iberia rule(s); publish.
2. dev: snapshot applied (company fields present, rule limit stored); an ES test doctor's checkout lists Bank transfer only after entry 5 ships (until then: `pricing` gateways include `bank_transfer` for ES, not for PT, checked in a shell script); dev-ae unchanged.

## Staging

- 2026-11-20. mytp-core 0.29.0 (d8ad580, tag v0.29.0, core CI green). Portal PR #117 merged 3a14795 (CI test pass; master run 44727118701 green, dev + dev-ae deployed); admin PR mytp-admin#44 merged ebd3aeb (admindev deployed). Suites: core 1641 / 4, admin 1651 / 12, portal 3805 / 13.
- admindev (real writes, `staging-7.14` command actor): billing 0004 applied; a partial set refused on `bic`, a bad IBAN checksum on `iban`; the ES company has staging bank details (CaixaBank test IBAN, normalised) and a test creditor id; its audit entry masks the values; bank_transfer limited to ES on the 9 active Iberia rules; the snapshot moved; removing ES from Iberia refused on `countries` (rolled back).
- dev: catalogue 0010 applied; ES company has bank details and creditor id, IE none; 9 rules with limits; tps_branded gateways ES redsys + bank_transfer, PT redsys, IE and DE revolut. dev-ae: no bank details, no limits.
- Checks 1 and 2 of the plan: confirmed.
