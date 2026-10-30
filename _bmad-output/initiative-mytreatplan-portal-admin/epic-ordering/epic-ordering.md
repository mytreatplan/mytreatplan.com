---
type: epic
title: "Doctors order and pay for cases"
parent: initiative-mytreatplan-portal-admin
covers: [P8]
after: []
assignee: ""
risk: high
---

# Doctors order and pay for cases

## Description

The rest of P8: patients (select or create, internal patient code), the product pages and the "start a case" banner, the wizard for TPS and VirtuaOrtho with its options and add-ons, the cart grouped by patient, try-me vouchers, the ICE deduction, one work order per patient-service, order and payment emails, and the dashboard list of manual "pending payment" work orders that epic-clinical-ops creates.

## Outcome

Doctors order cases without help; the signal is a doctor on staging ordering a TPS and a VirtuaOrtho case for two patients in one cart and paying.

## Requirements

Completed at inception from the initiative's P8 and the source.

## Done when

1. A doctor creates a patient, runs the wizard for a TPS (maker, size, one arch) and a VirtuaOrtho case (DigitalTwin, Byte Sync, Junior on medium), and both appear in the cart under their patients.
2. VirtuaOrtho cannot be ordered without a shipping address, nor by a doctor whose subscription or country does not include it.
3. An ICE bought earlier for a patient is deducted from a later planning for the same patient; a try-me voucher is accepted once per doctor.
4. Payment creates one work order per patient-service and sends the order email.
5. A manual pending-payment work order (fixture until epic-clinical-ops) is listed on the dashboard and can be added to an order.

## Boundaries

Choosing and buying cases. Not the case after purchase (epic-case-lifecycle).

## References

- parent — _bmad-output/initiative-mytreatplan-portal-admin/initiative-mytreatplan-portal-admin.md, section Requirements (P8)
- reqs — ../MyTP_Reqs_Def_V1_21Oct2026_v3.docx (parent folder of the repo)
- architecture — the part-2 architecture document (bmad-architecture), once written

## Notes

- Handoff (2026-10-30, epic-subscriptions inception): pending-payment work orders are priced with the doctor's effective tier when they are added to an order (entry 1's rule).
- Decision (2026-10-29, epic-platform inception): this epic builds the patient-code generator (AD-5), using the prefix from the region profile.
- Handoff: epic-clinical-ops creates the manual pending-payment work orders this epic lists.
- Waits on epic 5 because: it needs resolver and products.
- Waits on epic 6 because: it needs fiscal data and shipping addresses.
- Waits on epic 7 because: it needs order model, checkout and invoices.
- Waits on epic 8 because: it needs subscription discounts and voucher redemption.
