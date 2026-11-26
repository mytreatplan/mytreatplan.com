# Onboarding: MyTreatPlan portal development with Claude Code cloud sessions

This guide is for a developer joining with their own Claude Team seat. You work in **Claude Code cloud sessions** from the **Claude desktop app for macOS**. The code runs in an Anthropic-hosted environment, so nothing is installed on your Mac except SSH for the occasional server task. The guide covers access, the cloud environment, how stories are built, server access, and the rules the project runs on.

## 1. What you are working on

- **`mytreatplan/mytreatplan.com`** (this repo): the regional doctor portal and the public site.
  - Stack: Django 6.1, Python 3.13, Tailwind CSS 4 (built with npm), htmx, Playwright browser tests.
  - One codebase runs as separate regional instances: EU `dev` and UAE `dev-ae` on staging. `REGION` selects the instance.
- **`mytreatplan/mytp-admin`**: the separate staff admin site (AdminLTE). It talks to the regions only through versioned remote reads and writes.
- **`mytreatplan/mytp-core`**: the shared library (crypto, codes, stage machine, pricing). Each site uses it as a wheel committed under `vendor/` (currently `mytp_core-0.35.0`), so a portal session never needs the mytp-core repo itself.

Every merge to `master` deploys to staging through GitHub Actions: the portal to dev and dev-ae, mytp-admin to admindev.

## 2. Access (one-time)

### The owner sets up

1. **GitHub.** Add your GitHub account to the `mytreatplan` organisation, with write access to the repos you will work on.
2. **The Claude GitHub App.** Install it on the organisation, with access to those repos: https://github.com/apps/claude/installations/new. An organisation owner may need to approve the installation.
3. **Team admin settings** on claude.ai:
   - **Organization settings > Connectors:** turn the **GitHub** connector on. Without it, cloud sessions report "GitHub access is required for Claude Code cloud sessions".
   - **Admin settings > Claude Code:** make sure cloud (remote) sessions are allowed. If they are off, sessions report "Cloud sessions are disabled by your organization's policy".
   - Your seat must include Claude Code.
   - If the organisation uses custom roles, your role needs the cloud sessions permission (**Organization settings > Roles**).
4. **Server access:** only if you will need it; see section 7.

### You do

1. Install the Claude desktop app for macOS and sign in with your Team account.
2. Connect GitHub once. Open claude.ai/code in a browser, choose **Sign in with GitHub**, and approve it on GitHub's authorisation page. The desktop app then uses the same connection.

You do **not** get server settings files, payment-gateway keys or production data. The owner keeps those. If a task truly needs one, ask the owner; do not look for a workaround.

## 3. The cloud environment in the desktop app

Create the environment once:

1. Open the desktop app and go to the **Code** tab.
2. Before typing the first message, open the **environment selector** in the row under the prompt box. Its entries are grouped as Local, SSH and Cloud.
3. Choose **New environment**, then the **Cloud** tab, then **Add environment**.
4. Fill in the fields:
   - **Name:** `MyTreatPlan portal`.
   - **Network access:** **Limited**, with **Allow package managers** ticked. This already allows PyPI, npm, the Ubuntu package mirrors and GitHub. Under **Allowed domains**, add these, one per line:
     ```
     playwright.azureedge.net
     playwright.download.prss.microsoft.com
     cdn.playwright.dev
     objects.githubusercontent.com
     release-assets.githubusercontent.com
     ```
     The first three are Playwright's Chromium download. The last two serve the Python 3.13 build, because the cloud image (Ubuntu 24.04) ships Python 3.12. If an install still fails with a blocked domain, add the domain the error names, or switch to **Full**.
   - **Setup script:**
     ```bash
     bash scripts/web-setup.sh
     ```
   - **Environment variables:** none. The script writes the test settings itself. Never put a real key here: anyone who uses the environment can read these values.
5. Save. To edit the environment later, use the gear icon next to it in the selector.

To start a session:

1. In the **Code** tab, pick **MyTreatPlan portal** under **Cloud**.
2. In the **repositories** control, pick `mytreatplan/mytreatplan.com`.
3. Choose a permission mode. Cloud sessions offer *Accept edits*, *Plan* and *Auto*.
4. Type your request.

**What the setup script does.** Everything a session needs is in the repo, including the BMad skills (`.agents/skills/`, linked from `.claude/skills/`). `scripts/web-setup.sh` mirrors CI and is safe to run again. It installs, in order:

