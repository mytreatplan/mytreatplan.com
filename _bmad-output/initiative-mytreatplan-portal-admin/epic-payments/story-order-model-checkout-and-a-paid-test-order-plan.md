---
title: 'Order model, checkout and a paid test order'
type: 'feature'
ticket: '1'
created: '2026-11-18'
status: 'done'
baseline_revision: 'edbff829bda7f096a06fec8ce89c5eb29cbfd2b9'
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

**Problem:** Nothing in the portal records a purchase. Subscriptions (epic 8) and ordering (epic 9) need one order model, one order-creation service, one checkout and hooks for paid, cancelled and expired, before any real gateway exists (AD-6, AD-15, AD-16, AD-20).

**Approach:** A new portal app `payments`: an `Order` with lines that snapshot the priced values from `subscriptions.service.price_order_for` (and `price_subscription` for a subscription purchase), the buyer's fiscal entity copied into encrypted fields and marked used, one company, one currency and one gateway; a service that creates and moves orders under a row lock with a version and actor; a hook registry other apps register into; a checkout (fiscal entity, payment method, redirect, return and failure pages); a fake gateway adapter for dev. Portal only: no mytp-core release, no admin change, no outbox event (entry 8 sends orders to the admin site).

## Boundaries & Constraints

**Always:**
- Amounts are `Money`; stored as `DecimalField(17, 2)` + one `currency` on the order. Each line keeps product id, code and name, quantity, add-ons, list amount, discount % and amount, ICE, voucher share, net, VAT % or text, and the provider cost (`resolver.cost(...).total` × quantity, with its `complete` flag). Company code and legal name are copied, not linked (snapshot tables are read-only).
- Totals and VAT groups are copied from `PricedOrder`; nothing in `payments` does price arithmetic.
- The chosen gateway must be one of `priced.gateways` that has a registered adapter; a 0.00 order needs no gateway and is marked paid at creation (its paid hook fires).
- Fiscal entity: only from `accounts.fiscal.usable_entities(user)`; `vies=fiscal.vies_status(entity)`; FK with `SET_NULL`; snapshot of `fiscal.FIELDS` + entity id, version and VIES status; `mark_used` in the same transaction that freezes the order (Pay, or creation of a 0.00 order); the snapshot is retaken whenever the entity changes in `checkout`.
- Every state change goes through one service function with `select_for_update`, an `expected_version`, an actor (doctor, admin, system + UUID, AD-22) and an append-only change row; a hook fires exactly once per transition.
- Checkout pages show only the doctor's own orders (404 otherwise); new prefix in `accounts.page_records.PORTAL_PREFIXES`; EN source + ES in the .po.

**Never:**
- No real gateway, webhook handling, refunds or saved cards (entries 2–4, 9); no invoice (entry 6); no bank-transfer page (entry 5); no admin pages or events (entry 8, epic-sales-finance).
- No voucher redemption or included-case use rows (epic-subscriptions 4 and 13 register hooks); the line only carries an `included_case` flag and the order its voucher id.
- No patient data on orders in this entry; no patient data in URLs, logs or task arguments (AD-23).
- The fake gateway is never available when `DEBUG` is off and the site is a production site (startup check).

## Decisions

