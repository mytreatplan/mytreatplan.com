---
type: review
lens: adversary (incompatible pairs)
target: architecture-portal-admin.md (draft, 2026-10-29)
units: the 16 epics under _bmad-output/initiative-mytreatplan-portal-admin/epic-*/
date: 2026-10-29
---

# Adversarial review — two epics that obey every AD and still don't fit together

**Verdict:** The spine is solid on transport, ownership of *definitions*, money and files. It is thin wherever two sites mutate or interpret the same fact over time: ordering and deletes on the client-record copy, erasure across the three copies, redemption state for centrally defined vouchers, who owns *assignment* and *slot booking*, idempotency of remote-access writes, and how business dates are read across time zones. I found 12 pairs that both follow the ADs to the letter and still build incompatibly. Pairs 1–6 are blockers for epic-platform / epic-admin-interface inception. Pairs 7–12 should be settled before the epic that hits them is incepted.

Each finding gives: the two units, how each obeys the spine, the clash, and the AD to add or tighten.

---

## Blockers

### 1. Client-record sync: deltas vs snapshots, no ordering, no deletes

- **Unit A: epic-doctor-profile.** It emits `fiscal_entity.created`, `fiscal_entity.updated`, `shipping_address.deleted` from the profile service in the same transaction (AD-3, AD-15, naming convention). Each event carries only the changed entity.
- **Unit B: epic-admin-interface.** It owns the client record and builds it from `client.updated`, with a payload that is the full doctor snapshot. It upserts on receipt and deduplicates on `event_id` (AD-3).
- **Clash:** AD-3 gives at-least-once delivery with dedup, but says nothing about order. When delivery A fails, the retry lands after a later event B, so a stale snapshot overwrites a newer one. Unit B has no handler for Unit A's per-entity deltas. A deleted shipping address never leaves the copy, because nothing says deletes travel as events. Both units are compliant.
- **Close with AD-17 (Copy sync semantics):**
  - Every centrally stored copy is fed by **full-state events per aggregate root**: `client.*` for the doctor and its fiscal entities and addresses, `invoice.*`, `contact_message.*`. Each event carries a per-aggregate monotonically increasing `version` (an integer bumped by the owning service in the same transaction).
  - The hub applies an event only if `version` is greater than the stored one, and ignores it otherwise.
  - A delete is a `*.deleted` event carrying a tombstone, never an omission.
  - The outbox delivers per aggregate in `version` order, so one stuck event blocks only its own aggregate.
  - A full re-sync endpoint (`GET /api/v1/clients?since=`) exists for rebuilds.

### 2. GDPR erasure: three copies, two triggers, conflicting retention

- **Unit A: epic-admin-interface (client record).** On a doctor erasure it receives `client.updated` with blanked fields, or nothing at all, because part-1 `accounts/erasure.py::erase_user` writes no outbox event. It keeps the copy.
- **Unit B: epic-sales-finance (accounting copy and export).** It joins the accounting copy to the client record by doctor UUID to print the buyer's name and VAT number. If Unit A deletes or blanks the record, past exports change after the fact, or they lose the buyer data the law requires them to keep.
- **Also:**
  - Part-1 erasure runs from the portal's `django.contrib.admin` with `by` = a local staff `User`. Part 2 adds a second trigger: an admin-site user over remote access, who is not a portal `User`. AD-15 says one service, but `erase_user(by=User)` cannot take an admin-site actor (see pair 5).
  - Patient deletion (48 h) removes the code-to-name mapping in the region, which turns the central patient codes into anonymous data. That is fine, but no AD says so.
  - The part-1 purge record and the 30-day backups (S4) are regional only. Nothing says the hub's backups age out erased client data.
- **Close with AD-18 (Erasure and retention across copies):**
  - Erasure is executed only by the regional service, and the admin site requests it over remote access.
  - The service emits `client.erased` (via AD-17), and the hub then **deletes the client record** except UUID, region and erased_at.
  - The **accounting copy is self-contained and immutable**: each invoice and credit note copy carries the buyer's fiscal snapshot as issued, never a join to the client record, and is kept for the statutory period. Erasure does not touch it.
  - Hub backups have a stated maximum age.
  - Patient codes stay in the accounting copy after patient deletion, as anonymous references.
  - Tighten AD-7: "the accounting copy is the invoice as issued, including buyer fiscal data".

### 3. Voucher redemption: central definition, regional use, no owner of usage state

