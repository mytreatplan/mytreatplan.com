---
type: context-update
date: 2026-11-26
initiative: initiative-mytreatplan-portal-admin
covers: [9.11, 7.16, 8.5, CI]
audience: AI agents (BMad / Claude Code) working on mytreatplan.com, mytp-admin or mytp-core
---

# Context update, 2026-11-26 (evening): renamed helpers, CI triggers, Revolut saved cards

Read this before planning or building anything in `payments`, `cart` or `patients`, and before
pushing or merging. It follows `context-update-2026-11-26-redsys-chain.md`.

## 1. Renamed helpers (9.11 refactor sweep, PR #150)

9.11 is a cleanup with no behaviour change. Once PR #150 is on master, these private names no longer
exist; code that imported them breaks at runtime, not at merge time:

| Old name | New name |
| --- | --- |
| `payments.service._lock` | `payments.service.lock_order` (PR #150 already updates `payments/repeat.py` from 7.11) |
| `payments.views._refusal_message` | `payments.views.refusal_message` |
| `patients.views._create` | `patients.views.create_patient_from_form` |
| `cart.views._doctor_only`, `_own_patient`, `_fiscal_block`, `_refusal_texts` | `cart.common.doctor_only`, `own_patient`, `fiscal_block`, `refusal_texts` |

Also new:
- `patients.service.may_take_line(blocking, is_ice)`: the one ICE rule ("nothing blocks, or a live
  ICE blocks and the line is not an ICE"). `payments.service._patient_free` and the cart use it;
  new code that decides whether a patient may take a line should too.
- `cart/service.py` adds to the cart through shared pre-checks (`_require_doctor`,
  `_entity_patient`, `_require_free`). The order of refusals is pinned by
  `cart/tests/test_refusal_order.py`.
- One add-patient template: `patients/templates/patients/new.html` (the view passes `action`,
  `back_url`, `back_text`); `cart/templates/cart/new_patient.html` is gone.

Rule from now on: do not import a `_private` name from another module. Make it public, or ask.
`payments/repeat.py` still calls other private `service._…` helpers (`_stamp`, `_change`,
`_live_attempts`, `_patients_still_free`, `_set_payment_state`, `_check_actor`). They are left
for epic 10's sweep; do not add more.

## 2. CI and merging (PR #152)

- A pull request runs the tests once. The branch push no longer starts a second run.
- A push to master that changes only `_bmad-output/**` or Markdown starts no CI and no deploy.
  A push that mixes docs and code still runs everything.
- **The rule "don't push or merge to master while a deploy runs" is dropped.** The deploy job queues one
  deploy per site and skips a commit that is no longer the tip of master, so a merge during a deploy
  just queues the next one. Merge as soon as the PR's checks are green.
- Two PRs that pass separately can still break master together, as when one PR renames a helper and
  the other adds a caller. Before merging, update the branch with master if master has moved, and run
  the tests of the apps both touched.

## 3. 9.8 backfill not needed

The product owner decided (2026-11-26) that no backfill is needed: only staging data predates 9.8 and production starts
empty. The stale rows on dev and dev-ae (work orders created before 9.8, pending-payment transfer
orders with no pending-payment work order) are counted and removed at 9.11's staging step, after the
product owner confirms. Expect those test orders to disappear.

## 4. Revolut saved cards (7.16, later; not part of the colleague's chain)

The epic-payments notes record what the Revolut guides say: the card is saved for the merchant on the
first payment, and each period is an order plus `POST /api/orders/{id}/payments` with
`initiator: merchant`. Proposal: 7.16 uses this saved-card charge through 7.11's gateway-neutral
repeat-charge service, not Revolut's own Subscriptions, so the portal keeps the renewal schedule on
both gateways.

Revolut also requires an email before each charge (amount with tax, card, date, how to cancel), a
receipt after it (with the next date) and a failure email with the reason. These apply to renewals
on Redsys too; whoever plans 8.5's renewal emails should cover them.

Still open for 7.16: whether Revolut's hosted payment page (our integration) can save a card for the
merchant, or whether the embedded card field is needed; fees; whether our merchant account has it enabled.
