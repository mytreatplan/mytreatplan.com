- source_plan: `epic-platform/story-site-aware-deploy-tooling-plan.md`
  summary: The browser test `signup/tests/browser/test_failure_policies.py::test_row5_email_in_a_pending_registration` is flaky in CI (page URL not yet /en/signup/legal/ when asserted).
  evidence: Failed in PR #49's CI run 41034239589 and passed on re-run with no code change; unrelated to the diff (deploy tooling only).

- source_plan: `epic-platform/story-mytp-core-package-with-field-encryption-and-kms-plan.md`
  summary: CI should fail fast with a clear message when the host's /usr/local/bin/mytp-release differs from the repo's deploy/release.sh, instead of failing later in the release.
  evidence: 1.4's release.sh change reached the server only after provision was re-run; the first deploy failed at pip install (run 41046629920), as documented in 1.1's README note on release-command updates.

- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-admin-access/story-verification-provider-and-codes-in-mytp-core-plan.md`
  summary: Make the portal's provider checks (signup E001 Twilio credentials, mytp_publicsite E006 fake provider in deploy) recognise the provider by class, so the mytp-core 0.4.0 paths (`mytp_core.verification.FakeVerificationProvider`, `mytp_core.twilio_verify.TwilioVerifyProvider`) are checked too; best done with entry 4, when the admin site needs the same checks (possibly moved into mytp-core).
  evidence: Both checks compare `settings.VERIFICATION_PROVIDER` as a string against the `signup.*` paths; the core README presents the core paths as valid values. Nothing uses the core paths today (low); the fix needs check changes and new tests, which 3.2's "no test file changes" rule excludes.
