---
title: 'KMS key provider'
type: 'feature'
ticket: '12'
created: '2026-10-14'
status: 'built'
baseline_revision: 'c94b2bc0622e7858208bdb23454b7a4ff18ba7aa'
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

**Problem:** The field encryption keys (`FIELD_ENCRYPTION_KEYS`, `BLIND_INDEX_KEY`) sit in plain text in the server's `settings.env`. The encryption policy wants envelope encryption: data keys wrapped by a KMS service key that never leaves OVHcloud KMS. Story 1.15 set up the KMS and its client, and story 2.2 left a `KeyProvider` interface for this.

**Approach:**
- A `KMSKeyProvider` behind 2.2's interface. `settings.env` holds only KMS-wrapped data keys. The provider unwraps them through the KMS once per process and keeps the plain keys in memory only.
- A command creates new wrapped keys, and a documented rotation moves staging off its plain environment keys.

## Boundaries & Constraints

**Always:**
- **Settings:**
  - `FIELD_KMS_KEYS` holds `<key_id>:<wrapped key>,...` (first is current, the others only decrypt), as `FIELD_ENCRYPTION_KEYS` does today.
  - The wrapped keys are the KMS's opaque strings, unwrapped with `KMS_SERVICE_KEY_ID`.
  - `FIELD_KEY_PROVIDER=accounts.crypto.KMSKeyProvider` selects the provider.
  - Development and CI keep `EnvKeyProvider`.
- **Unwrapping:**
  - Each wrapped key is unwrapped with `mytp_publicsite.kms.KMSClient.decrypt_data_key` on first use and cached for the life of the process. The KMS is not called per value.
  - A plain key never reaches a log, an error message, Sentry, the database, the cache or a file.
  - A KMS failure raises an error naming the step and the key id only. It is not cached, so the next use retries.
- **Transition keys:** during a rotation the provider may also read keys from `FIELD_ENCRYPTION_KEYS`, for decryption only. A KMS key is always current.
- **New keys:** `manage.py generate_kms_field_key <key_id>` asks the KMS for a new 256-bit data key and prints only `<key_id>:<wrapped key>`. The plain bytes are discarded at once.
- **Rotation:**
  1. Put the new key first.
  2. Restart.
  3. Run `reencrypt_fields` (story 2.2), which rewrites every value and recomputes the blind indexes.
  4. Remove the old key.

  Existing data stays readable throughout.
- **`kmscheck`** also unwraps every configured `FIELD_KMS_KEYS` entry, printing key ids and ok or failed only, so the daily job alerts if a field key can no longer be unwrapped.
- **Deploy check:** `check --deploy` fails while `DEBUG` is off and `FIELD_KEY_PROVIDER` is not the KMS provider.
- Decision (user, 2026-10-14): staging moves off its plain keys by rotation.
  1. Generate new KMS data keys.
  2. Put them first, keeping the plain keys as transition keys.
  3. Restart.
  4. Run `reencrypt_fields`.
  5. Delete the plain keys from `settings.env` and from the password manager.
- Decision (user, 2026-10-14): the blind index key is also a KMS-wrapped data key, in `BLIND_INDEX_KMS_KEY` (a single wrapped value).
  - With the KMS provider it replaces `BLIND_INDEX_KEY`. `get_blind_index_key()` unwraps it through the KMS once per process, under the same rules as the field keys.
  - Switching it: set it, restart, then run `reencrypt_fields`, which recomputes every index. Email and phone lookups fail between the restart and the end of that run. That's accepted on staging, and the README says so.
  - `kmscheck` also unwraps it. `generate_kms_field_key` also makes it (e.g. `--blind-index`, printing the wrapped value only).
- Decision (user, 2026-10-14): disaster recovery rests on the KMS service key alone.
  - No plain-key escrow.
  - `deploy/README.md` states that deleting the service key, or losing the KMS domain, makes all personal data and its backups unrecoverable.
  - It also says to restrict key deletion in OVHcloud IAM to named administrators.
  - The password manager never holds a current plain key.
- Decision (user, 2026-10-14, replacing "key ids only"): the "MyTreatplan staging KMS" entry holds the key ids and the wrapped values of `FIELD_KMS_KEYS` and `BLIND_INDEX_KMS_KEY`, updated at every generation and rotation.
  - Superseded wrapped values are kept 30 days after a rotation, so older dumps can be restored.
  - Recovery therefore needs the KMS service key plus the wrapped values, from `settings.env` or, if the server is lost, from that entry.
