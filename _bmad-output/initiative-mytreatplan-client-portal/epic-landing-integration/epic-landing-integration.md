---
type: epic
title: "Public site and platform baseline"
parent: initiative-mytreatplan-client-portal
covers: [I1, I2, I5]
after: []
assignee: ""
risk: high
---

# Public site and platform baseline

## Description

The mytreatplan.ae public site is rebuilt inside this Django project in English and Spanish and goes live on mytreatplan.com. It runs on a base layout and on a production platform that the signup and login epics deploy onto. This epic is the platform baseline.

## Outcome

Visitors read the Mytreatplan public site on mytreatplan.com in English or Spanish and can contact the company; later epics deploy onto staging and production without platform work of their own.

## Requirements

- E1 (I1): The one-page public site (Home, What we do, About us, Who we are, Where we are, Contact) renders in Django matching mytreatplan.ae, rebuilt in Tailwind with MyTreatPlan design tokens, with the burger menu and responsive images.
- E2 (I1): English and Spanish with a language switcher. Spanish is drafted by Claude and reviewed by the team.
- E3 (I1): Privacy and Terms pages, sitemap, robots, Open Graph and JSON-LD.
- E4 (I1): Menu links Sign up and Login use URL names `signup` and `login`.
- E5 (I2): Contact form with the existing fields and anti-spam (honeypot, JS check, minimum fill time, per-sender rate limit). Messages are stored, visible in admin, emailed to an inbox through SendGrid, and forwarded to the CRM.
- E6 (I5): pytest-django and GitHub Actions CI. Staging and production on the chosen host with managed PostgreSQL over TLS, disk encryption, HTTPS, secrets management, a cron host, off-region backups, monitoring, a KMS and SendGrid transactional email. Public site live in production.

## Done when

1. Home, What we do, About us, Who we are and Where we are render with the mytreatplan.ae content in English and Spanish, with a language switcher. The menu links Sign up and Login use URL names `signup` and `login`.
2. The contact form delivers a message to the company inbox and the CRM, and rejects spam.
3. Every page uses one base template with Tailwind CSS, HTMX and the MyTreatPlan design-system tokens.
4. pytest-django and CI run on every push. Staging and production exist with managed PostgreSQL over TLS, HTTPS, secrets management, a cron host, monitoring, off-region backups, disk encryption and transactional email. The hosting provider and its KMS are decided.
5. The public site is live in production on mytreatplan.com.

## Boundaries

The public site and the platform. Not the Sign up and Login pages (epics 2 and 3), not portal features. mytreatplan.ae stays on its current hosting, unchanged.

## References

- parent — ../initiative-mytreatplan-client-portal.md, Requirements I1, I2, I5
- source — GitHub repo mytreatplan/uae_landing (git@github.com:mytreatplan/uae_landing.git), commit 1a8f27e: built Astro output of mytreatplan.ae. index.html holds the sections, inline CSS and JS; capas/ holds the images; _astro/ holds the Inter fonts; privacy/ and terms/ hold the legal pages; enviar.php is the hidden contact form handler (fields and anti-spam rules)
- design — MyTreatPlan design system, https://claude.ai/artifact/N6SvdLiD4asJTiZJakaqB3
- constraint — ../epic-client-portal-signup/spec-client-portal-signup/encryption-policy.md, rows Storage, Transport and Keys

## Notes

- Tracer bullet: entry 1. It runs the base layout, Home section and CI. It stops short of a deploy because hosting is undecided; deploys arrive with entry 8.
- Decision (2026-10-08): CI is GitHub Actions; transactional email is SendGrid, owned by marketing together with Twilio.
- Decision (2026-10-08): contact messages go to an inbox and are forwarded to a CRM. Zoho is likely; the choice is pending.
- Decision (2026-10-08): public pages are rebuilt in Tailwind with design-system tokens, not the old CSS.
- Decision (2026-10-08): Spanish is drafted by Claude and reviewed by the team. The contact form keeps the existing fields. There is no Astro source, so the port uses the built site.
- Decision (2026-10-08): mytreatplan.ae stays on its current hosting; the new project serves only mytreatplan.com.
- Decision (2026-10-08): entry 1 creates a minimal custom user model before the first migration, and signup entry 2.1 extends it. The dev database may be reset if it already has Django's default tables (user's approval).
- Decision (2026-10-08): signup and login routes live inside i18n_patterns. Entry 8's portal switch is on in staging and off in production until epic 3 ships.
- Recommendation for entry 7 (Claude, 2026-10-08): OVHcloud with managed PostgreSQL and OVHcloud KMS (same provider as dev; route to HDS-certified health-data hosting). Scaleway (managed PostgreSQL plus Key Manager) is the cleanest alternative. UpCloud only works with its managed PostgreSQL and a separate KMS. Never self-run production PostgreSQL on the app VPS. The team's candidates are UpCloud and OVH; current dev runs on an OVH dedicated server.
- Found (2026-10-08): mytreatplan/settings.py and settings.env are gitignored; settings.py reads all secrets from settings.env and holds one commented-out old SECRET_KEY. __pycache__ files and static_root/ are tracked. Entry 1 fixes all three.
- Unknown: hosting provider (entry 7), SendGrid credentials and inbox (entry 5), CRM choice (entry 6).
