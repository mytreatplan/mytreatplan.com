---
title: 'Settings shell and personal data'
type: 'feature'
ticket: '2'
created: '2026-11-15'
status: 'built'
baseline_revision: '83ef699'
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

**Problem:** The doctor's settings link leads to a coming-soon page. A doctor cannot see or change their personal data or consents (D1, D2), and the portal has no profile model or doctor-side write path that tells the admin site about a change (AD-15).

**Approach:** Build the settings area (section navigation per the mockup) and its personal-data page: language, city, time zone, title, profession, names, landline, alternative email and consents (commercial information, a new WhatsApp-contact kind with provisional text). Country, mobile and login email are read-only. Every save goes through one service function that emits `client.updated`. The new fields travel to the admin record: mytp-core 0.26.0, mytp-admin columns, then the portal.

**Decisions (user, 2026-11-15):**
- The new data lives in a new profile table, one per doctor; city, landline and alternative email are encrypted.
- All six new fields (city, time zone, profession, landline, alternative email, language) travel in `client.updated` in one mytp-core release (0.26.0) and are stored on the admin client record. Consents stay on the portal.
- First name, middle name and surname are editable on the page.
- Until 6.3: the mobile is shown read-only, and one email row (the login email) is shown read-only with the note that a change is requested through the support area. The separate "contact email" arrives with 6.3.
- Profession list: Orthodontist, General dentist, Paediatric dentist, Prosthodontist, Periodontist, Implantologist, Oral and maxillofacial surgeon, Other.
- City is free text.
- Time zone: the full standard (IANA) list with the site's zone preselected. It is stored only; pages keep the site's zone.
- Language: stored as the preferred language; saving switches the page to it. Landing in it after login comes later. The field is hidden on a site with one language (.ae).
- The "privacy policy and terms" checkbox is shown ticked and locked, with the date accepted and links to the texts.
- All five section tiles are shown as drawn. The four unbuilt ones (maker data, change password, fiscal data, shipping addresses) open a coming-soon page inside the settings area. "Change password" has no owning story: recorded in deferred-work.md.
- The photo block shows the initials avatar with no upload control (6.4).
- WhatsApp consent is a new legal kind with provisional EN/ES text, not shown at signup. Unticking the WhatsApp or the commercial consent records a withdrawal with its time; erasing a doctor withdraws both.
- Staging: the admin site has no client page yet, so "reaches admindev" is checked by reading the record from the shell.
- Merge (core tag, admin PR, portal PR) and staging are delegated for 6.2.

## Boundaries & Constraints

**Always:** One service function is the only doctor-side writer of the user's personal data, the profile and the consent choices; it runs in one transaction and calls `client_sync.emit` once when a travelling field changed (a consent-only save emits nothing). Country, mobile and login email are refused by the server, not only greyed in the page. Consent choices are append-only with the current published document of the site and the time; the latest choice wins. Erasure also clears the profile and withdraws the WhatsApp consent. The admin site deploys before the portal (AD-12); an event without the new fields is still accepted. Contact data is encrypted at rest on both sites as the existing email and phone are. Every new string has its Spanish translation (formal "usted", as the mockup). The URL name `settings` stays and renders the personal-data page.

