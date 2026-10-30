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

Source: the initiative's P8 (the rest after epic-payments and epic-subscriptions), the requirements document's purchasing workflow, and the architecture spine, AD-2, AD-5, AD-6, AD-15, AD-16, AD-20, AD-21, AD-23.

- O1 (P8): Patients (regional, field-encrypted) with the patient code, the patient list and portrait, and the name lookup for invoices.
- O2 (P8): Product pages and the start-a-case links.
- O3 (P8): The case wizard: TPS by maker or in-house, VirtuaOrtho ranges and add-ons; maker login and shipping address checks.
- O4 (P8): The cart and checkout with try-me vouchers.
- O5 (P8): The ICE deduction.
- O6 (P8): Work orders per patient-service with the case stage machine (AD-21), work-order events without patient data, and the order emails.
- O7 (P8): Manual pending-payment work orders on the doctor's home page.

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
- architecture — architecture-portal-admin/architecture-portal-admin.md, AD-2, AD-5, AD-6, AD-15, AD-16, AD-20, AD-21, AD-23

## Notes

- Tracer bullet: entry 1 (a patient, an ICE paid with the fake gateway, its work order in pending assignment); it owns the patient and work-order models, the patient-code generator and the case stage machine in mytp-core.
- Lanes: one lane (1 → … → 9).
- Decision (user, 2026-10-30): a paid ICE is deducted once, with no time limit, from the first later planning (TPS or VirtuaOrtho) for the same patient.
- Decision (user, 2026-10-30): a live ICE work order closes automatically when the planning is paid; otherwise one live work order per patient blocks new services.
- Decision (user, 2026-10-30): a named maker needs a saved maker login before ordering; the wizard warns and links to the maker-logins settings page ('not decided yet' and 'other' excepted).
- Decision (2026-10-30): the ICE deduction applies only within the same billing company and currency, uses the ICE line's snapshot net amount, and is reserved, consumed and released like a voucher.
- Decision (2026-10-30): one service per patient per order; the one-live rule is checked again at payment; a pending-payment manual work order does not block other services until paid.
- Decision (2026-10-30): this epic sends the order confirmation and payment receipt emails; epic-payments sends none of its own besides the bank-transfer reminder.
- Assumption: retainers can be ordered only after a finished treatment for that patient (tested with a fixture until epic-case-lifecycle finishes cases).
- Handoff (2026-10-30, epic-subscriptions inception): pending-payment work orders are priced with the doctor's effective tier when they are added to an order (entry 1's rule).
- Decision (2026-10-29, epic-platform inception): this epic builds the patient-code generator (AD-5), using the prefix from the region profile.
- Handoff: epic-clinical-ops creates the manual pending-payment work orders this epic lists.
- Waits on epic 5 because: it needs resolver and products.
- Waits on epic 6 because: it needs fiscal data and shipping addresses.
- Waits on epic 7 because: it needs order model, checkout and invoices.
- Waits on epic 8 because: it needs subscription discounts and voucher redemption.
