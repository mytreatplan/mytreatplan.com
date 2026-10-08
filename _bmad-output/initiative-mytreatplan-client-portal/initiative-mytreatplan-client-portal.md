---
type: initiative
title: Mytreatplan Client Portal
parent: none
covers: [I1, I2, I3, I4, I5]
after: []
assignee: ""
risk: high
---

# Mytreatplan Client Portal

## Description

mytreatplan.com is the client portal of the Mytreatplan brand. Doctors (individuals or company representatives, never patients) sign up, verify their contact data, accept the legal terms, and later order treatment-planning services, upload patient data, follow their services, and get support. This initiative delivers the first part: the public site on a production platform, Doctor signup, and login.

## Outcome

Doctors find Mytreatplan on its public site, register themselves verified, and log in to the portal. The signal is the signup spec's Success signal plus a successful first login.

## Requirements

Source: the user's concept text (chat, 2026-10-08).

- I1: Public site with Home, What we do, About us, Who we are, Where we are, Contact, and menu links to Sign up and Login, integrating the mytreatplan.ae content and design, in English and Spanish. The Sign up and Login pages belong to I3 and I4.
- I2: Contact form.
- I3: Doctor signup wizard. Spec: `epic-client-portal-signup/spec-client-portal-signup/`, CAP-1 to CAP-11 and CAP-13.
- I4: Login with forgot-password, and logging of logins and browsing in the registered area (signup spec non-goal, former CAP-12).
- I5: Platform: environments, CI, deployment and operations.

## Done when

1. The public site is live in production in English and Spanish, and its contact form works.
2. A new Doctor signs up in production, verifies email and mobile, and lands logged in.
3. That Doctor logs out, logs back in, and can recover a forgotten password.
4. Logins and page visits in the registered area are logged.

## Boundaries

The public site, signup and login. Deferred, not planned: personal/billing data configuration, knowledge base, orders, patient data upload, service status, invoices, support, specialist interaction, and welcome/onboarding content.

- Touch point: welcome/onboarding route. Epic 2 creates the route; its content is a later spec; epic 3 redirects there after login. Owner: epic-client-portal-signup.
- Touch point: Twilio Verify (SendGrid, WhatsApp, SMS), KMS, CAPTCHA provider, Have I Been Pwned. Configured for signup; owner: epic-client-portal-signup.
- Touch point: transactional email for Django (contact form, password reset). Owner: epic-landing-integration.

Tracer path: epic 1's base layout and deploy target → epic 2's signup → epic 3's login, released together.

## References

- spec — epic-client-portal-signup/spec-client-portal-signup/spec-client-portal-signup.md, section Capabilities
- design — MyTreatPlan design system, https://claude.ai/artifact/N6SvdLiD4asJTiZJakaqB3
- source — mytreatplan.ae landing files (to be provided)

## Notes

- Decision (2026-10-08): epics run landing → signup → login; signup waits on the landing epic.
- Decision (2026-10-08): the decisions shared across epics (custom user model, encrypted fields with HMAC blind index, key-provider interface) live in epic 2, entries 1–2, instead of the opening epic; epic 3 waits on them.
- Decision (2026-10-08): URL names `signup` (epic 2) and `login`, `password_reset` (epic 3); epic 1's menu links use them.
- Decision (2026-10-08): epic 3's activity logging reuses epic 2's signup event log service (2.3).
- Decision (2026-10-08): signup and login go to production together; signup stays on staging until epic 3 is live.
- Decision (2026-10-08): the contact form, EN/ES for the public site, operations, and the hosting/KMS choice belong to epic 1.
- Decision (2026-10-08): the frontend uses Django templates, Tailwind CSS and HTMX; scheduled jobs are management commands run by cron; the rate-limit cache is Django's database cache.
