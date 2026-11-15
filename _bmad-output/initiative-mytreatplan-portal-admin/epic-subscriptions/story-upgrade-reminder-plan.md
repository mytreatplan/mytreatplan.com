---
title: 'Upgrade reminder'
type: 'feature'
ticket: '3'
created: '2026-11-15'
status: 'built'
baseline_revision: '83ef699'
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

**Problem:** After signup nothing reminds a Basic or Advanced doctor of their subscription level or leads back to the upgrade screen (S4): the doctor header and home built by 6.1 left the level out on purpose.

**Approach:** Show the doctor's current level with a link to the upgrade screen (`/upgrade/`) in the doctor header and on the home page, only for a doctor who can actually upgrade. Portal only, no migration.

**Decisions (user, 2026-11-15):**
- Header: a short item "{Level} · Upgrade" between the icons and the avatar, also in the phone menu.
- Home: a line in the profile card under its second line: "Level: Basic · See upgrade options".
- Doctors who cannot upgrade (Premium, manual, staff, a group that sells nothing higher) see nothing, not even their level.
- The header item shows on every doctor page except the upgrade screen and its purchase placeholder.
- Merge and staging are delegated for 8.3. Staging: Basic on dev-ae and the manual doctor on dev are checked from the server side, Advanced and Premium in a rolled-back transaction; the visual check is the user's.

## Boundaries & Constraints