- **Unit A: epic-subscriptions (redemption).** It reads the voucher from the read-only snapshot (AD-3, AD-16), checks "once per person" against a regional `Redemption` table, and marks the voucher used when the order is **paid** (webhook).
- **Unit B: epic-ordering (try-me vouchers, cart).** It reuses the logic but applies the voucher when the order is **created**, so a cart with two patients and two bank-transfer orders pending for days each passes the "not yet used" check.
- **Also:**
  - epic-catalogue models general vouchers with a global cap, or a validity "region" that can be blank. Two regions can then each redeem up to the cap. No AD forbids this, and the snapshot is read-only, so no instance can decrement anything.
  - epic-sales-finance needs voucher usage, but redemption is not in the AD-2 table, so it is regional-only and has no event.
  - Disabling a voucher centrally while an order is in flight is undefined.
- **Close with AD-19 (Vouchers):**
  - The voucher definition is admin-owned. **Redemption is a regional entity** owned by one service (epic-subscriptions), with a unique constraint `(voucher_uid, doctor_uid)` for once-per-person.
  - A voucher is **reserved at order creation** and becomes **consumed on paid**. It is **released on cancel or expiry** of the order, under the same row lock that creates the order.
  - Every voucher is scoped to exactly one region. A cross-region cap is not supported, or it needs a hub-side reservation call, which this spine does not have.
  - A voucher disabled or expired in a new snapshot does not void reservations already made.
  - `voucher.redeemed` and `voucher.released` events feed the usage report.

### 4. Assignment and the case state machine have two owners

- **Unit A: epic-clinical-ops.**
  - Specialist calendars are admin-owned (AD-2).
  - Pre-assignment ("first free slot, balanced workload") needs the calendar, so the epic stores assignment and pre-assignment in the admin DB.
  - A timer on the admin site firms the assignment after 24 h and then calls the remote API "set stage Assigned".
  - The "Clinical User sees only assigned" filter queries the admin DB.
- **Unit B: epic-case-lifecycle.** It owns stages (work orders and case stages are regional per AD-2) and adds `assignee_uid` and a `pre_assigned` flag on the work order. It sends the "assigned" message the moment the remote call sets the assignee, and its own job firms the assignment at 24 h.
- **Clash:**
  - Two stores hold the assignment and two timers firm it.
  - The doctor gets the "assigned" message at pre-assignment in one design and only at firm assignment in the other.
  - Unit A's admin lists show assignments the region does not have, and the reverse.
  - Unit A may call "set stage" for a transition that Unit B's machine forbids, for example skipping "Pending registers upload". AD-15 only says that one service is called, not who defines the legal transitions.
- **Close with AD-20 (Case state machine and assignment):**
  - The stage machine (states, allowed transitions, which actor types may trigger each) is defined once in `mytp-core` and enforced only by the portal `cases` service. Remote calls request a **transition by name**, never "set stage = X".
  - **Assignment is a regional field** on the work order (`assignee_uid` = admin user UUID, plus a `firm_at` timestamp). The portal job firms it, and the "assigned" message is sent at firm time.
  - The admin site reads assignments over remote fan-out or from a `work_order.assigned` event. It keeps only calendar *blocks* referencing a work-order UUID, never a second assignment record.

### 5. Admin-user identity inside the regions

- **Unit A: epic-admin-interface / AD-4.** The remote-access token carries the admin UUID and role. The region logs who did what by UUID.
- **Unit B: epic-case-lifecycle and epic-support.** They show the doctor "Your specialist: Dr X" and "Answered by Ana". To do that they store the display name passed in each remote call, in the payload. epic-appointments instead expects a synced admin-user directory. epic-clinical-ops passes the name only on assignment.
- **Clash:**
  - Names drift across records and nothing updates them.
  - Part-1 regional models (`erase_user(by=User)`, the `LogEntry` user_id, `Attempt.account_id`) assume the actor is a local `User` pk, so an admin-site actor has no slot. One epic will create shadow `User` rows for admin staff in each region. Those rows become a second owner of admin users, which violates AD-1 and AD-2.
  - If an admin user is deleted on the hub, the UUIDs in regional logs dangle.
- **Close with AD-21 (Actors):**
  - Every regional write and log records `(actor_type ∈ {doctor, admin, system}, actor_uid)`. No shadow admin `User` rows in instances.
  - A minimal **admin directory** (uuid, display name, public title, active) travels in the configuration snapshot. It is the only source of admin names in a region.
  - Admin users are never hard-deleted, only deactivated.
  - Part-1 services that take `by=User` get an actor parameter.

