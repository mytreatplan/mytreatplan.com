---
type: architecture-review
lens: security and compliance (EU GDPR, UAE health-data residency)
target: ../architecture-portal-admin.md (draft, 2026-10-29)
reviewed: '2026-10-29'
inputs:
  - architecture-portal-admin.md (AD-1 to AD-16, conventions, deferred)
  - initiative-mytreatplan-portal-admin.md (Notes: lawyer and user decisions)
  - part-1 code at 591150e: accounts/crypto.py, accounts/sessions.py, accounts/devices.py, accounts/erasure.py, eventlog/retention.py, mytreatplan/sentry.py, mytreatplan/settings.py, mytp_publicsite/management/commands/backupdb.py, deploy/nginx/mytp.conf.template, deploy/jobs.conf, deploy/README.md
verdict: 'Sound shape (hub never stores patient data, regional owners, signed interface); not yet safe to build on: an epic can obey every AD and still replay tokens, persist patient data on the admin site, and ship UAE patient data out of the UAE through part-1 plumbing.'
---

# Security review — architecture spine, portal + admin site

## Verdict

**Approve the paradigm, change before epic 1 starts.** The hub-and-spoke split, "the admin site never stores patient data" (AD-2), one write path (AD-15), the patient code (AD-5) and the access log in the region (AD-4) are the right foundations. The gaps are in the details an epic will fill in by itself: AD-4 does not say what a token must contain or how it is checked; AD-2/AD-3 forbid storing patient data centrally but name none of the places it leaks into by accident; and AD-12/AD-13 inherit part-1 infrastructure (OVH KMS in GRA, off-region backups to SBG, Sentry in Germany, restore test on a team Mac) that breaks UAE residency as soon as `REGION=ae` runs on it.

Severity: **High** = an epic can obey every AD and still cause a breach or a residency violation; **Medium** = likely gap or rework; **Low** = hardening.

| # | Severity | Finding | AD to change |
| --- | --- | --- | --- |
| F1 | High | Interface tokens: no audience, expiry limit, replay rule, request binding, key id or region-to-key binding; config snapshots are not signed and can be rolled back | AD-4 (and AD-3 for snapshots) |
| F2 | High | "The admin site keeps no patient data" has no enforcement list: flash messages, alerts, task arguments, calendars, query-string search in Nginx logs, browser cache, CSV export, Sentry free text | AD-2, AD-3, AD-11; new convention row |
| F3 | High | UAE residency is broken by inherited plumbing: EU KMS, off-region backup to SBG, restore on a team Mac, Sentry DE, email/SMS content, and the EU admin server rendering UAE patient data | AD-12, AD-13, Deferred (UAE host requirements); lawyer sign-off |
| F4 | Medium-High | Field encryption list for new regional tables is not stated; uploaded file names carry patient names into object keys, logs and the "trace" kept after deletion | AD-2 (new column), AD-9 |
| F5 | Medium | Erasure vs retention unresolved: immutable invoices with patient names vs the 48 h patient deletion; outbox and received-event tables keep payloads forever; doctor erasure does not propagate to the central client record | AD-7, AD-3, AD-9 |
| F6 | Medium | Admin site exposure and sign-in: 90-day remembered device reused for staff, no network restriction on the admin UI, robots rule only in the requirement; gateway webhook verification wholly deferred | AD-4/AD-10 (admin profile), AD-11, new AD for gateways (or AD-6/AD-7) |
| F7 | Medium | Presigned URL lifetimes ("minutes") and bucket hardening (SSE, versioning, multipart cleanup, quarantine enforced by policy) unspecified | AD-9 |
| F8 | Low | Signing private keys stored in plain settings.env while field keys are KMS-wrapped; staging and production keys not separated by rule | AD-4, conventions (Config and secrets) |

---

## F1 — Ed25519 tokens: replay, audience, expiry, rotation, revocation (High; AD-4, AD-3)

