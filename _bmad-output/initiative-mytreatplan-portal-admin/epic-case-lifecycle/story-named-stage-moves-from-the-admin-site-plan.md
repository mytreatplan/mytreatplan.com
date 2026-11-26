---
title: 'Named stage moves from the admin site'
type: 'feature'
ticket: '1'
created: '2026-11-26'
status: 'done'
baseline_revision: '5a89ea7a77d849baac8a6caa01293c927350cb0c'
route: 'full'
route_source: 'auto'
risk: 'high'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-mytreatplan-portal-admin/architecture-portal-admin/architecture-portal-admin.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A work order's stage can only change through order hooks (payment, cancel, ICE close). Staff cannot move a case or assign it, the doctor's two steps don't exist, and the doctor's patient file doesn't show the case's stage.

**Approach:** mytp-core 0.36.0 names each case move, with the actor allowed to make it. One portal service applies a move: it checks the move and the actor, writes a stage history row, and fires the receivers registered for that move. A signed remote route (`work_order` resource: read one, write one move) lets the admin site request staff moves with a version and an idempotency key. The patient file shows the current service and its translated stage.

## Boundaries & Constraints

**Always:**
- Moves (core `mytp_core/moves.py`, built on `stages.ALLOWED`; every pair must pass `check_transition`):
  - `assign` (staff): pending_assignment → assigned. Needs `assignee` (an admin UUID).
  - `request_records` (staff): assigned → pending_registers_upload.
  - `records_complete` (doctor): pending_registers_upload → processing_prescription.
  - `prescription_done` (staff): processing_prescription → prescription_done.
  - `request_appointment` (staff): assigned or prescription_done → pending_appointment.
  - `start_development` (staff): pending_appointment → in_development.
  - `send_for_review` (staff): in_development → pending_review.
  - `send_for_approval` (staff): pending_review → pending_approval.
  - `request_changes` (staff): pending_review or pending_approval → in_development.
  - `approve` (doctor): pending_approval → approved.
- A doctor move needs a doctor actor who owns the patient. A staff move needs an admin actor.
- Every stage change writes one history row, including creation and the existing cancel/close/payment changes. The `move` field stays empty for changes that are not named moves.
- AD-15: the remote view is a thin adapter over `patients.service.apply_move`. The move emits the existing `work_order.stage_changed` event with the existing payload.
- Patient data stays out of task arguments, logs and Sentry (AD-23).
- Stage labels are translated (EN/ES).

**Never:**
- No admin-site screens; epic-clinical-ops builds them.
- No change to the `work_order.*` event payloads (mytp-admin validates them with its vendored core).
- No staff cancel move, no proposal or 24 h confirmation (10.10), no messages (10.2), no doctor buttons for Records complete or Approve (10.5, 10.8), and no stage history list on the patient file (10.4).
- No "set stage" anywhere on the interface.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Assign | Clinical Manager POST `{move: assign, assignee}`, right version, WO pending_assignment | 200: record with stage assigned, the assignee and version +1. One history row, one outbox event. | — |
| Replay | Same idempotency key and body | Same stored response; nothing applied twice | — |
| Stale version | expected_version ≠ current | 409 `stale_version` with current_version | No change, no history row |
| Wrong stage | Move not allowed from the current stage | 409 `stale_version` (precedent: `integration/transfers.py:97`) | No change |
| Doctor move remotely | `{move: approve}` | 400 `invalid_body` (not in the staff changes model) | Logged as refused |
| Clinical User assigns | Changes include `assignee` | 403 `forbidden` (field not writable) | Logged as refused |
| Clinical User, other's WO | Move on a WO assigned to someone else | 403 (`assigned` scope) | Logged as refused |
| Doctor move, wrong actor | `apply_move(records_complete)` by an admin actor, or by another doctor | `MoveRefused` | No change |

## Decisions

