---
title: 'Per-region catalogue'
type: 'feature'
ticket: '16'
created: '2026-11-13'
status: 'built'
baseline_revision: 'd04c49ec14ed7fefa130c1b9cafa5e118da17e19'
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

**Problem:** C10, C3, C7, C8 (AD-3, AD-14): the catalogue has no region. Country groups, companies, products and subscriptions are one global set sent whole to every portal, Rest of world is one group shared by both regions, and dev stores and can price from the UAE records.

**Approach:** Catalogue records get a region on the admin site; the admin site serves each portal only its own region's catalogue through entry 11's filter (mytp-core 0.25.0), and a portal skips anything of another region.

## Boundaries & Constraints

**Always:**
- Decision (user, 2026-11-12): every region has its own country groups, companies, products and subscriptions as well as its vouchers, and each portal receives only its own.
- A record's region never changes after creation, so a record never vanishes from a portal that has it (AD-6: past orders read it).
- A body served to a portal stays valid for a portal on mytp-core 0.24.0 (it ignores the new field).
- A region left without billing rules is not switched back to "offer everything" by the filter.
- One stored body and one version (entry 11).

**Never:** no change to prices, discounts or the order of the pricing steps (entry 12); no staging seed (entry 13); no change to vouchers, the country map's content or the admin directory.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Create with a region | a country group, a billing company or a product created for region ae | saved with that region; in the body served to dev-ae; absent from the one served to dev | a missing or unknown region refused |
| Region is fixed | an edit that changes the region of a group, company or product | — | refused |
| Group's countries | a group of ae listing a country that eu serves | — | refused, naming the country |
| Country map move | a country listed by an active group moved to another region or banned | — | refused until it is removed from the group |
| Catch-all per region | Rest of world (eu) and Rest of Middle East (ae) both active catch-alls | both saved; each catches the countries its own region serves that no other group of that region lists | a second active catch-all in one region refused |
| Group name | a name already used in the same region / in another region | the second case saved | the first refused |
| Subscription | a subscription of an eu group linking an ae product | — | refused; the form offers only its region's products |
| Subscription's group | a subscription moved to a group of another region | — | refused |
| Billing rule | a rule of an eu group naming the AE company, or moved to an ae group | — | refused; the form offers only its region's companies |
| Clone | an eu product cloned with region ae chosen, currency AED | the clone belongs to ae and reaches dev-ae only | — |
| Served body | dev asks for the configuration | eu's groups, companies, products, subscriptions and rules; every add-on and provider cost; no ae record; valid for a portal on mytp-core 0.24.0 | — |
| No rules left | a region whose every billing rule belongs to other regions | its served body carries an empty rules list while any rule exists anywhere: nothing is offered there | — |
| Portal skip | a body that still holds a record of another region | the portal skips it (and the subscriptions and rules of a skipped group) with a warning giving the counts | — |
| Other region's doctor | on dev, a doctor whose country ae serves | no group, no subscription, no offers | — |
| Banned country | a doctor of a banned country | no offers | — |
| Old admin body | a body whose records carry no region | the portal treats them as its own, as today | — |
| Existing records | the migration on admindev | Iberia, Rest of EU and Rest of world to eu; UAE to ae; a new active catch-all Rest of Middle East for ae; ES and IE to eu, AE to ae; AED products to ae, EUR and USD to eu; a subscription's link to a product of another region removed and logged | — |

## Decisions

