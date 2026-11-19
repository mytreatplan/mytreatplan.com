Mytreatplan.com main site

## The rules of the configuration snapshot (epic-catalogue entry 11)

The admin site owns the catalogue, the country map and the admin directory, and sends them
in the signed configuration snapshot (`integration.config`). On this portal:

- **Region-specific.** An object that carries a region goes only to that region's portal
  (today: vouchers). The admin site filters what it serves; a voucher of another region
  that still arrives is skipped with a warning (`catalogue.applier.apply_vouchers`).
- **Never deleted.** The admin site deactivates a published object, never deletes it. A
  deactivated one is still sent and stored inactive: it is no longer offered, and it is
  read by its id or code for the orders that refer to it (`catalogue.resolver`: `product`,
  `subscription_by_id`, `add_on`, `country_group_by_id`, `billing_company`,
  `billing_rule_by_id`, `provider_product_by_id`, `voucher_by_code`; `price_catalogue
  --country-group <id>`, `--billing-rule <id>`). A row the snapshot no longer lists is
  replaced away at the apply.
- **Read-only here.** The snapshot-fed tables (the nine catalogue tables,
  `regions.Country`, `integration.AdminDirectoryEntry`) are written only while
  `integration.config` applies a snapshot (`integration.snapshot_tables`). Any other ORM
  write (`save`, `delete`, `objects.create`, a queryset's `update` / `delete`,
  `bulk_create`, `bulk_update`, the base manager, a related manager's `add` / `set`)
  raises `ReadOnlyTable`, also from `manage.py shell`, except what the module docstring
  of `integration.snapshot_tables` lists as not guarded: raw SQL, the private queryset
  methods, the migrations' historical models and raw deserialisation (`loaddata`
  fixtures, `serialized_rollback`). Tests write rows through a pull, or with
  `integration/tests/snapshot_rows.py`.
- **A peer without a region is refused.** The admin site accepts signed calls only from
  the sites its regions name (any other gets 401 `unknown_key`); a region that loses its
  site between the key check and the snapshot answer gets 403. Either way that portal
  keeps its last snapshot.

## Pricing an order (epic-catalogue entry 12)

One function prices an order (AD-7, AD-16): `catalogue.pricing`, exposed by
`catalogue.resolver`, which is the only module a caller imports. Ordering, subscriptions
and payments never compute a price, a VAT amount or a voucher share of their own. It reads
the snapshot tables only: it writes and reserves nothing.

- `price_order(billing_country, subscription, lines, *, vies, voucher=None, doctor=None,
  day=None)`: a service order. `lines` are `OrderLine(product, quantity=1, add_ons=(),
  ice=None)`; the ICE credit (`Money`) goes on the one line that takes it.
- `price_subscription(billing_country, subscription, period, *, vies, voucher=None,
  doctor=None, day=None)`: buying a subscription, `monthly` or `upfront`, billed by the
  `subscription` category rule of the billing country's group.
- `billing_country`: the fiscal entity's country once it exists, the profile's country
  until then; it decides the country group, the rules and the reverse charge. `vies`: one
  of `valid`, `invalid`, `pending`, `not_applicable`. `voucher`: a `Voucher` row (`try_me`
  for an order, `subscription` for a subscription purchase). `doctor`: the doctor's
  client UUID. `day`: Madrid time, today by default.
