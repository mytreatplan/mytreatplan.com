---
title: 'Production checks for the EU go-live'
type: 'chore'
ticket: '12'
created: '2026-11-05'
status: 'done'
baseline_revision: '60b6c9be491be65e102ac0c2774075a4da51411d'
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

**Problem:** epic-admin-access's Done when 5 says its production checks are listed in epic-eu-golive, and epic-eu-golive's Done when 3 says each epic's production check is "listed at inception"; nothing is listed yet, so the go-live has no runnable check for the admin site's access features.

**Approach:** Add to `_bmad-output/initiative-mytreatplan-portal-admin/epic-eu-golive/epic-eu-golive.md`'s Done when one block for epic-admin-access: one runnable production check per Done when item 1–4 of epic-admin-access, covering what entry 12 names (2FA sign-in per role, lockout with the Sentry warning and the inbox alert, the fresh-code check, the emergency reset over SSH, the audit log). Each check says where (admin.mytpdso.com, or SSH on the admin instance), who (which test account), the steps, and the expected result, in the style of the staging checks in the PR descriptions (mytp-admin PRs #2–#11). The edit follows the bmad-ticket rules for epic files (no frontmatter change; a Notes line recording the addition). No other file changes except this plan.

- Decision (user, 2026-11-05): the checks go nested under epic-eu-golive's Done when item 3, as a sub-list headed "epic-admin-access (entry 12)"; later epics add their own sub-lists there.
- Decision (user, 2026-11-05): a Sysadmin creates one temporary test user per role at go-live and deactivates them after the checks (never deleted, so they stay in the audit history); the lockout and emergency-reset checks use a test Sysadmin.

</frozen-after-approval>

## Implementation Notes

Oneshot route: a documentation change to one epic file, written from the merged stories' plans (epic-admin-access entries 1–11) and their PR staging checks.

- Files: `_bmad-output/initiative-mytreatplan-portal-admin/epic-eu-golive/epic-eu-golive.md` (Done when 3: the sentence on per-epic sub-lists and the epic-admin-access block; Notes: the decision line). Frontmatter unchanged.

## Review Triage Log

Pass 1 (quick lens, opus, 2026-11-05): 8 findings, all real (medium 2, low 6); all patched in the epic file.

| # | Finding | Verdict | Action |
|---|---------|---------|--------|
| 1 | List demo and Download record are removed when epic-sales-finance's first list ships, before go-live | medium | Checks moved to Admin users and Audit log; the fresh-code example is New / Reset password; the CRM region scope goes to the production check of the epic that replaces List demo (stated in the block). |
| 2 | The create button is "New", not "Add" | low | Fixed. |
| 3 | The test CRM's regions were not given | low | "one region only". |
| 4 | The first sign-in goes to the new-password page; temporary passwords must be noted and passed on | medium | Added to Before and 3a. |
| 5 | "One by SMS" did not say how (email first, SMS on request) | low | "Send it by SMS instead" step added. |
| 6 | 3b and 3e named no account | low | Accounts named in every check. |
| 7 | 3e did not check the fixed columns | low | ID, Created, Status, View more added. |
| 8 | 3d's site name was an undefined placeholder | low | Says it is set when the production admin instance is provisioned (this epic); the command and expected output stay. |

## Verification

**Manual checks (if no CLI):**
- epic-eu-golive's Done when has one epic-admin-access block whose checks map 1:1 to epic-admin-access Done when 1–4, each runnable without reading code.
- `uv run --no-cache _bmad/method/scripts/tickets.py --project-root "$PWD" find 3.12` still resolves (the tree is not broken).

## Staging

- Documentation only, no server step: committed to master as 29de64e (2026-11-05). The checks run at the EU go-live (epic-eu-golive Done when 3).
