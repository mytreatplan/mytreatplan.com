---
title: 'Fiscal entities'
type: 'feature'
ticket: '5'
created: '2026-11-16'
status: 'ready-for-dev'
baseline_revision: 'c4f30ab'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A doctor cannot record who they bill as (D4). The "Fiscal data" settings tile is still "coming soon". Orders (epic-payments entry 1) will need a fiscal entity to snapshot and mark as used.

**Approach:** Add a regional `FiscalEntity` model, with several per doctor and one default. Names, tax id, address and billing email are field-encrypted (AD-2). Add list, add, edit, set-default and delete pages under settings. A billing country outside the site's region or banned is refused with a warning (AD-14, via `regions.countries.lookup_country`). Leave seams for the stories that follow: a tax-id blind index and a status slot for 6.6 (per-country checks + VIES), `version` and a UUID primary key for 6.9 (copy to the admin client record), and `mark_used` for epic-payments.

**Decisions (user, 2026-11-16):**
- **Kinds and fields:** an entity is a person (first name, last name) or a company (legal name). Both have a tax id, address lines 1–2, city, postal code, province, country and billing email. Billing country AE also has a **VAT id** (the UAE uses two ids). Every other country, on either site, has one tax id, because the fiscal rules of the other ae-region countries are not known yet.
- **Required fields:** the name(s), tax id, address line 1, city, postal code and country. Billing email is optional; when blank, invoices use the login email.
- **Required fields for billing country AE:** only the name(s), tax id and country. The VAT id, the address lines, city, postal code and province are optional.
- **Tax id:** required, free text, normalised (uppercase, no spaces or dashes), with a non-unique blind index for 6.6 and staff search. The VAT id is normalised the same way, with an index of its own. Per-country format checks and VIES are entry 6.
- **Limit:** at most 10 entities per doctor (archived ones do not count).
- **Default:** the first entity becomes the default automatically, and the database allows only one per doctor. When the default is deleted or archived, the most recently changed remaining entity becomes the default.
- **Delete:** a hard delete while the entity is unused. Once epic-payments has marked an entity used, "Delete" archives it instead: hidden from the list and not selectable on orders. Delete asks for confirmation.
- **Billing country:** it must be in this site's region and not banned. It may differ from the profile country.
- **Country rules changing later:** an existing entity whose country later becomes banned, or moves to the other region, stays listed, marked "cannot be used for new orders". Saving it again is refused, and epic-payments re-checks at checkout.
- **Editing an entity already used on an order:** it is edited in place (the order keeps its own snapshot, AD-7), and `version` goes up on each change.
- **Account erasure:** deletes all the doctor's fiscal entities. The invoices' and accounting's own snapshots are the legal record (AD-7, AD-18).
- **Pricing country:** pricing stays on the profile country. Switching it to the default entity's country is a separate decision, recorded in deferred-work.md.
- **No fiscal entity:** ordering is blocked until the doctor has one, with a direct link to the add page that brings the doctor back afterwards. Ordering and checkout belong to epic-ordering and epic-payments (handoff notes there). 6.5 provides the add page with a safe `next` return and a service query `usable_entities(user)` / `has_usable_entity(user)`. 6.5 shows no prompt on home.
- **Refusal wording:**
  - Another region: "This billing country is served by MyTreatPlan {other site}. Billing data for it cannot be saved here.", with a link to the other site.
  - A banned country gets signup's existing message.
  - Spanish uses "usted"; the labels are in Design Notes.
- **Merge and staging are delegated.** They run server-side on dev:
  - add an ES and a PT entity and pick a default;
  - an AE billing address is refused with the warning;
  - an IR address (banned) is refused;
  - a raw SQL read shows the tax id as ciphertext.
  - The user checks the pages by eye. dev-ae gets an AE entity with a tax id only (no address), and a VAT id round-trips.

## Boundaries & Constraints

**Always:**
- UUID primary key (AD-5), FK to the user with CASCADE.
- Every write goes through one service module (`accounts/fiscal.py`): atomic, user row locked, version checked (AD-15).
- Encrypted fields use `mytp_core.crypto`; the country is a plain `CountryField` (needed for queries).
- The region and banned check uses `lookup_country` at every save.
- Every new string is translated (formal "usted").
- The settings tile and the URL name `settings_fiscal` are kept.

**Never:**
- No `client.updated` change and no core release (that is entry 9).
- No VIES or per-country tax-id validation (that is entry 6).
- No change to the pricing country.
- No tax id, name or address in logs, page records or the event log.
- No admin-site change.

## I/O & Edge-Case Matrix

