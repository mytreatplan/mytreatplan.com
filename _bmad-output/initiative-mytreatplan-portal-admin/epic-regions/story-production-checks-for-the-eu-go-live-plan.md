---
title: 'Production checks for the EU go-live (epic-regions)'
type: 'chore'
ticket: '8'
created: '2026-11-19'
status: 'done'
baseline_revision: '56b4870edeaf945aa0b71e5d0388199fd497bdde'
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

**Problem:** epic-eu-golive's Done when 3 has no production checks for epic-regions: the .es/.ie 301, the region suggestion bar on mytreatplan.com, the region company and legal texts, and the home region recorded on a real signup (with the other-region warning).

**Approach:** Add a sub-list "epic-regions (entry 8)" under Done when 3 of `epic-eu-golive/epic-eu-golive.md`, after epic-doctor-profile's, in the same form (header, Before, lettered checks from 3ai, After), one runnable check per epic-regions Done when item (DW1–DW5). Add one Decision line to epic-eu-golive's Notes and one Notes line to epic-regions. Docs only.

**Decisions (user, 2026-11-19):**
- The home-region check uses the test doctor of 3k and runs before its erasure (3x's erasure wording waits for it too, as 6.10 did).
- The suggestion bar is checked in a browser on a VPN exit in AE or SA (bar shown, "Stay here" remembered across pages and reloads, no bar from Spain), plus a shell fallback on portal-eu: `ip_country('<GCC ip>')` and the test client with that IP as the last X-Forwarded-For entry and a browser user agent.
- The legal-text check expects the final .com texts: no "Dubai", "UAE" or "mytreatplan.ae" in privacy or terms (EN and ES), no provisional notice, current `LegalDocument` rows `provisional=False`; Before names the lawyer's texts (`LEGAL_TEXTS_FINAL` True) as a prerequisite.
- A separate check covers mytreatplan.es, www.mytreatplan.es, mytreatplan.ie and www.mytreatplan.ie: 301 to `https://mytreatplan.com` with path and query kept, http answered in one hop, a final 200; it refers to 3g for the apex .es line.
- One curl line for www.mytreatplan.com, expected a 301 to `https://mytreatplan.com/` (default recorded; the user did not state the answer). The nginx template does not do this yet: a deferred-work entry.
- The .es/.ie checks run after 1.10's DNS switch for those names.
- Production portal-eu only; the reverse half of Done when 1 and the AE footer belong to epic-uae-launch.
- Done when 2: the warning is part of the signup check (country SA shows the .ae link, do not continue); 3s covers the banned side.
- The story lands as one docs commit on master with this plan inside, pushed directly, no PR.

</frozen-after-approval>

## Code Map

- `epic-eu-golive/epic-eu-golive.md`: Done when 3 sub-lists per epic; epic-doctor-profile's (6.10) is the last, at ~line 66, checks up to 3ah; Notes' last Decision line ~97. Next label 3ai. 3x holds the test doctor's erasure ("once … are done with the account") and must also wait for the new signup check.
- `epic-regions/epic-regions.md`: Done when 1–5; Notes (2026-11-02: .es and .ie redirect, EXTRA_DOMAINS has all four names).
- Facts for the checks (read, not changed): `deploy/nginx/site.conf.template` (extras block `return 301 https://mytreatplan.com$request_uri`, only when provision.sh gets EXTRA_DOMAINS); `regions/suggestion.py` (cookie `mytp_stay_region`, bot UA regex includes curl, last X-Forwarded-For entry, GeoIP file at GEOIP_DB_PATH from the monthly `geoipupdate` job); `templates/base.html` footer (MyTPDSO Limited · Company number 815526 (Ireland), Dublin office); `legal.models.LegalDocument` (region, kind, version, provisional); `accounts.User.home_region` (read-only in the Django admin, section "Region").
- Model plan: `epic-doctor-profile/story-production-checks-for-the-eu-go-live-plan.md` (6.10).

## Implementation Notes

Oneshot: one sub-list, two sentences in 3x and the admin-interface clean-up, two Notes lines and one deferred-work entry; no code.

- `epic-eu-golive/epic-eu-golive.md`: sub-list "epic-regions (entry 8)" after epic-doctor-profile's (header, Before, 3ai–3al, After); 3x's erasure and the admin-interface clean-up line now also wait for 3al; one Decision line in Notes. `epic-regions/epic-regions.md`: one Notes line. `deferred-work.md`: the www.mytreatplan.com redirect.
- Facts read in the portal code at d369d40 (story/7.2 branch; `regions/`, `legal/`, `deploy/nginx` unchanged since 56b4870); nothing run in production.
- The signup warning in 3al is seen on a new signup page (the test doctor signed up earlier, in 3k), not during the test doctor's own signup.

## Review Triage Log

Quick lens (Sonnet), one pass (2026-11-19): 7 findings (1 a confirmation, no defect); 5 patched, 1 false.

| # | Finding | Verdict | Action |
|---|---------|---------|--------|
| 1 | 3ai's www.mytreatplan.com line fails: no certificate name, DNS or server block for it; Before does not order the deferred work | medium | patched: Before names the www redirect (DNS, certificate, 80/443 block) as a prerequisite; the deferred-work entry lists all three |
| 2 | 3aj's fallback lacks the test-client half of the decision (bar rendered, "Stay here" cookie, curl excluded) | medium | patched: a test-client shell line printing `200 True True`, `302 True`, `False`, `False` (`stay` is an `HttpResponseRedirect`) |
| 3 | 3al does not say where the SA note's link goes | low | patched: `https://mytreatplan.ae/en/signup/` (`signup_url = site_url + SIGNUP_PATH`), marker `data-country-signup-link` |
| 4 | 3al's banned refusal text not given | low | patched: "We can't offer MyTreatPlan to doctors in this country." (`signup/forms.py` `COUNTRY_BANNED_MESSAGE`) |
| 5 | 3al attributes the signup to 3k, which is storage only | low | patched: the temporary test doctor of epic-platform's Before (made for 3k) |
| 6 | Implementation Notes claim an admin-interface clean-up change the diff lacks | false | line 65 is epic-admin-interface's "After all checks" line, and the diff changes it |
| 7 | 3ak: the Spanish legal bodies do not exist yet (fallback to English) | low | patched: Before requires the final texts in English and Spanish |
