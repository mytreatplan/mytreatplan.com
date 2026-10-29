# Review — architecture-portal-admin.md against the good-spine checklist

Reviewed: `architecture-portal-admin.md` (status draft, 2026-10-29), initiative altitude, against the initiative file, the 16 epic files, and the part-1 codebase at master 591150e (`deploy/`, `mytreatplan/settings.py`, `mytreatplan/sentry.py`, `accounts/crypto.py`, `eventlog/`, `.github/workflows/ci.yml`, `requirements.txt`).

## Verdict

**Revise before epic-platform starts.** The product-side core is good: data placement (AD-1/2), the four channels (AD-3), ids and patient codes (AD-5), money and invoices (AD-6/7), the region profile (AD-13) and the resolver (AD-16) are clear, enforceable, and close real divergence points. The gaps are mostly in the operational envelope, which this altitude owns and which the spine barely mentions (backups, key management per site, monitoring, DR, version skew, CD for three repos). AD-12 also describes the deploy tooling as already multi-site when the brownfield code is single-site, and one cross-site channel (instance reads of admin-owned live data) is missing.

## Checklist results

| Check | Result |
| --- | --- |
| Fixes the real divergence points, misses none | Partly. Misses: instance→admin live reads and slot reservation (F5), outbox ordering and poison handling, remote-access write idempotency (F6), audit-log write semantics (F7), version skew (F3) |
| Every AD's rule is enforceable and prevents its divergence | Mostly. AD-4's region access log has no fail-closed rule (F7). The paradigm's "writes through service.py" has no rule for part-1 apps (F9) |
| Nothing under Deferred lets two units diverge | Pass, with one note. Each deferred item is either single-owner (contract format, gateway adapter, task backend) or bounded by an AD. The UAE-provider constraint list should add "a KMS or equivalent in the UAE" (F1) |
| Named tech plausible and current | Pass. The versions match `requirements.txt` (Django 6.1.1, psycopg 3.3.6, cryptography 50.0.2, gunicorn 26.2.0, sentry-sdk 2.71.0) and CI (`postgres:17`, Python 3.13). Note: Django's `django.tasks` ships only the Immediate and Dummy backends, so a PostgreSQL worker means a third-party backend (e.g. the `django-tasks` package). The deferral is right; say so in the Stack row |
| Ratifies rather than contradicts the brownfield | Partly. Contradictions or unstated changes: AD-12 vs `provision.sh`/`release.sh`/CI (F4), the part-1 off-region backup vs AD-2 for the UAE (F1), extracting `accounts/crypto.py` into mytp-core (F8), the service-layer claim (F9) |
| Every dimension the altitude owns is decided, deferred or an open question | **Fail** for the operational envelope (F1–F4). Decided: deployment topology (AD-12), scheduled jobs (AD-8). Undecided and not listed anywhere: backups per site, key management per site, monitoring and alerting, DR (RPO/RTO), migrations and version skew between admin, mytp-core and two regions, production CD for three repos |

## Findings

### F1 — Critical — Operational envelope undecided; part-1 backup design breaks AD-2 in the UAE (new AD-17 "Operations per site", AD-12, Deferred)

The spine says nothing about backups, key management, monitoring or DR, and none of them is listed as deferred or open. Concrete conflicts with the brownfield:

- **Backups.** Part 1's `backupdb` (deploy/README.md §10, "Backups and restore") sends a nightly `age`-encrypted `pg_dump` to Object Storage **SBG (France)** as an *off-region* copy, and the quarterly restore test restores it **on a team Mac**. If the UAE instance copies this, the UAE patient database leaves the UAE, which breaks AD-2 and the lawyer's decision ("patient data stored only in its region"). Nothing covers backups of **regional object storage** (records, STL files: AD-9) or of the **admin site** (which holds doctor personal data and the accounting copy). AD-2 says the admin keeps no patient data in its backups, but no rule says how that is verified.
- **Key management per site.** Field keys are wrapped by the **OVHcloud KMS (France)** (`accounts/crypto.py` `KMSKeyProvider`, README "Keys (KMS)"). The spine does not say which KMS the UAE instance uses, or whether a UAE provider must offer one (the UAE-provider entry under Deferred lists VM, PostgreSQL and S3 only). It also does not say where each site's Ed25519 private key (AD-4) lives (settings.env? KMS-wrapped?), how it rotates, or how peers learn a new public key (key id in the token, overlap window).
- **Monitoring and alerting.** Part 1 uses Sentry (EU, `*.de.sentry.io`), Sentry cron check-ins and UptimeRobot. Undecided: whether the UAE instance reports to the EU Sentry org, one project per site or per repo, and the interface-specific alerts this design needs: outbox backlog age, the age of the last applied config snapshot, failed signature checks (AD-4), and a stuck scan quarantine (AD-9). epic-eu-golive's done-when #1 ("both with backups, monitoring and alerts") currently has nothing to build against.
- **DR.** There is no RPO/RTO per site, no runbook ownership, and no answer to "admin site lost" beyond AD-3's "keep working on the last snapshot". The outbox has no stated retention or cap while the admin is down.