| # | Situation | Expected |
|---|---|---|
| 1 | eu doctor with no entity adds an ES company with all required fields | Saved, encrypted, made the default; the list shows it with a "Default" badge and the tax id masked to its last 4 characters |
| 2 | Adds a PT person, then makes it the default | The PT entity is the default and the ES one is not; exactly one default (DB constraint) |
| 3 | eu site, billing country AE | Not saved; other-region warning with a link to the .ae site; nothing stored |
| 4 | Billing country IR (banned), either site | Not saved; the banned message |
| 5 | eu site, billing ES with the postal code, city or line 1 missing | Field errors; not saved |
| 6 | ae site, billing AE with only the name(s), tax id and country | Saved; the VAT id is shown and optional |
| 7 | ae site, billing SA | One tax id and no VAT id field; address fields required |
| 8 | VAT id posted for a non-AE country (forged) | Ignored or refused; never stored |
| 9 | Tax id "es b-1234 5678" | Stored normalised "ESB12345678"; blind index set; raw column is ciphertext |
| 10 | Person with no last name, or company with no legal name | Field error |
| 11 | 11th active entity | Refused with a plain message; archived ones do not count |
| 12 | Delete an unused non-default entity (confirmed) | Row gone |
| 13 | Delete the default when others exist | Deleted; the most recently changed remaining entity becomes the default |
| 14 | Delete an entity marked used | Archived (hidden, not in `usable_entities`), not deleted; the default moves if needed |
| 15 | Edit an entity | Saved in place; `version` +1; stale version (two tabs) refused with "changed elsewhere, reload" |
| 16 | Existing entity whose country is now banned or in the other region | Listed with "cannot be used for new orders"; excluded from `usable_entities`; saving it is refused unless the country is changed |
| 17 | Another doctor's entity id in edit, default or delete URLs | 404 |
| 18 | Add page with `?next=/en/…` (own site) and with `next=https://evil.example` | Own-site path honoured after save; external ignored (settings list) |
| 19 | Account erased | All the doctor's entities deleted; `purge_erased` unaffected |
| 20 | Any save | No `client.updated`; no tax id, name or address in logs, page records or the event log |
| 21 | Staff (non-doctor) opens the pages | 404, as the other settings pages |
| 22 | ES interface | Every new string translated ("usted") |

## Code Map

- **Encryption:** `mytp_core.crypto` (`EncryptedCharField`, `EncryptedEmailField`, `blind_index` :429; encrypted fields allow only `isnull`). Pattern: `accounts/models.py:211-252` `Profile`; `:282-289` `PendingContactChange.save()` fills the index; `:147` conditional UniqueConstraint. Next migration: `accounts` 0019. Test keys: `conftest.py:13-14,38-43`.
- **Region and country:** `regions/countries.py:95` `lookup_country` → `CountryAnswer(.banned, .own_region, .other_region, site_url)`. The docstring already names the billing country as a caller. Banned message: `signup/forms.py:62` `COUNTRY_BANNED_MESSAGE` (:300-307). Other-region note: `signup/templates/signup/_country_check.html:10-16`, context from `signup/views.py:535`. Seed: `AE_COUNTRIES` :22, banned list :41.
- **Settings:** `accounts/views.py:419-436` `SETTINGS_SECTIONS` / `SETTINGS_COMING_SOON` (drop `fiscal`); `_settings_context` :439; `settings_coming_soon` :758; `accounts/urls.py:36-37` (`settings_fiscal`). Template `accounts/settings_base.html`; form pattern `personal_data.html` + `PersonalDataForm.changes()` (`forms.py:159,302`).
- **Service pattern:** `accounts/profile.py:116-163`.
- **Sync:** `accounts/client_sync.py:46-75`. Core `ClientUpdated` has no fiscal slot (stays untouched).
- **Erasure:** `accounts/erasure.py:77-114`; `purge_erased`.
- **Pricing seam left alone:** `subscriptions/service.py:160-162` `_country`; `catalogue/pricing.py:15`.
- **Tests to update:** `accounts/tests/test_personal_data.py:29-33` (`SECTIONS`, used at :143 and :487); `accounts/tests/browser/test_personal_data.py:54-56` (uses Fiscal as the unbuilt example; switch it to Shipping).
- **Architecture:** AD-2 :87 (fiscal entities: region-owned, copied to the client record, encrypted names/contact/fiscal ids); AD-5 :124; AD-7 :136; AD-14 :184; AD-15 :188; AD-18 :208.
- **Later consumers:** epic-payments entry 1 ("snapshotted and marked used, per 6.5"); 6.6 (VIES status → resolver, epic notes :60-62); 6.7 shipping (shares the address fields: an abstract `AddressFields`); 6.9 client record.
- **No fiscal mockup** (Annex I has home and personal data only). Requirements .docx paragraphs 66, 195, 377.

