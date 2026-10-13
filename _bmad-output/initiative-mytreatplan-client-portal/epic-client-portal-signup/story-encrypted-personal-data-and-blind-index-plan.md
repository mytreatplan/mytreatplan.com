---
title: 'Encrypted personal data and blind index'
type: 'feature'
ticket: '2'
created: '2026-10-13'
status: 'built'
baseline_revision: '3ed5399b3cd7a5aea030e3c09a61742b819bd168'
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

**Problem:** Since 2.1, the user's and the pending registration's email (and the user's names) are plaintext in PostgreSQL. `encryption-policy.md` requires personal fields to be encrypted in the app (AES-256-GCM), with email and phone lookups through a blind index and passwords hashed with Argon2id.

**Approach:**
- Add encrypted model fields: AES-256-GCM, with keys from a key-provider interface. For now an environment-key provider supplies the keys; the KMS provider replaces it in 2.12.
- Add an HMAC-SHA256 blind index for normalized email and phone.
- Switch password hashing to Argon2id.
- Apply all of this to `accounts.User` and `signup.PendingRegistration`, so that every email lookup (uniqueness, signup checks, login, admin) uses the index.

## Boundaries & Constraints

**Always:**
- **Encrypted fields:**
  - AES-256-GCM with a random 96-bit nonce per value (`cryptography`'s `AESGCM`).
  - The stored text carries a format version and a key id, e.g. `enc:v1:<key_id>:<base64(nonce|ciphertext|tag)>`, so keys can rotate.
  - The associated data is `<app_label>.<model>.<field>`, so a value copied to another field fails to decrypt.
  - Decryption failure raises an error. It never returns ciphertext or an empty value.
  - A query by an encrypted field (other than `isnull`) raises an error instead of silently matching nothing.
- **Fields encrypted:**
  - `User.email`, `first_name` and `last_name`;
  - `PendingRegistration.email`;
  - entry 4 reuses the field for its new fields.
- **Key provider:**
  - Interface: `current_key() -> (key_id, key)` and `get_key(key_id) -> key`, chosen by the setting `FIELD_KEY_PROVIDER`.
  - Environment provider: `FIELD_ENCRYPTION_KEYS` lists `<key_id>:<base64 32-byte key>` entries, comma-separated. The first is current; the others only decrypt.
  - Rotation: add a new key first in the list, then run `manage.py reencrypt_fields`, which rewrites every encrypted value with the current key.
- **Blind index:**
  - `HMAC-SHA256(BLIND_INDEX_KEY, normalized value)`, hex, with its own key.
  - Email is normalized by stripping spaces and lowercasing all of it, matching 2.1's case-insensitive identity. Phone is normalized to `+` followed by digits only; entry 4 uses it.
  - `User.email_index` is unique and replaces the `Lower('email')` constraint. `PendingRegistration.email_index` is indexed but not unique (entry 5 uses it).
  - The index is set on every save.
  - These all look up by the index:
    - `get_by_natural_key`;
    - signup's `email_is_registered`;
    - the admin add-form check;
    - the admin search (an exact email only).
- **Missing keys:** a missing or malformed key stops the app with a clear message naming the setting: a system check error, and `migrate` fails too. There is never a silent fallback or a generated key. `manage.py generate_field_keys` prints new keys for a person to store.
- **Passwords:**
  - `PASSWORD_HASHERS` puts Django's Argon2 hasher (Argon2id) first, with the others kept so old hashes still verify. Django upgrades them at the next login.
  - Pending registrations store Argon2id hashes.
  - Uses `argon2-cffi`.
- **Data migration:** encrypts and indexes the existing users and pending registrations in place, and reverses cleanly.
- **Tests, CI and examples:** tests and CI use fixed non-secret test keys. `settings.env.example` and `deploy/staging.env.example` list the new keys with no values.
- **Runbook:** a new step 12 in `deploy/README.md`, done **before the merge**:
  - generate the keys and put them in staging `settings.env`;
  - record that the copy is in the team password manager;
  - how to rotate;
  - why losing the encryption key makes the data unreadable;
  - why the backups need the same keys.

- Decision (user, 2026-10-13): a copy of the staging field keys (`FIELD_ENCRYPTION_KEYS`, `BLIND_INDEX_KEY`) goes in the team password manager, entry "MyTreatplan staging field keys", besides `/srv/mytp/shared/settings.env`; a lost key cannot be reissued.
- Decision (user, 2026-10-13): existing users and pending registrations (staging test user, local `devops@mytreatplan.com`) are encrypted and indexed in place by a reversible data migration.
- Decision (user, 2026-10-13): the plan is kept whole although above 1600 tokens.

**Never:**
- The KMS provider (2.12).
- The event log (3).
- New step-1 fields (4).
- pgcrypto or any encryption in SQL.
- Keys in the repo, logs, Sentry or the chat.
- Plaintext personal values in the database, in admin list filters or ordering, or in error messages.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Signup | 2.1 flow | Raw DB rows: email/names are `enc:v1:...`, `email_index` is hex, password `argon2$argon2id$...` | — |
| Lookup | `Ana@Example.com` registered; look up `ana@example.com` | Found through the index (login, signup check, admin search) | — |
| Duplicate | Second user with a case variant | Refused by the unique `email_index` | Form error / IntegrityError |
| Old hash | User with a PBKDF2 hash logs in | Login works; hash becomes Argon2id | — |
| Rotation | New key added first; `reencrypt_fields` | Old values readable before, all on the new key id after | — |
| Unknown key id | Value with a key id not in the provider | — | Clear error, no plaintext or ciphertext in the message |
| Tampered value | One byte changed | — | Decryption error |
| Missing keys | `FIELD_ENCRYPTION_KEYS` or `BLIND_INDEX_KEY` empty | App refuses to start; message names the setting | System check error |
| Existing rows | Plaintext user before migrating | Encrypted and indexed after; reverse restores plaintext | — |

</frozen-after-approval>

## Code Map

- `accounts/models.py` -- `User` with `email` unique plus a `Lower('email')` constraint; `accounts/managers.py` -- `normalize_email`, `get_by_natural_key` (`email__iexact`). Both move to the index.
- `accounts/migrations/0002_email_username.py` -- merged; add new migrations after it (schema, then data).
- `accounts/admin.py:12-48` -- the add form's `iexact` check, `list_display`/`search_fields`/`ordering` by email; the search becomes an exact match by index, and the ordering by `id`.
- `signup/models.py`, `signup/forms.py` (`email_is_registered`, `normalize_email`), `signup/views.py` (`_create_account` race check), `signup/steps.py` (`make_password`).
- `mytreatplan/settings.py` -- the decouple pattern; `AUTH_PASSWORD_VALIDATORS` block for the new settings. `requirements.txt` -- add `cryptography` and `argon2-cffi` (pinned).
- `.github/workflows/ci.yml:29-35` -- job `env`: add the test keys (CI runs `migrate`). `deploy/release.sh:118` runs `migrate` on deploy, so staging keys must exist before the merge.
- `deploy/README.md` -- add `### 12.` after `### 11. KMS`; the `## Keys (KMS)` section is for 2.12.
- Tests: `accounts/tests/test_user.py`, `signup/tests/test_wizard.py` (2.1 no-plaintext test now also checks ciphertext), root `conftest.py`.

## Tasks & Acceptance

**Execution:**
- [x] `accounts/crypto.py` (or a shared `mytp_crypto` module) -- key-provider interface, env provider, encrypt/decrypt, blind index and normalizers, the encrypted model field, system checks.
- [x] `accounts/models.py`, `accounts/managers.py`, `accounts/backends.py`, `accounts/admin.py`, migrations -- encrypted fields, `email_index`, index lookups.
- [x] `signup/models.py`, `signup/forms.py`, `signup/views.py`, migrations -- the same for pending registrations.
- [x] `accounts/management/commands/generate_field_keys.py`, `reencrypt_fields.py` -- key generation and rotation.
- [x] `mytreatplan/settings.py`, `requirements.txt`, `.github/workflows/ci.yml`, `settings.env.example`, `deploy/staging.env.example` -- settings, hashers, dependencies, test keys.
- [x] `deploy/README.md` -- step 12.
- [x] tests -- every matrix row, plus "no key material in any output".

**Acceptance Criteria:**
- Given the 2.1 signup on a migrated DB, when it completes, then a raw SQL select shows no plaintext email or name in `accounts_user` or `signup_pendingregistration`.
- Given the runbook step 12 done on staging, when the merge deploys, then `migrate` succeeds and the existing staging user can still be found by email.

## Implementation Notes

- `accounts/crypto.py` holds everything: `KeyProvider` / `EnvKeyProvider` (cached, reset on `setting_changed`), `encrypt`/`decrypt`, `blind_index`/`email_index`/`phone_index` and normalizers, `EncryptedCharField`/`EncryptedEmailField` (text column, plaintext validation, any lookup but `isnull` raises `EncryptedFieldLookupError`), the system check (`accounts.E001` encryption keys/provider, `accounts.E002` blind index key, also when it equals an encryption key), and the raw-SQL data-migration helper.
- Migrations: accounts 0003 (schema, nullable `email_index`) → 0004 (RunPython encrypt + index, reverse decrypts) → 0005 (`email_index` required, unique); signup 0002 → 0003 → 0004 the same (index not unique). The data migrations use raw SQL because the historical encrypted fields would try to decrypt plaintext.
- Admin: search by exact email through the index, `ordering = ('id',)`, `sortable_by = ('is_staff',)`, change form also refuses a taken email, and the admin log stores `User <pk>` instead of `str(user)` (the email) in `django_admin_log.object_repr`.
- Tests use the fixed keys in root `conftest.py` (`pytest_configure` overrides any developer key); CI uses the same values in its job `env`.
- `generate_field_keys` (no system checks, `--rotate`, `--key-id`), `reencrypt_fields` (saves each row with `update_fields` so `save()` recomputes `email_index`; fails if any value is left on another key id).
- Not covered (not used yet): Django's `PasswordResetForm.get_users` filters `email__iexact` and would raise; the login epic must look up by `User.objects.by_email`.

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, 2026-10-13): high 0, medium 2, low 3, false 0, maybe-false 0. Locally (SQLite) 400 passed; 3 tests using PostgreSQL-only raw SQL (`now()`, UUID parameters) fail on SQLite and run in CI on Postgres.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | After `migrate` succeeds, a rollback to the pre-2.2 release (automatic on a failed health check, or by hand) meets ciphertext and a NOT NULL `email_index`; the runbook doesn't warn or give a recovery | medium | patch | Confirmed: `release.sh` migrates before switching and doesn't reverse migrations on rollback. Patch: step 12 warns and gives the recovery (`migrate accounts 0002` and `signup 0001` with the keys set, before switching back). |
| 2 | Gunicorn starts without valid keys: the check is only a system check, `/healthz` doesn't touch keys, so "app refuses to start" holds only for `check`/`migrate`/`runserver` | medium | patch | Confirmed: `get_wsgi_application` runs no system checks. Patch: `mytreatplan/wsgi.py` validates the keys at startup (a failed key setting stops Gunicorn with the setting's name). |
| 3 | Rotation runbook restarts Gunicorn without `check`; `generate_field_keys`' default id `k<date>` repeats on the same day | low | patch | Confirmed. Direct corrections: run `manage.py check` before the restart; default key id with UTC date and time. |
| 4 | `createsuperuser` with a taken email ends in an IntegrityError traceback instead of "already taken" | low | reject | Confirmed (`username_is_unique` is false), but operator-only, rare, and no data harm (the unique index refuses it); a fix needs a custom command. |
| 5 | Plan ticks `signup/views.py` though unchanged; Design Notes say `createsuperuser` uses `get_by_natural_key` for the check | low | reject | The behaviour is right (`_create_account` uses `email_is_registered`, now by index); the fix would edit this build's plan. |