- gettext, Pango and the Python venv module through apt;
- Python 3.13, fetched through `uv` when the image has only 3.12;
- a `.venv` with `requirements-dev.txt` and `uv`, which the BMad skills use;
- Chromium for the browser tests;
- `npm ci` and the Tailwind build;
- the compiled Spanish translations.

It also writes two git-ignored files:
- a `settings.env` holding CI's **non-secret test values**;
- `_bmad/custom/config.user.toml`, which sets the active initiative.

It ends with `manage.py check`.

The environment caches the setup only when the script finishes within about 5 minutes. Until then, each new session runs it again, which takes a few minutes. If a session starts before the setup ran (for example, the setup reports it is not inside the repository), ask Claude to run `bash scripts/web-setup.sh` once the repo is open.

**Running the tests.** Full suite: `. .venv/bin/activate && python -m pytest -q -p no:cacheprovider --tb=line`.
- Expected result: **0 failures**, roughly 4,350 tests in about 6 minutes.
- Locally the suite runs on SQLite (CI uses PostgreSQL 17), so 3 PostgreSQL-only tests skip.
- Browser tests carry the `browser` marker and skip when Chromium is missing.
- Rebuild the CSS (`npm run build:css`) after adding Tailwind classes. The built `static/css/site.css` is never committed.

Known flaky tests, worth one re-run before you investigate:
- the `p2` substring test in `accounts/tests/test_makers.py`.

A browser test that turns a lazy translation into a string must wrap it in `translation.override('en')`.

## 4. Where the plans and decisions live

Everything is in git under `_bmad-output/initiative-mytreatplan-portal-admin/`:

- `initiative-mytreatplan-portal-admin.md` and `tickets.toml`: the initiative and its 16 epics in build order.
- `epic-<name>/epic-<name>.md`: each epic's requirements, Done when, and a **Notes** section full of dated user decisions. Read it before you touch that area. Don't re-ask anything recorded there.
- `epic-<name>/tickets.toml`: the stories (entries). A story is referred to as `<epic>.<entry>`, e.g. `9.16` is entry 16 of epic 9 (`epic-ordering`).
- `epic-<name>/story-<slug>-plan.md`: one plan per story. Its `<frozen-after-approval>` block is the human-approved intent; only the owner changes it.
- `architecture-portal-admin/architecture-portal-admin.md`: architecture decisions AD-1 to AD-24 (regions, data placement, privacy, remote API).
- `deferred-work.md`: findings and ideas deliberately left for later.
- `deploy/README.md`: the staging runbooks (sites, services, jobs, re-provisioning).

## 5. How a story is built

The project uses the BMad skills, committed in the repo. The owner makes skill updates (the `skills` CLI rewrites `.agents/skills/` and `skills-lock.json`) and commits them like any other change, so pull master to get them.

1. **Pick a story with the owner.** The owner keeps the build order. Agree who takes which story, so two people never work on the same files. One person, one story, one branch.
2. **`/bmad-build <ref>`** (e.g. `/bmad-build 9.17`). Claude investigates and writes the plan, then asks **numbered questions with options**. Answer like `1. a; 2. b`. Answers go into the plan's frozen block as dated decisions. Then a checkpoint: *Approve and continue*, *Approve and stop*, or *Review plan*.
3. **Build.** The work happens on a branch named `story/<ref>-<slug>` (e.g. `story/9-17-cart-on-transfer-approval`).
   - An implementer subagent writes the code from the plan, and a review subagent checks it.
   - The review findings are triaged in the plan's Review Triage Log and fixed.
   - The full suite must pass.
4. **PR.** The session pushes the branch through GitHub under your account.
   - Open the PR with the **Create PR** button on the session's **Changes** card. `gh pr` commands are blocked in cloud sessions; `gh api` REST calls work.
   - Paste the PR description Claude wrote: what changed, review result, test count, and **numbered staging checks**, ending with the Claude Code attribution line.
   - Watch CI until every check is green. A piped watch can exit 0 on failure, so read each check's state.
   - Tag pushes and branch deletion are blocked in cloud sessions. mytp-core releases (tags) stay with the owner.
