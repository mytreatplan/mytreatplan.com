---
title: 'Signup country check'
type: 'feature'
ticket: '3'
created: '2026-11-03'
status: 'done'
baseline_revision: 'bb089c013cf285fb2f1ac694c9393ea9c00e4266'
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

**Problem:** R3: signup step 1 accepts any country. A doctor from the other region signs up on the wrong instance, and a doctor from a banned country can sign up at all.

**Approach:** Step 1 asks the country map (`regions.countries.lookup_country`) about the chosen country. When the country belongs to the other region, a live note under the country field links to that region's signup page; the doctor may still continue. A banned country is a form error, so step 1 cannot be saved with it. EN and ES texts.

## Boundaries & Constraints

**Always:**
- The map is read only through `lookup_country(code)` and its `.banned` / `.other_region` / `.signup_url`. No `'eu'`/`'ae'` literals outside `regions/` (guard test).
- The live note follows the email check pattern: htmx `hx-post` on the country select (`hx-trigger: change`, `hx-params: country,csrfmiddlewaretoken`) to a new `signup_check_country` view that renders a partial into `#country-check` (`aria-live="polite"`). Same no-op answers as `check_email` for a logged-in user and after the idle timeout.
- The same partial is rendered server-side whenever step 1 is shown with a country already chosen (a re-render after a form error, or Back to step 1), so it does not depend on JS there.
- The banned refusal is `AccountForm.clean_country`. Its message does not say why (no "sanctions"): EN "We can't offer MyTreatPlan to doctors in this country.", ES "No podemos ofrecer MyTreatPlan a doctores de este país." The live check shows the same text for a banned country.
- The other-region note: EN "{country} is served by our {site} site. Sign up there to have your account in your region." + link "Go to {site}'s signup" (`signup_url`, same tab), where `{site}` is the host of `site_url`; ES equivalent. The link is plain text in the note, not a redirect.
- The rejected step 1 is logged like other form rejections (`_log_rejected`, reason from the field error) — no new event type.

- Decision (user, 2026-11-04): without JavaScript, Continue goes straight on and the other-region note is not shown; the banned refusal is server-side either way.
- Decision (user, 2026-11-04): step 1 is the only country check; no re-check at account creation (the map changes only by migration until epic-catalogue).
- Decision (user, 2026-11-04): the banned text above, with no contact line.

- Decision (user, 2026-11-04): the brand is spelled "MyTreatPlan", as on the rest of the site.

**Never:**
- No change to `lookup_country`, the map, or its seed.
- No blocking of the other-region country (warning only). No geo-IP here (entry 5).
- No profile-country edit (does not exist; epic-doctor-profile).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Own region | ES on dev (AE on dev-ae) | No note; Continue goes to step 2 | — |
| Other region | SA on dev | Note with link to dev-ae's `/en/signup/` (on dev ES pages: `/en/` since ae offers only en); Continue still goes to step 2 | — |
| Other region, mirror | ES on dev-ae | Note with link to dev's `/en/signup/` | — |
| Banned | IR or NG | Live note shows the refusal; Continue re-renders step 1 with the field error; nothing saved | logged as rejected |
| Banned via tampering | POST country=IR without the live check | Same field error | — |
| Country cleared | blank option chosen | Partial renders empty | — |
| Live check, logged in / idle | — | Empty partial | — |

</frozen-after-approval>

## Code Map

- `regions/countries.py:94` `lookup_country(code, language=None)` → `CountryAnswer(code, state, site_url, signup_url)` with `.banned`, `.own_region`, `.other_region`. Unknown/blank code reads as eu. Unchanged.
- `signup/forms.py` -- `AccountForm.country` (:223, ChoiceField from `country_choices()` :122); `clean()` :310. Add the htmx attrs to the country widget (pattern: the email widget :185-199, `reverse_lazy`) and `clean_country`.
- `signup/views.py` -- `check_email` (:506) is the template for `check_country` (POST only, `_idle_timed_out`, logged-in → empty). No rate limit is needed: it reveals only the public map, and no DB row of a person. `_render` builds step 1's context: add `country_check` (the `CountryAnswer` or None) when the bound/initial country is set. `_submit` (:295) is unchanged; the form error path already re-renders.
- `signup/urls.py` -- add `path('signup/check-country/', views.check_country, name='signup_check_country')` BEFORE `signup/<slug:slug>/` (:13).
- `signup/templates/signup/step_account.html:27` -- the country field; add `<div id="country-check" aria-live="polite">{% include "signup/_country_check.html" %}</div>` right after it (as `#email-check` :22).
- New `signup/templates/signup/_country_check.html` -- comment header like `_email_check.html`; the other-region note (panel styling of the resume offer) or the banned refusal.
- `locale/es/LC_MESSAGES/django.po` -- new strings; `.mo` is gitignored and compiled in CI/deploy.
- Tests: `signup/tests/helpers.py` (`STEP1` country 'ES', `_step1`, `_complete`), `signup/tests/test_step1.py` (`test_spanish_labels_and_errors` :319 for the ES pattern), the email-check tests next to it for the live view. `regions/tests/test_country_map.py` shows how tests set `REGION_PROFILE`/`REGION_SITE_URLS` with `override_settings`. Browser tests `signup/tests/browser/` (`fill_step1` :70) -- add one.
- `regions/tests/test_guard.py` -- run it.

