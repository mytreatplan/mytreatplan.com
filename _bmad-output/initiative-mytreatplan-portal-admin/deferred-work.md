- source_plan: `epic-platform/story-site-aware-deploy-tooling-plan.md`
  summary: The browser test `signup/tests/browser/test_failure_policies.py::test_row5_email_in_a_pending_registration` is flaky in CI (page URL not yet /en/signup/legal/ when asserted).
  evidence: Failed in PR #49's CI run 41034239589 and passed on re-run with no code change; unrelated to the diff (deploy tooling only).

- source_plan: `epic-platform/story-mytp-core-package-with-field-encryption-and-kms-plan.md`
  summary: CI should fail fast with a clear message when the host's /usr/local/bin/mytp-release differs from the repo's deploy/release.sh, instead of failing later in the release.
  evidence: 1.4's release.sh change reached the server only after provision was re-run; the first deploy failed at pip install (run 41046629920), as documented in 1.1's README note on release-command updates.

- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-admin-access/story-verification-provider-and-codes-in-mytp-core-plan.md`
  summary: Make the portal's provider checks (signup E001 Twilio credentials, mytp_publicsite E006 fake provider in deploy) recognise the provider by class, so the mytp-core 0.4.0 paths (`mytp_core.verification.FakeVerificationProvider`, `mytp_core.twilio_verify.TwilioVerifyProvider`) are checked too; best done with entry 4, when the admin site needs the same checks (possibly moved into mytp-core).
  evidence: Both checks compare `settings.VERIFICATION_PROVIDER` as a string against the `signup.*` paths; the core README presents the core paths as valid values. Nothing uses the core paths today (low); the fix needs check changes and new tests, which 3.2's "no test file changes" rule excludes.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-admin-access/story-list-conventions-plan.md`
  summary: Add browser tests (Playwright/live_server, like the portal's) to mytp-admin so the htmx list behaviour (confirmation modal Cancel/Confirm, sort and pager POSTs keeping the search, the address bar never carrying a term) is exercised automatically, not only by markup asserts and the manual admindev check.
  evidence: 3.7 review: mytp-admin has no browser test setup; the implementer verified the flow once with a manual Chromium run. Low; the setup is new test infrastructure, outside 3.7's scope.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-admin-access/story-fresh-code-check-for-sensitive-actions-plan.md`
  summary: Fix the mytp-core `policy.py` wording of `fresh_code` (the module docstring's "a fresh code at every sign-in" and the field comment): the flag turns on the fresh code of sensitive actions (mytp-admin's `staff.stepup`), not a code at every sign-in. Next core release.
  evidence: mytp-core 0.5.0 `policy.py` (ADMIN docstring and `fresh_code` comment); entry 5 builds the behaviour in mytp-admin only (no core release now, per its plan).
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-admin-access/story-fresh-code-check-for-sensitive-actions-plan.md`
  summary: Give the admin step-up (fresh-code) checks their own Twilio Verify service, so a code issued for sign-in can never confirm a sensitive action on Twilio (today one Verify service serves every purpose and shares its pending verification), before the patient-record download (epic-clinical-ops) relies on it.
  evidence: 3.5 review: mytp_core.twilio_verify posts only To and Code; purpose separation holds only in the fake provider. Practical exposure is small (a signed-in user's sign-in code is already used), but the separation is not guaranteed. Needs a second Verify SID setting and provider wiring (user creates the service).
