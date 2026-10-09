# Microsoft Graph integration — staged security handoff

## Status

This is an unpublished local feature branch until explicitly reviewed.
Current Microsoft helpers are not HTTP routes and do not connect any production account.

Implemented: Graph /me identity verification against OAuth email hint; bounded
read-only Graph Inbox and exact-message requests; required User.Read, Mail.Read
and offline_access scopes; owner-and-mailbox-scoped durable token namespace;
generation-bound connect, reconnect and rotating refresh; atomic CAS writes
without automatic expiry; race-safe concurrent refresh; fail-closed error paths.
Server-side microsoft_mailbox_access.py resolves the current Auth0 owner
and one exact connected mailbox before loading scoped tokens or using Graph.
The Microsoft OAuth callback uses microsoft_activation.py:
1) CAS-stage a disconnected owned mailbox; 2) CAS-persist an exact
owner/mailbox/generation-bound token; 3) CAS-activate only after both durable
readbacks. Failure must leave the mailbox disconnected, never report
Connected, and never erase an unrelated inbox. Initial activation stores
the signed OAuth issue timestamp and nonce while pending. A newer initial
grant can safely recover a stalled pending record; an older/equal-time grant
cannot revert a newer reservation. Successful activation clears both markers.
Reconnect is CAS-reserved by the authenticated owner before OAuth redirection:
it immediately disables the existing mailbox for Graph access, records the
new generation and reservation, and permits a newer reservation to supersede
an abandoned attempt. The callback matches the signed mailbox ID, verified
email and Graph account ID, atomically rotates the existing scoped token (or
creates it if absent), and CAS-reactivates only after durable readback.
The Microsoft OAuth start requires an owner and a selected mailbox email.
No Microsoft tokens or authorization metadata are returned in browser JSON.

## Implemented activation invariants

1. Re-resolve the current authenticated Cuevion member at the callback.
   Never trust an owner email or mailbox ID supplied by the browser.
2. Resolve the exact owned managed mailbox from the authenticated member config.
   For a new account create a pending owned record and secure generation;
   for reconnect verify the previous generation and account identity.
   Active Microsoft managed mailbox records MUST persist and read back:
   oauthOwnerEmail, oauthOwnerUserId, oauthWorkspaceId, oauthAccountId,
   oauthCredentialGeneration, provider=microsoft, connectionMethod=oauth,
   connected=true and connectionStatus=connected. Never infer these fields
   from the browser or from a stale OAuth state.
3. Verify Graph /me id and email before persisting scoped credentials.
   Only switch to connected after durable token and mailbox readbacks agree.
   Reconcile any partial failures and do not falsely report connected.
4. For every operational request, use the strict
   microsoft_mailbox_access.resolve_owned_microsoft_mailbox guard, which
   calls resolve_owned_managed_inbox_record(include_member_authority=True).
   It enforces owner role, exact Auth0 user+workspace IDs, provider,
   connected state, exact email/account ID and token generation.
   Never expose access_token or refresh_token in browser JSON.
5. Add the actual Graph provider adapter for Inbox, Sent, read/unread, reply,
   send, trash and threading using existing Cuevion mailbox contracts.
6. Configure Microsoft credentials in Vercel only when ready. Never commit
   or print tokens, secrets or OAuth state.
7. Test in Preview and with a real Microsoft account before PR or merge.

## Do not shortcut

- The initial callback may report Connected only after staged config,
  scoped token and final active config have all been verified.
- Interrupted initial activation can be recovered using a fresh grant,
  only if the signed issue time is newer and owner/account binding matches.
- Interrupted Microsoft reconnect can be recovered by a new reserved
  generation. A stale callback cannot override a newer reservation.
- During any reconnect reservation, the mailbox is disconnected and the
  owner-bound Graph access gate rejects it before token or Graph calls.
- Reconnect writes do not update Google or unrelated mailbox records.
- Never revive legacy Microsoft token storage keyed only by email.
- Never skip CAS for reconnect or refresh.
- Keep Out of Office and Production branches untouched during Microsoft work.
- Do not use Codex and another agent simultaneously on the same Git branch.



