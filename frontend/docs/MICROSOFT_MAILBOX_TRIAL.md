# Microsoft mailbox trial — next steps

## Product direction

Build Microsoft into a credible Cuevion mailbox alternative **before** choosing
a permanent cross-provider UI. Keep current Gmail/IMAP unchanged during the trial.
The Microsoft view is a controlled single-user Production pilot with explicit
owner-scoped OAuth authority. New features are developed on feature branches and
Vercel Preview first; Production requires an independently approved release.

## Filtered trial, phase 1 (Preview candidate)

- **Filtered is a Cuevion view of Graph Inbox, not a Microsoft folder.**
  Folder switching, unread-only, search, sort, paging, exact message read,
  attachments and Auto-Read use the existing Inbox Graph authority. No Graph
  MOVE, archive, delete, send, or new OAuth scope is introduced.
- Only *loaded* Inbox page rows (25/page, 4 pages maximum) participate.
  Counts and search must never imply a whole-account mailbox scan.
- Microsoft reads existing scoped Cuevion sender/domain learning via the
  canonical v2 learning storage key, with authenticated workspace/user context.
  Explicit learned Show Less / low-priority routing is reused when present.
  Subject keywords alone never automatically hide a message.
- **Show less from this sender** and **Keep this sender in Inbox** are explicit
  local decisions, persisted to a separate Microsoft-specific key scoped to
  workspace/user/mailbox ID. Existing Gmail/IMAP learning rules are read, **not
  modified** by a Microsoft choice. Outlook message location is unchanged.
- Priority, active, review, reply and uncertain messages fail open to Inbox.
  Local rules are discarded on parse errors and never applied if owner identity
  is missing or changed. Only sender keys are persisted; no subject, snippet,
  body, Graph token, attachment or provider message IDs are stored.
- This first trial's Microsoft-specific sender choices are **browser-local**:
  not synchronized across devices and not yet integrated into global Cuevion
  learning audit/history. This must be considered in the final UX comparison.

## Evaluation of the completed Microsoft alternative

1. Verify live Microsoft connection, Auto-Read and paging across all six Graph
   folders. Check virtual Filtered against shared learned rules and a deliberate
   Show Less / Keep action, including refresh and moving between pages.
2. Compare mailbox list density, header/action layout, keyboard/mobile behavior,
   detail view, attachment handling and empty/error states with existing Gmail/IMAP.
3. Design owner-scoped Microsoft Reply, Reply All and Forward including drafts
   and explicit send confirmation. Mail.Send needs separate consent, service
   review and Production authorization before any live email is sent.
4. Implement Archive/Delete/restore as separate verified Graph changes only
   with explicit approval, idempotency/lease safeguards and rollback plan.
5. Review whether Microsoft and Gmail/IMAP should share the same presentation
   component and unified learned rule store. Do **not** migrate old mailboxes
   or replace Gmail/IMAP until the user compares both working alternatives.

## Explicit exclusions for this feature branch

No change to main/Production, Gmail, IMAP, Auth0, Microsoft OAuth scopes,
Out-of-Office, or any remote mailbox content. No server-side filtered mailbox,
automatic server-side classification, global sender-rule writes, or bulk move.
