---
title: 'English and Spanish'
type: 'feature'
ticket: '3'
created: '2026-10-08'
status: 'built'
baseline_revision: 'cf56cac10989d0c2a90bd77c91496e0a8410b457'
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

**Problem:** The public site exists only in English. Spanish is required (EN/ES decision for the whole portal), and the signup and login epics need a fixed, language-aware URL structure to build on.

**Approach:** Turn on Django i18n with both languages prefixed in URLs and a language switcher. Add a Spanish catalogue drafted by Claude, with a side-by-side review table the team corrects before the story is done, plus a test that fails whenever a public string lacks a Spanish translation.

## Boundaries & Constraints

**Always:**
- Languages are English (default) and Spanish. Every public page lives under `/en/…` and `/es/…` via `i18n_patterns` with `prefix_default_language=True`. `/` redirects by the browser's `Accept-Language` (Spanish → `/es/`, anything else → `/en/`). `admin/` stays unprefixed.
- `LocaleMiddleware` is placed after `SessionMiddleware` and before `CommonMiddleware`. `LOCALE_PATHS = [BASE_DIR / 'locale']`. The catalogue is `locale/es/LC_MESSAGES/django.po`. The compiled `.mo` is not committed; it's built with `compilemessages` locally and in CI.
- The language switcher sits in the header and links to the same page in the other language (`translate_url`). Each link carries `lang` and `hreflang`, and the current language is marked `aria-current`.
- Spanish keeps the brand and proper nouns (MyTreatPlan, VirtuaOrtho, TPS, team names, email). Tone follows the design system's voice: plain, second person (usted/your practice → "su clínica"), no hype.
- `<html lang>` follows the active language.
- Decision (user, 2026-10-08): stacked on branch `story/1-2-remaining-sections`, as `story/1-3-english-spanish`.
- Decision (user, 2026-10-08): both languages prefixed; `/` redirects by browser language.
- Decision (user, 2026-10-08): the team reviews the Spanish in a side-by-side table `story-english-and-spanish-es-review.md` (beside this plan): EN string, ES draft, and an empty "Correction" column. Their corrections go into the `.po`, and that approval is the story's human step.
- Decision (user, 2026-10-08): the English page says "Dubai"; Spanish says "Dubái".
- Decision (user, 2026-10-08): Spanish addresses Doctors with the formal *usted* ("su clínica", "contáctenos").
- Decision (user, 2026-10-08): keep the full plan (~2,000 tokens) rather than split.

**Never:**
- Legal pages, the contact form, or the `signup`/`login` routes themselves (other stories). Only the URL structure they'll plug into is set here.
- Machine-translation services or any third-party call at runtime.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| English page | GET `/en/` | 200, `<html lang="en">`, English text | No error expected |
| Spanish page | GET `/es/` | 200, `<html lang="es">`, Spanish text for every section (e.g. "Qué hacemos") | No error expected |
| Root redirect | GET `/` with `Accept-Language: es` / `en` / none | 302 to `/es/` / `/en/` / `/en/` | No error expected |
| Switcher | On `/en/` | A link to `/es/` (and vice versa), current language marked | No error expected |
| Missing translation | A new `{% translate %}` string with no Spanish `msgstr` (or a fuzzy entry) | The catalogue test fails, naming the string | Test failure |
| Unknown language | GET `/fr/` | 404 | Django default |

</frozen-after-approval>

## Code Map

- `mytreatplan/settings.py` -- `LANGUAGE_CODE='en-us'`, `USE_I18N=True`. No `LANGUAGES`, `LocaleMiddleware` or `LOCALE_PATHS` yet. Add them, with `LANGUAGE_CODE='en'`.
- `mytreatplan/urls.py` -- `admin/` plus `include('mytp_publicsite.urls')`. Wrap the public include in `i18n_patterns`.
- `templates/base.html` -- header and nav from 1.1/1.2; `<html lang="{{ LANGUAGE_CODE|slice:':2' }}">`. Add the switcher next to the nav: visible above 700px, and inside the burger panel at ≤700px.
- Templates with strings: `templates/base.html`, `mytp_publicsite/templates/publicsite/home.html`, `…/sections/_*.html`. 61 `{% translate %}` and 18 `{% blocktranslate %}` tags. Some msgids contain `&amp;` and `<br>`. The slide labels use `{{ n }}`, `{{ total }}` and `{{ name }}`, and the dots use `{n}`/`{total}` in `data-label-template`, so Spanish must keep those placeholders exactly.
- `mytp_publicsite/templates/publicsite/sections/_where_we_are.html` -- the office heading `{% translate "Dubái" %}` becomes `"Dubai"`, with Spanish msgstr "Dubái".
- `mytp_publicsite/tests/test_home.py` -- 9 tests call `client.get('/')`. Point them at `/en/` (or `reverse` under English). `test_sections_show_their_key_content` asserts "Dubái": change it to "Dubai" for EN.
- `.github/workflows/ci.yml` -- add `sudo apt-get install -y gettext` and `python manage.py compilemessages` before pytest.
- `gettext`/`msgfmt` are installed locally (Homebrew).

