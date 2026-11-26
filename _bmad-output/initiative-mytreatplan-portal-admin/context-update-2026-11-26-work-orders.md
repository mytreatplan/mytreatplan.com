---
type: context-update
date: 2026-11-26
initiative: initiative-mytreatplan-portal-admin
covers: [9.8, 9.19, 9.9]
supersedes_status_of: context-update-2026-11-25-ordering-payments.md
audience: AI agents (BMad / Claude Code) working on mytreatplan.com, mytp-admin or mytp-core
---

# Context update, 2026-11-26: work orders per service, and what comes next

Read this before planning or changing anything in ordering, payments, patients and work orders, the admin
site's work-order data, or mytp-core stages and contracts. It replaces the status table of
`context-update-2026-11-25-ordering-payments.md`. That file still holds the details of 9.17, 9.7 and 9.18;
read it too. The plans are the source of truth: decisions, code maps, review logs and staging results.

## Status

| Ref | Status | Plan (in `_bmad-output/initiative-mytreatplan-portal-admin/epic-ordering/`) | Where |
| --- | --- | --- | --- |
| 9.17, 9.7, 9.18 | done | see the 2026-11-25 file | master |
| 9.8 | built, merged, staging check in progress | `story-work-orders-per-service-and-order-emails-plan.md` | mytp-core deae060 (PR 2, release 0.35.0), mytp-admin 34e5ebc (PR 51), portal 6d86bcc (PR 146) |
| 9.19 | plan drafted, decisions recorded, waiting for approval | `story-pending-payment-documents-in-the-doctor-s-invoices-list-plan.md` | branch `feat/9-19-pending-payment-documents` |
| 9.9 | plan drafted, decisions recorded, waiting for approval | `story-pending-payment-work-orders-plan.md` | branch `feat/9-9-pending-payment-work-orders` |
| order emails | split from 9.8, needs a ticket | `deferred-work.md` (entry with the user's answers 2A/3A/4A) | - |

Order agreed with the product owner: 9.19, then 9.9. Do not start building either until its plan is approved
(status `ready-for-dev`).

## Behaviour that now exists after 9.8 (do not re-implement; build on it)

### mytp-core 0.35.0
- New final stage `closed` (`mytp_core/stages.py`). Every live stage may move to `closed`, and nothing leaves it.
  It is not `cancelled` and not a finished treatment. Used when a paid planning supersedes a live ICE.
- New contract `contracts/work_orders.py`: events `work_order.created` and `work_order.stage_changed`. The payload
  is `work_order_id`, `stage`, `product_category` (`''` if unknown) and `at`, with no patient name or code.
  Writer models are closed. Reader models take any lowercase stage code.
- Both apps vendor the same 0.35.0 wheel. The tag `v0.35.0` is created by the new auto-tag workflow (below) or
  by a person on GitHub.

### Portal (`patients/service.py`, `patients/apps.py`)
- **One work order per order line that names a patient**, any service (no longer ICE only). Model
  `WorkOrder.order_id` is new (migration `patients/0004_work_order_order_and_closed.py`).
- **Hooks** (all idempotent per line):
  - `transfer_approved` -> `work_orders_hold`: work orders in `pending_payment` with `order_id` set.
  - `paid` -> `work_orders`: the line's pending row moves to `pending_assignment`, or one is created there. For a
    line that is not an ICE, the patient's live ICE work order is `closed` first.
  - `cancelled` / `expired` -> `work_orders_cancel`: the order's `pending_payment` rows are cancelled.
- **Blocking rule:** `blocking_work_order(patient, exclude_order=None)` is THE check for "may this patient get a
  new service". A live work order blocks, and so does a `pending_payment` row WITH an `order_id` (a
  bank-transfer hold) from another order. A `pending_payment` row WITHOUT an `order_id` (a 9.9 manual one) never
  blocks. A live ICE still lets a planning through (`is_live_ice`). Cart (`add_tps`, `add_virtuaortho`) and
  payments (`_check_patients`, `_patients_still_free`) use it. `live_is_ice` was removed. Do not add new checks
  on `LIVE_STAGES` alone.
