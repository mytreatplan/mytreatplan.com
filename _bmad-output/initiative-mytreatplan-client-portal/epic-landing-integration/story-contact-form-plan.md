---
title: 'Contact form'
type: 'feature'
ticket: '5'
created: '2026-10-23'
status: 'done'
baseline_revision: '303252ab15ed3c56651170ed6c419b2b259e7b9f'
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

**Problem:** mytreatplan.com's Contact section has only a mailto link. The mytreatplan.ae form (handler `enviar.php`, in `../uae_landing`) was never ported, so a Doctor can't send a message from the site (epic E5, Done when 2, inbox part; CRM forwarding is entry 6).

**Approach:** Add the form to the Contact section, with `enviar.php`'s fields and validation and its anti-spam rules. It submits through htmx and is in EN and ES. Each accepted message is stored, listed in the admin, and emailed to `CONTACT_INBOX` (webform@mytreatplan.com, from `settings.env`) through the 3.9 mailer, in the background. Bot cases are dropped silently with the same thank-you.

## Boundaries & Constraints

**Always:**
- **Fields** (`enviar.php`):
  - first name 80, middle name or initial 80 (optional), surname 80, country 80 (text), phone 32, email 254;
  - three optional yes/no questions: "Interested in aligners?", "Wants to know about the UAE club?", "Makes purchasing decisions?".
- **Required:** first name, surname, country, phone and email.
- **Validation:**
  - control characters become spaces, then the value is trimmed;
  - the email must be valid;
  - the phone must match `^[0-9 +().\-]{6,32}$`;
  - the yes/no answers must be empty, "yes" or "no".
- **Errors:** a real person's errors show next to their fields, translated.
- **Anti-spam, dropped silently** (same thank-you, nothing stored or sent):
  - the honeypot `website` is filled in;
  - the JS flag `cf_js` isn't "1";
  - the form is sent less than 3 s, or more than 6 h, after it was rendered;
  - a URL pattern (`https?://`, `www.`, `[url`, `<a `) appears in the name or country fields.
- **Rate limits:** 5 accepted messages per IP per hour and 60 per hour overall, as sliding windows in the database cache.
  - Over a limit: a generic "please try again later" error. The first block per IP and hour is reported (no values).
  - When the cache fails, the request passes.
- **Email:**
  - plain text, from `DEFAULT_FROM_EMAIL`, Reply-To the sender, to `CONTACT_INBOX`;
  - subject "New contact from the website: {first name} {surname}";
  - body: one "Label: value" line per field ("-" when empty), then "Sent from mytreatplan.com on {UTC time}".
  - A failed send is reported to Sentry without values; the message stays in the admin.
- **Storage:** personal fields are encrypted at rest (encryption policy, "Personal fields"). The admin is a read-only list and detail, plus delete.
- **Settings:** `CONTACT_INBOX` comes from `settings.env`. With DEBUG off, an empty value fails `check --deploy`.
- **Tests:** no real SMTP (locmem, background job run inline).

**Never:**
- CRM forwarding (entry 6). No change to signup, login or the mail settings.
- New dependencies. No CAPTCHA: the decided anti-spam is the four checks above.
- Logging or reporting a message's values.

**Decisions (user, 2026-10-23):**
1. Messages are kept until an admin deletes them; no automatic deletion.
2. A short line under Send: "We use your details only to answer you. See our Privacy policy." (linked to the Privacy page; EN and ES). The Privacy and Terms texts stay as they are, for the lawyer to update.
3. "Wants to know about the UAE club?" stays in the model, the form and the mail, but is not shown on the page for now (no input rendered, so it is always empty and the mail line reads "-"). Showing it again is a template change only.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Valid | Required fields, JS flag, >3 s | Thank-you replaces the form; one row stored; one mail to the inbox | — |
| Invalid | Missing, too long, bad email or phone, bad yes/no | Form re-rendered with field errors; nothing stored | — |
| Honeypot / no JS flag / too fast / too old / URL in a name | Each case | Same thank-you; nothing stored or sent | — |
| IP limit | 6th message from one IP within the hour | Generic "try again later"; nothing stored | First block reported |
| Global limit | 61st message within the hour | Same | Same |
| Mail fails | SMTP error | Thank-you; row stored | Sentry report, no values |
| Spanish | `/es/` | Labels, errors, thank-you and the privacy line in Spanish | — |
| UAE club hidden | Home page; a POST with `club` set | No club input on the page; a posted value is still validated and stored | — |
| No inbox | `CONTACT_INBOX` empty, DEBUG off | `check --deploy` error | — |

