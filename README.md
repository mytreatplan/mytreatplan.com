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
  (its state in `detail`), `voucher_type`, `voucher_currency`, `voucher_doctor`, `voucher_used` (a live redemption
  blocks it, epic-subscriptions entry 4), `voucher_unknown` (the code is not in this
  region's snapshot),
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

On a staging site (`SENTRY_ENVIRONMENT` dev or dev-ae; locally `development` with DEBUG on),
`reset_to_basic` puts a test doctor (standard or manual, paid or not) back on a standard Basic,
so the same account can repeat the purchase checks; a change by the system with reason
`test_reset`. Anywhere else it refuses and writes nothing:

    python manage.py reset_to_basic <uid or email>

## Buying a subscription (epic-subscriptions entry 5)

A Basic doctor buys Advanced or Premium from the upgrade screen's button, on the purchase page
`/<lang>/upgrade/<level>/` (`subscriptions.views.upgrade_purchase`):

- **Periods.** A year upfront, and monthly (a 12-month commitment) only where
  `subscriptions.service.monthly_possible(user)`: the doctor's `subscription` billing rule (their
  country's group, the gateways it allows for their country) lists a gateway of the region that
  saves cards (`payments.gateways.can_save_cards`: Redsys today). The fake gateway never counts,
  so a Revolut country (IE) sees the year upfront only, and a forged monthly POST is refused. Prices
  are the catalogue's net ones with "+ VAT where applicable"; the order carries the VAT.
- **The order.** `payments.service.create_subscription_order`, with `save_card` whenever
  `monthly_possible` (the annual renewal is charged to the card too, 8.6; the payment link is for
  when that fails) and the optional subscription voucher code (`vouchers.voucher_for_code`), which applies to the
  year upfront only (the page hides the field with monthly; a code posted with monthly is refused,
  `voucher_period`). The rules live in `service.purchase` / `service.open_purchase`, not the view. A
  refusal shows the page again with its message (`payments.views.REFUSALS`) and the typed code; no
  order. Then the existing checkout; an order the voucher covers in full is paid at once (no
  gateway, a 0.00 invoice) and goes to its return page. A doctor with an open subscription order
  is sent to its checkout (no second order); a manual record, or a paid tier that counts, goes back
  to `upgrade` (changing between paid tiers is 8.8).
- **Activation.** The `paid` receiver `subscriptions_activate` (`service.activate`, in the
  transition's transaction) moves the record through `change` (the doctor as the actor, reason
  `purchase`, the change row's `order_id` naming the order: one per order, so a repeated `paid`
  changes nothing): the line's tier and catalogue row and period, started the payment day (Madrid),
  ending 12 months later minus one day, auto-renew on, active. A record that moved meanwhile (made
  manual, or already on a paid tier) is left as it is with a Sentry error by order UUID; the
  payment is kept and staff decide (refund). The return page of a paid subscription order says
  "Continue" to the welcome page.
- **Email.** After commit, `subscriptions.tasks.send_purchase_email` sends one confirmation per
  order (`run_once`, one try, a failure logged by order UUID): level, billing, amount, the end of
  the term (monthly: the next charge date), whether the card is saved for renewals, how to cancel
  (contact us, until 8.10), the invoice's codes-only PDF attached. Renewals, charges, their emails
  and cancellation are 8.6, 8.7 and 8.10.

Dev check: as a Basic ES doctor with a fiscal entity, buy Advanced upfront with a subscription
voucher of the snapshot (the record shows Advanced, the upgrade screen marks it); with Redsys's
public test terminal, buy Advanced monthly (one month invoiced, a `SavedCard`); with a voucher
covering the price, the order is paid at once with a 0.00 invoice and no gateway.

## Included cases (epic-subscriptions 13)

A paid subscription includes free TPS branded cases (the catalogue's `included_cases`, 5.17).
`subscriptions.included` keeps a per-doctor ledger (`CaseGrant`, `CaseUse`; only that module and
`subscriptions.service` read it):

- **Grant.** When a purchase activates the record (`service.activate`, same transaction), the doctor
  gets one grant for the term: the catalogue's number for `tps_branded`, frozen (a later catalogue
  edit never changes it), `upfront` or `monthly` from the order's period, the term's dates, keyed
  by the order (a repeated `paid` creates nothing). Renewals, upgrades and manual subscriptions get
  theirs in 8.6, 8.8 and 8.11.
- **Available** on a day: earned − reserved − consumed, over the grants whose term contains the
  day, and only while the record counts as a standard paid tier. Earned: the count (upfront) or
  `count × months paid // 12` (monthly; the confirmed payments of the purchase order,
  `payments.service.months_paid`).
- **Uses.** One per order line with `included_case` (the cart's tick is 9.3/9.6), from the
  soonest-ending grant with one available, under a lock on the doctor's grants: reserved by the
  `created` hook (none available: the hook raises and no order is created), consumed at `paid`,
  released at `cancelled` / `expired`, and a consumed one released at `refunded` while its grant's
  term runs. No outbox events (sales-finance reads the usage later).
- **Home.** The profile card shows "N of M included cases left this term" (M: earned so far) from
  `service.included_left`, through the `included_cases` template tag (never raises).

Run once after the deploy that adds it, for the doctors who bought before (deploy/README.md,
"Doctor subscriptions"); running it again grants none:

    python manage.py grant_included_cases [--dry-run]

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
links to the home. The welcome page, the upgrade screen and the purchase page use
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

The first production receivers (epic-subscriptions entry 4, registered in
`SubscriptionsConfig.ready`) are `voucher_reserve` (`created`), `voucher_consume` (`paid`) and
`voucher_release` (`cancelled`, `expired`) of `subscriptions.vouchers`: a `VoucherRedemption`
follows the order (reserved, consumed, released) and records `voucher.redeemed` and
`voucher.released` in the outbox. Before an order is created `create_order` and
`create_subscription_order` re-read the voucher (`voucher_unknown`) and call
`subscriptions.vouchers.check_use` (`voucher_used`); a screen that takes a typed code gets its
voucher with `subscriptions.vouchers.voucher_for_code(code)`, which raises `voucher_unknown`
when the code is absent.

Work orders (epic-ordering 9.8, `patients.service`, registered in `PatientsConfig.ready`): one
per order line that names a patient. `work_orders_hold` (`transfer_approved`) creates them in
`pending_payment`, where they block the patient (`blocking_work_order`; a manual pending-payment
work order without an order never blocks, and an order's own never block it);
`work_orders` (`paid`) moves them to `pending_assignment` (or creates them there), closing the
patient's live ICE first for a planning (`closed`, mytp-core 0.35.0); `work_orders_cancel`
(`cancelled`, `expired`) cancels them. Every create and stage change records
`work_order.created` or `work_order.stage_changed` in the outbox (no patient name or code).

Pages (portal prefix `checkout/`): the checkout, the return and failure pages, the fake gateway's
page and the test-order page. A doctor sees only their own orders (404 otherwise). Two flags,
both off by default and refused by `manage.py check` on a public site (DEBUG and ROBOTS_NOINDEX
off; `payments.E001`, `payments.E002`): `PAYMENTS_FAKE_GATEWAY` offers the fake gateway (a portal
page with Pay and Fail) next to the real ones, and `PAYMENTS_TEST_ORDERS` serves
`/<lang>/checkout/test-order/`, where a doctor account picks a product it is offered and goes
through the real checkout (set both on dev and dev-ae in `settings.env`). Not here yet: real
gateways, webhooks and refunds (entries 2-4), the admin pages and the order event to the admin site (8).

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
order is frozen: the doctor cannot cancel it (except a bank transfer attempt, which `cancel` closes
itself, see entry 5 below) and `expire_orders` skips it until the session timed out. `payments.models.WebhookLog` has one row per webhook, accepted or refused (result `accepted`,
`duplicate`, `bad_signature`, `stale`, `amount_mismatch`, `unknown`, `not_payable`, plus
`too_large` and `rate_limited`), with the IP, the gateway's event and payment ids, the amount, the
SHA-256 and size of the body (never the body); kept 180 days (`purge_webhook_logs`, job
`purgewebhooklogs`).

`POST /payments/webhook/<gateway>/` (public, csrf-exempt, outside the language prefixes and
`/api/`): the adapter checks the signature and timestamp, the payment is looked up by the
gateway's id, the event is taken in once (unique (gateway, event id) in the database), a log row is
written, a `django.tasks` task (`payments.tasks.confirm_webhook`) is queued and the answer is 200
at once. The task re-reads the payment from the gateway and calls `payments.service.confirm_payment`,
the way a gateway's payment pays an order (a bank transfer is paid by
`service.mark_transfer_paid`, see entry 5 below): the payment must be the order's open attempt, the order
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

## Payments: bank transfer (epic-payments entry 5)

`payments.bank_transfer` is an adapter that calls no gateway. Paying by transfer opens an attempt
with no session end (epic-ordering 9.17), fires the `transfer_approved` hook (each time the
transfer is chosen; the cart app removes the order's patients' items there) and sends the doctor
to `/<lang>/checkout/<id>/transfer/`: the billing company's holder, IBAN (in groups of four), BIC
and bank, the amount and the order reference as the transfer concept, with a line saying the
order stays pending until the transfer is received (no deadline). The checkout and return pages lead there while the
attempt is open (`?change=1` on the checkout picks another method). The order stays
`pending_payment`. `Adapter.accepts(order)` is a new optional hook: `gateways.offered` keeps a
gateway only when it returns true; transfer accepts an order whose company
(`BillingCompany.has_bank_details`) has all its bank details, and the rule's `gateway_countries`
(entry 14) limit it per country. Bank details are shown to the order's own doctor only and never
logged.

Staff record a received transfer with `payments.service.mark_transfer_paid(order, amount,
*, expected_version, actor)` (admin or system actor): attempt `confirmed` and `mark_paid` in one
transaction; refused unless the order is `pending_payment` with an open transfer attempt and the
amount (and currency, for a `Money`) equals the total; a repeat returns the order unchanged. The
doctor can cancel a pending transfer order: `cancel` closes the transfer attempt and cancels the
order (an open attempt of another gateway still refuses with `AttemptOpen`). `expire_orders` never
expires a `pending_payment` order that ever had a transfer attempt (even after a switch to card),
and "Finish order" in the cart never cancels one: staff cancel it by hand. The `remind_transfers`
job (`remindtransfers` in `deploy/jobs.conf`, hourly) sends one plain-text reminder per order every
7 days from the freeze (`pending_at`) while a transfer attempt is open, to the login email in the
doctor's language (`Profile.language`); `Payment.reminded_at` records each one, set only after a
successful send, so a failed send is retried by the next run.

## Invoices (epic-payments entry 6)

The `invoices` app holds structured, immutable invoices (AD-7): `Invoice` with the seller's snapshot
(from the billing company at issue time), the buyer's encrypted `bill_*` snapshot, the VAT groups,
the totals, the issue date and time in the company's legal time zone and an e-invoice status
(`none` until a channel exists); `InvoiceLine` with a tax category (S, Z, E, AE), the included-case
flag and a `patient_code` (blank until 9.1 fills `OrderLine.patient_code`); and `InvoiceSeries`, a
locked counter per (company, instance, year). The number is `<company>-<instance>-<year>-<6
digits>` (`ES-EU-2026-000001`), taken in the transaction that creates the invoice, so the series
has no gap; the year is the company's local year. An invoice is copied from the order, never
re-priced, and is append-only: `save` of an issued invoice, `delete` and the queryset `update`,
`bulk_update` and `delete` raise `invoices.models.AppendOnly`; only the three e-invoice fields
change, through `invoices.service.set_einvoice_status`. Nothing links an invoice to the user or the
fiscal entity, so an erasure leaves it.

`invoices.service.issue_for_order` is the `paid` hook receiver (registered in `InvoicesConfig.ready`):
it runs inside the payment's transaction, is idempotent per order, and a failure (a company missing
from the snapshot) rolls the payment back. `issue_manual(company_code, buyer, lines, currency,
actor)` invoices a sale with no order and no account (`kind=manual`); epic-sales-finance builds its
screen. Orders paid before this release have no invoice. Not here yet: the
accounting copy (7.8), restore-safe numbering (7.9), the PDF and the doctor's download (7.10) and
any e-invoice transmission.

## Credit notes, refunds and cancellation (epic-payments entry 7)

A paid order leaves `paid` only through a refund. `payments.refunds.refund(order, expected_version,
actor, reason, manual=False, note='')` (staff or the system; a doctor actor raises `InvalidActor`)
refunds the whole order, in phases: a `Refund` row is committed `pending`, the gateway refunds
outside any lock (or nothing is called for `manual=True`, which needs a `note`: a bank transfer, a
0.00 order), then one transaction under the order lock marks
the refund `done`, issues the credit note and moves the order to `refunded`, firing the new
`refunded` hook (receivers that release vouchers or give back included cases belong to later
stories; a used voucher stays used). A gateway refusal leaves the refund `failed` (`RefundFailed`,
order still `paid`, staff may try again). A timeout or an answer that cannot be trusted leaves it
`needs_attention` with a Sentry message naming the order by UUID: it is never retried
automatically, because Redsys may already have refunded, and the order stays `paid` and blocked for
any refund (manual ones too) until staff resolve it, which the admin site's tools (epic-sales-finance)
will do. At most one pending refund per order (database constraint);
`Refund.gateway_refund_id` is not unique (Redsys repeats it per payment). Partial refunds are not
supported yet.

A credit note is an `Invoice` of `kind=credit_note`: the original's seller and buyer snapshots,
lines and VAT groups with negated amounts, `original` (so its number and issue date), a `reason`
(`cancellation`, `goodwill`, `error`), `order` empty (it reaches its order through
`original.order`) and an empty `extensions["rectification_code"]` for the tax advisor. It is
numbered `<company>-<instance>-<year>-R<6 digits>` in its own `InvoiceSeries(kind=credit_note)`,
gapless like the invoices, and append-only like them. One per invoice.

`payments.refunds.staff_cancel` cancels an unpaid order with a staff actor (`AttemptOpen` while a
card session is live). For staff and staging until the admin screens exist (epic-sales-finance),
both run as the system actor: `python manage.py refund_order <reference> --reason
{cancellation,goodwill,error} [--manual --note TEXT]` (replaces `redsys_refund`) and
`python manage.py cancel_order <reference>`.

## The accounting feed (epic-payments entry 8)

The admin site keeps the accounting copy. Each invoice (an order's, a manual sale's, a
credit note), each confirmed payment and each completed refund is recorded on the outbox, in
the transaction that creates it (`invoices/feed.py`, mytp-core 0.31.0): `invoice.issued`
(kind `order`, `manual` or `credit_note`), `payment.confirmed` and `refund.completed`, each
sent once, at version 1. An idempotent repeat (`issue_for_order`, `issue_credit_note`) sends
nothing, and a 0.00 order sends the invoice only. Records issued before the feed existed are
not sent. A payload the contract refuses rolls the payment or refund back.

- Amounts are `{amount, currency}` (negative on a credit note), percents strings.
- The doctor is referenced by `client_uid` only, left out for a manual sale or an order
  whose account is gone; the buyer is sent as issued, and the admin site stores it
  encrypted, so erasing the doctor never touches the copy.
- Patients are `patient_code` only (the key is left out when a line has none).
  `issue_manual` refuses a patient code that is not a region prefix and 8 characters
  (`EU-7K3QX9MD`) before anything is written.
- **For staff:** never type a patient's name in a manual line's `name`; the contract cannot
  check free text.
- A refund's staff note, gateway secrets and card data are never sent.

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
- **Refund.** `redsys.refund` refunds the confirmed payment over Redsys REST (`trataPeticionREST`,
  transaction type 3); the signed answer is verified and must say `Ds_Response` 0900. A refusal
  is a `GatewayError`; no answer or an untrustworthy one is a `GatewayUnknown`. Refunds go through
  `refund_order` (entry 7, below). A type-3 notification is logged and marked done, never retried.

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

## Payments: saved cards (epic-payments entry 11)

Renewals (epic-subscriptions 8.5 on) charge a doctor's card without the doctor present.
`payments.repeat` is the gateway-neutral part; a gateway takes part when it registers
`saves_cards=True` with a `charge_saved_card(card, payment)` operation (`gateways.can_save_cards`:
Redsys and the fake gateway; not Revolut until entry 16).

- **Saving.** An order saves the card only when it asks (`Order.save_card`, set by whoever
  creates it: `service.create_subscription_order(..., save_card=True)`; 8.5 sets it for monthly
  or auto-renewing subscriptions). Such an order is offered only gateways that save cards, and
  its checkout shows a consent line (no checkbox: Venta + is priced per stored card). On Redsys
  the payment form adds the COF "initial" fields (`DS_MERCHANT_IDENTIFIER=REQUIRED`,
  `DS_MERCHANT_COF_INI=S`, `DS_MERCHANT_COF_TYPE=R`); the signed paid notification's
  `Ds_Merchant_Identifier` (the token), `Ds_Merchant_Cof_Txnid`, `Ds_ExpiryDate`, `Ds_Card_Number`
  (masked) and `Ds_Card_Brand` become the doctor's one `SavedCard` of that gateway (a newer save
  replaces the older). Token, COF transaction id and masked number are field-encrypted and never
  logged, sent to Sentry or put in a URL; only brand, last 4 digits and expiry are ever shown.
  A paid save-card order without a token is a Sentry warning by order UUID. Account erasure deletes
  the cards. Showing and removing the card is 8.10's.