**What AD-4 says:** short-lived token signed with the sender's Ed25519 key; public keys only; Nginx IP allowlist on interface paths; remote-access tokens carry admin user UUID, role, permitted regions; the instance checks permission and logs.

**Gaps an epic can fall into while obeying it:**

1. **No audience.** A token minted for `dev-ae` is valid at `dev` (and, if a staging public key is ever pasted into production settings.env, at production). The three staging sites share one host and therefore one source IP, so the Nginx allowlist does not separate them.
2. **No upper bound on "short-lived"** and no clock-skew rule; one epic picks 30 s, another 1 h.
3. **No replay rule.** A captured remote-access token (proxy log, Sentry breadcrumb, debug print) can be replayed against any endpoint until it expires, including writes. Nothing binds a token to one request.
4. **No request binding.** A token for `GET /api/v1/patients/<uuid>` is equally valid for `POST /api/v1/cases/<uuid>/approve`.
5. **No key id.** Without `kid` a rotation needs a flag day across three sites; with "public keys in settings.env typed by a person" a rotation will be postponed.
6. **No revocation procedure.** If the admin site's private key leaks, the attacker holds every role in every region (the instance trusts the role claim). The spine does not say how fast, or how, each instance stops trusting it.
7. **No region-to-key binding on the outbox.** The admin site must reject an event whose envelope `region` is `ae` but which is signed with the EU key; otherwise a compromised EU instance can write UAE accounting records centrally.
8. **Config snapshots are not signed as documents.** AD-3 says the instance pulls a snapshot; the token authenticates the request, but the snapshot content (country map, banned states, prices, VAT rates, gateway per billing company) is not signed and has no anti-rollback rule. An older snapshot replayed or served by a misconfigured proxy re-opens a banned country or applies old VAT.
9. **Role claim is the whole authorisation.** "The instance checks the permission itself" only checks the role the admin site asserts. That is acceptable (the hub is the identity owner) but needs compensating controls at the instance: per-admin-user rate limits and a volume alert (e.g. more than N patient reads per hour), so a hub compromise is noisy.

**Change to AD-4 (proposed text):**

> Tokens are compact JWS (EdDSA) or an equivalent mytp-core format with: `iss` (site id), `aud` (the exact HTTPS origin of the receiving site), `iat`, `exp` ≤ 60 s after `iat` (clock skew 30 s; hosts run NTP), `jti` (UUID), `kid`, and for requests `htm` + `htu` (method and path) + `bh` (SHA-256 of the body). The receiver rejects a token whose `aud` is not its own origin, whose `jti` it has seen within `exp + skew` (kept in the database cache), or whose method/path/body hash does not match. Remote-access tokens add `sub` (admin user UUID), `role`, `regions`, and `sid` (admin session id, for the access log). Each site accepts a **list** of peer public keys by `kid` (current + next); rotation is: add next key everywhere → switch signing → remove old key, at least yearly and at once after suspected compromise, with a runbook target of under 1 hour to revoke the admin key at every instance. Outbox events: the receiver checks that the envelope `region` matches the `kid`'s region. The instance rate-limits remote access per `sub` and alerts on volume.

**Change to AD-3 (Configuration):** the snapshot is a signed document (detached Ed25519 signature over the canonical JSON, with `version` and `region`); the instance verifies it before applying and refuses a version lower than or equal to the one applied. The nudge carries no data.

---

## F2 — Where patient data leaks into the admin site despite AD-2 (High; AD-2, AD-3, AD-11)

AD-2 says the admin site keeps no patient data "in its database, cache, logs, search index, files or backups"; AD-3 says remote access "renders the response without persisting it". Both are correct, but an epic following normal Django habits will persist patient data in these places without noticing:

