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

- Handoff (2026-10-30, epic-subscriptions inception): voucher usage events are received and stored on the admin site by epic-subscriptions entry 4; this epic shows them.
- Handoff (2026-11-15, included cases): the client record shows a doctor's used and remaining included cases (events from epic-subscriptions entry 10); the cancel button gives an included case back only while the term runs; margin reports count the provider cost of free cases.
- Handoff (2026-11-18, epic-doctor-profile 6.9): the admin site stores each doctor's fiscal entities and shipping addresses as child rows of the client record (`ClientFiscalEntity`, `ClientShippingAddress`, encrypted, synced from `client.updated`; `ClientRecord.active_*` and `archived_*` querysets). 6.9 builds no page. The client record page shows both lists, active ones by default with a "Show archived" filter for auditing (the portal sends the 100 most recently archived of each list, with `archived_at`). It masks per role: the tax id and the VAT id show only their last 4 characters for roles without fiscal duties. The rows have no blind index: a tax id search on this page adds one (a `sync_clients` re-sync fills it). Maker logins and the photo never reach the admin site. The child rows order by default flag, then UUID (not the portal's creation order), so the page must choose its own order (for example `used_at` or `archived_at`, or a portal order field added later).
- Idea (user, 2026-11-21): a manual order can be paid through a payment link that staff send to the doctor (Santander Redsys "TPV Get Link & Pay": email, SMS, WhatsApp or QR). It is part of the paid "Servicios Venta +" add-on, priced by the number of stored cards, which also brings recurring payments (epic-payments entry 11). Weigh it at slicing: whether a link-paid order goes through the epic-payments gateway interface and webhook like any other payment, and whether Revolut offers an equivalent for the IE and AE companies.
- Open question: cancellation and refund conditions ("provided later" in the source); settled with the user at inception.
- Waits on epic 4 because: it needs client record and contact-message events.
- Waits on epic 5 because: it needs vouchers.
- Waits on epic 7 because: it needs invoices, refunds, credit notes, paid state.
- Waits on epic 9 because: it needs orders and work orders.
- Waits on epic 11 because: it needs admin-side patient creation.
- Handoff (user, 2026-11-24): the bank-transfer 'mark as paid' button and a cancel button for pending transfer orders are built early by epic-ordering entry 18 (a 'Pending bank transfers' list, fresh code, audit log); this epic's orders view reuses or replaces them. Cancel with a refund or credit note for paid orders stays here.
