---
title: 'mytp-core package with field encryption and KMS'
type: 'refactor'
ticket: '4'
created: '2026-10-30'
status: 'built'
baseline_revision: '7218109afb9e995830d47c0b66f8547546ec3c3e'
route: 'full'
route_source: 'auto'
risk: 'high'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-mytreatplan-portal-admin/architecture-portal-admin/architecture-portal-admin.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The admin site (entry 10) needs the same field encryption, blind index and OVHcloud KMS key handling as the portal, but they live inside the portal (`accounts/crypto.py`, `mytp_publicsite/kms.py` and three commands). AD-10 puts them in a shared, versioned package so neither site re-implements them.

**Approach:** Create the package in the new private repo `mytreatplan/mytp-core` (already created, empty) as a small Django app `mytp_core` holding `crypto`, `kms` and the commands `generate_field_keys`, `generate_kms_field_key` and `kmscheck`, with its own tests and CI, tagged `v0.1.0`. The portal installs that tag, keeps `accounts/crypto.py` and `mytp_publicsite/kms.py` as re-exports so existing code, migrations and the `accounts.crypto.*Provider` strings in live settings.env files keep working, and its behaviour does not change.

## Decisions

- Distribution (user, 2026-10-30): the built wheel is committed in the portal repo (`vendor/mytp_core-0.1.0-py3-none-any.whl`) and referenced from requirements.txt; local dev, CI and servers install that file; a script rebuilds it from a mytp-core tag. The read-only deploy key on mytp-core and the portal's `MYTP_CORE_DEPLOY_KEY` secret are then unused and get deleted.

## Boundaries & Constraints

**Always:** stored ciphertext, its format (`enc:v1`), blind indexes and keys stay byte-for-byte compatible; `FIELD_KEY_PROVIDER=accounts.crypto.KMSKeyProvider` in existing settings.env files keeps working; every existing migration still imports and `makemigrations --check` is clean after the change; the portal's full suite passes; the package has no import of portal code.

**Never:** no change to encryption behaviour, key rotation or key material; no secrets in the repo, tests or logs; no 2FA, Sentry, runjob or backup code in this story (entry 5); no admin-site code.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Existing data | Rows encrypted before the move | Decrypt and blind-index lookups return the same values | — |
| Live setting | `FIELD_KEY_PROVIDER=accounts.crypto.KMSKeyProvider` | KMS provider used; kmscheck and the startup check treat it as KMS | — |
| New setting | `FIELD_KEY_PROVIDER=mytp_core.crypto.KMSKeyProvider` | Same behaviour | — |
| Old migrations | `migrate` from zero on an empty database | All migrations apply | — |
| Missing keys | No field keys configured | Same system check errors and startup refusal as today | Same messages |

</frozen-after-approval>

## Code Map

