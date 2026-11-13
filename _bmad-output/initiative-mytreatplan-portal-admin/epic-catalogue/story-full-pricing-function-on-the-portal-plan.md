---
title: 'Full pricing function on the portal'
type: 'feature'
ticket: '12'
created: '2026-11-13'
status: 'done'
baseline_revision: '130468417022d43bc50b910c3dad57c05dd24c51'
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

**Problem:** The portal's resolver lists offers and a net line price, but nothing prices an order: no VAT, no VIES decision, no ICE credit, no voucher, no check that an order has one company, one currency and a way to pay. Ordering, subscriptions and payments must not each compute these (AD-16).

**Approach:** Add one order-pricing function to the portal's `catalogue` app, and a second entry for a subscription purchase that shares its arithmetic. Given the billing country, the subscription, the VIES status, the lines, an optional ICE credit and an optional voucher, it returns each line and the order totals (net, discount, ICE, voucher, VAT rate or text, VAT amount, company, currency, gateways), or refuses the order with a named reason. Portal only: no mytp-core release, no admin change.

## Boundaries & Constraints

**Always:**
- Line order: net price (quantity, add-ons) → subscription discount → ICE credit → voucher → floor at 0 → VAT.
- VAT is computed per order on the sum of lines at each rate, rounded half up to the cent (AD-7); a 0 rate carries its text.
- One order has one billing company and one currency; an order that would span two is refused, never split.
- Amounts are `Money`; currencies are never converted.
- The function only reads the snapshot tables; it writes nothing and reserves nothing.
- The existing resolver functions and the existing `price_catalogue` output keep working unchanged.

**Never:**
- No order, cart, fiscal entity, VIES lookup, voucher redemption, use limits or gateway call (epic-ordering, epic-doctor-profile, epic-subscriptions, epic-payments).
- No staging seed (entry 13).
- No pricing arithmetic outside this function.

## Decisions

