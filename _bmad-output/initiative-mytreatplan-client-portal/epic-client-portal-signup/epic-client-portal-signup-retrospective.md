---
epic: epic-client-portal-signup
date: 2026-10-23
verdict: accepted-with-open-items
criteria: declared
headless: false
---

# Retrospective: epic-client-portal-signup

## Epic summary

- **Epic:** `epic-client-portal-signup`, "Doctors sign up for the client portal" (initiative requirement I3; spec `spec-client-portal-signup/`, CAP-1 to CAP-11 and CAP-13).
- **Tickets:** 2.1–2.14, all `done` (`tickets.py status`). `pending_tickets` is empty; none at `built`.
- **Going-in concerns from the user:** none.
- **Re-run (2026-10-23, on master `86388ac`):** the user chose to keep the verdict rejected until R1 and S1 were fixed, then re-run. Both fixes are merged: R1 by PR #40 (`71c2079`), S1 by PR #41 (`ba378f6`). The resumed run checked them in the code and re-ran the signup browser suite; the earlier findings and views are otherwise unchanged. Tickets 2.1–2.14 are still all `done`.
  - The R1 plan (`../plan-fix-resume-takeover.md`) is `done`, but its manual staging check was never reported. The S1 plan (`../plan-fix-legal-unpublished.md`) is still `built`: merged and deployed, with its staging check not yet reported.