- Decision (user, 2026-11-26): the moves and their actors are as listed under Always.
- Decision (user, 2026-11-26): sysadmin and Clinical Manager may make every staff move, including assign; a Clinical User only on work orders assigned to them, and never assign.
- Decision (user, 2026-11-26): portal and mytp-core only; mytp-admin is not vendored in this story. The staging check is a shell script on admindev that signs the call with its own key through the generic signed POST.
- Decision (user, 2026-11-26): the plan is kept whole (about 2,000 tokens) as one tracer path.
- Decision (user, 2026-11-26, approve and continue): DELEGATED for the stories of this session: push the story branch and open the PR; merge once the user says "merge"; run the staging checks on dev over SSH; push the completion commit to master.

</frozen-after-approval>

## Code Map

Version trap: the local `../mytp-core` checkout is 4 commits behind (0.33.0). Pull it with the sandbox off before editing. The portal vendors v0.35.0.

- `../mytp-core/mytp_core/stages.py` -- stage constants, `ALLOWED`, `check_transition`. Reuse it; don't change the table.
- `../mytp-core/mytp_core/contracts/remote.py:249-278` -- `RESOURCE_READERS`/`RESOURCE_WRITERS`, `changes_for`. Precedents: `PendingTransferData`/`PendingTransferChanges`.
- `../mytp-core/mytp_core/access.py:113-140` -- `_RESOURCES`, `Rule(scope, fields, writable)`, scope `ASSIGNED`. Add `work_order` here (area `work_orders`, as in admin `permissions/matrix.py:140,148`).
- `../mytp-core/tests/` -- `test_stages.py`, `test_remote_writes.py`, `test_remote_access.py`, `test_contracts.py`.
- `../mytp-core/pyproject.toml`, `mytp_core/__init__.py` -- version 0.36.0 and the docstring line.
- `scripts/vendor-mytp-core.sh` -- vendors the wheel. Then update `integration/tests/test_vendored_core.py:26` (version and `registered_types()`).
- `patients/models.py:73` -- `WorkOrder` (has `stage`, `version`, `actor_type`, `actor_uuid`; no assignee).
- `patients/service.py:224-260` -- `_record_event`, `_move_locked`, `move_work_order`, `_check_actor`, `StaleVersion`. `_create_work_order` is the creation path.
- `subscriptions/service.py:118` -- `Actor`, `admin_actor`, `doctor_actor`. Reuse them.
- `payments/hooks.py` -- registry pattern (`register`/`unregister`/`receivers`/`fire`, `NAME_RE`) to copy for moves.
- `integration/remote.py:112,291` -- `remote_read`, `remote_write` (`assignee=` callback for the `assigned` scope). `integration/transfers.py` -- the load/build/update/version adapter to mirror.
- `integration/views.py:160-190`, `integration/urls.py` -- the `remote_pending_transfers` view and route to mirror.
- `patients/views.py:96-131`, `patients/templates/patients/patient.html` -- the patient file. `catalogue.resolver.product(...)` gives the service name and category.
- Tests: `integration/tests/test_transfers.py` (`admin_site`, `_headers`, `scope`), `patients/tests/conftest.py` (`patient`, `manual_work_order`, `CLINICAL_OPS`), `patients/tests/test_views.py`.

## Tasks & Acceptance

