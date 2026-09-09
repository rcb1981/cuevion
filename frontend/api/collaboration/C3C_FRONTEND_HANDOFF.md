# C3C frontend trace and C3D handoff

## Pre-edit trace

The paths and functions below were inspected before the guest send changes. The
existing Notifications UI, read storage, count, and click behavior remain frozen
through C3C.

- `frontend/src/components/workspace/WorkspaceShell.tsx`:
  `buildVisibleNotificationItems` derives notifications from locally loaded
  Inbox/Filtered messages and the shared Collaboration projection. It reads
  `MailMessage.collaboration`, current viewer identity/name, and
  `collaborationLastSeenByKey`; it returns at most 24 items. Its local kinds are
  `collaboration`, `reply`, and `mention`. It creates rows for start, resolution,
  replies/Internal replies, and existing local mention entries. Self suppression
  compares display names and normalized email/identity values. These rules are
  not the C3C server recipient authority.
- `buildGroupedNotificationItems` groups adjacent replies for the same mail over
  a 15-minute window. `buildPrioritizedNotificationItems` sorts local mentions,
  replies, then Collaboration events. `VisibleNotificationItem` has local
  mailbox/message IDs, optional Collaboration message ID, copy and an action.
- `CUEVION_NOTIFICATION_READ_STORAGE_KEY` and
  `buildNotificationReadStorageKey` produce
  `cuevion-notification-read:${workspacePersistenceScope}`. The
  `readNotificationIds` initializer/effects read and write a local string array.
  `markNotificationSourceIdsRead` updates that array; opening Notifications
  marks all currently rendered source IDs read, and
  `handleOpenNotificationItem` marks the clicked item before invoking its action.
- `unreadNotificationIds` and `notificationUnreadCount` count locally grouped
  items whose source IDs are absent from the stored array. The sidebar receives
  this count; the Dashboard `NotificationsPreviewBlock` and Notifications
  workbench receive `prioritizedNotificationItems`. No C3C authority is wired.
- `NotificationNavigationRequest`, `handleOpenNotificationNavigation`, and the
  mailbox component's consuming effect route through loaded mailbox/message IDs,
  optionally open the Collaboration overlay, and highlight a local
  `collaborationMessageId`. The handler requires a locally loaded target. This
  is the exact place C3D must integrate canonical provider source routing.
- `frontend/src/lib/collaborationGuestApi.ts`:
  `replyToGuestCollaboration(text, csrfToken)` originally posted only
  `{operation: "reply", text}` with the existing CSRF header and guest cookie.
  It provided no logical-send identity.
- `frontend/src/components/collaboration/ExternalCollaborationGuestView.tsx`:
  `handleReply` originally posted the draft directly and cleared it after
  success. Network failures and `recoverSessionAfterReplyFailure` preserved the
  draft but had no stable retry key. The disabled button depended on the next
  render and there was no synchronous in-flight latch. Therefore a lost success
  response followed by manual retry could append a duplicate guest message.
- Direct existing guest coverage was inspected in
  `frontend/src/lib/collaborationGuestApi.test.ts`,
  `frontend/src/components/collaboration/ExternalCollaborationGuestView.test.tsx`,
  and `frontend/src/components/collaboration/ExternalCollaborationGuestView.web.test.mjs`.

## Guest send integration

C3C adds one Web Crypto 32-byte base64url key per logical send. The public reply
request adds `idempotencyKey`, with strict canonical unpadded base64url format
`^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$`. The same in-memory send retains its key
through uncertain responses and CSRF refresh. A changed draft starts a new send;
successful acknowledgement clears the send, so a later identical message gets
a new key. A synchronous in-flight latch prevents overlapping submissions.
Session end/logout clears send memory. No retry loop or persistent guest storage
is introduced. The server must validate and deduplicate this key atomically.

## Verified server interface

All operations use `POST /api/notifications`, `Content-Type: application/json`,
`Accept: application/json`, cookie credentials, and `cache: "no-store"`. The route
does not accept a query string. It uses the generic Cuevion Auth0 session,
canonical current account and workspace authority, and the generic Team HTTP
security contract: exact canonical Host and trusted Origin plus non-simple JSON
requests. It does not reuse Collaboration owner rollout/allowlist permissions or
the owner-specific CSRF token. Current Team entitlement is checked each time.
An external guest cookie alone returns 401; stale Team entitlement returns 403.
The body limit is 2,048 bytes. All responses are `no-store` and `nosniff`.