**Always:** The rule for who sees the reminder lives in `subscriptions.service` and is built on `upgrade_options` (one rule for the screen and the reminder). Templates and tags read subscriptions only through the service (the structure test). The level name is the same one the upgrade screen shows (the row's name in the page language, else the level label). The service result is computed once per request. Every new string has its Spanish translation. The link goes to `upgrade`, never to `/upgrade/<level>/`.

**Never:** No change to the subscription record, the upgrade screen's rules, prices or the purchase placeholder. No migration, no new setting, no context processor on public pages. No renewal reminder email (entry 6). No admin or mytp-core change.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Basic doctor | group sells Advanced or Premium | header and home show "Basic" and a link to `/<lang>/upgrade/` | none |
| Advanced doctor | group sells Premium | header and home show "Advanced" and the link | none |
| Advanced, no Premium sold | group lists no Premium | no reminder | none |
| Premium doctor | effective tier premium | no reminder | none |
| Manual doctor | manual record that counts | no reminder | none |
| Staff account | staff or superuser | no reminder | none |
| Nothing to offer | no country group, or no paid level sellable | no reminder | none |
| Upgrade pages | GET `/upgrade/`, `/upgrade/<level>/` | no header reminder on these two pages | none |
| Spanish | `/es/home/` | "Básica" / "Avanzada" and the Spanish link text | none |
| Catalogue fault | the service raises while building the reminder | page renders without the reminder; error logged | never a 500 from the header |
| Cost | home page of a Basic doctor | `upgrade_options` runs once for header and home together | none |

</frozen-after-approval>

## Code Map

- `templates/doctor_base.html:17-55` -- header; one `<nav id="site-menu">` (:32-52) serves desktop and the phone panel; link list `<ul data-doctor-nav>` (:35-42), avatar (:45-47), logout (:48-50), language switcher (:51). `here` (:17) is the URL name. Extended by `home.html`, `coming_soon.html`, `welcome.html`, `subscriptions/upgrade.html`, `upgrade_purchase.html`.
- `accounts/views.py:391-401` home view (context `greeting_name`, `profile_name`, `profile_line`); `accounts/templates/accounts/home.html` profile card :41-47.
- `subscriptions/service.py` -- `effective` (:185-210), `upgrade_options` (:297-337) → `UpgradeOptions(tier, subscription, manual, can_switch, levels, on_offer)`, `Level(..., current, can_upgrade)`; staff and no-group give `_NOTHING`. About 12 queries per call.
- `subscriptions/views.py:27-36` -- private `TIER_NAMES` (pgettext "subscription level") and `_name(row, tier, language)`; the reminder must share them.
- `subscriptions/urls.py:9-10` -- `upgrade`, `upgrade_purchase`.
- `subscriptions/tests/test_structure.py` -- non-service modules read through `service` only.
- Tests: `accounts/tests/test_doctor_area.py` (fixture `doctor` :36 has no catalogue → no reminder; header regexes :218-244), `subscriptions/tests/test_upgrade.py` (asserts on the upgrade page: `data-current>` count :248, `'Upgrade to' not in page` :284/:307, `'/en/upgrade/advanced/' not in page` :299), helpers `subscriptions/tests/helpers.py` (`load`, `doctor`), `test_upgrade.py` `on()`, `manual()`, `staff_account()`.
- i18n: `locale/es/LC_MESSAGES/django.po` edited by hand; `mytp_publicsite/tests/test_i18n.py`. Tailwind scans `templates`, `accounts/templates`, `subscriptions/templates` (`assets/css/site.css:8-13`); built CSS is git-ignored.
- `deploy/README.md` "Doctor subscriptions".

## Tasks & Acceptance

**Execution:**
- [x] `subscriptions/service.py` -- add `reminder(user, day=None)`: None unless `upgrade_options` says on offer, can switch and some level can be upgraded to; otherwise the current tier and its subscription row; export it. Move `TIER_NAMES` and the level-name helper out of `subscriptions/views.py` to a module both the views and the tag import (no model named outside the service).
- [x] `subscriptions/templatetags/` (new) -- a tag giving the reminder for the request's user, computed once per request, returning nothing and logging an error if the service raises.
- [x] `templates/doctor_base.html` -- the item "{Level} · Upgrade" linking to `upgrade`, between the link list and the avatar, styled for the desktop row and the phone panel; not rendered when `here` is `upgrade` or `upgrade_purchase`.
- [x] `accounts/templates/accounts/home.html` -- the line "Level: {Level} · See upgrade options" in the profile card.
- [x] `locale/es/LC_MESSAGES/django.po` -- Spanish for the new strings.
- [x] `subscriptions/tests/` (new module) and `accounts/tests/` if a pinned assert changes -- every matrix row, EN and ES, the once-per-request count; keep `subscriptions/tests/test_upgrade.py` passing unchanged.
- [x] `deploy/README.md` "Doctor subscriptions" -- who sees the reminder and where.

**Acceptance Criteria:**
- Given a Basic or Advanced doctor whose group sells a higher level, when any doctor page except the upgrade pages opens, then the header shows the level and a link to `/upgrade/`, and the home profile card shows the line.
- Given a Premium, manual or staff account, or a group with nothing higher to sell, when the home page opens, then no level and no upgrade link appear.
- Given the full suite, when it runs, then it passes with no new skip, `makemigrations --check` reports no change, and the structure test passes.

## Design Notes

A template tag in `subscriptions/templatetags/` (used by `doctor_base.html` and `home.html`) calls a small service function `reminder(user)` → level name source + tier, or None; the tag memoises it on the request. Predicate: `on_offer and can_switch and any(level.can_upgrade)`.

## Verification

Full suite; `makemigrations --check`; `npm run build:css` to see the header locally.

## Implementation Notes

- Level names: `TIER_NAMES` and the helper moved to the new `subscriptions/levels.py` (`level_name(row, tier, language)`); `subscriptions/views.py` imports it as `_name`, so the view code is unchanged. The `.po` references of the three tier names now point at `subscriptions/levels.py` (same msgids, same translations).
- Service: `reminder(user, day=None)` returns a frozen `Reminder(tier, subscription)` (the `UpgradeOptions.subscription` row, i.e. what the screen names as the current level) or None; both exported in `__all__`; the module docstring has an entry for it.
- Tag: `subscriptions/templatetags/upgrade_reminder.py`, `{% upgrade_reminder as reminder %}` gives None or `{'tier', 'name', 'url'}`; the service result (None included, and None after a fault) is kept on the request as `request._upgrade_reminder`; the name is resolved per call in the active language. No request in the context or a signed-out user gives None without calling the service. A fault is `logger.exception` on `subscriptions.templatetags.upgrade_reminder` (ERROR with traceback, so Sentry gets it).
- Header markup: a pill link (`data-upgrade-reminder="<tier>"`) right after the `<ul data-doctor-nav>` and before the avatar, inside the one `<nav id="site-menu">`, so the phone panel shows it too (white text, 20 px there). The `here` check skips the tag call entirely on `upgrade` / `upgrade_purchase`.
- Home markup: a third line in the profile card (`data-upgrade-line="<tier>"`), "Level: {level}" as one string, the separator " · " in the markup, then the link "See upgrade options".
- Spanish: kept the proposed wording, "%(level)s · Mejorar", "Nivel: %(level)s", "Ver opciones de mejora". The 8.2 "Pasar a %(name)s" needs a target level, which the header item has none of (it links to the screen, not to a level), so "Mejorar" reads better; "nivel" matches 8.2's "Su nivel actual".
- No marker the upgrade tests assert absent (`Upgrade to`, `data-current>`, `/upgrade/<level>/`) is in the new markup; the reminder only links to `upgrade`.
- Full suite (sqlite): 3180 passed, 12 skipped, 1 failed: `accounts/tests/test_remembered_device.py::test_ticked_right_code_logs_in_and_sets_the_mark` (`str(doctor.pk)` "1" found in the cookie name's hex digest). It fails the same way on an export of HEAD (83ef699) without this story's changes, so it predates this story. The test is fragile: a hex digest often contains the character "1".
- That failing test is the SECRET_KEY-dependent one already recorded in deferred-work.md.
- Review fixes: (1) `effective(user, day=None, *, log_drift=True)` and `upgrade_options(..., *, log_drift=True)`; `reminder()` passes `log_drift=False`, so a missing or edited held row is not logged once per doctor page; the screen, offers and pricing still log it (their tests unchanged). Test `test_a_drifted_held_row_is_not_logged_by_the_reminder[gone|edited]`. (2) The tag now builds the whole value (service call, `level_name`, `reverse`) inside the one try/except and memoises the built dict (in the language active at first use); test `test_a_failure_in_the_name_lookup_leaves_the_page_without_the_reminder`.
- Session's own full run after the fixes (sqlite, worktree): 3183 passed, 12 skipped, 1 failed (the recorded `test_remembered_device` test); `makemigrations --check`: no changes.
- Tests: `subscriptions/tests/test_reminder.py` (service rows, page rows EN/ES, upgrade pages EN/ES, the fault with its logged error, the once-per-request count for header + home, the tag without request / signed out / memoised). No change to `accounts/tests/` or `test_upgrade.py` was needed (the doctor fixture of `test_doctor_area.py` has no catalogue, so no reminder).

## Review Triage Log

| # | Severity | Finding | Verdict |
|---|----------|---------|---------|
| 1 | medium | The reminder runs `effective()` on every doctor page, so a drifted held row would log an error per page view | patched: `log_drift=False` on the reminder path; the other paths still log |
| 2 | low | `level_name()` and `reverse()` were outside the tag's try/except: a failure there would be a 500 from the header | patched: the whole value is built inside the try |
| 3 | low | Screen readers may read the middle dot in "Basic · Upgrade" aloud | rejected: needs another translated label for little gain; told the user |
