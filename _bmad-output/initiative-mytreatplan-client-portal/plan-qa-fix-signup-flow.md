---
title: 'QA fix 2: signup flow'
type: 'bugfix'
ticket: ''
created: '2026-10-23'
status: 'built'
baseline_revision: 'b93f5a151d212c900fa965da5ab7bf5dda0012ff'
route: 'full'
route_source: 'auto'
risk: 'low'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The specs-vs-code QA (`qa-spec-vs-code-2026-10-23.md`, items A10, A11 and A17; A1 was fixed by the signup retrospective's S1) found:
- **A10:** after a resend the page says "We have sent you a new code", but a resend within the code's 10 minutes repeats the same code, as Twilio does.
- **A11:** the support contact shows on a plain 10-minute expiry, and never after checks that couldn't be made. The user decided it shows after 5 wrong codes, the hourly limit, a failed send, or 2 consecutive checks that couldn't be made, but not after a plain expiry.
- **A17:**
  - the "How did you find us" choice reads "E-mail", where the spec says "e-mail";
  - the "Others" text field stays visible without JavaScript, although it's only for "Others".

**Approach:**
- **A10:** the resend messages no longer promise a new code; the wording is decision 1.
- **A11:** the code pages count, per page in the session, the wrong codes and the consecutive checks that couldn't be made, both since the last successful send. The support contact shows when:
  - "no longer valid" follows 5 wrong codes;
  - the hourly limit is reached;
  - a send fails;
  - 2 consecutive checks couldn't be made.

  A plain expiry ("no longer valid" without 5 wrong codes) doesn't show it.
- **A17:**
  - the label becomes "e-mail" (EN; the Spanish entry is updated to match);
  - the "Others" wrapper is rendered `hidden` by the server unless "Others" is the selected value, so it's hidden without JavaScript too. The script still toggles it.

## Boundaries & Constraints

**Always:**
- The same rules apply to the signup code pages and the resume page. The login code page (`accounts/templates/accounts/login_code.html`, through `code_help_context`) follows the same support rules, since it shares the helper.
- No change to limits, codes, events or what is sent. A11 changes only when the support contact shows. A10 changes only the text.
- EN and ES for every changed text.
- The counts live in the session state these pages already keep (`signup.steps` code state), and reset on a successful send.

**Never:**
- New dependencies, or migrations (the label is a choice label: no data change).
- Changes to `signup/views.py` beyond what A11 needs, if any.

**Decisions (2026-10-23):**
1. A10: after a sent resend the message is "We have sent you the code again." and, for "Send by SMS instead", "We have sent you the code again by SMS." (ES translated to match).
2. (2026-10-23, at review) A11, how wrong codes are counted. Replaces the Approach's "since the last successful send" for the wrong-code count:
   - only real "not correct" answers count (`invalid_code` from a check that reached the provider), not malformed input, which the provider never sees;
   - the count is kept across resends while it's the same code, and resets only when a new code is issued: a send more than 10 minutes (`CODE_TTL_SECONDS`) after the current code's first send;
   - the support contact shows when "no longer valid" follows 4 "not correct" answers on that code (the provider's 5th wrong attempt). Any other "no longer valid" is a plain expiry, with no support contact.

   The consecutive check-failure count is unchanged (2 in a row; ended by a check that is made or a successful send).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| A10 resend | "Send a new code" sent | "We have sent you the code again." | — |
| A10 SMS | "Send by SMS instead" sent | "We have sent you the code again by SMS." | — |
| A11 plain expiry | A code entered after its 10 minutes, no wrong codes before | "no longer valid"; no support contact | — |
| A11 5 wrong | 5 wrong codes | "no longer valid"; support contact shown | — |
| A11 hourly | Hourly limit reached | Support contact shown (as now) | — |
| A11 send failed | A failed send | Support contact shown (as now) | — |
| A11 checks failed | 1 check that couldn't be made / 2 in a row | No support / support shown | — |
| A11 reset | A successful send after any of these | Counts reset; no support | — |
| A11 login page | The same rules on the login code page | Same results | — |
| A17 label | Step 1's how-found choices | "e-mail" (ES updated) | — |
| A17 Others, no JS | Step 1 with how-found not "Others" | The Others field is `hidden` in the HTML | — |
| A17 Others chosen | Step 1 re-rendered with "Others" selected (e.g. after an error) | Others field visible | — |
| Spanish | `/es/` | Spanish texts | — |

</frozen-after-approval>

## Code Map

- `signup/steps.py:366-395` `code_help_context`: `expired` (`code_expired` on the form), `notice`, `failed`, and the support flag (`support_email` shown on hourly / failed / expired). Change the flag to the decision rules, using the counts.
- `signup/steps.py:219-246`, `CODE_STATE_SESSION_KEY` and `code_state` / `_store_outcome`: the per-page session state. Add the wrong-code count and the consecutive-check-failure count, reset on a successful send.
- `signup/forms.py:401-451` `CodeForm.clean_code`: the error codes `invalid_code`, `code_expired` and `check_failed`. Count them where the form is handled (the code steps' `handle` in `signup/steps.py`, the resume view's code POST, `accounts/views.py` `login_code`), or in one helper they all call.
- `signup/steps.py:420-481`: the two "We have sent you a new code" and the "…by SMS" messages (A10).
- `signup/templates/signup/_code_help.html`: the support block (`code.support_email`).
- `accounts/models.py:29` `HowFound.EMAIL` label, and `locale/es/LC_MESSAGES/django.po` (msgid change).
- `signup/templates/signup/step_account.html:39-42`: the `data-show-if-field` wrapper; `static/js/signup-account.js:29` toggles `hidden`.
- Tests:
  - `signup/tests/test_mobile_code.py`: support contact tests (e.g. `test_failed_send_offers_a_retry_and_the_support_contact`, hourly);
  - `signup/tests/test_step1.py`: how-found;
  - `accounts/tests/test_login_code.py`: login support;
  - the `clock` fixture.

## Tasks & Acceptance

**Execution:**
- [x] `signup/steps.py` (+ the code POST handlers) -- the counts and the support rule (A11); the resend messages (A10).
- [x] `accounts/models.py`, `signup/templates/signup/step_account.html` -- A17.
- [x] `locale/es/LC_MESSAGES/django.po` -- ES for changed texts.
- [x] Tests for every matrix row.

**Acceptance Criteria:**
- Given the full suite (browser tests included), when it runs, then it passes; tests asserting the old "new code" text or the old support triggers change only as the items require.

## Implementation Notes

- A11: the counts are kept in `code_help_context` (`_count_check`), the one helper the code steps, the resume page and the login code page all render with their bound form, so no handler counts on its own. Wrong codes follow decision 2 (see the change log). Any check made ends a run of `check_failures`, which resets on a successful send (`_store_outcome`). Two consecutive check failures keep the support contact until a check is made or a code is sent.
- A17: see the change log for where the "e-mail" label lives.

## Plan Change Log

- 2026-10-23, A10 on the resume page: its resend message lives in `signup/views.py`, so that one string was changed there, beyond the Never list's "only what A11 needs", because the matrix requires the same texts on the resume page.
- 2026-10-23, A11 after review (decision 2): only `invalid_code` answers from well-formed codes count (`CodeForm.malformed`); the count is kept across resends of the same code and reset only by a send at least `CODE_TTL_SECONDS` after the current code's first send (`code_sent_at` in the page state), or the first send after a "no longer valid" (the code is gone); support shows on "no longer valid" only after 4 counted answers on a code still within its validity.
- 2026-10-23, A17 label: changing `HowFound.EMAIL`'s label in `accounts/models.py` makes `makemigrations` produce two `AlterField` migrations (the choices are in `accounts` and `signup` migrations 0006), which the plan forbids. The label is set instead on step 1's form choices (`signup/forms.py` `HOW_FOUND_CHOICES`: "e-mail", ES "correo electrónico"); the model label, and so the admin's, stays "E-mail".

## Review Triage Log

**Pass 1 (quick lens, 2026-10-23):** high 0, medium 1, low 4, false 1, maybe-false 0. Three patches (one after a user decision); three rejected.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | The wrong-code count reset on every send and counted every refusal, so it drifted from the provider: 4 wrong, a resend, then 1 wrong gave no support; 4 wrong then an expiry, or one expired code 5 times, showed support; malformed input counted | medium | patch (decision 2) | Only well-formed `invalid_code` answers count. The count is kept across same-code resends and resets on a new code (a send ≥ 10 min after the code's first send, or the first send after "no longer valid"). Support shows on "no longer valid" only after 4 counted answers within the code's 10 minutes. Tests cover each case. |
| 2 | `signup/views.py` changed for A10, beyond the Never list's "only what A11 needs" | low | rejected | Needed so the matrix's "same rules on the resume page" holds. Recorded in the Plan Change Log. |
| 3 | Stale `#:` references in `django.po` | low | patch | Corrected for the changed entries. |
| 4 | The label was set on step 1's form choices, not `HowFound.EMAIL`; the admin still shows "E-mail" | false | rejected | A model label change makes Django generate a migration, which the plan forbids; recorded in the Change Log. The spec's wording concerns signup's step 1 choice. |
| 5 | Matrix rows untested on some pages: resume check-failures, email-step plain expiry, login Spanish resend texts | low | patch | Tests added for each. |
| 6 | (same root as 1) a plain expiry after earlier wrong codes showed support | low | patch (with 1) | Covered by decision 2's "within the code's 10 minutes" rule and its tests. |

After the patches (2026-10-23), on SQLite with Chromium (sandbox lifted): the full suite gives 1,366 passed, 3 failed (the known PostgreSQL-only tests). `makemigrations --check`: no changes.

## Verification

**Results (2026-10-23, before the review fixes; SQLite, test keys, browser outside the sandbox):** full suite 1,358 passed, 3 failed (the known PostgreSQL-only tests in `accounts/tests/test_crypto.py`). `makemigrations --check`: no changes. `compilemessages -l es`: OK.

**Commands:**
- The local suite command (test keys, SQLite), outside the sandbox for the browser tests. Expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py makemigrations --check`, `compilemessages`. Expected: no changes, OK.
