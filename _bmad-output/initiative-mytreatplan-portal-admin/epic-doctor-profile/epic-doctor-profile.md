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

Source: the initiative's P5, the Annex I mockups and the architecture spine, AD-2, AD-5, AD-9, AD-14, AD-15, AD-18.

- D1 (P5): The doctor area's layout and home page per the mockups, in EN and ES.
- D2 (P5): Personal data and consents; country and login email read-only (changes come with the support area); new mobile and contact email confirmed by a code.
- D3 (P5): An optional profile photo, regional only.
- D4 (P5): Fiscal entities, field-encrypted; billing country in the site's region and not banned.
- D5 (P5): Tax id checks per country and VIES, with a pending state and retries; the status feeds the resolver.
- D6 (P5): Shipping addresses, field-encrypted.
- D7 (P5): Maker logins, field-encrypted, never shown back; revealed to staff by epic-clinical-ops.
- D8 (P5): Fiscal entities and shipping addresses in the admin client record.

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
- architecture — architecture-portal-admin/architecture-portal-admin.md, AD-2, AD-5, AD-9, AD-14, AD-15, AD-18

## Notes

- Handoff (2026-10-30, epic-ordering inception): the case wizard links to entry 8's maker-logins settings page when a login is missing.
- Tracer bullet: entry 1 (the doctor home on dev).
- Lanes: one lane (1 → 2 → … → 9); entry 2 owns the settings shell, the profile model and the write pattern the others extend.
- Decision (user, 2026-10-30): the doctor cannot change their country; a Sysadmin changes it on request, never to a banned country and only within the doctor's home region (another region means a new account).
- Decision (user, 2026-10-30): no country or login-email change is possible until the support area (epic-support) exists; the profile says so.
- Decision (user, 2026-10-30): a login-email change is entered by a Sysadmin and takes effect only after the doctor confirms a code sent to the new address.
- Decision (user, 2026-10-30): a new mobile and a new contact email are each confirmed by a code.
- Decision (user, 2026-10-30): when VIES is unavailable the entity is saved as pending and retried; VAT is charged until it is valid.
- Decision (2026-10-30): after a mobile change the doctor's other sessions end and remembered devices are forgotten.
- Handoff (2026-10-30, epic-catalogue inception): the fiscal entity's VIES status is passed to epic-catalogue's resolver (entry 12) as an input.
- Handoff (2026-10-30, epic-admin-interface inception): profile changes keep going through the write path that emits client.updated (epic-admin-interface entry 8); this epic extends that payload with fiscal entities and shipping addresses.
- Handoff: epic-clinical-ops reveals maker logins to the assigned clinical user and logs each view.
- Source conflict: The Admin Site, Clients — Ormco contract acceptance; ignored by the user's decision, 2026-10-29.
- Waits on epics 2, 3, 4 and 5 because: country map and home region; mytp-core codes; client record sync and contract; the resolver's VIES input.
