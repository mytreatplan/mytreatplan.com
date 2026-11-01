---
title: 'Country map with its starting lists'
type: 'feature'
ticket: '1'
created: '2026-11-01'
status: 'built'
baseline_revision: '549697ebcbdfca70244977cae975a463c3ea896c'
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

**Problem:** The portal has no notion of which region serves a country. Signup (entries 2–3), the geo-IP suggestion (entries 4–5) and epic-doctor-profile's billing-country block all need one answer: a country's state (eu, ae or banned) and, for the other region, where to send the person.

**Approach:** Add a country map model to the `regions` app (one row per country with its state), shaped so the admin site's configuration snapshot can replace the rows later (AD-3, AD-14). Seed it by data migration with the agreed lists. Add one lookup helper that returns a country's state and, when the country belongs to the other region, that region's site URL and signup URL, built from the 1.6 profiles.

## Boundaries & Constraints

**Always:**
- Seed lists (user, 2026-10-30):
  - ae: AE, SA, KW, QA, BH, OM, JO, LB, IQ, PS, YE, TR, EG, MA, ZA
  - banned: every other African country, plus IR, SY, KP, CU, RU, BY
  - eu: every other country
- The map is read-only on the portal: no admin or edit UI. Until epic-catalogue, a change is a data migration.
- All region literals stay inside `regions/` (guard test). Callers compare against the helper's result or `profile.code`.
- A country missing from the map reads as eu.

**Never:** no signup, geo-IP or footer changes (entries 2–6). No configuration channel or snapshot sync (epic-admin-interface). No new dependency for continent data.

## Decisions

- Other region's URL: a per-region env override (`REGION_SITE_URL_EU`, `REGION_SITE_URL_AE`), defaulting to the profile's production site URL. On staging, dev's settings.env points `REGION_SITE_URL_AE` at https://dev-ae.mytpdso.com and dev-ae's points `REGION_SITE_URL_EU` at https://dev.mytpdso.com (user, 2026-11-01).
- Banned African codes: the 51 sovereign African states other than EG, MA and ZA, plus EH, RE, YT, SH and IO (user, 2026-11-01); also AC and TA, Saint Helena's Ascension and Tristan da Cunha, which only `phonenumbers` produces (user, 2026-11-01, after review).
- Key: the ISO alpha-2 code is the primary key. The model is not marked `CROSSES_SITES`; the admin snapshot replaces rows by code (user, 2026-11-01).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Own region | `ES` on an eu site | state eu, no other-region URLs | No error expected |
| Other region | `AE` on an eu site | state ae, the ae site URL and its signup URL | No error expected |
| Banned | `IR`, `NG` | state banned, no URLs | No error expected |
| Lowercase or blank | `ae`, `''`, `None` | `ae` is handled like `AE`; blank or `None` reads as eu | No error expected |
| Not in map | a code with no row (e.g. `XK`) | eu | No error expected |

</frozen-after-approval>

## Code Map

- `regions/profiles.py` -- `RegionProfile` (`code`, `domains`, `site_url` :54), `PROFILES` :68, `get_profile` :100. Keep it free of Django imports, because settings imports it. The other region is the profile whose code differs from `settings.REGION_PROFILE.code`. The ae profile offers only `en`, so the signup URL must use a language the target profile supports.
- New: `regions/models.py`, `regions/migrations/0001_*` (schema) and `0002_*` (seed, `RunPython` with `apps.get_model` and a reverse). Follow the data-migration style of `signup/migrations/0008_*` and `accounts/migrations/0010_uid_fill.py`: a header comment naming the story.
- New: `regions/countries.py` (or similar), holding the seed lists as constants plus the helper. The migration imports the lists, or copies them with a test pinning equality.
- `regions/tests/test_guard.py` -- it skips `regions/`, `migrations` and tests, so the new code is fine there. Leave it unchanged.
- Signup's country source: `signup/forms.py:122` `country_choices()` uses `django_countries.countries` (ISO; XK is not included by default). `phonenumbers` regions add XK, AC, TA. Signup path: name `signup`, `/<lang>/signup/` under `i18n_patterns`.
- `mytreatplan/settings.py:314` -- the comment "no models; regions/profiles.py is plain data" needs updating. `regions` is already in INSTALLED_APPS.
- Global-uid rule: `mytreatplan/uids.py`, `mytp_publicsite/tests/test_cross_sites.py`. It applies only to models marked `CROSSES_SITES`; the map is not marked (see Decisions).
- No continent data in deps (django-countries 9.1.0, phonenumbers): hard-code the African ISO codes.