- Each line: net price (quantity, add-ons) → subscription discount → ICE credit (planning
  lines only, in the order's currency; what exceeds the line is lost) → voucher (spread in
  proportion to each line's amount after discount and ICE, rounded down to the cent, the
  remaining cents to the lines in order; what exceeds the order is lost) → floor at 0 →
  VAT. VAT is computed per order, on the sum of the lines at each rate, rounded half up.
  The reverse charge (rate 0 with the rule's reverse-charge text) applies when VIES is
  `valid`, the billing country is another than the company's and the rule has that text.
- The result (`PricedOrder`): the company, the currency, each `PricedLine` (list amount,
  discount percent and amount, ICE, voucher share, net, VAT percent and text), the
  `VatGroup`s, the totals (net + VAT = total), the gateways every line's rule allows and
  the region profile has (in the first line's rule order; none for a 0.00 order),
  `needs_payment` and `no_payment_method` (above 0 with no common gateway: priced and
  flagged).
- `OrderRefused.reason` (the object it names in `subject`): `no_lines`,
  `country_not_served`, `no_billing_rules` (no rule typed at all: no company bills),
  `product_not_offered` (unlinked, no available rule, another currency, outside its
  validity, inactive), `quantity_refused`, `add_on_refused`, `two_companies`,
  `two_currencies`, `ice_twice`, `ice_not_planning`, `ice_currency`, `voucher_not_usable`
  (its state in `detail`), `voucher_type`, `voucher_currency`, `voucher_doctor`,
  `subscription_not_offered` (inactive, no available `subscription` rule, or priced in
  another currency than the rule's), `period_not_offered`.

The staging check runs through `price_catalogue` (reads only; a refusal is an error naming
its reason):

    python manage.py price_catalogue --order --country ES --tier advanced \
        --product <id> --ice 80.00 --product <id> --quantity 2 --add-on CODE \
        [--vies valid] [--voucher CODE] [--doctor UUID] [--on YYYY-MM-DD]
    python manage.py price_catalogue --buy upfront --country ES --tier advanced \
        [--voucher CODE] [--vies valid] [--doctor UUID] [--on YYYY-MM-DD]

Each `--product` starts a line; the `--quantity`, `--add-on` and `--ice` after it belong
to that line. `--subscription <id or code>` replaces `--tier` for a manual subscription.

## A doctor's subscription (epic-subscriptions entry 1)

Each doctor (neither staff nor a superuser) has one subscription record
(`subscriptions.DoctorSubscription`) and one change row per change of it
(`SubscriptionChange`: when, who, why, the values before and after). One module reads and
writes them (AD-15): `subscriptions.service`. A record of a staff account (a doctor
made staff or a superuser) is ignored: the service reads it as no record and refuses to
change it (`RecordGone`); the row is kept. Nothing else writes the two tables; the
record names the catalogue subscription by its UUID only (no database link), so a
snapshot apply never deletes, blocks on or changes a record.

- `ensure_basic(user, actor)`: Basic for a doctor without a record, inside the caller's
  transaction. Signup calls it (actor: the doctor) and so does the Django admin's add
  (actor: the system). It reads no catalogue table: signup never fails because of the
  catalogue. A Basic record that is not manual holds the level only: the doctor's
  subscription is whichever active standard Basic their country group has today.
- `effective(user, day=None)`: the one rule. Active and past due count as the record's
  tier; cancelled as the record's tier up to and including its end date, Basic after
  it; expired as Basic; a doctor without a record as Basic. Basic is the active standard
  Basic of the profile country's group (none for another region's country, a blank one,
  a group without a Basic or no snapshot yet: the doctor is then offered nothing).
  Another tier is the record's own catalogue row (`held`), whatever its state: a row
  deactivated on the admin site is no longer sold, and its holder keeps it until its end
  date. A row the snapshot does not have is logged as an error and priced as the
  group's Basic; so is a row whose tier or kind (standard or manual) was changed on the
  admin site under the same id. A manual record cannot be switched by the doctor
  (`can_self_switch`) while it counts as its own tier; an expired one, or a cancelled
  one past its end date, is Basic and the doctor may switch (the record then becomes
  standard). A cancelled record always has an end date.
- `offers_for(user, day=None)`, `price_order_for(user, lines, *, vies, voucher=None,
  day=None)`: the doctor's offers and an order's price, through
  `catalogue.resolver.offers_under` and `price_order` (new optional `held=True`: a
  deactivated subscription the doctor holds offers as an active one). No arithmetic in
  the service (AD-16).
- `change(user, *, expected_version, actor, reason, **fields)`, `assign_manual(user,
  subscription, *, expected_version, actor, ends_on=None)`: lock the record, check its
  version (`StaleVersion` with the current one, the remote write's), refuse a doctor's
  change of a manual record (`ManualLocked`), check the values (`ChangeRefused`), apply,
  version + 1, store the actor (doctor, admin or system, AD-22), add a change row.
  `assign_manual` takes an admin or system actor and an active manual catalogue row.

Existing doctors get Basic from migration `subscriptions.0002` (started the day they
joined). After a deploy, `assign_basic` gives one to any doctor the previous release
created meanwhile; `doctor_subscription` prints a doctor's record (both in
deploy/README.md, "Doctor subscriptions"):

    python manage.py assign_basic [--dry-run]
    python manage.py doctor_subscription <uid or email> [--offers]
    python manage.py doctor_subscription <uid or email> --order --product <UUID or code> \
        [--quantity N] [--vies valid|invalid|pending|not_applicable]

## Legal texts and the region's company (epic-regions 2.6)

The footer, the home page's JSON-LD and the public Privacy and Terms pages name the
company of the instance's region profile (`settings.REGION_PROFILE.company`, exposed to
templates as `region_company` by `regions.context_processors`). Legal documents
(`legal.LegalDocument`) carry a `region`: `current`, `current_by_kind`, the version
numbering (per region and kind) and the admin read only the instance's region, so a site
never offers another region's document; acceptances carry no region (their document
does). A document is `provisional` until the lawyer's final text replaces it: a final
text is a new version with `provisional` off in the admin; for the public pages, the
templates are replaced and `LEGAL_TEXTS_FINAL` is set to `True` (deploy/README.md,
"Legal texts: from provisional to final").

