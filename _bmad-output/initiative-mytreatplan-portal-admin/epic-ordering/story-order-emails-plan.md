---
title: 'Order emails'
type: 'feature'
ticket: '20'
created: '2026-11-26'
status: 'ready-for-dev'
baseline_revision: ''
route: 'full'
route_source: 'auto'
risk: 'medium'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A doctor who approves a bank-transfer order or pays a service order gets no email. They have no written confirmation, no bank details in their inbox and no receipt.

**Approach:** Two receivers on the payments hooks queue a task after commit. The task sends an order confirmation (on `transfer_approved`) or a payment receipt with the invoice PDF (on `paid`), each at most once per order. Decisions (user, 2026-11-25, answers 2A/3A/4A): bank-transfer orders get the confirmation at approval, with the bank details as the 7-day reminder shows them; every service order gets one receipt when paid, with the invoice PDF attached; card and free orders get the receipt only.

Decisions (user, 2026-11-26):
- The receipt attaches a codes-only render of the invoice PDF (no patient names); the PDF downloaded in the portal keeps the names.
- A failed send is tried once and logged by order UUID; nothing is marked sent, so staff can re-run it by hand. No automatic retry.
- The plan is kept whole (~2100 tokens).

## Boundaries & Constraints

**Always:**
- Only orders of kind SERVICE with at least one line that has a patient code, and with a doctor account. Others send nothing.
- Patients appear by code only, in the emails and in anything attached.
- The doctor's language, chosen as `payments.transfers.build_message` does.
- The receiver only queues the task after commit; it never sends, renders a PDF or raises inside the hook's transaction. Rollback of the transition queues nothing.
- Once per order per email, even when the hook fires again (`transfer_approved` fires on every choice of bank transfer).
- A failed send is logged by order UUID and exception type only. It never changes the order.

**Never:**
- Changes to the 7-day transfer reminder, its templates or `Payment.reminded_at`.
- Changes to hook receiver order: `invoice_issue` stays before every `paid` receiver of this story.
- Emails for subscription orders, refunds, cancellations or expiry.
- Logging names, addresses, bank details or email addresses.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Transfer approved | service order with patient lines, bank transfer chosen | one confirmation: reference, total, patient codes, bank details, link to the transfer page | none |
| Transfer chosen again | same order, transfer → card → transfer | no second confirmation | none |
| No bank details | company without bank details | confirmation still sent, without the bank-details block (as the reminder) | none |
| Transfer paid | staff confirm on admindev | one receipt with `<invoice number>.pdf` | none |
| Card / free paid | Redsys or zero-total order | receipt only, no confirmation | none |
| Subscription / no patient lines / no user | any | no email | none |
| Send or render fails | SMTP or PDF error in the task | order unchanged; nothing marked sent | logged by order UUID + type |
| Transition rolls back | another receiver raises | nothing queued | none |

</frozen-after-approval>

## Code Map

