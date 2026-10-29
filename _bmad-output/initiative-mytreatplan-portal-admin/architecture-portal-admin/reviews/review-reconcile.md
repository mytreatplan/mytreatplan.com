# Reconcile review — architecture-portal-admin spine vs its inputs

- **Reviewed:** `architecture-portal-admin.md` (draft, 2026-10-29), AD-1 to AD-16, conventions, Deferred
- **Against:** the initiative (P1–P15 + Notes), the 16 epic envelopes, `.memlog.md`, the converted requirements document `reqs.md` (initiative Notes override it)
- **Lens:** what binds more than one epic but did not land in the spine; contradictions between the spine and a recorded decision. Single-epic details are left to their epics.
- **Date:** 2026-10-29

## Verdict

**Needs revision before epic-platform starts.** The topology, interface, money, ids, files and jobs decisions are sound and match the memlog. The gaps are the "quiet" rules that several epics will each implement on their own unless the spine fixes them: who may see what (roles, region scope, assignment), the inputs to pricing and VAT, the retention and deletion windows, patient data reaching third-party processors, and a few entities missing from the placement table. Two ADs are marked adopted although the memlog says they still need the user's confirmation.

## Findings (ordered by impact)

### F1 — No authorization model: roles, region scope and assignment scope are not architecture yet  (High)