## The doctor area (epic-doctor-profile entry 1)

A signed-in doctor's pages extend `templates/doctor_base.html` (itself on
`portal_base.html`, so not indexed): the gradient header with the isotype
(`static/img/isotype.svg`, a `currentColor` mask), the links home, patients and support, the
messages, settings and cart icons, the avatar with the doctor's initials, the logout button
(a POST form) and the language switcher, collapsing behind `static/js/menu.js`'s menu button
below the `tablet` breakpoint; then the white rounded panel holding the page
(`portal_content`, widened in `portal_width`) and the handshake band (`doctor_band`). The
names shown come from `accounts.doctor_area` (decrypted in Python; the `doctor_area`
template filters `initials` and `display_name`). Login lands on the home (`/<lang>/home/`,
`accounts.views.home`); signup still ends on the upgrade screen or the welcome page, which
links to the home. The welcome page, the upgrade screen and the purchase placeholder use
the doctor layout; login and signup keep the light one.

Every area not built yet has its own address, served by `accounts.views.coming_soon`
("coming soon", naming the area): `patients`, `support`, `messages`, `settings`, `cart`,
`tutorials`, `virtuaortho`, `planning_services` and `start_a_case` (the URL names, in
`accounts/urls.py`). An epic that builds one replaces that route with its own view under the
same URL name and path, and removes the area from `COMING_SOON_AREAS`; the header and the
home's links follow by themselves. A new doctor-area address also needs its first segment
in `accounts.page_records.PORTAL_PREFIXES` (a test checks it) and its name in
`mytp_publicsite/tests/test_settings_staging.py`'s `PORTAL_URL_NAMES`.

## A doctor's fiscal entities (epic-doctor-profile entry 5)

A doctor records who they bill as in settings, "Fiscal data" (`accounts.FiscalEntity`, UUID
primary key, region-owned; copied to the admin site by `client.updated` since entry 9, see below): a person (first and last
name) or a company (legal name), with a tax id, an address and an optional billing email;
billing country AE also has a VAT id. Names, ids, address and email are field-encrypted; the
country is plain. The tax id and the VAT id are stored uppercase without spaces or dashes,
each with a non-unique blind index (`tax_id_index`, `vat_id_index`). Up to 10 active entities
per doctor, exactly one default among them (a database constraint); `version` goes up on each
change, and a stale edit is refused.

`accounts.fiscal` is the only writer (atomic, the user row locked): `create`, `update`,
`make_default`, `remove` (a hard delete, or an archive once an order used the entity),
`mark_used(entity, at)`, `erase(user)`. The rules depend on the billing country: the name(s),
tax id and country are always required; the address line 1, city and postal code for every
country but AE. The country must be in this site's region and not banned
(`regions.countries.lookup_country`), checked at every save.

