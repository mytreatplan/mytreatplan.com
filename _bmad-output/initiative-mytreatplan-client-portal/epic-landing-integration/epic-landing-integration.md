---
type: epic
title: "Public site and platform baseline"
parent: initiative-mytreatplan-client-portal
covers: [I1, I2, I5]
after: []
assignee: ""
risk: medium
---

# Public site and platform baseline

## Description

The mytreatplan.ae public site moves into this Django project in English and Spanish. It runs on a base layout and on a production platform that the signup and login epics deploy onto. This epic is the platform baseline.

## Outcome

Visitors read the Mytreatplan public site in English or Spanish and can contact the company; later epics deploy onto staging and production without platform work of their own.

## Requirements

Planned at this epic's inception, from initiative I1, I2 and I5.

## Done when

1. Home, What we do, About us, Who we are and Where we are render with the mytreatplan.ae content in English and Spanish, with a language switcher. The menu links Sign up and Login use URL names `signup` and `login`.
2. The contact form delivers a message to the company and rejects spam.
3. Every page uses one base template with Tailwind CSS, HTMX and the MyTreatPlan design-system tokens.
4. pytest-django and CI run on every push. Staging and production exist with secrets management, a cron host, monitoring, backups, disk encryption, TLS to the database and transactional email. The hosting provider and its KMS are decided.
5. The public site is live in production.

## Boundaries

The public site and the platform. Not the Sign up and Login pages (epics 2 and 3), not portal features.

## References

- parent — ../initiative-mytreatplan-client-portal.md, Requirements I1, I2, I5
- design — MyTreatPlan design system, https://claude.ai/artifact/N6SvdLiD4asJTiZJakaqB3
- source — mytreatplan.ae landing files (to be provided)
- constraint — ../epic-client-portal-signup/spec-client-portal-signup/encryption-policy.md, rows Storage and Transport

## Notes

- Unknown: hosting provider. Epic 2 entry 12 (KMS) waits on it.
- Decision (2026-10-08): the contact form, EN/ES public site, operations and hosting choice belong to this epic.
