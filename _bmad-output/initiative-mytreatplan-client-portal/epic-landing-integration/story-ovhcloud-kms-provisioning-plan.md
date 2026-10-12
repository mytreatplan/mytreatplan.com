---
title: 'OVHcloud KMS provisioning'
type: 'feature'
ticket: '15'
created: '2026-10-12'
status: 'done'
baseline_revision: '8d3c70517221554e83e8b42071af62a9ce24f375'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The encryption policy requires personal fields to be encrypted with data keys wrapped by a master key in OVHcloud KMS (France). Signup 2.12 builds the key provider on it, but there is no KMS, no master key and no server access to one yet, and nobody has shown the staging server can use it.

**Approach:** Provision an OVHcloud KMS domain and a staging service key, give only the staging server credentials for it, and add a `manage.py kmscheck` command that wraps and unwraps a test data key through the KMS REST API. Document access, credential renewal and key rotation for 2.12 and production (1.12).

## Boundaries & Constraints

**Always:**
- **KMS REST data plane:** `https://<region>.okms.ovh.net/api/<okmsId>/v1/servicekey/<keyId>/...`.
  - `POST .../datakey` with `{"name": ..., "size": 256}` returns `key` (wrapped, base64) and `plaintext` (base64).
  - `POST .../datakey/decrypt` with `{"key": ...}` returns `plaintext`.
- **`kmscheck`:**
  - Generates a data key, unwraps it, and checks the unwrapped bytes equal the original.
  - Prints the service key's id and state, the round-trip result and the credential's expiry date. It never prints, logs or sends to Sentry the plaintext, the wrapped key or credential material.
  - Exits non-zero on any failure, with a message naming the step (connect, auth, generate, unwrap, mismatch).
- **Settings:** env-driven via decouple, all empty by default: `KMS_ENDPOINT`, `KMS_OKMS_ID`, `KMS_SERVICE_KEY_ID`, plus `KMS_CLIENT_CERT`, `KMS_CLIENT_KEY` and the optional `KMS_CA_FILE`.
  - When a setting is missing, `kmscheck` stops before calling the KMS and names the key.
  - A shared `mytp_publicsite`-level client module holds the HTTP calls, so 2.12 can reuse them. Stdlib only (`urllib`/`ssl`/`json`), with no new dependency.
- **Credential files** live only in `/srv/mytp/shared/` (owner `mytp`, mode `600`). `deploy/staging.env.example` lists the keys with no values. The certificate's private key has no backup copy: it is created on the server and never leaves it; if it is lost, a new certificate is issued. The runbook says how to renew and replace them.
- **Runbook (`deploy/README.md`):**
  - create the KMS domain and the staging service key (`oct`, 256, encrypt/decrypt operations);
  - create the server credential and an IAM policy limited to this domain's data-key and get operations;
  - install the credentials and settings;
  - run `kmscheck`;
  - credential renewal;
  - key rotation, for 2.12 to implement:
    - create a new service key;
    - point `KMS_SERVICE_KEY_ID` at it;
    - re-wrap old data keys (unwrap with the old key, encrypt with the new one);
    - deactivate the old key only once nothing uses it, and never delete it while any data might.
- Tests mock the KMS HTTP calls. They cover the settings check, the round trip, the error paths, and that no plaintext, wrapped key or credential appears in output or exception text.
- Decision (user, 2026-10-08): OVHcloud KMS, France region, chosen with hosting; envelope encryption per `encryption-policy.md`.
- Decision (user, 2026-10-12): the KMS domain goes in the France region nearest GRA that the panel offers (GRA if listed, else RBX or SBG), picked at setup. The code isn't region-specific: the region is only in `KMS_ENDPOINT`. The runbook records the region chosen.
- Decision (user, 2026-10-12): the server authenticates with an **access certificate (mTLS)**. Settings: `KMS_CLIENT_CERT` and `KMS_CLIENT_KEY` (paths to the PEM files in `/srv/mytp/shared/`) and an optional `KMS_CA_FILE` (default: system CAs). The IAM policy is attached to the certificate's identity.
- Decision (user, 2026-10-12): `kmscheck` runs **daily** as a scheduled job (`kmscheck | kmscheck | *-*-* 04:00:00 UTC` in `deploy/jobs.conf`). It also fails when the client certificate expires within **30 days**, so Sentry alerts ahead of expiry. The expiry is read without a new Python dependency (e.g. `openssl x509 -noout -enddate`, as `openssl` is on Debian).
- Decision (user, 2026-10-12): the staging service key uses protection level **`SOFTWARE`**; production (1.12) may choose `HSM`.
- Decision (user, 2026-10-12, at review): no password-manager copy of the certificate's private key (server only); a lost key means issuing a new certificate. Replaces the earlier "backup copy in the team password manager" rule.