- Decision (user, 2026-11-18): the dev test order is a hidden "test order" page, on only when `PAYMENTS_TEST_ORDERS=True` (dev, dev-ae); a doctor account picks one product it is offered and goes through the real checkout.
- Decision (user, 2026-11-18): the fake adapter is registered only when `PAYMENTS_FAKE_GATEWAY=True` (refused at startup on production sites) and is offered in addition to `priced.gateways`; its "hosted page" is a portal page with Pay and Fail.
- Decision (user, 2026-11-18): callers create the order in state `checkout` (priced with the default usable entity; `created` hook fires there, for voucher and included-case reservation). Changing the entity on the checkout page re-prices it from the stored line inputs; Pay freezes it as `pending_payment` and redirects to the adapter. States: `checkout` → `pending_payment` → `paid`; `checkout`/`pending_payment` → `cancelled` / `expired`.
- Decision (user, 2026-11-18): hooks `created`, `paid`, `cancelled`, `expired` run inside the transition's transaction (a raising hook rolls it back); receivers are idempotent, keyed by order UUID.
- Decision (user, 2026-11-18): job `expireorders` every 15 min expires `checkout` and `pending_payment` orders older than 24 h (firing `expired`); bank transfer's 14 days is entry 5's.
- Decision (user, 2026-11-18): the doctor can cancel their own unpaid order (Cancel on the checkout and failure pages; fires `cancelled`).
- Decision (user, 2026-11-18): order reference = 2 letters + 6 digits, easy to type for a bank transfer, shown as `KM-482915` (letters A–Z without I and O, random; digits random; unique per instance, retried on collision; input accepted with or without the dash, any case). Internal key stays the UUID.
- Decision (user, 2026-11-18): the service also accepts a subscription purchase priced by `price_subscription` (one line with its period), for epic 8.
- Decision (user, 2026-11-18): Sonnet implementer, Opus security reviewer; merge and staging delegated (the user still says "merge N" and OKs SSH writes on dev and dev-ae); staging uses a test doctor and removes its test orders after.
- Decision (user, 2026-11-18): kept as one story although the plan exceeds 1600 tokens.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Test order, fake pay | doctor on dev, one TPS line, ES entity, `fake` | order `pending_payment` → fake page → Pay → `paid`; paid hook once | None |
| Fake page Fail | same | order stays `pending_payment`, failure page with retry | None |
| Replayed return / double Pay | order already `paid` | no second transition, no second hook | Shown as paid |
| Catalogue price changes after the order | product net raised | order lines unchanged | None |
| Fiscal entity marked used | first order with this entity | `used_at` set | None |
| No usable fiscal entity | doctor has none or only refused countries | checkout blocked, link to `settings_fiscal_add?next=<checkout>` | No order paid |
| VIES valid DE entity | Irish company rule with reverse-charge text | line VAT 0 with the text | None |
| 0.00 order | voucher covers all | created `paid`, no gateway, paid hook once | None |
| Gateway not offered | POST a gateway outside `priced.gateways` or without adapter | — | Refused, form error |
| No payment method | AE portal, total above 0 | checkout says no payment method yet | No order started |
| Pricing refused | `OrderRefused` (two companies, voucher currency, …) | — | Named message, no order |
| Another doctor's order id | GET checkout/return of foreign UUID | — | 404 |
| Stale version | concurrent transition | — | `StaleVersion`, no hook |
| Cancel / expire | `checkout` or `pending_payment` order | `cancelled` / `expired`, hook once | Paid order refused |
| Entity changed at checkout | IE entity → DE VIES-valid entity | order re-priced (VAT 0 + text); version up | Only in `checkout` |
| Expiry job | order 25 h in `pending_payment`; another 23 h | first `expired`, second untouched | Job rerun is a no-op |
| Hook raises | `paid` receiver raises | order stays `pending_payment` | Error logged, no partial state |
| Subscription purchase | Advanced, upfront | one line with period, `subscription` rule's company and VAT | None |
| Flags off | `PAYMENTS_TEST_ORDERS` / `PAYMENTS_FAKE_GATEWAY` false | test page 404; `fake` not offered | — |
| Reference collision | random draw already used | redrawn; reference unique | — |

</frozen-after-approval>

## Code Map

All paths in the portal repo; mytp-core 0.28.0 vendored (no change).

- `subscriptions/service.py:237` `price_order_for(user, lines, *, vies, voucher=None, day=None)` — the only pricing entry for service orders; `Actor` (:118), `_check_actor` (:138), `_write` (:475, lock + `expected_version` + change row), `StaleVersion`, `RecordGone` — copy this pattern.
- `catalogue/pricing.py` — `OrderLine` (:127, input), `PricedLine` (:137), `VatGroup` (:156), `PricedOrder` (:165: `company`, `currency`, `lines`, `vat_groups`, totals, `gateways`, `needs_payment`, `no_payment_method`), `price_subscription` (:265), `OrderRefused.reason` + `REFUSALS`. Gateways are the intersection of rule gateways and `settings.REGION_PROFILE.gateways`; it does not refuse mixed gateways.
- `catalogue/resolver.py:440` `cost(product, day=None)` → `ProductCost.total`, `.complete` (per unit).
- `catalogue/models.py` — `BillingCompany` (code, legal_name), money field convention (`Product.net_amount` + `currency` ~:154).
- `accounts/fiscal.py` — `usable_entities` (:495), `default_entity` (:508), `is_usable` (:488), `mark_used` (:459, idempotent, nested atomic ok), `vies_status` (:325), `FIELDS`; `accounts/models.py:322` `FiscalEntity` (encrypted fields → use the same `Encrypted*Field` types for the snapshot).
- `accounts/urls.py:49` `settings_fiscal_add`; link with `?next=` (the add view validates it with `_safe_next`).
- `regions/profiles.py:76` `gateways`; EU `('redsys','revolut','bank_transfer')`, AE `()`.
- `storage/purposes.py` — `register()` registry pattern (name regex, frozen dataclass, registered in `AppConfig.ready`): mirror for `payments/hooks.py` and `payments/gateways.py`.
- `accounts/templates/accounts/settings_base.html`, `subscriptions/templates/subscriptions/upgrade.html` — doctor layout to reuse.
- `accounts/page_records.py:40` `PORTAL_PREFIXES`; `mytp_publicsite/tests/test_settings_staging.py` `PORTAL_URL_NAMES`.
- `mytreatplan/settings.py:371` INSTALLED_APPS; `mytreatplan/urls.py:52-55` app includes; `pytest.ini` testpaths; `locale/es/LC_MESSAGES/django.po`.
- `deploy/jobs.conf` + `<app>/management/commands/` — job registration (`name | command | OnCalendar UTC`).
- Test helpers: `subscriptions/tests/conftest.py`, `helpers.py`, `integration/tests/snapshot_rows.py`; autouse `fake_vies`.