**Execution:**
- [ ] `../mytp-core/mytp_core/moves.py` -- `Move(name, from_stages, to_stage, actor)` table, `MOVES`, `STAFF_MOVES`, `DOCTOR_MOVES`, `check_move(name, from_stage, actor_type)` -> to_stage (raises `MoveNotAllowed`). An import-time assert that every pair passes `check_transition`.
- [ ] `../mytp-core/mytp_core/contracts/remote.py` + `access.py` -- `WorkOrderData(code, stage, product_category, assignee)`, and `WorkOrderChanges(move: Literal[staff moves], assignee: Optional[CanonicalUuid])` with a validator: `assign` needs `assignee`, other moves refuse it. Access rules: sysadmin and clinical_manager ALL, writable (`move`, `assignee`); clinical_user ASSIGNED, writable (`move`). No financial fields, no patient names.
- [ ] `../mytp-core/tests/` + version 0.36.0 -- unit tests for moves, the changes model and the access rules. Commit, then tag locally (the release flow is in Design Notes).
- [ ] `vendor/`, `requirements.txt`, `integration/tests/test_vendored_core.py` -- vendor v0.36.0.
- [ ] `patients/models.py` + migration -- `WorkOrder.assignee_uuid` (nullable). New append-only `StageChange(work_order FK, move, from_stage, to_stage, version, actor_type, actor_uuid, at)`, ordered by `at`.
- [ ] `patients/moves.py` -- receiver registry per move name (copy of the `payments.hooks` pattern). `fire(move, work_order)` is called inside the transaction; receivers defer side effects with `on_commit`.
- [ ] `patients/service.py` -- `apply_move(work_order, move, expected_version, actor, assignee=None)`: lock, version check (`StaleVersion`), `moves.check_move` by actor type (`MoveRefused` on refusal), check that a doctor actor owns the patient, set the assignee on `assign`, then `_move_locked`, then fire receivers. `_move_locked` and `_create_work_order` write the `StageChange` row.
- [ ] `integration/work_orders.py`, `integration/views.py`, `integration/urls.py` -- `remote/work-orders` GET (one by `id`) and POST, as the transfer adapter. `update` maps `MoveRefused` and a wrong stage to `StaleVersion(current)`. `assignee(record)` returns `assignee_uuid`.
- [ ] `patients/stages.py` (labels) + `patients/views.py` + `patient.html` + `locale/es` -- translated `STAGE_LABELS`. The file shows the live work order (or else the latest) with its service name and stage label, and "No case yet" when there is none.
- [ ] Tests: `patients/tests/test_moves.py` (service, history rows, receivers, actor checks), `integration/tests/test_remote_work_orders.py` (every matrix row, plus the access log), `patients/tests/test_views.py` (stage shown in EN and ES).

**Acceptance Criteria:**
- Given a dev work order in pending_assignment, when a call signed as the admin site with a Clinical Manager token assigns it and then sends `request_appointment`, then the patient file shows the new stage, and two history rows and two outbox events exist.
- Given any existing path that changes stage (payment, cancel, ICE close), when it runs, then exactly one history row is written for that change.
- Given the full suite, when run, then it passes with 0 new failures.

## Design Notes

Remote write body example: `{"idempotency_key": "...", "expected_version": 3, "object_id": "<wo uuid>", "changes": {"move": "assign", "assignee": "<admin uuid>"}}`.