- Decision (user, 2026-10-14): after the move, the old plain keys leave `settings.env` at once. The "MyTreatplan staging field keys" entry stays 30 days, until the last `backupdb` dump made with them expires, and is then deleted.
- Decision (user, 2026-10-14): the plan is kept whole, above the size guideline.
- **Docs:** `deploy/README.md` gets the staging steps (generate, configure, restart, re-encrypt, retire the plain keys) and the rotation procedure. The person runs every command that touches keys in their own terminal.

**Never:**
- Printing or logging a plain key. Storing wrapped keys in the database next to the data.
- Changing the stored value format (`enc:v1:<key_id>:...`) or the blind index algorithm.
- Production setup (story 1.12). It follows the same README steps with its own KMS domain.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Encrypt | KMS provider, one wrapped key | Values encrypted with its key id; one unwrap call per process | No error expected |
| Decrypt older key | Value on key `k1`, current `k2`, both wrapped | Decrypts; `k1` unwrapped on first use | No error expected |
| Transition | Value on env key `env1`; `FIELD_KMS_KEYS=k2:...`; `FIELD_ENCRYPTION_KEYS=env1:...` | Decrypts with `env1`; new values use `k2` | No error expected |
| Unknown id | Value on a key in neither list | `DecryptionError` naming the id, no value | No error expected |
| KMS down | Unwrap fails | Error naming the step and key id; next call retries | Not cached |
| Bad setting | Malformed `FIELD_KMS_KEYS` | `KeyConfigurationError` naming the setting, no value | No error expected |
| Generate | `generate_kms_field_key k2` | Prints `k2:<wrapped>` only | No error expected |
| Rotation | Add `k2` first, `reencrypt_fields`, remove `k1` | Every value on `k2`; lookups by email and phone work | No error expected |
| kmscheck | Field keys configured | Each key id reported unwrap ok; fails if one can't be unwrapped | No error expected |
| Deploy check | `DEBUG` off, `EnvKeyProvider` | `check --deploy` error | No error expected |
| Blind index via KMS | `BLIND_INDEX_KMS_KEY` set, KMS provider | Index computed with the unwrapped key; one unwrap per process; lookups work after `reencrypt_fields` | No error expected |

</frozen-after-approval>

## Code Map

- `accounts/crypto.py`:
  - `KeyProvider`, `EnvKeyProvider`, `parse_key_list`, `get_key_provider` (process cache, cleared on `setting_changed` via `KEY_SETTINGS`) and `validate_field_keys`, which `check` calls.
  - Add `KMSKeyProvider`. Parse `FIELD_KMS_KEYS` with the same id rules but opaque wrapped values. Unwrap lazily per key id under a `threading.Lock`. `current_key()` unwraps the first.
  - Add `FIELD_KMS_KEYS` to `KEY_SETTINGS`. `validate_field_keys` should not call the KMS for the KMS provider: parse only. The KMS is checked by `kmscheck`, so `check` and `migrate` work without network.
- `mytp_publicsite/kms.py`: `KMSClient.from_settings(settings)`, `decrypt_data_key(key_id, wrapped)`, `generate_data_key(key_id, name, size)`, and `KMSError(step, detail)`, whose messages are already safe. Reuse it; don't change its HTTP layer.
- `mytp_publicsite/management/commands/kmscheck.py`: add the field-key unwrap loop after the round trip.
- `accounts/management/commands/generate_field_keys.py`: the output style for the new `generate_kms_field_key`, which goes in `accounts/management/commands/`. `reencrypt_fields.py` already re-encrypts and recomputes indexes. Its docstring describes rotation with `FIELD_ENCRYPTION_KEYS`; update it to name both lists.
- `mytp_publicsite/checks.py`: add `mytp_publicsite.E004` beside E001–E003. The tests go in `mytp_publicsite/tests/test_settings_staging.py`.
- `mytreatplan/settings.py` (field encryption block, ~line 150): add `FIELD_KMS_KEYS` with its comment. `deploy/staging.env.example` and `settings.env.example`: commented entries.
- `deploy/README.md`:
  - section "Keys (KMS)" (~line 694): the rotation;
  - step 12 (field keys): the move to the KMS provider;
  - the IAM policy table: the existing actions suffice (`dataKey/create`, `dataKey/decrypt`).
