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

## Desktop workspace integration — explicit Inbox read-status actions

The existing WorkspaceShell now detects an authenticated, managed Microsoft
mailbox and mounts MicrosoftMailboxReadOnlyView IN THE NORMAL WORKSPACE
NAVIGATION. Other providers still use the original MailboxView and its
unmodified Gmail/IMAP synchronization, archive, actions and snapshots.

- The Microsoft view is deliberately independent of mailboxStore,
  Gmail/IMAP local snapshots, threading, priority and non-Microsoft actions.
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
- The view has NO Send, Reply, Delete, Move or attachment interaction.
- Opening a message never auto-marks it as read. The explicit
  Mark as read / Mark as unread button is the only Microsoft mutation
  exposed here, disabled during in-flight changes; it updates local UI
  only after exact server proof. An uncertain result suggests Refresh Inbox.
- No real Microsoft account has yet been connected in a browser.

Important: MobileWorkspaceShell is STILL separate and has not been wired
for Microsoft mailbox views. Global unread counts, priority, archive/Sent,
pagination beyond the newest 25, full HTML/attachments, reply/send and other
mailbox mutations are not integrated. The Microsoft view shows only unread
counts within its latest 25 messages; this is NOT the global badge count.
Do not claim the general workspace Inbox is fully Microsoft-enabled.

## Exact Microsoft read/unread action — implemented locally

- Microsoft Graph requires the delegated Mail.ReadWrite permission for
  PATCH of the message isRead property. Microsoft OAuth defaults and custom
  scopes now include this permission, while the operation still verifies
  it in the actual stored Microsoft token; otherwise it fails closed.
- New `microsoft_message_flags.py` uses ONLY
  /v1.0/me/mailFolders/inbox/messages/{exact immutable ID}. A scoped
  preflight GET proves Inbox membership, a single PATCH changes only
  isRead, and a second scoped GET confirms the result. The operation is
  idempotent when already satisfied. It never follows provider URLs.
- `mark_owned_microsoft_inbox_read` acquires the existing CAS-backed
  mailbox mutation lease, revalidates owner+mailbox generation before the
  PATCH and after the Graph readback, and releases the lease in a finally
  block. No write is attempted for unowned, pending, reconnecting or
  permission-deficient mailboxes.
- New POST-only `mark-microsoft-read.py` requires exactly mailboxId,
  providerMessageId and a boolean unread. Responses contain ONLY the
  confirmed exact provider message ID, unread flag and changed status.
- The client `setMicrosoftMessageUnread` does NOT retry unknown-status
  operations; it accepts no browser-supplied provider URLs or tokens, and
  strips arbitrary remote error text.
- MicrosoftMailboxReadOnlyView retains a segregated state model and only
  publishes confirmed flags to the selected message and its list row.
  Stale fetches, double-clicks and selection changes are suppressed. The
  UI provides an explicit Refresh Inbox path when confirmation fails.
- Fake Graph, lease/authority, HTTP and client tests cover permission
  denial, concurrency, PATCH failure, stale authorization and exact
  post-write status. No actual Microsoft mailbox was changed during tests.

## Microsoft Sent Items — read-only local integration

- Graph Sent Items is addressed only through the well-known folder
  /me/mailFolders/sentitems/messages and its folder-scoped exact-message
  route. No browser-controlled Graph URL or folder is accepted.
- The API modules microsoft_sent_graph.py, fetch-microsoft-sent.py,
  fetch-microsoft-sent-message.py and microsoft_http.py provide POST-only
  owner-bound reads, exact immutable IDs and capped 25-message lists.
- Sent projection uses the SENT folder and label, explicit To recipients,
  sentDateTime and no Inbox unread flag or fake Inbox UID set.
- List previews are not presented as complete bodies. Exact contents are
  converted to inert plain text, without embedding Graph HTML, tracking
  images or fabricated attachment objects.
- The same authenticated owner and token generation are checked before
  and after Graph I/O. Reconnect/ownership changes invalidate stale results.