- Decision (user, 2026-11-13): the region is written on country groups, billing companies and products, chosen at creation and fixed. A subscription and a billing rule take their group's region, link only products of that region and name only a company of that region.
- Decision (user, 2026-11-13): add-ons and provider costs are shared: every portal receives all of them.
- Decision (user, 2026-11-13): Rest of world is billed from eu only: it is eu's group. The ae region has two groups: UAE (billed in AED) and Rest of Middle East (billed in USD). No copies of eu's subscriptions or rules.
- Decision (user, 2026-11-13, approved at the checkpoint): Rest of Middle East is ae's catch-all (every country ae serves other than the UAE), created empty by the migration; its USD rules and subscriptions are typed by staff or seeded by entry 13.
- Decision (user, 2026-11-13): group names are unique within a region.
- Decision (user, 2026-11-13): a group lists only countries its region serves per the country map; moving a country to another region is refused while an active group lists it.
- Decision (user, 2026-11-13): a doctor whose country another region serves is offered nothing: a region's catch-all covers only the countries that region serves.
- Decision (user, 2026-11-13): existing records are mapped as the matrix's last row says.
- Decision (user, 2026-11-13): the clone form asks the clone's region, defaulting to the source's.
- Decision (user, 2026-11-13): the Commercial Regional Manager stays read-only on every region's catalogue.
- Decision (user, 2026-11-13): one plan; mytp-core 0.25.0, then admin, then portal. Merge and staging delegated.

</frozen-after-approval>

## Code Map

- Repositories: this plan is in the portal (`mytreatplan.com`, baseline d04c49ec14ed7fefa130c1b9cafa5e118da17e19); mytp-core is `../mytp-core` (baseline 4cc569cd84fa1bdb038a6dedf8e8102832d6b743, v0.24.0; this story makes 0.25.0) and the admin site `../mytp-admin` (baseline e77d45b084286005f9d403e7a27bf1c7caa14a3a). The branch `story/5.16-per-region-catalogue` is checked out in all three: work there, commit nothing, push nothing, tag nothing. Do not build or copy a wheel: point both `requirements.txt` at `./vendor/mytp_core-0.25.0-py3-none-any.whl`, update the version tests, and run the two site suites with `PYTHONPATH=../mytp-core` until the release (the admin tests that need the wheel file then fail: expected). Suites: each README; portal venv `../.venv`, admin venv `/private/tmp/claude-501/admin-venv2`. The portal has 3 known PostgreSQL-only failures on SQLite. A test marked `django_db(transaction=True)` (PostgreSQL only, skipped locally) must create its own regions, country rows and groups (`get_or_create`): the migration-seeded rows are gone there. Portal tests write snapshot-fed tables only through a pull or `integration/tests/snapshot_rows.py`. Search by name when a line number is off.
- mytp-core: `contracts/catalogue.py` `CatalogueCountryGroup` :310, `CatalogueProduct` :599, `CatalogueBillingCompany` :1014, `CatalogueSection._consistent` :1152-1219 (one active catch-all, a country in one active group: global today); `contracts/vouchers.py` `for_region` :153-166 (the filter to extend); `contracts/__init__.py:106,167`; schema `schemas/config_snapshot.json`, drift test `tests/test_contracts.py:314`; pins `pyproject.toml:7`, `mytp_core/__init__.py:44`, `tests/test_hub.py:29-30`.
- Admin: `catalogue/models.py` `CountryGroup` :171-212 (`name` unique :181), `Product`, `Subscription` (`country_group` :466, `products` :469); `catalogue/service.py` `group_rule_errors` :126-142, `_lock_groups` :149-159, `GROUP_FIELDS` :88, `SUBSCRIPTION_FIELDS` :85; `catalogue/views.py` `CountryGroupForm` :701, `SubscriptionForm` :625; `billing/models.py` `BillingCompany`, `BillingRule` :163-169, `billing/service.py` `RULE_FIELDS` :48, `billing/views.py` `RuleForm` :285; `catalogue/snapshot.py:39-151`, `billing/snapshot.py`; `interface/config.py:95-102` `snapshot_body()` validates the whole stored body against the contract; `interface/views.py` `for_region`; `country_map/service.py:21-34` `set_state`; `vouchers/` (the model of a region field: `models.py:51`, `views.py:100-101`, `service.py:40`); `staff/migrations/0002_seed_regions.py` (eu, ae); seeded groups `catalogue/migrations/0002`. Tests that change: `catalogue/tests/test_catalogue.py:98,130,955,1013,1111`, helpers `group(name)` (:30, `billing/tests/test_billing.py:60`), `billing/tests/test_billing.py:247,399`, `interface/tests/test_config.py:253-266`.
- Portal: `catalogue/applier.py:135-207` (replace), `apply_vouchers` :231-249 (the skip to copy); `catalogue/resolver.py` `country_group` :173-182, `has_billing_rules` :208; `regions/countries.py:95-112` `lookup_country` (`own_region`); `regions/tests/test_guard.py` (no region names in code); `catalogue/tests/test_billing.py` (feeds eu and ae rows to an eu portal: :191, :247, :468-494), `test_snapshot_rules.py:117`, `test_catalogue.py:100-110`.