## Tasks & Acceptance

**Execution:**
- [x] `mytreatplan/settings.py` -- add `LANGUAGES = [('en', 'English'), ('es', 'Español')]`, `LANGUAGE_CODE='en'`, `LOCALE_PATHS` and `LocaleMiddleware`.
- [x] `mytreatplan/urls.py` -- wrap the public site in `i18n_patterns(..., prefix_default_language=True)`.
- [x] `templates/base.html` -- language switcher (desktop and inside the burger panel), `hreflang` alternate `<link>`s in `<head>`.
- [x] `_where_we_are.html` -- "Dubai" in EN.
- [x] `locale/es/LC_MESSAGES/django.po` -- `makemessages -l es`, then fill every msgstr with Claude's Spanish, keeping placeholders and markup.
- [x] `story-english-and-spanish-es-review.md` (beside this plan) -- table: # / English / Español (draft) / Corrección (empty), one row per msgid, grouped by section.
- [x] `.gitignore` -- add `*.mo`.
- [x] `mytp_publicsite/tests/` -- tests for every matrix row. The catalogue test runs `makemessages` into a temp copy and fails if any msgid is missing from the committed `.po`, or has an empty or fuzzy msgstr.
- [x] `.github/workflows/ci.yml` -- install gettext and run `compilemessages` before the tests.

**Acceptance Criteria:**
- Given the team has filled the review table, when their corrections are applied to the `.po`, then `/es/` shows the approved Spanish and the review table records approval.
- Given any page, when switching language, then the same page opens in the other language.

## Implementation Notes

- Switcher: a second `<ul aria-label="Language">` inside `<nav id="site-menu">`, so it sits beside the links above 700px and inside the burger panel at ≤700px (the panel became `flex-col`). Links show the native names ("English", "Español") rather than codes, so the accessible name matches the visible text. URLs come from a small `translated_url` tag (`mytp_publicsite/templatetags/language_urls.py`, wrapping `django.urls.translate_url`); `<head>` also lists `hreflang` alternates plus `x-default` → `/`.
- Spanish writes "&amp;" as "y"; the placeholder/markup test ignores `&amp;` and checks every other tag, entity and placeholder.
- `makemessages` writes into `./locale` of the working directory whatever `LOCALE_PATHS` says, so the catalogue test copies the source tree to a temp dir and runs there; it also asserts the real `.po` is untouched. Refresh by hand with `python manage.py makemessages -l es -i node_modules -i static_root -i '_bmad*'`.
- Not done here: the visual check at phone/desktop width (headless Chrome is blocked by the sandbox) and the team's review of the Spanish (`story-english-and-spanish-es-review.md`).

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, 2026-10-08): high 0, medium 0, low 2, false 2, maybe-false 0.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | AC1 (team approval of the Spanish) not met; review table still pending | false | — | Not a code defect: the plan makes team approval the story's human step. Handed to the user at presentation. |
| 2 | Manual phone/desktop check not done; the switcher may wrap at narrow tablet widths, leaving its divider orphaned | false | — | Not a code defect: manual check is the user's. The wrap risk is passed on to that check. |
| 3 | hreflang alternates copy the query string | low | patch | Confirmed in language_urls.py: it uses `get_full_path()`. Real SEO harm with tracking parameters. Patch: `request.path` for absolute alternates, plus a test. |
| 4 | Catalogue test copies the whole repo minus a skip list | low | patch | Confirmed. A venv inside the repo would fail it. Patch: copy only the folders that hold strings. |

## Verification

**Commands:**
- `python manage.py compilemessages` -- expected: compiles without errors
- `pytest` -- expected: all pass
- `python manage.py check` -- expected: no issues

**Manual checks (if no CLI):**
- `/en/` and `/es/` side by side at phone and desktop width; switcher in the header and in the burger panel; nothing overflows with the longer Spanish text.
- The team fills the review table; their corrections are applied and approved.