- Frontend functions fetchMicrosoftSent and fetchMicrosoftSentMessage have
  separate strict response schemas. Desktop Inbox / Sent switching clears
  previous rows and selected bodies and invalidates outstanding requests.
- Mark as read / unread remains available only in Inbox, not in Sent.
- Gmail/IMAP mailboxStore, priority, archive, reply, send and trash logic
  remain untouched; no live Microsoft mailbox was contacted in tests.
- Still open: older-message paging, attachments, rich HTML, global
  unread counts, mobile and real-account send/reply/Sent acceptance.

## Remaining release gates (not ready for Preview or Production)

1. Validate the complete Microsoft OAuth POST and browser callback
   flows with request-level fixtures and state-collision/recovery cases.
   Current unit tests cover signed-state roundtrip, owner reservation, tokens
   and partial-failure safety.
2. Integrate Microsoft into MobileWorkspaceShell and, when the
   current read-only UI is accepted, reconcile its Inbox state with normal
   Cuevion mailbox presentation without using Gmail/IMAP-only assumptions.
   Add trash, pagination, attachment handling, and global unread/priority
   calculations separately. New message and reply sending are now locally
   implemented but not yet proven against a real Microsoft account.
   Inbox mark-read/unread also requires live acceptance testing.
   The reconnect client request
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

## Microsoft outbound draft, reply and send — local implementation

- Microsoft OAuth now requests delegated Mail.Send alongside Mail.ReadWrite.
  Operational sending verifies both scopes from the actual stored token;
  an older connection without either scope must reconnect.
- The new Graph transport creates a NEW draft via POST /me/messages,
  or a reply/reply-all draft via the exact Inbox source message endpoint
  POST /me/mailFolders/inbox/messages/{sourceId}/createReply (or
  createReplyAll). Graph owns Reply-To recipients and conversation threading;
  the browser cannot override a reply's recipients or source mailbox.
- For a new draft, server verifies subject, To and Cc recipients, body,
  immutable draft ID and isDraft status; for replies, server checks the
  Inbox source's exact conversation ID and validates the reply draft.
- Only after these checks can ONE POST /me/messages/{draftId}/send occur.
  Its HTTP 202 means Microsoft accepted the request, NOT delivered;
  neither Sent copy nor inbox delivery is claimed as verified.
- New private CAS-based outbound-attempt ledger reserves an unpredictable
  43-character operation ID before any Graph draft call, records the draft,
  marks send_started DURABLY before Graph send, and records acceptance
  afterward. No plaintext subject/body/recipients or OAuth tokens are
  persisted in the ledger. Attempts never automatically retry.
- Repeating the same operation ID can report an existing accepted receipt
  but must NEVER make a second Graph call. Uncertain drafts and sends
  remain blocked for that ID; user must check Drafts/Sent before any
  deliberate new message.
- Every operation runs under Cuevion's exact authenticated-owner mailbox
  authority and shared mailbox mutation lease. Owner/generation is rechecked
  before draft creation, before send and after the Graph response.
- New strict POST-only /api/inboxes/send-microsoft enforces canonical
  Host and same Origin, bounded JSON, exact allowed fields and safe errors.
  This currently REJECTS arbitrary Vercel Preview hosts by design;
  a verified Preview host policy is required before live Preview sending.
- A desktop New message / Reply composer performs one explicit Send click.
  It uses CSPRNG attempt IDs, no HTML or attachments, and freezes the
  attempted form to prevent automatic retries and double clicks. UI labels
  success only as accepted, with Sent copy and delivery still unconfirmed.
- All provider HTTP actions were tested with simulated Graph and CAS data;
  NO real message was sent in this build step.

## Next Microsoft acceptance and completion gates

1. Verify Microsoft app registration, exact OAuth redirect/scope behavior,
   a trusted Preview origin policy, and owner-bound login before live tests.