What ordering and checkout read: `usable_entities(user)`, `has_usable_entity(user)` and
`default_entity(user)` (non-archived, country allowed now: an entity whose country was banned,
or moved to the other region, stays listed as "cannot be used for new orders" but is not
usable). Ordering is blocked until `has_usable_entity`; send the doctor to
`settings_fiscal_add?next=<this page>` (only a path of this site is honoured). Call
`fiscal.mark_used` when an order uses an entity, and reference it from the order with
`SET_NULL` plus the order's own snapshot, never `PROTECT`.

### Tax id checks and VIES (entry 6)

`accounts.fiscal.problems` also checks the tax id (`accounts.taxid`, python-stdnum): Spain
needs a DNI, an NIE or a K, L or M NIF for a person and a CIF for a company (checksum and kind), Portugal the
NIF checksum, a country prefix of another country is refused (Greece may be typed `EL` or
`GR`), and the AE VAT id is exactly 15 digits. Any other country's tax id is free text.

For a billing country in the EU-27 the entity has a service-owned `vies_status` (the pricing
resolver's `catalogue.pricing.VIES_*` values; never in `FIELDS`, so a posted value is ignored)
with `vies_checked_at` and the retry fields. `create` and `update` ask the EU VIES REST API
(`accounts.vies`, `VIES_URL`, `EXTERNAL_CHECK_TIMEOUT`) after the offline checks and outside
any lock, only when the tax id or country is new or changed: `valid`, `invalid` (not found,
or not a well-formed VAT number for its country: VAT is charged, the save is never refused)
or `pending` when VIES is unavailable. Every other country is `not_applicable`. The
`retry_vies` job (`deploy/jobs.conf`, every 15 minutes) retries each pending entity 15 minutes
after its first failure, 1 hour after the second, then every 6 hours; after 3 days it logs one
error with the entity id only. Hand `fiscal.vies_status(entity)` to
`subscriptions.service.price_order_for(..., vies=...)`. An id saved before the checks that
fails them stays usable; its card says "Please check the tax id" and the next edit must fix
it. Tests use the autouse `fake_vies` fixture (`conftest.py`).

## Fiscal entities and shipping addresses on the admin site (epic-doctor-profile entry 9)

`client.updated` (mytp-core 0.28.0) carries two lists, `fiscal_entities` and
`shipping_addresses`, so the admin site's client record (AD-2) has a copy of each, encrypted
with its own keys. The event stays a full snapshot with the per-client `sync_version`
(`accounts.client_sync.payload_of`): the active entries, plus the 25 most recently archived
of each list with their `archived_at` (so the admin site can show archived ones for auditing).
Fiscal items carry the VIES status and check time; never the retry fields (attempts, next try,
pending since, alerted). Maker logins and the photo are never sent.

Every write that changes a copied value emits inside its own transaction, with the user's row
locked: `fiscal` and `shipping` `create`, a changed `update`, a changed `make_default`,
`remove` (a delete or an archive) and `mark_used` when it sets the date; and the `retry_vies`
job's write when the status or the check time changes (it takes the user's lock first, then
the entity's; a retry that stays pending sends nothing). If the event cannot be recorded the
write rolls back. A text value that breaks mytp-core's central payload rules (a link to object storage, a
data: URI, 200 or more base64-like characters) is refused at save with the field error
`not_allowed`, so the payload is never altered. The erasure sends `client.erased` as before.

After deploying, run `sync_clients --all` on each portal so every existing doctor's lists reach
the admin site (the admin site deploys first and leaves its rows alone for an event without the
lists). Check on the admin site that a test doctor's rows are ciphertext in
`clients_clientfiscalentity` and `clients_clientshippingaddress`.

## A doctor's shipping addresses (epic-doctor-profile entry 7)

A doctor records where physical products are sent in settings, "Shipping addresses"
(`accounts.ShippingAddress`, UUID primary key, region-owned; copied to the admin site by `client.updated` since
entry 9, see "Fiscal entities and shipping addresses on the admin site"). It reuses the `AddressFields` of the fiscal entities and adds an optional label, a
required recipient name, an optional clinic name, a required contact phone (parsed with
`phonenumbers`, the address country being the default region, stored in E.164) and optional
delivery notes (300 characters at most). Every field but the country is field-encrypted. Up to
10 active addresses per doctor, exactly one default among them (a database constraint, the
first address takes it); `version` goes up on each change and a stale edit is refused.

