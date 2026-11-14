---
title: 'Upgrade screen and welcome page'
type: 'feature'
ticket: '2'
created: '2026-11-14'
status: 'built'
baseline_revision: '68680f4b5e054c63de737ae57284470e70f34e0d'
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

**Problem:** After signup a doctor lands on the welcome page and never sees the subscription levels: nothing shows what Advanced and Premium cost in their country group or leads towards buying one (S3).

**Approach:** Add the portal's upgrade screen: the three levels of the doctor's country group with their net prices and "+ VAT where applicable", the doctor's current level marked, upgrade buttons and "Not now", which leads to part-1's welcome page. Signup sends the new doctor there. A doctor on a manual subscription sees no upgrade buttons. Portal only.

**Decisions (user, 2026-11-14):**
- The screen is a new page `/upgrade/`. Signup redirects to it; "Not now" goes to the welcome page; a later login still lands on the welcome page.
- Any signed-in doctor can open `/upgrade/` later (entry 3's reminder will link to it). It is shown automatically only once, after signup.
- Prices are the net monthly and upfront prices typed on the group's subscription, read through a new function of the subscription service. A level is listed only when its row is active and its group has a `subscription` billing rule.
- Both periods are shown: the monthly price with "12-month commitment" and the price for a year upfront. A missing one is left out. Basic shows "Free".
- Nothing to offer (no country group, or the group sells neither Advanced nor Premium): the screen is skipped. Signup goes straight to the welcome page and `/upgrade/` redirects there.
- A doctor on a manual subscription sees the screen with the special subscription's name as the current level, the standard levels for information, no upgrade buttons, and "Continue".
- Until entry 5 builds the purchase page, each upgrade button leads to a placeholder page `/upgrade/<level>/` saying online purchase is coming soon. Entry 5 replaces it.
- Staging: the user signs up one new ES doctor on dev. A manual "Ormco Premium" for Iberia is created on admindev by script and assigned to that doctor, who stays as the manual fixture for entries 3 and 10.
- Merge and staging are delegated for 8.2.
- Money is shown as the number formatted for the page's language plus the currency code ("60.00 EUR", "60,00 EUR"). Spanish wording: "+ IVA cuando corresponda". The page uses a wider column than the portal's 480 px. Staff accounts skip the screen.

## Boundaries & Constraints

**Always:** The views read subscriptions only through `subscriptions.service` (the structure test). Prices come from the catalogue through the resolver (AD-16), never typed in a template. Every new string has its Spanish translation. Both new routes are signed-in only and recorded as portal pages. The server decides whether buttons are shown and refuses the placeholder page to a doctor who cannot switch.

**Never:** No purchase, order, payment or change of the subscription record. No new field on the user or the record, no migration. No VAT amount or gross price. No change to the `welcome` route, its template's heading or the login redirect. No admin or mytp-core change.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| New doctor, full group | signup completes; group sells Basic, Advanced, Premium | redirect to `/<lang>/upgrade/`; three levels, Basic marked current, net prices with currency and "+ VAT where applicable", a button on Advanced and Premium, "Not now" → welcome | none |
| One period missing | Advanced has a monthly price, no upfront price | only the monthly price is shown | none |
| Level missing or not sellable | group has no Premium, or the row is inactive, or the group has no `subscription` rule | that level is not listed; if neither Advanced nor Premium is left, the screen is skipped | none |
| Nothing to offer | no country group (or the case above) | signup redirects to welcome; GET `/upgrade/` redirects to welcome | none |
| Manual doctor | record manual and counted (e.g. "Ormco Premium") | screen shows the special name as current, the standard levels, no buttons, "Continue" → welcome | GET `/upgrade/<level>/` redirects to `/upgrade/` |
| Paid Advanced doctor (fixture) | effective tier advanced | Advanced marked current, button only on Premium | none |
| Premium doctor (fixture) | effective tier premium | Premium current, no button | none |
| Placeholder | GET `/upgrade/advanced/` by a Basic doctor | page naming the level, "coming soon", link back to `/upgrade/` | unknown level, the current level or a lower one: 404 |
| Staff account | staff or superuser signed in | `/upgrade/` redirects to welcome | none |
| Signed out | GET `/upgrade/` | redirect to login with `next` | none |
| Spanish | `/es/upgrade/` | names and descriptions in Spanish, "60,00 EUR", "+ IVA cuando corresponda" | none |

</frozen-after-approval>

## Code Map

- `signup/views.py:405-485` `_create_account` -- ends with `redirect('welcome')` (:485); `anonymous_only` (:130-139) also sends a signed-in user to `welcome` (leave it). About 40 asserts in `signup/tests/` (wizard, mobile code, duplicates, legal step, country check, event log, home region), `subscriptions/tests/test_service.py:61`, `accounts/tests/conftest.py:38` and the browser helpers (`signup/tests/browser/test_success.py:110-114`, `accounts/tests/browser/helpers.py:60-66`) pin `/en/welcome/` as the end of signup.
- `accounts/views.py:382-385` `welcome` (`@login_required`, `accounts/welcome.html`, URL name `welcome`); login lands there (`LOGIN_REDIRECT`, :61). Do not change them.
- `subscriptions/service.py` -- the only module naming the models (`subscriptions/tests/test_structure.py:149` scans every non-test module, strings included). `effective(user, day)` → `Effective(tier, subscription, held, manual, can_self_switch, record)`; staff have no counted record (`accounts.client_sync.is_client` is the doctor test).
- `catalogue/resolver.py` -- `subscription(country, tier)` (:306, the group's active standard row or None), `country_group` (:210), `subscription_rule(subscription)` (:292). `catalogue/models.py:191-282` `Subscription`: `TIERS` (in order), `name_in(lang)`, `description_in`, `free`, `monthly_price`, `upfront_price` (`Money` or None).
- No money filter exists and no page shows a price yet (`Money.__str__` is `60.00 EUR`). Template tags live in `mytp_publicsite/templatetags/` and `regions/templatetags/`.
- `templates/portal_base.html` -- `portal_content` block inside a 480 px column (:28). Tailwind 4 source `assets/css/site.css`, tokens `design/tokens.json`.
- `accounts/page_records.py:40` `PORTAL_PREFIXES` + `accounts/tests/test_page_records.py:211` -- every portal route needs its prefix. `mytreatplan/urls.py:57` mounts portal routes under `PORTAL_ENABLED`.
- i18n: `mytp_publicsite/tests/test_i18n.py` (:16 the `makemessages` command; :168, :175, :203 fail on missing, fuzzy or broken Spanish). Only `locale/es/LC_MESSAGES/django.po` is committed.
- Test data: `integration/tests/snapshot_rows.py` (`billed()`, manual `MANUAL` code `ORMCO`, only Iberia has a `subscription` rule, no standard Premium), `subscriptions/tests/helpers.py` (`catalogue()`, `load`, `doctor`, `MANUAL`, `ADVANCED`); a manual doctor is made with `service.assign_manual(...)` (`test_service.py:273-293`).
- `deploy/README.md` "Doctor subscriptions" (:3281-3332) -- where the screen and its staging check are documented.

## Tasks & Acceptance

**Execution:**
- [x] `subscriptions/service.py` -- add `upgrade_options(user, day=None)`: the doctor's current level (tier, display name, manual), the listed levels in `TIERS` order (tier, name and description source, free, monthly and upfront `Money` or None, current, can upgrade) and whether anything is on offer; a level is listed by the decisions above; export it -- the one place the screen's rules live.
- [x] `subscriptions/views.py`, `subscriptions/urls.py`, `mytreatplan/urls.py` -- views `upgrade` and `upgrade_purchase` (placeholder), URL names `upgrade` and `upgrade_purchase`, under the language prefix and `PORTAL_ENABLED`; behaviour by the matrix; through the service only.
- [x] `subscriptions/templates/subscriptions/upgrade.html`, `upgrade_purchase.html`, `assets/css/site.css` if needed -- the screen (wider column, levels side by side on a desktop, stacked on a phone, current level marked in text, not by colour alone) and the placeholder.
- [x] a money template filter beside the existing template tags -- `Money` → number in the active language plus the currency code -- first price shown on the portal.
- [x] `signup/views.py` -- `_create_account` redirects to `upgrade` when the service says something is on offer, else to `welcome`.
- [x] `accounts/page_records.py` -- add the new prefix.
- [x] `locale/es/LC_MESSAGES/django.po` -- Spanish for every new string.
- [x] `subscriptions/tests/` (service, views), `signup/tests/`, `accounts/tests/`, the browser helpers and one browser test -- cover every matrix row; add a standard Premium and upfront prices to the test catalogue; update the asserts and fixtures that pin the end of signup.
- [x] `deploy/README.md` "Doctor subscriptions" -- the screen, the placeholder, and how to assign a manual subscription from the shell for the staging fixture.

**Acceptance Criteria:**
- Given a doctor whose group sells the three levels, when signup completes, then the browser is on the upgrade screen and "Not now" opens the welcome page.
- Given the same doctor signing in later, when login completes, then the welcome page opens, and `/upgrade/` still shows the screen.
- Given any non-test module of `subscriptions` other than the service, when the structure test runs, then it passes.
- Given the full suite, when it runs, then it passes with no new skip, and `makemigrations --check` reports no change.

## Implementation Notes

- `subscriptions/service.py`: `upgrade_options(user, day=None)` → `UpgradeOptions(tier, subscription, manual, can_switch, levels, on_offer)` with `Level(tier, subscription, free, monthly, upfront, current, can_upgrade)`. A level is listed when the group's standard row of its tier passes `resolver.subscription_sold(row, rule)` (new in `catalogue/pricing.py`, taken out of `price_subscription`, which now calls it: active row, an available `subscription` rule in a known currency, a priced row in the rule's currency) and, for a paid level, has at least one price. No level is marked current for a manual doctor.
- `subscriptions/views.py`, `urls.py`, two templates: `upgrade` and `upgrade_purchase`, mounted in `mytreatplan/urls.py` `portal_patterns`; `upgrade/` added to `PORTAL_PREFIXES`. `templates/portal_base.html` gained a `portal_width` block (default 480 px; the screen uses 1040 px); `assets/css/site.css` scans `subscriptions/templates`.
- `mytp_publicsite/templatetags/money.py`: `{{ price|money }}`, grouped per language: "1,200.00 EUR" in English, "1 200,00 EUR" in Spanish (Django's Spanish format groups with a no-break space; kept), '' for None or a value that is not a `Money`.
- `signup/views.py`: the end of `_create_account` redirects to `upgrade` when something is on offer; any error reading the options is logged and the doctor goes to `welcome`.
- Spanish: 18 entries added by hand to `locale/es/LC_MESSAGES/django.po` (a `makemessages` run would have rewritten the whole file); `subscriptions` added to the i18n test's folders.
- Tests: `subscriptions/tests/test_upgrade.py` (every matrix row, the money filter, the page record), a browser test in `signup/tests/browser/test_success.py`; the test catalogue gained a standard Premium and upfront prices. The signup tests that pin `/en/welcome/` load no catalogue, so they still end on welcome and were left.
- No migration. `deploy/README.md` "Doctor subscriptions": the screen, the placeholder, the shell recipe to assign a manual subscription, the staging checks.

## Plan Change Log

## Review Triage Log

Quick lens, one pass (2026-11-14): 8 findings, 0 high, 3 medium, 5 low, 0 false; 7 patched, 1 deferred.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | A level was listed whenever a `subscription` rule existed, also one not available or in another currency | medium | patch | `resolver.subscription_rule` returns any active rule; now `subscription_sold`, the condition `price_subscription` applies; tests for both cases |
| 2 | A paid level with no price showed "Free" with a button | medium | patch | `Subscription.free` is True without amounts; `free` is Basic only, a paid level without any price is not listed; tests |
| 3 | The current name (the held row) and the marked card (the profile country's group) can come from two groups after a country change | low | defer | real, but a doctor cannot change country until epic-doctor-profile; entry in deferred-work.md |
| 4 | The end of signup read the catalogue unguarded after the commit: an error was a 500 for an existing account | medium | patch | against the invariant at `signup/views.py:436-438`; guarded, logged, welcome; test |
| 5 | The money filter raised on a value that is not a `Money` | low | patch | returns '' ; test lines |
| 6 | The money filter never grouped thousands | low | patch | `force_grouping=True`; Spanish groups with a no-break space (Django's format), kept |
| 7 | The staff row of the matrix was tested with a superuser only | low | patch | both tests now also run for a staff account that is not a superuser |
| 8 | The README recipe lacked an import and mixed shell and Python prompt lines | low | patch | one `manage.py shell -c` block with the import |

## Design Notes

Work on the branch `story/8.2-upgrade-screen`, already checked out in this checkout. Do not create another branch and do not commit.

The placeholder page is refused (404) for the current level and lower ones so entry 5 inherits a route that only ever names a real upgrade. The level names come from the catalogue row (`name_in`), falling back to the translated tier name when the row has no text.

## Verification

**Commands:**
- `export FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU="; export DATABASE_URL="sqlite:///$TMPDIR/x.sqlite3"; ../.venv/bin/python -m pytest -q -p no:cacheprovider --tb=line` -- expected: 0 failed (2970 passed, 12 skipped before this story).
- `../.venv/bin/python manage.py makemigrations --check --dry-run` (same environment) -- expected: no changes.
