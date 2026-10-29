---
type: epic
title: "Staff define what is sold, where and at what price"
parent: initiative-mytreatplan-portal-admin
covers: [P4]
after: []
assignee: ""
risk: high
---

# Staff define what is sold, where and at what price

## Description

The commercial definitions in the admin site and their sync to the regions: providers and provider products with prices over time; our products with i18n texts, currencies, add-ons, quantity pricing, call allowances, linked costs and clone-with-conversion; subscriptions by country group and tier with their products and discounts, one default per tier and group; billing rules per company × country × category (VAT or exemption text, gateway, availability); manual vouchers; country-map editing; and the resolver that tells a portal what a given doctor may buy and at what price.

## Outcome

Commercial staff change what is sold without a developer; the signal is a new product linked to a subscription appearing, at the right price and VAT, for a test doctor of the right country on staging.

## Requirements

Completed at inception from the initiative's P4 and the source.

## Done when

1. A product is created, cloned into USD and AED with converted prices, linked to a subscription, and appears for a doctor of a matching country on staging, and for no other.
2. Two subscriptions cannot both be the default for one tier and country group.
3. A billing rule decides the company, VAT rate or exemption text and gateway for a sample of countries (ES, PT, IE, DE, US, AE).
4. A voucher created in the admin site is known to the regions; editing the country map changes routing on staging.

## Boundaries

Definitions and the resolver. Not buying (epic-subscriptions, epic-ordering), not voucher redemption (epic-subscriptions), not voucher usage reports (epic-sales-finance).

## References

- parent — _bmad-output/initiative-mytreatplan-portal-admin/initiative-mytreatplan-portal-admin.md, section Requirements (P4)
- reqs — ../MyTP_Reqs_Def_V1_21Oct2026_v3.docx (parent folder of the repo)
- architecture — the part-2 architecture document (bmad-architecture), once written

## Notes

- Handoff: epic-subscriptions and epic-ordering use the resolver and must not compute prices themselves.
- Assumption: prices are fixed per currency, converted once on clone.
- Waits on epic 2 because: it needs country map model.
- Waits on epic 3 because: it needs admin roles and list conventions.
- Waits on epic 4 because: it needs configuration channel.
