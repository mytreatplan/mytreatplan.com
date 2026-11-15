---
title: 'Doctor area layout and home'
type: 'feature'
ticket: '1'
created: '2026-11-14'
status: 'built'
baseline_revision: '0886c03e5b1381c298ce3ed769f506bbfdeefa1f'
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

**Problem:** A signed-in doctor lands on a placeholder page with a heading and their email; there is no doctor area, navigation or home page (D1), so no later epic has a place to put its pages.

**Approach:** Build the signed-in layout and the home page of the Annex I mockup in EN and ES, with the doctor's name, initials and a profile card, and "coming soon" pages behind every link a later epic fills.

## Boundaries & Constraints

**Always:**
- Every doctor-area page requires a signed-in user and is not indexed.
- EN and ES for every visible string; assets are local (no CDN, no external fonts).
- Every new portal URL is recorded by page records and exists in the test URL configurations.
- The brand is spelled "MyTreatPlan".

**Never:**
- No change to the login, code or remembered-device flow, the signup steps, or the `subscriptions` views' behaviour.
- No footer content change (2.6 owns it) and no change to the public header.
- No migration: profession, city and photo arrive with entries 2 and 4.
- No subscription level in the navigation or on the home page (8.3).

## Decisions

- Decision (user, 2026-11-14): a new home page at `/home/` (per language) that login lands on; the welcome page stays as the page after signup (and after the upgrade screen) and gains a button to the home.
- Decision (user, 2026-11-14): the greeting is "Hello, {first name}".
- Decision (user, 2026-11-14): the profile card's second line is the country name until entry 2 stores profession and city.
- Decision (user, 2026-11-14): each area not built yet has its own address, all served by one "coming soon" page naming the area; each epic later replaces its address.
- Decision (user, 2026-11-14): the messages and tutorials tiles and the messages icon are shown, leading to "coming soon"; that they have no owning epic is recorded in `deferred-work.md`.
- Decision (user, 2026-11-14): the header logo is the isotype (the tooth outline of the logo set); the user provides the file.
- Decision (user, 2026-11-14): the layout matches the mockup now (gradient header, white rounded panel over it, the two large tiles, the green bar, the handshake band above the footer).
- Decision (user, 2026-11-14): on narrow screens the header collapses into the public site's menu-button pattern and the tiles stack in one column.
- Decision (user, 2026-11-14): the welcome page, the upgrade screen and the purchase placeholder adopt the doctor header; login and signup keep the light one.
- Decision (user, 2026-11-14): staff accounts see the same home; the language switcher stays in the header; merge and staging for this entry are delegated.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Login | right password and code | lands on `/<lang>/home/` | none |
| Login with `next` | a safe `next` address | lands there, as today | unsafe `next` → home |
| Signup ends | levels on offer / none | upgrade screen / welcome page, as today; welcome has a button to home | none |
| Home, EN and ES | signed-in doctor | "Hello, {first name}" / "Hola, {first name}", the intro line, both large tiles, the bar, the profile card, the three small tiles | none |
| Profile card | title, names, country set | "{title} {first} {last}" in bold, the country name in the page language | none |
| Initials | first and last name / only one / neither | two letters / one letter / the email's first letter, upper case, in the avatar | none |
| Anonymous visitor | any doctor-area address | redirected to login with `next` | none |
| Area not built | patients, support, messages, settings, cart, tutorials, VirtuaOrtho, planning services, start a case | 200, "coming soon" naming the area, a link back to home, EN and ES | unknown area → 404 |
| Header | every doctor-area page | isotype → home; home (marked current on home), patients, support; messages, settings, cart icons with accessible names; avatar; language switcher; logout as a POST form | none |
| Narrow screen | below the `tablet` breakpoint | menu button opens the same links; tiles in one column | none |
| Other signed-in pages | welcome, upgrade, purchase placeholder | doctor header; their content and behaviour unchanged | none |
| Login and signup pages | anonymous | light header, unchanged | none |
| Page records | a doctor opens home or an area | a page view is recorded with the URL name | none |

</frozen-after-approval>

## Code Map