## Tasks & Acceptance

**Execution:**
- [x] mytp-core 0.25.0 (`contracts/catalogue.py`, a per-region filter next to `contracts/vouchers.py` `for_region`, `contracts/__init__.py`, `schemas/config_snapshot.json`, version in `pyproject.toml` and `__init__.py`, README, tests) -- an optional `region` on country group, billing company and product entries; the group rules (a country in one active group, one active catch-all) hold per region, a record without a region counting as one shared region; one function that returns a body for a region: its groups, the subscriptions (both lists) and rules of those groups, its companies, its products, every add-on and provider cost, its vouchers, an empty rules list kept when rules were filtered away, records without a region kept, unknown keys copied; the result of the filter always passes the 0.24.0 rules.
- [x] Admin (`catalogue`, `billing`: models, migrations, services, forms, lists, details, snapshot builders, README, tests) -- `region` (to `staff.Region`, PROTECT, fixed after creation) on `CountryGroup`, `BillingCompany` and `Product`; group names unique per region; the group rules per region; a group's countries checked against the country map; subscriptions and rules restricted to their group's region (products, company, a move to another group); the clone form's region; lists show and filter by region; the data migration of the matrix's last row.
- [x] Admin (`country_map/service.py`, tests) -- a move of a country that an active group lists refused, under the group lock.
- [x] Admin (`interface/config.py`, `interface/views.py`, tests) -- the stored body validated with the 0.25.0 contract and each region's served body checked too; `for_region` calls the core filter.
- [x] Portal (`catalogue/applier.py`, `catalogue/resolver.py`, `price_catalogue`, `deploy/README.md`, tests) -- the applier skips a group, company or product of another region and what hangs from a skipped group, with a warning; `country_group` and what builds on it answer nothing for a country this region does not serve (through `lookup_country`, no region names in code); tests that fed both regions' rows to one portal split per region.
- [x] Both sites -- vendor the 0.25.0 wheel name and the pinned versions (core `tests/test_hub.py`, both `test_vendored_core.py`, admin `layout/tests/test_deploy.py`); do not build or copy a wheel.
- [x] Tests for every row of the matrix; a PostgreSQL-only test creates its own regions, country rows and groups.

**Acceptance Criteria:**
- Given the migrated staging data, when dev and dev-ae pull, then dev holds no UAE group, AE company or AED product, and dev-ae holds none of eu's.
- Given Rest of world (eu) and Rest of Middle East (ae), when a doctor of the US asks on dev and a doctor of Saudi Arabia asks on dev-ae, then each falls into their own region's catch-all; a doctor of Saudi Arabia on dev is offered nothing.
- Given a portal still on mytp-core 0.24.0, when it pulls a filtered body, then it applies it.
- Given an edit that would move a group, company, product, subscription or rule to another region, then it is refused.

## Implementation Notes

