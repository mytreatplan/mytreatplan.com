---
title: 'REGION profile module'
type: 'feature'
ticket: '6'
created: '2026-10-30'
status: 'built'
baseline_revision: 'a07788629b9996f5fa8ecc2f2cb3aadc89882e27'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-mytreatplan-portal-admin/architecture-portal-admin/architecture-portal-admin.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The portal will run as one codebase per region (EU on .com, UAE on .ae, US later), and AD-13 requires every region-specific value to come from one profile selected by `REGION`, never from region-name checks scattered through the code. Today the values are hard-coded (`TIME_ZONE = 'Europe/Madrid'`, `LANGUAGES` en/es, one company in the footer).

**Approach:** Add a `regions` app with one profile per region (eu, ae) holding domains, region company, default time zone, languages, currencies, object-storage endpoint and bucket, KMS endpoint, gateway ids, patient-code prefix and invoice instance prefix, selected by `REGION` in settings.env (default `eu`). settings.py takes `TIME_ZONE`, `LANGUAGES` and the default `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS`/`SITE_URL` from it, settings.env values still overriding. `/healthz` adds `region: <REGION>` (mytp-core 0.2.1). A test fails on region-name comparisons outside the module.

## Boundaries & Constraints

**Always:** env values in settings.env keep overriding the profile defaults, so dev (dev.mytpdso.com) keeps working unchanged; secrets never live in the profile; the profile is plain data plus a lookup; dev's behaviour today (Europe/Madrid, EN and ES) is unchanged with `REGION=eu`.

**Never:** no footer or legal-text change (epic-regions 2.6); no country map (2.1); no dev-ae site (1.7); no gateway code (epic-payments).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| EU | `REGION=eu` (or unset) | TIME_ZONE Europe/Madrid, languages en+es, currencies EUR+USD, patient prefix EU, invoice prefix EU | — |
| UAE | `REGION=ae` | TIME_ZONE Asia/Dubai, languages en, currencies AED+USD, prefixes AE | — |
| Override | `REGION=eu` and `ALLOWED_HOSTS=dev.mytpdso.com` in settings.env | env value used | — |
| Unknown | `REGION=us` before a US profile exists | Django refuses to start, naming the valid regions | Clear error |
| Guard | Code outside `regions/` compares to `'eu'`/`'ae'` | Guard test fails naming the file and line | — |

</frozen-after-approval>

## Code Map

- `mytreatplan/settings.py` -- `ALLOWED_HOSTS` :38, `CSRF_TRUSTED_ORIGINS` :40, `SITE_URL` :45, `SITE` :52, `LANGUAGES` :436, `TIME_ZONE` :445. Import the profile from a module with no Django imports (settings load before apps).
- New `regions/` app -- `profiles.py` (dataclass per region, `get_profile(code)`), `apps.py`, tests (profile values, settings wiring, guard test scanning `*.py` outside `regions/`, migrations and tests for quoted `'eu'`/`'ae'` comparisons).
- `../mytp-core/mytp_core/views.py` `healthz` :18-32 -- add `region: <REGION>` when `settings.REGION` is set; tag `v0.2.1`, vendor it (`scripts/vendor-mytp-core.sh v0.2.1`).
- `deploy/staging.env.example` -- document `REGION=eu`.

## Design Notes

Profile values (defaults; staging overrides domains through settings.env):

| | eu | ae |
|---|---|---|
| domains | mytreatplan.com, www.mytreatplan.com | mytreatplan.ae, www.mytreatplan.ae |
| region company | MyTPDSO Limited · Company number 815526 (Ireland) | MyTPDSO L.L.C-FZ · License 2542361.01 (Dubai) |
| default time zone | Europe/Madrid | Asia/Dubai |
| languages | en, es | en |
| currencies | EUR, USD | AED, USD |
| storage endpoint | https://s3.gra.io.cloud.ovh.net (bucket from env) | to be set at epic-uae-launch (env) |
| KMS endpoint | from env (OVH, France) | from env (UAE provider) |
| gateways | redsys, revolut, bank_transfer | (none until epic-uae-launch) |
| patient-code prefix | EU | AE |
| invoice instance prefix | EU | AE |

## Tasks & Acceptance

**Execution:**
- [x] `regions/` -- profiles, lookup, app config, tests including the guard test.
- [x] `mytreatplan/settings.py`, `deploy/staging.env.example` -- `REGION` setting, profile-driven defaults with env overrides.
- [x] `../mytp-core` -- healthz region line, tests, tag `v0.2.1` locally (no push); `vendor/`, `requirements.txt`.

**Acceptance Criteria:**
- Given the merge deployed to dev with `REGION=eu`, when `/healthz` is fetched, then it shows `ok`, `site: dev` and `region: eu`, and dev behaves as before.
- Given a test settings run with `REGION=ae`, when settings load, then the UAE values apply.

## Implementation Notes

- `../mytp-core` is the local clone at v0.2.0 (pushed). Commit and tag `v0.2.1` there; do not push either repo.

## Plan Change Log

- 2026-10-30 implementation: with DEBUG on and ALLOWED_HOSTS unset, the default also allows Django's own DEBUG list (.localhost, 127.0.0.1, [::1]) (before, an empty list let Django allow them), so local dev is unchanged. An explicitly empty env value still overrides the profile (as decouple reads it). `mytp_publicsite/tests/test_platform.py::test_missing_secret_key_refuses_to_start` now copies `regions/profiles.py` beside the copied settings; `test_monitoring.py` healthz expects `region: eu`; `test_deploy.py` checks `REGION=eu` in the staging example.
- 2026-10-30 review fixes: the profile drops `storage_bucket` and `kms_endpoint` (env settings only); `STORAGE_ENDPOINT` setting defaults to the profile's endpoint, env-overridable; the guard flags every exact `'eu'`/`'ae'` string literal (constants, dict keys), not only comparisons; `settings.env.example`, `mytreatplan/urls.py` and `deploy/README.md` mention REGION / `region: eu`.

## Review Triage Log

- Pass 1 (quick): 5 findings; medium 1, low 4. All patched.
  - medium, patch: `storage_bucket` and `kms_endpoint` were always '' in the profile while the real values lived in separate settings (two sources of truth). Removed from the profile; `STORAGE_ENDPOINT` is a setting whose default comes from the profile.
  - low, patch: the DEBUG local-hosts default narrowed Django's own fallback (`.localhost`). Now exactly `['.localhost', '127.0.0.1', '[::1]']`.
  - low, patch: the guard caught only literal comparisons. It now flags any `'eu'`/`'ae'` string literal outside `regions/`, migrations and tests, covering constants and dicts.
  - low, patch: `/healthz` docs (urls.py comment, README curl examples) now show `region: eu`.
  - low, patch: `settings.env.example` gained `REGION=eu`.
- After patches: portal full suite 1493 passed, 3 known PostgreSQL-only failures; `check` clean. The Irish company as .com default was confirmed by the user (2026-10-30).

## Verification

**Commands:**
- Portal full suite, from the repo root (non-secret test keys; venv `../.venv`; browser tests need the sandbox off): `export FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU="; export DATABASE_URL="sqlite:///$TMPDIR/x.sqlite3"; ../.venv/bin/python -m pytest -q -p no:cacheprovider --tb=line` -- expected: all pass except the 3 known PostgreSQL-only tests in `accounts/tests/test_crypto.py`.
- mytp-core tests from a clean clone of `v0.2.1` -- expected: all pass.
- `../.venv/bin/python manage.py check` -- expected: no issues.
