---
type: context-update
date: 2026-11-26
initiative: initiative-mytreatplan-portal-admin
covers: [7.11, 8.5, 8.13, 8.6, 8.7, 8.8, 8.9, 8.10, 8.11, 8.12]
audience: AI agents (BMad / Claude Code) working on mytreatplan.com, mytp-admin or mytp-core
---

# Context update, 2026-11-26: the Redsys saved-card chain is handed to the colleague

Read this before planning 7.11 or any epic-subscriptions entry from 8.5 on. It also lists who works on what,
so two agents do not change the same code at the same time. Read `context-update-2026-11-26-work-orders.md`
too: it describes the hooks and work orders this chain builds on.

## Who does what (product owner, 2026-11-26)

| Work | Owner | Notes |
| --- | --- | --- |
| 9.9 Pending-payment work orders, 9.19 (done) | colleague | as agreed in the work-orders file |
| 7.11 → 8.5 → 8.13 → 8.6 → 8.7 → 8.8 → 8.9 → 8.10 → 8.11 → 8.12 | colleague | this file |
| 9.20 Order emails | coordinating agent | plan `epic-ordering/story-order-emails-plan.md` |
| 9.11 Refactor sweep | coordinating agent | starts once 9.9 and 9.19 are done; limited to finished entries; the rest goes to epic 10's sweep |
| Plan epic 10 (Doctors follow each case to approval) | coordinating agent | after 9.11 |

## The chain and its order

The chain is strictly sequential. Use `tickets.py find <ref>` for each entry's description and prerequisites.

1. **7.11 Saved cards and repeat charges on Redsys** (`epic-payments/tickets.toml` entry 11, hitl, high risk).
   It owns the gateway-neutral "charge saved card for order and period" service (idempotent per order and
   period, AD-8), its Redsys implementation (COF/MIT), a declined state the caller handles, and the
   registry's save-card flag (on for Redsys).
2. **8.5 Buy Advanced or Premium.** It waits on 8.4, 7.6 (done) and 7.11. It offers monthly payment only where
   the gateway can save cards. Revolut countries get annual only until 7.16.
3. **8.13 Included-case allowance.** It waits on 8.5 and 5.17 (done). It is the per-doctor, source-tagged
   case-credit ledger. Keep the ICE credit off included-case lines in pricing (`patients/ice.py`, see 9.7).
4. **8.6 → 8.12:** annual renewals, monthly charges and failed payments, mid-term upgrade, cancellation and
   expiry, subscriptions on the admin site, production checks, refactor sweep. Each waits on the one before.

7.16 (Revolut saved cards) is NOT part of this hand-over. It waits on Revolut and comes later.

## Santander and Redsys: what is known

The detail is in `epic-payments/epic-payments.md`, `## Notes`, and is kept up to date there.

- Recurring charges on the Redsys terminal need Santander's paid add-on **"Servicios Venta +"**, priced per
  month by the number of stored cards: 0–300 cards cost €8, 301–600 cost €11, 601–1000 cost €15. The account
  holder is MyTPDSO Spain S.L.
- Venta + covers merchant-initiated charges without the doctor present (COF/MIT), in the test environment too.
- Pre-authorisation (authorise, then capture or cancel) is possible.
- **The contract is NOT signed (product owner, 2026-11-26).** Santander first wants screenshots showing three
  things: a product with a price, the legal notice with the company name and CIF, and the contracting
  conditions. The team is not ready to take them in the form Santander needs, and will come back to this soon.
  Test credentials for our own terminal come only after signing.
- Consequences for 7.11:
  - Plan it and build it against Redsys's public test terminal, as 7.2 did.
  - Its staging check (a card saved on our test terminal and charged again by a task) cannot run until the
    contract is signed and Santander sends the test credentials.
  - Record that check as owed in the plan's `## Staging`. Leave the plan `built`, not `done`, until it runs.
  - Do not ask the product owner for the credentials before they say the contract is signed.
- The 8.x entries that charge saved cards (8.5 monthly, 8.7, 8.8) inherit the same limit for their checks on
  real Redsys. The registry flag lets 8.5 be built and tested with the fake gateway.

## Avoid collisions

- 9.20 adds two receivers in `cart/apps.py`, on `transfer_approved` and `paid`, and new files `cart/emails.py`
  and `cart/tasks.py`. If 7.11 or 8.x needs to change the hooks (`payments/hooks.py`), the receiver order or
  `payments/service.py` `_move`/`_start_payment`, start from master after 9.20 merges. If you cannot wait,
  write the change into a context-update file first.
- Renewal orders are SUBSCRIPTION orders. 9.20 sends no email for them; any renewal or failed-payment email
  belongs to epic-subscriptions (8.6/8.7).
- Branches are `feat/<ref>-<slug>`. Merge order is core → admin → portal. mytp-core releases are tagged
  automatically (see the work-orders file).
