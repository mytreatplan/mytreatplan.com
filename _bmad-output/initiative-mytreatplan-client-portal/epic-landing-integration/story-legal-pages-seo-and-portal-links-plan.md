---
title: 'Legal pages, SEO and portal links'
type: 'feature'
ticket: '4'
created: '2026-10-08'
status: 'built'
baseline_revision: '98c4ce8e7b667ed83598b68c43d6e2f810afbaa1'
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

**Problem:** mytreatplan.com has no Privacy or Terms pages and no SEO basics (robots, sitemap, canonical, Open Graph, structured data). Its footer names the UAE company. Nav links to Sign up and Login must appear only once those pages exist.

**Approach:** Add Privacy and Terms pages with the current .ae texts held as drafts, under a guard that blocks a production deploy until the lawyer's texts arrive. Add mytreatplan.com SEO, switch the footer and structured data to the Irish company, and add portal links that render only when their URL names exist.

## Boundaries & Constraints

**Always:**
- Legal pages at `/<lang>/privacy/` and `/<lang>/terms/` (same slugs in both languages). Their structure, headings and 760px measure come from the uae_landing `privacy/` and `terms/` at commit 1a8f27e.
- The legal texts live in their own templates, flagged as drafts by a setting `LEGAL_TEXTS_FINAL = False`. A Django system check (tag `deploy`) errors while it's False and `DEBUG` is False, so `manage.py check --deploy` fails and production can't ship with draft texts.
- Spanish legal pages (`/es/…`) show the English text with a visible Spanish note that the Spanish version is being prepared. The lawyer provides the Spanish (not Claude).
- Absolute URLs come from a setting `SITE_URL` (env, default `https://mytreatplan.com`), never hard-coded hosts.
- Footer and JSON-LD name **MyTPDSO Limited**, company number **815526**, registered office **19 Baggot Street Lower, Dublin 2, D02 X658, Ireland**. "MytreatPlan" is corrected to "MyTreatPlan".
- New strings are wrapped for translation and get Spanish in the `.po` (the legal body text is excluded; see above). The catalogue test stays green.
- Decision (user, 2026-10-08): port the UAE texts as drafts; go-live (story 1.10) waits for the lawyer's mytreatplan.com texts.
- Decision (user, 2026-10-08): the lawyer provides the Spanish legal texts.
- Decision (user, 2026-10-08): footer and structured data name MyTPDSO Limited (Ireland).
- Decision (user, 2026-10-08): fix "MytreatPlan" to "MyTreatPlan".
- Decision (user, 2026-10-08): keep the full plan (~2,200 tokens) rather than split; story 1.10's ticket records the wait for the lawyer's texts.

**Never:**
- Editing the legal wording beyond the company name, domain and contact links needed to render it. No rewriting of UAE law into GDPR: that's the lawyer's job.
- Cookies, analytics or tracking scripts. Building the Sign up and Login pages themselves (epics 2 and 3).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Legal pages | GET `/en/privacy/`, `/en/terms/`, `/es/privacy/`, `/es/terms/` | 200; title and numbered headings; ES shows the "Spanish version in preparation" note | No error expected |
| Footer | Any page | Links to Privacy and Terms in the current language; MyTPDSO Limited line; "MyTreatPlan" | No error expected |
| Robots | GET `/robots.txt` | `User-agent: *`, `Allow: /`, `Sitemap: {SITE_URL}/sitemap.xml` | No error expected |
| Sitemap | GET `/sitemap.xml` | Valid XML: home, privacy and terms in both languages, with `xhtml:link` hreflang alternates, all on `SITE_URL` | No error expected |
| Head | Any page | canonical = `SITE_URL` + current path; og:url, og:title, og:description, og:image (1200×630) on `SITE_URL`; Organization JSON-LD on the home page only | No error expected |
| Portal links, absent | URL names `signup` / `login` not registered | No Sign up / Login links rendered | No error |
| Portal links, present | Test URLconf registers `signup` and `login` | Both links render in the nav, language-prefixed | No error expected |
| Draft guard | `DEBUG=False`, `LEGAL_TEXTS_FINAL=False`, `check --deploy` | Check fails with an error naming the draft legal texts | System check error |