## Design Notes

- **Email stays `USERNAME_FIELD`** but can't be unique in the DB (each ciphertext is different). Django then requires a non-default auth backend: `accounts.backends.EmailBackend(ModelBackend)` in `AUTHENTICATION_BACKENDS`, and `auth.W004` silenced with a comment. `get_by_natural_key` looks up `email_index=blind_index(email)`, so `createsuperuser` and admin login keep working.
- **Index on save:** `User.save()` / `PendingRegistration.save()` set `email_index` from `email`. Queryset `update()`/`bulk_create` bypass it; the data migration and `reencrypt_fields` must set it themselves.

## Verification

Done locally (2026-10-13) after the review patches, on SQLite: `pytest` 405 passed, 6 skipped (PyYAML), 3 failed only because those tests use PostgreSQL-only raw SQL (`now()`, UUID parameters); they pass on Postgres per the implementer's run against a throwaway PostgreSQL 17 (403 passed before the patches) and run in CI. `makemigrations --check` no changes; `check` with keys no issues (W004 silenced), without keys errors naming the settings. Staging checks pending (runbook step 12 before the merge).

**Commands:**
- `pytest` -- expected: all pass
- `python manage.py makemigrations --check --dry-run` -- expected: no changes
- `python manage.py check` -- expected: no issues with keys set; an error naming the setting without them