| Leak path | How it happens | Rule needed |
| --- | --- | --- |
| Django messages framework | `messages.success(request, f"Patient {name} updated")` → stored in the session (admin DB `django_session`) or a cookie | Messages and confirmations on the admin site name patients by code only |
| Admin "my messages and alerts" (P2) | "New appointment request for Juan Pérez" stored as an alert row | Alerts/messages reference work order UUID and patient code only |
| Specialist calendars, team calendar (AD-2: owned by admin) | An appointment slot titled with the patient's name | Calendar entries store work order UUID + patient code; names are fetched live when shown |
| `django.tasks` task arguments (AD-8) | A task enqueued on the admin site with a remote response as argument (e.g. "build CSV export") lands in the admin DB task table | Admin-site tasks take UUIDs only; never a remote payload |
| Django cache / template fragment cache | `{% cache %}` around a patient block, or caching a remote API response to speed up fan-out search (DatabaseCache = admin DB) | No cache of remote-access responses in any backend; a check (lint/test) that remote-access client results are never passed to `cache.set` |
| Nginx access log and browser history | django-filter search uses GET (`?q=juan+perez`); AD-11 lists are GET-filtered; admin-side fan-out search on patient name | Patient-name search forms POST (or names only in the body to the instance); Nginx admin access log format omits `$args`, or the query string is never a name |
| Browser and proxy caches | Pages with patient data cached by the browser (back button, shared staff PC) | Remote-access views send `Cache-Control: no-store`, and the admin site sends `Cache-Control: no-store` by default on every authenticated page |
| CSV export (AD-11) | "Every list has CSV export" — a remote patient list exported to a staff laptop is the same accepted gap as records download, unlogged | CSV export of regional-only lists is off by default, permission-gated, and logged in the region as an access |
| Sentry | `mytreatplan/sentry.py` redacts only emails, IPs and phone numbers by regex; a `ValidationError`/`KeyError`/`JSONDecodeError` value containing a name, or a log line used as a breadcrumb, passes through | Admin site: exception values and log messages from the remote-access client and views are replaced by their type (deny by default); logging breadcrumbs off for those loggers; HTTP breadcrumbs keep path only (already) |
| Django `LogEntry` / admin action log | `object_repr` of a remote object | Action log stores UUID/code only |
| Access log at the region (AD-4 "what") | "what" written as a free-text description with the name | "What" = endpoint + entity UUID/patient code |

**Change:** add to AD-2 a short normative list: "In the admin site, patient data exists only in the response being rendered. It never enters: sessions or flash messages, alerts, calendar entries, task arguments, any cache backend, query strings, LogEntry/object_repr, Sentry event text, CSV files on the server. Remote-access pages send `Cache-Control: no-store`." Add a row to Consistency Conventions ("Patient references outside the region: patient code or UUID, never a name") and a CI test in the admin repo that fails on `cache.set`/`messages.*`/task enqueue fed from the remote-access client. AD-11: CSV export excluded for regional-only lists unless the epic makes it a logged, permission-gated download.

Note also the outbox (AD-3): each epic adds its own events. Without a contract-level guard an epic will put `patient_name` in a payload. **Add to AD-10/AD-3:** the contract definitions in mytp-core allow only declared fields, and a contract test fails if any event schema contains a field tagged as patient data (name, surname, photo, comment, file name).

---

## F3 — UAE data residency: inherited plumbing and the EU hub (High; AD-12, AD-13, Deferred)

The UAE health-data law (Federal Law No. 2 of 2019 on ICT in health fields, Art. 13, and its implementing decisions) restricts storing **and processing** health data outside the UAE except under ministerial exceptions. The lawyer decision says "stored only in its region and accessed remotely by staff". Several paths in the spine and in part-1 code contradict this for `REGION=ae` while obeying every AD:

