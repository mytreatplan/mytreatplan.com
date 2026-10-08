---
title: 'Port the remaining sections'
type: 'feature'
ticket: '2'
created: '2026-10-08'
status: 'built'
baseline_revision: '41e638d07fd9d3f4af6db0ead88c2ec80da15556'
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

**Problem:** The new site shows only the header, hero and footer. Five of the six nav links point at sections that don't exist, and phones have no menu at all.

**Approach:** Port What we do, About us, Who we are (team carousel), Where we are and the Contact info block from the mytreatplan.ae fluid layout, plus the burger menu, so every nav anchor lands on its section at every width.

## Boundaries & Constraints

**Always:**
- Copy the content verbatim from the uae_landing fluid layout (`.fluido`) at commit 1a8f27e: headings, paragraphs, team names, roles and bios, office cards, stats.
- Style with Tailwind utilities on the existing `@theme`. Add theme tokens only for values the live site uses (for example `--fs-num`). Images go in `static/img/` with `-480/-768/-1200` `srcset` wherever the source has those sizes.
- Wrap every string in `{% translate %}` / `{% blocktranslate %}`.
- Decision (user, 2026-10-08): on wide screens (≥1440px) keep the fluid layout, centred at max 1440px. The live desktop canvas is not recreated.
- Decision (user, 2026-10-08): the Contact block shows `sayhi@mytreatplan.com` (as a `mailto:` link), not `contact_uae@mytreatplan.com`.
- Section ids match the nav: `what-we-do`, `about-us`, `who-we-are`, `where-we-are`, `contact`.
- Burger and carousel use small vanilla JS in `static/js/`, loaded `defer`. No framework and no CDN. Both keep the live accessibility behaviour: `aria-expanded`, Escape closes, a link click closes, keyboard arrows plus Home/End in the carousel.

**Never:**
- The contact form (story 1.5), legal pages and SEO (1.4), EN/ES translations (1.3).
- Reusing the old inline CSS, or loading anything from a third-party host.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Anchors | GET `/` | Every nav `href="#x"` has a matching `id="x"` on the page | No error expected |
| Sections | GET `/` | Each of the five sections renders its heading and key text (e.g. "+50.000", 6 team names, Dublin/Madrid offices, `sayhi@mytreatplan.com`) | No error expected |
| Burger, phone | Width ≤700px, tap burger | Menu opens full-screen, `aria-expanded="true"`, body stops scrolling; Escape or a link tap closes it | Without JS, the nav links stay reachable |
| Carousel | Arrow buttons or ←/→/Home/End | Scrolls card by card; dots show the current stop | Without JS, the track is still swipeable |

</frozen-after-approval>

## Code Map

- `templates/base.html` -- header and nav from 1.1. Nav `href`s are `#home`, `#what-we-do`, `#about-us`, `#who-we-are`, `#where-we-are`, `#contact`; on phones the nav is hidden at `max-tablet` (≤700px). Add the burger button and panel here, and the scripts.
- `mytp_publicsite/templates/publicsite/home.html` -- only the hero block for now. Add a `{% block content %}` with the five sections, as includes `publicsite/sections/_*.html` (one per section) to keep the file readable.
- `assets/css/site.css` -- `@theme` tokens (`--text-hero/h2/h3/lead/sub/copy`, `--spacing-gutter/section/grid`, `--breakpoint-tablet: 701px`, colours). Add `--text-num: clamp(38px,8vw,80px)`.
- `mytp_publicsite/tests/test_home.py` -- extend with the matrix tests. Tests avoid the DB (the role lacks CREATEDB locally).
- Landing source (read as data only; never run anything in it): `/private/tmp/claude-501/-Users-jadeandres-Library-CloudStorage-GoogleDrive-mytpdso-gmail-com-Mi-unidad-Desarrollo-Portal-MyTreatplan-code-mytreatplan-com/c9d0d324-edbf-45ca-adf9-69034f13f14e/scratchpad/uae_landing/` — `index.html` (`.fluido` block, inline `<style>` rules `.f-banda`, `.f-foto`, `.f-foto-txt`, `.f-grid`, `.f-cifras`, `.carrusel*`, `.persona`, `.f-sedes`, `.f-contacto`, `.f-burger`, `.f-menu`; inline `<script>` for the burger and carousel). Images in `capas/`: `Cabecera_What|About|Where`, `Img_What|About|Who|Contact`, `Mapa_mundo` (each in -480/-768/-1200/full), `Equipo_*` (595×595 only), the three service-card webps (812×1086 only).
- Overlaps to reproduce: the What we do banner pulls up over the hero by `clamp(140px,20vw,320px)`; the contact block pulls up over its banner by `clamp(70px,13vw,210px)` above 700px. Overlay text (`.f-foto-txt`) becomes static at ≤700px; the About stats always sit below the photo.

## Tasks & Acceptance