- `accounts/crypto.py` (599 lines) -- moves to `mytp_core/crypto.py`: constants, exceptions, key parsing, `KeyProvider`/`EnvKeyProvider`/`KMSKeyProvider`, provider helpers, `encrypt`/`decrypt`/`key_id_of`, normalizers and `email_index`/`phone_index`, `EncryptedCharField`/`EncryptedEmailField`/`EncryptedJSONField`, `encrypted_fields`, `check_field_keys`, `_rewrite_rows`/`encrypt_existing_rows`, the `setting_changed` receiver. Only project import: `from mytp_publicsite import kms` (:41) → `from mytp_core import kms`.
- `mytp_publicsite/kms.py` (288 lines) -- stdlib only; moves as is to `mytp_core/kms.py`.
- Provider string comparisons: `mytp_publicsite/management/commands/kmscheck.py:92` and `mytp_publicsite/checks.py:94-100` compare `FIELD_KEY_PROVIDER` to `crypto.KMS_PROVIDER` by string -- compare the resolved class instead (`get_key_provider_class()`), so both the old and new dotted paths work.
- Commands moving into the package app: `accounts/management/commands/generate_field_keys.py`, `generate_kms_field_key.py`, `mytp_publicsite/management/commands/kmscheck.py` (still run daily by `deploy/jobs.conf:17`). `reencrypt_fields` stays in accounts (it walks the portal's models).
- Migrations recording `accounts.crypto.Encrypted*Field`: accounts 0003, 0006; signup 0002, 0006; mytp_publicsite 0001; eventlog 0001; data migrations accounts 0004, signup 0003 import `encrypt_existing_rows`. The shim keeps them importable.
- Check registration `accounts/apps.py:9-17`, startup validation `mytreatplan/wsgi.py:21`, default provider string `mytreatplan/settings.py:246`.
- Tests: `accounts/tests/test_crypto.py` (35), `test_kms_provider.py` (28), `mytp_publicsite/tests/test_kms.py` (40), root `conftest.py:33-42`; `test_settings_staging.py:379,385` assert the provider string.
- Install points: `requirements.txt`, `.github/workflows/ci.yml:56`, `deploy/release.sh:129-131` (pip install on the server).

## Design Notes

- **Field paths:** the package's fields deconstruct to `mytp_core.crypto.*`; the portal gets one generated no-op `AlterField` migration per app that has encrypted fields (accounts, signup, mytp_publicsite, eventlog). No database change; old migrations import the classes through the `accounts.crypto` re-export.
- **Checks:** the package's AppConfig registers `check_field_keys` with ids `mytp_core.E001`/`E002` and a hint naming the commands, so the admin site gets them too; `accounts/apps.py` stops registering it.
- **Package tests:** own minimal Django settings and a test model, PostgreSQL in its CI (the raw-SQL helpers are PostgreSQL-only), Python 3.13, Django 6.1.2.
- **Versioning:** semantic versions, tag `v0.1.0`; the portal pins the tag.

## Tasks & Acceptance

**Execution:**
- [x] `../mytp-core/` (local clone of `mytreatplan/mytp-core`) -- pyproject, `mytp_core` app (crypto, kms, commands, AppConfig with checks), tests, README, CI workflow; push and tag `v0.1.0`. (Committed and tagged locally at 2111c72; not pushed.)
- [x] `accounts/crypto.py`, `mytp_publicsite/kms.py` -- re-export the package's names.
- [x] `kmscheck` callers, `mytp_publicsite/checks.py`, `accounts/apps.py`, `mytreatplan/settings.py` -- class-based provider comparison; add `mytp_core` to INSTALLED_APPS; remove moved commands from the portal.
- [x] portal migrations -- one no-op AlterField migration per affected app. (None needed: see the Plan Change Log.)
- [x] `vendor/`, `scripts/vendor-mytp-core.sh`, `requirements.txt` -- the committed wheel, the rebuild script (clone the tag, build the wheel, replace the file, update the requirement line), and the requirement pointing at the file; CI and release.sh need no change beyond installing requirements as today.
- [x] portal tests -- move package-only tests to mytp-core; keep portal integration tests; add a test that `accounts.crypto.KMSKeyProvider` and `mytp_core.crypto.KMSKeyProvider` both resolve to the KMS provider.

**Acceptance Criteria:**
- Given the portal on the mytp-core tag, when the full suite and `makemigrations --check` run, then both pass with no behaviour change.
- Given the mytp-core repo, when its CI runs on the tag, then its tests pass on PostgreSQL.
- Given the merge deployed to dev, when `kmscheck` runs and a page reads encrypted data, then both succeed with the unchanged settings.env.

## Implementation Notes

- Setup (orchestrator, 2026-10-30): `mytreatplan/mytp-core` is cloned at `../mytp-core` (sibling of this repo, empty, default branch to be `master`). Commit and tag `v0.1.0` there locally; do not push either repo — pushing happens after review. Writing outside this repo needs the sandbox off.

## Plan Change Log

- 2026-10-30 (implementation): no portal migrations. `makemigrations` detects nothing: the historical fields in the old migrations are instances of the same (moved) classes, so their deconstructed path is already `mytp_core.crypto.*` on both sides. An AlterField would be an empty operation, so none was added; `makemigrations --check` is clean and old migrations import through the `accounts.crypto` re-export.
- 2026-10-30 (implementation): `deploy/release.sh` runs its `pip install -r requirements.txt` from the release folder. pip resolves the relative wheel path (`./vendor/...`) from the working directory, not the requirements file (verified: it fails from another directory), and release.sh ran pip from wherever `mytp-release` was started. CI already installs from the repository root.
- 2026-10-30 (implementation): the portal's `accounts.crypto.DEFAULT_KEY_PROVIDER` / `KMS_PROVIDER` stay the `accounts.crypto.*` strings (settings default, runbook, examples and the E004 hint unchanged); the package's own constants are `mytp_core.crypto.*`. The KMS provider is recognised by class through `mytp_core.crypto.uses_kms_provider()`.

## Review Triage Log

- Pass 1 (quick): 1 finding; high 1. Patched.
  - high, patch: mytp-core `tests/settings.py` was missing from the commit and tag v0.1.0 because the user's global gitignore excludes `settings.py`; a clean clone of the tag failed with ImportError, so the package CI would fail. Added `!tests/settings.py` to mytp-core's `.gitignore`, committed it, amended and re-tagged v0.1.0 (not pushed before); a clean clone of the tag now passes 163 tests. The vendored wheel is unchanged (test files are not in it). No other file in the portal is hidden by the global ignore.

## Verification

**Commands:**
- Portal full suite, from the repo root (non-secret test keys; venv `../.venv`; browser tests need the sandbox off): `export FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU="; export DATABASE_URL="sqlite:///$TMPDIR/x.sqlite3"; ../.venv/bin/python -m pytest -q -p no:cacheprovider --tb=line` -- expected: all pass except the 3 known PostgreSQL-only tests in `accounts/tests/test_crypto.py` (if they move to mytp-core, they run there on PostgreSQL).
- `../.venv/bin/python manage.py makemigrations --check --dry-run` -- expected: no changes.
- mytp-core: its test command in its README -- expected: all pass (PostgreSQL in CI).

**Manual checks (if no CLI):**
- After the merge, dev deploys, `mytp-dev-job@kmscheck` succeeds, and signup/login (encrypted fields) work.