- **Events:** every work-order create and stage change (including `move_work_order`) records an outbox event in
  the same transaction.
- The account archive copies `order_id`.

### mytp-admin
- New app `work_orders`: model `WorkloadEntry` (uid, region, stage, product_category, version, at). The appliers
  for both events are registered in `interface/events.py` `appliers()`. A replay or older event changes nothing.
  There are no views yet. The admin site never sets a stage (AD-21).

## Decisions to respect (product owner)
- 2026-11-25: when a planning is paid for a patient with a live ICE work order, the ICE is `closed`, never
  `cancelled`, and the planning gets its own work order.
- 2026-11-26, 9.19: the pending-payment document reads the seller's details from the live billing company (no
  snapshot, no migration). Title "Pending payment document" with "This is not an invoice". It sits in the same
  invoices list by date, with a "Pending payment" badge instead of a number.
- 2026-11-26, 9.9:
  - Portal service seam only, plus a test fixture; epic-clinical-ops adds the remote write and the admin screen
    later.
  - The manual work order carries the product. The doctor picks the options (add-ons, quantity, shipping
    address) in the case wizard with the patient and product fixed.
  - While its order waits for a bank transfer it blocks like a 9.8 hold. On cancel or expiry it goes back to
    a plain manual work order (`order_id` cleared), never cancelled.
  - The doctor cannot decline it.
- Order emails (deferred story): a bank-transfer order gets a confirmation at approval, with the bank details,
  and every service order gets a receipt when paid, with the invoice PDF attached. Card and free orders get the
  receipt only.

## mytp-core releases are tagged automatically (new, 2026-11-26)
- Cloud agent sessions CANNOT create tags: the session's git proxy refuses tag pushes (403), and the GitHub tools
  have no create-tag or create-release action. Do not try; do not ask the person to push tags either.
- mytp-core's `Tag release` workflow (`.github/workflows/tag-release.yml`, mytp-core PR 3) runs when CI passes on
  master. It reads `version` from `pyproject.toml` and creates the annotated tag `v<version>` on that commit, if
  it does not exist yet. It never moves a tag.
- So a mytp-core release is:
  1. bump `version` in `pyproject.toml` (and `mytp_core/__init__.py`, README) in the story's core PR;
  2. merge it; the tag appears a few minutes later, once CI is green;
  3. vendor the wheel into both apps with `scripts/vendor-mytp-core.sh vX.Y.Z`.
  For vendoring before the merge, the agent may create the tag in its LOCAL clone only (never pushed), as
  9.8 did.
- Until PR 3 is merged, a person creates the tag on GitHub (Releases > new release).

## Conventions confirmed in practice
- One branch per story per repo from the latest master (`feat/<ref>-<slug>`), one PR per repo.
- mytp-core first. Merge order for a cross-repo story: core, then the admin site (it receives events), then the
  portal.
- Staging deploys from CI on every push to master (portal: dev, dev-ae; admin: admindev), only if the commit is
  still master's tip. Do not push to master while a deploy runs.
- Dev checks are read-only Django-shell scripts run over SSH by a person. Everything runs inside one rolled-back
  transaction, and no patient names are printed. Dry-run them against the test fixtures first.
- Known flaky patterns:
  - asserting a short digit string is absent from text containing a random UUID;
  - comparing a rendered date with a UTC `strftime`. The page shows the region's local date, so use
    `timezone.localtime`. Fixed in `accounts/tests/test_personal_data.py`.

## Open items (deferred-work.md)
- The account purge deletes work orders without a `work_order.*` event, so admin workload rows of a purged account
  go stale. Needs a decision.
- No backfill at the 9.8 deploy: older work orders reach the admin site only at their next stage change. A 9.17
  transfer order approved before 9.8 has no hold. Staging data only; settle before production.
- From before: a transfer arriving after its order was cancelled; whether cancel should refuse while a transfer is
  pending. Built but not marked done in the tree: 3.5, 1.13, 3.10, 3.11, 7.4 (needs Revolut sandbox
  credentials), 9.12.