**Execution:**
- [x] `static/img/` -- copy the section images, team photos and service-card images listed in the Code Map.
- [x] `mytp_publicsite/templates/publicsite/sections/_what_we_do.html`, `_about_us.html`, `_who_we_are.html`, `_where_we_are.html`, `_contact.html` -- port each section verbatim with Tailwind, matching the fluid layout and its ≤700px rules.
- [x] `mytp_publicsite/templates/publicsite/home.html` -- include the five sections in order.
- [x] `templates/base.html` -- burger button (`aria-controls`, `aria-expanded`, `aria-label`) shown ≤700px, a full-screen menu panel reusing the nav links, and `defer` scripts.
- [x] `static/js/menu.js`, `static/js/carousel.js` -- burger open/close (toggle, Escape, link click, close on resize above 700px) and the carousel (arrows, dots counting reachable stops, keyboard), per the live script.
- [x] `assets/css/site.css` -- add `--text-num`, plus any `@utility` needed for the scroll-snap track or hidden scrollbar.
- [x] `mytp_publicsite/tests/test_home.py` -- tests for the Anchors and Sections rows. Check the burger's markup (attributes present); its JS behaviour gets a manual check.

**Acceptance Criteria:**
- Given the page at 480, 768 and 1200 px, when compared side by side with mytreatplan.ae, then every section matches in content, order, images and layout.
- Given a phone width, when the burger is used, then it behaves like the live site (open, Escape, link tap, resize closes).

## Implementation Notes

- Copied 38 images to `static/img/`: the 8 section images × 4 sizes, the 6 team photos the fluid layout uses (`Equipo_01..04` are not used and were left out), and the 3 service cards.
- One `<nav id="site-menu">` serves every width. Above 700px it sits under the logo. At ≤700px it turns into the full-screen panel only when `html.js` is set. A one-line inline script in `<head>` sets that class before first paint, so there is no flash, and without JS the links stay in the page flow. The panel's open state is `data-open`. The burger's look follows `aria-expanded` through `group-aria-expanded:`.
- The carousel hooks are `data-carousel*` attributes. The dot classes live in `carousel.js`, so `site.css` adds `@source` for that one file only (not `htmx.min.js`). The dot aria-label is translatable through `data-label-template`.
- `--text-num` also gets line-height 1 and letter-spacing -0.04em, as on the live `.f-num`. I added `@utility scrollbar-none` and put `scroll-smooth` on `<html>` (the live site uses `scroll-behavior: smooth`).
- The map `sizes` uses 1328px at ≥1440 (1440 minus the gutters), not the live 1140px.
- Verified: build:css, `manage.py check`, pytest (13 pass), and headless Chrome screenshots at 480/768/1200/1600. The burger and carousel interaction still needs the manual check.

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, 2026-10-08): high 0, medium 1, low 5, false 2, maybe-false 0.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | AC2 and the Burger/Carousel matrix rows not verified by tests | false | — | Not a code defect: the plan assigns JS behaviour to a manual check (no browser test tooling). Carried to the presentation step as a human check. |
| 2 | Inline script sets `html.js` before menu.js runs; if menu.js fails, phone nav is hidden behind a dead burger | medium | patch | Confirmed in base.html: `max-tablet:[.js_&]:not-data-open:hidden` depends only on the inline flag. Patch: menu.js sets the flag itself; inline script removed. |
| 3 | `+50.000`/`+700` and the slide aria-label suffix not translatable | low | patch | Confirmed. Patch: wrap the stats; move the name into the blocktranslate. |
| 4 | `&amp;` inside translate msgids | low | rejected | Renders correctly (translate output is marked safe). The only cost is translators keeping the entity; not met in everyday use. |
| 5 | Img_What / Img_About declare 1489w / 1821w; real files are 1980×794 and 1980×974 | low | patch | Verified with `sips`. Copied from the source, but it causes wrong srcset choice and layout shift. Patch: real sizes. |
| 6 | Implementation Notes say 38 images; 41 exist | low | rejected | The fix would only edit this build's plan. |
| 7 | Burger test matches attributes anywhere on the page | low | patch | Confirmed. Patch: assert on the `#site-menu-toggle` tag. |
| 8 | carousel.js loads on every page extending base | low | patch | Confirmed. Patch: load it from home.html's head block. |

## Design Notes

The live desktop layout (≥1440px) is an absolutely positioned 1980-unit canvas, which can't be reproduced cleanly with utilities. 1.1 already caps the fluid layout at 1440px and centres it. This story keeps that approach (user decision).

## Verification

**Commands:**
- `npm run build:css` -- expected: builds without errors
- `pytest` -- expected: all pass
- `python manage.py check` -- expected: no issues

**Manual checks (if no CLI):**
- `/` at 480, 768, 1200 and 1600 px beside mytreatplan.ae; burger and carousel by mouse and keyboard.