- **Charging.** `repeat.charge_saved_card(order, period, actor=...)` freezes the order as
  `pending_payment` with the card's gateway, opens a `Payment` of that `period` (the caller's key),
  calls the gateway outside any transaction and pays the order only through
  `service.confirm_payment`. It returns `RepeatResult(outcome, payment, code)`: `paid`; `declined`
  (the attempt `failed` with the gateway's code, e.g. `0190`, or `card_expired` without a call;
  the order stays payable and the caller decides what follows); `unknown` (no answer, an
  untrustworthy one or an `errorCode`: the attempt `needs_attention`, a Sentry error by order
  UUID; staff check Redsys's admin module, there is no status query); `not_payable`. Idempotent
  per order and period: a confirmed, open or needs-attention charge of the period is returned as
  it is, never repeated (a database constraint too); only a declined one lets a new charge, with a
  new `Ds_Order`, be made. `NoSavedCard` / `UnsupportedOperation` create nothing.
- **Redsys.** A merchant-initiated REST charge (`trataPeticionREST`, type 0) with
  `DS_MERCHANT_IDENTIFIER`, `COF_INI=N`, `COF_TYPE=R`, `COF_TXNID`, `DIRECTPAYMENT=true`,
  `EXCEP_SCA=MIT`; the answer is trusted only when its signature verifies and its order, amount,
  currency and type match the request. `Ds_Response` 0000-0099 is paid. The COF field names come
  from ActiveMerchant's `redsys_rest.rb` and creagia/redsys-php (Redsys's documentation was not
  reachable): confirm them on the test terminal.
