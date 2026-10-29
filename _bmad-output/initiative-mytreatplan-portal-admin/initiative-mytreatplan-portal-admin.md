---
type: initiative
title: MyTreatPlan regional portals and admin site
parent: none
covers: [P1, P2, P3, P4, P5, P6, P7, P8, P9, P10, P11, P12, P13, P14, P15]
after: []
assignee: ""
risk: high
---

# MyTreatPlan regional portals and admin site

## Description

Part 2 of the MyTreatPlan platform. Part 1 (initiative-mytreatplan-client-portal) delivered the public site, Doctor signup and login on staging. Part 2 turns it into the business platform: one portal codebase deployed per region (EU on mytreatplan.com first, UAE on mytreatplan.ae soon after) where doctors subscribe, keep their profile and fiscal data, order TPS and VirtuaOrtho services, upload patient records and follow their cases; and a separate admin site (own repo, admin.mytpdso.com) that runs sales, finance and clinical operations for every region as if they were one.

## Outcome

Doctors in the EU subscribe, order, pay and follow a case end to end on mytreatplan.com while staff run the whole flow from the admin site; the signal is a first paid order taken through every case stage in production, followed by the UAE instance running the same code with configuration only.

## Requirements

Source: the requirements document MyTP_Reqs_Def_V1_21Oct2026_v3 (parent folder of the repo) and the user's decisions in chat, 2026-10-24 to 2026-10-29, recorded in Notes; the decisions override the document where they differ.

