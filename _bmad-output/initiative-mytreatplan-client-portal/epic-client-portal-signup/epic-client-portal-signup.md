---
type: epic
title: "Doctors sign up for the client portal"
parent: initiative-mytreatplan-client-portal
covers: [I3]
after: []
assignee: ""
risk: high
---

# Doctors sign up for the client portal

## Description

Doctors register themselves through a four-step wizard and end verified and logged in. The spec in `spec-client-portal-signup/` is the full contract.

## Outcome

Doctors register themselves verified and logged in; the signal is the spec's Success signal.

## Requirements

The spec `spec-client-portal-signup/spec-client-portal-signup.md` with its companions. CAP-1 to CAP-11 and CAP-13 map to initiative I3. CAP-12 is retired and its scope moved to epic-login.

## Done when

1. On staging a Doctor completes the four steps in English or Spanish, verifies email and mobile, and lands logged in. The end-to-end suite (entry 14) proves it.
2. A registered email or mobile is blocked with a recovery link; a pending one can be resumed only after a code.
3. Personal data in the user, pending-registration and signup-log tables is ciphertext with keys from the KMS; email and phone lookups work through the blind index.
4. An attempt abandoned for 24h leaves no personal data; log personal data is anonymized at 30 days and deleted at 2 years.
5. Every row of `registration-failure-policies.md` has a passing automated test.
6. Goes to production together with epic-login.

## Boundaries

The signup wizard and its data. The spec's non-goals apply. Owns the Twilio Verify, KMS, CAPTCHA and Have I Been Pwned integrations.

## References

- parent — ../initiative-mytreatplan-client-portal.md, Requirements I3
- spec — spec-client-portal-signup/spec-client-portal-signup.md, sections Capabilities and Constraints, plus its companions
- design — MyTreatPlan design system, https://claude.ai/artifact/N6SvdLiD4asJTiZJakaqB3

## Notes

- Tracer bullet: entry 1. It runs one email-and-password signup through the wizard, a fake code and auto-login, before encryption (entry 2) and mobile verification (entry 7).
- Decision (2026-10-08): entry 10 runs beside entries 4–9 after entry 3 and owns the purge/anonymize service that entry 8 reuses.
- Decision (2026-10-08): entry 11 (Twilio) waits only on entry 7 and on marketing's account data; entry 12 (KMS) waits on entry 2 and on epic 1's hosting decision.
- Decision (2026-10-08): 14 entries is above the usual range but they form one lane with one owner, so the epic is not split.
- Decision (2026-10-08): the rate-limit cache is Django's database cache; the end-to-end suite runs with the fake provider, and real delivery is checked by entry 11.
- Decision (2026-10-08): signup stays on staging until epic-login is live.
- Waits on epic 1 because: it needs the base layout, i18n, CI, staging, the cron host and the hosting/KMS decision.
