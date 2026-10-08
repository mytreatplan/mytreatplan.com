---
title: 'Public site tracer bullet'
type: 'feature'
ticket: '1'
created: '2026-10-08'
status: 'in-progress'
baseline_revision: 'a9b5546a3e6f7f49c5f5a76c8e521aebcb30e7fa'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The repo can't be cloned and run: settings.py, settings.env and .gitignore itself are untracked, settings loads a hardcoded absolute path, and there is no user model, base template, page, test or CI. Every later story and epic needs this foundation.

**Approach:** Make settings portable and committed, start a minimal custom user model before any migration, and render the mytreatplan.ae Home section, header and footer on a Tailwind + HTMX base template, with pytest-django and a GitHub Actions workflow on Postgres.

## Boundaries & Constraints

**Always:**
- Secrets come only from environment variables, with an optional `settings.env` beside `manage.py`. Commit `settings.env.example` listing the keys with dummy values.
- `accounts.User` subclasses `AbstractUser` with no added fields, and `AUTH_USER_MODEL = 'accounts.User'` lands in the first migration. Signup 2.1 extends it.
- The look matches mytreatplan.ae Home at 480/768/1200 px. Content comes from the built site in the uae_landing repo at commit 1a8f27e, and colours, type and radii come from the MyTreatPlan design-system tokens.
- Inter (latin and latin-ext woff2), images and HTMX are served as local static files. No CDN, so no third-party requests (GDPR).
- English strings are wrapped in `{% translate %}` so 1.3 can add Spanish without touching markup.
- Decision (user, 2026-10-08): Tailwind v4 is built with the npm CLI from `package.json`.
- Decision (user, 2026-10-08): the user allows localhost PostgreSQL in `/sandbox`; the agent runs migrations, the dev DB reset and pytest itself.
- Decision (user, 2026-10-08): keep the full plan (~1,800 tokens) rather than split.

**Never:**
- Other sections, burger menu, i18n setup, legal pages, contact form, deploy: these belong to stories 1.2–1.10.
- Committing secrets, `settings.env`, built CSS, `node_modules/`, `__pycache__/` or `static_root/`.
- Reusing the old inline CSS. Styling is Tailwind only.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Home renders | GET `/` | 200; base template; hero "Digital Orthodontics Made Simple", header, footer | No error expected |
| Missing secret | `SECRET_KEY` unset in env and file | Django refuses to start, naming the key | decouple `UndefinedValueError` |
| CI | push to any branch | Workflow installs, checks migrations, runs pytest on Postgres 16 | Job fails on any failure |

</frozen-after-approval>

## Code Map

