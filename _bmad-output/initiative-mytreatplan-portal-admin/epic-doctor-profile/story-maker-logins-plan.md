---
title: 'Maker logins'
type: 'feature'
ticket: '8'
created: '2026-11-17'
status: 'done'
baseline_revision: 'bd13bf098c2db63878107fdc02d3d5f0c6b2fb1f'
route: 'full'
route_source: 'pinned'
risk: 'high'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Specialists work inside the aligner makers' platforms with the doctor's login. Today a doctor has no safe place to give it (D7). epic-ordering also requires a saved login before a case for a named maker can be ordered.

**Approach:** Add a regional `MakerLogin` model whose username and password are field-encrypted (AD-2: region, no central copy). The settings page shows only "Saved on {date}", never the values. Doctors can replace or delete a login, and a hint recommends an auxiliary profile. epic-ordering gets `has_login(user, maker)`. epic-clinical-ops gets a tested regional reveal seam, which it will serve over remote reads (4.5). Nothing ever goes to the admin site.

**Decisions (user, 2026-11-18):**
- **Stored:** the username (or e-mail) and the password only. The maker's site is known per maker.
- **How many:**
  - One login per named maker: Invisalign, Ormco Spark, Angel Aligner, Solventum, Smartee.
  - Up to 5 "other" logins, each with a maker name (required, 60 characters at most, unique per doctor, case-insensitive).
- **Page:**
  - Each named maker shows "Not saved" or "Saved on {date}", with Save or Replace, and Delete.
  - "Other" logins show their name and the date.
  - No value, and no part of one, is ever rendered.
- **Replace:** both fields are entered again. The old values are overwritten and the date is updated. Delete asks for confirmation and removes the row.
- **Reveal: what 6.8 builds.** Only the regional seam `accounts/makers_remote.py`:
  - `RESOURCE = 'maker_login'`;
  - `load(object_id)`, where the object id is the login's UUID;
  - `assignee(record)`, which returns `None` until epic-clinical-ops supplies case assignments;
  - `version(record)`;
  - `build(record, fields)`, which returns the maker, the other name, the username and the password.

  It is shaped like `integration/sample.py` and tested by calling the functions directly. There is no mytp-core resource, no route and no admin screen; epic-clinical-ops adds those with a core release. A handoff note goes in epic-clinical-ops.
- **Who may reveal.** This is recorded in the epic-clinical-ops handoff, for its resource rule:
  - Sysadmin and Clinical Manager, for doctors in their regions;
  - a Clinical User, only for doctors with a case assigned to them;
  - until assignments exist, `assignee` answers nobody, so Clinical Users are refused.
- **What a reveal records:** who and when, through the `RemoteAccess` row the remote-read framework writes (actor, role, site, IP, resource, object, field names, never values). A reason or case id is left to epic-clinical-ops (`access_reason`).
- **Reveal history for the doctor:** not now (deferred-work entry).
- **Hint**, under the form: "We recommend creating an auxiliary profile for MyTreatPlan on the maker's platform instead of sharing your main login, without two-step codes where the maker allows it."
- **Ordering lookup:** `has_login(user, maker)` for the five named makers, plus `saved_makers(user)` for the wizard. "Other" and "not decided yet" never block ordering. The save page honours a safe `next`.
- **Merge and staging are delegated.** On dev, in a rolled-back transaction:
  - save an Invisalign login;
  - the page shows only the date;
  - a raw SQL read shows ciphertext;
  - a replace removes the old value;
  - no outbox event holds it.

  The user checks the page by eye. Sonnet implements and Opus reviews with a security focus.

## Boundaries & Constraints

**Always:**
- UUID primary key, FK to the user with CASCADE. Every write goes through `accounts/makers.py` (atomic, user row locked).
- Username and password use `mytp_core.crypto` `EncryptedCharField`. The maker code and the other name are plain.
- The password input is never rendered with a value. Both inputs use `autocomplete="off"` / `"new-password"`, so browsers neither fill in the portal password nor offer to save this one.
- An error re-render keeps the other name but clears both secrets.
- Every new string is translated (formal "usted"). The URL name `settings_maker` and the tile are kept.
- After this story only `password` is left in `SETTINGS_COMING_SOON`, and it becomes the tests' unbuilt example.

**Never:**
- No maker login in `client.updated`, logs, page records, the event log, exceptions, e-mails or `__str__`.
- No admin-site change, no core release and no remote route.

## I/O & Edge-Case Matrix