### 6. Remote-access writes have no idempotency or concurrency control

- **Unit A: epic-sales-finance.** It clicks "mark as paid", the remote call times out, and the interface client (mytp-core) retries.
- **Unit B: epic-payments.** Its `mark_paid(order)` service issues the invoice. It is idempotent only if it checks state under lock, and AD-8's idempotency rule binds *tasks*, not interface requests. On retry it may issue a second invoice. Under AD-7 numbers are gapless and immutable, so the extra invoice can only be undone with a credit note.
- **Same pattern:**
  - Clinical-ops "uploaded to maker" retried restarts the 7-day records-deletion timer.
  - A stage transition retried sends two messages.
  - Profile edits from Sales and from the doctor at the same moment end with the last write winning silently.
- **Close by tightening AD-3 and AD-15:**
  - Every remote-access mutation carries an `Idempotency-Key` (a UUID minted by the admin site per user action), stored by the instance with the response for at least 24 h.
  - Every mutation carries the entity's `version` (from AD-17) as an `If-Match` precondition. A mismatch returns 409 with `{"error":{"code":"stale"}}`.
  - Services that cause external effects (invoice issue, message, gateway call) are idempotent on the entity's state transition, not only on the request.

---

## Should fix before the affected epic

### 7. Specialist calendar (admin) vs appointment (region): double booking

- **Unit A: epic-appointments, portal side.** The doctor requests a slot at least 72 h ahead. The portal needs availability, but the channels in AD-3 have no portal→hub read. So it either reads the snapshot (availability is not in it) or stores the appointment regionally (AD-2) and emits `appointment.requested`.
- **Unit B: epic-clinical-ops.** It computes availability from admin calendars plus the appointments it has seen as events, and pre-assignment books the "first free slot".
- **Clash:**
  - An EU doctor and a UAE doctor request the same specialist slot in their own instances. Both are accepted, and the hub learns of the clash asynchronously.
  - The specialist's "record an appointment arranged by WhatsApp" writes where? If it is written into the admin calendar, the region doesn't know. If it is written over remote access, the admin calendar doesn't know until the event arrives.
- **Close with AD-22 (Slots):**
  - **The admin site owns slot occupancy.** A slot hold is a hub entity with no patient data (specialist, interval, region, appointment UUID).
  - Doctor requests are *requests* only. The specialist's confirmation on the hub atomically takes the hold and then calls the regional appointment service over remote access. The hold is released if that call fails.
  - Doctor-visible availability is fetched live through a new, explicitly allowed **portal→hub read call** (signed per AD-4). Alternatively, the spine states that doctors pick from coarse windows and only the hub resolves exact slots.
  - Either way, add the portal→hub call to AD-3's channel list, or forbid it explicitly.

### 8. Time zones and business dates

- **Unit A: epic-catalogue.** It stores voucher validity and subscription validity as `date` and emits them in the snapshot as ISO dates. The convention "dates ISO 8601 in UTC with Z" cannot apply to a date-only value.
- **Unit B: epic-subscriptions and epic-payments.**
  - They evaluate "valid until 31 Dec" and "annual renewal on the anniversary" in UTC.
  - The invoice date and the series year are taken from `timezone.now()` in UTC. A UAE invoice issued 1 Jan 01:30 Dubai time gets dated 31 Dec of the previous year and numbered in the old year's series.
  - epic-sales-finance reports by Europe/Madrid day.
- **Also:**
  - "Appointments store both zones" is ambiguous: wall times plus zones, or an instant plus zones?
  - The 72 h rule is unaffected only if it is computed on instants.
  - The specialist's zone lives in the admin user profile (hub), which the region does not have (see pair 5).
- **Close with AD-23 (Time):**
  - Instants are stored and transmitted in UTC.
  - **Business dates** (invoice date, series year, voucher and subscription validity, renewal day, report day) are `date` values interpreted in the **billing company's** time zone, or the region profile's when there is no company. They travel as `YYYY-MM-DD` with no Z.
  - An appointment stores the UTC instant plus the IANA zone of the doctor and of the specialist, never wall times.
  - The specialist's zone travels with the admin directory (AD-21).

### 9. Subscription renewal vs upgrade vs invoicing

