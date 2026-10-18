---
title: 'Account erasure from the admin'
type: 'feature'
ticket: ''
created: '2026-10-15'
status: 'done'
baseline_revision: '0fea05e017df5d261411cb06c7b834789cd354b2'
route: 'full'
route_source: 'auto'
risk: 'high'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A registered user can't be deleted in the admin: `LegalChoice.user` is `PROTECT` (story 2.6, kept as evidence). No path exists to erase an account, for example on the user's request (GDPR art. 17), and the legal choices still need to be kept as proof for a period the lawyer sets. Found on staging, 2026-10-15 (deferred-work entry "Account erasure from the admin").

**Approach (user's design, 2026-10-15):**
- An admin **Erase account** action does a logical deletion:
  - it deactivates the account;
  - it keeps only what the proof needs, until `purge_after`;
  - it clears everything else.
- A daily job physically deletes each erased account, with its legal choices, once `purge_after` has passed.

## Boundaries & Constraints

**Always:**
- **Erasing** (one transaction per user):
  - set `erased_at = now` and `purge_after = now + ACCOUNT_ERASURE_RETENTION_DAYS`;
  - set `is_active=False`, `is_staff=False`, `is_superuser=False`, and remove groups and permissions;
  - give the user an unusable password, which also ends their sessions;
  - keep email, title, names, country, phone, `date_joined` and the legal choices; clear `how_found`, `how_found_other` and `last_login`;
  - record commercial consent as withdrawn: add a `LegalChoice` with `accepted=False` on the current commercial-consent version, at `now`. Skip this when no version is published, or when the user's latest choice on that kind is already not accepted.
- **Blind indexes once erased:**
  - `email_index` becomes a unique tombstone (`erased:<pk>`), so signup and the duplicate checks no longer find the account, and the same email can register again;
  - the real index moves to a new `erased_email_index` (not unique, indexed);
  - `phone_index` is blank.
  - `set_blind_indexes` applies this whenever `erased_at` is set, so `save()` and `reencrypt_fields` keep it.
- **Admin:**
  - The default list hides erased users. The "Status" filter offers active / erased / all.
  - The search by exact email also matches `erased_email_index`, so staff can find an erased account when it's needed as evidence.
  - An erased user is read-only.
  - Plain delete (the `delete_selected` action and per-object delete) is off for users.
  - The erase action has a confirmation page that shows the number of accounts and the purge date, warns in plain words that erasing can't be undone and that the account is deleted for good on that date, and needs an explicit confirmation (a required "I understand this can't be undone" checkbox) before the confirm button works; without it nothing changes. It skips users who are already erased, and it refuses the staff member's own account.
  - Each erasure writes an admin log entry "Erased", with object `User <pk>` (as `_LoggedUser`), the staff member and the time. No personal values.
- **Retention setting:**
  - `ACCOUNT_ERASURE_RETENTION_DAYS` is an int with no default. While it is unset, the erase action refuses with a message.
  - Deploy check `mytp_publicsite.E005`: `check --deploy` fails while it is unset and `DEBUG` is off.
- **Purge:**
  - `purge_erased(now=None, batch_size=...)` deletes, per batch in a transaction, each user with `purge_after <= now`: its legal choices first, then the user. It returns the count, and a second run changes nothing.
  - The command prints only that count. Job `purgeerased`, daily at 04:15 UTC, through `runjob` (Sentry check-in).
- New admin texts are in English and Spanish.

**Decisions (user, 2026-10-15):**
- Retention has no default in code; staging sets `ACCOUNT_ERASURE_RETENTION_DAYS=1` for the check until the lawyer gives the real value.
- Kept during retention: email, title, names, country, phone, `date_joined`, the legal choices (the phone stays encrypted, but its blind index is cleared).
- Who can erase: staff with Django's `accounts.delete_user` permission (superusers have it).
- No undo; instead, the warning and the explicit confirmation above.

**Never:**
- No self-service erasure (that belongs to the profile settings, a later epic) and no undo.
- No change to the event log: its personal values are already anonymized 30 days after an attempt (2.10).
- No retention period guessed in code.
- No email or name in logs, messages or job output.
- Don't install or download anything outside the sandbox.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error handling |
|---|---|---|---|
| Erase | Active user with choices, retention 30 | Inactive, unusable password, kept fields only, tombstone index, `purge_after` 30 days on, consent withdrawn, log "Erased" | — |
| Re-register | Erased email used at signup step 1 | Not a duplicate, signup proceeds | — |
| Find for evidence | Admin search with the exact erased email and filter "erased" or "all" | Erased user listed | — |
| Unset retention | Setting missing | Nothing changed | Admin error message |
| Own account / already erased | Selected | Skipped, with a message | — |
| No consent version / already declined | — | No withdrawal choice added | — |
| Re-encrypt | `reencrypt_fields` after an erasure | Tombstone and `erased_email_index` kept | — |
| Purge due | `purge_after <= now` | User and its choices deleted; count 1 | — |
| Purge not due / not erased | — | Untouched | — |
| Unconfirmed | Confirm pressed without the checkbox | Nothing changed, page shown again with an error | — |
| No permission | Staff without `accounts.delete_user` | Action not offered; direct POST refused | — |
| Plain delete | Admin delete URL or action | Not available (403 / no action) | — |

</frozen-after-approval>

## Code Map

- `accounts/models.py:45-92`:
  - `User`; the encrypted fields are `email`, the names, `phone` and `how_found_other`;
  - `email_index` is unique and required;
  - `set_blind_indexes` (:81) is called by `save()` (:85), which adds the indexes to `update_fields`. Add `erased_at`, `purge_after` and `erased_email_index` here, plus the tombstone rule. Migration `0007`.
- `accounts/managers.py:16`: `by_email` stays on `email_index` only.
- `accounts/admin.py:55` `UserAdmin`:
  - add the action, the list filter, the search extension (:85), and read-only and no-delete permissions;
  - reuse `_LoggedUser` (:43) for the log entry.
  - The confirmation page is a template under `templates/admin/accounts/user/`.
- New `accounts/erasure.py`: `erase_user(user, *, by, now=None)` and `purge_erased(now=None, batch_size=500)`.
- `legal/models.py`: `LegalDocument.objects.current(Kind.COMMERCIAL_CONSENT)` (:24); `LegalChoice` (:110), whose user FK stays `PROTECT`.
- `signup/forms.py:78,83` (`email_is_registered`, `phone_is_registered`) and `signup/views.py:399,543`: no change. They already miss a tombstoned account.
- Command `accounts/management/commands/purge_erased_accounts.py`. Copy the shape of `eventlog/management/commands/eventlog_retention.py`.
- `deploy/jobs.conf`: add `purgeerased | purge_erased_accounts | *-*-* 04:15:00 UTC`.
- `mytp_publicsite/checks.py:8-11,58,81`: E005 follows the E003/E004 pattern.
- `mytreatplan/settings.py:86`: the `config(..., cast=int)` style. Examples go in `settings.env.example:53` and `deploy/staging.env.example:24`.
- `accounts/management/commands/reencrypt_fields.py:35`: saves through the model, so no change. Test it.
- `locale/es/LC_MESSAGES/django.po`: new strings.
- `deploy/README.md`:
  - a short "Erase an account" section: the action, the retention setting, the purge job, and that backups keep an erased account until their 30-day expiry;
  - re-run `provision.sh` for the job.

## Tasks & Acceptance

**Execution:**
- [x] `accounts/models.py` + migration `0007`: the three fields and the tombstone rule in `set_blind_indexes`.
- [x] `accounts/erasure.py`: `erase_user` and `purge_erased`, per Always.
- [x] `accounts/admin.py` + confirmation template: the action, the filter, the search, the permissions and the log entry.
- [x] `accounts/management/commands/purge_erased_accounts.py` and `deploy/jobs.conf`: the job.
- [x] `mytreatplan/settings.py`, both env examples, and `mytp_publicsite/checks.py` (E005): the setting and the deploy check.
- [x] `locale/es/LC_MESSAGES/django.po` (+ `compilemessages`), `deploy/README.md`.
- [x] Tests: `accounts/tests/test_erasure.py` covers every matrix row, through the admin client where the row is about the admin. Also: the job prints only the count, `jobs.load_jobs` has `purgeerased`, and E005 is tested in `mytp_publicsite/tests/test_settings_staging.py`.

**Acceptance Criteria:**
- Given an erased user, when staff open it in the admin, then the kept fields show read-only and there is no Delete button.
- Given staging with `ACCOUNT_ERASURE_RETENTION_DAYS` set, when a test user is erased and the job runs after `purge_after`, then the user and its legal choices are gone and the job prints `1`.

## Implementation Notes

- The erasure's admin log entry is a CHANGE entry with the message "Erased" (the user row still exists), object `User <pk>` via `_LoggedUser`.
- The "status" filter labels use their own translation context ("erasure status") so they don't change Django's Spanish "All" of the other admin filters.
- A withdrawal is recorded even when the user never chose on commercial consent (the plan skips only when no version is published or the latest choice is already declined).
- The tests did not run on PostgreSQL locally (the local database user can't create the test database); CI is the first PostgreSQL run.

## Plan Change Log

## Review Triage Log

Quick review, pass 1 (2026-10-15): 6 findings. high 0, medium 1, low 4, false 1, maybe-false 0.

| # | Finding | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | The confirmation page says the account is deleted on `purge_date`, but the daily 04:15 UTC job deletes it at its first run after `purge_after`, up to a day later | low | patch | Real: `purge_erased` filters `purge_after <= now` and runs once a day. Reworded: retention ends on the date, the next daily purge deletes it. |
| 2 | Purging an erased former staff account cascades to the admin log entries they wrote, including "Erased" entries about other users | low | reject | Real (`LogEntry.user` is CASCADE), but it needs a staff member to be erased and their retention to end before that of the users they erased; erasure is for clients' requests. Fixing it needs new behaviour (refuse staff accounts, or keep the entries elsewhere). Told the user: deactivate staff accounts rather than erase them. |
| 3 | `ACCOUNT_ERASURE_RETENTION_DAYS=0` or negative passes E005 and makes the next purge delete the evidence at once | medium | patch | Real: the cast accepts any int and E005 only rejects `None`. Values below 1 now count as unset (action refuses, E005 fails), with tests. |
| 4 | `reencrypt_fields` loads users with `.only()`, so reading `erased_at` in `set_blind_indexes` costs one extra query per user | low | reject | Real, but correct (tombstone kept, tested); rotation is rare and staging has 5 users. The fix would special-case the User model in a generic command. |
| 5 | The "own account skipped" / "already erased" warnings repeat on the confirmation page, after confirming, and on each unconfirmed resubmit | low | patch | Real: the messages are added on every pass through the action. Now emitted once. |
| 6 | Acceptance criterion 2 (staging run of erase and purge) has no evidence | false | reject | It is a staging check after the merge, by design; not something the diff can show. |

## Design Notes

The tombstone keeps `email_index` unique and required with no constraint change. Moving the real index to `erased_email_index` keeps the account findable as evidence without it blocking a new signup. If the same person registers again during retention, the new account and the erased one share `erased_email_index` with the new `email_index`. That's fine: the filter tells them apart.

## Verification

**Commands:**
- `DATABASE_URL="sqlite:///$TMPDIR/x.sqlite3" ../.venv/bin/python -m pytest -q` (with the test keys). Expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py makemigrations --check`, after adding `0007`. Expected: no changes.
- `manage.py compilemessages`. Expected: OK.

Done locally (2026-10-15) after the review patches, on SQLite: `pytest` 842 passed, 7 skipped (PyYAML, and the browser test without Playwright), 3 failed only because those tests use PostgreSQL-only raw SQL. `makemigrations --check` reports no changes; `compilemessages` is up to date. The admin's refusal message was reworded after the patches to cover 0 and negative values ("is not set to a positive number of days"). CI on PostgreSQL 16 (PR #24): 852 passed.

Staging (2026-10-15 to 2026-10-18): the merge deployed; `ACCOUNT_ERASURE_RETENTION_DAYS=1` was set and the app restarted. The user reported the erase checks passed: an erased test user shows only under status "Erased", read-only; the exact-email search under "Erased" finds it; the same email signs up again. The `purgeerased` timer was missing at first (`provision.sh` had not installed it); after re-running `provision.sh` the timer is listed, and a run by hand (2026-10-18) printed `5` and ended ok: the 5 accounts erased in the admin during testing (5 "Erased" admin log entries), all past retention. Afterwards: 1 user, 0 erased.