`accounts.shipping` is the only writer (atomic, the user row locked): `create`, `update`,
`make_default`, `remove` (a hard delete, or an archive once an order used the address; a
removed default passes to the most recently changed usable address), `mark_used(address, at)`
and `erase(user)`. Required: line 1, city, postal code (not for AE), country, recipient and
phone. The country must be in this site's region and not banned, checked at every save, with
the fiscal pages' messages. The pages are the fiscal ones (list, add with a safe `next`,
edit, make default, confirmed delete); the URL names are `settings_shipping` and
`settings_shipping_add`, `_edit`, `_default`, `_delete`.

What ordering reads: `usable_addresses(user)`, `has_usable_address(user)` and
`default_address(user)` (non-archived, country allowed now: an address whose country was
banned, or moved to the other region, stays listed as "cannot be used for new orders" but is
not usable), and `mark_used`. Orders keep their own snapshot and reference the address with
`SET_NULL`. See the epic-ordering Notes.

## A doctor's maker logins (epic-doctor-profile entry 8)

A doctor gives the login (username or e-mail, and password) for an aligner maker's platform in
settings, "Maker data" (`accounts.MakerLogin`, UUID primary key, region-owned, never in
`client.updated`, never on the admin site). Both values are field-encrypted
(`mytp_core.crypto`). One login per named maker (Invisalign, Ormco Spark, Angel Aligner,
Solventum, Smartee) and up to 5 "other" logins, each with a maker name (60 characters at most,
unique per doctor ignoring case). The page shows only "Saved on {date}" or "Not saved": no value,
and no part of one, is ever rendered. Both inputs come back empty after an error and ask the
browser neither to fill in nor to save a password. A doctor can replace (both fields again) or
delete a login; a hint recommends an auxiliary profile on the maker's platform.

`accounts.makers` is the only writer (atomic, the user row locked): `save`, `add_other`,
`remove` and `erase`. What epic-ordering reads: `has_login(user, maker)` and `saved_makers(user)`
(named makers only: "other" never blocks); the save page `settings_maker_save` honours a safe
`next`. URL names: `settings_maker`, `settings_maker_save`, `settings_maker_delete`,
`settings_maker_other_add`, `_other_replace`, `_other_delete`.

The only way a value leaves the database is the regional reveal seam `accounts/makers_remote.py`
(`RESOURCE = 'maker_login'`, `load`, `assignee` (None until epic-clinical-ops has assignments),
`version`, `build(record, fields)`), shaped like `integration/sample.py` and tested by direct
calls. There is no core resource, route or admin screen yet: epic-clinical-ops adds them (see
its Notes). The erasure deletes all of an account's logins.

## Orders and checkout (epic-payments entry 1)

The `payments` app is the one place a purchase is recorded. `payments.models.Order` (UUID key,
a typed reference such as `KM-482915`, `payments.reference`) holds the lines
(`OrderLine`: the inputs to re-price it plus the priced values, the provider cost and its
`cost_complete` flag), the totals and VAT groups copied from `catalogue.pricing.PricedOrder`
(nothing in `payments` does price arithmetic), the company and currency, the gateways the
pricing offered and the one chosen, and the buyer's fiscal entity copied into encrypted
`bill_*` fields (entity id, version and VIES status kept; the link goes to NULL when the entity
is erased). `OrderChange` is the append-only change row (who, what, state before and after,
version).

`payments.service` is the only writer: `create_order` / `create_subscription_order` (priced by
`subscriptions.service.price_order_for` / `catalogue.pricing.price_subscription`; born in
`checkout`; a 0.00 order with an entity is paid at creation), `choose_entity` (checkout only;
copies the entity again and re-prices from the stored inputs), `start_payment` (freezes the order
as `pending_payment` and `accounts.fiscal.mark_used` the entity in the same transaction),
`mark_paid`, `cancel`, `expire` and `expire_stale`. Each locks the order, checks
`expected_version`, raises the version, stores the actor (doctor, admin or system, AD-22) and
fires its hook once; a replay on an order already in the target state does nothing.