- Decision (user, 2026-11-13): the caller passes the billing country (the fiscal entity's country once it exists, the profile country until then); it decides the country group, the rules and the reverse charge.
- Decision (user, 2026-11-13): the reverse charge applies when VIES is `valid`, the billing country differs from the company's country and the rule has a reverse-charge text: rate 0 with that text. `invalid`, `pending` and `not_applicable` pay the rule's rate; a rule without the text always charges its rate.
- Decision (user, 2026-11-13): the caller names the line that takes the ICE credit; only a planning line (`tps_branded`, `tps_in_house`, `virtuaortho_one`, `virtuaortho_digitaltwin`) may take it; it must be in the order's currency; what exceeds that line's price is lost.
- Decision (user, 2026-11-13): a voucher is spread over the lines in proportion to each line's amount after discount and ICE; what exceeds the order is lost.
- Decision (user, 2026-11-13): the function checks the voucher's day, currency, doctor (when the voucher names one) and type: `try_me` on a service order only, `subscription` on a subscription purchase only.
- Decision (user, 2026-11-13): the function returns the gateways every line's rule allows and the region profile has, in the rule's order; a 0.00 order needs none; an order above 0 with no common gateway is priced and flagged as having no payment method.
- Decision (user, 2026-11-13): the VirtuaOrtho discount is left to the data; nothing forces it to 0 in code.
- Decision (user, 2026-11-13): this entry also prices a subscription purchase (monthly or upfront amount, the group's `subscription` rule, a `subscription` voucher, the same VAT rules).
- Decision (user, 2026-11-13): merge and staging for this entry are delegated.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| ES doctor, Basic, TPS branded Medium | ES, no VIES | 249.00 EUR net, Spanish company, 0 % with the exemption text, total 249.00 | None |
| ES doctor, Advanced, same product | discount 6 % | discount 14.94, net 234.06 | None |
| VirtuaOrtho line under Advanced | discount map has no VirtuaOrtho entry | discount 0.00 | None |
| IE doctor | IE, rule 23 % | VAT 23 % on the order net | None |
| DE doctor, VIES valid | Irish company, rule has a reverse-charge text | rate 0, reverse-charge text, VAT 0.00 | None |
| DE doctor, VIES invalid or pending | same rule | 23 % charged | None |
| IE doctor, VIES valid | company's own country | 23 % charged | None |
| ICE credit 80 on a TPS line of 249 | same currency | line net 169.00 | None |
| ICE credit above the line, or on a retainer line, or in another currency | — | above: line 0.00, rest lost | Other two refused: named reason |
| Voucher 50 on lines 100 + 300 (after ICE) | try-me, usable | 12.50 and 37.50 taken; shares sum to the voucher exactly | None |
| Voucher above the order | 500 on 400 | every line 0.00, total 0.00, no gateway needed | None |
| Two VAT rates in one order | lines at 23 % and 0 % | one VAT amount per rate, each on its own sum | None |
| Voucher in another currency | AED voucher, EUR order | — | Refused: voucher currency |
| Voucher not usable on the day, for another doctor, or of the wrong type | — | — | Refused: named reason |
| Lines of two companies or two currencies | — | — | Refused: named reason |
| Product not offered to this doctor | unlinked, no rule, other currency, out of validity, inactive | — | Refused: names the product |
| Country not served by this region, or no lines | AE on the eu portal | — | Refused: named reason |
| Order above 0, no common gateway | UAE rules today | priced; gateways empty; flagged no payment method | None |
| Subscription purchase, upfront, with a subscription voucher | Advanced Iberia | upfront amount less the voucher, `subscription` rule's company and VAT | None |
| Free subscription | amount 0 or none | total 0.00, no gateway needed | None |

</frozen-after-approval>

## Code Map

- `catalogue/resolver.py` -- the resolver (512 lines). Reuse `country_group`, `billing_rule_for`, `subscription_rule`, `linked`, `discount_percent`, `unit_price`, `price`, `offers_under`, `voucher_state`, `today`. `price()` returns one `Money` and checks neither rules nor validity. Docstring lines 94–96 and 117 say no VAT or voucher is applied: update. Do not change existing signatures.
- `catalogue/models.py` -- `BillingRule` (`vat_percent`, `exemption_text`, `reverse_charge_text`, `gateways` in preference order, `currency`, `available`, `exempt`, `vat_text`), `BillingCompany.country`, `Voucher` (`type`, `money`, `client`, `in_force_on`), `Subscription` (`monthly_price`, `upfront_price`, `free`, `discounts`). Snapshot-fed: read only.
- `../mytp-core/mytp_core/money.py` -- `Money` (`__mul__` rounds half up, `split`, `zero`, `CurrencyMismatch`); `contracts/catalogue.py` `line_price`, `billing_offers`, `BILLING_GATEWAYS`. Use, do not change.
- `regions/profiles.py` -- `RegionProfile.gateways` (eu: redsys, revolut, bank_transfer; ae: none). Read through the profile only (AD-13).
- `catalogue/management/commands/price_catalogue.py` -- output pinned by `catalogue/tests/test_billing.py:499–587`, `test_subscriptions.py:398`, `test_vouchers.py:202,219`, `test_snapshot_rules.py:164`, `test_regions.py:241`. Add modes; keep the existing lines.
- `catalogue/tests/test_billing.py:45–109` -- rule fixtures (Iberia ES 0 % exempt, Rest of EU IE 23 % with reverse-charge text, World IE 0 % export, UAE AE 5 % AED); `test_catalogue.py:38–77` builders and `pull`; `integration/tests/snapshot_rows.py` for direct rows. Reuse.
- No caller of the resolver exists outside `catalogue/`.
- Documented prices (requirements document): ICE 80; TPS branded 199/249/359 EUR (AED 799/1045/1499); in-house 125/170/230; VirtuaOrtho One 800/1200/1699; Advanced 6 %, Premium 12 % on TPS.

## Tasks & Acceptance

**Execution:**
- [x] `catalogue/pricing.py` (new) -- `price_order(billing_country, subscription, lines, *, vies, voucher=None, doctor=None, day=None)` and `price_subscription(billing_country, subscription, period, *, vies, voucher=None, doctor=None, day=None)`; an input line type (product, quantity, add-ons, optional ICE credit as `Money`); frozen result types for the priced line and the priced order; `OrderRefused` with a stable `reason` code and the object it names; VIES status constants -- the one place that prices an order.
- [x] `catalogue/resolver.py` -- expose the new names from the resolver (`__all__`), update the module docstring -- AD-16: callers import one resolver.
- [x] `catalogue/management/commands/price_catalogue.py` -- `--order` (with `--country`, `--tier` or `--subscription`, one or more `--product`, `--quantity`, `--add-on`, `--vies`, `--ice AMOUNT`, `--voucher`, `--doctor UUID`, `--on`) and `--buy monthly|upfront`; prints each line, the VAT per rate, totals, company, gateways or "no payment method", or the refusal reason with a non-zero exit -- the staging check runs through it.
- [x] `catalogue/tests/test_pricing.py` (new) -- every matrix row, with the documented prices on fixtures; rounding cases (voucher shares with odd cents; VAT per rate versus per line differing by a cent); the function issues no write.
- [x] `catalogue/tests/` command tests -- the two new modes, a refusal, and the existing pinned output unchanged.
- [x] `catalogue/README.md` or the app's existing doc -- the function, its inputs, the refusal reasons, the staging commands.

**Acceptance Criteria:**
- Given any priced order, when its lines are summed, then net total + VAT total = total, the voucher shares sum to the amount taken, and no line is below 0.00.
- Given an order priced twice with the same inputs and day, when compared, then the results are equal (line order decides where odd cents go).
- Given the existing suite, when it runs, then no existing test changed its expectations.
- Given a module outside `catalogue/`, when it needs a price, then `catalogue.resolver` gives it everything (no import of `catalogue.pricing` internals needed).

## Implementation Notes

- `catalogue.resolver` exposes the pricing names lazily (a module `__getattr__`, PEP 562) because `catalogue.pricing` imports the resolver; either module may be imported first.
- Extra refusal codes beyond the matrix: `ice_twice` (an ICE credit on more than one line), `quantity_refused` / `add_on_refused` (from `resolver.price`), `subscription_not_offered` (inactive subscription, no available `subscription` rule in the billing country's group, or priced in another currency than that rule's), `period_not_offered` (a priced subscription without the chosen period's amount). A free subscription still needs the group's `subscription` rule (its company); it is priced at 0.00 in the rule's currency.
- `price_subscription` bills by the billing country's group `subscription` rule (decision 1), not necessarily the subscription's own group.
- An unknown `vies` or `period` value is a `ValueError` (a caller bug), not an `OrderRefused`.
- When the order total is 0.00 the gateways are empty and `needs_payment` is False. The common gateways follow the first line's rule order.
- `price_catalogue`: `--product` now collects a list (several only with `--order`); `--quantity`, `--add-on` and `--ice` are recorded in command-line order so each belongs to the `--product` before it. `--vies` defaults to `not_applicable`. `--ice` takes "80.00" (line currency) or "80.00 AED".

## Plan Change Log

## Review Triage Log

Quick lens, one reviewer (2026-11-13). 5 findings: 1 medium, 3 low, 1 false; 4 patched, 1 rejected. The reviewer found the arithmetic, the VAT grouping, the voucher split, the reverse charge and the gateways as the plan says.

| # | Finding | Verdict | Evidence | Route |
|---|---------|---------|----------|-------|
| 1 | the refusal codes and `MONTHLY` / `UPFRONT` are not reachable through `catalogue.resolver` (acceptance criterion 4) | medium | they were missing from `pricing.__all__`; the tests imported `catalogue.pricing` for them; epic-ordering would compare bare strings | patch: in both `__all__` lists; the tests use the `resolver.` names |
| 2 | `price_catalogue --ice -80` ends in a traceback | low | `Money` accepts a sign; `_price` raises a plain `ValueError` the command does not catch | patch: the command refuses it as a `CommandError`; test |
| 3 | a free subscription whose `subscription` rule is in an unknown currency raises `ValueError`, not a refusal | low | `Money.zero(rule.currency)` ran before any currency check | patch: refused as `subscription_not_offered`; test |
| 4 | the matrix row "free subscription: amount 0 or none" is tested for "none" only | low | an amount of 0.00 is not `free` and takes the other branch | patch: test (total 0.00, nothing to pay, no gateways) |
| 5 | the docs went into the project README, not an app README | false | there is no `catalogue/README.md`; the project README is where the catalogue's documentation already is | rejected |

## Design Notes

- A priced line carries: product, quantity, list amount (after add-ons and quantity, before discount), discount percent and amount, ICE taken, voucher taken, net, VAT percent and VAT text. The order carries: company, currency, lines, VAT groups (rate, text, base, VAT), net total, VAT total, total, gateways, a "needs payment" and a "no payment method" flag, and the voucher amount taken.
- The billing country gives the country group; each line's rule is that group's rule for the product's category. "Offered" means what `offers_under(subscription, day, country)` means today; with no billing rules typed at all there is no company, so the order is refused.
- The home region needs no input of its own: a country this region does not serve has no country group and is refused.
- A voucher naming a doctor is refused when `doctor` is absent or different.
- Voucher shares: proportional, rounded down to the cent, the remaining cents given one by one to the lines in order, never past a line's amount.

## Verification

**Commands:**
- the repo's full suite command (SQLite, `../.venv`) -- expected: all passing except the 3 known PostgreSQL-only tests in `accounts/tests/test_crypto.py`
- `../.venv/bin/python manage.py makemigrations --check --dry-run` -- expected: no changes

## Staging

Merged 2026-11-13: PR #95, merge e71b47d. CI: the PR's test checks passed; master run 43455098939 passed (tests, deploy to dev and dev-ae). Local suite: 2894 passed, 8 skipped, 3 known PostgreSQL-only failures. Merge and staging delegated by the user. All checks ran through `price_catalogue` (read only) on snapshot v111.

1. `pull_config` after the deploy printed `v111 applied` on dev and dev-ae. Passed.
2. dev, ES Basic, STG57 (249.00 EUR): discount 10 % (the staging Basic's), net 224.10, Spanish company, 0 % with the exemption text, gateways redsys and bank_transfer. Passed.
3. dev, ES Basic, a TPS branded line and a VirtuaOrtho One line: 10 % on the TPS line, 0 % on the VirtuaOrtho line. Passed.
4. dev, billing country DE: Irish company; with `--vies valid` rate 0 with the reverse-charge text; without it 23 % (20.70 on 90.00, total 110.70). Passed.
5. dev, ICE credit 80 on STG57: net 144.10; on an ODB line refused (`ice_not_planning`). Passed.
6. dev, try-me voucher of 10.00 EUR on lines of 90.00 and 100.00: 4.74 and 5.26 taken; on a USD order (billing country US, STG57-USD) refused (`voucher_currency`). Passed.
7. dev, `--buy upfront` for Advanced Iberia with the subscription voucher of 120.00: 600.00 less 120.00 = 480.00, Spanish company; a try-me voucher there refused (`voucher_type`). Passed.
8. dev-ae: not run. The ae region has no subscription on staging, so no order can be priced there until entry 13's seed; "no payment method" is proven by the tests only (`test_a_uae_order_today_has_no_payment_method`).