## Read-only Microsoft Inbox transport — implemented locally

- `api/inboxes/fetch-microsoft.py`: POST-only, max 25 bounded messages,
  strict payload and member-owned mailbox authority.
- `api/inboxes/fetch-microsoft-message.py`: POST-only exact provider message
  read; never follows browser URLs or unverified OData nextLink.
- `api/inboxes/microsoft_http.py`: bounded JSON/headers, security/no-store
  response headers and safe status/error mapping.
- `api/inboxes/microsoft_inbox_projection.py`: Graph metadata projected
  into Cuevion LiveInboxMessageSnapshot with immutable Graph ID,
  INBOX membership and unread marker. List items have an EMPTY body rather
  than pretending Graph bodyPreview is the complete text.
- Exact-message content is normalized to plain text; returned Graph HTML,
  tracking images and fabricated attachment objects are not passed through.
- `src/lib/inboxConnectionApi.ts` exports `fetchMicrosoftInbox` and
  `fetchMicrosoftMessage` with strict mailbox/source validation and
  reject-by-default response parsing.
- Tests: `api.inboxes.test_microsoft_http` and
  `src/lib/microsoftInboxApi.test.ts`, plus existing authority and OAuth
  regressions. No real mailbox was connected or fetched by these tests.

## Desktop workspace integration — read-only local implementation

The existing WorkspaceShell now detects an authenticated, managed Microsoft
mailbox and mounts MicrosoftMailboxReadOnlyView IN THE NORMAL WORKSPACE
NAVIGATION. Other providers still use the original MailboxView and its
unmodified Gmail/IMAP synchronization, archive, actions and snapshots.

- The Microsoft view is deliberately independent of mailboxStore,
  Gmail/IMAP local snapshots, threading, priority and unread mutations.
- When connected, it loads the 25 newest messages on mount, refreshes every
  two minutes while mounted and offers a manual Refresh button.
- Selecting one inbox item requests exact content via fetchMicrosoftMessage;
  it never treats Graph list previews as complete bodies.
- Async generation tickets invalidate old list/detail results on refresh,
  selection changes and unmount. Server returns are additionally checked for
  the same exact owned mailbox authority AFTER the remote Graph request,
  preventing responses from racing a reconnect or ownership change.
- Connection-status transitions remount the view; any 401/reconnect-required
  result clears local message content and requests reauthorization.
- The view has no Send, Reply, Delete, Move or attachment interaction.
- No real Microsoft account has yet been connected in a browser.

Important: MobileWorkspaceShell is STILL separate and has not been wired for
Microsoft read-only views. Microsoft unread counts, priority, archive/Sent,
pagination beyond the newest 25, full HTML/attachments, reply/send and mailbox
mutations are not integrated. Do not claim the general workspace Inbox is
fully Microsoft-enabled until those release gates are completed.

## Remaining release gates (not ready for Preview or Production)

1. Validate the complete Microsoft OAuth POST and browser callback
   flows with request-level fixtures and state-collision/recovery cases.
   Current unit tests cover signed-state roundtrip, owner reservation, tokens
   and partial-failure safety.
2. Integrate Microsoft into MobileWorkspaceShell and, when the
   current read-only UI is accepted, reconcile its Inbox state with normal
   Cuevion mailbox presentation without using Gmail/IMAP-only assumptions.
   Add read/unread, Sent, reply, send, trash, pagination, attachment handling,
   and unread/priority calculations separately. The reconnect client request
   builder exists, but connection UI is not fully integrated.
3. Validate normal user-config presentation-only saves preserve every
   server-held Microsoft account/user/workspace/generation field.
4. Configure Microsoft app registration through Vercel environment
   variables; complete Preview and real-account acceptance testing.
5. Existing broad Gmail ownership suite has 87 baseline failures, identical
   on the previous Microsoft commit; treat as separate known test debt until
   repaired. Narrow Gmail OAuth regression must remain green.

No production change, GitHub push, or Vercel deployment was performed by
the current activation development step.