- **Unit A: epic-subscriptions.** A renewal job (AD-8) runs `renew(subscription)`, idempotent "keyed by the UUID of the record it acts on", which is the subscription UUID. The second month's renewal is then treated as a duplicate, or the key is ignored and a rerun charges twice.
- **Unit B: epic-subscriptions upgrade path / epic-payments.** An upgrade mid-cycle creates a proration order in the same minute the renewal runs. Two orders, two charges and two invoices follow for overlapping periods. Each one is valid under AD-7.
- **Close:**
  - Tighten AD-8: the idempotency key is *the record plus the transition or period*, for example `(subscription_uid, period_start)`.
  - Add to AD-19 or a new subscription AD: a subscription has a single `SubscriptionPeriod` row per period, created under a row lock on the subscription by one service. Renewal, upgrade, cancel and expiry all go through it, and an order references the period it pays for.

### 10. Workshop invoices for another region's doctor; manual billing for non-regional customers

- **Unit A: epic-sales-finance.** It builds "manual order and billing for an existing customer". An initiative decision says that "another region's doctor gets a manual invoice", for example a UAE doctor billed for an EU workshop by the EU company.
- **Unit B: epic-payments / AD-7.** Only the regional instance issues invoices, and only for its doctors (AD-1: the doctor is owned by its home region).
- **Clash:** To comply, Sales either creates a duplicate doctor account in the EU instance, which means two owners of one doctor, or issues the invoice on the hub, which violates AD-7. Each is a reasonable reading of the spine.
- **Close by tightening AD-7:** introduce a regional **billing customer** (a fiscal party, not a doctor account) that an instance may invoice. A doctor from another region is invoiced as a billing customer that references the doctor's UUID, with no login, profile or patients. Alternatively, state that cross-region manual invoices are out of the system.

### 11. Manual work orders: two creation paths, different invariants

- **Unit A: epic-clinical-ops.** It creates a manual patient and work order over remote access, in state "pending payment", with no order yet.
- **Unit B: epic-ordering.** "One work order per patient-service" is created at payment from an order line, with `order_line` set to NOT NULL. Its pending-payment list instead expects an *order* in state pending. epic-sales-finance's "manual order for an existing customer" also creates an order, on another path.
- **Clash:** A work order with no order line breaks Unit B's model. Two "pending payment" concepts (order vs work order) appear on the dashboard.
- **Close by tightening AD-15:** for each regional entity, the spine names the **owning epic and service module**: work orders belong to `orders` (epic-ordering), orders to `payments`. Other epics create through those services only. A manual work order is a work order whose order line is filled in later, and the owning epic's model must allow that.

### 12. Snapshot versions vs definitions referenced by past sales

- **Unit A: epic-catalogue.** It hard-deletes a retired product or edits the provider cost row in place ("prices over time" implemented as current-price-only). It renumbers snapshot versions per entity type.
- **Unit B: epic-sales-finance.** The margin report joins accounting-copy lines (product UUID) to current provider costs. AD-6 snapshots price, discount, VAT and company, but not **cost**, so the report shows today's cost against last year's sale. A deleted product orphans lines.
- **Also:**
  - AD-3 says "apply atomically", but not that versions are monotonic and site-wide, or that an older snapshot must be refused. A delayed nudge after a manual pull could apply an older version.
  - An order line does not record which snapshot priced it.
- **Close by tightening AD-3 and AD-6:**
  - Snapshot versions are a single monotonic integer per hub, and an instance never applies a version at or below its current one.
  - Definitions are never hard-deleted, only retired.
  - Provider costs are effective-dated rows.
  - Each order line stores `snapshot_version` and the **unit cost** resolved at order time, alongside price.

---

## Smaller holes noticed (no pair needed, worth a line in the spine)

- **Special subscription assignment.** P6's "manually assigned special subscription" has no owner in AD-2. Is the definition-to-doctor binding hub data or a regional field set via remote access? Pick regional (consistent with pair 4) and list it.
- **Home region change.** A doctor who moves from the EU to the UAE has no path. Either state "never; a new account" or define a migration.
- **Admin "my calendar" and "team calendar".** These must show patient *codes*, not names, because AD-2 keeps names off the hub, and calendar entries are hub data. State it in AD-2's note.
- **Contact messages.** These are copied centrally but contain free text from the public, which may include health data. Say whether the hub copy is subject to AD-18 erasure.
- **Interface version skew.** The "served alongside the old until every region upgrades" rule needs a counterpart for events: the hub must accept the event versions of the oldest live region.