**Never:** No change of mobile, login or contact email (6.3). No photo upload (6.4). No fiscal entities, shipping addresses or maker logins (6.5–6.9). No password page. No per-user time-zone or language activation beyond the redirect after saving. No WhatsApp kind on the signup legal step or among the required kinds. No consent data in the event. No client page on the admin site.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Open the page | doctor with no profile row, GET `/<lang>/settings/` | form with the user's title and names, blank city/profession/landline/alternative email, site zone preselected, language = page language; country, mobile, login email read-only with the support note; five tiles, "Personal data" current; member-since date; link back to home | none |
| Save | title, city, time zone, profession changed | saved; profile row created; one `client.updated` with the new fields; success message; page reloads | none |
| Nothing changed | POST of the same values | no event, no consent row | none |
| Language changed | EN page, language set to Spanish | saved; redirect to `/es/settings/`; event carries the language | none |
| One-language site | .ae | no language field; a posted language is ignored | none |
| Forged read-only field | POST carries country, phone or email | ignored; values unchanged | none |
| Landline | valid number / invalid number | stored in the mobile's format / field error, nothing saved | form error |
| Alternative email | valid / malformed / same as the login email | stored / field error / field error | form error |
| Time zone | value not in the list | field error | form error |
| Commercial consent | ticked → unticked (and back) | a new choice row with `accepted` false (true), the current document and the time; no event | no published document of the kind: the checkbox is not shown |
| WhatsApp consent | first tick; later untick | choice rows as above on the WhatsApp document | as above |
| Terms and conditions | any POST | shown ticked, locked, date accepted; never changed here | none |
| Unbuilt section | GET `/settings/fiscal/` etc. | coming-soon text inside the settings area, tiles visible, that tile current | none |
| Signup | new doctor after this release | legal step unchanged (no WhatsApp checkbox) | none |
| Erasure | doctor erased | profile cleared, WhatsApp and commercial withdrawn | none |
| Staff account | staff opens settings | page works; no event (staff are not clients) | none |
| Old event | admin receives an event without the new fields | applied; the new columns are blank | none |
| Admin record | event with city, time zone, profession, landline, alternative email, language | stored on the client record | none |
| Home card | profession and city set | second line "Profession · City"; otherwise the country as today | none |

</frozen-after-approval>

## Code Map

- `accounts/urls.py:14-26` -- `path('settings/', views.coming_soon, {'area': 'settings'}, name='settings')`; replace the line, keep the name. `accounts/views.py:403-424` `COMING_SOON_AREAS`, template `accounts/coming_soon.html`.
- `templates/doctor_base.html` -- blocks `portal_content`, `portal_width` (default `max-w-[480px]`), `doctor_band`; the gear is current when `here == 'settings'` (sub-pages with other URL names need the template changed). Messages show inside the panel.
- `accounts/doctor_area.py` -- `display_name`, `initials`, `first_name`, `country_name` ("the profile card's second line until entry 2" → "Profession · City"). Home tile `accounts/templates/accounts/home.html:50`.
- Tests pinning names: `accounts/tests/test_doctor_area.py:25` (`AREAS` settings row), :76, :212-228 (gear link, aria-label); `mytp_publicsite/tests/test_settings_staging.py:222-224` `PORTAL_URL_NAMES`; `mytp_publicsite/tests/urls_with_portal.py:24`; `accounts/page_records.py:40-41` `PORTAL_PREFIXES` has `'settings/'` (sub-paths covered).
- `accounts/models.py:65-164` `User`: `title` (choices `Title` :17-25), names, `email` (encrypted + `email_index`), `country`, `phone` (encrypted + index), `uid`, `home_region`, `sync_version`; migrations to `0014_sync_version`. No profile model. `accounts/crypto.py` `EncryptedCharField`, `EncryptedEmailField`.
- `signup/forms.py:11,207-209` reuses `Title` and validates the mobile; `regions/profiles.py:17,90-92` languages per site (EU en+es, AE en), :106/:126 time zones.
- `legal/models.py` -- `LegalDocument.Kind` :70-74 (`terms`, `conditions`, `ormco_tps`, `commercial_consent`), `REQUIRED_KINDS`, `of_site()`, `current(kind)` :45-66, `provisional`; `LegalChoice` :157-188 (append-only, latest wins). No privacy kind (Privacy is a template page).
- `signup/steps.py:198-205`, `signup/views.py:422-445` record choices; `accounts/erasure.py:63-73` `_withdraw_commercial_consent` (the withdrawal pattern).
- Traps for a new kind: `signup/forms.py:404` `LegalForm` loops over every kind with a published document and reads `LEGAL_LABELS[kind]` (:366-373); `conftest.py:210` `publish_site_documents` publishes every kind; seed pattern `legal/migrations/0002_draft_documents.py`; 2.6's migrations `legal` 0006–0007 (region, provisional).
- `accounts/client_sync.py` -- `emit(user)` inside `atomic()`; `payload_of` :44-62. Callers: `signup/views.py:438`, `accounts/admin.py:211-220`, `accounts/erasure.py:103`, `integration/tasks.py:69`.
- `../mytp-core/mytp_core/contracts/clients.py` -- `ClientUpdated` writer closed (`extra='forbid'`), reader ignores unknown fields; latest v0.25.0, vendored wheel in both sites (`test_vendored_core.py`).
- `../mytp-admin/clients/models.py` `ClientRecord`; applier `interface/events.py:98-110` (fixed field list); the `client.erased` handler.
- Language comes from the URL prefix only; switcher `templates/includes/_language_switcher.html`. `TIME_ZONE = REGION_PROFILE.time_zone`.
- Mockup: Annex I `image2.png` of `MyTP_Reqs_Def_V1_21Oct2026_v3.docx` (two levels above the repo; Spanish only). Top: profile card, "Fecha de alta", "Volver a Panel de Control →". Five green-tint tiles: "Mis Datos" (bold, current), "Datos Fabricante", "Cambio de Clave", "Datos Fiscales", "Direcciones Envío". Heading "Revise y/o modifique sus datos". Left column: photo. Right: Idioma, Ubicación (País), Ubicación (Población), Franja Horaria; Tratamiento, Profesión; Nombre, Apellidos; Teléfono Móvil, Teléfono Fijo; EMAIL alternativo; USUARIO (EMAIL) de acceso + note "Si necesita modificar su usuario de acceso, solicítelo abriendo un ticket en el área de soporte"; three checkboxes (privacy and terms; "Doy mi permiso para que contacten conmigo vía Whatsapp"; "Sí, deseo recibir información comercial de MyTreatPlan y sus asociados"); "Cancelar" (grey pill), "Guardar" (green pill).
- i18n `locale/es/LC_MESSAGES/django.po` by hand; Tailwind `@source` list `assets/css/site.css:8-17` (add a Python file that holds classes); `pytest.ini` testpaths.