Other apps plug in with `payments.hooks.register(event, name, receiver)` (events `created`,
`paid`, `cancelled`, `expired`; the receiver gets the locked order, runs inside the transition's
transaction, must be idempotent by `order.id`, and a raise rolls the transition back) and
`payments.gateways.register(name, start, enabled=None)` (`start(order, request)` returns the
redirect URL). The `expire_orders` job (`deploy/jobs.conf`, every 15 minutes) expires orders left
in `checkout` or `pending_payment` for more than 24 hours.

Pages (portal prefix `checkout/`): the checkout, the return and failure pages, the fake gateway's
page and the test-order page. A doctor sees only their own orders (404 otherwise). Two flags,
both off by default and refused by `manage.py check` on a public site (DEBUG and ROBOTS_NOINDEX
off; `payments.E001`, `payments.E002`): `PAYMENTS_FAKE_GATEWAY` offers the fake gateway (a portal
page with Pay and Fail) next to the real ones, and `PAYMENTS_TEST_ORDERS` serves
`/<lang>/checkout/test-order/`, where a doctor account picks a product it is offered and goes
through the real checkout (set both on dev and dev-ae in `settings.env`). Not here yet: real
gateways, webhooks and refunds (entries 2-4), the invoice (6), the bank-transfer page (5), the
admin pages and the order event to the admin site (8).

## Payments: webhooks (epic-payments entry 2)

`payments.gateways` is the full adapter interface: `register(name, start, enabled=None, ...)` with
the optional operations `verify_webhook(request)`, `fetch_payment(payment)`, `cancel_attempt`,
`refund`, `save_card`, `charge_saved_card`, the `session_lifetime` (30 minutes), the
`timestamp_tolerance` (5 minutes) and `required_settings`; `Adapter.supports(op)` says what an
adapter does. `start(payment, request)` now gets the `Payment` and records the gateway's own id
(`payments.service.set_gateway_payment_id`). The fake adapter implements every operation (its
records live in the cache, shared by the web workers, the task worker and the commands).

`payments.models.Payment` is one attempt at one gateway (order, gateway, the gateway's payment id
unique per gateway, amount, currency, state `open` / `confirmed` / `failed` / `closed` /
`needs_attention`, `session_expires_at`). One attempt is open per order (a database constraint): a
retry or a second tab first closes the previous one at the gateway (`cancel_attempt`); a gateway
without it waits for the session to time out (`service.AttemptOpen`). While an attempt is open the
order is frozen: the doctor cannot cancel it and `expire_orders` skips it until the session timed
out. `payments.models.WebhookLog` has one row per webhook, accepted or refused (result `accepted`,
`duplicate`, `bad_signature`, `stale`, `amount_mismatch`, `unknown`, `not_payable`, plus
`too_large` and `rate_limited`), with the IP, the gateway's event and payment ids, the amount, the
SHA-256 and size of the body (never the body); kept 180 days (`purge_webhook_logs`, job
`purgewebhooklogs`).

`POST /payments/webhook/<gateway>/` (public, csrf-exempt, outside the language prefixes and
`/api/`): the adapter checks the signature and timestamp, the payment is looked up by the
gateway's id, the event is taken in once (unique (gateway, event id) in the database), a log row is
written, a `django.tasks` task (`payments.tasks.confirm_webhook`) is queued and the answer is 200
at once. The task re-reads the payment from the gateway and calls `payments.service.confirm_payment`,
the one way an order is paid: the payment must be the order's open attempt, the order
`pending_payment`, and the re-read amount and currency equal the order's. Otherwise (paid already,
cancelled, expired, a late success of an earlier attempt, another amount) the order is left alone,
the payment is `needs_attention`, an error goes to Sentry by order UUID and the doctor's return page
shows an error message. A failed re-read is retried with backoff on the log row (`attempts`,
`next_try_at`); after the last try an error goes to Sentry. Bodies over 64 KB get 413, an IP with 20
failed signatures in 10 minutes gets 429. The return page re-reads the payment once through the same
path. Logs, Sentry and URLs carry no signature, secret, body or personal data.

`PAYMENTS_FAKE_WEBHOOK_SECRET` (settings.env; unset: derived from `SECRET_KEY`) signs the fake
gateway's webhooks. On dev and dev-ae, `manage.py fake_webhook <reference>` sends a valid one for
the order's open fake attempt; `--bad-signature`, `--stale`, `--amount X` and `--replay` send each
refusal on purpose. `payments.E003` fails `check` on a public site when an available gateway's
`required_settings` secret is missing. Nginx serves `/payments/webhook/` without the staging
basic auth (entry 3; deploy/README.md "Payment webhooks").