- **Plan ranges.** Ordered by baseline ancestry, the build order differs from the row order: 2.10 came before 2.8, and 2.12 before 2.11.

  | Ticket | Range | Commits |
  |---|---|---|
  | 2.1 | d291eea..3ed5399 | 3 |
  | 2.2 | 3ed5399..a73a473 | 3 |
  | 2.3 | a73a473..1aa2a40 | 3 |
  | 2.4 | 1aa2a40..4d3e58b (also holds 2.5's first, replaced flow) | 8 |
  | 2.5 | 4d3e58b..0f819dc | 4 |
  | 2.6 | 0f819dc..33a7e70 | 3 |
  | 2.7 | 33a7e70..fb18cfd | 4 |
  | 2.10 | fb18cfd..8c3861c | 3 |
  | 2.8 | 8c3861c..3c1a871 | 4 |
  | 2.9 | 3c1a871..c94b2bc | 4 |
  | 2.12 | c94b2bc..95d9a4c (holds PR #24, excluded) | 5 |
  | 2.11 | 95d9a4c..9af7b25 (holds 0a9bf3e, excluded) | 5 |
  | 2.13 | 9af7b25..103bc81 | 3 |
  | 2.14 | 103bc81..2245bbe (cut, recorded) | 3 |

  - The overall range is `d291eea..2245bbe`: 55 commits, 16 merges, all measured, no binary revisions.
  - Excluding the account-erasure fix (`b65faf9`, PR #24 merge `95d9a4c`, `0a9bf3e`) leaves 52 epic commits.
  - The cut is at `2245bbe` (2.14 marked done, 2026-10-18). Everything after it is epic-login, landing or QA work.
- **Evidence inventory:**
  - **Available:**
    - the epic file, with its Done when and Notes;
    - initiative I3;
    - 14 plans, each with a Review Triage Log and Verification;
    - one Plan Change Log entry (2.11);
    - `deploy/README.md`;
    - the QA report `../qa-spec-vs-code-2026-10-23.md`.
  - **Missing:**
    - session logs: none in the repository, so the process-lesson analysis relies on the plans only;
    - `## Code Review` blocks: none;
    - story files: none, since no ticket was pulled.

## Findings

Each finding carries a disposition: **fix now**, **defer**, or **accept**. The ones the review lenses found were re-checked in the source before routing.

### Cross-ticket review (adversarial, edge-case, verification gap)

The `bmad-review` skill wasn't invoked. A delegated reviewer ran the three lenses inline over the current signup code and the plans, weighted on the boundaries between tickets. Findings already in a plan's triage log or in the QA report were excluded. This is a narrowed scope.

| # | Finding | Source | Boundary | Disposition |
|---|---|---|---|---|
| R1 | **Resume takeover.** Anyone can create a pending row for someone else's unregistered email, choosing the password and ticking Terms, TPS and commercial consent. Its real owner can then only resume, after a code to the email. The resume copies the stranger's password hash (`RESUMED_FIELDS` includes `password`) and every legal choice with its original `decided_at` (`legal_choices.copy_to`), and `LegalStep.is_done` then skips the legal step. The account is created with consent the owner never gave and, if they leave the password fields empty, a password the stranger knows. | `signup/models.py:93,111-125`; `signup/views.py:574-606` (`_take_over`); `signup/steps.py:180-189`; test `signup/tests/test_legal_step.py:225` asserts the skip | 2.4 × 2.5 × 2.6 | **fixed** (re-run): PR #40, `71c2079`. `RESUMED_FIELDS` (`signup/models.py:95`) no longer holds `password`, `_take_over` copies no legal choices, and `copy_to` is gone. Tests: `test_resume_of_a_row_someone_else_made_keeps_none_of_their_choices`, `test_resume_after_the_legal_step_asks_it_again`, browser rows 5–7 |
| R2 | **The signup wizard serves logged-in users.** A logged-in Doctor who completes a signup with new details is silently logged out (`login(request, B)` flushes A's session), and a second account exists. | `signup/views.py:236-277,401`; no `is_authenticated` check anywhere in `signup/` | 2.1 × login 3.x | **fix now** |
| R3 | **Cancel after the idle timeout doesn't cancel.** The idle check handles the POST first, so `remove_pending` never runs; the row stays up to 24 h and the message invites the person to continue. | `signup/views.py:637-660`; `signup/retention.py:151` | 2.8 × 2.10 | **fix now** (small) |
| R4 | **Login code sends use up signup's "first IP block" marker without logging it**, since they run with no attempt. Later signup code-send blocks from that IP in the hour are then never logged. | `signup/limits.py:231-234`; `signup/steps.py:305-316`; `accounts/views.py` login code sends | 2.9 × login 3.2/3.5 | **defer** (log completeness only) |
| R5 | **`reencrypt_fields` handles each model in one transaction** with `select_for_update()` over all rows. Over `eventlog.Event`, which login page records make large, it locks every row for the whole pass and blocks the daily anonymization. | `accounts/management/commands/reencrypt_fields.py:39-46` | 2.2/2.12 × 2.3 × login 3.7 | **defer** to production readiness (batch it before the first production rotation) |
| R6 | **Verification gaps** (confirmed by grep): no tests for a resume by another person (**added** by PR #40), a logged-in user in the wizard, the shared code-send IP marker between signup and login, codes across purposes on Twilio's single service (only the fake is tested), `reencrypt_fields` over event rows, or a cancel POST after the idle timeout. | `signup/tests/`, `accounts/tests/`, `eventlog/tests/` | several | **fix now** with R1–R3; the others **defer** |

### Spec-to-implementation reconciliation

The source is the QA report `../qa-spec-vs-code-2026-10-23.md`: signup parts A and B, about 58 divergences, of which 48 are recorded decisions.

| # | Finding | Source | Disposition |
|---|---|---|---|
| S1 | **The legal step passes without Terms or Conditions** when no version is published (QA A1). | `signup/forms.py:369-384` | **fixed** (re-run): PR #41, `ba378f6`. `LegalForm.clean` refuses with `documents_missing` while either has no published version (`signup/forms.py:392-400`), and `_create_account` refuses without accepted Terms and Conditions (`signup/views.py:388-394,412`). Tests in `signup/tests/test_legal_step.py` |
| S2 | **Step 1 can't be submitted without JavaScript** or with a script blocker: a missing Turnstile token counts as failed (QA A2). | `signup/captcha.py:55-56` | **defer**: needs a decision (accept, or add a no-JS path) |
| S3 | **`OTP not delivered` is never logged**; delivery isn't tracked (QA A3). Done-when 5's test for that row checks the behaviour, not the logged reason. | failure policies, row "Code not received" | **accept**, and reconcile the spec (no webhooks, by the 2.11 decision) |
| S4 | **Backups keep cancelled or purged signups** and pre-anonymization log values for up to 30 days (QA A8). | `deploy/README.md` backups | **defer**: a stated policy with the lawyer |
| S5 | **Small text and labelling differences** (QA A10–A14): "new code sent", the "E-mail" label, "Cancel registration" casing, the "Others" field without JS, the 1 h purge lag. | QA report | **accept**, or fix in a sweep |
| S6 | **About 48 decided deviations** never brought into the spec: email-first step 1; 4 wizard steps; 4 sends an hour per purpose; WhatsApp off; fail-open external checks; logging exclusions; `abandoned` as an outcome; the encryption-policy Logs row now true for signup only; erasure freeing the email; CAP-7 now has a second factor. | QA report part B | **reconcile the spec** (proposed) |

### Aggregate views

- **Architecture delta.** Narrowed: built with an AST import scan at both endpoints, since pydeps isn't installed.
  - New app edges: signup → accounts (`http`, `crypto`, `models`, `password_validation`), signup → eventlog, signup → legal, eventlog → accounts (`EncryptedJSONField`), and `mytreatplan/wsgi.py:21` → `accounts.crypto`.
  - `mytp_publicsite/checks.py:7-8` imports signup constants, so the public-site app depends on the portal app for its checks (from 2.13).
  - `accounts` ↔ `mytp_publicsite` both import each other's KMS pieces, but there's no cycle at module level (2.12).
  - No app code imports from tests. Signup is acyclic internally. **Accept**: these are documented dependencies. The `mytp_publicsite` → `signup` edge is noted for whoever restructures the checks.
- **Duplication map.**
  - What remains at the span's end: three `_now()` copies (kept by 3.11, decision 1); two ways to classify a contact (`limits._kind` versus `verification.contact_index`); two email normalizations with different semantics (`signup/forms.py:38` versus `accounts/crypto.py:400`); two HTTP client paths (kept by 2.13, decision 1(a)).
  - The refuse helpers, the lock loop and the IP-block recorder were already merged by 2.13 and 3.11.
  - **Defer** the contact-kind and normalization pair to a future sweep. Accept the rest as decided.
- **Size growth.**
  - **`signup/views.py`, 701 lines, 36 functions:** the wizard, resume, idle and cancel, the email check and session helpers. It's the largest app file and a candidate for a split like 3.11's `login_guard` (**defer**).
  - **`accounts/crypto.py`, 599 lines:** the providers, the fields, the blind index, a check and a backfill (**accept**, as one cohesive concern).
  - **`signup/forms.py`, 471 lines:** it also holds the event-log reason constants (**defer**: move the constants with the views split).
  - **`signup/steps.py`, 453 lines** (**accept**).
- **Pattern divergence.**
  - Event names are a mix of constants and inline literals (`views.py:162,175,301,422,427`; `steps.py:342,353`).
  - Two log-message styles (key=value versus prose).
  - Inconsistent `_` prefixes on cross-module helpers.
  - Story references written several ways (left by 2.13, decision 1(e)).
  - **Defer** the event-name constants to a sweep; accept the rest.

## Behavior verification

On master at `ef7eebe` (2026-10-23), locally on SQLite, with Chromium run outside the sandbox:
- `pytest -m browser signup/tests/browser`: **16 passed**. That covers the success signal in EN and ES, every failure-policy row (rows 1–10 and 12–14) and row 11 (connection).
- The full suite: **1,280 passed**, 3 failed. The failures are the known PostgreSQL-only tests in `accounts/tests/test_crypto.py`.
- CI on PostgreSQL 17 passed on every later merge (last: PR #39, 1,283 passed).

On staging, recorded in the plans' completion notes by the user:
- a full signup in EN and ES with real Twilio email and SMS codes (2.11, 2.14);
- KMS field keys with `kmscheck` ok and the data re-encrypted (2.12);
- the retention jobs ran (2.10).

Re-run, on master at `86388ac`:
- `pytest -m browser signup/tests/browser`: **16 passed**, including failure-policy rows 5–7 with the new resume behaviour.
- The S1 branch's full suite: 1,287 passed, plus the 3 known failures; CI on PostgreSQL 17 passed for PRs #40 and #41 and their master merges (1,284 passed on #40's master run).
- Not exercised on staging: the R1 takeover check and the S1 normal-path check were not reported.

**Not exercised in this retrospective:** the R1 takeover scenario and the R2 logged-in signup, live. Both were confirmed by reading the code.

## Previous-retro follow-through

The previous epic in build order, `epic-landing-integration`, has **no retrospective file**. There was nothing to follow through on because no file exists, not because no items were outstanding.

## Action items

Every item is proposed; nothing is applied yet.

| # | Action | Kind | Owner |
|---|---|---|---|
| 1 | **Done** (PR #40). Fix **R1**. A resume must not carry over the original row's password or legal choices: the person sets a password and goes through the legal step again. Add tests for a resume of a row someone else created. | remediation (bug story, `bmad-build`) | dev |
| 2 | **Done** (PR #41). Fix **S1**: refuse the legal step, or fail `check --deploy`, while no Terms or Conditions version is published. | remediation | dev |
| 3 | Fix **R2**: the signup wizard sends a logged-in user to welcome (or asks them to log out first). | remediation | dev |
| 4 | Fix **R3**: a cancel POST after the idle timeout still removes the pending registration. | remediation | dev |
| 5 | Decide **S2** (signup without JavaScript): accept and say so, or add a path. | decision | user |
| 6 | Write the **backup and server-log retention** policy (S4, QA A9) with the lawyer and ops. | policy | user + lawyer |
| 7 | Before the first production key rotation, make `reencrypt_fields` work in batches over large tables (**R5**). | remediation (production readiness) | dev |
| 8 | **Reconcile the signup spec** with the recorded decisions (S3, S6) through `bmad-spec`, including the encryption-policy Logs row. | spec reconciliation | user + `bmad-spec` |
| 9 | A future sweep: event-name constants, one contact-kind helper, one email normalization, and splitting `signup/views.py` (aggregate views). | remediation (sweep) | dev |
| 10 | Make the code-send IP block marker per flow, or log login blocks (**R4**). | remediation (low) | dev |

**Process lessons:**
- **Cross-story interactions were the gap.** Each story review saw only its own diff. The worst defect (R1) sits where three stories meet (2.4 × 2.5 × 2.6), and a test even asserted the risky behaviour. For future epics: add a cross-story review pass before the epic closes, as this retrospective did, and write a "who can reach this state?" line into plans that copy or carry data between flows.
- **Spec drift.** Story decisions changed the spec about 48 times without updating it. Fold each decision into the spec with `bmad-spec` when it's made, not at the end.

## Acceptance verdict

**Verdict: accepted-with-open-items** (human decision, 2026-10-23), criteria declared.

The user overrode the machine verdict (**rejected**, from criterion 6) once R1 and S1 were fixed. The open items:
- **Criterion 6**, production with epic-login: tracked by 3.13, which waits on 1.7 and 1.12.
- **Fix now:** R2 and R3 (action items 3 and 4).
- **Deferred:** R4, R5, S2 and S4, plus the spec reconciliation and the sweep (action items 5–10).
- **Staging checks not reported:** R1 (PR #40) and S1 (PR #41).

The epic file's Done when, criterion by criterion:
1. **Staging journey, EN/ES, both codes, logged in, the end-to-end suite proves it:** met (2.14 suite; staging runs recorded in 2.11 and 2.14).
2. **A registered email or mobile is blocked with a recovery link; a pending one resumes only after a code:** met in the letter (tests in `test_duplicates.py`). R1 shows the resume is unsafe in substance.
3. **Personal data is ciphertext with KMS keys; lookups go through the blind index:** met (2.2, 2.12; KMS on staging).
4. **Abandoned for 24 h leaves no personal data; logs anonymized at 30 days and deleted at 2 years:** met in the application (2.10 jobs), with the backup caveat (S4) and the 1 h purge lag.
5. **Every failure-policy row has a passing automated test:** met (2.14 browser suite plus client tests). S3 notes that one row's logged reason is untested.
6. **Goes to production together with epic-login:** **not met.** Production doesn't exist yet (1.7 and 1.12 are open, and 3.13 waits on them).

**Evidence for the call (re-run):**
- Criterion 6 is still unmet: production doesn't exist yet.
- The blocking findings R1 and S1 are fixed (PRs #40 and #41), so criterion 2 is now met in substance too.
- Still open: R2 and R3 (fix now, not blocking), and the deferred R4, R5, S2 and S4.
- Every ticket is finished.

The criterion-6 dependency is by design: the epic ships with epic-login (3.13). With no human decision the machine verdict stays **rejected**.

## Open questions

- **S2:** is signup without JavaScript a requirement?
- **S4:** is a 30-day backup retention acceptable under the lawyer's reading of "cleared at once"?