- **Fake gateway.** Its Pay saves a fake card for a save-card order; `fake.decline_next_charge(code)`
  makes the next charge decline with that code.

Recurring charges on our own terminal need Santander's paid add-on "Servicios Venta +" (per month,
by stored cards: 0-300 cost 8 EUR, 301-600 11 EUR, 601-1000 15 EUR); the contract is not signed
yet (2026-11-26).

Test-terminal check (dev, Redsys's public test terminal, `REDSYS_*` as in "Payments: Redsys"):
create a subscription order with `save_card=True` in `manage.py shell`, pay it with the test card,
check the doctor has one `SavedCard` (brand, last 4, expiry); then for a second order of the same
doctor run `repeat.charge_saved_card(order, '2027-01')` twice: Redsys's admin module shows one
charge, and the second call returns the first outcome. Note whether the notification carried
`Ds_Merchant_Identifier`/`Ds_Merchant_Cof_Txnid`, whether the REST charge also sent a notification,
and the decline code of a refused MIT. The same check on our own terminal is owed until Santander
signs and sends its test credentials.

## Payments: Revolut (epic-payments entry 4)

`payments.revolut` is the card gateway of the Irish company (the EU region's `revolut`, shown as
"Card") on Revolut's hosted checkout page, over the Revolut Merchant API (sandbox first, API
version pinned to `2026-09-22`; every call sends `Authorization: Bearer <secret key>`).