</frozen-after-approval>

## Code Map

- `mytp_publicsite/templates/publicsite/sections/_contact.html:10` -- the placeholder ("The form arrives in story 1.5"): the form goes after the contact info. Keep the banner and the mailto.
- `../uae_landing/enviar.php` -- the fields (137-164), the cleaning (167-197), the anti-spam (64-130, 205-210) and the mail (216-262).
- `../uae_landing/index.html:74-180` and its `.cf*` CSS -- the old form's look and client JS: a reference for the layout in Tailwind with design tokens.
- `mytp_publicsite/models.py`, `admin.py` -- empty: add the message model and its admin. First migration of the app.
- `mytp_publicsite/views.py:65` `HomeView`, `urls.py` (app_name `publicsite`) -- add the POST view and route inside `i18n_patterns` (`mytreatplan/urls.py:52`).
- `accounts/crypto.py:470-478` -- `EncryptedCharField` and `EncryptedEmailField`.
- `accounts/password_reset.py`:
  - `build_message` (178), `_send` (187) and `in_background` (226), which runs a daemon thread and closes the connections: reuse the pattern, or move a shared helper.
  - The tests monkeypatch `in_background` (`accounts/tests/test_password_reset.py:54`).
- `accounts/cache_mutex.py` and `password_reset.reserve_mail` (140) -- the sliding-window counter under the lock, for the rate limits.
- `eventlog/service.py:34` `client_ip` -- the IP key; hash it (HMAC with SECRET_KEY, as `signup/limits.py`), never store it plain.
- `mytreatplan/settings.py`:
  - add `CONTACT_INBOX` near `SIGNUP_SUPPORT_EMAIL` (88), next to the mail block (405-437);
  - `DEFAULT_FROM_EMAIL` is the noreply address.
- `mytp_publicsite/checks.py:10-15` -- the deploy-check pattern; add E007 for an empty `CONTACT_INBOX`.
- `mytreatplan/sentry.py` -- `scrub_event` already redacts emails, phones and IPs.
- `locale/es/LC_MESSAGES/django.po` -- the Spanish strings.
- `mytp_publicsite/tests/` -- add `test_contact.py`; follow `test_home.py`.
- `deploy/README.md` "Email" -- document `CONTACT_INBOX`.

## Tasks & Acceptance

**Execution:**
- [x] `mytp_publicsite/models.py`, `migrations/0001_*.py`, `admin.py` -- the encrypted message model (fields plus the time sent and the mail status) and the read-only admin.
- [x] `mytp_publicsite/contact.py` (new), `forms.py` (new) -- the form, cleaning, anti-spam checks, rate limits, mail building and background send.
- [x] `mytp_publicsite/views.py`, `urls.py` -- the htmx POST view: errors partial, thank-you partial.
- [x] `_contact.html` (+ a form partial), and a small script under `static/js/` -- the form, the JS flag, and the htmx swap.
- [x] `mytreatplan/settings.py`, `mytp_publicsite/checks.py`, `deploy/README.md` -- `CONTACT_INBOX` and E007.
- [x] `locale/es/LC_MESSAGES/django.po` -- Spanish.
- [x] `mytp_publicsite/tests/test_contact.py` -- every matrix row, plus the admin and the check.

