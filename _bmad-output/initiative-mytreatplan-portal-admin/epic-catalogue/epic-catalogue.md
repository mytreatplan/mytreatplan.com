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

Source: the initiative's P4, the requirements document's catalogue and subscription sections, and the architecture spine, AD-2, AD-3, AD-6, AD-7, AD-14, AD-16, AD-20.

- C1 (P4): The Money type in mytp-core (AD-6).
- C2 (P4): A catalogue tracer from the admin site to a priced product on dev.
- C3 (P4): Named, reusable country groups (rest of world as the catch-all) and country-map editing (AD-14).
- C4 (P4): Providers and provider products with dated prices; provider details stay on the admin site.
- C5 (P4): Our products in full: texts per language, categories, add-ons, per-aligner pricing, call allowance and length, linked and variable costs, validity, never hard-deleted.
- C6 (P4): Cloning a product into another currency with an ECB-based, editable, rounded conversion.
- C7 (P4): Subscriptions in full: tier, standard or manual, country group, products, discount per category, call extras, one default per tier and group.
- C8 (P4): Billing companies and billing rules per country group and category (AD-7, AD-16).
- C9 (P4): Voucher definitions per region (AD-20).
- C10 (P4): The catalogue in the configuration snapshot: per-region payloads, deactivation not deletion, read-only on the portal (AD-3).
- C11 (P4): The portal's pricing function (AD-16).
- C12 (P4): A staging seed with the documented catalogue and subscription grid.

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
- architecture — architecture-portal-admin/architecture-portal-admin.md, AD-2, AD-3, AD-6, AD-7, AD-14, AD-16, AD-20

## Notes

- Tracer bullet: entry 2 (a product priced on dev); it also owns the shared setup every later entry extends.
- Lanes: one lane (2 → 3 → … → 13); entries add their own snapshot section to entry 2's registry.
- Decision (user, 2026-10-30): named, reusable country groups for subscriptions and billing rules.
- Decision (user, 2026-10-30): line order net price → subscription discount → ICE deduction → voucher → floor at 0 → VAT; epic-subscriptions and epic-ordering pass the voucher and the ICE amount in.
- Decision (user, 2026-10-30): subscription discounts are configurable per subscription and per category; the standard discount applies only to plain TPS; VirtuaOrtho is 0% but its cases count for the parked 5-case program.
- Decision (user, 2026-10-30): currency clones pre-fill the ECB EUR→USD rate and AED through the 3.6725 peg, editable, rounded to whole units.
- Decision (2026-10-30): provider fiscal and payment details are field-encrypted on the admin site and never sent to the regions.
- Handoff: the resolver takes the fiscal entity's VIES status as an input; epic-doctor-profile checks VIES.
- Decision (2026-10-29, epic-platform inception): this epic builds the Money type in mytp-core (AD-6, AD-10).
- Handoff: epic-subscriptions and epic-ordering use the resolver and must not compute prices themselves.
- Assumption: prices are fixed per currency, converted once on clone.
- Waits on epic 2 because: it needs country map model.
- Waits on epic 3 because: it needs admin roles and list conventions.
- Waits on epic 4 because: it needs configuration channel.
