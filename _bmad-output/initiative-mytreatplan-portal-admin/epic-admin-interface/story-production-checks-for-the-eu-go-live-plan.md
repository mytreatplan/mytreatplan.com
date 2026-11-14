---
title: 'Production checks for the EU go-live (epic-admin-interface)'
type: 'chore'
ticket: '11'
created: '2026-11-14'
status: 'done'
baseline_revision: '24c3d49a3fc6bc0516266b05847f557f18b1bb64'
route: 'oneshot'
route_source: 'auto'
risk: 'low'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** epic-eu-golive's Done when 3 lists the production checks of epic-admin-access, epic-platform and epic-catalogue. Nothing says how to prove in EU production that the interface between `admin` and `portal-eu` works: signed calls both ways, the snapshot with the admin site down, client records and contact messages reaching the admin site, remote access, refusals and alerts.

**Approach:** Add a sub-list "epic-admin-interface (entry 11)" under Done when 3 of `epic-eu-golive/epic-eu-golive.md`, in the form of the epic-catalogue one (header, Before, lettered checks 3u–3z, clean-up), with one runnable check per Done when item of epic-admin-interface (1–4). Add one Notes line to each of the two epics. Docs only.

**Decisions (user, 2026-11-14):**
- Remote read and write (Done when 3): the staging sample record stays off in production. The check shows what production can show now: the signed call from `/patients/` is accepted and logged on portal-eu, the page answers "The region has no such record.", nothing is stored on admin, and the setting is off. The granted read and write with its regional access-log row is named as the production check of epic-ordering / epic-clinical-ops.
- Hub lookup: `hub_lookup sample` on portal-eu is refused with 404 and logged on admin; the real lookup is epic-appointments' check.
- Client record and contact message (Done when 2) use test data: the temporary test doctor of 3k signs up, is read on admin (count, ciphertext, `resync_client`), then is erased and gone from admin; one contact message with the text "PRODUCTION CHECK" is sent, read on admin and in the company inbox, then deleted on the portal and gone from admin.
- Admin site down (Done when 1) and the outbox alert: one stop of the admin site of about 20 minutes, in the maintenance window 3m uses (before 1.10's DNS switch). The pull is refused and the last snapshot kept, portal-eu keeps serving, a test contact message waits in the outbox, the outbox alert arrives at the real 15-minute threshold; after the start everything is delivered and the alert recovers.
- The story lands as one docs commit on master with this plan inside, pushed directly, no PR; nothing to deploy or check on staging. Merge and staging are delegated for 4.11.

</frozen-after-approval>

## Code Map

- `_bmad-output/initiative-mytreatplan-portal-admin/epic-eu-golive/epic-eu-golive.md` -- Done when 3: add the sub-list after the epic-catalogue one (3t and its clean-up line); Notes: one Decision line. Change nothing else.
- `_bmad-output/initiative-mytreatplan-portal-admin/epic-admin-interface/epic-admin-interface.md` -- Notes: one line pointing at the sub-list.
- Portal commands: `ping_peer admin`, `pull_config`, `show_config`, `dispatch_outbox`, `purge_outbox`, `check_interface`, `hub_lookup sample`, `sync_clients --all`, `sync_contact_messages --all`; jobs `dispatchoutbox`, `pullconfig`, `checkinterface`, `purgeoutbox`, `purgeinterfacecalls`; shell queries in `deploy/README.md` (call log `integration.InterfaceCall`, outbox, `integration.RemoteAccess`); Django admin: Users → Erase account, Contact messages.
- Admin commands: `ping_peer portal-eu`, `add_peer_key`, `retire_peer_key`, `nudge_config`, `resync_client <uid>`, `resync_contact_message <uid>`; shell queries in `README.md` (`interface.InterfaceCall`, `interface.PeerKey`, `interface.AggregateVersion`, `clients.ClientRecord`, contact messages, the `dbshell` ciphertext check). Pages: `/interface-events/` (Sysadmin only), `/patients/` (Sysadmin, Clinical Manager, Clinical User). No page for the call log, clients or contact messages.
- Refusals: 401 `{"error": {"code": ...}}` with `missing`, `signature`, `unknown_key`, `audience`, `expired`, `request_hash`, `replayed`; journal line `interface call refused: ...`; the replay recipe in the portal's `deploy/README.md`. Allowlist: `API_PEER_IPS` on `provision.sh`; 403 from another address.
- Already covered, not repeated: 3j (`kmscheck` unwraps the interface signing key), 3n (`nudge_config`, `pull_config`, `show_config`).
- Alerts: `check_interface` lines `outbox ok|warned|still_failing|recovered`, `config ok|...`; thresholds 15 and 60 minutes; monitor `mytp-portal-eu-checkinterface`.

## Tasks & Acceptance

**Acceptance Criteria:**
- Given epic-admin-interface's Done when 1–4, when the sub-list is read, then each item is named by at least one lettered check with its command or page, its expected output and its clean-up.
- Given a command, flag, query, page, role or quoted output named in a check, when it is looked up in the portal or mytp-admin at master, then it exists with that name and behaves as the check says.
- Given the diff, when it is read, then only the two epic files and this plan changed, and nothing else in epic-eu-golive moved.

## Implementation Notes

Oneshot: one sub-list and two Notes lines in planning documents, under 100 lines, no code.

- `epic-eu-golive/epic-eu-golive.md`: the sub-list "epic-admin-interface (entry 11)" after the epic-catalogue one (header, Before, 3u signed calls / keys / allowlist, 3v Done when 4, 3w Done when 1 and the alerts, 3x and 3y Done when 2, 3z Done when 3 and hub lookups, clean-up) and one Decision line in Notes. `epic-admin-interface/epic-admin-interface.md`: one Notes line.
- Commands, flags, outputs, pages and roles were read in the code and READMEs at portal 24c3d49 and mytp-admin bd1ef04. No check was run in production (that is epic-eu-golive's work).
- Choices: six checks 3u–3z so the letters end at z; the config alert is provoked by one `check_interface` run with `CONFIG_ALERT_MINUTES=1` given inline during the stop (the settings read the environment before `settings.env`), the outbox alert at its real 15 minutes; 3v provokes `missing`, `replayed`, `signature` and `unknown_key` (a request signed with `issuer='unknown'`); the first contact message of 3y is the event that waits in 3w's outbox.
- Landed as one docs commit on master, no PR, nothing deployed, no staging check (user decision).

## Plan Change Log

## Review Triage Log

Quick lens, one pass (2026-11-14): 12 findings, 0 high, 7 medium, 5 low, 0 false; 11 patched in the sub-list, 1 patched and its code side deferred.

| # | Finding | Verdict | Action |
|---|---------|---------|--------|
| 1 | 3x had the doctor change their phone on the portal: no profile page exists and `phone` is read-only in the Django admin (`accounts/admin.py:122`) | medium | patched: a staff member changes the title in the Django admin |
| 2 | 3x's `resync_client` with a made-up UUID stops before any call without `--region` | medium | patched: `--region eu` |
| 3 | The dbshell queries (`limit 3`) cannot show or rule out one uid; no source for the uid | medium | patched: `where uid = '<uid>'`; the uid from the Django admin page and the `AggregateVersion` query |
| 4 | 3w: `config recovered` is printed at the next timer run, with the admin site still down, not after the start | medium | patched |
| 5 | 3w: the journal line `interface alert cleared: outbox` is never written (`integration.alerts` not in `LOGGING`) | low | patched: the check reads the `recovered` line; the README / logger fault is in deferred-work.md |
| 6 | 3w: the outbox alert counts from the message, not from the stop, and could arrive after the start | medium | patched: the message is sent first, the start waits for the warning, the stop is 20 to 25 minutes |
| 7 | 3w and 3y use https://mytreatplan.com before the DNS switch | medium | patched: a hosts entry on the tester's machine, in Before |
| 8 | 3v: changing the last character of the signature may leave it unchanged (base64 padding bits) | low | patched: a character in the middle of the third part |
| 9 | 3v: the README's query (`[:3]`) cannot show every refusal; `curl -s` prints no status | low | patched: `filter(accepted=False)[:4]`, `-w '%{http_code}'` |
| 10 | 3v: an unknown service was not provoked, and "needs a second key" was wrong (`sign_request` takes `issuer=`) | medium | patched: a request signed with `issuer='unknown'` gives 401 `unknown_key` |
| 11 | Before allowed a test doctor already signed up, so the count could not rise | low | patched: the count is noted before the signup |
| 12 | The test doctor's erasure was stated in 3k's clean-up and again in 3x | low | patched: one erasure, the last step of 3x, named as epic-platform's clean-up |