**Never:**
- The key provider, field encryption or blind indexes (signup 2.12). Production keys or credentials (1.12).
- Credentials, keys or plaintext data keys in the repo, logs, Sentry or chat. Deleting or deactivating keys from code.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Cert expiring | Client certificate expires within 30 days | Round trip still attempted; exit non-zero naming the expiry date | Non-zero, "certificate expires" |
| Round trip | Settings and credentials valid | Prints key id, state `active`, "round trip ok", credential expiry; exit 0 | — |
| Settings missing | e.g. `KMS_SERVICE_KEY_ID` empty | No KMS call | Non-zero, names the key |
| Auth refused | KMS returns 401/403 | — | Non-zero, "auth" step, HTTP status, no credential text |
| Key not usable | Key deactivated (KMS 400) or unknown (404) | — | Non-zero, names the step and status |
| Mismatch | Unwrapped bytes differ | — | Non-zero, "mismatch"; neither value printed |
| Unreachable | DNS/TLS/timeout | — | Non-zero, "connect" step |

</frozen-after-approval>

## Code Map

- `mytreatplan/settings.py` -- the decouple pattern; the `BACKUP_*` block (~line 74) is the model for the `KMS_*` settings.
- `mytp_publicsite/management/commands/backupdb.py` -- the model for settings checks, error messages without secrets (`scrub`, no exception chaining) and the command structure. Reuse its style, not its code.
- `mytp_publicsite/management/commands/runjob.py`, `deploy/jobs.conf` -- the scheduled-job route; add the `kmscheck` line (format checked by `test_monitoring.py`). `provision.sh` installs the timer on its next run.
- `deploy/staging.env.example`, `deploy/README.md` -- add step 11 (KMS) after `### 10. Off-region backups`, plus a `## Keys (KMS)` section after `## Backups and restore`.
- `deploy/provision.sh` -- `ensure_dir` and `install_file` helpers. No change expected: credential files are copied by hand per the runbook.
- Tests: `mytp_publicsite/tests/` (no DB). `test_backups.py` shows the fake-client and no-secret-leak patterns.

## Tasks & Acceptance

**Execution:**
- [x] `mytp_publicsite/kms.py` -- the minimal KMS REST client (generate data key, unwrap data key, get service key), with the chosen auth and errors without secrets.
- [x] `mytp_publicsite/management/commands/kmscheck.py` -- the check described above.
- [x] `mytreatplan/settings.py`, `deploy/staging.env.example` -- the `KMS_*` settings.
- [x] `deploy/jobs.conf` -- the daily `kmscheck` line.
- [x] `deploy/README.md` -- step 11 and the "Keys (KMS)" section.
- [x] `mytp_publicsite/tests/test_kms.py` -- every matrix row, plus the no-leak checks.

**Acceptance Criteria:**
- Given the runbook is done, when `kmscheck` runs on the staging server as `mytp`, then it reports a successful round trip with the staging service key.
- Given the credential files, then they exist only in `/srv/mytp/shared/` on the server (and the password manager), never in the repo.
- Given the runbook, a person can follow its rotation and renewal steps without other sources.

## Implementation Notes

- IAM actions in the runbook come from OVHcloud's "OKMS authentication methods" guide: `okms:apikms:serviceKey/get`, `serviceKey/dataKey/create`, `serviceKey/dataKey/decrypt`. The access certificate is issued from a CSR made on the server (key born there); it has no backup copy (decision at review).
- Step names in errors: settings, cert, connect, auth, get, generate, unwrap, mismatch. `auth` also covers a certificate/key that cannot be loaded and a TLS alert refusing the client certificate, besides HTTP 401/403. HTTP error bodies are never echoed; only `error_code` and `request_id` when they look like identifiers.
- Extra guards: KMS ids must be UUID-like (path safety), `KMS_ENDPOINT` must be a bare https origin, the private key file must not be group/other-accessible, an encrypted private key fails instead of prompting.
- Real mTLS round trip checked locally against a throwaway HTTPS server requiring client certificates (not committed).
- Rotation note for 2.12: keys re-wrapped with `encrypt` are unwrapped with `decrypt`, not `datakey/decrypt`; the provider must record how each data key was wrapped and the policy then needs `serviceKey/encrypt` and `serviceKey/decrypt`.