## Tasks & Acceptance

**Execution:**
- [x] `regions/models.py`, `regions/migrations/` -- country map model and schema migration; seed migration from the lists.
- [x] `regions/countries.py` -- seed constants and the lookup helper (state, other region's site and signup URLs).
- [x] `regions/tests/` -- seed matches the decision lists (a test fails if they diverge); the verify examples; the I/O matrix; the helper answers for every django-countries code and every `phonenumbers` region; no region literal escapes `regions/`.
- [x] `mytreatplan/settings.py`, `deploy/staging.env.example`, `deploy/README.md` -- update the regions comment, plus the `REGION_SITE_URL_<CODE>` overrides; `deploy/staging.env.example` and `deploy/README.md` (Add dev-ae) document the staging values.

**Acceptance Criteria:**
- Given a migrated database, when the map is read, then AE, SA, TR, EG, MA and ZA are ae; IR and NG are banned; ES and US are eu.
- Given any code that signup or `phonenumbers` can produce, when the helper is asked, then it answers without error.
- Given the seed and the decision lists, when one changes without the other, then a test fails.

## Implementation Notes

- Model `regions.models.Country` (`code` primary key, `state` one of the profile codes or `banned`). Seed `0002_seed_country_map` keeps a frozen copy of the lists and writes one row per django-countries code (249: 15 ae, 62 banned, 172 eu); its reverse empties the table. `test_the_migrated_map_matches_the_lists` compares the migrated rows with `regions/countries.py`, and the test file restates the decision lists.
- Helper `regions.countries.lookup_country(code, language=None)` returns a `CountryAnswer` (`state`, `site_url`, `signup_url`, plus `banned` / `own_region` / `other_region`). The signup URL is `<site>/<lang>/signup/`, built from `SIGNUP_PATH` (a test pins it to `reverse('signup')`), in the requested or active language if the target region offers it, otherwise in its first language.
- `settings.REGION_SITE_URLS` (code -> URL) is built from `REGION_SITE_URL_<CODE>` for every profile, defaulting to the profile's `site_url`.
- AC and TA (phonenumbers-only, parts of Saint Helena) and XK have no row, so they read as eu.

## Plan Change Log

## Review Triage Log

- Pass 1 (quick): 3 findings; medium 2, low 1. Patched 3 (implementer).
  - medium, patch: the seed migration read its country codes from the installed django-countries, so after a package upgrade a fresh database would get different rows than older ones. The full code list is now frozen inside the migration.
  - medium, patch: a blank `REGION_SITE_URL_<CODE>=` gave '' and a relative signup URL on the current site. Blank now falls back to the profile's site URL, with a test.
  - low, patch: the README's restart for the new settings left out the task workers; it now restarts both gunicorns and both workers.
- After review, the user banned AC and TA too (Saint Helena's codes known only to phonenumbers). They are seeded as banned rows, giving 251 rows.
- After patches: portal full suite 1578 passed (3 known PostgreSQL-only failures); `makemigrations --check` clean.

## Verification

**Commands:**
- Portal full suite (non-secret test keys; venv `../.venv`; sandbox off) -- expected: all pass, except the 3 known PostgreSQL-only tests.
- `manage.py makemigrations --check --dry-run` -- expected: no changes.