- Core: `region` is optional on `CatalogueCountryGroup`, `CatalogueProduct` and `CatalogueBillingCompany`, left out of a dump when absent. The group rules run once per region; a group without a region is counted in every region's check. So a valid stored body always gives served bodies that keep the 0.24.0 rules. No `min_reader` bump: the whole body is never served.
- Core: the filter is `contracts/regions.py` `for_region`, exported as `snapshot_for_region`, with `catalogue_for_region` for the section alone. It calls the vouchers filter. It also leaves out a rule whose company is not kept, and drops the ids of products not kept from a subscription's `products`. Both keep the served body free of dangling references. A list the body has stays (empty if nothing is left); an absent list stays absent. Region None keeps only records without a region.
- Core, a semantic change: `CatalogueSection.billing_offered` now reads a present but empty `billing_rules` list as "rules exist" (nothing offered). Before 0.25.0 an empty list meant "offer everything". The admin site never sent an empty list before (`billing/snapshot.py` leaves it out). Without this change the plan's "empty list" would not stop "offer everything" on either the contract or the portal.
- Portal: the empty list cannot be stored as rule rows, so a new snapshot-fed table holds it: `catalogue.CatalogueState` (migration `catalogue.0008`, `billing_rules_listed`). It is guarded and added to `SNAPSHOT_FED`. `has_billing_rules()` is true when there is a rule row or the flag is set.
- Portal applier: a record whose `region` is another region is skipped, with the subscriptions (both lists) and rules of a skipped group and any rule naming a skipped company. A skipped product's id is removed from linked lists. There is one warning with the counts only. `price_catalogue` adds a line saying a country is not served by this region.
- Portal resolver: `country_group` returns None unless `lookup_country(country).own_region`, so no region names appear in code. Everything built on it (subscription, offers, billing_rule) answers nothing for another region's or a banned country.
- Admin: the region is an FK to `staff.Region` (PROTECT) on `CountryGroup`, `Product` and `BillingCompany`. Group names are unique per region (constraint `catalogue_country_group_name_unique_per_region`). The service checks names first so the error stays on `name`. A region given to an update is ignored when it is the same and refused (`region`) when it differs.
- Admin: the group-countries check (country map) is in `CountryGroup.clean()`. The country map move check takes `catalogue.service._lock_groups()` first. Only active groups block a move. A reactivated group is checked again.
- Admin forms: on create, the subscription and rule forms cannot know the region before a group is chosen. Their lists then label each group, product and company with its region, and `clean` refuses a mix. On edit they offer only the record's region. The Commercial Regional Manager keeps view access to every region (lists use `region_filter_field`, not `region_field`).
- Admin migrations: `catalogue 0007` and `billing 0002` add nullable columns, `catalogue 0008` maps the data, `catalogue 0009` and `billing 0003` make the columns required. Groups are ae when all their countries are ae in the country map, else eu. Companies follow their country. AED products are ae. Rest of Middle East gets a fixed UUID so the reverse can remove it. Links are removed through the many-to-many table and logged on `catalogue.migrations`. Cross-region billing rules are only logged. 0008 seeds eu and ae again (as staff 0002 does) when a test flush removed them.
- Admin: the migration builds no snapshot version, so `nudge_config --rebuild` was added: it stores the next version when a section changed, then nudges. `snapshot_body` validates the stored body, then each Region's `served_body(...)` with `region` stripped (`without_regions`): the 0.24.0 rules.
- Tests that fed both regions' rows to one portal (`catalogue/tests/test_billing.py`, `test_snapshot_rules.py`) now ask the AE questions under an `ae_portal` settings fixture. The bodies stay region-less (an old admin body).
- Review fixes (2026-11-13). The release order is now portals before the admin site (Design Notes), in the core README, `contracts/catalogue.py`, the admin README and the portal `deploy/README.md`. The contract now says a 0.25.0 reader must itself skip another region's records; that, not the admin site's behaviour, is why the group rules could be relaxed without a `min_reader` bump.
- Admin review: a rule whose company is of another region than its group no longer covers its pair in `missing_pairs` (`company__region=F('country_group__region')`). The rules list shows a column "Sent to the portals" and the rule's page a row with the same label ("No: company of another region ..."). Each missing pair names the group's region.
- Migration 0008 gives the row with Rest of Middle East's fixed id the ae region before mapping, and does not create it again when that id exists. This makes going back and forth safe when something refers to it.
- `nudge_config --rebuild`: a version it builds is nudged only by the task the rebuild queues; the command does not nudge as well. A body the contract refuses becomes a CommandError listing field paths and messages, never values.

## Plan Change Log

- 2026-11-13 (build): "the filter keeps an empty rules list" needed a change of meaning: before 0.25.0 an empty list meant "offer everything". Since 0.25.0 a present, empty list means "rules exist, none for this region", and the portal records it in a new table (`catalogue.CatalogueState`).
- 2026-11-13 (review): the order is mytp-core, then the portals, then the admin site (the task and the decision said admin before portal). The admin site also gained `nudge_config --rebuild`, run once after its deploy.