- **Pay.** `start` creates a Revolut order (`POST /api/orders`: the amount in cents, EUR, the order
  reference, "MyTreatPlan order <reference>", `redirect_url` = the return page from `SITE_URL`,
  `expire_pending_after` 30 minutes; the attempt's session is 35), stores its id as the attempt's `gateway_payment_id` and
  redirects the doctor to Revolut's `checkout_url`. No doctor name, email or other personal data
  is sent. A Revolut fault shows "try again" on the failure page and leaves no live attempt.
  EUR orders only.
- **Webhook.** `/payments/webhook/revolut/` through the shared handler. `Revolut-Signature` holds
  one or more `v1=<hex>` entries, each HMAC-SHA256 (signing secret) over
  `v1.{Revolut-Request-Timestamp}.{raw body}`; the timestamp (Unix milliseconds) must be within 5
  minutes. The payload carries no status: the event id is `order_id:event` and the task re-reads
  the order (`GET /api/orders/{id}`: `completed` is paid, `failed` and `cancelled` failed,
  `pending`, `processing`, `authorised` pending). The order is paid only through
  `service.confirm_payment`, which checks the amount and currency. A lost webhook is recovered
  by the return page's re-read.
- **Retry.** `cancel_attempt` cancels the pending Revolut order (`POST /api/orders/{id}/cancel`)
  before a new attempt; a refusal is settled by re-reading the order.
- **Refund.** `POST /api/orders/{id}/refund` with an `Idempotency-Key`; Revolut accepting it
  (201) counts as refunded. A 4xx or a `decline_reason` is a `GatewayError`; a timeout or 5xx a
  `GatewayUnknown`. A refund that later fails shows only in Revolut's dashboard. Refunds go
  through `refund_order` (entry 7).

Settings (settings.env; never logged, never shared): `REVOLUT_SECRET_KEY`, `REVOLUT_WEBHOOK_SECRET`
(comma-separated while it is rotated) and `REVOLUT_ENVIRONMENT` (`sandbox`, the default, or
`production`). The adapter is offered when the key is set. On a public site `payments.E003`
wants both secrets and `payments.E005` refuses `sandbox` (the site refuses to start).

Registering the webhook (once per site, after typing `REVOLUT_SECRET_KEY` and restarting):

```sh
python manage.py revolut_webhook register   # prints the signing secret once
python manage.py revolut_webhook list
python manage.py revolut_webhook delete <id>
```

Type the printed secret into settings.env as `REVOLUT_WEBHOOK_SECRET` and restart gunicorn and
the worker.