- Mockups: only inside `MyTP_Reqs_Def_V1_21Oct2026_v3.docx` (two levels above the repo), Annex I, `word/media/image1.png` (home, Spanish) and `image2.png` (personal data, entry 2). Home: header gradient cornflower → pale; nav "home · pacientes · soporte", icons speech bubble, gear, cart, oval avatar, violet "Logout" pill; "Hola" + "Aquí tienes algunos accesos rápidos que te pueden ayudar."; tiles "Virtua ORTHO / Alineadores, planificación y soporte clínico todo en uno" and "Servicios de Planificación / Planificación y soporte experto en cada paso"; green bar "Solicita Nueva Planificación"; grey profile card (oval photo, bold name, "Ortodoncista · Madrid"); three green-tint tiles "Datos personales / Configuración", "Mensajes / Alertas", "Bienvenida / Tutoriales"; handshake image; footer.
- `accounts/views.py:61` `LOGIN_REDIRECT = 'welcome'` (used `:95`, `:105`); `:376-379` logout (POST → login); `:382-385` welcome view; `accounts/urls.py:13`; `accounts/templates/accounts/welcome.html` (h1 pinned by browser tests: `accounts/tests/browser/helpers.py:105,167`, `signup/tests/browser/test_success.py:111,157`).
- `signup/views.py:140,496` -- redirects to `upgrade` / `welcome`.
- `mytreatplan/urls.py:51-61,77-82` -- `portal_patterns` under `PORTAL_ENABLED` and `i18n_patterns`. `accounts/page_records.py:40-41` `PORTAL_PREFIXES`. `mytp_publicsite/tests/test_settings_staging.py:222,233,270` `PORTAL_URL_NAMES`. Test URL configurations: `signup/tests/urls_portal.py`, `mytp_publicsite/tests/urls_with_portal.py:19`.
- `templates/base.html` (blocks title, head, region_bar, header, content, footer) → `templates/portal_base.html:10-26` (noindex, light header, `includes/_portal_links.html`, `includes/_language_switcher.html`, `portal_width` block).
- `assets/css/site.css:8-17` `@source` list (a new app's templates must be added); theme colours exist: `cornflower`, `periwinkle`, `mint`, `sky`, `green-tint`, `card-grey`, `violet`, `green-ui`; breakpoint `tablet`; `static/js/menu.js` (menu button); `static/img/virtuaortho-remote-orthodontist.webp`, `Img_Contact*.webp`; icons are inline SVG.
- `accounts/models.py:17-25,65-128` -- `title`, `first_name`, `middle_name`, `last_name` (encrypted: initials in Python), `email`, `country`.
- `subscriptions/urls.py:10`, `upgrade_purchase.html` -- the existing "coming soon" wording.
- `mytp_publicsite/tests/test_home.py:33` (local assets), `test_i18n.py:130+` (`.po` checks).
- Later owners: patients, tiles, bar, cart → epic-ordering 2, 5, 6; support → epic-support; settings → 6.2; photo → 6.4; level reminder → 8.3.

## Tasks & Acceptance

**Execution:**
- [ ] `static/img/isotype.svg` -- already in the working tree (made from the user's PDF, single path, `fill="currentColor"`, checked against the PDF): use it inline or as a file so the header can colour it white; do not redraw it.
- [ ] `templates/doctor_base.html` (extends `portal_base.html`), `templates/includes/` -- the doctor header and the white panel of the mockup; inline SVG icons with accessible names; avatar with initials; logout form; language switcher; menu button below `tablet` through `static/js/menu.js`.
- [ ] `accounts/views.py`, `accounts/urls.py`, `accounts/templates/accounts/home.html`, `coming_soon.html` -- the `home` view and one `coming_soon` view behind named routes `patients`, `support`, `messages`, `settings`, `cart`, `tutorials`, `virtuaortho`, `planning_services`, `start_a_case`; all `@login_required`; `LOGIN_REDIRECT = 'home'`.
- [ ] `accounts/` (a small helper module) -- display name and initials from the decrypted names, with the fallbacks of the matrix.
- [ ] `accounts/templates/accounts/welcome.html`, `subscriptions/templates/…/upgrade.html`, `upgrade_purchase.html` -- extend `doctor_base.html`; welcome gains the button to home; its heading and the upgrade behaviour stay.
- [ ] `accounts/page_records.py`, `signup/tests/urls_portal.py`, `mytp_publicsite/tests/urls_with_portal.py`, `mytp_publicsite/tests/test_settings_staging.py` -- the new prefixes and URL names.
- [ ] `assets/css/site.css`, `locale/es/LC_MESSAGES/django.po` -- any new `@source` line or token; the Spanish strings, the mockup's wording where it has one.
- [ ] Tests -- one per matrix row (EN and ES where the row says so); the assertions that expect login to land on `welcome` move to `home`; one browser test for login → home → an area → back, and the menu button on a narrow viewport.
- [ ] `_bmad-output/…/deferred-work.md` -- messages and tutorials have no owning portal epic; the corporate-style review now also covers the doctor layout.
- [ ] `README.md` -- the doctor area: the layout template, how an epic replaces a "coming soon" address.

**Acceptance Criteria:**
- Given the suite, when it runs, then 0 failed, and `makemigrations --check --dry-run` reports no changes.
- Given the home page in each language, when its HTML is read, then no asset comes from another host and no string is untranslated in ES.
- Given the signup flow's browser tests, when they run, then signup still ends on the upgrade screen or the welcome page with its current heading.
- Given dev after the deploy, when a doctor signs in, then the home matches the mockup in EN and ES with their name, initials and profile card, and every header and tile link opens a "coming soon" page.

## Implementation Notes

## Plan Change Log

## Review Triage Log

Quick lens (Opus), 2026-11-15: 6 findings (2 medium, 4 low), all patched by the implementer; none deferred.

| # | Severity | Finding | Verdict |
|---|----------|---------|---------|
| 1 | medium | The matrix row "Other signed-in pages" was tested only for welcome | patched: tests for the upgrade screen, the purchase placeholder, and welcome as staff, superuser and a manual-subscription doctor |
| 2 | low | `mytp_publicsite/tests/urls_with_portal.py` had only the `home` stub | patched: the nine area stubs |
| 3 | medium | The public site's "Client portal" link still led a signed-in doctor to the welcome page | patched: the link's target is `home` (only the target; told to the user as a change inside the public header's include) |
| 4 | low | A signed-in visitor opening signup went to welcome, one opening login to home | patched: both go to `home`; the end of a signup is unchanged (told to the user) |
| 5 | low | Initials of a name stored decomposed showed the bare base letter | patched: names normalised to NFC; tests with decomposed, non-Latin and HTML-character names |
| 6 | low | Each area was named differently on its tile and on the page it opens | patched: one msgid per area; the bar is one string, so it lost the mockup's two weights (left to the style review) |

Outside the review: three tests in `subscriptions/tests/test_service.py` (8.1) began to fail on 2026-11-15 because they set an end date of 2026-11-14 with a start date of today; they now pass their start date. Recorded in `deferred-work.md`.

## Design Notes

- The isotype: the user provided `AAFF_MytreatPlan_IsotipoDef_RGB.pdf` on 2026-11-14, kept two levels above the repo in `Isotipo_MyTreatPlan/`. A vector PDF, one variant per page: gradient (blue to lime) on white and on dark, white on black and on dark grey, dark grey on white, black on white. The mockup header uses the white outline on the blue gradient. Convert the needed page(s) to an optimised SVG in `static/img/` (no converter is installed: use a throwaway virtualenv with PyMuPDF or similar, outside the repo); one SVG using `currentColor` covers the white, grey and black variants.
- Working notes for the implementer: the branch is `story/6.1-doctor-area-home`, already created in the portal checkout; do not create another branch and do not commit. 2.6 is merged: the footer and the context processor `regions/context_processors.py` are in place. Baseline suite: 3082 passed, 12 skipped. The mockup images are not in the repo; an extracted copy of the home mockup is at `/private/tmp/claude-501/annex/word/media/image1.png` (temporary; read it first, and if it is gone work from the description in the Code Map). Do not copy the mockup into the repo.

## Verification

**Commands:**
- the portal's full suite command (SQLite, `../.venv`, sandbox off) -- expected: 0 failed
- `npm run build:css` -- expected: builds; the new templates' classes are present
- `manage.py makemigrations --check --dry-run` -- expected: no changes
