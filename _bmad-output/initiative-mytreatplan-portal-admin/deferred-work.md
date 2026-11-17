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
  status: resolved by epic-doctor-profile entry 4 (6.4, 2026-11-16): `accounts.erasure.purge_erased` detaches every object an account still owns and deletes the ones not marked yet through `storage.service.delete` before the accounts go (`_release_stored_objects`); `accounts/tests/test_profile_photo.py::test_row15_purge_detaches_and_deletes_objects_still_owned`.
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
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-platform/story-refactor-sweep-plan.md`
  summary: Status (1.15, 2026-11-14) of the admin README's `/api/` allowlist sentence (deferred to mytp-admin by `plan-allowlist-fail-closed-and-restore-pg18.md`): closed by mytp-admin commit b453016; its `README.md:943-946` says a run without `API_PEER_IPS` is refused (exit 2) on a site that already has an allowlist and `API_PEER_IPS=none` removes the allowlist on purpose.
  evidence: read in mytp-admin during the epic-platform refactor sweep.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-platform/story-refactor-sweep-plan.md`
  summary: Status (1.15, 2026-11-14) of raising `MIN_PG_MAJOR` in `deploy/restore-db.sh` (left to the user by entry 14's review, patch 7): closed by portal commit e0b6f81 (`plan-allowlist-fail-closed-and-restore-pg18.md`); `deploy/restore-db.sh:39` is `MIN_PG_MAJOR=18`.
  evidence: read in the portal during the epic-platform refactor sweep.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-platform/story-refactor-sweep-plan.md`
  summary: Status (1.15, 2026-11-14) of the flaky browser test `signup/tests/browser/test_failure_policies.py::test_row5_email_in_a_pending_registration` (entry 1): still open.
  evidence: no fix since PR #49; a test fix is outside a cleanup sweep.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-platform/story-refactor-sweep-plan.md`
  summary: Status (1.15, 2026-11-14) of the CI fail-fast when the host's `/usr/local/bin/mytp-release` differs from the repo's `deploy/release.sh` (entry 4): still open.
  evidence: `deploy-staging` has no such check; adding one is a CI step change, excluded by the sweep.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-platform/story-refactor-sweep-plan.md`
  summary: Status (1.15, 2026-11-14) of `StoredObject.owner` PROTECT against `accounts.erasure.purge_erased` (entry 11, review finding 5): still open; due with the first purpose that sets an owner.
  evidence: no purpose sets `owner` yet; a behaviour change, outside the sweep.
  status: resolved by epic-doctor-profile entry 4 (6.4, 2026-11-16), the first purpose that sets an owner (`profile_photo`): see the entry above.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-platform/story-refactor-sweep-plan.md`
  summary: Recorded and left (2026-11-14): run the portal's CI test job in the Playwright container image instead of `python -m playwright install --with-deps chromium` on every run (that step sometimes takes 8-15 minutes on a slow Ubuntu mirror); the database service host changes, Python stays 3.13.
  evidence: portal `.github/workflows/ci.yml:58-59`; a CI infrastructure change, excluded by the sweep.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-platform/story-refactor-sweep-plan.md`
  summary: Recorded and left (2026-11-14): `provision.sh` installs `postgresql-client-18` from the PostgreSQL apt repository (PGDG, entry 14) because admindev's database is PostgreSQL 18 and Debian 13 ships only the 17 client; drop PGDG (the three files the runbook names) once Debian ships the 18 client. mytp-core's CI also installs `postgresql-client-17` from PGDG for its restore e2e tests. mytp-admin's CI runs PostgreSQL 17 while admindev runs 18 (its `ci.yml` comment now says so; the image is unchanged).
  evidence: `deploy/README.md` "PostgreSQL client tools"; `epic-platform/epic-platform.md` entry 14 note; mytp-admin `.github/workflows/ci.yml:16-17`.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-platform/story-refactor-sweep-plan.md`
  summary: Recorded and left (2026-11-14): mytp-admin's `deploy/run-job.sh` is a byte-for-byte copy of the portal's, and admindev is provisioned from a separate copy of the portal's `deploy/` on the host (`~/deploy-admindev`, with the admin's `jobs.conf` and `staging.env.example`); both copies must be refreshed by hand when the portal's change.
  evidence: entry 10's plan (2026-10-31 note); `diff deploy/run-job.sh ../mytp-admin/deploy/run-job.sh` is empty on 2026-11-14; a shared home is a server or mytp-core change, excluded by the sweep.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-platform/story-refactor-sweep-plan.md`
  summary: Recorded and left (2026-11-14): ClamAV on the hosts is Debian 13's 1.4.3 (decision 2026-11-07, entry 12), not the architecture's "1.4.6 or later"; upgrade when Debian ships 1.4.6 (`apt-cache policy clamav`). The architecture and the epic now point to that decision.
  evidence: `epic-platform/story-virus-scanning-of-uploads-plan.md` Decisions; `deploy/README.md` "Scanning" checks print `ClamAV 1.4.3`.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-platform/story-refactor-sweep-plan.md`
  summary: Recorded and left (2026-11-14): the backup bucket's old `staging/` lifecycle rule (`expire-dumps-30d`) may be dropped by the bucket owner after 2026-12-09, 30 days after dev moved to `staging-dev/` (entry 13); keep the per-site rules.
  evidence: `epic-platform/story-backups-and-monitoring-per-site-plan.md` staging step 13.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-platform/story-refactor-sweep-plan.md`
  summary: Recorded and left (2026-11-14): the `mytp-{site}-*` Sentry cron monitors (dev, dev-ae, admindev, the restoretest ones included) exist but are disabled on the Sentry Developer plan, so failed jobs and a stopped worker do not alert; on the upgrade, enable them all and set Notify to devops@ before production go-live.
  evidence: `epic-platform/epic-platform.md` Decision 2026-11-01; entries 9, 7 and 13's plans.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-platform/story-refactor-sweep-plan.md`
  summary: Recorded and left, one entry, no ticket (user decision 2026-11-14): code copied between the two sites that only mytp-core could share: the sample task (portal `mytp_publicsite/tasks.py` and `manage.py enqueue_sample_task`, admin `layout/tasks.py` and its `enqueue_sample_task`); the SQLite JSON-feature test patch (`supports_json_field`, both `conftest.py`) and the restore settings block (`BACKUP_AGE_RESTORE_RECIPIENT`, `RESTORE_AGE_IDENTITY_FILE`, `RESTORE_TEST_DATABASE_URL`, both `settings.py`); the site-name pattern `[a-z]([a-z0-9-]{0,18}[a-z0-9])?` in 7 places besides core's `jobs.SITE_RE` and `interface.SITE_RE` (both `settings.py`, portal `deploy/provision.sh` twice, `deploy/release.sh`, both `deploy/run-job.sh`); and the S3 address in Sentry breadcrumbs: core's `backupdb` boto3 calls leave `aws.request.url` (bucket host and object key), which `mytp_core/sentry.py:193-204` keeps (it strips only query strings and fragments); the portal's storage calls avoid it with `storage.s3.out_of_sentry()`.
  evidence: read in both repos and mytp-core on 2026-11-14; each needs a mytp-core release and re-vendoring, excluded by the sweep.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-admin-interface/story-production-checks-for-the-eu-go-live-plan.md`
  summary: The portal's `interface alert cleared: <kind>` line never reaches the `checkinterface` job's journal, though deploy/README.md "Interface alerts" says it does: the logger `integration.alerts` has no entry in `LOGGING`, so its INFO lines are dropped (only the command's `recovered` line is printed). Add the logger or correct the README, in epic-admin-interface's refactor sweep (entry 12).
  evidence: `integration/alerts.py:167` (`logger.info`), `mytreatplan/settings.py` `LOGGING` (lists `integration.calls`, `.outbox`, `.config`, `.clients`, `.contact_messages`, `.hub`, not `.alerts`); found by 4.11's review on 2026-11-14.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-subscriptions/story-upgrade-screen-and-welcome-page-plan.md`
  summary: On the upgrade screen the current level's name comes from the held subscription row while the listed levels and the card marked current come from the group of the doctor's profile country today; once a doctor can change country (epic-doctor-profile), a held row of another group shows a name and a marked card that disagree, and a button priced for the new group. Decide then what a held subscription of another group shows.
  evidence: `subscriptions/service.py` `upgrade_options` (`held.subscription` vs `resolver.subscription(country, tier)`); not reachable today: no profile page, `country` changes only in the Django admin; found by 8.2's review on 2026-11-14.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-subscriptions/story-upgrade-screen-and-welcome-page-plan.md`
  summary: Before the EU go-live, review the Cloudflare Turnstile widget (user, 2026-11-14): on Safari on an iPad the signup widget failed several times in a row on the Spanish page ("Hora del dispositivo incorrecta", then "La verificación falló") and passed after reloads; English seemed to work. Nothing reached the server (no `captcha` event, no Turnstile log line), so the cause is unknown. To do: record the widget's error code in the event log (the error callback discards it today); reset the widget when the page comes back from the browser's cache or its token expires; test iPad and iPhone Safari in both languages, a tab left open for hours included; add the widget's failure rate to the go-live checks.
  evidence: `static/js/turnstile.js` (`error-callback` and `timeout-callback` call `onFailure` without the code; no `expired-callback`, no `pageshow` handling); `templates/includes/_turnstile.html` (the page's language is the only difference between the Spanish and English widgets); dev's event log and service log on 2026-11-14.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-subscriptions/story-upgrade-screen-and-welcome-page-plan.md`
  summary: The upgrade screen's design is functional only: review it later against the corporate style (user, 2026-11-14, after the staging checks). Entry 5's purchase page and the welcome page are candidates for the same review.
  evidence: `subscriptions/templates/subscriptions/upgrade.html`, `upgrade_purchase.html`, `assets/css/site.css`.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-admin-interface/story-refactor-sweep-plan.md`
  summary: Status (4.12, 2026-11-14) of the `integration.alerts` logger missing from the portal's `LOGGING` (4.11's review): closed by 4.12; `mytreatplan/settings.py` `LOGGING` now lists `integration.alerts` and `integration.remote` (its remote read and write lines were dropped the same way), and `integration/tests/test_alerts.py::test_the_info_lines_reach_the_journal` proves an INFO line of each reaches the console handler.
  evidence: the 4.12 diff; deploy/README.md "Interface alerts" (`interface alert cleared: <kind>` in the job's journal) is true again.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-admin-interface/story-refactor-sweep-plan.md`
  summary: Status (4.12, 2026-11-14) of the mytp-core `fresh_code` wording (3.5, and 3.13's status entry): closed by mytp-core 0.8.0 and mytp-admin commit fa47691; core `mytp_core/policy.py:11-15` (ADMIN docstring) and `:49-50` (the field comment) say the flag turns on a fresh code before each sensitive action, not a code at every sign-in, and mytp-admin commit fa47691 (entry 3, mytp-core 0.8.0 vendored) replaced the workaround comment at `staff/signin.py` with a plain pointer to `mytp_core.policy.ADMIN`.
  evidence: read in mytp-core and mytp-admin during the epic-admin-interface refactor sweep.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-admin-interface/story-refactor-sweep-plan.md`
  summary: Recorded and left, one entry, no core release (user decision 2026-11-14): epic-admin-interface's mytp-core leftovers: two names nothing uses (`contracts/checks.py:47` `PATIENT_FIELDS`, `interface.py:254` `signing_key_configured`), and the code copied between the sites that core could hold: the signed-endpoint checks and call log (portal `integration/calls.py`, admin `interface/calls.py`), the `purge_interface_calls` command (both sites, identical), and `client_ip` (portal `eventlog/service.py:34`, admin `interface/calls.py:44`).
  evidence: read in both repos and mytp-core (e3d95ef) on 2026-11-14; each needs a mytp-core release and re-vendoring, excluded by the sweep.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-admin-interface/story-refactor-sweep-plan.md`
  summary: Recorded and left (user decision 2026-11-14): the admin's event-ingest region guard needs an existing record, so a `client.*` or `contact_message.*` event for a uid the admin has no record of (never received, or deleted) is accepted from any region (an update then creates the record in the sender's region), and a wrong-region event at or below the stored version is `stale`, not `rejected`, as the version check comes before the applier.
  evidence: mytp-admin `interface/events.py:87-95` (`_locked_record`) and `:246-250` (the version check before the applier); its `README.md:1048-1052` "Known limits"; closing it changes what the ingest accepts (a behaviour change), out of the sweep.
- source_plan: `epic-admin-interface/story-refactor-sweep-plan.md`
  summary: mytp-admin's `layout/tests/test_pages.py::test_home_is_the_empty_adminlte_layout` fails at random: it asserts that the lower-cased home page HTML does not contain `cdn`, and the page carries random tokens that can spell it. Check the assets' addresses instead of the whole page.
  evidence: master run 43632718501 (commit 78aadf0) failed on that assertion and passed on the re-run of the same commit; both PR runs of the same change passed; the cause (a random token) is the likely one, not confirmed from the failing page. The portal has the same pattern (`'cdn'` in `mytp_publicsite/tests/test_home.py`).

- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-doctor-profile/story-doctor-area-layout-and-home-plan.md`
  summary: The home's "Messages / Alerts" and "Welcome / Tutorials" tiles and the header's messages icon lead to "coming soon" pages (`/<lang>/messages/`, `/<lang>/tutorials/`) that no portal epic owns yet (user decision 2026-11-14): give them an owner, or remove them, before they are noticed as dead ends.
  evidence: `accounts/urls.py` (`messages`, `tutorials` on `accounts.views.coming_soon`), `accounts/templates/accounts/home.html`, `templates/doctor_base.html`.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-doctor-profile/story-doctor-area-layout-and-home-plan.md`
  summary: The corporate-style review of the upgrade screen, the purchase placeholder and the welcome page (8.2's entry above) now also covers the doctor layout and home (6.1): the gradient, the isotype, the avatar with initials (until 6.4's photo), the tiles and the handshake band follow the Annex I mockup by eye, with the theme's colours and arbitrary sizes, and the Spanish home uses the mockup's informal "tú" ("Aquí tienes…", "Solicita…") while the rest of the portal says "usted".
  evidence: `templates/doctor_base.html`, `accounts/templates/accounts/home.html`, `locale/es/LC_MESSAGES/django.po`.
- source_plan: `epic-doctor-profile/story-doctor-area-layout-and-home-plan.md`
  summary: Tests that mix a fixed calendar day with today's date fail once that day has passed; three in `subscriptions/tests/test_service.py` did on 2026-11-15 and were given an explicit start date in 6.1's PR. The other fixed days in the test trees (`catalogue/tests` `DAY` constants, `subscriptions/tests/helpers.py` `DAY`) have not been audited for the same pattern: do it in epic-subscriptions' refactor sweep (entry 12).
  evidence: `subscriptions/tests/helpers.py:25` `DAY = date(2026, 11, 14)`; `service.change` defaults `started_on` to today and refuses an end date before it; the full suite on 2026-11-15 gave 3 failed, 3138 passed before the fix.

- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-doctor-profile/story-settings-shell-and-personal-data-plan.md`
  summary: The settings area's "Change password" tile (6.2) opens a "coming soon" page and no story owns a password change for a signed-in doctor (user decision 2026-11-15): give it an owner (epic-login or epic-doctor-profile) or drop the tile.
  evidence: `accounts/urls.py` (`settings_password` on `accounts.views.settings_coming_soon`), `accounts/views.py` `SETTINGS_SECTIONS`.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-doctor-profile/story-settings-shell-and-personal-data-plan.md`
  summary: The preferred language saved on the personal-data page (6.2) only switches the page after the save; a login does not land in it yet (the language still comes from the URL prefix). Land the doctor in `Profile.language` after login in a later story.
  evidence: `accounts/models.py` `Profile.language`; `accounts/views.py` `personal_data` (the redirect) and `LOGIN_REDIRECT`.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-doctor-profile/story-settings-shell-and-personal-data-plan.md`
  summary: The time zone saved on the personal-data page (6.2) is stored and sent to the admin site only; every page keeps the site's zone (`TIME_ZONE = REGION_PROFILE.time_zone`). Activating the doctor's zone (dates and times shown in it) is for a later story.
  evidence: `accounts/models.py` `Profile.time_zone`; `mytreatplan/settings.py` `TIME_ZONE`.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-doctor-profile/story-settings-shell-and-personal-data-plan.md`
  summary: Product/legal decision for when the final texts arrive: should a new version of an optional consent text (WhatsApp contact, commercial communications) require a fresh acceptance? Today (6.2) an acceptance given on an earlier version still shows as ticked on the personal-data page after a new version is published (the latest choice of the kind wins, whatever its version); only a save made on a page showing the older version is refused.
  evidence: `accounts/profile.py` `consents_of` (latest choice per kind), `accounts/forms.py` `PersonalDataForm.clean` (`consents_changed`).
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-doctor-profile/story-code-confirmed-mobile-and-contact-email-changes-plan.md`
  summary: Which address future doctor messages use (epic-case-lifecycle): 6.3 makes the contact (alternative) email code-confirmed, but nothing sends to it yet; the change notices go to the login email. Decide whether case messages go to the login email, the contact email, or both.
  evidence: `accounts/models.py` `Profile.alternative_email`; `accounts/profile.py` `notify_change` (to `user.email`).
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-doctor-profile/story-code-confirmed-mobile-and-contact-email-changes-plan.md`
  summary: A pending contact change lives 10 minutes from its start, like the first code; a code first sent later (a failed send retried after some minutes) can outlive the pending row, and the doctor then sees "This change has expired. Please start again." before the code expires. Refresh the row's start on each new code if this shows up in use.
  evidence: `accounts/profile.py` `PENDING_CHANGE_SECONDS`, `pending_change`; `accounts/views.py` `_change_code`.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-doctor-profile/story-code-confirmed-mobile-and-contact-email-changes-plan.md`
  summary: Change codes share Twilio Verify's one service with signup and login codes (as the open item above): a change code and a pending signup or login code to the same new contact are interchangeable at Twilio, and wrong tries count against both. Accepted as for login (control of the same contact); revisit with the provider-path item.
  evidence: `signup/twilio_verify.py` (module docstring), `accounts/profile.py` `CHANGE_PURPOSE`.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-doctor-profile/story-profile-photo-plan.md`
  summary: Staff will be able to see a doctor's profile photo later (user, 2026-11-16) in two places. The first is a doctor's file holding the rest of the doctor's personal and business data; no epic owns that file yet. The second is a support ticket (handoff in epic-support Notes). 6.4 keeps the photo owner-only and regional. A staff view must use the logged download path (AD-9) and serve the photo from the region, because AD-2 keeps it off the admin site.
  evidence: 6.4 plan Decisions; the `profile_photo` purpose allows download by the owner only.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-doctor-profile/story-fiscal-entities-plan.md`
  summary: Should pricing and the subscription resolver use the default fiscal entity's billing country instead of the profile country? 6.5 left them on the profile country (user, 2026-11-16).
  evidence: `subscriptions/service.py` `_country` and `catalogue/pricing.py` read `user.country`, as their comments say.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-doctor-profile/story-tax-id-checks-and-vies-plan.md`
  summary: Re-check VIES at checkout (epic-payments). 6.6 checks a VAT number when it is saved or changed and retries only while it is pending; there is no periodic re-check, so a `valid` status can be old when an order is priced with it. Decide whether checkout re-checks (and what to do when VIES is down).
  evidence: `accounts/fiscal.py` `vies_status`, `retry_due`; `accounts/vies.py` `fetch_vies`.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-doctor-profile/story-tax-id-checks-and-vies-plan.md`
  summary: Spanish person NIFs starting with K, L or M (minors, Spaniards living abroad, foreigners without an NIE) are refused for a person, since 6.6 accepts only a DNI-NIF or an NIE as the plan's decision says; a non-resident Spanish doctor billed as a person cannot save. The user was asked on 2026-11-17 whether to accept them.
  evidence: `accounts/taxid.py` (person ids: stdnum `es.dni`, `es.nie`); stdnum `es.nif` accepts `M1234567L`.
- source_plan: `_bmad-output/initiative-mytreatplan-portal-admin/epic-doctor-profile/story-shipping-addresses-plan.md`
  summary: A "Use my fiscal address" button on the shipping address form, to copy the default fiscal entity's address into a new shipping address. Deferred by the user (2026-11-17): not built in 6.7.
  evidence: `accounts/views.py` `settings_shipping_add`; `accounts/fiscal.py` `default_entity`.
