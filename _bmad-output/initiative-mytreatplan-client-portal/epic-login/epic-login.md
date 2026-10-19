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
- Waits on epic 1 because: password reset sends email through epic 1's transactional email.