- Tests:
  - new `accounts/tests/test_kms_provider.py` with a fake `KMSClient` (patch `KMSClient.from_settings`): every matrix row, plus "plain key never in logs or `repr`";
  - extend `mytp_publicsite/tests/test_kms.py` for `kmscheck`.

## Tasks & Acceptance

**Execution:**
- [x] `accounts/crypto.py` -- `KMSKeyProvider`, transition keys, settings parsing.
- [x] `accounts/management/commands/generate_kms_field_key.py`, `reencrypt_fields.py` docstring.
- [x] `mytp_publicsite/management/commands/kmscheck.py`, `mytp_publicsite/checks.py`.
- [x] `mytreatplan/settings.py`, env examples, `deploy/README.md`.
- [x] Tests -- every matrix row.

**Acceptance Criteria:**
- Given staging after the merge and the README steps, when the app runs with `FIELD_KEY_PROVIDER=accounts.crypto.KMSKeyProvider`, then signup and the admin read and write personal data, `kmscheck` reports each field key unwrapped, and `settings.env` no longer holds a plain field key.
- Given CI on PostgreSQL, when the suite runs, then every test passes.

## Implementation Notes

- The blind index key comes from the provider *class* (`check_blind_index_key()` parses
  only, `load_blind_index_key()` may call the KMS), so a broken `FIELD_KMS_KEYS` does not
  hide a broken `BLIND_INDEX_KMS_KEY` in the checks. `EnvKeyProvider` keeps reading
  `BLIND_INDEX_KEY`. `get_blind_index_key()` keeps a `cache_clear()` and caches only a
  success, under a lock.
- `check` / `validate_field_keys()` (WSGI startup) parse only (`check_keys()`); the KMS is
  called on first use and by `kmscheck`. A KMS failure raises `KeyUnavailableError`
  (setting, key id, KMS step), not chained, not cached.
- `KMS_*` settings joined `KEY_SETTINGS`, so changing them resets the cached provider.
- `conftest.py` blanks `FIELD_KMS_KEYS` / `BLIND_INDEX_KMS_KEY`, so a developer's
  `settings.env` cannot make tests use the KMS provider.
- Service key rotation is not supported by this provider (one `KMS_SERVICE_KEY_ID` unwraps
  every key); `deploy/README.md` says so and leaves it for a follow-up story.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-14):** high 1, medium 1, low 3, false 0, maybe-false 0. One intent gap, resolved by the user; four patches.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | The wrapped keys exist only in the server's `settings.env`, which nothing backs up, so losing the server makes all personal data and the off-region dumps unreadable even with the KMS intact. The README's "rests on the service key alone" was untrue | high | intent_gap | Resolved by the user (2026-10-14): wrapped values are copied to the password manager, and old plain keys are kept 30 days for older dumps. Docs, env example and runbook test were updated in place, with no code re-derivation since only wording changed. |
| 2 | Between the restart and the end of `reencrypt_fields`, a signup for a registered email creates a duplicate whose new `email_index` later collides (`unique=True`), so `reencrypt_fields` aborts | medium | patch | Real. The README now turns signups off for the window (`PORTAL_ENABLED=False`) and gives the recovery steps if it aborts anyway. |
| 3 | `staging.env.example` and README step 12 still presented plain keys in the password manager as the normal setup | low | patch | Reworded: the plain-key setup applies only before 2.12 and during the transition. A new environment starts with KMS keys. |
| 4 | `kmscheck` raised on a field-key failure before the certificate-expiry check, hiding "renew the certificate" | low | patch | Both messages are now collected and reported together. Test added. |
| 5 | `checks.py` duplicated the provider path | low | patch | It now imports `accounts.crypto.KMS_PROVIDER`. |

## Verification

**Commands:**
- `DATABASE_URL="sqlite:///$TMPDIR/x.sqlite3" ../.venv/bin/python -m pytest -q` (with test keys) -- expected: only the 3 known PostgreSQL-only tests fail; the browser test skips.
- `manage.py makemigrations --check` -- expected: no changes.
- Don't call the real KMS, download anything, or install anything outside the sandbox.

Done locally (2026-10-14) after the review patches, on SQLite: `pytest` 804 passed, 7 skipped (PyYAML, and the browser test without Playwright), 3 failed only because those tests use PostgreSQL-only raw SQL. `makemigrations --check` reports no changes. No real KMS was called. Pending: CI on PostgreSQL and the staging move (README "Move to the KMS provider").