## Payments: Redsys (epic-payments entry 3)

`payments.redsys` is the card gateway of the Iberian company (5.9's rule offers `redsys`, shown as
"Card", then `bank_transfer`): Redsys's hosted payment page by redirection, signed with
HMAC_SHA256_V1 (a per-order key: `Ds_Order` encrypted with 3DES under the merchant key, then
HMAC-SHA256 of `Ds_MerchantParameters`).

- **Pay.** `start` gives the attempt a fresh `Ds_Order` (4 digits and 8 of `[0-9A-Z]`, the
  attempt's `gateway_payment_id`) and sends the doctor to `/<lang>/checkout/<order>/redsys/`
  (the doctor's own order only), which posts the signed form to Redsys's `realizarPago` by itself
  (`static/js/redsys-form.js`; a button without JavaScript): the amount in cents, currency 978
  (EUR), the notification URL and UrlOK / UrlKO, all built from `SITE_URL`. Redsys sends the
  doctor back to `/<lang>/checkout/<order>/redsys/ok/` or `/ko/` (GET or POST, CSRF-exempt), which
  answers 303 to the return or failure page; nothing posted there is read.
- **Notification.** Redsys's signed online notification arrives at `/payments/webhook/redsys/`
  through the shared handler: the signature is checked with our key (standard or URL-safe
  base64), `Ds_Date`/`Ds_Hour` (Europe/Madrid) must be within 30 minutes, the event id is
  `Ds_Order:Ds_Response`, and `Ds_Response` 0000-0099 is paid, any other failed. The verified
  notification is the record of the payment: its status is stored on the log row
  (`WebhookLog.status`), and `fetch_payment` returns a paid one of the attempt if any (a later
  refusal never hides a charge), else the latest failed one, else PENDING (no SOAP query). The
  signed merchant code and terminal must be ours. Showing the form page moves the attempt's
  session forward to 30 minutes from then. A lost notification leaves the order pending until it expires;
  staff can check Redsys's admin module.
- **Retry.** Redsys cannot close a session (no `cancel_attempt`): while an attempt may still be
  paid (`session_lifetime`, 30 minutes) a retry shows "payment in progress"; a refusal
  notification fails the attempt and the doctor can retry at once.
- **Refund.** `python manage.py redsys_refund <reference> [--amount X]` refunds the order's
  confirmed Redsys payment (the full amount by default) over Redsys REST (`trataPeticionREST`,
  transaction type 3); the signed answer is verified and must say `Ds_Response` 0900; it prints
  the refund id (`Ds_Order:authorisation code`). Nothing changes on the order (no state change,
  no credit note: entry 7). A refusal or an HTTP fault is an error naming only Redsys's code.

Settings (settings.env; the key is never logged and never shared): `REDSYS_MERCHANT_CODE` (the
FUC), `REDSYS_TERMINAL`, `REDSYS_SECRET_KEY` and `REDSYS_ENVIRONMENT` (`test`, the default, or
`production`). The adapter is offered when the code and the key are set. On a public site
`payments.E003` wants the first three, and `payments.E004` refuses the `test` environment and
Redsys's public test terminal (also at start: the site refuses to run).

Test terminal (published by Redsys; dev and dev-ae): merchant code `999008881`, terminal `1`,
the published SHA-256 test key, `REDSYS_ENVIRONMENT=test` (`https://sis-t.redsys.es:25443`). Test
card 4548 8120 4940 0004, CVV 123, any future expiry, 3-D Secure code 123456. The notification
reaches a staging site only once nginx serves `/payments/webhook/` without the basic auth
(re-provision, deploy/README.md "Payment webhooks").

Measuring the session lifetime: on dev, pay a test order up to Redsys's card form, leave it open
and note when Redsys refuses to go on (and whether a refusal notification arrives: the log shows
an accepted row with status `failed`). Set `SESSION_LIFETIME` in `payments/redsys.py` to that
time plus a margin; until then a retry during the session is refused for 30 minutes.