5. **Merge and staging belong to the owner** unless the owner says otherwise for that story. The owner (or the owner's session) runs the staging checks on dev.
6. **Completion commit** on master: plan status `done` plus a `## Staging` section (merge hash, CI run, checks passed or owed).

Use Sonnet subagents by default and Opus where it clearly does better, such as security reviews and tricky design.

## 6. Rules that apply to every change

- **No secrets anywhere.** Never paste a key, password or token into a chat, a commit, a plan, a PR or the cloud environment's variables.
  - Never print a settings file.
  - Keep infrastructure details (server addresses, IPs, database hosts and names, personal account names) out of commits and plans.
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

## 7. Server access over SSH (only when a task needs the staging server)

Server work happens from **your Mac's Terminal**, never from a cloud session. The cloud environment must not hold your server key, and its network allowlist does not reach the server. Use it for checks the owner asks for and runbook steps from `deploy/README.md`, agreed with the owner first.

### 7.1 You: create a key on your Mac (once)

```bash
ssh-keygen -t ed25519 -C "<your-name> mytp staging" -f ~/.ssh/mytp_staging
```

1. When `ssh-keygen` asks, give the key a **passphrase**.
2. Store the passphrase in the macOS keychain, so you type it only once:
   ```bash
   ssh-add --apple-use-keychain ~/.ssh/mytp_staging
   ```
3. Send the owner **only the public key**, the contents of `~/.ssh/mytp_staging.pub`. It is safe to share. Never share the private key (`~/.ssh/mytp_staging`, without `.pub`), never put it in the repo, and never paste it into a cloud session or the environment's variables.

### 7.2 Owner: give you your own account on the server

You get a personal account, so the server's logs and `sudo` records show who did what. The owner runs these over their own SSH session, replacing `<user>` with your account name:

```bash
sudo adduser --disabled-password --comment "" <user>
sudo install -d -m 700 -o <user> -g <user> /home/<user>/.ssh
sudo install -m 600 -o <user> -g <user> /dev/null /home/<user>/.ssh/authorized_keys
sudo tee -a /home/<user>/.ssh/authorized_keys   # paste the colleague's .pub line, then Ctrl-D
```

Then the owner decides your rights with a sudoers file, checked with `visudo` before it takes effect:

```bash
echo '<user> ALL=(mytp-dev,mytp-dev-ae,mytp-admindev) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/<user>
sudo chmod 440 /etc/sudoers.d/<user> && sudo visudo -cf /etc/sudoers.d/<user>
```

That example lets you run commands **as the site users only**, which covers Django shells and management commands. Service restarts, re-provisioning and `settings.env` stay with the owner. The owner widens it only if your tasks need it.

The owner also gives you the server's address, privately, never in git. If the provider's firewall limits SSH by source IP, the owner adds yours. To revoke access later: `sudo deluser --remove-home <user>` and `sudo rm /etc/sudoers.d/<user>`.

### 7.3 You: an SSH shortcut (once)

Add this to `~/.ssh/config` on your Mac (create the file with `chmod 600` if it does not exist):

```
Host mytp-staging
    HostName <server address from the owner>
    User <your account name>
    IdentityFile ~/.ssh/mytp_staging
    IdentitiesOnly yes
    AddKeysToAgent yes
    UseKeychain yes
```

Connect with `ssh mytp-staging`. The first time, check the host key fingerprint with the owner before answering `yes`.

### 7.4 Working on the server

- The sites live under `/srv/mytp-<site>/current`, run by the user `mytp-<site>` (sites: `dev`, `dev-ae`, `admindev`). A Django shell on dev:
  ```bash
  sudo -u mytp-dev -H bash -c 'cd /srv/mytp-dev/current && venv/bin/python manage.py shell'
  ```
- A longer script goes in through standard input: `... manage.py shell < check.py`. On the admin site, scripts that change data run inside `audit.command_actor('<your-name>-<task>')` so the audit log names you.
- **Read-only by default.** Anything that changes data or settings is agreed with the owner first, and whatever is left behind is reported to the owner, as the plans' `## Staging` sections do.
- **Never print `settings.env`** or any secret, not even to check a value. Check that a setting is present with a count, never by showing it.
- Health check, without SSH: `curl -s https://<site host>/healthz`.

## 8. What to ask the owner first

- Which story is yours, and which files the owner's own current story touches.
- Whether you may merge your own PRs once CI is green, or whether the owner merges.
- Whether you need server access (section 7), and for which tasks.
- Any by-eye checks the owner wants you to do on dev (you can use the staging site in a browser if the owner shares its login).
