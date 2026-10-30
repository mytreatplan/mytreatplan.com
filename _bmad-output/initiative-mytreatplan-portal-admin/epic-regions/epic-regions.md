---
type: epic
title: "Customers land in the right region"
parent: initiative-mytreatplan-portal-admin
covers: [P1]
after: []
assignee: ""
risk: medium
---

# Customers land in the right region

## Description

Delivers the rest of P1: the country map (eu / ae / banned) seeded in each instance as a synced model whose editing arrives with epic-catalogue; the home region saved at signup; the geo-IP suggestion on the public site with "stay here" remembered; the signup warning for another region's country; the footer and legal texts naming the region's main company; the mytreatplan.es redirect.

## Outcome

Visitors and doctors end up on the instance of their region; the signal is a GCC visitor on .com seeing the .ae suggestion, and a doctor signing up with a GCC country being warned.

## Requirements

Source: the initiative's P1 (the parts not delivered by epic-platform) and the architecture spine, AD-3, AD-13, AD-14, AD-23.

- R1 (P1): Country map with state eu, ae or banned per country, seeded with the agreed lists in a shape the admin site's configuration snapshot replaces later; one lookup helper for a country's state and the other region's URLs (AD-14).
- R2 (P1): Home region stored at signup from the instance's region profile and never recomputed (AD-14).
- R3 (P1): Signup step 1 warns when the country belongs to the other region (with a link to its signup) and refuses a banned country.
- R4 (P1): Geo-IP suggestion on public pages from a local DB-IP Lite database: a slim dismissible bar with a remembered "stay here"; not for bots; attribution shown (AD-14).
- R5 (P1): The footer and the legal pages name the region's company; legal documents are region-specific.
- R6 (P1): mytreatplan.es and www answer with a 301 to mytreatplan.com, only on the production EU site.

## Done when

1. A visitor whose IP is in a GCC country sees the .ae suggestion on .com, can stay, and is not asked again; the reverse holds on the ae staging site.
2. Signup with a country of the other region warns and offers the right signup page; a banned country cannot sign up.
3. Each new doctor has a home region recorded at signup; changing the country map later does not change it.
4. The footer and the legal pages name the company of the instance's region.
5. mytreatplan.es answers with a 301 to mytreatplan.com (checked at the EU go-live).

## Boundaries

Routing and region identity on the portal. The billing-country block lives with fiscal data (epic-doctor-profile); editing the map lives in epic-catalogue.

## References

- parent — _bmad-output/initiative-mytreatplan-portal-admin/initiative-mytreatplan-portal-admin.md, section Requirements (P1)
- reqs — ../MyTP_Reqs_Def_V1_21Oct2026_v3.docx (parent folder of the repo)
- architecture — architecture-portal-admin/architecture-portal-admin.md, AD-3, AD-13, AD-14, AD-23

## Notes

- Tracer bullet: entry 1 (country map and helper), then 2 and 3 carry it through signup on dev and dev-ae.
- Lanes: signup (1 → 2 → 3); suggestion (4 → 5 → 6, sharing base.html); redirect (7) on its own.
- Decision (user, 2026-10-30): starting lists — ae: AE, SA, KW, QA, BH, OM, JO, LB, IQ, PS, YE, TR, EG, MA, ZA; banned: the other African countries plus IR, SY, KP, CU, RU, BY; eu: every other country.
- Assumption: the sanctions part of the banned list (IR, SY, KP, CU, RU, BY) waits on the lawyer's confirmation; until epic-catalogue can edit the map, a change is a data migration.
- Decision (user, 2026-10-30): the geo-IP suggestion is a slim dismissible top bar.
- Decision (user, 2026-10-30): legal documents become region-specific now; .com keeps its current texts and dev-ae serves placeholders, both flagged provisional until the lawyer's .com texts and epic-uae-launch's UAE texts arrive.
- Open question: whether changing the profile country to a banned one is refused too; epic-doctor-profile settles it.
- Decision (2026-10-30): the .es redirect is checked on staging by a template test and nginx -t; its curl check goes into epic-eu-golive (entry 8).
- Handoff: epic-catalogue edits the country map through the configuration channel; this epic owns the model and its seed data.
- Geo-IP uses a local database so no IP leaves the instance (architecture document).
- Waits on epic 1 because: it needs REGION profile and global UUIDs.
