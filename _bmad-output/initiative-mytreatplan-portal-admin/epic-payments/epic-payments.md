---
type: epic
title: "Doctors pay and get invoiced by the right company"
parent: initiative-mytreatplan-portal-admin
covers: [P7, P8]
after: []
assignee: ""
risk: high
---

# Doctors pay and get invoiced by the right company

## Description

The order model every purchase uses; one gateway interface with Revolut and Redsys adapters on hosted pages; bank transfer with a paid state; refunds; saved cards for renewals. Invoices and credit notes per billing company with their numbering series, VAT or exemption text; the doctor's copy with patient names, downloadable from the dashboard; the accounting copy with codes, sent on the accounting feed. It delivers the invoicing part of P8.

## Outcome

Every purchase is paid through the right gateway and invoiced by the right company; the signal is test payments in both gateway sandboxes producing correct invoices on staging and in the admin site's accounting books.

## Requirements

Source: the initiative's P7 and P8 (invoicing) and the architecture spine, AD-6, AD-7, AD-8, AD-15, AD-16, AD-18, AD-20, AD-23, AD-24.

- Y1 (P7): The order model every purchase uses, the order-creation service, the checkout, and order hooks (paid, cancelled, expired).
- Y2 (P7): The gateway interface and registry with hardened webhook handling.
- Y3 (P7): Revolut for the Irish company.
- Y4 (P7): Redsys for the Spanish company.
- Y5 (P7): Bank transfer with a mark-as-paid service, a day-7 reminder and expiry after 14 days.
- Y6 (P8): Invoices and credit notes per company, instance and year, restore-safe, immutable, with a manual-invoice service (AD-7).
- Y7 (P8): Invoice PDFs in the doctor's language with patient names added at render time.
- Y8 (P8): The accounting feed with patient codes only (AD-18, AD-23).
- Y9 (P7): Saved cards and repeat charges for renewals (AD-8).

## Done when

1. A test order from a Spanish doctor pays through Redsys and one from an Irish doctor through Revolut, both sandboxes, confirmed by webhook.
2. Each produces an invoice from the right company, in its numbering series, with the right VAT or exemption text; the doctor downloads it, and the accounting copy in the admin site shows codes only.
3. A bank-transfer order stays pending until marked as paid; a refund issues a credit note.
4. A saved card is charged again without the doctor present (renewal path).

## Boundaries

Paying and invoicing. Not what is bought (epic-subscriptions, epic-ordering). The admin buttons for mark as paid and cancel are epic-sales-finance's; this epic builds the services they call.

## References

- parent — _bmad-output/initiative-mytreatplan-portal-admin/initiative-mytreatplan-portal-admin.md, section Requirements (P7, P8)
- reqs — ../MyTP_Reqs_Def_V1_21Oct2026_v3.docx (parent folder of the repo)
- architecture — architecture-portal-admin/architecture-portal-admin.md, AD-6, AD-7, AD-8, AD-15, AD-16, AD-18, AD-20, AD-23, AD-24

## Notes

- Handoff (2026-10-30, epic-subscriptions inception): the order-creation service accepts a €0 order (a voucher covering the whole price), which skips the gateway, is marked paid and still gets a €0 invoice.
- Tracer bullet: entry 1 (a test order through checkout and a fake gateway on dev).
- Lanes: one lane (1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9), with 10 after 7 and 11 after 4.
- Decision (user, 2026-10-30): the user requests the Revolut Merchant sandbox and Santander's Redsys test credentials; building starts against Redsys's public test terminal.
- Decision (user, 2026-10-30): VeriFactu is not in this epic; the tax advisor confirms whether and when it applies; invoices leave room for it.
- Decision (user, 2026-10-30): an unpaid bank-transfer order gets a reminder on day 7 and expires after 14 days.
- Decision (user, 2026-10-30): invoice numbers are <company>-<instance>-<year>-<6 digits>, e.g. ES-EU-2026-000123; credit notes ES-EU-2026-R000012; restarting each year.
- Handoff: epic-subscriptions consumes and releases vouchers through the order hooks and owns renewals; epic-ordering implements the patient-name lookup hook; epic-sales-finance builds the buttons on the mark-as-paid, refund, cancel and manual-invoice services; epic-catalogue's billing companies gain a short company code (entry 6).
- Handoff: epic-subscriptions and epic-ordering create orders through this epic's order model.
- Handoff: epic-sales-finance builds the admin buttons for mark as paid, cancel and refund on top of this epic's services.
- Unknown: whether Redsys (Santander must enable it) and Revolut support recurring charges on our merchant accounts; the saved-card entry waits on it.
- Waits on epic 4 because: it needs accounting feed.
- Waits on epic 5 because: it needs billing rules: company, VAT, gateway.
- Waits on epic 6 because: it needs fiscal entity on the order.
