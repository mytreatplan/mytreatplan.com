---
type: epic
title: "Doctors log in and recover access"
parent: initiative-mytreatplan-client-portal
covers: [I4]
after: []
assignee: ""
risk: medium
---

# Doctors log in and recover access

## Description

Doctors registered by signup log in with email and password and recover a forgotten password. Their logins and browsing in the registered area are logged. This scope moved here from the signup spec (former CAP-12).

## Outcome

A Doctor created by signup logs in and gets back into the portal after forgetting the password; every login and page visit is on record.

## Requirements

Spec: `spec-client-portal-login/` (CAP-1 to CAP-9), written 2026-10-18/19 from initiative I4 and this epic.

## Done when

1. A Doctor created by signup logs in with email, password and a second factor: a Twilio Verify code to the email or the mobile, as the Doctor chooses. The device can be remembered for 90 days.
2. Forgot-password resets the password; the signup recovery link (URL name `password_reset`) opens it.
3. Each login records date/time, IP and browser info; each page request records the page and date/time. The records are kept 5 years.
4. A Doctor has one session at a time: a new login ends the older one, and a session idle for 15 minutes ends.
5. Signup and login are live in production together.

## Boundaries

Authentication and activity logging. Not signup (epic 2), not profile, billing or onboarding content. Login redirects to the welcome route created by epic 2.

## References

- parent — ../initiative-mytreatplan-client-portal.md, Requirements I4
- spec — spec-client-portal-login/spec-client-portal-login.md
- spec — ../epic-client-portal-signup/spec-client-portal-signup/spec-client-portal-signup.md, section Non-goals (former CAP-12)
- constraint — ../epic-client-portal-signup/spec-client-portal-signup/encryption-policy.md, row Uniqueness and lookups

## Notes

- Waits on epic 2 because: login looks users up through 2.2's blind index, logs activity through 2.3's log service, reuses 2.9's rate limits and CAPTCHA, and redirects to 2.1's welcome route.
- Waits on epic 2 also for 2.10's retention jobs and 2.11's Twilio Verify integration.
- Waits on epic 1 because: the go-live entry needs the production environment (1.12).
- Decision (2026-10-19): breakdown of 13 entries. Entry 1 is the tracer bullet (login page to welcome and logout). Entries 1–6 are one lane on the login flow, in order. Entry 9 (email) runs off the lane; 7 and 8 follow the records and sessions they use. Entries 11 → 12 → 13 close the epic.
- Decision (2026-10-19): this epic owns transactional email (entry 9, SendGrid). Landing 1.5 reuses it, so the reset isn't blocked by 1.5's wait for marketing.
- Decision (2026-10-19): the final email provider must be chosen before production; recorded as entry 13's unknown.
- Decision (2026-10-19): wrong passwords and wrong second-factor codes have separate counters. 5 of either locks the account for 10 minutes, and wrong codes are recorded like failed logins, from the second.
