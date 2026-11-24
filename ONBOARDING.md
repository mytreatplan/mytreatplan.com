# Onboarding: MyTreatPlan portal development with Claude Code on the web

This guide is for a developer joining with their own Claude Team seat and working in **Claude Code on the web** (claude.ai/code), with no local install. It covers access, the cloud environment, how stories are built, and the rules the project runs on.

## 1. What you are working on

- **`mytreatplan/mytreatplan.com`** (this repo): the regional doctor portal and the public site. Django 6.1, Python 3.13, Tailwind CSS 4 (built with npm), htmx, Playwright browser tests. One codebase runs as separate regional instances (EU `dev`, UAE `dev-ae` on staging); `REGION` selects the instance.
- **`mytreatplan/mytp-admin`**: the separate staff admin site (AdminLTE). It talks to the regions only through versioned remote reads and writes.
- **`mytreatplan/mytp-core`**: the shared library (crypto, codes, stage machine, pricing). Each site uses it as a wheel committed under `vendor/` (currently `mytp_core-0.33.0`), so a portal session never needs the mytp-core repo itself.

Every merge to `master` deploys to staging through GitHub Actions: the portal to dev and dev-ae, mytp-admin to admindev.

## 2. Access you need (the project owner sets these up)

1. Your own GitHub account added to the `mytreatplan` organisation, with write access to the repos you will work on.
2. Your Claude Team seat, signed in at claude.ai/code, with GitHub connected and the repo selected when you start a session.
3. Nothing else. You do **not** get staging SSH, server settings files, payment-gateway keys or production data. The owner keeps those. If a task truly needs one, ask the owner; do not look for a workaround.

## 3. The cloud environment

Everything a session needs is in the repo, including the BMad skills (`.agents/skills/`, linked from `.claude/skills/`). One script installs the rest; it mirrors CI and is safe to run again:

```bash
bash scripts/web-setup.sh
```

It installs, in order:
- gettext and Pango through apt;
- a Python 3.13 `.venv` with `requirements-dev.txt` and `uv`, which the BMad skills use;
- Chromium for the browser tests;
- `npm ci` and the Tailwind build;
- the compiled Spanish translations.

It also writes two git-ignored files:
- a `settings.env` holding CI's **non-secret test values**;
- `_bmad/custom/config.user.toml`, which sets the active initiative.

It ends with `manage.py check`.

In the Claude Code environment settings, set `bash scripts/web-setup.sh` as the setup script, so every new session starts ready. Otherwise, ask Claude to run it at the start of a session. Locally the suite runs on SQLite (CI uses PostgreSQL 17), so 3 PostgreSQL-only tests skip.

Full suite: `. .venv/bin/activate && python -m pytest -q -p no:cacheprovider --tb=line`. Expected result: **0 failures**, roughly 4,350 tests in about 6 minutes. Browser tests carry the `browser` marker and skip when Chromium is missing. Rebuild the CSS (`npm run build:css`) after adding Tailwind classes. The built `static/css/site.css` is never committed.

Known flaky tests, worth one re-run before you investigate:
- the `p2` substring test in `accounts/tests/test_makers.py`;
- `test_remembered_device` (depends on `SECRET_KEY`).

A browser test that turns a lazy translation into a string must wrap it in `translation.override('en')`.

## 4. Where the plans and decisions live

Everything is in git under `_bmad-output/initiative-mytreatplan-portal-admin/`:

- `initiative-mytreatplan-portal-admin.md` and `tickets.toml`: the initiative and its 16 epics in build order.
- `epic-<name>/epic-<name>.md`: each epic's requirements, Done when, and a **Notes** section full of dated user decisions. Read it before you touch that area. Don't re-ask anything recorded there.
- `epic-<name>/tickets.toml`: the stories (entries). A story is referred to as `<epic>.<entry>`, e.g. `9.16` is entry 16 of epic 9 (`epic-ordering`).
- `epic-<name>/story-<slug>-plan.md`: one plan per story. Its `<frozen-after-approval>` block is the human-approved intent; only the owner changes it.
- `architecture-portal-admin/architecture-portal-admin.md`: architecture decisions AD-1 to AD-24 (regions, data placement, privacy, remote API).
- `deferred-work.md`: findings and ideas deliberately left for later.

## 5. How a story is built

The project uses the BMad skills, committed in the repo. Skill updates are made by the owner (with the `skills` CLI, which rewrites `.agents/skills/` and `skills-lock.json`) and committed like any other change, so pull master to get them.

1. **Pick a story with the owner.** The owner keeps the build order. Agree who takes which story, so two people never work on the same files. One person, one story, one branch.
2. **`/bmad-build <ref>`** (e.g. `/bmad-build 9.17`). Claude investigates and writes the plan, then asks **numbered questions with options**. Answer like `1. a; 2. b`. Answers go into the plan's frozen block as dated decisions. Then a checkpoint: *Approve and continue*, *Approve and stop*, or *Review plan*.
3. **Build.** The work happens on a branch named `story/<ref>-<slug>` (e.g. `story/9-17-cart-on-transfer-approval`). An implementer subagent writes the code from the plan; a review subagent checks it; the review findings are triaged in the plan's Review Triage Log and fixed. The full suite must pass.
4. **PR.** Push the branch and open the PR with a body file: what changed, review result, test count, and **numbered staging checks**. End the body with the Claude Code attribution line. Watch CI until every check is green; a piped watch can exit 0 on failure, so read each check's state.
5. **Merge and staging belong to the owner** unless the owner says otherwise for that story. The owner (or the owner's session) runs the staging checks on dev.
6. **Completion commit** on master: plan status `done` plus a `## Staging` section (merge hash, CI run, checks passed or owed).

Use Sonnet subagents by default and Opus where it clearly does better, such as security reviews and tricky design.

## 6. Rules that apply to every change

- **No secrets anywhere.** Never paste a key, password or token into a chat, a commit, a plan or a PR. Never print a settings file. Keep infrastructure details (hosts, IPs, database names) out of commits and plans.
- **Patient privacy.** No patient name in any URL, query string, log line, flash message or cache key. URLs carry the patient code only (`EU-XXXXXXXX`). Names are field-encrypted.
- **Both languages.** Every user-facing string in English and Spanish (`locale/es/LC_MESSAGES/django.po`). The brand is spelled **MyTreatPlan**.
- **UI rules** from the owner:
  - Content uses the full shared width (`max-w-portal`, 1280px) and must work at phone width.
  - A choice with 3 or more options is a `<select>`, unless a priced list is clearly better.
  - Two options can be cards with a short explanation.
  - Long lists need search and paging.
  - Dialogs fit the screen.
  - Every page works without JavaScript.
- **Business rules live in services**, not in views or templates; views call the service and show its refusal message.
- **New commits, never amended or force-pushed** on shared branches.

## 7. What to ask the owner first

- Which story is yours, and which files the owner's own current story touches.
- Whether you may merge your own PRs once CI is green, or whether the owner merges.
- Any by-eye checks the owner wants you to do on dev (you can use the staging site in a browser if the owner shares its login).