| # | Situation | Expected |
|---|---|---|
| 1 | Doctor saves an Invisalign login | Stored encrypted; the page shows "Saved on {date}" for Invisalign and "Not saved" for the others |
| 2 | The page's HTML after any save, error or replace | Contains neither the username nor the password, nor any part of them |
| 3 | Save with the username or password missing | Field error; both secret inputs come back empty |
| 4 | Replace the Invisalign login | Old values gone (raw SQL and decrypt); date updated; still one row |
| 5 | Delete (confirmed) | Row removed; the maker shows "Not saved" |
| 6 | Add an "other" login with the name "Clear Aligner X" | Saved; listed with its name and date |
| 7 | "Other" with no name, a duplicate name (any case), or a 6th "other" | Field error, or the limit message |
| 8 | Unknown maker code or a forged second Invisalign row | Refused (404 or a DB constraint); never two rows for one named maker |
| 9 | Another doctor's login id in the replace or delete URLs | 404 |
| 10 | Staff opens the pages; anonymous opens them | 404; redirect to login |
| 11 | Save page with a safe or an external `next` | Own-site path honoured; external ignored (`_safe_next`) |
| 12 | `has_login(user, 'invisalign')` before and after saving; `saved_makers` | False, then True; the list holds the saved makers |
| 13 | Account erased | All maker logins deleted |
| 14 | Any write, then `payload_of(user)`, the outbox, log records and Event rows | No maker-login key or value anywhere |
| 15 | Raw SQL read | Username and password columns are ciphertext |
| 16 | Seam: `load` with an existing id, a missing id, or another region's | Record; `None` for a missing id, so the framework answers 404 |
| 17 | Seam: `assignee` | `None` now |
| 18 | Seam: `build(record, ['username', 'password'])` | Decrypted values, only for the requested fields |
| 19 | ES interface | All new strings translated |

</frozen-after-approval>

## Code Map

- **Remote-read seam template:** `integration/sample.py` (`RESOURCE`, `load`, `assignee`, `version`, `build`); the framework is `integration/remote.py:91` `remote_read`, with `access_log.record_granted` (fail-closed) and `RemoteAccess` (`integration/models.py:228`, `access_reason`). Do not touch `integration/`.
- **Encryption:** `accounts/crypto.py:1-20` re-exports `mytp_core.crypto` (associated data `<app>.<model>.<field>`; only `isnull` lookups).
- **Settings:**
  - `accounts/views.py:421-440` (`SETTINGS_SECTIONS` tile "Maker data"; drop `'maker'` from `SETTINGS_COMING_SOON`);
  - `accounts/urls.py` `settings_maker`;
  - reuse `_safe_next` and the doctor guard from the fiscal/shipping views.
  - Service, view and template pattern: `accounts/shipping.py`, `accounts/views.py` (shipping views), `accounts/templates/accounts/settings_shipping*.html`.
- **Unbuilt example, repoint maker → password:**
  - `accounts/tests/test_personal_data.py` (`SECTIONS` and the coming-soon test);
  - `accounts/tests/browser/test_personal_data.py`;
  - check `mytp_publicsite/tests/test_settings_staging.py` and `urls_with_portal.py` (names only; likely no change).
- **Erasure:** `accounts/erasure.py`; add `makers.erase(user)` after `shipping.erase`.
- **Sync:** `accounts/client_sync.py` `payload_of` (explicit list). Add a negative test beside `accounts/tests/test_client_sync.py:189`.
- **Requirements:**
  - initiative :77;
  - epic-ordering.md:58, tickets.toml:29-32 (makers list; "other" and "not decided yet" excepted);
  - epic-clinical-ops.md:30,46;
  - AD-2 :89, log rule :258.
- **Migration:** `accounts` 0022.

## Tasks & Acceptance

1. `accounts/models.py` and migration 0022: `MakerLogin`.
   - Fields: UUID pk, user FK, `maker` (choices `invisalign`, `spark`, `angel`, `solventum`, `smartee`, `other`), `other_name`, encrypted `username` and `password`, `saved_at`, `created_at`.
   - Constraints: a conditional UniqueConstraint (user, maker) for maker ≠ other, and (user, lower(other_name)) for other. `__str__` shows no value.