2. Run real Microsoft sandbox-account NEW and REPLY delivery acceptance:
   verify To/Cc, Reply-To behavior, thread identity, and eventual Sent copy.
   Explicitly test ambiguous HTTP 202/lost ACK versus durable attempt status.
3. Add Sent-copy proof/status reconciliation by immutable message ID;
   202 alone does not establish message delivery.
4. Add secure attachments, rich HTML where appropriate, drafts recovery,
   pagination, mobile view, general unread/priority and remaining actions.
5. Repair or classify 87 known baseline failures in the broad Gmail ownership
   suite before any Production merge.

## Microsoft Sent-copy confirmation — locally implemented

- Microsoft Graph's official immutable-ID guidance explicitly states that
  the ID of a draft created with Prefer: IdType="ImmutableId" can be used
  after sending to find the corresponding copy in Sent Items. The copy
  may appear asynchronously; HTTP 404 is PENDING, not a failed delivery.
- The new read-only microsoft_sent_copy_proof.py verifies one exact
  immutable draft ID at /me/mailFolders/sentitems/messages/{id} and
  confirms that its parentFolderId matches the real Sent Items folder ID.
  It requires isDraft=false and a valid sentDateTime; it does not search by
  subject, body or recipient, follow redirects, or issue a send.
- microsoft_outbound_ledger.inspect_outbound_attempt permits reading only
  the exact owner/mailbox/generation-bound attempt. It never returns
  hashed compose content, recipients, subject, body, access or refresh token.
- microsoft_sent_status_service and POST-only
  /api/inboxes/check-microsoft-sent-status return exactly one of:
  sent_copy_confirmed, sent_copy_pending or send_not_started,
  always with deliveryConfirmed=false. There is no automatic polling,
  draft recreation or send retry. Owner and token generation are rechecked
  after Graph I/O before publishing any proof.
- The Microsoft composer now offers an explicit Check Sent status button
  after a send attempt. This action is read-only and uses the same opaque
  attempt ID, not a newly created send request. Even a positive Sent match
  is not proof that the recipient received the message. The status is
  checked live on request, NOT yet persisted as a terminal ledger phase.
  Closing the composer loses its in-memory attempt ID; an owner-scoped
  attempt-history/recovery view is a remaining production acceptance gate.
- Unit tests cover missing/late Sent copies, invalid/mismatched IDs,
  incorrect folder identity, unauthorized access, reconnect races,
  malformed responses and safe HTTP/client status handling.

## Preview and live-test readiness (checked October 12, 2026)

- No Microsoft OAuth variables are presently configured in Vercel:
  MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET,
  MICROSOFT_OAUTH_REDIRECT_URI, MICROSOFT_OAUTH_TENANT and
  MICROSOFT_OAUTH_SCOPES are absent. This was checked using environment
  metadata only, without reading other values or secrets.
- The Microsoft app registration needs explicit consent for User.Read,
  Mail.Read, Mail.ReadWrite, Mail.Send and offline_access; the redirect URI
  must match the actual intended preview callback URL exactly.
- The existing outbound send and status endpoints require canonical Host
  app.cuevion.com and Origin https://app.cuevion.com. They will FAIL
  CLOSED on arbitrary Vercel preview URLs. A separately approved exact
  preview host/origin policy must be implemented and tested before
  real-account Preview acceptance. Do not add a wildcard/any-vercel.app rule.
- Use a controlled Microsoft test mailbox and designated test recipient.
  Verify initial connect, reconnect, New, Reply, CC, draft creation,
  HTTP 202 acceptance, delayed Sent-copy confirmation, expired token,
  ambiguous ACK and no automatic retry. Real sends require the user's
  explicit approval immediately before execution.
- The existing broad Gmail ownership suite has 87 baseline failures;
  the same cases failed on the prior Microsoft commit. These require
  separate remediation or classification before Production merge.

No remote GitHub push, Preview deployment, Production change or real send
was performed in this local Sent-copy confirmation step.
