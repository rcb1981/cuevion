# Microsoft auto-mark-read: controlled release checklist

Status: explicitly approved for Production release on 2026-10-15.
Code gates are enabled only in the reviewed release candidate; Microsoft
reconnection and live mailbox smoke testing are still user-controlled.
No sending, archiving, deletion, or mark-unread is approved.

## Scope and safety boundaries

- Only the authenticated owner's exact Inbox message may be marked **read**,
  triggered after successfully opening that full message.
- Opening Sent, Archive, Drafts, Deleted Items, or Junk Email never changes
  read state. Merely loading a list does not change read state.
- The existing owner-scoped mutation lease, exact-ID Graph GET/PATCH/readback
  and authority recheck remain the sole server write path. No retries after
  ambiguous provider results. UI indicators change only on confirmation.
- Source-only Production code gate:
  `_PRODUCTION_READ_FLAGS_RELEASED = True` in
  `microsoft_production_pilot.py`. Environment variables cannot enable it.
- Independent UI code gate:
  `MICROSOFT_AUTO_MARK_READ_RELEASED = true` in
  `MicrosoftMailboxReadOnlyView.tsx`. Keep the general
  `MICROSOFT_READ_ONLY_PILOT = true` so Reply, New Message and other mail
  mutations do not become available.
- Future Production mark-read POST requires an exact canonical Host and
  `Origin: https://app.cuevion.com`; mark-unread remains blocked.
- Microsoft sending remains blocked by
  `production_pilot_writes_blocked()` at the outbound HTTP boundary.
  No `Mail.Send` consent is requested.
- Production OAuth remains the same read-only scope set while the release
  switch is false. A later separately approved release may request
  `Mail.ReadWrite` **only** in addition to existing `Mail.Read`,
  `User.Read`, `offline_access`, and identity scopes.
  `Mail.ReadWrite` grants Microsoft mailbox write privileges broadly:
  the application's per-operation enforcement is a critical restriction.

## Required before a separate Production release

1. Obtain explicit approval for this specific Production mail mutation and
   incremental OAuth consent. Do not infer approval from Preview signoff.
2. Review tests for the production source-only gate, canonical Origin/Host,
   owner scope and lease, no Mark Unread, no send, and unchanged Gmail/IMAP.
3. Verify only `_PRODUCTION_READ_FLAGS_RELEASED = True` and
   `MICROSOFT_AUTO_MARK_READ_RELEASED = true` are enabled.
   Never disable `MICROSOFT_READ_ONLY_PILOT`.
4. Run scoped backend/frontend/OAuth tests and Vite build; verify Git tree,
   merge by fast-forward after comparing `main`, deploy only after approval.
5. On Production, reconnect the Microsoft account to grant the newly
   requested `Mail.ReadWrite` consent. Existing read-only credentials
   cannot be silently promoted.
6. Test opening exactly one known unread Inbox message, verify read status
   in Outlook and the changed dot, then test other folders and that send and
   archive remain unavailable. Do not test with irreplaceable mail.
7. Keep the previous READY Production deployment/commit for rollback and
   do not enable automatic retries for unconfirmed Graph mutations.

## Explicitly out of scope

Reply, Reply All, Forward, Compose, Archive, Delete, Spam actions,
Mark Unread, AI Filtered routing, bulk actions and any additional
Microsoft scopes. These require separate review and approval.