## Review Triage Log

Quick lens, one reviewer per repository (2026-11-13). 15 findings: 4 medium, 11 low; 14 patched, 1 rejected.

| # | Repository | Finding | Verdict | Evidence | Route |
|---|-----------|---------|---------|----------|-------|
| 1 | core | with the order core, admin, portals, a portal on 0.24.0 served an empty rules list offers everything | medium | 0.24.0 `billing_offered` reads `[]` as no rules; a 0.25.0 portal reads a 0.24.0 admin body correctly | patch: the order is core, portals, admin, written in the three READMEs and the contract |
| 2 | core | the reason given for no `min_reader` bump is the admin site's behaviour | medium | a 0.25.0 reader accepts an unfiltered body with two regional catch-alls | patch: the contract says a reader skips other regions' records itself (the portal applier does) |
| 3 | core | the `billing_offers` docstring defines `rules_exist` the old way | low | `contracts/catalogue.py` | patch |
| 4 | core | the reader rules do not mention regions | low | `contracts/config.py`, `catalogue.py` | patch |
| 5 | core | the filter's docstring says "a copy"; rows are shared | low | shallow copy | patch: the docstring says so |
| 6 | core | four filter cases without a test; one test name claims 0.24.0 code | low | the reviewer's fuzz (66,104 served bodies, all valid, all accepted by the 0.24.0 code) found no fault | patch: four tests, the rename |
| 7 | admin | a rule whose company is of another region is no longer served but still counts as covering its pair and shows as active | medium | the reviewer's staging-like run: Rest of world billed by AE left out of eu's body, absent from the missing pairs | patch: not counted; the list and the rule's page say "not sent to the portals" |
| 8 | admin | migration 0008 fails going back and forward when Rest of Middle East was kept | low | the fixed id exists without a region | patch: the row gets ae first, no second create; test |
| 9 | admin | `nudge_config --rebuild` nudges twice and fails with a traceback on a refused body | low | the rebuild queues the nudge itself | patch: one nudge, a CommandError; tests |
| 10 | admin | two test names or comments say more than the tests do; no test of a body without any rule | low | `catalogue/tests/test_regions.py` | patch |
| 11 | admin | a missing pair is labelled by the group's name only | low | names repeat across regions now | patch: the region in the label |
| 12 | portal | the README's staging check for SA on dev-ae cannot print the group | medium | Rest of Middle East starts without a subscription: "no offers for SA basic" | patch: the check asks for a billing rule, which names the group; test |
| 13 | portal | until its first pull of the filtered body a portal still holds the other region's rows | low | replace-at-apply | patch: the deploy steps pull straight away |
| 14 | portal | no test of a body mixing records with and without a region | low | the logic handles it | patch: test |
| 15 | portal | after a rollback and a re-deploy `CatalogueState` is stale until the next pull | low | very unlikely; corrects itself at the pull | rejected as code: one sentence in the README |

## Design Notes

- The admin site validates the whole stored body with the contract before storing it. Two active Rest of world groups break today's global rule, so mytp-core 0.25.0 makes the group rules per region and the admin site validates each region's served body.
- The filter keeps an empty `billing_rules` list when a region had rules filtered away, so the portal does not fall back to "offer everything".
- Deploy order (review, 2026-11-13): mytp-core 0.25.0, then the portals, then the admin site (its release carries the filter and the second catch-all together), then `nudge_config --rebuild` on admindev, then `pull_config` on each portal. A 0.25.0 portal reads a 0.24.0 admin body correctly (no regions: the old global check; the old admin site never sends an empty rules list). The other way round, a portal on 0.24.0 reads the empty rules list sent to a region without rules of its own as "no rules" and offers everything until it is deployed.

## Verification

**Manual checks (staging):**
- The UAE group, the AE company and the AED products are not on dev; eu's are not on dev-ae; each region has its own Rest of world; an ae-country doctor on dev per question 7.
