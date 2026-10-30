---
type: epic
title: "Doctors keep their profile, fiscal data and maker logins"
parent: initiative-mytreatplan-portal-admin
covers: [P5]
after: []
assignee: ""
risk: medium
---

# Doctors keep their profile, fiscal data and maker logins

## Description

The doctor area's layout and home (Annex I mockup) and the settings section: personal data, fiscal entities with per-country validation and VIES, the billing-country block, shipping addresses, and aligner-maker logins stored encrypted in the region (revealing them to staff is epic-clinical-ops).

## Outcome

A doctor can complete everything an order needs; the signal is a doctor on staging saving personal data, a valid fiscal entity, a shipping address and a maker login.

## Requirements

Completed at inception from the initiative's P5 and the source.

## Done when

1. The logged-in home and the personal-data page match the Annex I mockups in EN and ES.
2. A Spanish fiscal entity with an invalid NIF is refused; an EU VAT number is checked against VIES; a billing country of the other region is not saved and a warning is shown.
3. A doctor adds several fiscal entities and shipping addresses and chooses defaults.
4. A maker login is stored encrypted and is never shown back in clear on the portal.

## Boundaries

The doctor's own data. Not subscriptions, orders or the case pages.

## References

- parent — _bmad-output/initiative-mytreatplan-portal-admin/initiative-mytreatplan-portal-admin.md, section Requirements (P5)
- reqs — ../MyTP_Reqs_Def_V1_21Oct2026_v3.docx (parent folder of the repo)
- architecture — the part-2 architecture document (bmad-architecture), once written

## Notes

- Handoff (2026-10-30, epic-catalogue inception): the fiscal entity's VIES status is passed to epic-catalogue's resolver (entry 12) as an input.
- Handoff (2026-10-30, epic-admin-interface inception): profile changes keep going through the write path that emits client.updated (epic-admin-interface entry 8); this epic extends that payload with fiscal entities and shipping addresses.
- Handoff: epic-clinical-ops reveals maker logins to the assigned clinical user and logs each view.
- Source conflict: The Admin Site, Clients — Ormco contract acceptance; ignored by the user's decision, 2026-10-29.
- Waits on epic 2 because: it needs country map and home region.
