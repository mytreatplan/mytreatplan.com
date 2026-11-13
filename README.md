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