## Plan Change Log

- 2026-10-12, at staging setup: the frozen runbook item "encrypt/decrypt operations" was not enough. Data keys need the service key's `wrapKey`/`unwrapKey` operations (`generate` failed with HTTP 400 otherwise), and operations cannot be changed after creation, so the staging key was recreated. Runbook step 11.2 now lists wrapKey, unwrapKey, encrypt and decrypt (follow-up PR #11).

## Review Triage Log

Pass 1 (quick lens, 2026-10-12): high 0, medium 1, low 5, false 0, maybe-false 0.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | Runbook still has region placeholders; staging round trip (AC1) not shown | low | patch | The region is now known (user chose GRA, endpoint `eu-west-gra`). Patch: runbook records it. AC1 is verified on staging after the merge (runbook step 11); certificate expiry is recorded then. |
| 2 | Step 11.5 example `# KMS_CA_FILE=   (empty: system CAs)` has text after the value | medium | patch | Confirmed; decouple keeps trailing text (this broke the first backup run). Patch: explanation on its own line. |
| 3 | `check_settings` only `stat`s credential files, so an unreadable file fails later as `auth`/`cert` | low | patch | Confirmed. Patch: `os.access(R_OK)` check at the `settings` step, plus a test. |
| 4 | `KMS_ENDPOINT` with a fragment or password passes the bare-origin guard | low | patch | Confirmed (`urlsplit` keeps path and query empty). Patch: reject both, plus tests. |
| 5 | `openssl` CLI needed by `kmscheck` but not in `provision.sh` `PACKAGES` | low | patch | Confirmed; present only via `ca-certificates`. Patch: added to `PACKAGES`. |
| 6 | Client-certificate refusals sent as `HANDSHAKE_FAILURE` / `ACCESS_DENIED` alerts reported as `connect` | low | patch | Confirmed in `_network_step`. Patch: classified as `auth`, plus test cases. |

## Verification

Done locally (2026-10-12) after the review patches: `pytest` 272 passed, 6 skipped (PyYAML workflow tests, run in CI); `python manage.py check` no issues; `bash -n deploy/*.sh` ok. Implementer's run of `kmscheck` against a local mTLS test server: round trip ok; untrusted certificate and missing CA failed as expected. Staging (2026-10-12, after the merges of PRs #10 and #11, `provision.sh` re-run and runbook step 11):
- KMS domain in GRA (`eu-west-gra.okms.ovh.net`); staging service key `mytp-staging`, SOFTWARE, with wrapKey/unwrapKey/encrypt/decrypt (recreated, see the change log).
- Access certificate `mytp-kms-staging` made from a CSR on the server; the key and certificate are only in `/srv/mytp/shared/` (mode 600, owner `mytp`). Expires 2027-10-12 17:07 UTC.
- The IAM policy needed to be created at account level with the product type Key Management Service and the KMS as its resource; runbook steps 11.3-11.4 now say so.
- `kmscheck` printed `state unknown` at first because the API returns the state under `attributes`; fixed in PR #11.
- `mytp-job@kmscheck` (timer daily at 04:00 UTC): round trip ok. Sentry Crons monitor `mytp-staging-kmscheck` has its check-in, and notifications go to devops@mytreatplan.com.

**Commands:**
- `pytest` -- expected: all pass
- `python manage.py check` -- expected: no issues
- `bash -n deploy/*.sh` -- expected: no errors

**Manual checks (if no CLI):**
- On staging after the runbook: `sudo -u mytp ./venv/bin/python manage.py kmscheck` reports a round trip ok and the certificate expiry; after re-running `provision.sh`, `systemctl list-timers 'mytp-job@*'` lists `kmscheck`; Sentry Crons shows its ok check-in.