| Operation | Exact request body | Successful response |
| --- | --- | --- |
| Summary | `{"operation":"summary"}` | `{"v":1,"unreadCount":0}` |
| List | `{"operation":"list","limit":50,"cursor":null}` | `{"v":1,"notifications":[],"nextCursor":null,"unreadCount":0}` |
| Mark read | `{"operation":"mark_read","notificationId":"ntf_…"}` | `{"v":1,"notification":{…},"unreadCount":0}` |

List `limit` is optional, defaults to 50, and must be an integer from 1 to 50.
`cursor` is optional or null for the first page. Use the returned opaque string
unchanged for the next page; it is bounded to 1,024 ASCII bytes and signed for
the authenticated user/workspace. Pages descend by the exclusive
`createdAt, notificationId` ordering. New arrivals above the cursor cannot cause
loops; expiration or bounded eviction may remove older rows between requests.
Malformed/non-advancing cursors fail safely. Stop at `nextCursor: null`.

The server derives the user and workspace. Do not send `userId`,
`recipientUserId`, `workspaceId`, mailbox, source, actor, email, or Redis keys in
these requests. Unknown fields are rejected. Foreign, missing, or expired
notification IDs return 404. Corrupt/unavailable authority returns 503.
Mark-read sets server `readAt` once, decrements unread once, and repeats return
the same timestamp without extending retention. There is no mark-all-read.

Dedicated limits are scoped to user, workspace, and operation: summary/list
60 per minute with burst 20; mark-read 30 per minute with burst 10. The limiter
uses one outer EVAL, with 3 nested commands for a new bucket and 4 for an existing
bucket. These are isolated local measurements. No polling is added by C3C.

## Notification DTO and exact routing

The DTO contains exactly:

```ts
type NotificationDto = {
  v: 1;
  notificationId: string; // "ntf_" plus 40 lowercase hexadecimal characters
  workspaceId: string;
  kind: "collaboration_started" | "participant_added" | "shared_message" | "internal_note";
  collaborationId: string;
  mailboxId: string;
  sourceRef:
    | { provider: "google"; providerMessageId: string }
    | { provider: "custom_imap"; folder: "INBOX"; uidValidity: string; imapUid: string };
  activityId: string | null;
  actor:
    | { type: "cuevion_user"; userId: string; displayName: string }
    | { type: "external_guest"; displayName: string };
  createdAt: number; // milliseconds
  expiresAt: number; // milliseconds
  readAt: number | null; // milliseconds, server authority
};
```

`recipientUserId` remains in the canonical server record and is omitted from
the DTO. No shared message/Internal Note body, email, guest bearer, invite token,
secure link, CSRF, or session is copied into notifications. `activityId` is the
exact canonical Collaboration message ID for Shared/Internal/guest replies; it
is null for start and participant-add events without an activity record.

C3D must route using `workspaceId`, `mailboxId`, `sourceRef`, `collaborationId`,
and `activityId`. Google uses the exact provider message ID. IMAP uses exact
INBOX, UID validity and UID. Do not resolve through subject, sender, timestamp,
or fuzzy matching. Display copy may use the bounded actor display-name snapshot.
Server recipient selection and no-self suppression use canonical `usr_*` IDs.
External guests are never internal app notification recipients.

## C3D integration plan

1. Add strict DTO/response parsing and a client for this neutral route. Derive
   authentication and workspace from the current session, and clear cached UI
   state when that session/workspace changes.
2. Replace `buildVisibleNotificationItems` as notification authority with paged
   server records. Keep local mailbox and Collaboration loading independent so
   a notification can exist before its source mail is loaded. Do not continue
   deriving resolution or mention rows from local Collaboration projections.
3. Replace `notificationUnreadCount` with the lightweight summary authority for
   the sidebar and Dashboard. C3D chooses explicit request moments; C3C adds no
   polling, startup hydration, per-row fetches, or cleanup loops.
4. Replace local `readNotificationIds`, local-storage persistence, and the
   current automatic section-open marking with explicit server `mark_read`
   calls according to C3D's read-on-open behavior. Only a successful server
   response establishes read state/count. Preserve idempotency for repeated
   opening and safe handling of expired/foreign IDs.
5. Replace local-only click lookup in `handleOpenNotificationNavigation` with
   exact source routing and authorized Collaboration access. Target
   `activityId` when present, while handling expired/inaccessible mail without
   inventing a fallback target.

Visible Notifications UI, sidebar badge, Dashboard, local read IDs, and current
click navigation remain unchanged in C3C. Existing local mention behavior is
frozen; authoritative mentions belong to C3E and are not implemented here.