1. **KMS.** `accounts/crypto.py` `KMSKeyProvider` uses the OVHcloud KMS (GRA). If the UAE provider has no KMS, the obvious move is to point the UAE instance at the GRA KMS: the keys protecting UAE patient fields are then controlled from the EU. **The Deferred UAE host requirement must add: a KMS (or HSM) in the UAE**, or a documented alternative key provider in mytp-core.
2. **Backups.** `backupdb` sends an age-encrypted dump "off-region" to OVH SBG (deploy/README.md §10). Copied as is, UAE patient data goes to France. Rule: the backup bucket of a region is in that region (second UAE location / second provider zone); the profile (AD-13) names it. Encryption with age is good but does not lift residency.
3. **Restore test on a team Mac** (`restore-db.sh`, deploy/README.md). Every restore test downloads the whole regional database to a laptop. Field encryption protects the encrypted columns, but work orders, stages, patient codes and anything not field-encrypted is readable. For UAE: restore tests run on a UAE VM, never a laptop; for EU: a laptop restore of a database with patient data is itself a records-download gap and should run on a throwaway OVH instance.
4. **Sentry.** Both sites send to Sentry DE (`*.de.sentry.io`). For the UAE instance that is a transfer of whatever the scrubber misses (F2). Options: a UAE-only Sentry project with exception values and messages dropped entirely (type + stack only), or lawyer acceptance.
5. **Messages to doctors** ("one notification service", email first, SMS fallback): the SMTP service and Twilio are outside the UAE. If templates name the patient ("Case for Juan Pérez is pending review"), patient data leaves the region in every message. Rule: messages to doctors name the patient code and link to the case page; the name appears only after login.
6. **The admin server renders UAE patient data in the EU.** AD-3 remote access: the admin site (OVH EU) calls the UAE instance and renders the response server-side. Transient, but it is processing in the EU, plus the Sentry/log/memory exposure of F2. The lawyer accepted remote access by staff; confirm explicitly that hub-side processing in the EU is covered. If not, the alternative that keeps AD-1 intact: the admin page embeds region-served fragments (the staff browser fetches directly from `mytreatplan.ae` with a short-lived, audience-bound token minted by the admin site, F1), so UAE patient data never passes through an EU server.
7. **Records downloads outside the region (accepted gap).** For EU data this is a GDPR risk accepted by the controller; for UAE data it may be unlawful without a ministerial exception. Mitigations to require now, not later: download only by users assigned to the work order (P10), each download logged in the region (AD-9 already), short URL lifetime (F7), staff devices with full-disk encryption and a deletion rule after "uploaded to maker", and a record in the DPIA/RoPA. **Gate epic-uae-launch on the lawyer's written answer** for UAE downloads, or on the in-region remote desktop.

**Change:** AD-13's region profile also sets: KMS endpoint/provider, backup bucket and location, Sentry DSN (per region), and notification templates' patient-reference rule. AD-12: "every store of regional data (database, object storage, backups, restore targets, key management, error reporting) is in the region; off-region means another location within the same jurisdiction". Deferred: UAE host must offer KMS/HSM and a second in-country storage location. Add an explicit open question in the initiative Notes for the lawyer on items 4, 6 and 7.

---

## F4 — Field encryption for new regional tables; file names (Medium-High; AD-2, AD-9)

The convention says "part-1 KMS envelope encryption for fields" but no AD says which new fields use it, so each epic decides. Part 1 encrypts personal fields with AES-256-GCM (associated data `<app>.<model>.<field>`) and finds email/phone through an HMAC blind index — good machinery to reuse.

**Proposed: add a column "Field-encrypted" to the AD-2 table**, minimum:

| Entity | Encrypted fields |
| --- | --- |
| Patient | first name, surname, any free-text identity, date of birth if added; photo is an object (SSE, F7) with a UUID key |
| Patient comments, case notes, appointment notes | text |
| Maker logins | username **and** password; reveal only through a service that logs (P10); consider a separate data key id so it can be rotated alone |
| Support tickets | subject and description (doctors will name patients in them) |
| Contact messages (also copied centrally) | message text, name, email, phone — encrypted on the admin site too |
| Fiscal entities, shipping addresses | as part-1 personal fields |
| File records | original file name (see below) |
| Admin site client record copy | the same personal fields as the regional account (the admin site needs its own KMS key; AD-10 shares code, not keys) |

