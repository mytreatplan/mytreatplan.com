---
title: 'Case messages by email'
type: 'feature'
ticket: '2'
created: '2026-11-27'
status: 'in-progress'
baseline_revision: '3ebd81e0546e5d3a1655f2e593736482c6706123'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-mytreatplan-portal-admin/architecture-portal-admin/architecture-portal-admin.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** When staff move a case into Pending appointment, Pending review or Pending approval (10.1), the doctor is not told, so the case waits on them unseen (L9).

**Approach:** A small notification service in the region: each such move writes one message row in the move's transaction, and a task sends the doctor's email from it after the commit and keeps the result on the row. Later stories add the SMS fallback (10.3) and the "assigned" message (10.10) on the same row.

## Boundaries & Constraints

**Always:**
- Receivers of 10.1's stage hook (`patients.moves.register`) for `request_appointment`, `send_for_review`, `send_for_approval`, one per move. The receiver only writes the row and enqueues on commit; it never sends.
- One row per (work order, work-order version, channel). Replaying the same move, or running the task again, sends nothing twice. A later, real return to the same stage, such as `request_changes` then `send_for_review`, is a new version and sends again.
- To the doctor's login email (`patient.doctor.email`), in the doctor's language (`invoices.pdf.language_of`). The patient is named by code only, and the email links to the patient page.
- AD-23: task arguments are the row's UUID only. Log lines carry the row UUID, kind and outcome. No names, emails or codes in logs or errors.
- The row stores the outcome: `sent` with the time, or `failed` with a short reason code (`no_address`, or the exception type) for 10.3 to act on.

**Never:**
- Moving the order emails (9.20) onto this service; that is the refactor sweep's.
- SMS, assignment messages, or a doctor opt-out.
- Sending inside the move's transaction, or failing the move because a message cannot be written.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Enter one of the three stages | `send_for_review` succeeds | 1 row `pending`; after commit 1 email to the login email, row `sent` | — |
| Spanish doctor | profile language `es` | subject and body in Spanish, stage label translated | — |
| Replayed write | same idempotency key resent | no new row, no second email | — |
| Task runs twice | row already `sent` | nothing sent | — |
| Back to the same stage later | `request_changes`, then `send_for_review` | a second row (new version), a second email | — |
| Move rolled back | the move's transaction fails | no row, nothing queued | — |
| No login email | doctor email empty | row `failed`, reason `no_address` | logged by row UUID |
| SMTP error | backend raises | row `failed`, reason is the exception type | logged, no retry |
| Other moves | `assign`, `start_development`, … | no row | — |

## Decisions

- Decision (user, 2026-11-27): a failed email gets one try. The row stays `failed` with its reason, and 10.3's SMS fallback reads it (as 9.20).
- Decision (user, 2026-11-27): one template per message, translated through the `.po` catalogue (as 9.20), not a file per language.

</frozen-after-approval>

## Code Map

- `patients/moves.py:23,47` -- `register(move, name, receiver)`; `fire` calls `receiver(locked_work_order)` inside `apply_move`'s atomic block (`patients/service.py:300-319`). A receiver that raises rolls the move back.
- `cart/emails.py`, `cart/tasks.py:18-48`, `cart/apps.py` -- 9.20's pattern to copy: `enqueue_on_commit`, `@task` with a UUID string, plain-text templates with `{% autoescape off %}`, `translation.override(language_of(user))`, `f"{settings.SITE_URL}{reverse(...)}"`, failure logged by UUID.
- `cart/tests/test_order_emails.py` -- test style: `django_capture_on_commit_callbacks(execute=True)`, `mail.outbox`, Spanish, no patient name.
- `invoices/pdf.py:35` `language_of(user)` -- reuse.
- `patients/stages.py` `stage_label` -- translated stage name for the body.
- `mytreatplan/settings.py:485-501` -- `django.tasks` with the db worker; add the new app to `INSTALLED_APPS`.
- Do not use `mytp_core.tasks.run_once` here: the row's `state` is the once-only mark, as its docstring advises for business messages.

## Tasks & Acceptance

**Execution:**
- [ ] `notifications/` (new app: `apps.py`, `models.py`, migration `0001`) -- `Message`: uuid pk, `kind` (`case_pending_appointment`, `case_pending_review`, `case_pending_approval`), `channel` (`email`), `work_order_id` UUID, `work_order_version`, `recipient` FK user (PROTECT), `state` (`pending`/`sent`/`failed`), `reason`, `created_at`, `sent_at`; unique (`work_order_id`, `work_order_version`, `channel`).
- [ ] `notifications/service.py` -- `case_message(work_order, kind)` (get-or-create the row, then `enqueue_on_commit`); `send(message_id)` (lock the row, skip unless `pending`, render, send, set the state).
- [ ] `notifications/tasks.py` -- `@task send_message(message_id)` calling `service.send`; exceptions are caught and stored on the row.
- [ ] `notifications/receivers.py` + `apps.py` `ready()` -- register the three receivers (name `notifications_case`).
- [ ] `notifications/templates/notifications/email/<kind>.txt` and `<kind>_subject.txt` -- patient code, stage label, patient page link, no name.
- [ ] `locale/es/LC_MESSAGES/django.po` -- Spanish subjects and bodies.
- [ ] `notifications/tests/` -- one test per matrix row, plus: no patient name or email in the logs, a body without the name.

**Acceptance Criteria:**
- Given a dev case at `in_development`, when admindev sends `send_for_review`, then the doctor's login email gets one "Pending review" email with the patient code and no name, and the row is `sent`.
- Given the same request is replayed, when it arrives, then no second email is sent.

## Design Notes

The row is written in the move's transaction, so a rolled-back move leaves nothing behind and a committed move always has its row. The task is queued only after the commit (`enqueue_on_commit`), and the row's state makes the task safe to run again. A crash between sending and marking `sent` can still send twice (at least once, as 9.20).

## Verification

**Commands:**
- Portal suite (per the local dev notes: `../.venv/bin/python -m pytest -q`) -- expected: 0 failures besides the known WeasyPrint ones.
- `../.venv/bin/python manage.py makemigrations --check --dry-run` -- expected: no changes.

## Implementation Notes

## Plan Change Log

## Review Triage Log