**Change:** add an AD that fixes, per site, the backup layers and their location (in-region for UAE, including object storage; the restore test runs in-region or on scrubbed data), the KMS per region, the signing-key custody and rotation, the Sentry/uptime layout and the four interface alerts, and RPO/RTO. Or list each of these explicitly under Deferred, with an owner epic (epic-platform, epic-eu-golive, epic-uae-launch) and the constraints that bind it. Add "KMS in the UAE" to the UAE-provider constraints.

### F2 — High — Restoring from a backup can duplicate invoice numbers (AD-7 together with F1)

AD-7 makes invoice numbers gapless and assigned at issue time, and the outbox has already sent issued invoices to the admin's accounting copy. If an instance is restored from a nightly dump (part-1 RPO is up to 24 h), the series rewinds. The next invoices then reuse numbers already issued to customers and already held centrally with different content. Legally that is worse than a gap. The admin's dedup-on-event-UUID does not catch it, because these are new events with old numbers.

**Change:** in AD-7 (or the new ops AD), require point-in-time recovery for regional databases and a post-restore step: every series resumes from max(local, the highest number in the admin's accounting copy), and the restored instance's outbox is reconciled against what the admin acknowledged.

### F3 — High — Version skew between admin, mytp-core and two regions has no rule (AD-3, AD-10, Conventions "Interface envelope")

The only rule is "a breaking change is a new `/api/v<n>/`, served alongside the old until every region upgrades". Missing:

- **Deploy order.** For an additive contract change, which side deploys first (admin before instances for events, instances before admin for remote-access endpoints?).
- **Tolerance.** Do readers ignore unknown fields and unknown event types? Without this, one epic will define strict contract models (pydantic `extra="forbid"`) and another lenient ones, and the outbox will retry a rejected event forever.
- **Config snapshot schema.** AD-3 versions snapshot *content*. Nothing says what an older instance does with a snapshot that carries a field or product category its code does not understand: apply it, refuse it (stay on the last one and alert), or ask for a downlevel format.
- **mytp-core skew.** It is pinned by tag separately in each repo, and the two regions may run different portal builds (EU first, then UAE). A rule is needed: the contract in mytp-core changes only additively within an API version, and each side reports its mytp-core and contract version in the envelope or the token, so a mismatch is visible.
- **Migrations across instances.** `release.sh` runs `migrate` per site during each release. Nothing says that data migrations must not depend on the admin site or the snapshot, or how a migration that changes an interface payload is sequenced.

**Change:** add a "Compatibility and rollout" rule to AD-3/AD-10 covering these five points.

### F4 — High — AD-12 does not ratify the brownfield deploy tooling, and CD for three repos is undecided (AD-12, Structural Seed)

AD-12 says "`provision.sh` takes a site name and gives each site its own Linux user, folder, socket, systemd units, database and job list" as if that were true today. In fact `deploy/provision.sh` takes `<domain> <htpasswd-user>` and hard-codes `APP_USER=mytp`, `APP_HOME=/srv/mytp`, `mytp-gunicorn.socket`, `mytp-job@<name>.timer`, `/etc/nginx/sites-available/mytp.conf`, the `mytp.htpasswd` file and `sudoers/mytp-deploy`. `release.sh` hard-codes `SOCKET=/run/mytp-gunicorn.sock`, and `ci.yml` copies to `/srv/mytp/incoming`. The Sentry cron monitor slug `mytp-<environment>-<name>` (jobs.conf) would collide when two portal sites share a host and an environment name. This is a real change for epic-platform, so state it as one and fix the naming scheme so it cannot drift: site name → user, path, unit prefix, nginx file, and Sentry monitor prefix.

Also undecided:

- Where the admin repo's deploy tooling comes from (a copy of `deploy/`, which will drift, or shared from the portal repo or mytp-core).
- How the `dev` and `dev-ae` sites both deploy from one CI run.
- Production CD. Part 1 has `deploy-staging` only. The spine does not say what triggers a production deploy (tag, manual approval), how the regions are ordered, or whether the UAE provider is reachable by the same SSH-based job.
- How mytp-core is tested and released (its own CI, tag policy, who bumps the pins).

### F5 — High — Missing channel: instance reads of admin-owned live data, and who reserves a specialist slot (AD-2, AD-3)

epic-appointments done-when #1: "a doctor sees only slots at least 72 h ahead from the assigned specialist". Specialist calendars and availability are admin-owned (AD-2), while appointments are regional. AD-3's channels let the admin read instances live, but give instances only the scheduled config pull from the admin. So epic-appointments must either put availability into the snapshot (stale, and not part of the snapshot's listed contents) or invent an instance→admin live call (a fifth channel AD-3 forbids by omission). One specialist serves both regions, so two regions can book the same slot, and no AD names the owner of the reservation. epic-clinical-ops's pre-assignment ("first free slot, lightest load") has the same question: it reads calendars centrally and workload regionally.

**Change:** decide the owner of slot reservation (most naturally the admin site, with the regional appointment as a copy keyed by a reservation UUID), and either add an instance→admin query channel to AD-3 under the same AD-4 trust rules or put availability in a frequently pulled feed with a stated staleness bound.

### F6 — Medium — Outbox and remote-access write semantics are underspecified (AD-3, AD-15)

- **Ordering.** At-least-once with UUID dedup does not say whether delivery is ordered per entity or per region. The accounting feed needs it (an invoice before its credit note; `order.paid` before `order.cancelled`), and the admin cannot detect a missing event without a per-region sequence number.
- **Poison events.** If the admin rejects an event (validation error, unknown version), does delivery block behind it or skip it and park it? This decides whether one bad event stops the accounting feed for a region.
- **Retention.** How long are acknowledged and unacknowledged outbox rows kept?
- **Remote-access writes.** AD-8 makes tasks idempotent, but a remote-access POST that times out and is retried by the admin can apply twice (a stage change and its message sent twice). Require an idempotency key (a request UUID) on every remote-access write, deduplicated in the region.

### F7 — Medium — AD-4's region access log needs fail-closed semantics and tighter token rules (AD-4)

Part 1's `eventlog.service` is deliberately best-effort: "Logging never breaks the flow it logs"; failures are caught and only reported to Sentry. If epics reuse it for remote-access and maker-login-reveal logs (P3: "every access logged"; P10: "every view logged"), a failed log write still returns the data. The rule should say that an access-log write is part of the same transaction as the read or write it records, and that no log means no data. On tokens: bind each token to its audience (the target site or region) as well as the permitted-regions claim, carry a key id for rotation, and state replay protection (`jti` with a short cache, or the body hash signed for outbox pushes).

### F8 — Medium — Extracting part-1 code into mytp-core and "no patient data in admin logs" need explicit rules (AD-10, AD-2)

- `accounts/crypto.py` imports `mytp_publicsite.kms` and reads part-1 setting names, and eight part-1 migrations reference `accounts.crypto.*` field classes (accounts 0003/0004/0006, signup 0002/0003/0006, eventlog 0001, mytp_publicsite 0001). AD-10 should require that `accounts.crypto` stays importable as a re-export shim, that the ciphertext format (`enc:v1:<key_id>:…`) and key ids are unchanged, and that the KMS client moves with it.
- Add the Sentry scrubber (`mytreatplan/sentry.py`: no PII, no request bodies, no local variables, query strings stripped) to mytp-core's list. The admin site renders patient data from remote access, so without the scrubber that data reaches Sentry.
- AD-2's "no patient data in logs" also needs a rule for the admin's Nginx and Gunicorn access logs: remote-access searches by patient name must not put the name in a URL query string. Either use POST bodies for searches or turn off query-string logging on admin interface paths.

### F9 — Medium — The service-layer claim does not match part 1 (Design Paradigm, AD-15)

"Every write goes through that app's `service.py` (as `eventlog/service.py` does today)": `eventlog` is the only app with a `service.py`. `accounts` and `signup` write from `views.py`, `forms.py`, `steps.py`, `erasure.py` and similar modules. Also, `eventlog.service` is a best-effort logger rather than a domain write service. State the rule for part-1 apps: grandfathered until touched, or refactored when a part-2 epic adds an interface adapter over them (accounts and doctor profile will be, through `client.updated`). Then AD-15 has a concrete target for the doctor entity.

### F10 — Low — Housekeeping

- The frontmatter companion `.memlog.md` does not exist in `architecture-portal-admin/`.
- Stack: name the likely `django.tasks` database backend package. Note that AdminLTE 4 is Bootstrap 5 based, so the admin site carries Bootstrap while the portal uses Tailwind (deliberate, but worth stating so mytp-core never ships templates).
- AD-5: say that patient codes have a unique constraint per region with regenerate-on-collision (40 random bits).
- The Capability map gives P14 only AD-8 and AD-12. Once F1 lands, add the new ops AD.

## What is solid (keep)

- AD-1/AD-2's ownership table, with "anything not listed is regional-only", is an excellent default-deny rule.
- The AD-3 outbox in the same transaction as the change, with no broker, fits the brownfield's PostgreSQL-only stance (the `DatabaseCache`, no Redis).
- AD-4's rule that the region checks the permission itself and logs in the region matches the lawyer's decision.
- AD-6/AD-7: Decimal plus currency, order-line snapshots, credit notes only, per-invoice VAT rounding.
- AD-13: the ban on comparing against a region name outside the profile module is greppable and enforceable in CI.
- AD-16: a single resolver for every purchase path.
- AD-8 ratifies part 1's systemd-timer + `runjob` + Sentry check-in pattern exactly.