2. `accounts/makers.py`: `save(user, maker, data, *, other_name=None)` creates or overwrites; plus `remove`, `has_login`, `saved_makers`, `logins(user)` (metadata only), `erase`, and the other-limit and name checks. Nothing is logged except ids.
3. `accounts/makers_remote.py`: the seam per Decisions.
4. `accounts/forms.py`: `MakerLoginForm` (password input, `render_value=False`, autocomplete off).
5. `accounts/views.py`, `accounts/urls.py` and templates: the list, save/replace per maker (`settings/maker/<maker>/`), add other, replace other, delete confirmation, the hint, and the ES `.po`.
6. `accounts/erasure.py`: call `makers.erase`.
7. Docs:
   - handoff notes in epic-clinical-ops (the seam, the reveal rules, `access_reason`) and epic-ordering (`has_login`, `saved_makers`, the `next` link);
   - a deferred-work entry for the doctor's reveal history;
   - README.
8. Tests: matrix rows 1-19 in `accounts/tests/test_makers.py`; the unbuilt-example updates; full suite with 0 failures; `makemigrations --check` clean.

**Acceptance:** the ticket's verify, plus matrix rows 1-19 covered by tests.

## Design Notes

ES: "Datos de fabricantes"; "Guardado el {date}"; "No guardado"; "Usuario o email"; "Contraseña"; "Otro fabricante"; "Nombre del fabricante"; "Sustituir"; and the hint in "usted" form.

## Implementation Notes

- `version()` is `saved_at` in microseconds (no version column in the plan).
- The username input never renders a value either (a custom widget), like the password.
- Review patches: writes re-check the locked user row (erasure race); the "other" name lookup uses the constraint's `Lower()` expression; the epic-clinical-ops handoff corrected (`remote_read` calls `build(record)`; framework change or a `FIELDS` wrapper).
- Verified 2026-11-18: full suite 3582 passed / 12 skipped / 0 failed (SQLite); `makemigrations --check` clean. Case-insensitive other names on PostgreSQL: checked in staging.

## Plan Change Log

## Review Triage Log

Quick lens (Opus, security focus), 2026-11-18. No AGENTS.md/CLAUDE.md in the repo.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | `makers._write`/`remove` check the user before the lock; a save queued behind `erase_user`'s user-row lock inserts a login for the erased account | medium | `_check_user` reads the caller's object; `erase_user` (`accounts/erasure.py`) locks the same row, then `makers.erase`; nothing re-checks `locked.is_erased` | patch |
| 2 | Other-name lookup uses `iexact` (UPPER on PostgreSQL) while the constraint uses `Lower`; e.g. Kelvin sign vs "k" → uncaught IntegrityError (500) | low | Django's PostgreSQL `iexact` is `UPPER(x) = UPPER(y)`; constraint `Lower('other_name')`; fix is a direct swap of the lookup expression | patch |
| 3 | epic-clinical-ops handoff suggests `lambda record: makers_remote.build(record, decision.fields)`, which cannot work | low | `integration/remote.py` `remote_read` calls `build(record)` with one argument; `decision` is local to it | patch (docs) |
| 4 | Ciphertext associated data binds model and field, not the row/user; a DB writer could move a ciphertext between rows | low | `mytp_core.crypto` `associated_data` = `<app>.<model>.<field>`; pre-existing library design, accepted by the plan | defer |
| 5 | Other-login replace re-finds the row by name; a concurrent delete makes the replace re-create it | low | real but needs a delete in another tab mid-submit; result is the values the doctor just typed; the claimed 500 needs an `other_name` problem, which only `add_other` (`must_be_new`) raises; fix adds a service entry point | reject |
| 6 | Row-4 raw-SQL check compares against plaintext, so it cannot show the old ciphertext is gone | low | ciphertext never contains the plaintext; fix is a direct test change | patch |

## Verification

**Commands:**
- the full suite (see local-dev notes) -- expected: 0 failures
- `python manage.py makemigrations --check` -- expected: no changes

## Staging

- PR #111 merged as 2bcdb84 on 2026-11-18. PR CI `test` passed on PostgreSQL (runs 44291298813, 44291311437). Master run 44293157725 passed: test plus deploys to dev and dev-ae.
- Checks 1-6 passed on both dev (eu) and dev-ae (ae), run in one rolled-back transaction per site, with nothing kept:
  1. Migration 0022 applied.
  2. Save works; the list and form pages show only the date, with no value or part of one.
  3. The raw columns hold ciphertext, and it decrypts.
  4. A replace changes both ciphertexts, keeps one row, and decrypts to the new values.
  5. No outbox event and a clean `payload_of`.
  6. PostgreSQL refuses a case-insensitive duplicate "other" name.
- Check 7 (the user's look at the page in EN/ES, desktop and phone) has not been reported yet.
