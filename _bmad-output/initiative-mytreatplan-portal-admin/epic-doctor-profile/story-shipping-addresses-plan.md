---
title: 'Shipping addresses'
type: 'feature'
ticket: '7'
created: '2026-11-17'
status: 'built'
baseline_revision: '0df4a25'
route: 'full'
route_source: 'pinned'
risk: 'low'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A doctor cannot record where physical products are shipped (D6). The "Shipping addresses" settings tile still says "coming soon", and VirtuaOrtho (epic-ordering) cannot be ordered without a shipping address.

**Approach:** Add a regional `ShippingAddress` model built on 6.5's `AddressFields`, with several per doctor and one default, all field-encrypted (AD-2). Add list, add, edit, set-default and delete pages copied from the fiscal pages. Give epic-ordering a read API (`usable_addresses`, `default_address`, `has_usable_address`) and `mark_used`, so ordering can offer the default first.

**Decisions (user, 2026-11-17):**
- **Country:** the same rule as the billing country. It must be in this site's region and not banned, and `lookup_country` checks it at every save. An address whose country later becomes banned, or moves to the other region, stays listed, marked "cannot be used for new orders". Saving it again is refused unless the country is changed. The refusal messages are the fiscal ones.
- **Fields:** the `AddressFields` plus:
  - an optional label (for example "Clinic Madrid");
  - a required recipient name;
  - an optional clinic name;
  - a required contact phone, parsed with `phonenumbers` (country default = the address country) and stored in E.164;
  - optional delivery notes, at most 300 characters.

  Every field except the country is encrypted.
- **Required:** line 1, city, postal code, country, recipient name and phone. The postal code is optional for country AE.
- **Limit:** 10 active addresses per doctor. Archived ones do not count.
- **Default:** the first address becomes the default, and the database allows only one per doctor. When the default is deleted or archived, the most recently changed usable address becomes the default.
- **Delete:** a hard delete while unused. Once epic-ordering calls `mark_used`, Delete archives instead: the address is hidden and not offered. Delete asks for confirmation. Orders keep their own snapshot and reference the address with SET_NULL (handoff note in epic-ordering).
- **Edit:** in place, with `version` +1 on each change. A stale version is refused.
- **"Use my fiscal address" button:** not now (deferred-work entry).
- **Erasure:** deletes all the doctor's addresses.
- **Build order:** after 6.6, on top of it, with migration `accounts` 0021.
- **Merge and staging are delegated.** The checks run on dev and dev-ae in rolled-back transactions:
  - dev: two addresses, then a default is set;
  - dev: an AE address is refused;
  - dev: a raw SQL read shows ciphertext;
  - dev-ae: an AE address without a postal code is saved.

  The user checks the pages by eye. Sonnet implements and Opus reviews with a security focus.

## Boundaries & Constraints

**Always:**
- UUID primary key (AD-5), FK to the user with CASCADE; `version`, `used_at` and `archived_at` as on `FiscalEntity`.
- Every write goes through `accounts/shipping.py`: atomic, user row locked, version checked (AD-15).
- The country is the plain `CountryField` from `AddressFields`.
- Every new string is translated (formal "usted").
- The URL name `settings_shipping` and the tile are kept.

**Never:**
- No `client.updated` change and no core release (that is entry 9).
- No change to fiscal-entity behaviour. A shared helper may be extracted only if fiscal's tests stay unchanged.
- No address, recipient, phone or notes in logs, page records or the event log.
- No admin-site change and no ordering page.

## I/O & Edge-Case Matrix

| # | Situation | Expected |
|---|---|---|
| 1 | eu doctor with no address adds an ES address with every required field | Saved, encrypted, made the default; the card shows label or recipient, city, country and a "Default" badge |
| 2 | Adds a second address and makes it the default | Exactly one default (DB constraint) |
| 3 | eu site, country AE; any site, country IR | Not saved; the other-region warning with a link, or the banned message |
| 4 | ES address missing the postal code, city, line 1, recipient or phone | Field errors |
| 5 | ae site, AE address without a postal code | Saved |
| 6 | Phone "612 34 56 78" with country ES; phone "123" | Stored as +34612345678; "123" is a field error |
| 7 | 11th active address | Refused with a plain message |
| 8 | Delete an unused address, the default, or a used one | Gone; the default passes on; a used one is archived instead |
| 9 | Stale version (two tabs) | "Changed elsewhere, reload" |
| 10 | An address whose country is now banned or in the other region | Marked "cannot be used for new orders"; left out of `usable_addresses`; saving it is refused |
| 11 | Another doctor's id in the edit, default or delete URLs | 404 |
| 12 | Add page with a safe or an external `next` | Own-site path honoured; external ignored (uses `_safe_next`) |
| 13 | Account erased | All addresses deleted |
| 14 | Any save | No event, and no value logged |
| 15 | Staff opens the pages; anonymous opens them | 404; redirect to login |
| 16 | Delivery notes over 300 characters | Field error |
| 17 | Raw SQL read | Ciphertext for every field except the country |
| 18 | ES interface | All new strings translated |

</frozen-after-approval>

## Code Map

- `accounts/models.py`:
  - `AddressFields` :292-307 (encrypted line 1-2, city, postal code and province; plain `country`).
  - `FiscalEntity` :321 is the pattern. Its constraint :362-367 becomes `accounts_shippingaddress_one_default`.
  - Put `ShippingAddress` after `FiscalEntity`.
