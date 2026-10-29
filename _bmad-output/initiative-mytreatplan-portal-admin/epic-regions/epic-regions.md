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

Completed at inception from the initiative's P1 and the source.

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
- architecture — the part-2 architecture document (bmad-architecture), once written

## Notes

- Handoff: epic-catalogue edits the country map through the configuration channel; this epic owns the model and its seed data.
- Geo-IP uses a local database so no IP leaves the instance (architecture document).
- Waits on epic 1 because: it needs REGION profile and global UUIDs.