- `mytreatplan/settings.py` (untracked) -- hardcoded `RepositoryEnv("/Users/.../settings.env")`; `unipath.Path` shadows `pathlib.Path`; a commented old SECRET_KEY on line 33; `MAILERS` console backend; `STATIC_ROOT=static_root`; TEMPLATES `DIRS=[]`. Rewrite the config loading, keep the rest.
- `.gitignore` (untracked, ignores itself and settings.py) -- rewrite and commit.
- `mytp_publicsite/` -- empty app; holds the Home view, template and tests. `__pycache__` .pyc files are tracked: untrack.
- `static_root/` tracked (collectstatic output) -- untrack.
- `mytreatplan/urls.py` -- only `admin/`; add `/`.
- `requirements.txt` -- Django 6.1.1, psycopg 3.3.6, decouple, dj-database-url, Unipath (drop Unipath). Add `requirements-dev.txt` for pytest and pytest-django.
- Local copies (read as data only; never run anything in them): landing clone at `/private/tmp/claude-501/-Users-jadeandres-Library-CloudStorage-GoogleDrive-mytpdso-gmail-com-Mi-unidad-Desarrollo-Portal-MyTreatplan-code-mytreatplan-com/c9d0d324-edbf-45ca-adf9-69034f13f14e/scratchpad/uae_landing/` (commit 1a8f27e); design tokens at `/private/tmp/claude-501/-Users-jadeandres-Library-CloudStorage-GoogleDrive-mytpdso-gmail-com-Mi-unidad-Desarrollo-Portal-MyTreatplan-code-mytreatplan-com/c9d0d324-edbf-45ca-adf9-69034f13f14e/scratchpad/artifact-files/aad29b78-a2e4-4aa0-8da2-32704393debe/project/tokens.json`.
- Landing source `uae_landing/index.html` (from `<body>` to `id="f-what-we-do"`) -- header, Home hero and service cards; images `capas/Cabecera*.webp`, `Imagen_0*.webp`, `Logo_MyTreatPlan.webp`; fonts `_astro/inter-latin*-wght-normal.*.woff2`.
- Design tokens -- design-system artifact `project/tokens.json` (32 colours e.g. green #7fb800, green-deep #4d7300, ink #252525, panel #f6f8f7; type groups Display/Text; radius, shadow, spacing). Copy it to `design/tokens.json` for reference.
- Python 3.13 venv at `../.venv`; Node and npm available locally.

## Tasks & Acceptance

**Execution:**
- [x] `.gitignore` -- rewrite: drop the `.gitignore` and `settings.py` lines; add `__pycache__/`, `*.pyc`, `static_root/`, `node_modules/`, `static/css/site.css`, `_bmad/render/`; commit it -- the repo must be clonable.
- [x] `git rm -r --cached` on tracked `__pycache__` and `static_root` -- remove generated files from the index.
- [x] `mytreatplan/settings.py` -- load config from env with optional `BASE_DIR/settings.env`; remove the commented key and Unipath; add `accounts`, TEMPLATES `DIRS`, `STATICFILES_DIRS`; commit -- portable settings.
- [x] `settings.env.example` -- keys SECRET_KEY, DEBUG, DATABASE_URL, ALLOWED_HOSTS with dummy values.
- [x] `accounts/` (apps, models `User(AbstractUser)`, admin, migrations/0001) -- the custom user model before any other migration.
- [ ] Local dev DB -- if auth/admin migrations are already applied, drop and recreate the dev database, then `migrate` (user approved: "Yes, reset dev if needed").
- [x] `package.json`, `assets/css/site.css` (Tailwind `@theme` from tokens) -- build to `static/css/site.css` with an npm script.
- [x] `templates/base.html` -- head (Inter `@font-face`, site.css, vendored `static/js/htmx.min.js`), header (logo, desktop anchor nav), `{% block content %}`, footer (company/licence lines, socials).
- [x] `mytp_publicsite/views.py`, `urls.py`, `templates/publicsite/home.html` -- Home hero and service cards with responsive `srcset` images.
- [x] `static/` -- copy fonts, the images used, logo, htmx.
- [x] `mytp_publicsite/tests/` + `pytest.ini` -- tests for the I/O matrix: home 200 with hero text and base template; `get_user_model()` is `accounts.User`; migration check.
- [x] `.github/workflows/ci.yml` -- Python 3.13, Postgres 16 service, env vars, `npm ci && npm run build:css`, `makemigrations --check`, `pytest`.

**Acceptance Criteria:**
- Given a fresh clone with `settings.env` from the example, when `npm run build:css && python manage.py migrate && python manage.py runserver`, then `/` shows the Home section matching mytreatplan.ae.
- Given the repo, when listing tracked files, then no secrets, `.pyc`, `static_root` or built CSS are tracked, and `settings.py` and `.gitignore` are.
- Given a push, when GitHub Actions runs, then the CI job passes.

## Implementation Notes

- Settings load `settings.env` via `decouple.RepositoryEnv` only when the file exists (else `RepositoryEmpty`); env vars always win. `AutoConfig` was not used because it only looks for `.env`/`settings.ini`.
- `.gitignore` adds `!mytreatplan/settings.py` because the developer's global `~/.gitignore_global` ignores `settings.py`.
- Layout reproduces the live site's fluid layout (<1440px, which covers 480/768/1200). Phone (<=700px) hides the nav, as the live site does behind its burger; the burger itself is story scope 1.x. Page is capped at 1440px wide.
- `@font-face` lives in `base.html` `<style>` so URLs go through `{% static %}`; all other styling is Tailwind utilities on `@theme` tokens.
- Tests avoid DB access (Home is a `TemplateView`; migration check uses `MigrationAutodetector`), so they run locally even though the dev role `mytp_db` lacks CREATEDB. CI runs `makemigrations --check` and `migrate` against Postgres 16 as well.
- Local npm needs `npm_config_cache` outside `~/.npm` inside the sandbox (root-owned files in `~/.npm`).

## Plan Change Log

- 2026-10-08 (dev): `capas/Imagen_0*.webp` are not used by the live site and show different photos than the service cards; the cards use the live images (`tailored-tps-treatment-planning.webp`, `virtuaortho-remote-orthodontist.webp`, `orthodontics-education-masterclass.webp`, 812x1086 only). Responsive `srcset` applies to the hero (`Cabecera-480/768/1200`); no smaller service-card variants exist in the source.
- 2026-10-08 (dev): Dev DB reset not done: the drop of the existing default Django tables was refused by the agent permission classifier. Needs the user to run it (see Verification).

## Review Triage Log

## Verification

**Commands:**
- `python manage.py check` -- expected: no issues
- `python manage.py makemigrations --check --dry-run` -- expected: no changes
- `pytest` -- expected: all pass
- `git ls-files | grep -E '\.pyc|static_root|settings\.env$'` -- expected: no output

**Results (2026-10-08):**
- `manage.py check` -- no issues.
- `pytest` -- 7 passed.
- `git ls-files | grep ...` -- no output.
- `makemigrations --check --dry-run` and `migrate` -- pass on a throwaway SQLite DB (`DATABASE_URL` override); `runserver` served `/` 200 with local CSS, JS, fonts and images only. On the dev Postgres DB both fail with `InconsistentMigrationHistory` until the dev DB is reset.
- Pending (user): reset dev DB (drop the 10 default Django tables in `mytreatplan`, or drop/recreate the DB as a superuser), then `python manage.py migrate`; optionally `ALTER ROLE mytp_db CREATEDB` so pytest can create a test DB later. CI not yet run (not pushed). Visual comparison at 480/768/1200 not done.

**Manual checks (if no CLI):**
- Open `/` at 480, 768 and 1200 px beside mytreatplan.ae: same content, layout and colours.