- `accounts/fiscal.py`: the service to mirror (`create`, `update(version)`, `make_default`, `remove → DELETED|ARCHIVED`, `mark_used`, `erase`, `problems`, `is_usable`, `usable_*`, `default_*`, `country_allowed`, `_lock`, `FIELDS` whitelist, the exceptions).
- `accounts/forms.py`:
  - `FiscalEntityForm` :456+ (`error_messages`, `entity_data()`, `add_service_problems`) is the form pattern.
  - Phone parsing: `parse_mobile` :138-145.
- `accounts/views.py`:
  - The fiscal views :761-919 are the pattern. Reuse `_safe_next` :783; do not copy it.
  - Drop `'shipping'` from `SETTINGS_COMING_SOON` :440.
- `accounts/urls.py:45-46`: replace the coming-soon route with the list, `add/` and `<uuid:pk>/edit|default|delete/` routes.
- Templates: `settings_fiscal*.html` → `settings_shipping.html`, `_form.html` and `_delete.html`.
- `accounts/erasure.py:110-112`: add `shipping.erase(user)`.
- Tests:
  - Mirror `accounts/tests/test_fiscal.py`.
  - Change the "unbuilt example" to Maker logins in `accounts/tests/test_personal_data.py:32,101`, `accounts/tests/browser/test_personal_data.py:55`, `mytp_publicsite/tests/test_settings_staging.py:228` and `mytp_publicsite/tests/urls_with_portal.py:27-28`.
  - `mytp_publicsite/tests/test_i18n.py` needs every new msgid in `locale/es/LC_MESSAGES/django.po`.
- Requirements: D6; epic-ordering.md:27,36; epic-ordering tickets.toml:40-41. No mockup exists.

## Tasks & Acceptance

1. `accounts/models.py` and migration 0021: `ShippingAddress` (Decisions fields, UUID pk, `is_default`, `version`, `used_at`, `archived_at`, timestamps) with the one-default constraint.
2. `accounts/shipping.py`: the service, mirroring fiscal, with the Decisions rules.
3. `accounts/forms.py`: `ShippingAddressForm`.
4. `accounts/views.py` and `accounts/urls.py`: list, add, edit, default and delete pages; drop the coming-soon entry.
5. Templates and the ES `.po`/`.mo`.
6. `accounts/erasure.py`: call `shipping.erase`.
7. Docs:
   - epic-ordering Notes handoff: the VirtuaOrtho wizard requires `has_usable_address`, links to `settings_shipping_add?next=…`, offers `default_address` first, calls `mark_used`, and orders snapshot the address with SET_NULL.
   - A deferred-work entry for the fiscal-address shortcut.
   - A README section.
8. Tests: matrix rows 1-18 in `accounts/tests/test_shipping.py`; the unbuilt-example updates; full suite with 0 failures; `makemigrations --check` clean.

**Acceptance:** the ticket's verify, plus matrix rows 1-18 covered by tests.

## Design Notes

ES labels:
- Direcciones de envío; Etiqueta; Destinatario; Clínica; Teléfono de contacto; Instrucciones de entrega; Predeterminada.
- "Añadir una dirección de envío".

The layout follows the fiscal pages: cards, then the form, then the delete confirmation.

## Implementation Notes

Done as planned (Sonnet implementer, 2026-11-17):
- `ShippingAddress` model with migration `accounts` 0021 `shipping_address` (depends on 0020), and the `accounts_shippingaddress_one_default` constraint.
- `accounts/shipping.py` mirrors fiscal without VIES. `parse_phone` takes the address country as the default region and stores E.164. `NO_POSTAL_COUNTRY = 'AE'`.
- For epic-ordering: `usable_addresses`, `default_address`, `has_usable_address`, `mark_used`.
- `ShippingAddressForm`, five routes reusing `_safe_next`, three templates, ES `.po`, erasure, README, the epic-ordering handoff note, and the deferred-work entry (fiscal-address shortcut).
- `accounts/tests/test_shipping.py` covers matrix rows 1-18. The "unbuilt example" is now Maker in the personal-data tests.

Choices within the plan:
- A blank country skips phone parsing. The save is refused for the country anyway.
- A phone in another country's format must carry its prefix.
- `mytp_publicsite/tests/urls_with_portal.py` and `test_settings_staging.py` needed no change. They only list route names, and the reviewer confirmed this.
- Delivery-note line breaks are normalised to `\n`, both in the form field and in `clean_data`, before the 300-character check.

The PR also carries commit 0df4a25: Spanish K, L and M NIFs are accepted for a person (user, 2026-11-17, after 6.6). It sits outside this plan's diff, since the baseline is 0df4a25.

## Plan Change Log

## Review Triage Log

Quick lens with a security focus (Opus reviewer, 2026-11-17). 0 high, 0 medium, 2 low. All acceptance criteria were met, and focus points 1-13 were ok: IDOR, open redirect, region and banned country, encryption and no values logged, one default, version, `mark_used` and the limit, erasure, phone, AE postal code, translations, the migration, and the two test files the plan named.

| # | Finding | Severity | Verdict | Resolution |
|---|---|---|---|---|
| 1 | Textarea line breaks arrive as `\r\n`, so notes the browser allowed under its 300-character limit were refused by the server and stored with `\r\n` | low | patch | Normalised to `\n` in the form field and in `clean_data` before the length check. A test posts 299 characters with two `\r\n` breaks |
| 2 | Removing the default with no usable successor made an unusable address the default (a "Default" badge on a "cannot be used" card), against the decision | low | patch | The fallback is dropped, so there is no default when none is usable. `create` takes the default whenever none is usable (tested) |

After the patches, my own run: 3553 passed, 12 skipped, 0 failed (SQLite); `makemigrations --check` reports no changes.

## Verification

**Commands:**
- the full suite (see local-dev notes) -- expected: 0 failures
- `python manage.py makemigrations --check` -- expected: no changes