**Search by patient name:** do not add a blind index on names (low entropy; enumerable by anyone holding the index key). Doctor-side search decrypts the doctor's own patients in memory (small sets); admin-side search uses patient code, doctor, or work order.

**File names.** Doctors name uploads after the patient (`Juan_Perez_CBCT.zip`). AD-9 keeps "only its trace (name, who, when)" after deletion — i.e. the patient's name survives the patient's erasure. And if the object key is built from the file name, the name lands in S3 access logs, Nginx logs and Sentry. **Change AD-9:** object keys are `<prefix>/<uuid>`; the original file name is stored field-encrypted and served only through `Content-Disposition`; the deletion trace keeps the file UUID, size, hash, who and when — not the name; the name is erased with the patient.

---

## F5 — Erasure vs retention (Medium; AD-7, AD-3, AD-9)

1. **Invoices vs the 48 h patient deletion.** AD-7: the doctor's invoice shows patient names and issued invoices are immutable; P9: a patient is deleted 48 h after marking. Either the deletion breaks an immutable invoice, or the name stays for the tax retention period (ES 6 y, IE 6 y, UAE 5 y). Neither tax law requires the patient name on the invoice. **Change AD-7:** the issued invoice record stores the patient code; the doctor's view and PDF are rendered with the name joined live while the patient exists, and with the code after erasure. The invoice stays immutable in every legally relevant field.
2. **Accounting copy.** Patient codes are pseudonymous personal data under GDPR while the region can re-identify them; once the patient is erased in the region the code is effectively anonymous centrally. State this in AD-2/AD-5 (it is the argument that lets the accounting copy outlive erasure) and keep a tombstone of used codes (no personal data) so "never reused" holds after deletion.
3. **Doctor erasure.** Part-1 `accounts/erasure.py` handles erasure inside one site. In part 2 the doctor's data is also in the admin client record. **Add an event** `client.erased` (AD-3 catalogue) that makes the admin site clear the client record except the billing data kept under the accounting legal basis, with its own retention date.
4. **Outbox and inbox retention.** Delivered outbox rows and the admin site's deduplication store keep full payloads (doctor data, accounting data) forever. Rule: an outbox row's payload is cleared after acknowledgement + N days; the admin dedup store keeps only `event_id` and receipt time after processing.
5. **Logs and backups.** The region's access log and maker-login reveal log need a stated retention (part 1 uses 5 years for activity records); 30-day backups keep erased data until expiry (already accepted in part 1, should be repeated for patient data and object-storage versions, F7).

---

## F6 — Admin site exposure, sign-in and gateway webhooks (Medium; AD-4/AD-10, AD-11, new AD)

**Admin sign-in.** AD-10 reuses part-1 2FA "including remember-device"; `accounts/devices.py` remembers a device for 90 days. For staff who can read every region's patients and reveal maker logins, that is too long. **Change:** an admin profile for the 2FA blocks: remember-device off (or ≤ 12 h), idle limit ≤ 15 min (part-1 `IDLE_LIMIT` is 15 min — keep it, do not relax it), re-authentication (fresh 2FA) before revealing a maker login, downloading records, exporting, or managing admin users; login rate limit and lockout as part 1.

**Exposure.** AD-4 restricts only interface paths by IP; the admin UI is open to the internet, and `admin.mytpdso.com` is public in certificate-transparency logs regardless of robots. Proposed for AD-11 or a new AD: the admin UI is reachable only from an allowlist (office IPs / VPN) or behind a client certificate or identity-aware proxy; if staff must log in from anywhere, then 2FA without remember-device is mandatory and the login page is rate-limited per IP and per account. Also: `robots.txt` with `Disallow: /`, `X-Robots-Tag: noindex, nofollow` on every response (as part-1 staging Nginx already does), a strict Content-Security-Policy (AdminLTE + HTMX, no inline script), `X-Frame-Options: DENY`, HSTS with a real value in production, no link from the mytreatplan domains (requirement P2, not yet in any AD).

