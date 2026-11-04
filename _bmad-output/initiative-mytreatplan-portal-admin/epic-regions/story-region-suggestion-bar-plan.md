---
title: 'Region suggestion bar'
type: 'feature'
ticket: '5'
created: '2026-11-03'
status: 'built'
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

**Problem:** R4 / AD-14: a GCC visitor on .com (or a European visitor on .ae) gets no hint that their region has its own site.

**Approach:** Public pages render a slim dismissible top bar when `regions.geoip.request_country` gives a country that `lookup_country` maps to the other region. The bar links to that region's site and offers "Stay here", which sets a remembered cookie so the bar is not shown again. Bots, banned countries, the own region and an unknown country get no bar. The DB-IP attribution is shown.

## Boundaries & Constraints

**Always:**
- Computed server-side per request (no page caching exists). An inclusion template tag `{% region_suggestion_bar %}` (takes_context) in `regions/templatetags/` renders `regions/_suggestion_bar.html` or nothing. It reads the request only; no view changes.
- Order of checks, cheapest first: stay cookie present → nothing; bot user agent → nothing; `request_country` None → nothing; `lookup_country(country)` not `.other_region` → nothing.
- Bots: a case-insensitive regex in `regions/` over `HTTP_USER_AGENT` (`bot|crawl|spider|slurp|facebookexternalhit|preview|headless|python-requests|curl|wget`), and an empty user agent counts as a bot.
- Only the public templates show it: `base.html` gets an empty `{% block region_bar %}{% endblock %}` as the first child of `<body>`; the home page and `publicsite/legal/base_legal.html` fill it with the tag. Portal pages (`portal_base.html`: login, signup, account) never show it.
- The bar: text EN "It looks like you're in {country}. MyTreatplan for your region is at {host}." with a link button "Go to {host}" (`site_url` + `/`, same tab) and a "Stay here" control. ES translations. `{country}` from django-countries in the active language; `{host}` is the host of `site_url`.
- "Stay here" is a plain link to a new GET view `region_stay` (`/region/stay/?next=<path>`, outside `i18n_patterns`) that sets the cookie and redirects to `next` when `url_has_allowed_host_and_scheme` accepts it, else `/`. Works without JS; no new JS file.
- Cookie `mytp_stay_region`, value `1`, path `/`, HttpOnly, SameSite=Lax, Secure when DEBUG is off (as `accounts/devices.py:80-84`), max-age 1 year.
- No literal `'eu'`/`'ae'` in .py outside `regions/`. No IP or country is logged.

