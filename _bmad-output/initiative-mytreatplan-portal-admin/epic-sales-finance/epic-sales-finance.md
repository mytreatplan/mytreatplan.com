---
type: epic
title: "Sales and finance run orders and accounts"
parent: initiative-mytreatplan-portal-admin
covers: [P12]
after: []
assignee: ""
risk: high
---

# Sales and finance run orders and accounts

## Description

The Commercial/Sales and Financial areas: the clients list (extending the client record) with contact messages, sale orders with cancel (refund or credit note) and mark as paid, manual orders and billing for an existing customer, voucher usage, sales, costs and margin reports, and the accounting export, all with patient codes only.

## Outcome

Sales and finance work from one place for all regions; the signal is a manual order billed, a bank transfer marked paid, an order cancelled with a credit note, and an accounting export, all on staging.

## Requirements

Completed at inception from the initiative's P12 and the source.

## Done when

1. A Commercial Regional Manager sees only their region's clients and orders; a Commercial Manager and a Financial Manager see all.
2. Mark as paid closes a bank-transfer order; cancel issues a refund or credit note per the conditions settled at inception.
3. A manual order for an existing customer (new patient created through epic-clinical-ops' service) is billed by the right company.
4. Margin reports use the linked provider costs; the accounting export shows patient codes only.

## Boundaries

The admin side of sales and money. The services underneath are epic-payments's.

## References

- parent — _bmad-output/initiative-mytreatplan-portal-admin/initiative-mytreatplan-portal-admin.md, section Requirements (P12)
- reqs — ../MyTP_Reqs_Def_V1_21Oct2026_v3.docx (parent folder of the repo)
- architecture — the part-2 architecture document (bmad-architecture), once written

## Notes

- Open question: cancellation and refund conditions ("provided later" in the source); settled with the user at inception.
- Waits on epic 4 because: it needs client record and contact-message events.
- Waits on epic 5 because: it needs vouchers.
- Waits on epic 7 because: it needs invoices, refunds, credit notes, paid state.
- Waits on epic 9 because: it needs orders and work orders.
- Waits on epic 11 because: it needs admin-side patient creation.
