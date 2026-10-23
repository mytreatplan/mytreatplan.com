---
id: 12
type: story
title: "Production environment and promotion"
parent: epic-landing-integration
covers: [E6]
after: [7, 9, 13, 14, 15]
hitl: true
risk: high
---

# Production environment and promotion

## Description

Provisions production on its own OVHcloud GRA11 project with entry 13's setup (Managed PostgreSQL over TLS, HTTPS, secrets, Gunicorn/systemd/Nginx, no basic auth, indexable), the portal switch off until epic 3, entry 9's timers and monitoring, entry 14's backups and entry 15's KMS applied to production, and a manual-approval GitHub Actions job that promotes the exact build already deployed to staging.

## Acceptance Criteria

Verify: A manual approval deploys the staging build to production over HTTPS; the DB connection uses TLS; the portal switch is off; backups, monitoring and cron run on production.

## References

- parent — _bmad-output/initiative-mytreatplan-client-portal/epic-landing-integration/epic-landing-integration.md
- qa — _bmad-output/initiative-mytreatplan-client-portal/qa-spec-vs-code-2026-10-23.md, section C
- runbook — deploy/README.md

## Notes

Production checklist from the specs-vs-code QA, part C (user, 2026-10-23). Each item is part of this story's work.

- Open question (C1): nightly backups keep signup data and pre-anonymization log values up to 30 days after the app purges them. The backup retention policy is written with the lawyer and ops (signup retrospective S4) and applied to production's backups.
- Open question (C2): Nginx and journald access logs keep client IPs under OS defaults. Set and document their retention on production, in line with the C1 policy.
- Decision (C3): production disk encryption at rest must be confirmed with OVHcloud or added; staging runs without it (epic Notes, 2026-10-11).
- Decision (C4): the KMS master key can't be rotated yet (deferred-work.md), and rotating the blind-index key breaks email and phone lookups until `reencrypt_fields` finishes. Document both for production, and make `reencrypt_fields` work in batches before the first production rotation (signup retrospective R5).
- Decision (C5): repeat the manual setup on production: the Sentry "failed sends" alert, Twilio Fraud Guard, and `TWILIO_WHATSAPP_ENABLED` (off until Meta approves the sender), per deploy/README.md.
- Risk check (high): the user confirms each checklist item on production before go-live (1.10, 3.13).
