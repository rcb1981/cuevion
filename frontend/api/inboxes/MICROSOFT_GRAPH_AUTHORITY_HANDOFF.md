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
The Microsoft initial OAuth callback now uses microsoft_activation.py:
1) CAS-stage a disconnected owned mailbox; 2) CAS-persist an exact
owner/mailbox/generation-bound token; 3) CAS-activate only after both durable
readbacks. Failure must leave the mailbox disconnected, never report
Connected, and never erase an unrelated inbox.
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
- Interrupted initial activation with the same generation may be retried
  safely. A NEW authorization generation against a stale pending record is
  intentionally blocked until explicit fail-closed recovery is implemented.
- Never revive legacy Microsoft token storage keyed only by email.
- Never skip CAS for reconnect or refresh.
- Keep Out of Office and Production branches untouched during Microsoft work.
- Do not use Codex and another agent simultaneously on the same Git branch.

## Remaining release gates (not ready for Preview or Production)

1. Recover interrupted pending Microsoft activations when the next OAuth
   authorization uses a fresh generation: distinguish unpersisted/stale
   tokens with exact owner-bound durable CAS; never overwrite a foreign token.
2. Implement Microsoft reconnect in connect-oauth and callback using the
   prior active generation, matched Graph identity, and CAS-updated account
   config. Do not re-enable operation on partial reconnect.
3. Wire authenticated Graph handlers into the workspace mailbox contracts,
   including Inbox, read/unread, Sent, reply, send, trash and paging.
4. Validate OAuth start and browser callback integration with full request
   fixtures, and test production user-config presentation-only saves preserve
   all server-held Microsoft account and generation fields.
5. Configure the Microsoft app registration through Vercel environment
   variables, then complete Preview and real-account acceptance testing.
6. Existing broad Gmail ownership suite has 87 baseline failures, identical
   on the previous Microsoft commit; treat as separate known test debt until
   repaired. Narrow Gmail OAuth regression must remain green.

No production change, GitHub push, or Vercel deployment was performed by
the current activation development step.
