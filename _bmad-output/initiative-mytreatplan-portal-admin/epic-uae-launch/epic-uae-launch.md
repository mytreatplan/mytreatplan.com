---
type: epic
title: "UAE instance live"
parent: initiative-mytreatplan-portal-admin
covers: [P15]
after: []
assignee: ""
risk: high
---

# UAE instance live

## Description

Everything the .ae instance needs beyond configuration: the UAE hosting comparison and provisioning (VM, managed PostgreSQL, object storage in the UAE), the UAE gateway adapter, TDRA SMS sender registration, Meta-approved WhatsApp templates, legal texts for the UAE company, the UAE tax-invoice format, UAE e-invoicing through an Accredited Service Provider (Peppol, PINT AE), then the .ae production go-live.

## Outcome

GCC doctors use the platform on mytreatplan.ae with their data in the UAE; the signal is a first paid .ae order in production.

## Requirements

Completed at inception from the initiative's P15 and the source.

## Done when

1. A UAE provider is chosen from the comparison and provisioned with provision.sh, managed PostgreSQL over TLS and object storage in the UAE.
2. A UAE gateway adapter passes the same sandbox checks as Revolut and Redsys; invoices meet the UAE tax-invoice rules (TRN, 5% VAT).
3. SMS to UAE numbers come from a registered sender; the legal texts name the UAE company.
4. mytreatplan.ae is live in production on the same build, and a first order completes.
5. Every UAE tax invoice and credit note is sent through the appointed ASP and its status recorded, by the 1 July 2027 deadline.

## Boundaries

The UAE region's launch. The preparation entries (hosting comparison, TDRA, Meta, legal texts, gateway choice) have no prerequisite and start at once; only the go-live waits on epic-eu-golive.

## References

- parent — _bmad-output/initiative-mytreatplan-portal-admin/initiative-mytreatplan-portal-admin.md, section Requirements (P15)
- reqs — ../MyTP_Reqs_Def_V1_21Oct2026_v3.docx (parent folder of the repo)
- architecture — the part-2 architecture document (bmad-architecture), once written

## Notes

- Decision (user, 2026-10-29): EU first, then UAE quickly; preparation runs during EU development.
- Decision (user, 2026-11-18): the UAE company banks with Wio Bank. At inception, the first entry is "choose the UAE gateway": check Wio against the adapter needs (online card acquiring API, hosted page, signed webhooks, refunds, sandbox, saved cards with repeat charges) and compare UAE providers that settle into the Wio account; record the choice; the adapter entry follows it on epic-payments entry 2's interface. Tabby/Tamara/Amazon Pay (initiative Notes, 2026-10-26) would be extra gateways next to the card one.
- Decision (user, 2026-11-20, after a meeting with accounting and sales): the UAE company must be ready for UAE e-invoicing. It is under AED 50M a year, so it must appoint an Accredited Service Provider (ASP) by 31 March 2027 and go live on 1 July 2027 (secondary sources: Ministerial Decision 244 of 2025; reported penalty AED 5,000 a month). Invoices go in PINT AE (UBL) over Peppol through the ASP, which reports to the FTA. At inception, an entry "UAE e-invoicing through an ASP": send each tax invoice and credit note through the ASP's API and store its status on the invoice's e-invoice status field (epic-payments entry 6). Accounting will send the official document; it replaces these details. Choosing the ASP is a business action with that deadline, and the e-invoicing go-live does not wait on the .ae production go-live.
- Waits on epic 7 because: it needs gateway interface and invoicing.
- Waits on epic 15 because: it needs EU go-live, for the production step only.