## Tasks & Acceptance

**Execution:**
- [ ] `payments/` (new app, `apps.py`, `0001`) -- models `Order` (UUID pk, `reference` unique, `user` FK SET_NULL, `state`, `kind` service/subscription, `currency`, company code + legal name, gateway, totals, VAT groups JSON, voucher id, `version`, actor type/uuid, timestamps per state, fiscal entity FK SET_NULL + encrypted snapshot), `OrderLine` (line input kept for re-pricing + priced snapshot + `included_case` flag + provider cost and `cost_complete`), append-only `OrderChange` -- the model every purchase uses.
- [ ] `payments/reference.py` -- draw `XX-NNNNNN` with retry on collision; normalise typed input -- Decision on references.
- [ ] `payments/hooks.py` -- `register(event, name, fn)` registry for created/paid/cancelled/expired, mirroring `storage/purposes.py`; `fire()` called only by the service -- other epics plug in.
- [ ] `payments/gateways.py` + `payments/fake.py` -- minimal adapter registry (`start(order, request) -> redirect URL`), fake adapter behind its flag, startup check against production -- entry 2 extends this interface.
- [ ] `payments/service.py` -- `create_order(user, lines, *, actor, voucher=None, entity=None)`, `create_subscription_order(user, subscription, period, *, actor, voucher=None, entity=None)`, `choose_entity`, `start_payment(order, gateway)`, `mark_paid`, `cancel`, `expire`, `expire_stale(now)`; lock + `expected_version` + change row + hook; 0.00 order paid at creation; `mark_used` on Pay and at a 0.00 creation -- AD-15 single write path.
- [ ] `payments/views.py`, `urls.py`, templates -- checkout (entity choice, payment-method choice, totals, no-entity link to `settings_fiscal_add?next=`, no-payment-method message, Cancel), return, failure (retry, Cancel), fake hosted page, test-order page behind its flag; owner-only 404 -- the doctor's flow.
- [ ] `payments/management/commands/expire_orders.py` + `deploy/jobs.conf` line `expireorders` every 15 min -- expiry decision.
- [ ] `mytreatplan/settings.py`, `mytreatplan/urls.py`, `accounts/page_records.py`, `mytp_publicsite/tests/test_settings_staging.py`, `pytest.ini`, `settings.env.example`, `locale/es/.../django.po`, README section "Orders and checkout" -- wiring, flags (default off), ES texts, docs.
- [ ] `payments/tests/` -- every matrix row, hook-once under concurrent `mark_paid`, snapshot unchanged after a catalogue change, encrypted snapshot at rest, flags off.

**Acceptance Criteria:**
- Given a doctor on dev with both flags on, when they open the test-order page, choose a product and an entity, pay through the fake page, then the order is `paid`, the entity's `used_at` is set and a registered paid hook ran exactly once.
- Given a paid order, when the product's net price changes in the snapshot, then the order's lines and totals are unchanged.
- Given the production settings, when the site starts with `PAYMENTS_FAKE_GATEWAY=True`, then it refuses to start.

## Implementation Notes

- New app `payments` (`payments` 0001): `Order`, `OrderLine`, append-only `OrderChange` (PROTECT from both; the queryset refuses update and delete); `reference.py`, `hooks.py`, `gateways.py`, `fake.py`, `checks.py` (E001/E002), `service.py`, views and five templates under `checkout/`, `expire_orders` + `jobs.conf` line `expireorders`; wiring in settings, urls, `PORTAL_PREFIXES`, staging URL test, pytest.ini, `settings.env.example`, ES .po, README "Orders and checkout".
- "Production site" = DEBUG off and ROBOTS_NOINDEX off (the `regions/checks.py` precedent; staging sets ROBOTS_NOINDEX=True). With a flag on there: the system check fails, `PaymentsConfig.ready()` raises `ImproperlyConfigured` (the process does not start), and the fake page and test page 404.
- Choices where the plan was silent: a 0.00 order is paid at creation only when it has an entity (else it waits in `checkout`); with the fake flag on, `fake` is offered even where the region has no gateway (dev-ae); `expected_version` is required on every move and a replay to the same state is a no-op; Pay stops to show a new total when changing the entity changed it; Pay with an entity edited since it was chosen re-copies it and asks for review (`EntityChanged`).
- Expiry: `checkout` by `created_at`, `pending_payment` by `pending_at`, both 24 h.
- The concurrent `mark_paid` test runs on PostgreSQL only (CI); skipped on SQLite.
- After the review patches: full suite (SQLite) 3673 passed, 13 skipped, 0 failed; `makemigrations --check` clean.