## Tasks & Acceptance

**Execution:**
- [x] `signup/forms.py` -- htmx attrs on the country select; `clean_country` refuses `lookup_country(code).banned` -- the server-side gate.
- [x] `signup/views.py`, `signup/urls.py` -- `check_country` view + route; `country_check` in step 1's render context -- the live note and its non-JS fallback.
- [x] `signup/templates/signup/step_account.html`, new `signup/templates/signup/_country_check.html` -- the note area and partial.
- [x] `locale/es/LC_MESSAGES/django.po` -- ES texts.
- [x] `signup/tests/test_country_check.py` (new) -- the I/O matrix with `override_settings` for both profiles, ES rendering, the live view's empty cases; one browser test: choosing SA shows the link, choosing IR shows the refusal, Continue with IR stays on step 1.

**Acceptance Criteria:**
- Given the eu profile, when step 1 is posted with SA, then step 2 follows and the account is later created with country SA.
- Given any profile, when step 1 is posted with a banned country, then no `PendingRegistration` is saved and the page shows the refusal in the active language.
- Given the ES interface on the eu profile, when SA is chosen, then the note is in Spanish and its link targets the ae site's `/en/signup/`.

## Implementation Notes

- `signup.views.country_check_context(code)` builds the partial's context (`country_check` = the `CountryAnswer`, `country_name` from django-countries in the active language, `country_site` = host of `site_url`). A blank or unknown code (no django-countries name) gives `country_check: None`, so an unknown code on ae does not show a note to eu.
- On a re-rendered step 1 with a banned country, the field error already shows the refusal, so the server-side partial is left empty then (no duplicate text). The live check still shows it.
- The partial renders the refusal from `country_banned_message` (= `signup.forms.COUNTRY_BANNED_MESSAGE`, set by `country_check_context`), so the text exists only in forms.py (review fix). It is auto-escaped like the form error (`can&#x27;t`).
- `.po` entries added by hand: `makemessages -l es` rewrites the whole file here (reorders/adds ~2,300 lines from other sources), so it was run only to check the new entries (matched, no `#, fuzzy`) and then reverted to the hand-edited file.
- Browser test (`signup/tests/browser/test_country_check.py`) seeds ES/SA/IR in the map itself: an earlier transactional test flushes the migrated map in a full run (same reason as `legal_documents`).

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, opus, 2026-11-04): 1 finding. high 0, medium 0, low 1, false 0, maybe-false 0.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | `_country_check.html` re-typed the banned refusal instead of using `COUNTRY_BANNED_MESSAGE`; two copies could drift with no test comparing them | low | patch | Real: the msgids matched only by hand. Patched by the implementer: `country_check_context` passes `country_banned_message` and the partial renders it; the `.po` entry now cites only `signup/forms.py`. |

Verification after the patch: full suite on SQLite, 1702 passed, 3 failed (the known PostgreSQL-only `accounts/tests/test_crypto.py` cases).

## Verification

**Commands:**
- the full suite (see local dev notes) -- expected: all pass except the 3 known PostgreSQL-only failures.
- `../.venv/bin/python manage.py makemessages -l es` then check no `#, fuzzy` on the new entries -- expected: clean.

**Manual checks (if no CLI):**
- On dev: SA shows the dev-ae link and continues; IR is refused; ES shows nothing. Mirror on dev-ae.

## Staging

- Merged in PR #64 (2026-11-04, merge 7073f89) at 3a6e3ce. The "MyTreatPlan" spelling commit (user decision, 2026-11-04) came after that merge and went in through PR #66 (merge c67a04c).
- CI: PR run 41816408674 green on PostgreSQL. Master runs 41818490414 (#64), 41818581609 (#65, with both stories) and 41820568914 (#66) were green, with both deploy legs (dev, dev-ae). Local suite: 1702 passed, plus the 3 known PostgreSQL-only failures.
- Checks (user, 2026-11-04): signup step 1 on dev and dev-ae: SA (on dev) / ES (on dev-ae) shows the other region's signup link and lets the doctor continue; IR is refused; the own region shows no note. All OK.