**Inputs:** P2 (six roles); reqs "The Admin Site" (role definitions; Clinical Manager "no financial nor sales access"; Financial Manager "no access to clinical operations"; Commercial Regional Manager "restricted to their assigned region"; Clinical User only data tied to active assigned work orders, and in client files only contact data, maker access, comments and patient list, "financial information or support tickets info is skipped"); epic-admin-access DW1; epic-sales-finance DW1 (CRM sees only their region's clients and orders); epic-clinical-ops DW2 (Clinical User without financial data); epic-support; epic-appointments (my calendar / team calendar for the Clinical Manager only).

**What the spine says:** AD-4 only: remote-access tokens carry UUID, role and permitted regions, and the instance checks permission. Nothing about:
- **Central data.** The Commercial Regional Manager region restriction must also apply to data the admin site *stores* (client record copies, accounting copy, contact messages, voucher usage, sales reports, dashboards, CSV exports). AD-4 covers only remote access. Every central record therefore needs a `region` attribute and every admin query a region filter; otherwise each of epic-sales-finance, epic-admin-interface, epic-support and the dashboards will each apply (or forget) it.
- **Assignment scope.** "Clinical User sees only assigned work orders" needs the instance to know the assignee when it receives a remote-access call. AD-4's token carries "permitted regions", not assignments, and AD-2 does not say where the assignment lives (see F5). As written, the instance cannot enforce it.
- **Field-level scope.** Role-dependent *fields* (no financial data for clinical roles, no clinical data for finance/commercial roles) must be filtered in the instance's remote-access response, not by the admin template, or AD-4's "the instance checks the permission itself" is defeated by over-fetching.

**Change:** add an AD (e.g. AD-17 "Authorization") with: one role → area → action matrix defined once in mytp-core (both sides read the same definition); every central record carries `region`, and admin querysets go through one scoping helper (role, permitted regions); remote-access tokens carry role + regions and the instance resolves assignment-based scope from its own data; instance serializers filter fields per role. Update AD-4 to point at it, and add it to the P2, P10, P12, P13 rows of the capability map.

### F2 — Pricing/VAT input is the doctor's country, but the rules say the billing entity decides; one cart may span companies  (High)

**Inputs:** reqs BMAD answer "what really limits services and functionalities access is the country at the billing info"; reqs intro (EU B2B with a valid intra-community VAT number is VAT-exempt from another EU state; customers outside the EU buying from an EU company are exempt; VirtuaOrtho may be billed from Spain while TPS from Ireland for the same Portuguese customer); P4 billing rules per billing company × customer country × category; P5 VIES; P7 "gateway chosen by billing company"; initiative Notes (subscription VAT may be 0); epics catalogue, doctor-profile, payments, subscriptions, ordering.

**What the spine says:**
- AD-16: the resolver answers "for a doctor (country, home region, subscription)". Which country — profile, signup or billing — is not said, and the **VIES status of the chosen fiscal entity** is not an input, although it changes the VAT (0 % with exemption text vs the company's rate) for every intra-EU sale. Subscription default assignment uses the signup country (P6), product availability and VAT use the billing country: two different "countries" that epic-subscriptions and epic-ordering will otherwise pick differently.
- AD-6/AD-7: order lines snapshot their billing company; the gateway is per billing company. Nothing says what happens when one cart holds lines from two billing companies (or two currencies). A single hosted payment cannot pay two merchants; epic-payments and epic-ordering will each guess.

**Change:** AD-16 — the resolver's inputs are (selected fiscal entity: billing country + VIES-validated flag, home region, subscription, product category); the signup/profile country is used only for default-subscription assignment (say so explicitly). AD-6/AD-7 — add "an order has exactly one billing company, one currency and one gateway; checkout splits a cart into one order per billing company" (or the alternative the user prefers), and "VIES validation result and date are snapshotted on the order".

### F3 — Retention and deletion rules are missing (48 h / 7 days, traces, logical erasure, backups)  (High)

**Inputs:** P9 (patient deleted 48 h after marking; records 7 days after "uploaded to maker"); reqs Clinical/Patients (a trace with internal patient id, who marked, when; marking possible from the doctor *or* the admin site); reqs Work Order details (keep only file name, uploader, upload/processing/deletion dates); reqs status display (queued for deletion with date, deleted with effective date); reqs admin lists "Edit includes deactivation and logical erasure" (almost every admin entity); part-1 decision S4 (30-day backups); epics case-lifecycle DW3, clinical-ops DW3, platform DW5, admin-access, catalogue, sales-finance.

**What the spine says:** AD-9 "deleting a file deletes the object and keeps only its trace". No windows, no patient deletion, no definition of the trace, nothing on soft deletion of admin entities, nothing on backups or on derived copies (thumbnails/photos, search indexes, outbox payloads, access logs, the admin site's accounting copy).

**Change:** add an AD "Retention and deletion": (a) patient data — mark → physical delete after 48 h by a scheduled job, including photos and every object under the patient; records — 7 days after "uploaded to maker"; both triggered through the one service (AD-15) whether marked by doctor or staff; (b) the trace record's fields, fixed once; (c) outbox payloads and access logs reference patients by UUID/patient code only so they survive deletion without holding personal data; (d) backups of regional databases and buckets expire within a stated bound (30 days, part-1 S4) and a restore re-applies pending deletions; (e) admin-site master data uses logical erasure (status active / inactive / erased) and never physical delete — add the status vocabulary to the AD-11 fixed columns.

### F4 — Patient data can leave the region through processors and the admin browser, not just through the database  (High)

**Inputs:** initiative Notes (patient data stored only in its region; exchanges restricted, logged, encrypted; Zoom "with generic titles"); Done when 3; reqs "Regarding medical data"; part-1 stack (Sentry, SendGrid/SMTP, Twilio); P11/P9 automatic messages; P12 "accounting export with patient codes only"; AD-11 "every list … CSV export".

**What the spine says:** AD-2 forbids patient data in the admin site's database, cache, logs, index, files, backups; the logging convention forbids patient names in logs. It does not cover:
- **Error reporting:** Sentry events from the UAE (and EU) instance can carry request bodies, form values and local variables, i.e. patient names, to a Sentry region outside the UAE. Needs `send_default_pii=False`, a scrubber in mytp-core, and a Sentry data-region decision for UAE (epic-platform, epic-uae-launch, every epic).
- **Messages to doctors:** order emails (cart lists patient names), stage messages, appointment and support notifications pass through email/SMS/WhatsApp providers outside the region. The Zoom "generic titles" decision shows the user's intent; the messages convention should say the same: messages identify the case by patient code or a link, never by name or clinical content.
- **Admin browser and exports:** remote-access pages rendered by the admin site must send `Cache-Control: no-store`; AD-11's blanket CSV export, applied to the remote patients / work-order lists, writes patient names to a staff computer. The accepted gap in the Notes covers *records download* only. Either exclude names from those exports or record that the gap is extended, and log each export in the region.

**Change:** extend the AD-2 rule from "the admin site keeps no patient data" to "patient data leaves the region only as a live rendered response to an authorized staff user"; add to Consistency Conventions: Sentry/PII scrubbing, messages carry no patient names, `no-store` on remote-access responses; amend AD-11: CSV export of regional-only lists is generated by the instance, logged there, and follows the decided name rule.

### F5 — The placement table misses entities several epics share  (Medium)

**Inputs:** P10 (pre-assignment to the first free slot with balanced workload, firm after 24 h; manual assignment; team calendar); P11 (doctor requests slots from the assigned specialist's availability; my calendar); P6 (manually assigned special subscription stops self-service); P4/P8 (vouchers once per person; try-me once per doctor); epic-clinical-ops DW1, epic-appointments DW1/DW4, epic-subscriptions DW4, epic-sales-finance (voucher usage).

**What the spine says:** AD-2 puts *specialist calendars* at the admin site and *appointments* and *work orders* in the region, and "anything not in this table is regional-only". It does not place:
- **Work-order assignment / assignee** (and its pre-assignment state and 24 h deadline). The pre-assignment job needs every region's open work orders and every specialist's occupancy; the instance needs the assignee to enforce F1. Regional-only by default means the admin-side job has no data to balance on.
- **Calendar occupancy:** the admin calendar shows appointments, which are regional and patient-linked. The admin needs at least (specialist, start, end, region, work-order UUID) without patient identity to compute availability; the portal needs availability to offer 72 h-ahead slots.
- **A doctor's current subscription** (assigned by the instance at signup, changed manually by staff) and **voucher redemptions** (regional, but reported centrally for usage reports and single-use checks).

**Change:** add rows to AD-2: assignment (owner: regional work order, assignee = admin user UUID; central copy: assignment + slot occupancy without patient identity, via the outbox); appointment slots (central: time, specialist, region, work-order UUID; regional: everything else); doctor subscription (regional owner, central copy in the client record); voucher redemptions (regional owner, event to admin). State where the pre-assignment job runs (admin, writing through remote access per AD-15).

### F6 — AD-15 and AD-16 are marked [ADOPTED] but the memlog says they await the user's confirmation  (Medium, contradiction)

**Inputs:** `.memlog.md` last entry: "AD-15 one write path per entity and AD-16 one catalogue resolver: distilled from A1/A2 and the epic handoffs; to confirm with the user at review". Also the memlog question "OVH Object Storage presigned multipart … not confirmed; first story of epic-platform checks it (spike)", which AD-9 adopts without mention and Deferred omits.

**Change:** mark AD-15 and AD-16 **[PROPOSED]** until the user confirms (and AD-16 should be confirmed in its F2-corrected form); add the OVH multipart spike to Deferred / AD-9 as a precondition of the upload stories.

### F7 — "Admin hidden from robots and never linked" is not an architecture rule  (Medium)

**Inputs:** P2 ("never linked from the mytreatplan domains and hidden from robots"); reqs ("never accessible from the mytreatplan domains or detectable in any way by robots or scanners"; login page with branding and the support message only); epic-platform DW1 (staging only), epic-admin-access DW2, epic-eu-golive.

**What the spine says:** nothing beyond AD-4's IP allowlist on interface paths.

**Change:** add to AD-12 (or a conventions row): admin site serves `X-Robots-Tag: noindex, nofollow` and a disallow-all robots.txt in every environment, no server/framework banners, the login page as the only public route, no link or reference to the admin domain in portal pages, emails, CSP reports or JS bundles; the portal's config pull uses a non-guessable interface host or path. Note that certificate-transparency logs expose the hostname, so "undetectable" is limited to "unlinked and unindexed" (state it so the epics don't chase the impossible).

### F8 — The case stage machine has no single owner  (Medium)

**Inputs:** P9 stage list (parenthetical stages only for VirtuaOrtho and in-house, the new "pending appointment" stage); reqs "never two live work orders for one patient at the same time"; appointments only "in stages that allow it"; records upload only in certain stages; approval only in "pending approval"; the doctor sees "assigned" only once firm (clinical-ops DW1); epics case-lifecycle, clinical-ops, appointments, ordering, support.

**What the spine says:** AD-15 gives one write path, but the stage set, allowed transitions per product range, who may trigger each (doctor, staff, job) and which stages permit appointment / upload / approval are not defined anywhere shared.

**Change:** a convention or short AD: one stage-machine definition in the portal's cases app (stages, transitions by product range, actor per transition, stage guards for appointments/upload/approval, one live work order per patient), exposed read-only to the admin site through the contract; epics extend it only there.

## Smaller items (record or push to epics)

- **Field encryption scope:** the conventions say part-1 KMS envelope encryption for fields, but only maker logins are named. Decide whether patient names, comments and photos are field-encrypted (binds case-lifecycle, ordering, clinical-ops, support).
- **One person, two regional accounts:** the Notes say they are never cross-checked; the client record must therefore be keyed by (region, uid) and the admin site must not merge or dedupe clients (admin-interface, sales-finance).
- **Staff alerts:** "my messages and alerts" and "a notice at the admin site" need a source; state that staff alerts are driven by outbox events (admin-access, clinical-ops, support, sales-finance).
- **Every exchange logged (P3):** AD-4 logs remote access; say that config pulls and outbox deliveries are also logged with their event ids on both ends.
- **72 h rule:** computed in UTC against the stored slot start; single-epic, but the times convention should mention "minimum lead time is evaluated in UTC" so the portal and the admin calendar agree.
- **Workshops for another region's doctor get a manual invoice** (Notes) conflicts in spirit with AD-7's "series per instance"; education is parked, so record it as a known future exception in Deferred.

## Checked and found consistent

Hub-and-spoke placement and the client record central (memlog), outbox / config pull / remote access (A2), Ed25519 trust (A3), UUIDs and patient code (A4), Money and net prices (B1), jobs (B2), object storage and scan (B3), mytp-core (C1), AdminLTE/HTMX/tables2 (C2), deployment (C3), times / texts / messages / versioned API (C4), region profile, country map and home region, geo-IP local, single session in mytp-core, i18n languages per region and English fallback, currencies EUR / AED / USD, admin on its own production instance.
