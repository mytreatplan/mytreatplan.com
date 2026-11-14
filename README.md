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