## Tasks & Acceptance

1. **Address fields** (`accounts/models.py`): an abstract `AddressFields` model with encrypted line 1, line 2, city, postal code and province, plus a plain `CountryField` country. Shipping (entry 7) will reuse it.
2. **`FiscalEntity` model:**
   - UUID primary key (AD-5); FK to the user with CASCADE; `kind` is person or company.
   - Encrypted: first and last name, legal name, tax id, VAT id, billing email.
   - Indexes: `tax_id_index` and `vat_id_index` (non-unique blind indexes, filled in `save()` as `PendingContactChange` does).
   - Other fields: `is_default`, `version`, `used_at` (null), `archived_at` (null), `created_at`, `updated_at`.
   - A conditional UniqueConstraint allows one default per user among non-archived rows.
   - Migration `accounts` 0019. `is_client` users only.
3. **Service** (`accounts/fiscal.py`): `create`, `update` (version check), `make_default`, `remove` (hard delete or archive), `mark_used(entity, at)` (for epic-payments: idempotent, sets `used_at`), `usable_entities(user)` / `has_usable_entity(user)` / `default_entity(user)` (non-archived and allowed by `lookup_country` now), and `erase(user)`.
   - Every write is atomic with the user row locked.
   - The country check goes through `regions.countries.lookup_country`.
   - The required-field rules depend on the billing country (AE: names, tax id, country).
   - The 10-entity limit counts active entities.
   - Default rules follow matrix rows 2, 13 and 14.
   - Nothing is logged except ids.
4. **Forms** (`accounts/forms.py`):
   - One form for both kinds, with a kind switch that works without JavaScript (a small script only hides the other kind's fields).
   - The VAT id field shows only for billing country AE, and the server ignores it otherwise.
   - Normalisation of the tax id and VAT id.
   - Refusals use the wording in Decisions: banned reuses `COUNTRY_BANNED_MESSAGE`; the other region gets the new string with the other site's URL from `CountryAnswer`.
5. **Views and URLs:**
   - `settings_fiscal` (list) replaces the coming-soon route; `fiscal` is dropped from `SETTINGS_COMING_SOON`.
   - Add page `settings_fiscal_add` (honours a safe `next` via `url_has_allowed_host_and_scheme`), `settings_fiscal_edit <uuid>`, `settings_fiscal_default <uuid>` (POST), `settings_fiscal_delete <uuid>` (GET confirmation, POST delete or archive).
   - Doctor-only, login, CSRF, `never_cache`, 404 for others' ids and for staff.
6. **Templates:** a list of cards (name, masked tax id, country, Default badge, "cannot be used for new orders" mark), the form page, the delete confirmation. Settings shell layout, plus the ES `.po` entries.
7. **Erasure:** `erase_user` calls `fiscal.erase(user)`.
8. **Notes and docs:**
   - deferred-work.md entry: should pricing use the default entity's country?
   - Handoff notes in epic-ordering and epic-payments: ordering is blocked until `has_usable_entity`, with a link to `settings_fiscal_add?next=<this page>`; `mark_used` at order; orders reference the entity with SET_NULL plus a snapshot, never PROTECT.
   - README: a section on fiscal entities.
9. **Tests:** cover matrix rows 1–22. Update `accounts/tests/test_personal_data.py` `SECTIONS` and `accounts/tests/browser/test_personal_data.py:54-56` (use Shipping as the unbuilt example). Include a raw-SQL ciphertext assertion. Full suite: 0 failures. `makemigrations --check` clean.

**Acceptance:** the ticket's verify, plus matrix rows 1–22 covered by tests.

## Design Notes

Spanish labels: Persona física / Empresa; Nombre / Apellidos; Razón social; NIF / CIF / VAT; for billing country AE the two ids are labelled "Tax id" and "VAT id" ("Identificación fiscal" / "Número de IVA"); 6.6 may refine the labels per country. Dirección (línea 1, línea 2); Ciudad; Código postal; Provincia; País de facturación; Email de facturación; Predeterminada; "Datos fiscales" already exists in es.po.

Layout follows the settings shell: a list of cards (name, tax id masked to its last 4 characters, country, "Default" badge, Edit / Make default / Delete) and an "Add a fiscal entity" button. The form is one page that shows the person or company fields by kind (it works without JavaScript).

## Verification

## Implementation Notes

## Review Triage Log

</frozen-after-approval>