- `payments/hooks.py` -- `register(event, name, fn)`, `fire`; receivers run inside the transition's transaction in registration order (INSTALLED_APPS: payments, invoices, patients, cart). Do not change.
- `payments/service.py:561` -- `transfer_approved` fires in `_start_payment` on every bank-transfer choice. `_move` (:566) fires `paid` once per order (`mark_paid`, `confirm_payment`, `mark_transfer_paid`, `_pay_free`).
- `payments/models.py` -- `Order.kind` (SERVICE/SUBSCRIPTION), `reference`, `user` (nullable), `gateway`; `OrderLine.patient_code` (patient lines: `lines.exclude(patient_code='')`).
- `payments/transfers.py:38` `build_message` -- pattern to copy: profile language with fallback, `translation.override`, `.txt` subject/body templates with `{% autoescape off %}`, plain `EmailMessage(to=[user.email])`, `DEFAULT_FROM_EMAIL`. Failure log style :100.
- `payments/bank_transfer.py:54` `details(order, payment)` -- bank details, or None. Reminder templates in `payments/templates/payments/email/transfer_reminder*.txt` show how they are printed.
- `invoices/service.py:197` `issue_for_order` -- runs on `paid` before patients and cart receivers; after commit `Invoice.objects.get(order=order)` exists (free orders too).
- `invoices/pdf.py:236` `render(invoice, user, build=context)` returns bytes (WeasyPrint, heavy: run in the task). `_lines` (:88) prints the patient's current name next to the code via `invoices.patients.names_for`. File name pattern `f"{invoice.number}.pdf"` (`invoices/views.py:89`).
- `mytp_core.tasks.enqueue_on_commit(task, str(uuid))` and `run_once(effect, record, fn)` -- queue after commit; the TaskEffect mark and the send commit together, a raise leaves no mark. `django.tasks` with `django_tasks_db` + `db_worker`; task example `payments/tasks.py`.
- `cart/apps.py` -- already registers `cart_paid` and `cart_transfer_approved`; the ordering app owns the new receivers.
- Tests: `cart/tests/test_checkout.py` `finish`, `pay`, `by_transfer` (:76-112) build orders with patient lines; `payments/tests/test_bank_transfer.py` `bank` fixture, `start_transfer`, `switch_to_card`; on_commit needs `django_capture_on_commit_callbacks(execute=True)`; mail via `mail.outbox`. The PDF tests need WeasyPrint (fails locally on this Mac, fine on CI): mock `render` in the email tests.

## Tasks & Acceptance

**Execution:**
- [ ] `cart/emails.py` -- new: `should_email(order)`; `build_confirmation(order, payment)` and `build_receipt(order, invoice)` returning `EmailMessage`s in the doctor's language; receivers `on_transfer_approved(order)` and `on_paid(order)` that check `should_email` and `enqueue_on_commit` the task with the order UUID -- keeps the hooks fast and non-raising.
- [ ] `cart/tasks.py` -- new `@task send_order_email(kind, order_id)`: inside `run_once('order_confirmation' | 'payment_receipt', order_id, send)` re-read the order, build and send; catch every exception outside `run_once`, log `order email not sent: <kind> order <uuid> (<Type>)` -- once per order, failure leaves no mark.
- [ ] `cart/apps.py` -- register `transfer_approved` → `order_confirmation_email` and `paid` → `payment_receipt_email` after the existing cart receivers.
- [ ] `cart/templates/cart/email/order_confirmation{,_subject}.txt`, `payment_receipt{,_subject}.txt` -- reference, total, one row per patient line (product name + patient code), bank details block on the confirmation only when present, link to the transfer page or the invoices list.
- [ ] `invoices/pdf.py` -- a codes-only build (a `build` like `context` whose lines skip `names_for` and show the code alone) and a `render_codes_only(invoice, user)` helper; `render` and the portal download unchanged.
- [ ] `locale/es/LC_MESSAGES/django.po` -- Spanish strings for the four templates; `compilemessages`.
- [ ] `cart/tests/test_order_emails.py` -- one test per I/O matrix row, plus: receipt attachment name and content type; no patient name anywhere in the message or attachment; Spanish doctor gets Spanish text.

**Acceptance Criteria:**
- Given a doctor whose profile language is Spanish, when their transfer order is approved, then the confirmation subject and body are in Spanish.
- Given a paid order, when the receipt task runs twice, then `mail.outbox` holds one receipt.
- Given the full suite, when it runs, then it passes with the existing reminder and hook tests unchanged.

## Implementation Notes

## Plan Change Log

## Review Triage Log

## Verification

**Commands:**
- `../.venv/bin/python -m pytest -q -p no:cacheprovider cart payments invoices patients` (env as in the suite command) -- expected: all pass except the known local WeasyPrint failures.
- `../.venv/bin/python -m pytest -q -p no:cacheprovider --tb=line` -- expected: full suite passes.
