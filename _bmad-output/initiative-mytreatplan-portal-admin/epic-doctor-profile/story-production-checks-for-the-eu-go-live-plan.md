---
title: 'Production checks for the EU go-live (epic-doctor-profile)'
type: 'chore'
ticket: '10'
created: '2026-11-18'
status: 'done'
baseline_revision: 'a58885ef0e2d72ae6b26eeb7ccffe045d02ab289'
route: 'oneshot'
route_source: 'auto'
risk: 'low'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** epic-eu-golive's Done when 3 has no production checks for epic-doctor-profile: the doctor home and personal data, code-confirmed changes, the photo, fiscal entities with VIES, shipping addresses, maker logins and the client record on the admin site.

**Approach:** Add a sub-list "epic-doctor-profile (entry 10)" under Done when 3 of `epic-eu-golive/epic-eu-golive.md`, after epic-admin-interface's, in the same form (header, Before, lettered checks, clean-up), one runnable check per Done when item of epic-doctor-profile (1–4) plus the code-confirmed changes, the photo and the client record. Add one Decision line to epic-eu-golive's Notes and one Notes line to epic-doctor-profile. Docs only.

**Decisions (user, 2026-11-18):**
- The checks use the temporary test doctor of 3k and run before its erasure, the last step of 3x (3x's wording is extended to wait for these checks too).
- VIES is checked live with MyTPDSO Limited's own Irish VAT number once registered; until then SAP SE's public number DE143454214. The entity is deleted afterwards.
- Mobile and email changes are checked with real sends to a mobile and an inbox the operator controls (one or two Twilio sends), then set back. The fake verification provider is refused in production (`mytp_publicsite.E006`).
- The photo check uploads a small test JPEG, checks the scan and the UUID object, then deletes it.
- The maker-login check stores a made-up login (never a real maker account), checks it is never shown and is ciphertext in the database, then deletes it.
- Done when 1 (Annex I mockups, EN and ES, desktop and phone) is a visual check by the user, recorded like the others.
- Labels continue after 3z as 3aa, 3ab, …
- The story lands as one docs commit on master with this plan inside, pushed directly, no PR; nothing to deploy or check on staging.

</frozen-after-approval>

## Implementation Notes

Oneshot: one sub-list, one sentence in 3x, two Notes lines in planning documents, under 100 lines, no code.

- `epic-eu-golive/epic-eu-golive.md`: sub-list "epic-doctor-profile (entry 10)" after epic-admin-interface's (header, Before, 3aa–3ah, clean-up); 3x's erasure and the admin-interface clean-up line now wait for 3aa–3ah; one Decision line in Notes. `epic-doctor-profile/epic-doctor-profile.md`: one Notes line. One deferred-work entry (scan-worker log level).
- Facts read in the code at portal a58885e and mytp-admin c6132c9; nothing run in production.
- Deviation from the decision "then set back": the contact email cannot be removed or set to the login email (`EmailChangeForm`, `alternative_is_login`), so only the mobile is set back; the contact email stays on the second inbox until the erasure of 3x (told the user).

## Review Triage Log

Quick lens, one pass (2026-11-18): 7 findings, 0 high, 2 medium, 3 low, 1 maybe-false, 1 false; 6 patched, 1 also deferred.

| # | Finding | Verdict | Action |
|---|---------|---------|--------|
| 1 | 3ae: ES NIF `12345678Z` is sent to VIES (ES is EU-27), so "no VIES line" was wrong; `retry_vies` may change a pending one | medium | patched: expects "Not found in VIES" (or pending); retry changes only pending |
| 2 | 3ae: `DE000000000` is refused offline as not well formed, no VIES call | low | patched: wording says so |
| 3 | 3ab/clean-up: the contact email cannot be set back (required, not the login email) | medium | patched: stays until the erasure; told the user |
| 4 | 3ac: the clean-scan INFO line is probably dropped (no `storage` logger) | maybe-false | patched: check only that no file name appears; deferred entry to verify on dev |
| 5 | admin-interface clean-up line could erase the doctor before 3aa–3ah | low | patched |
| 6 | 3ah: "no maker login and no photo" stated without a command | low | patched: field-name shell check |
| 7 | baseline `a58885e0` not a prefix | false | the file already holds the full hash `a58885ef…` |
