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

Source: the initiative's P6 and P8 (subscription vouchers), the requirements document's subscription section, and the architecture spine, AD-6, AD-8, AD-15, AD-16, AD-20.

- S1 (P6): The Basic default for the doctor's country group at signup.
- S2 (P6): The doctor's subscription record and the effective-tier rule, including the manual lock.
- S3 (P6): The upgrade screen after signup, then the welcome page.
- S4 (P6): The upgrade reminder in the navigation and on the home page.
- S5 (P8): Voucher redemption: reserve, consume, release (AD-20).
- S6 (P6): Buying Advanced or Premium, upfront or monthly with a 12-month commitment.
- S7 (P6): Renewals and monthly charges.
- S8 (P6): Upgrades, cancellation, downgrade, failed payments and expiry.
- S9 (P6): The subscription in the admin client record and manual special subscriptions.

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
- architecture — architecture-portal-admin/architecture-portal-admin.md, AD-6, AD-8, AD-15, AD-16, AD-20

## Notes

- Tracer bullet: entry 1 (Basic at signup priced by the resolver); it owns the subscription service and the effective-tier rule the others call.
- Lanes: one lane (1 → … → 10), with 2 and 3 on the screens and 4 → 10 on purchase and lifecycle.
- Decision (user, 2026-10-30): monthly payment is a 12-month commitment, renewing for another 12 unless cancelled; a monthly payer who cancels keeps paying until the commitment ends.
- Decision (user, 2026-10-30): annual plans renew automatically with a reminder 30 days before; the doctor can turn it off; a payment link when the card cannot be charged.
- Decision (user, 2026-10-30): Advanced to Premium mid-term pays the difference for the remaining months, end date kept.
- Decision (user, 2026-10-30): cancelling stops the next renewal, benefits until the end of the paid term, no refund; a downgrade takes effect at renewal.
- Decision (user, 2026-10-30): a failed monthly payment gets 7 days of grace with retries on days 1, 3 and 7, then Basic; at expiry the doctor drops to Basic and cases already bought continue with what was paid.
- Decision (user, 2026-10-30): a manual special subscription applies immediately over a paid one, stops its auto-renew, and refunds are a staff decision.
- Decision (user, 2026-10-30): the upgrade screen shows net prices with '+ VAT where applicable'.
- Decision (user, 2026-10-30): a voucher may cover the whole price; the €0 order skips the gateway and gets a €0 invoice.
- Decision (2026-10-30): an annual renewal still unpaid on the end date drops the doctor to Basic that day.
- Handoff: epic-payments' order service accepts €0 orders without a gateway (7.1); epic-sales-finance shows voucher usage received here (entry 4); epic-appointments counts Premium call extras from the order-line snapshot; epic-ordering prices pending-payment orders with the effective tier when they are added to an order.
- Handoff: epic-ordering reuses the voucher redemption logic for try-me vouchers.
- Waits on epic 5 because: it needs subscriptions, vouchers and the resolver.
- Waits on epic 7 because: it needs order model, payments, invoices.