- P1 Regions: a REGION profile per deployment (domain, legal company, time zone, languages, currencies); a country map with states eu / ae / banned, edited in the admin site; the home region saved at signup; a geo-IP suggestion on the public site; a signup warning when the country belongs to another region; footer and legal texts name the region's main company; mytreatplan.es redirects (301) to .com; immutable global ids (UUID) on every record the admin site consolidates.
- P2 Admin site access: separate repo and domain (admindev.mytpdso.com, admin.mytpdso.com), never linked from the mytreatplan domains and hidden from robots; admin users separate from doctors; roles Sysadmin, Financial Manager, Commercial Manager, Commercial Regional Manager, Clinical Manager, Clinical User; 2FA login reused from part 1; no signup and no web password recovery (the superuser resets passwords; an SSH-run command for emergencies); a dashboard per role; list conventions (CSV export, column ordering, search, fixed fields, view more); confirmation before every change; admin user management; for every admin user: my profile, my messages and alerts.
- P3 Admin–instance interface: a configuration channel (admin → instances); business events (instances → admin, anonymised or aggregated); an accounting feed (invoices, payments, refunds with patient codes, stored centrally); a remote-access API (patient and clinical data read and written live, never stored centrally); service authentication, encryption and signing; every exchange and every access logged in the region.
- P4 Catalogue and billing rules (admin): providers; provider products with prices over time; our products with i18n texts, currency, validity, linked provider costs, add-ons (Byte Sync, Junior, one arch, ODB DigitalTwin), quantity pricing (Spark on Demand), call allowances, and a clone function with currency conversion; subscriptions (code, public i18n texts, country group, standard or manual, tier, products and discounts, one default per tier and country group); billing rules per billing company × customer country × product category (VAT rate, 0 allowed with its exemption text; gateway; availability); manual vouchers (generated code, validity dates and region, fixed amount, currency, general or user-specific); editing the country map; all synced to the instances; the resolver that decides what a doctor sees and at what price.
- P5 Doctor profile: the doctor-area layout and home (mockup); personal data per the mockup (language, country, city, time zone, photo, title, profession, phones, emails, consents); fiscal entities (several per doctor, per-country validation, VIES for EU VAT numbers, billing country must belong to the instance's region, otherwise not saved and a warning shown); shipping addresses (mandatory for VirtuaOrtho); aligner-maker logins stored encrypted in the region.
- P6 Subscriptions on the portal: the Basic default for the doctor's country assigned at signup; the upgrade screen after signup, then the welcome/onboarding page; the upgrade reminder in the navigation, home and dashboard (hidden for Premium); buying Advanced or Premium (annual upfront or monthly), renewal, upgrade, cancellation and expiry (terms settled at inception); a manually assigned special subscription stops self-service switching.
- P7 Payments: one gateway interface (start, confirm by webhook, refund, saved card for renewals) with Revolut (Ireland) and Redsys (Spain) adapters, hosted payment pages only (PCI SAQ A); bank transfer for selected countries with "mark as paid"; the gateway chosen by billing company; the order model every purchase uses.
- P8 Ordering and invoicing: patients (select or create; name and surname or a code; optional photo; an internal patient code); product pages and the "start a case" banner; the case wizard (TPS by maker or in-house: ICE, short, medium, long, one arch, Junior; VirtuaOrtho: One, DigitalTwin, ODB, retainers, Spark on Demand, Byte Sync); a cart grouped by patient; vouchers (subscription and try-me, once per person); the ICE amount deducted from a later planning for the same patient; invoices and credit notes per billing company with its numbering, VAT or exemption text; the doctor's copy with patient names, the accounting copy with codes; one work order per patient-service; order and payment emails; manual "pending payment" work orders shown to the doctor to add to an order.
- P9 Patient file and case lifecycle (doctor site): the patient list; the patient file (comments, current service, history, appointments block); stages Pending assignment → Assigned → (Pending registers upload) → (Processing prescription) → (Prescription done) → Pending appointment → In development → Pending review → Pending approval → Approved/finished; records upload to regional object storage (large files, resumable, virus scan) with its status; deletion jobs (patient 48 h after marking, records 7 days after being marked uploaded to the maker); case approval with confirmation; STL download for in-house printing; automatic messages to the doctor on assignment and on pending appointment, pending review and pending approval.
- P10 Clinical operations (admin): the clinical view of clients; patients per region (remote); work orders; specialist calendars and availability; automatic pre-assignment (first free slot, balanced workload) that becomes firm after 24 h unless the Clinical Manager confirms or changes it; manual assignment; Clinical Users see only their assigned work orders; records download and "uploaded to maker" marking; revealing a doctor's maker login, every view logged; manual patients and manual work orders; the team calendar; clinical reports.
- P11 Appointments: the doctor requests an appointment from the case page at least 72 h ahead and the specialist confirms; the specialist records an appointment arranged by WhatsApp or email; the meeting link on the case page (entered manually for now); calls counted per product (2–5; Spark Advanced one more; Premium one more on VirtuaOrtho) and call length limits (25/30 min; Premium 35 min to 1 h) with a warning to the doctor near the limit; my calendar for admin users.
- P12 Sales and finance (admin): the clients list with contact messages; sale orders with cancel (refund or credit note) and mark as paid; manual order and billing for an existing customer; voucher usage; sales, costs and margin reports; the accounting export.
- P13 Support: FAQ, knowledge base, support tickets on the doctor site (subject, description, attachments stored in the region; follow up and close) and for admin users; handling and answering tickets in the admin site.
- P14 Platform: admin staging (admindev.mytpdso.com, same host as portal staging, separate repo); a second staging site with REGION=ae; a background job runner for both sites; regional object storage; CI and deploys for the admin repo; EU production for the portal and, on its own instance, the admin site.
- P15 UAE launch: UAE hosting (VM, managed PostgreSQL and object storage in the UAE); the UAE gateway; TDRA SMS sender registration; Meta-approved WhatsApp templates; legal texts for the UAE company; the UAE tax-invoice format; the .ae instance in production.

## Done when

1. In EU production a new doctor signs up, upgrades to Advanced, orders a VirtuaOrtho case with a voucher, pays by card, and receives an invoice from the right company with the right VAT.
2. Staff take that case through every stage from the admin site (assignment, records handling, appointment, approval), and every access to regional data is logged in the region.
3. No patient data is in the admin site's database, and the accounting export shows patient codes only.
4. The same build runs with REGION=ae on staging and routes GCC visitors and doctors correctly, with no code change.
5. The UAE instance is live in production after the EU go-live.

## Boundaries

Split by capability; the portal and the admin site are the two deployment units. Not in part 2 (Notes, Parked): wholesale, 5-case reward vouchers, education, the case-support chatbot, the WhatsApp plugin, the Zoom API and its account pool, the admin system area beyond access, Tabby/Tamara. Out of scope: an external CRM (Zoho), Arabic, Portuguese.

- Touch point: part-1 signup (country, user model) — records the home region and shows the region warning; owner: epic-regions.
- Touch point: part-1 2FA login and Twilio Verify — reused for admin sign-in; owner: epic-admin-access.
- Touch point: part-1 transactional email and Twilio SMS — automatic messages by email with SMS as fallback; owner: epic-case-lifecycle.
- Touch point: part-1 welcome route (2.1) — onboarding content; owner: epic-subscriptions.
- Touch point: part-1 held production stories 1.7, 1.12, 1.10, 1.11, 3.13 — run at the EU go-live; owner: epic-eu-golive.
- Touch point: part-1 story 1.6 (CRM forwarding) — dropped; contact messages reach the admin site as an event; owner: epic-admin-interface (event), epic-sales-finance (list).
- Touch point: Revolut, Redsys, VIES, object storage provider — configured; owners: epic-payments, epic-doctor-profile, epic-platform.

Tracer path: platform → regions → admin access → interface → catalogue → profile → payments → subscriptions → ordering → case lifecycle → clinical → appointments → sales and finance → support → EU go-live → UAE.

## References

- source — MyTP_Reqs_Def_V1_21Oct2026_v3.docx, parent folder of the repo (all sections and Annex I mockups)
- previous — _bmad-output/initiative-mytreatplan-client-portal/initiative-mytreatplan-client-portal.md
- design — MyTreatPlan design system, https://claude.ai/artifact/N6SvdLiD4asJTiZJakaqB3; the doctor-site mockups in Annex I of the source

## Notes

- Decision (user, 2026-10-24/25): two regions. GCC (plus the rest of the Middle East, Africa and Turkey) on mytreatplan.ae, billed by the UAE company; the rest of the world on mytreatplan.com, billed by Ireland, except Spain and Portugal (Spanish company). Asia Pacific stays on .com, billed by Ireland, for now. Most African countries are banned; the lists are configurable. A US region may follow; it must be configuration, not a fork.
- Decision (user, 2026-10-25): one codebase with a REGION profile; global UUIDs; the admin site is a separate project and owns the catalogue; the instances push events and the admin site never touches an instance database.
- Decision (user, 2026-10-25): geo-IP only suggests; the signup country warns; the billing country blocks. Someone with billing addresses in two regions has two accounts, never cross-checked.
- Decision (user and lawyer, 2026-10-25/26): patient personal and medical data is stored only in its region and accessed remotely by staff. EU staff may access and store billing data centrally for accounting, auditing and consolidation under an intragroup agreement that customers accept; every exchange restricted, logged and encrypted. Accounting copies show patient codes, never names; the doctor's own cart and invoices show names.
- Decision (user, 2026-10-25): no Arabic; .ae in English, French maybe later; no Portuguese for now. Zoho is out; the in-house admin site replaces it.
- Decision (user, 2026-10-26): gateways Redsys (Spain), Revolut (Ireland); UAE to be decided, plus Tabby/Tamara/Amazon Pay. Workshops are billed by the local company; another region's doctor gets a manual invoice.
- Decision (user, 2026-10-29): maker logins (Ormco included; its TPS-program agreement ends with 2026 and is ignored) are stored encrypted in the region, auxiliary profiles preferred, every reveal logged.
- Decision (user, 2026-10-29): downloading records to a staff computer outside the region is an accepted gap for now; a remote desktop in the region comes later.
- Decision (user, 2026-10-29): currencies EUR (EU), AED (UAE), USD for every other country; prices set in the admin site.
- Decision (user, 2026-10-29): subscriptions are charged with VAT at a configurable rate that can be 0.
- Decision (user, 2026-10-29): new case stage "pending appointment"; automatic messages; automatic pre-assignment confirmed within 24 h; calls tracked with a warning; Zoom on a shared account pool with generic titles; admin password recovery by SSH command; chatbot in phase 2 without patient data.
- Decision (user, 2026-10-29): EU first, then UAE quickly. Scope of the first release confirmed: epics 1–15; wholesale later.
- Decision (user, 2026-10-29): epics 1–14 finish on staging; production checks are run by epic 15 at the EU go-live, as in part 1.
- Decision (user, 2026-10-29): the shared decisions are settled in an architecture document (bmad-architecture) before epic 1: REGION profile, data placement, ids and patient code, interface contract, money and VAT, job runner, object storage, code shared with the admin site, admin UI template.
- Decision (user, 2026-10-29): automatic messages go by email with SMS as fallback in the EU release.
- Decision (user, 2026-10-29): the admin site runs on its own instance in production.
- Parked (user, 2026-10-29): wholesale; 5-case reward vouchers (25% of the average for Advanced, 30% for Premium); education (phase 2 in the source); case-support chatbot; WhatsApp plugin; Zoom API and its account pool; admin system area beyond access (log charts and tables, Sentry/Uptime import, performance reports, legal-document management); Tabby/Tamara.
- Assumption: a doctor's (customer's) personal data may be stored centrally under the intragroup agreement; patient data never.
- Assumption: prices are fixed per currency, converted once from EUR at the reference rate when a product is cloned.
- Open question: subscription terms (monthly commitment, cancellation, upgrade proration, expiry with running cases); epic-subscriptions settles them at inception.
- Open question: cancellation and refund conditions; epic-sales-finance settles them at inception.
- Open question: VAT treatment of the subscription and the exemption texts; tax advisor; rates are configuration.
- Source conflict: The Admin Site, Clients — "Legal acceptances (… Ormco contract …)" vs the user's 2026-10-29 decision to ignore the Ormco agreement; no Ormco-contract field.
- Source conflict: Purchasing workflow and Sale Order details — patient names on sale orders vs the lawyer's rule; names only on the doctor's own copy and in Clinical.