## Tasks & Acceptance

**Execution:**
- [x] `../mytp-core` `mytp_core/contracts/clients.py`, schemas, contract tests, README/changelog, version 0.26.0 -- add optional `city`, `time_zone`, `profession`, `landline`, `alternative_email`, `language` to the `client.updated` writer and reader (blank defaults) -- do not tag (the session does the release).
- [x] `../mytp-admin` `clients/models.py` + migration, `interface/events.py` applier, the erased handler, tests -- six columns (contact data stored as the record's email and phone are), applied from the event, blank when absent, dropped on erasure. Vendoring the 0.26.0 wheel is done by the session after the tag; write the code against the new fields.
- [x] `accounts/models.py` + migration -- `Profile` (one-to-one with the user): `language`, `city` (encrypted), `time_zone`, `profession` (choices, translated), `landline` (encrypted), `alternative_email` (encrypted); no backfill, a missing row reads as blank.
- [x] `accounts/profile.py` (new) -- the service: read the page's data; `update_personal_data(user, …)` by the Always rules; consent read (latest choice per kind, date of the accepted terms and conditions) and write.
- [x] `accounts/client_sync.py` -- `payload_of` carries the six fields.
- [x] `legal/models.py` + migrations -- kind `whatsapp_contact`; a published, provisional document with EN/ES text for the site's region, following 2.6's conventions; `signup/forms.py` `LegalForm` and `conftest.py` leave the kind out of signup.
- [x] `accounts/erasure.py` -- clear the profile; withdraw WhatsApp as the commercial consent is withdrawn.
- [x] `accounts/forms.py`, `accounts/views.py`, `accounts/urls.py`, templates (`accounts/settings_base.html` or include for the header block and tiles, `accounts/personal_data.html`, coming-soon inside settings), `templates/doctor_base.html` (gear current on every settings route), `assets/css/site.css` if needed -- the settings area and page per the mockup, wider column, stacked on a phone; routes `settings`, `settings_maker`, `settings_password`, `settings_fiscal`, `settings_shipping` under `settings/`.
- [x] `accounts/doctor_area.py`, `accounts/templates/accounts/home.html` -- profile card second line "Profession · City" with the country as fallback.
- [x] `locale/es/LC_MESSAGES/django.po` -- Spanish for every new string.
- [x] Tests in `accounts/tests/`, `legal/tests/`, `signup/tests/`, `mytp_publicsite/tests/` (URL-name lists), one browser test -- every matrix row; update the asserts that pin settings as coming-soon.
- [x] `deploy/README.md` (doctor area / client sync sections) and `_bmad-output/.../deferred-work.md` -- the page, the release order, the staging read of the admin record; deferred entries: "Change password" has no owning story; login landing in the saved language; applying the saved time zone.

**Acceptance Criteria:**
- Given a doctor on the personal-data page, when title, city and time zone are saved, then the values are stored and the outbox holds one `client.updated` carrying them.
- Given that event, when the admin site applies it, then the client record holds the six fields.
- Given a forged POST with country, phone or email, when it is saved, then those values are unchanged.
- Given a doctor who unticks the commercial or WhatsApp consent, when saved, then a new refusal row with the time exists and earlier rows are untouched.
- Given a new signup, when the legal step is shown, then it lists the same kinds as before.
- Given the three suites, when they run, then they pass with no new skip, and `makemigrations --check` reports no change in each site.

## Design Notes

Release order: mytp-core tag v0.26.0 → wheel vendored in both sites → admin PR merged and deployed → portal PR. The portal's `test_vendored_core.py` pins the wheel version.

The page language field offers the site's languages (`regions/profiles.py`). The time-zone list is `zoneinfo.available_timezones()` sorted. "Cancel" returns to the home page.

## Verification

Full suites (portal, admin, core); `makemigrations --check` in both sites; `npm run build:css` to see the page locally.

## Implementation Notes

- **mytp-core 0.26.0.** The six fields are optional on both `ClientUpdated` and `ClientUpdatedReader`, default `''`. Forms: `city` ≤150 chars; `time_zone` an IANA-shaped name ≤64 (every `zoneinfo` zone passes, tested); `profession` a choice code (`Code`); `landline` E.164 or empty (`Phone`); `alternative_email` "something@domain" or empty (stricter than `Email` by requiring a local part, which Django's validator requires anyway); `language` `xx` or `xx-yyyy` ≤10. A writer that leaves them out dumps them as `''`, so every `client.updated` from a 0.26.0 portal carries the six keys. `payload_client_updated.json` re-exported. Version bump, README (fields, schema list, changelog paragraph with the release order), `__init__` and pyproject description as 0.25.0 did. Not tagged.
- **Vendored wheel is a local build** of the uncommitted mytp-core working tree (`pip wheel --no-deps`, `SOURCE_DATE_EPOCH` of core HEAD), copied as `vendor/mytp_core-0.26.0-py3-none-any.whl` in both sites with `requirements.txt` repointed; the session rebuilds it from the tag (`scripts/vendor-mytp-core.sh v0.26.0`) before the PRs. Pins updated: portal `integration/tests/test_vendored_core.py`; admin `interface/tests/test_vendored_core.py` and `layout/tests/test_deploy.py` (it pins the wheel file name).
- **Admin.** `ClientRecord` gains `city`, `landline` (EncryptedCharField), `alternative_email` (EncryptedEmailField) and plain `time_zone`, `profession`, `language`; no blind index on the new contact fields (nothing looks a client up by them). Migration `clients/0002_personal_data_fields` (AddField, blank). The applier copies them; the event is the full state, so an old-shaped event after a full one blanks them (tested). Erasure already deletes the row, so nothing else was needed there (a test covers it). README "Client records" documents the fields and the shell read for staging.
- **Portal model.** `accounts.Profile` (one-to-one, CASCADE, BigAutoField pk, not `CROSSES_SITES`: it never leaves the site as its own record), migration `accounts/0015_profile`. Profession codes: `orthodontist`, `general_dentist`, `paediatric_dentist`, `prosthodontist`, `periodontist`, `implantologist`, `oral_maxillofacial_surgeon`, `other`.
- **"Nothing changed" and the defaults.** The page preselects the site's zone and the page language; the view passes the service only the fields whose posted value differs from the shown one (`PersonalDataForm.changes()`, Django's `changed_data`), and the service writes only values that differ from the stored ones. So an untouched default is never stored (it stays blank in the profile and the event) and a POST of the shown values creates no row, no event and no consent row.
- **Service** `accounts.profile.update_personal_data(user, changes, consents)`: raises ValueError for any field outside title/names/the six profile fields and for any consent kind outside WhatsApp/commercial; one transaction with the user row locked; emits only when an account or profile field changed and the account is a client (`client_sync.is_client`; a staff account with `sync_version` > 0 would otherwise get `client.erased`). A consent row is written only when the ticked state differs from the latest choice (no choice reads as not accepted, so an untick without an earlier tick writes nothing).
- **Landline** is a prefix select (the signup's country list) plus a number, parsed with `phonenumbers` for that prefix; any valid number type is accepted (fixed or mobile), stored in E.164. A number without a prefix is a `landline_prefix` error. Refill shows the stored number's prefix and national form.
- **Alternative email** equal to the login email is refused case-insensitively after normalising.
- **Language** redirect: after a save the page always redirects to `settings` reversed in the new language (or the current one), with the success message translated in that language.
- **WhatsApp kind.** `LegalDocument.Kind.WHATSAPP_CONTACT`, `SIGNUP_KINDS` (the four signup kinds) and `PROFILE_CONSENT_KINDS`; `current_for_signup()` feeds the signup step, its `is_done` and the tests' helper; `LegalForm` also filters by `SIGNUP_KINDS`. One migration `legal/0008_whatsapp_contact` (AlterField of the choices, which changes no column, plus a RunPython publishing version 1 for the instance region, provisional, titles "(draft)/(borrador)" and the draft notice as legal 0002). `conftest.publish_site_documents` is unchanged: publishing the kind is harmless now that signup filters it, and the settings tests on the ae fixture need it. The commercial checkbox label on this page is a new msgid ("Yes, I want to receive commercial information from MyTreatPlan and its partners."), not signup's.
- **Erasure.** `_withdraw_commercial_consent` became `_withdraw_optional_consents` (both kinds through `accounts.profile.withdraw_consent`, same rule as before) and `profile.clear_profile` deletes the row. The existing erasure tests were narrowed to the commercial kind, and a new test covers WhatsApp and the profile.
- **Terms box.** Ticked and disabled when the account has an accepted Terms/Conditions choice, with the date of the latest such choice; links go to the public `privacy` and `terms` pages. Without an acceptance (e.g. staff) it is shown unticked and disabled.
- **Page.** `accounts/settings_base.html` (profile card, member-since as `Y·m·d`, "Back to Control Panel", the five tiles as a nav, current tile bold with a green ring and `aria-current`), `personal_data.html`, `settings_coming_soon.html`; column `max-w-[880px]`; two columns from the tablet breakpoint, stacked on a phone (tiles 2/3/5 per row). Tile lines use `pgettext` per tile because the word order differs ("Maker / Data" → "Datos / Fabricante"); the section name "Change password" has the context "settings section" because the msgid already exists with another translation. New icons in `includes/_icon.html`: user, tag, key, dollar, truck, arrow-right. The gear is current on every settings route through a new `in_settings` filter. No `site.css` change was needed (the templates folder is already a Tailwind source and the forms reuse signup's classes).
- **Read-only fields** are unnamed `readonly` inputs (never posted); each consent shows its document in a `<details>` with the provisional notice.
- **Home card.** `doctor_area.profile_line`: "Profession · City" only when both are set, else the country (as the matrix says; one of the two alone falls back to the country).
- **Tests**: `accounts/tests/test_personal_data.py` (matrix rows), `accounts/tests/browser/test_personal_data.py`, additions in `test_erasure.py`, `test_client_sync.py` (payload keys), `signup/tests/test_legal_step.py` (no WhatsApp on the legal step), `legal/tests` (kinds list; the 2.6 migration test ignores the 0008 draft), URL-name lists in `mytp_publicsite/tests`, `accounts/tests/test_doctor_area.py` (settings no longer a coming-soon area).
- **Review fixes (2026-11-15).**
  1. *Stale tab and consents.* The form carries a hidden `consent_token`: `django.core.signing` of {kind: [document id shown, ticked shown]}, salted `accounts.personal_data.consents:<user pk>`. `consents()` now holds only the kinds whose posted tick differs from the shown tick, so a stale tab that edits the city never repeats a choice withdrawn elsewhere (the service still skips a tick equal to the latest stored choice). A missing, tampered, malformed or other account's token is the non-field error `consents_stale` ("This page has expired. Please reload it and try again."), nothing saved, and the consents are shown again as stored with a fresh token. Tests: the two-tab scenario, forged/missing tokens, another account's token.
  2. *Text not seen.* When the current document of a shown kind (or the set of kinds) differs from the ids in the token, the save is refused with `consents_changed` ("The consent texts have been updated. Please review them again and save."), nothing written, consents reset to the current state (as signup's `documents_changed`). Tested, including the save after the reload recording on the new version. Left as it is: an acceptance on an earlier version still shows ticked after a new version; deferred-work.md entry added (the product/legal question for the final texts).
  3. *Language redirect.* After a save the page goes to `settings` in the form's cleaned language or, without the field (one-language site), the page language; a stored language different from the page's switches the page even if untouched. Tests for both (the one-language case stays on `/en/`).
  4. `aria-describedby="ro-terms-date"` only when the date is rendered (tested).
  5. The terms-date test uses `timezone.localtime`.
  6. Tests for legal 0008 in `legal/tests/test_regions.py` (MigrationExecutor, as 2.6's tests do for 0006/0007): a region that already has the kind is left alone; on the ae site the provisional draft is created for ae.
  7. Erasure and the outbox: behaviour unchanged; deploy/README.md says delivered `client.updated` rows keep their encrypted payload (now with landline and alternative email) until the outbox retention purge; the erasure test asserts the pending `client.updated` row (carrying the landline) is gone and only `client.erased` is left.
  8. `accounts/views.py` imports tidied.
  9. Rollback sentence (a portal rolled back before this release blanks the six fields on the admin record; re-sync after rolling forward) in mytp-admin's README "Client records" and the portal's deploy/README.md personal-data paragraph.
- **Local venvs.** The suites ran in dedicated venvs (`/private/tmp/claude-501/portal-6.2-venv`, `/private/tmp/claude-501/admin-6.2-venv`) built from each site's requirements with the local 0.26.0 wheel. The shared `../.venv` was briefly given 0.26.0, then restored to the committed 0.25.0 wheel so the 8.3 worktree's pin test keeps passing.

## Review Triage Log

Two reviewers: the portal diff (privacy and security focus) and the core + admin diffs.

| # | Severity | Finding | Verdict |
|---|----------|---------|---------|
| 1 | medium | A stale tab re-accepted a consent another tab had withdrawn (every shown checkbox was sent, compared with the stored state) | patched: a signed token carries what the page showed; a consent is written only when the tick differs from the shown one |
| 2 | medium | A consent could be recorded on a text version the doctor did not see | patched: the save is refused when a shown document is no longer current. The carry-over of an earlier acceptance to a new version is left and recorded in deferred-work.md (legal decision) |
| 3 | low | Delivered `client.updated` rows keep their encrypted payload after erasure until the outbox purge | documented in the README; behaviour unchanged (already true for email and phone); test asserts the pending rows are gone |
| 4 | low | The language select could show a stored language different from the page and not switch on save | patched |
| 5 | low | `aria-describedby` pointed at a missing element for an account with no accepted terms | patched |
| 6 | low | A test compared a UTC date with a date rendered in the site zone | patched |
| 7 | low | No tests for `legal` 0008, the one-language redirect, the outbox on erasure | patched |
| 8 | nit | Import order in `accounts/views.py` | patched |
| 9 | low | admin: an event from a rolled-back portal blanks the six fields | patched: rollback note in both READMEs |
| 10 | low | core: the optional-email pattern is loose | rejected: deliberate, the portal validates before emitting and a strict pattern could block the queue |
| 11 | low | core: no CHANGELOG file | rejected: the README holds the version history, as before |

Session runs after the fixes, with master (8.3) merged in and the wheel built from the v0.26.0 tag: portal 3241 passed, 12 skipped; admin 1588 passed, 12 skipped; `makemigrations --check` clean in both. Core CI passed on master and on the tag.
