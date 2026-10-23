---
title: 'Transactional email through our own mail service'
type: 'feature'
ticket: '9'
created: '2026-10-21'
status: 'done'
baseline_revision: '23d70d27e05dda062834c45a46cc0e3ea94cf79b'
route: 'oneshot'
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

**Problem:** The portal can't send email: `MAILERS['default']` is Django's console backend. The password reset (3.10) and the contact form (1.5) need real email. The user chose the company's own mail service over SMTP, from `noreply@mytreatplan.com`, for the low volume (fewer than 10 resets a week).

**Approach:**
- `MAILERS['default']` becomes Django's SMTP backend when `EMAIL_HOST` is set in `settings.env`, and stays the console backend otherwise (development, tests).
- Its options come from settings typed by a person on the server:
  - `EMAIL_HOST`;
  - `EMAIL_PORT`;
  - one of `EMAIL_USE_SSL` / `EMAIL_USE_TLS` (STARTTLS);
  - `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`;
  - `EMAIL_TIMEOUT`.
- `DEFAULT_FROM_EMAIL` and `SERVER_EMAIL` default to `MyTreatPlan <noreply@mytreatplan.com>` (user decision, 2026-10-21: the brand name shows in the inbox).
- Django's own deploy check `mail.E001` fails `check --deploy` while the console backend is in use, so no custom check is needed.
- `settings.env.example`, `deploy/staging.env.example` and `deploy/README.md` (an "Email" section) document the settings, and a test send with `manage.py sendtestemail`.

The password is typed only into the server's `settings.env`: never in chat, the repository or tests. Tests use Django's locmem backend and never reach a mail server.

</frozen-after-approval>

## Implementation Notes

- Django 6.1 raises ImproperlyConfigured when the deprecated `EMAIL_HOST`, `EMAIL_PORT`, … settings sit next to `MAILERS`. So `mytreatplan/settings.py` reads the `EMAIL_*` environment variables into lowercase locals (`_mail_host`, `_mail_use_ssl`) and puts them only in `MAILERS['default']['OPTIONS']`. The settings.env names stay `EMAIL_*`. A test asserts that no `EMAIL_*` setting is defined.
- The port defaults to 465 with `EMAIL_USE_SSL=True`, else 587 (STARTTLS, `EMAIL_USE_TLS` default on unless SSL). The timeout is 10 s.
- `DEFAULT_FROM_EMAIL` and `SERVER_EMAIL` are `MyTreatPlan <noreply@mytreatplan.com>`.
- No custom check: Django's `mail.E001` (deploy) fails while the default mailer is a development backend. Checked: with `EMAIL_HOST` empty, `check --deploy` lists `mail.E001`; with it set, it doesn't.
- Files:
  - `mytreatplan/settings.py`;
  - `deploy/staging.env.example` (an `EMAIL_*` block, values empty but the user `noreply@mytreatplan.com`);
  - `settings.env.example` (commented lines);
  - `deploy/README.md` (new "Email" section: the settings, `check --deploy`, `sendtestemail`);
  - `mytp_publicsite/tests/test_settings_staging.py`: 7 tests:
    - console without a host; SMTP with STARTTLS and with SSL;
    - no deprecated settings;
    - `mail.E001` with the console backend and without it with SMTP;
    - the from address on a sent mail.

    The `load_settings` baseline now sets `EMAIL_HOST=''` and `EMAIL_USE_SSL=False` and unsets the other mail variables: decouple can't cast an empty string.
- After the review: `EMAIL_USE_TLS` is gone. STARTTLS is on whenever `EMAIL_USE_SSL` is off, so the two can't conflict at send time, and plain SMTP is never used. This narrows the Intent's "one of `EMAIL_USE_SSL` / `EMAIL_USE_TLS`" to a single switch with the same two modes.
- Full suite after the patches (SQLite, browser tests run outside the sandbox): 1,217 passed, 3 failed (the known PostgreSQL-only tests).

## Review Triage Log

**Pass 1 (quick lens, 2026-10-21):** high 0, medium 1, low 6, false 0, maybe-false 0. Seven patches.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | `EMAIL_USE_SSL=True` with `EMAIL_USE_TLS=True` passes both flags, and Django's SMTP backend raises `InvalidMailer` only when mail is sent | medium | patch | `use_tls` is now `not use_ssl`; `EMAIL_USE_TLS` is removed from the settings and docs. |
| 2 | `staging.env.example` set `EMAIL_PORT=587`, which overrides the 465 default when SSL is switched on | low | patch | `EMAIL_PORT` is commented out; the comment says the port follows the mode. |
| 3 | The `UNSET_ENV` comment claimed a developer's environment can't change the values, but decouple still reads a local `settings.env` | low | patch | Comment corrected. |
| 4 | `test_mail_is_sent_from_the_noreply_address` read the live settings, so a local `settings.env` could break it, and it repeated another test | low | patch | Removed; the from address is asserted on the loaded settings. |
| 5 | The `mail.E001` test built `MAILERS` by hand, so it didn't test the project's settings | low | patch | It now loads `settings.py` with and without `EMAIL_HOST` and runs the deploy checks on its `MAILERS`. |
| 6 | The examples and README missed `EMAIL_TIMEOUT` and `SERVER_EMAIL` | low | patch | Added (commented, with defaults). |
| 7 | An empty `EMAIL_PORT=` / `EMAIL_TIMEOUT=` / `EMAIL_USE_SSL=` stops every process at startup (decouple can't cast `''`) | low | patch | Warned in both examples and the README. |

## Verification

**Commands:**
- The local suite command (test keys, SQLite). Expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py check --deploy` with `EMAIL_HOST` empty and DEBUG off: `mail.E001`. With `EMAIL_HOST` set: no `mail.E001`.

## Staging

- PR #36 merged as `fb37e3e`. Outgoing SMTP from the OVHcloud staging instance was blocked (465/587 timed out) until OVHcloud unblocked the mail ports on 2026-10-23.
- The user reported (2026-10-23): `nc` to the SMTP host on 465 and 587 succeeded, and `sendtestemail` arrived. The DKIM result in Gmail's "Show original" was not reported.