Release (colleague's flow):
1. Core PR bumps the version.
2. Merge it; `tag-release.yml` tags v0.36.0.
3. Vendor from the tag. A local tag is allowed for vendoring before the merge.
4. Merge order: core → portal.

The user pushes the core branch and the tag.

## Verification

**Commands:**
- `cd ../mytp-core && uv run pytest -q` -- expected: all pass.
- Portal suite (per local-dev notes) -- expected: 0 failures besides the known WeasyPrint ones.

**Manual checks (if no CLI):**
- Staging (dev): a signed admin-site call assigns a dev work order and makes one move. Check the history rows and the 409 on a stale version, then the patient file by eye. Roll back the data.


## Implementation Notes

- mytp-core branch feat/10-1-named-stage-moves (baseline 76a577b), version 0.36.0, local tag v0.36.0 vendored into the portal; the real tag comes from `tag-release.yml` after the core merge (re-vendor and check the wheel is identical).
- Deviations kept: `move` is also a read field of `work_order` (core `access.validate` requires writable ⊆ visible); `StageChange.work_order` is CASCADE, not PROTECT (the erasure purge deletes work orders); a reused idempotency key with another body is 422, as every remote write.
- Existing work orders get no history rows (no backfill): their history starts at their first change after the deploy.
- Review patches applied by the implementer: core commit 9f584a4 (code max 64, README notes; tag v0.36.0 moved to it), portal wheel re-vendored, AppendOnly guard on `StageChange` (queryset update/delete refused too), MoveRefused for a non-UUID assignee, `move` = last non-empty move, tighter remote tests.
- Verification after the patches: portal suite 4631 passed, 17 skipped, 11 failed (the known WeasyPrint OSError in invoices/tests/test_pdf.py and test_pending_documents.py); `makemigrations --check` clean; core suite 1858 passed, 4 skipped (PostgreSQL only). Every matrix row has a passing test.

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, Opus): 10 findings — high 0, medium 2, low 6, false 0, maybe-false 0, plus 2 test-coverage rows; patch 8, defer 1, reject 1.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | `WorkOrderData.code` max 40 vs portal code max 64 | medium | patch | `patients/models.py:103` and `catalogue/models.py` `Product.code` are 64; the admin validates `data` with the core model. Set to 64, new core commit, tag moved, re-vendored. |
| 2 | `assign` accepts any UUID; a wrong one leaves the case assigned to nobody with no reassign move | low | reject (+ defer the reassign gap) | The admin site sends its own user's UUID from its UI; a check against `AdminDirectoryEntry` adds a guard for a state not shown reachable. Reassigning a case is a real gap: deferred to 10.10 (assignment). |
| 3 | Non-UUID assignee raises ValueError, not MoveRefused | low | patch | `apply_move` calls `uuid.UUID(...)` without a guard; only the remote path is contract-checked. Wrapped in MoveRefused. |
| 4 | `StageChange` append-only not enforced | medium | patch | Precedent `payments.models.OrderChange` guards save/delete; copied. |
| 5 | Stage code in `data-case-stage` contradicts the stages.py docstring | low | patch | Docstring corrected (code only in a data attribute). |
| 6 | `move` read field null after a later non-move change | low | patch | `build` took the latest row; now the latest non-empty move, with a test. |
| 7 | No backfill of history for existing work orders | medium | defer | Real for 10.4's history list; earlier changes cannot be rebuilt from data. Deferred to 10.4. |
| 8 | Core README 0.36.0 note under "Releasing" step 3 | low | patch | Moved to the version notes. |
| 9 | `test_bad_changes_are_an_invalid_body` accepts 400 or 403 | low | patch | Parametrized per case with the refused access-log row. |
| 10 | Wrong-stage test lacks outbox/access-log asserts; replay after role change untested | low | patch (first part); reject (second) | Asserts added. The replay-after-role-change claim: `remote.py` already filters a stored answer by the fields the actor reads now (pre-existing, covered by its own tests). |

## Staging

- **Merge:** mytp-core PR #4 → 039d0a7. Core CI passed on the master run (45895162016), and `tag-release.yml` tagged `v0.36.0` on the merge commit. Re-vendored from that tag, the wheel's zip timestamps differ, but its unpacked contents are identical to the PR's wheel, so the PR's wheel was kept. Portal PR #153 → 9cad6a4. The PR's CI test job passed (run 45883030732). The master run 45895405483 passed: tests passed, and dev and dev-ae were deployed. The local suite had 4631 passed, 17 skipped and 11 local-only WeasyPrint failures.
- **Checks on dev (2026-11-26)** were run over SSH with shell scripts. The signed calls came from admindev with admindev's own key (`mytp_core.interface.signed_post`), because admindev still runs core 0.35. The work order was the seed work order 4591957d (`pending_assignment`, version 1, no history).
  1. Migration 0006 is applied on dev and dev-ae. Passed.
  2. As a Clinical Manager, the read showed `assignee, code, move, product_category, stage`. `assign` (to a test Clinical User UUID) returned 200 with version 2 and stage `assigned`. `request_appointment` returned 200 with version 3 and stage `pending_appointment`. Dev has 2 `StageChange` rows (`assign`, `request_appointment`, actor `admin`) and 2 `work_order.stage_changed` outbox events, both delivered. Passed.
  3. A stale version (1) returned 409 `stale_version` with current version 3. A Clinical User sending `assign` returned 403 `forbidden`. Both are in the access log: `refused stale_version` and `refused field`. Every call is in `InterfaceCall`. Passed.
  4. The patient page (`/en/` and `/es/`, as the patient's doctor) returned 200 with the Case section showing "Appointment requested" / "Cita solicitada". Passed.
- **Left on dev:** seed work order 4591957d, now at `pending_appointment`, version 3, assigned to test UUID c1a10000-…-0102, with its 2 history rows and access-log rows. It was not rolled back. Its 2 events were already delivered to admindev, and lowering the version again by SQL would leave admindev's copy ahead of dev.
