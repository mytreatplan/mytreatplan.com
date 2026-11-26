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
- Y10 (P7, user 2026-11-20): Payment methods configured per country (a billing rule limits a gateway to some countries of its group) and SEPA Core direct debit for EU doctors.

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

- Santander (user, 2026-11-21; answers part of entry 11's unknown):
  - Recurring charges on the Redsys terminal come with a paid add-on, "Servicios Venta +".
  - It includes subscriptions with recurring payments, a risk-management tool, and "TPV Get Link & Pay" (payment links by email, SMS, WhatsApp or QR).
  - Priced per month by the number of stored cards: 0–300 cards €8; 301–600 €11; 601–1000 €15.
  - The account holder is MyTPDSO Spain S.L.
  - Santander's answers (user, 2026-11-24): Venta + covers merchant-initiated charges without the doctor present (COF/MIT), in the test environment too; pre-authorisation (authorise, then capture or cancel) is possible; test credentials come only after the contract is signed.
  - Contract status (user, 2026-11-26): NOT signed yet. Santander first needs screenshots showing a product with a price, the legal notice with the company name and CIF, and the contracting conditions; the team is not ready to take them in the form Santander needs and will come back to this soon. Until then entry 11 can be planned and built against Redsys's public test terminal, but its saved-card and repeat-charge checks on our own terminal cannot run.
- Revolut (user shared the Merchant API docs, 2026-11-26; answers the Revolut half of the unknown on paper, for entry 16):
  - The API supports merchant-initiated charges on a saved card without a subscription: the first order saves the card with `savePaymentMethodFor: merchant` (the customer needs an email on a Revolut customer, `POST /api/customers`); later charges are `POST /api/orders/{order_id}/payments` with `saved_payment_method` {type, id, initiator: merchant}. The older `/orders/{id}/confirm` is deprecated. Headers: `Revolut-Api-Version` (a date, required on these endpoints).
  - Revolut also has its own Subscriptions API (plans with variations and phases, e.g. a trial; a setup order on the hosted page saves the card; Revolut charges at each billing cycle). Proposal for entry 16: use the saved-card charge, not Revolut Subscriptions, so the portal keeps owning the renewal schedule and amounts through entry 11's gateway-neutral service, as on Redsys.
  - From Revolut's guides (PDFs the user exported, 2026-11-26, at the project drive root: "Charge a customer's saved payment method" and "Set up a subscription management process"):
    - Save for the merchant on the first payment (widget `savePaymentMethodFor: "merchant"`; Revolut Pay `savePaymentMethodForMerchant: true`). The first order may be €0 (authorise the method only) or the first amount. A method saved for the customer cannot be charged with `initiator: merchant`. Saved Revolut Pay methods take merchant-initiated charges only.
    - Each period: create an order (`amount`, `currency`, `customer.id`; `metadata` recommended), then `POST /api/orders/{id}/payments` with `saved_payment_method` {type `card` or `revolut_pay`, id from `customer.payment_methods[]`, initiator `merchant`}.
    - Final payment states: `captured`/`completed` (paid; settles in about 24 h), `authorised` (manual capture only), `declined`, `failed`, `cancelled`. Webhooks fire only on final order states (`ORDER_COMPLETED`, `ORDER_PAYMENT_DECLINED`, `ORDER_PAYMENT_FAILED`); the guide suggests polling `/payments` for intermediate states. The decline reason is `payments.decline_reason` on the order.
    - Recovery after a decline: ask the doctor to top up, or reopen checkout with the failed order's token to save a new method, or cancel.
    - Merchant duties: email before each charge (fee with tax, method, date, how to cancel) and a receipt after it (also the next date); a failure email with the reason; update the terms and conditions for recurring payments.
  - Still open for entry 16: the guides show the card widget and Revolut Pay, not our hosted checkout page — check in the sandbox whether the hosted page can save a card for the merchant, or whether entry 16 needs the widget; fees; whether our merchant account has it enabled.

- Handoff (2026-10-30, epic-subscriptions inception): the order-creation service accepts a €0 order (a voucher covering the whole price), which skips the gateway, is marked paid and still gets a €0 invoice.
- Handoff (2026-11-15, included cases): an order line can be an included case of the subscription (epic-subscriptions entry 13): entry 1's paid, cancelled and expired hooks consume or release its use; entries 6 and 10 show it on the invoice at list price with an "included in your subscription" deduction to 0.00; entry 7 gives the case back on a refund only while the term runs. VAT treatment to be confirmed by the tax advisor.
- Tracer bullet: entry 1 (a test order through checkout and a fake gateway on dev).
- Lanes: one lane (1 → 2 → 3 → 14 → 5 → 6 → 7 → 8 → 9), with 4 after 3, 10 after 7, 11 after 4 and 15 after 14, 7 and 11.
- Decision (user, 2026-11-19): bank transfer (5) no longer waits on the Revolut adapter (4), which waits on the sandbox credentials; 4 is built when they arrive.
- Decision (user, 2026-11-20): entry 5's plan is split; the bank details on billing companies become entry 14, which also carries the SEPA creditor identifier and the per-country limit of a rule's gateways.
- Decision (user, 2026-11-20, after a meeting with accounting and sales): payment methods are configured per country. Bank transfer is allowed only in Spain at launch, until it is tested with the market; some countries never get it after a financial risk assessment.
- Decision (user, 2026-11-20): SEPA Direct Debit (entry 15), EU only, Core scheme, for one-off orders and renewals; at launch Spain and Portugal through Banco Santander and Ireland through Revolut if it offers collection; the other EU countries are undecided. An order is paid only when the collection is confirmed. The channel (Santander API or file, Revolut) is open; accounting asks.
- Decision (user, 2026-11-20): invoices (6) and credit notes (7) are stored as structured, immutable data with an e-invoice transmission status, so the UAE e-invoicing entry (epic-uae-launch) and VeriFactu plug in later.
- Decision (user, 2026-10-30): the user requests the Revolut Merchant sandbox and Santander's Redsys test credentials; building starts against Redsys's public test terminal.
- Decision (user, 2026-10-30): VeriFactu is not in this epic; the tax advisor confirms whether and when it applies; invoices leave room for it.
- Decision (user, 2026-10-30): an unpaid bank-transfer order gets a reminder on day 7 and expires after 14 days.
- Decision (user, 2026-10-30): invoice numbers are <company>-<instance>-<year>-<6 digits>, e.g. ES-EU-2026-000123; credit notes ES-EU-2026-R000012; restarting each year.
- Handoff: epic-subscriptions consumes and releases vouchers through the order hooks and owns renewals; epic-ordering implements the patient-name lookup hook; epic-sales-finance builds the buttons on the mark-as-paid, refund, cancel and manual-invoice services. (Correction 2026-11-21: billing companies already had their short company code, `BillingCompany.code`, since mytp-core 0.23.0; entry 6 needed no core release.)
- Handoff: epic-subscriptions and epic-ordering create orders through this epic's order model.
- Handoff: epic-sales-finance builds the admin buttons for mark as paid, cancel and refund on top of this epic's services.
- Unknown: whether Redsys (Santander must enable it) and Revolut support recurring charges on our merchant accounts; the saved-card entry waits on it.
- Waits on epic 4 because: it needs accounting feed.
- Waits on epic 5 because: it needs billing rules: company, VAT, gateway.
- Handoff (2026-11-16, epic-doctor-profile entry 5):
  - Checkout refuses an order without a usable fiscal entity (`accounts.fiscal.usable_entities`, which re-checks the billing country's region and the banned list), and links to `settings_fiscal_add?next=…`.
  - At order time, call `accounts.fiscal.mark_used(entity, at)` and keep the order's own snapshot of the buyer (AD-7). Reference the entity with SET_NULL, never PROTECT: account erasure deletes the entities.
  - An entity is edited in place, and its `version` rises.
  - Billing country AE carries an optional VAT id besides the tax id.
- Waits on epic 6 because: it needs fiscal entity on the order.
- Handoff (2026-11-17, epic-doctor-profile entry 6): pass `accounts.fiscal.vies_status(entity)` as `vies=` to `subscriptions.service.price_order_for` (`not_applicable` when there is no entity). The status is the resolver's own: `valid` gives reverse charge where the rule has the text; `invalid`, `pending` and `not_applicable` charge VAT. The `retry_vies` job turns `pending` into `valid` or `invalid`; re-checking at checkout is in deferred-work.
- Decision (user, 2026-11-24, supersedes the 2026-10-30 day-7/day-14 decision, Y5 and entry 5's expiry): bank-transfer orders no longer expire automatically; the reminder is sent every 7 days until the order is paid or cancelled; staff confirm or cancel on the admin site (epic-ordering entry 18, brought forward from epic-sales-finance). Built by epic-ordering entry 17.
- Decision (user, 2026-11-24): entry 11 is split: Redsys now (entry 11, owns the gateway-neutral repeat-charge service and the registry's save-card flag), Revolut later (entry 16).
