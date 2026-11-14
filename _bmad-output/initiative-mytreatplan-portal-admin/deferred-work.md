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
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-regions/story-region-suggestion-bar-plan.md`
  summary: Fix `accounts/tests/test_remembered_device.py::test_ticked_right_code_logs_in_and_sets_the_mark`: it asserts `str(doctor.pk) not in cookie.key`, but the cookie name is a hex digest, so a pk of 1 (SQLite) fails whenever the digest holds a "1" (it does with the CI SECRET_KEY); check that the name is not built from the pk some other way (e.g. compare with the expected digest, or use a pk with non-hex digits).
  evidence: fails identically on the untouched baseline in the 2.5 worktree with the CI environment on SQLite (`mtp_device_a55e6b7f73af1f96ea289524`); passes on CI's PostgreSQL, where the pk is not 1.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-admin-access/story-refactor-sweep-plan.md`
  summary: Status (3.13, 2026-11-05) of "recognise the verification provider by its class": done on the admin site (mytp-admin `staff/checks.py:34-46`); the portal side remains (`signup/checks.py:14`, `mytp_publicsite/checks.py:137`).
  evidence: read in both repos during the epic-admin-access refactor sweep; the portal change needs its own story (checks plus tests).
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-admin-access/story-refactor-sweep-plan.md`
  summary: Status (3.13, 2026-11-05) of the browser tests for the htmx list behaviour (3.7): still open; mytp-admin has no browser-test setup.
  evidence: needs Playwright infrastructure in mytp-admin (new behaviour, out of a refactor sweep).
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-admin-access/story-refactor-sweep-plan.md`
  summary: Status (3.13, 2026-11-05) of the mytp-core `fresh_code` wording (3.5): still open; fix it in epic-admin-interface entry 3 (the admin's next mytp-core vendoring), then remove the workaround comment at mytp-admin `staff/signin.py:121-125`.
  evidence: user decision 2026-11-05 in the 3.13 plan.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-admin-access/story-refactor-sweep-plan.md`
  summary: Status (3.13, 2026-11-05) of the separate Twilio Verify service for the fresh-code checks (3.5): still open, before epic-clinical-ops' patient-record download relies on it.
  evidence: needs the user to create the Verify service, then a second SID setting and the provider wiring.
- source_plan: `epic-platform/story-regional-object-storage-service-plan.md`
  summary: `StoredObject.owner` uses PROTECT, so once a real purpose sets an owner, `accounts.erasure.purge_erased` must delete (or detach) that user's objects first, or one owner blocks its whole purge batch.
  evidence: 1.11 review (2026-11-06): `purge_erased` deletes users in batches of 500 and clears only `LegalChoice`; a ProtectedError rolls the batch back on every run.
- source_plan: `epic-catalogue/story-products-add-ons-quantities-calls-and-costs-plan.md`
  summary: Make the portal re-apply the current snapshot whenever its appliers change, without relying on a migration that clears the applied version.
  evidence: Entries 5 and 6 clear `ConfigState.version` in a migration; the previous release can pull between `migrate` and the switch (the `pullconfig` timer runs the previous release), or after a failed deploy that is retried, and record the current version without the new parts, after which the new release answers `unchanged` until the next admin write (review finding 7 of entry 6). A stored applier revision next to the applied version would close it.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-catalogue/story-refactor-sweep-plan.md`
  summary: Status (5.15, 2026-11-14) of "re-apply the current snapshot whenever the appliers change" (epic-catalogue entry 6, review finding 7): closed by `plan-reapply-snapshot-after-deploy.md` (status done).
  evidence: that plan's Implementation Notes say it closes this entry; the entry above stays as written (append-only).
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-catalogue/story-refactor-sweep-plan.md`
  summary: Status (5.15, 2026-11-14) of the banned-country re-check at signup (epic-catalogue entry 3's follow-up: a pending registration that passed step 1 before its country was banned could still finish): closed by `plan-signup-banned-country-recheck.md` (status done).
  evidence: recorded as a follow-up in `epic-catalogue/story-country-groups-and-country-map-editing-plan.md`, not in this file; `signup/views.py::_create_account` re-reads the country map before the account is created.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-catalogue/story-refactor-sweep-plan.md`
  summary: Recorded and left (user decision 2026-11-14): mytp-admin's copied audited-write and view helpers across catalogue, billing and providers (each service's write-audit-rebuild steps; `catalogue.views`' `StatusColumn`, `EditColumn`, `RegionColumn`, `StyledForm`, `RegionFormMixin`, `PlainTextField` and `add_errors`, imported by billing, providers, vouchers and country_map; `catalogue.service`'s `region_of` and `plain_text`, imported by billing and vouchers). Only `_fixed_region` became public (`fixed_region`) in 5.15; `country_map.service` still imports the private `catalogue.service._lock_groups`.
  evidence: 5.15 sweep read of mytp-admin; a shared home (a common app or module) is a structural change, out of a behaviour-neutral sweep.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-catalogue/story-refactor-sweep-plan.md`
  summary: Recorded and left, one entry, no ticket now (user decision 2026-11-14): the mytp-core contract cleanup: drop the two former TPS discount fields (`tps_discount_percent`, `tps_in_house_discount_percent`, superseded by a subscription's `discounts` map), drop the legacy `tps` / `other` category reading, fix the stale `money.py` docstring, and give the portal core's voucher checks instead of its own copy (`catalogue/resolver.py::voucher_state`).
  evidence: each needs a mytp-core release and a contract change on both sites; 5.15 changed no core file.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-catalogue/story-refactor-sweep-plan.md`
  summary: Recorded and left: the portal's `catalogue/management/commands/price_catalogue.py` is 627 lines; split it (by mode) when an entry next extends it.
  evidence: 5.15 plan's Never list (no split in the sweep).