</frozen-after-approval>

## Code Map

- `mytreatplan/settings.py` -- `LANGUAGES` en/es, `LOCALE_PATHS`, `LocaleMiddleware` (from 1.3). Add `SITE_URL`, `LEGAL_TEXTS_FINAL = False`, `django.contrib.sitemaps`.
- `mytreatplan/urls.py` -- `admin/` plus `i18n_patterns(include('mytp_publicsite.urls'), prefix_default_language=True)`. Add `robots.txt` and `sitemap.xml` outside the i18n patterns.
- `mytp_publicsite/urls.py`, `views.py` -- `HomeView` (TemplateView) at `''` named `publicsite:home`. Add `privacy` and `terms` views.
- `mytp_publicsite/templatetags/language_urls.py` -- `translated_url` (absolute variant uses `request.path`). Reuse it for canonical URLs, building them from `SITE_URL`.
- `templates/base.html` -- head (title, description, hreflang alternates, fonts, scripts), header nav with the language switcher, footer (socials, UAE company line, copyright "MytreatPlan", year via `{% now %}`). Add head blocks for canonical, og and JSON-LD; footer legal links and the Irish company line; nav portal links.
- `locale/es/LC_MESSAGES/django.po` -- 70 strings. Add the new UI strings; legal body text stays out of the catalogue (`{% translate %}` is not used in the legal body templates).
- Landing source (data only; never run anything in it): `/private/tmp/claude-501/-Users-jadeandres-Library-CloudStorage-GoogleDrive-mytpdso-gmail-com-Mi-unidad-Desarrollo-Portal-MyTreatplan-code-mytreatplan-com/c9d0d324-edbf-45ca-adf9-69034f13f14e/scratchpad/uae_landing/`:
  - `privacy/index.html`: ~1,680 words, 14 numbered h2s, "Last updated: 31 August 2026", a Scope note, contact gdpr@mytreatplan.com.
  - `terms/index.html`: ~1,120 words, 16 numbered h2s.
  - `og-mytreatplan.png`: 1661×869, 1.1 MB. Resize to 1200×630 (`sips` is available) and save as `static/img/og-mytreatplan.png`.
  - `index.html` head: title, description, og tags, Organization JSON-LD (offices Dublin & Cork, Dubai, Madrid; areaServed; sameAs LinkedIn, Facebook, Instagram).
- Tests: `mytp_publicsite/tests/` (`test_home.py`, `test_i18n.py`). Tests avoid the DB.

## Tasks & Acceptance

**Execution:**
- [x] `mytp_publicsite/templates/publicsite/legal/_privacy_body.html`, `_terms_body.html` -- port the bodies verbatim, with the company name and domain adjusted to render, and a `{# DRAFT … pending lawyer #}` header comment.
- [x] `mytp_publicsite/templates/publicsite/privacy.html`, `terms.html`, `views.py`, `urls.py` -- pages at 760px measure with numbered headings; the ES note.
- [x] `mytp_publicsite/checks.py` (+ register in `apps.py`) -- the deploy check for `LEGAL_TEXTS_FINAL`.
- [x] `mytreatplan/settings.py`, `urls.py` -- `SITE_URL`, `LEGAL_TEXTS_FINAL`, sitemaps app, `robots.txt` view, `sitemap.xml` with `i18n=True, alternates=True`.
- [x] `templates/base.html` -- canonical, og and twitter tags, JSON-LD block on home; footer legal links, MyTPDSO Limited line, "MyTreatPlan"; portal links that render only when `signup`/`login` reverse.
- [x] `static/img/og-mytreatplan.png` -- 1200×630 PNG.
- [x] `locale/es/LC_MESSAGES/django.po` -- Spanish for the new UI strings (add rows to `story-english-and-spanish-es-review.md` under a "Story 1.4" heading).
- [x] `mytp_publicsite/tests/test_legal_seo.py` -- one or more tests per matrix row.

