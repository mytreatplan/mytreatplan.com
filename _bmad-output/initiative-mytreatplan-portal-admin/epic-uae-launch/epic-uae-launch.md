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
5. Every UAE tax invoice and credit note is sent by the .ae instance through the appointed ASP's API and its status recorded; onboarding and testing with the ASP finished before 31 March 2027, mandatory from 1 July 2027.

## Boundaries

The UAE region's launch. The preparation entries (hosting comparison, TDRA, Meta, legal texts, gateway choice) have no prerequisite and start at once; only the go-live waits on epic-eu-golive.

## References

- parent — _bmad-output/initiative-mytreatplan-portal-admin/initiative-mytreatplan-portal-admin.md, section Requirements (P15)
- reqs — ../MyTP_Reqs_Def_V1_21Oct2026_v3.docx (parent folder of the repo)
- architecture — the part-2 architecture document (bmad-architecture), once written
- e-invoicing — ../UAE_E-Invoicing_Briefing.pptx (parent folder of the repo; DGTX, the accountants, 2026-11)

## Notes

- Decision (user, 2026-10-29): EU first, then UAE quickly; preparation runs during EU development.
- Decision (user, 2026-11-18): the UAE company banks with Wio Bank. At inception, the first entry is "choose the UAE gateway": check Wio against the adapter needs (online card acquiring API, hosted page, signed webhooks, refunds, sandbox, saved cards with repeat charges) and compare UAE providers that settle into the Wio account; record the choice; the adapter entry follows it on epic-payments entry 2's interface. Tabby/Tamara/Amazon Pay (initiative Notes, 2026-10-26) would be extra gateways next to the card one.
- Decision (user, 2026-11-20, after a meeting with accounting and sales; confirmed by the accountants' briefing, References "e-invoicing"): the UAE company must be ready for UAE e-invoicing. Under AED 50M a year: appoint a Ministry of Finance accredited service provider (ASP) by 31 March 2027, with onboarding and testing finished before that date; e-invoicing mandatory from 1 July 2027; penalty AED 5,000 a month for missing either. Sales invoices and credit notes (B2B and B2G) go from us to our ASP, which reports the tax data to the FTA and delivers to the customer's ASP. VAT returns continue as before. (Format per secondary sources: PINT AE, UBL, over Peppol.)
- Decision (user, 2026-11-20, on the briefing): (1) DGTX offers two ASPs, DG Ledger (theirs) and Zoho Books: choose the one with a documented API to submit invoices and credit notes and read their status, with a test environment (Zoho Books has a public API; ask DGTX whether DG Ledger has one). (2) The .ae instance sends each invoice and credit note to the ASP when it issues it and stores the ASP's status on the invoice's e-invoice status field (epic-payments entry 6); not the admin site's accounting copy. (3) Purchase invoices (input VAT), which also arrive through the ASP, are accounting's, in the ASP's platform: out of the portal's scope. (4) The ASP integration is built and tested before 31 March 2027, whatever the .ae launch date. At inception: an entry "choose the ASP" (the API check, with DGTX; no prerequisite) and an entry "UAE e-invoicing through the ASP" (after epic-payments entries 6 and 7; must finish before 31 March 2027; does not wait on the .ae production go-live).
- Waits on epic 7 because: it needs gateway interface and invoicing.
- Waits on epic 15 because: it needs EU go-live, for the production step only.
