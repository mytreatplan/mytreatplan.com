---
type: epic
title: "Doctors choose and keep a subscription level"
parent: initiative-mytreatplan-portal-admin
covers: [P6, P8]
after: []
assignee: ""
risk: high
---

# Doctors choose and keep a subscription level

## Description

The subscription club on the portal: the Basic default assigned at signup, the upgrade screen then the welcome/onboarding page (reusing part-1's welcome route), the upgrade reminder, buying Advanced or Premium upfront or monthly, renewal, upgrade, cancellation and expiry, and the lock for manually assigned special subscriptions. It builds the voucher redemption logic (subscription vouchers first), the part of P8 that epic-ordering reuses.

## Outcome

Doctors move up a level on their own; the signal is a doctor on staging upgrading to Advanced with a voucher and seeing the Advanced products and discounts.

## Requirements

Completed at inception from the initiative's P6, P8 and the source.

## Done when

1. A new doctor gets the Basic default for their country, sees the upgrade screen then the welcome page, whether they skip or pay.
2. A doctor upgrades to Advanced (upfront and monthly), with and without a subscription voucher, and is invoiced correctly; the reminder disappears for Premium.
3. Renewal, upgrade, cancellation and expiry follow the terms settled at inception.
4. A doctor with a manually assigned special subscription cannot switch on their own.

## Boundaries

The subscription a doctor holds. Not defining subscriptions (epic-catalogue).

## References

- parent — _bmad-output/initiative-mytreatplan-portal-admin/initiative-mytreatplan-portal-admin.md, section Requirements (P6, P8)
- reqs — ../MyTP_Reqs_Def_V1_21Oct2026_v3.docx (parent folder of the repo)
- architecture — the part-2 architecture document (bmad-architecture), once written

## Notes

- Open question: monthly commitment, cancellation, upgrade proration, expiry with running cases; settled with the user at inception.
- Handoff: epic-ordering reuses the voucher redemption logic for try-me vouchers.
- Waits on epic 5 because: it needs subscriptions, vouchers and the resolver.
- Waits on epic 7 because: it needs order model, payments, invoices.