**Acceptance Criteria:**
- Given the home page in EN or ES, when it renders, then the Contact section shows the form with the six fields, the aligners and purchasing-decision questions (not the UAE club one), Send and the privacy line linking to the Privacy page, and the mailto stays.
- Given a stored message, when an admin opens it, then every field reads in plain text and can't be edited.
- Given the full suite (browser tests included), when it runs, then it passes, and `makemigrations --check` shows no changes.

## Implementation Notes

- New: `mytp_publicsite/contact.py` (bot checks, limits, mail job), `forms.py`, migration `0001_initial`, the `contact/` partials, `static/js/contact-form.js`, `tests/test_contact.py`, `tests/browser/test_contact.py`. Changed: models, admin, views, urls (POST `/<lang>/contact/`), `checks.py` (E007), `_contact.html`, `home.html`, `settings.py`, the `.po`, `deploy/README.md`, the env examples.
- The IP is keyed through `signup.limits._ip_hash` (a private import, reused rather than duplicated). The admin has no search (encrypted fields); `__str__` holds no personal value.
- Decision 3: an error on the hidden club question (only a crafted POST) shows in the general error box.
- The home page now sets Django's `csrftoken` cookie (the form keeps CSRF protection); the landing had no cookies before, so the Privacy text may need it (lawyer).
- Without JavaScript Send stays disabled; the mailto above the form remains.
- A form returned with errors keeps its first render time, so a quick correction is not "too fast".
- The Spanish strings are a draft for the team's review; the layout follows the old `.cf*` CSS in design tokens, not visually checked yet.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-23):** high 0, medium 1, low 2, false 0, maybe-false 0. Three patches.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | A busy lock (`CounterBusy`) was caught as a cache failure and let the message pass uncounted, with a Sentry error; with the one shared global lock, the limits switch off under load | medium | patch | `cache_mutex` raises `CounterBusy` after ~1 s; `signup.limits.ip_reserve` treats a lock not got as blocked. Now blocked with the generic error and no report; `test_a_busy_lock_blocks_without_a_report`. |
| 2 | The link check ran only after the form validated, so a link plus any field error got the errors back, not the silent thank-you (matrix row "URL in a name") | low | patch | Now on the posted values (cleaned) before validation; parametrized test with an invalid email, a country over 80 characters, and a link revealed by cleaning. |
| 3 | After a "try again later" answer focus fell to the page body (no `aria-invalid` field) | low | patch | The error box is focusable and takes the focus; browser test `test_the_limit_error_takes_the_focus`. |

After the patches (2026-10-23), on SQLite with Chromium (sandbox lifted): the full suite gives 1,454 passed, 3 failed (the known PostgreSQL-only tests). `makemigrations --check`: no changes. `compilemessages -l es`: OK.

## Design Notes

- **Fill time:** the render time is a signed value (`django.core.signing`) in a hidden field, so the client's clock doesn't matter. A missing or tampered value counts as a bot case.
- **The JS flag:** set by the small script. htmx needs JavaScript anyway, so without it the form can't be sent.
- **Mail and admin:** the mail is sent after the row is committed (`transaction.on_commit`), and its outcome is stored on the row, so the admin shows unsent messages. Entry 6 will add CRM forwarding the same way.

## Verification

**Commands:**
- The local suite command (test keys, SQLite), outside the sandbox for the browser tests -- expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py makemigrations --check`, `compilemessages -l es` -- expected: no changes, OK.


## Staging

PR #45 merged as 56cf729 (2026-10-23). CI on master (run 40026335388, PostgreSQL 17): 1,457 passed; deploy-staging succeeded. The user added `CONTACT_INBOX` to the server's settings.env, `check --deploy` passed, and staging checks 1–5 from the PR were confirmed: the form in EN and ES (no club question, privacy line), the field error, a real message delivered to webform@mytreatplan.com from noreply with Reply-To the sender, the read-only admin entry with mail "Sent", and the layout and Spanish wording.
