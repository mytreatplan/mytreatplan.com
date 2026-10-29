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

Everything the .ae instance needs beyond configuration: the UAE hosting comparison and provisioning (VM, managed PostgreSQL, object storage in the UAE), the UAE gateway adapter, TDRA SMS sender registration, Meta-approved WhatsApp templates, legal texts for the UAE company, the UAE tax-invoice format, then the .ae production go-live.

## Outcome

GCC doctors use the platform on mytreatplan.ae with their data in the UAE; the signal is a first paid .ae order in production.

## Requirements

Completed at inception from the initiative's P15 and the source.

## Done when

1. A UAE provider is chosen from the comparison and provisioned with provision.sh, managed PostgreSQL over TLS and object storage in the UAE.
2. A UAE gateway adapter passes the same sandbox checks as Revolut and Redsys; invoices meet the UAE tax-invoice rules (TRN, 5% VAT).
3. SMS to UAE numbers come from a registered sender; the legal texts name the UAE company.
4. mytreatplan.ae is live in production on the same build, and a first order completes.

## Boundaries

The UAE region's launch. The preparation entries (hosting comparison, TDRA, Meta, legal texts, gateway choice) have no prerequisite and start at once; only the go-live waits on epic-eu-golive.

## References

- parent — _bmad-output/initiative-mytreatplan-portal-admin/initiative-mytreatplan-portal-admin.md, section Requirements (P15)
- reqs — ../MyTP_Reqs_Def_V1_21Oct2026_v3.docx (parent folder of the repo)
- architecture — the part-2 architecture document (bmad-architecture), once written

## Notes

- Decision (user, 2026-10-29): EU first, then UAE quickly; preparation runs during EU development.
- Waits on epic 7 because: it needs gateway interface and invoicing.
- Waits on epic 15 because: it needs EU go-live, for the production step only.
