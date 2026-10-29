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

Completed at inception from the initiative's P7, P8 and the source.

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
- architecture — the part-2 architecture document (bmad-architecture), once written

## Notes

- Handoff: epic-subscriptions and epic-ordering create orders through this epic's order model.
- Handoff: epic-sales-finance builds the admin buttons for mark as paid, cancel and refund on top of this epic's services.
- Unknown: whether Redsys (Santander must enable it) and Revolut support recurring charges on our merchant accounts; the saved-card entry waits on it.
- Waits on epic 4 because: it needs accounting feed.
- Waits on epic 5 because: it needs billing rules: company, VAT, gateway.
- Waits on epic 6 because: it needs fiscal entity on the order.