**Gateway webhooks** are entirely in Deferred. The details may wait; the security rules may not, because each adapter will otherwise decide them. **Add (AD-6/AD-7 or a new AD-17):**

- Every webhook/notification is verified before any effect: Redsys `Ds_Signature` (HMAC-SHA256 with the per-order derived key), Revolut webhook signature with timestamp tolerance (≤ 5 min).
- The webhook is a trigger, not the truth: the handler re-reads the payment status from the gateway API and checks amount and currency (AD-6) against the order before marking it paid.
- Idempotent on the gateway's event/transaction id (AD-8 style).
- Webhook paths are exempt from the interface IP allowlist but rate-limited, CSRF-exempt only on those paths, and log verification failures in the region without the payload.
- Gateway secrets per billing company per region in settings.env; no card data ever touches the instance (SAQ A, hosted pages only — keep the P7 rule as an AD so an epic cannot add an embedded card field).

---

## F7 — Presigned URLs and bucket hardening (Medium; AD-9)

AD-9 says downloads are presigned GET URLs "valid for minutes"; the upload side has no lifetime at all.

**Proposed values for AD-9:**

- GET: ≤ 5 minutes, issued on click through a region endpoint that checks permission, logs, and redirects (never pre-rendered into list pages, emails or messages); `ResponseContentDisposition: attachment` with a code-based file name.
- Multipart part PUT URLs: ≤ 60 minutes each, issued in batches on demand by Uppy for an upload id created server-side under the quarantine prefix with a server-chosen UUID key; the size limit is checked at completion.
- `Referrer-Policy: same-origin` (Django default) kept on both sites so presigned URLs do not leak through Referer.
- Bucket: private, public ACLs blocked, CORS allowed only from the region's portal origin (and not the admin origin if downloads redirect through the region), server-side encryption on (provider-managed key at minimum; the UAE provider must support it), a lifecycle rule that aborts incomplete multipart uploads after 1–2 days (otherwise orphaned parts of patient records stay forever) and expires rejected quarantine objects.
- "Nothing in quarantine is downloadable" enforced by bucket/IAM policy (the signing credentials cannot read the quarantine prefix), not only by code.
- Versioning: if enabled, "deleting a file deletes the object" is false until the old versions expire; state the rule (versioning off, or a noncurrent-version expiry ≤ 30 days aligned with backups).
- The S3 credentials of the portal are scoped to its own bucket; the admin site has no object-storage credentials for any region.

---

## F8 — Signing keys and environment separation (Low; AD-4, conventions)

- Part 1 wraps field and blind-index keys through the KMS; AD-4's Ed25519 private key would sit in plain text in settings.env. Wrap it the same way (mytp-core key provider), or at least: file mode 0600 owned by the site user, never in the repo, never logged, `kmscheck`-style daily check that the key loads.
- Staging and production keys are distinct and production never lists a staging public key; `aud` (F1) enforces it in code.
- Staging `dev-ae` and `dev` use synthetic data only; a rule that no production dump is ever restored to staging.

---

## What is already right (keep)

- AD-1/AD-2: the hub never stores patient data and never touches an instance database.
- AD-4: asymmetric keys, no shared secrets, the access log written where the data lives.
- AD-5: random patient code, not derived from patient data, 40 bits of entropy, never reused.
- AD-9: quarantine + ClamAV before any download; direct-to-bucket upload keeps files off the web server.
- AD-15: one service per change, so admin-side changes leave the same events and logs.
- Part-1 Sentry setup: `send_default_pii=False`, no request bodies, no local variables, query strings stripped — a good base, but regex redaction cannot catch names (F2).

## Suggested order

F1 and F2 before epic-admin-interface starts (they shape the mytp-core contract and token code). F3 before epic-platform provisions regional storage and before any UAE work; send the lawyer questions now. F4 and F7 before epic-platform/epic-case-lifecycle create the first patient and file tables. F5 at epic-ordering inception (invoice model). F6 at epic-admin-access inception (sign-in profile, exposure) and epic-payments inception (webhooks).