- Decision (user, 2026-11-04): the cookie lasts 1 year; the bar's × is the same link as "Stay here".
- Decision (user, 2026-11-04): the DB-IP attribution is a small "IP geolocation by DB-IP" link (https://db-ip.com) in the footer of every page (base.html), translated.
- Decision (user, 2026-11-04): create `_bmad-output/initiative-mytreatplan-portal-admin/privacy-notes-for-lawyer.md` listing every cookie (csrftoken, sessionid, the remembered-device cookie, `mytp_stay_region`) with purpose and lifetime, and the local geo-IP lookup (no IP leaves the instance, nothing stored). The public privacy text stays unchanged until the lawyer answers.

**Never:**
- No redirect by geo-IP; no bar on portal pages; no JS-only dismissal; no change to `geoip.py` or `lookup_country`; no consent banner.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Other region | dev, override SA, browser UA | Bar linking to dev-ae's site | — |
| Mirror | dev-ae, override ES | Bar linking to dev's site | — |
| Own region | dev, ES | No bar | — |
| Banned | dev, IR | No bar | — |
| Unknown | private IP / no DB file | No bar | `request_country` returns None |
| Bot | Googlebot UA, or empty UA, with SA | No bar | — |
| Stayed | cookie set, SA | No bar | — |
| Stay link | `/region/stay/?next=/es/privacy/` | 302 to `/es/privacy/` with the cookie | `next=https://evil.example` → `/` |
| Portal page | login, SA | No bar | — |

</frozen-after-approval>

## Code Map

- `regions/geoip.py:133` `request_country(request)` → ISO-2 or None (override header `X-MyTP-Geo-Country` first, staging only; missing DB → None). Unchanged.
- `regions/countries.py:94` `lookup_country(code)` → `CountryAnswer`, `.other_region`, `.site_url`. One DB query; it runs only after the cookie and bot checks. Unchanged.
- `templates/base.html` -- `<body>` at :37-39 (add the empty block); footer :88-121, company line :107-110, Legal nav :111-116 (the attribution line goes in the footer).
- `templates/portal_base.html` -- extends base, overrides `header`; leave it so the block stays empty.
- `mytp_publicsite/templates/...` home template (`HomeView` views.py:71) and `publicsite/legal/base_legal.html` -- fill `region_bar`.
- New: `regions/templatetags/__init__.py`, `regions/templatetags/regions_bar.py`, `regions/templates/regions/_suggestion_bar.html`, `regions/suggestion.py` (bot regex, `suggestion_for(request)`, cookie constants, `stay` view) or split as fits.
- `mytreatplan/urls.py` -- add `path('region/stay/', ..., name='region_stay')` outside `i18n_patterns`.
- Cookie precedent: `accounts/devices.py:80-84`. Styling: the header/footer Tailwind classes in base.html (slim, full-width, `bg-panel`-like).
- `locale/es/LC_MESSAGES/django.po` -- ES strings.
- Tests: `regions/tests/test_geoip.py` (how the override header and `GEOIP_OVERRIDE_HEADER_ENABLED` are set in tests), `mytp_publicsite/tests/test_home.py`, `test_legal_seo.py`, `test_cross_sites.py`; browser tests `mytp_publicsite/tests/browser/` (conftest `plain_http`, skips without Chromium). `regions/tests/test_guard.py`.
- Privacy: the public text `mytp_publicsite/templates/publicsite/legal/_privacy_body.html:11-13,29,66-67` says the site places no cookies (already untrue: csrftoken, sessionid, remembered device). No lawyer notes document exists yet.

## Tasks & Acceptance

**Execution:**
- [ ] `regions/suggestion.py` (+ templatetag, partial) -- the check order, bot regex, bar rendering.
- [ ] `regions/suggestion.py` `stay` view, `mytreatplan/urls.py` -- the cookie and safe redirect.
- [ ] `templates/base.html`, public templates -- the block, the bar, the attribution.
- [ ] `locale/es/LC_MESSAGES/django.po` -- ES texts.
- [ ] `_bmad-output/initiative-mytreatplan-portal-admin/privacy-notes-for-lawyer.md` (new) -- the cookie list and the geo-IP note; read the cookie names, lifetimes and flags from settings and `accounts/devices.py`.
- [ ] `regions/tests/test_suggestion.py` (new) -- the I/O matrix on both profiles with the override header; one browser test: SA shows the bar, "Stay here" returns to the same page with no bar, and the next page has none.

**Acceptance Criteria:**
- Given the ES interface on the eu profile and SA, when the home page loads, then the bar is in Spanish and names Arabia Saudí.
- Given any page, when the footer renders, then the DB-IP attribution links to https://db-ip.com.

## Implementation Notes

- The bar's text says "MyTreatPlan" (the site's brand spelling), not "MyTreatplan" as written in the intent.
- django-countries 9.1 names SA "Arabia Saudita" in Spanish, not "Arabia Saudí" as in the acceptance line; the bar uses django-countries' name as the Boundaries require, and the test asserts "Arabia Saudita".
- The browser test lives in `mytp_publicsite/tests/browser/test_region_bar.py` (that folder's conftest gives the Chromium skip and `plain_http`). Headless Chromium's UA contains "HeadlessChrome" (a bot to the bar), so the test's browser context sends a desktop UA. It re-creates the SA map row, since an earlier transactional test may flush the seeded map.
- `assets/css/site.css` gains `@source "../../regions/templates"` so Tailwind sees the partial's classes.
- `regions/tests/test_suggestion.py` restores the thread's language after each test: requests to /es/ left Spanish active and broke `test_lockout_after_five_wrong_passwords`, which translates a lazy message in the test thread.

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, opus, 2026-11-04): 3 findings. high 0, medium 1, low 1, false 1, maybe-false 0.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | Acceptance says the Spanish bar names "Arabia Saudí"; the code uses django-countries' "Arabia Saudita" | false | rejected | The criterion is outside the frozen block and was the planner's illustration; the Boundaries require the django-countries name, which signup also uses. The fix would be editing this plan. (The "MyTreatplan"/"MyTreatPlan" spelling in the same finding is put to the user separately.) |
| 2 | "Go to {host}" is white on `bg-green` (#7fb800, ~2.4:1, fails WCAG AA), `rounded-full`, and no link in the bar has a focus-visible style | medium | patch | Real: every other pill button uses `bg-ink text-white hover:bg-green hover:text-ink focus-visible:shadow-focus-pill`. Patched: the Go button uses that pill style; Stay here and × get the site's green focus outline. |
| 3 | Lawyer notes say the `messages` cookie is set only when a notice "does not fit the session" | low | patch | Real: `MESSAGE_STORAGE` is unset, so Django's FallbackStorage uses the cookie first. Patched: the row now says it is set whenever a one-time notice is queued and removed once shown. |

Verification after the patches: full suite on SQLite with the CI environment, 1733 passed, 6 failed: the 3 known PostgreSQL-only `test_crypto` cases; 2 `signup/tests/browser/test_success.py` cases that need a built `static/css/site.css` (they pass once it is present); `accounts/tests/test_remembered_device.py::test_ticked_right_code_logs_in_and_sets_the_mark`, which fails identically on the untouched baseline (deferred).

## Verification

**Commands:**
- the full suite (see local dev notes) -- expected: all pass except the 3 known PostgreSQL-only failures.

**Manual checks (if no CLI):**
- On dev with the override header SA: the bar links to dev-ae; Stay here hides it on later pages; ES and a crawler UA see none. Mirror on dev-ae.