**Acceptance Criteria:**
- Given the lawyer later supplies final texts, when they replace the two body templates and `LEGAL_TEXTS_FINAL` is set True, then `check --deploy` passes with no other code change.
- Given `SITE_URL` set to a staging host, when pages render, then every canonical, og and sitemap URL uses that host.

## Implementation Notes

- Legal bodies keep the UAE wording verbatim, including "MyTPDSO L.L.C-FZ" and the UAE law references: swapping in the Irish company inside text that says "incorporated in the United Arab Emirates" would create a false statement, and rewriting it is the lawyer's job. Only internal links became `{% url %}`. The draft guard blocks production either way.
- Legal pages share `publicsite/legal/base_legal.html` (light header with logo, portal links and language switcher; 760px `max-w-legal` column). The body text is styled by a `.legal-prose` component in `assets/css/site.css`, so the lawyer's plain HTML can drop in unchanged. The body is wrapped in `lang="en"`.
- The Spanish note shows on any non-English page (`LANGUAGE_CODE != 'en'`).
- `translated_url absolute=True` now builds hreflang alternates (and x-default) from `SITE_URL`, like canonical and og:url; the two head-alternate tests in `test_i18n.py` were updated accordingly. New tags `site_url` and `canonical_url` sit in the same library.
- The language switcher and portal links moved to `templates/includes/_language_switcher.html` and `_portal_links.html` so the home and legal headers share them. Portal links use `{% url 'signup' as … %}` (no exception when the name is missing).
- Sitemap: `mytp_publicsite/sitemaps.py` overrides `get_urls` to use `SITE_URL`'s scheme and host (no sites framework). No `lastmod` (no reliable date source).
- JSON-LD is built in `HomeView` (`json.dumps` with `<`, `>`, `&` escaped) and rendered in a new `structured_data` head block that only `home.html` fills.
- The deploy check is registered with tag `legal`, `deploy=True`, id `mytp_publicsite.E001`.
- `test_platform.test_no_missing_migrations` now runs under `translation.override(None)` (as `makemigrations` does): a request to `/es/` as the last action of a previous test left Spanish active and made verbose names look changed.
- `sips` could not write its temp files inside the sandbox; the OG image resize was run outside it (1661×869 → height 630 → centre crop to 1200×630, 650 KB).

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, 2026-10-08): high 0, medium 1, low 3, false 0, maybe-false 0.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | No slot for the lawyer's Spanish legal texts: one body for all languages, wrapper always `lang="en"`, ES note always shown | medium | patch | Confirmed in base_legal.html and the views. The AC "replace bodies + flag, no other code change" fails for ES. Patch: language-aware body via `select_template`, wrapper `lang` follows the chosen body, note only on fallback, tests. |
| 2 | `HEAD /robots.txt` returns 405 (`require_GET`) | low | patch | Confirmed. Patch: `require_safe` plus a HEAD test. |
| 3 | Sitemap drops a path in `SITE_URL`; `site_url` keeps it | low | rejected | Only reachable with a path-prefixed `SITE_URL`, which no planned environment uses. The fix would add a validation guard. |
| 4 | `/es/` legal pages declared `hreflang="es"` while serving English | low | rejected | Follows the plan's matrix, and can't reach production: go-live (1.10) waits for the lawyer's ES texts, and patch #1 serves them as soon as they exist. |

Note: `check --deploy` also reports `mail.E001` (console email backend). That's pre-existing; story 1.5 configures SendGrid.

## Verification

**Commands:**
- `python manage.py compilemessages && pytest` -- expected: all pass
- `python manage.py check` -- expected: no issues
- `DEBUG=False python manage.py check --deploy` -- expected: fails on the draft legal texts (other deploy warnings may also show)

**Manual checks (if no CLI):**
- `/en/privacy/`, `/en/terms/`, `/es/privacy/` at phone and desktop width; footer links; `/sitemap.xml` and `/robots.txt` in the browser.