## Plan Change Log

## Review Triage Log

Opus security review, one pass (2026-11-18): 11 findings, 0 high, 6 medium, 5 low, 0 false; 10 patched by the implementer, 1 deferred into the 7.2 plan.

| # | Finding | Verdict | Route / action |
|---|---------|---------|----------------|
| 1 | `_checkout_post` EntityChanged handler calls `choose_entity` unguarded → 500 on StaleVersion, OrderRefused, EntityRefused | medium | patch: routed through `_failed`; test |
| 2 | Public-site guard only a system check; gunicorn never runs it (AC3) | medium | patch: `ready()` raises `ImproperlyConfigured`; fake and test page 404 on a public site; tests |
| 3 | Expiry counted from `created_at`: an order paying at 23 h 55 expires on the gateway page | medium | patch: `pending_payment` by `pending_at`; test |
| 4 | A retry may switch gateway; a late success from the first gateway (or a second success) marks paid / goes unrecorded | medium | defer: needs the per-attempt payment record; written into the 7.2 plan's Always |
| 5 | Retry from `pending_payment` requires a usable entity although the snapshot is frozen | medium | patch: usable entity only in `checkout`; test |
| 6 | Re-pricing silently drops a removed add-on or voucher | medium | patch: `OrderRefused` (ADD_ON_REFUSED / VOUCHER_NOT_USABLE); tests |
| 7 | `choose_entity` crashes (AttributeError) for an erased account | low | patch: `InvalidTransition`; test |
| 8 | Lines and change rows CASCADE; change queryset `delete()` bypasses append-only | low | patch: PROTECT + `delete()` raises; test |
| 9 | Planned concurrent `mark_paid` test missing | low | patch: two-thread test, PostgreSQL only |
| 10 | Flags-off startup tested only via `check` | low | patch: covered by 2's tests |
| 11 | Failure page POST pays an order still in `checkout`; no EntityChanged message | low | patch: acts only on `pending_payment`; `_failed` branch; test |

## Verification

**Commands:**
- `pytest` (full suite, from the repo root) -- expected: 0 failed
- `python manage.py makemigrations --check --dry-run` -- expected: no changes

## Staging

- Merged as PR #113, merge commit 593620d80a571228e408690d08b14a70b8393a2b (2026-11-19). Master CI run 44495816498 green: test plus both staging deploys (dev, dev-ae). PR CI: runs 44357647650 and 44357663319. The second first failed on the flaky maker test, a known pre-existing issue now in deferred-work; it passed on the rerun.
- Local suite 3673 passed, 13 skipped, 0 failed (SQLite); the concurrent `mark_paid` test ran on PostgreSQL in CI.
- `PAYMENTS_FAKE_GATEWAY=True` and `PAYMENTS_TEST_ORDERS=True` added to dev's and dev-ae's settings.env (both with `ROBOTS_NOINDEX=True`). Both sites re-provisioned: the `expireorders` timer is listed on each, and `/healthz` is ok.
- Checks 1–6, run server-side with the `check71.py` test client against a real doctor of each site, inside one rolled-back transaction (nothing kept; orders after: 0):
  1. On dev and dev-ae, a test order paid through the fake page:
     - the order went `checkout` → `pending_payment` → `paid`;
     - the entity's `used_at` was set;
     - there was one `paid` change row;
     - the paid hook fired once, including after a second Pay.
  2. The lines and total were unchanged after the product's net price was set to 9999.00.
  3. The failure path and cancel:
     - Fail kept the order `pending_payment`;
     - the retry then paid it once;
     - Cancel on another order gave `cancelled`, with the hook firing once.
  4. With no usable entity, the checkout showed the add link with `next` back to the checkout.
  5. On dev-ae only `fake` was offered. On dev only `fake` too, since Redsys and bank transfer have no adapter until entries 3 and 5.
  6. Ownership and the expiry job:
     - another doctor got a 404 on dev (dev-ae has a single AE doctor);
     - `expire_orders` printed `0 order(s) expired`.
- Check 7 (clean-up): nothing to remove, since everything was rolled back. The two flags stay on for dev and dev-ae.
