- source_plan: `epic-landing-integration/story-staging-environment-and-deploys-plan.md`
  summary: Staging provisioning, Nginx/systemd config, runbook and the GitHub Actions staging deploy, split off as ticket 1.13.
  evidence: The story 1.8 plan was ~2,400 tokens; the user chose to split it at the size gate (2026-10-11).
- source_plan: `_bmad-output/initiative-mytreatplan-client-portal/epic-landing-integration/story-off-region-database-backups-and-restore-test-plan.md`
  summary: Protect off-region dumps from being overwritten by the server's key (bucket versioning with noncurrent-version expiry, or object lock), decided for production in 1.12.
  evidence: The server's S3 user has `s3:PutObject` without delete; with no versioning or object lock it can still overwrite an existing dump (review 1.14, finding 2).
- source_plan: `_bmad-output/initiative-mytreatplan-client-portal/epic-client-portal-signup/story-legal-acceptance-and-commercial-consent-plan.md`
  summary: Account erasure from the admin (e.g. on the user's request). Proposed by the user (2026-10-15): a logical deletion that deactivates the account, keeps only what the legal proof needs (encrypted email and name, the legal choices) for a retention period set by the lawyer (`ACCOUNT_ERASURE_RETENTION_DAYS`, required for `check --deploy`), clears the blind indexes and the other personal fields, withdraws commercial consent, and a daily job physically deletes past the period. Open for the lawyer: the period, what is kept, the privacy policy text. To build as a fix story after 2.12, before 2.11.
  evidence: Found on staging after 2.12: a user cannot be deleted in the admin because `LegalChoice.user` is `on_delete=PROTECT` (story 2.6, deliberate as evidence) and no story covers erasure. Disabling works today through the admin's Active flag.
- source_plan: `_bmad-output/initiative-mytreatplan-client-portal/epic-client-portal-signup/story-kms-key-provider-plan.md`
  summary: KMS service key rotation for field encryption: let `accounts.crypto.KMSKeyProvider` unwrap data keys wrapped by more than one service key (an old and a new `KMS_SERVICE_KEY_ID`), with a documented procedure to rewrap `FIELD_KMS_KEYS` and `BLIND_INDEX_KMS_KEY` under the new service key and retire the old one.
  evidence: The 2.12 provider supports data key rotation only; one `KMS_SERVICE_KEY_ID` unwraps every key, and `deploy/README.md` leaves service key rotation to a follow-up story (story 2.12 plan, Implementation Notes). Recorded by the refactor sweep (ticket 13, 2026-10-18).
- source_plan: `_bmad-output/initiative-mytreatplan-client-portal/epic-login/story-second-factor-at-login-plan.md`
  summary: The admin site's login (`/admin/`, Django's own login form) has no second factor and no CAPTCHA. For the admin epics (decided by the user, 2026-10-19): add both there.
  evidence: Story 3.2 adds the Twilio Verify code to the portal login only; decision 4 of its plan keeps the admin site out of scope. Staff accounts log in to `/admin/` with a password alone.
- source_plan: `_bmad-output/initiative-mytreatplan-client-portal/qa-spec-vs-code-2026-10-23.md`
  summary: The admin site's login (Django's own, `/admin/`) has no login lockout and leaves no login or failure records, besides its missing second factor and CAPTCHA (earlier entry); for the admin epics.
  evidence: QA A3 (2026-10-23), the user chose to defer it: `mytreatplan/urls.py:27` routes Django's admin login, which bypasses accounts.login_guard (records, lockout) as well as the portal's second factor and CAPTCHA.

