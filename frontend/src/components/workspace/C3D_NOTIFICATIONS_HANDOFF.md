# C3D: server notifications and exact navigation

Local implementation on `perf-1`. Required baseline and commit parent:
`9ac9aafda5863cbd6844ffe1a6410579e9185715`. Local HEAD and live remote main
matched; staging was empty and only the three expected pre-existing entries
were present. No push or deployment is authorized by this task.

## Pre-edit trace

All paths below are relative to the repository root. The trace was performed
before editing the existing integration; C3C handoff documentation was also
cross-checked during verification.

| Required seam | Existing exact file / function |
| --- | --- |
| A, C, D: rows, Dashboard, workbench | `frontend/src/components/workspace/WorkspaceShell.tsx`: `buildVisibleNotificationItems`, `buildGroupedNotificationItems`, `buildPrioritizedNotificationItems`, `NotificationsPreviewBlock`, `DashboardView`, `WorkbenchView` |
| B, E, F: badge, read authority, section open | Same file: `WorkspaceSidebar`, `notificationUnreadCount`, `readNotificationIds`, `buildNotificationReadStorageKey`, `markNotificationSourceIdsRead`, Notifications section effect |
| G: old navigation | Same file: `NotificationNavigationRequest`, `handleOpenNotificationNavigation`, consuming `MailboxView` effect; remains for existing Team/Activity/Priority navigation |
| H: canonical Collaboration open | Same file: `openCollaborationOverlay`, `beginCollaborationOwnerRead`, `collaborationOwnerProjection`; `frontend/src/lib/collaborationOwnerReadApi.ts` and `collaborationOwnerApiTransport.ts` |
| I: activity | Same file: `highlightedCollaborationMessageId`, `collaborationMessageRefs`; canonical owner activity rows previously lacked those refs |
| J: identity | Same file: `hasAuthenticatedMemberAuthority`, authenticated `userId`/`workspaceId`, `learningStorageKey`, `workspacePersistenceScope`; `frontend/src/App.tsx` authenticated workspace activation |
| K, L: projection and loaded identity | Same file: `mailboxStore`, `setMailboxStore`, `mailboxStoreRef`, connection epochs, existing selection/location helpers; exact identity predicate in C3D0 client |
| M: C3C contract | `frontend/api/notifications/http.py`, `models.py`, `test_http.py`; no existing neutral frontend client at the attempted exact client paths, so added `notificationsApi.ts` |
| N, O: cold fetch / augmentation | `frontend/src/lib/exactMailboxMessageApi.ts`, `exactMailboxMessageStore.ts`, `frontend/api/inboxes/C3D0_EXACT_MESSAGE_HANDOFF.md` |
| P, Q: activation / rendering | Same WorkspaceShell file: active section branches, Dashboard, workbench, `fullMessageModalDialogRef`, canonical owner read/loading/error projection; `frontend/src/index.css` |

Additional directly inspected existing files: root `package.json`,
`frontend/package.json`, `src/App.tsx`, `frontend/src/main.tsx`,
`frontend/tsconfig.app.json`, `frontend/tailwind.config.js`,
`frontend/src/lib/useCollaborationSummaries.ts`,
`frontend/src/lib/collaborationOwnerSourceLocator.ts`,
`frontend/api/collaboration/models.py`,
`frontend/api/collaboration/C3C_VERIFICATION.md`,
`frontend/api/collaboration/C3C_FRONTEND_HANDOFF.md`,
`frontend/api/collaboration/test_lua_redis_integration.py`,
`frontend/api/collaboration/test_application.py`, and the exact test files listed
below. Compiler/transpiler imports use the existing protection guard.

Procedural exception: one initial `rg --files` filename query ran in the same
batch as reading the user's attachment, before its restrictions had been
processed. It was disclosed immediately. It returned package and handoff paths,
not protected filenames or protected contents. It was not repeated. The baseline also used a global quiet staged-diff check
on the empty index; that command was outside the requested command restriction
and returned no file names or contents. Subsequent staging checks use only
`git status --short` or exact allowlisted paths. No protected
contents were accessed, changed, diffed, restored, or staged. All later reads and
searches were scoped to exact non-protected files.

## One authority and request budget

`frontend/src/lib/workspaceNotificationStore.ts` owns scope, count, records by
ID, ordered loaded IDs, cursor, loading/errors, in-flight marks, first-page
refresh timestamp and request fencing. `useWorkspaceNotifications.ts` creates
one store for each authenticated account/workspace lifetime and shares it with
all three consumers. A scope change renders the new empty store immediately,
then initializes it; old requests are aborted and cannot publish. React
StrictMode effect replay does not duplicate the startup summary.

- Workspace startup: exactly one `summary`. No automatic list merely for count.
- First visible Dashboard preview or Notifications workbench: one `list`, limit
  50, cursor null. Concurrent consumers share the same promise.
- Dashboard reuses loaded data. A workbench remount/revisit after at least 60
  seconds refreshes the first page once. Both views expose explicit Refresh.
- Load more: one page per click with the server cursor unchanged. Concurrent
  clicks dedupe. No infinite scroll or automatic draining. Invalid/non-advancing
  or cycling cursors fail safely; already loaded rows survive page errors.
- Successful target display: one mark-read for an unread record. Reopening a
  read record sends no mark. Successful server DTO/count replace the local
  projection; there is no optimistic count decrement or summary refetch.
- No polling, intervals, focus/visibility listeners, per-row requests or
  Collaboration-mutation notification refresh. Cross-browser notifications
  appear on a later explicit refresh/revisit; this is expected v1 behavior.

The sidebar reads `notifications.state.unreadCount`, retaining its `99+` format;
it supports counts greater than 50 (the C3C limit is 1,000). Dashboard shows five
records; workbench shows the loaded pages. Both use `ServerNotifications.tsx`
and the same handler/store, with loading, safe errors/retry, empty state and
native keyboard-actionable rows. Unread is visible text, not color alone.

All local notification synthesis, grouping, local resolution/reply/mention
rows, local read arrays, persistence effects and section-open mark-all behavior
were removed. Existing stored read IDs are neither used nor migrated. Existing
Team Activity and unrelated Collaboration mention UI were not redesigned.

## Exact navigation and read point

`frontend/src/lib/notificationNavigation.ts` captures immutable notification,
account/workspace, mailbox configuration revision and generation. Same-request
activations dedupe; another activation cancels the old request. Every async
boundary checks current scope/generation/configuration. One 45-second deadline
bounds navigation; it is not a refresh timer and never retries.

The loaded-message Map is rebuilt only when the mailbox projection changes.
Lookup is O(1), keyed by mailbox/provider/source, with renderer-ID collisions
failing closed. It never uses subject, sender, timestamp, thread inference or
an approximate message ID. Google requires the provider message ID. IMAP
requires mailbox, INBOX, UIDVALIDITY and UID.

A missing loaded exact target calls C3D0 once and checks both returned source and
message identity again. The existing C3D0 seed and publication reducer augment
the existing mailbox inside its current-state updater. Other messages/folders
remain; an existing exact message is not duplicated. The integration does not
request a full mailbox refresh. C3D0 backend/client/helper are unchanged.

The mail view selects the exact source and confirms its visible, matching full
message modal. Only then does the navigator read the notification's exact
Collaboration ID through the canonical owner read client; it never looks up
whatever Collaboration happens to be active on that mail. Historical/resolved
IDs are accepted. Wrong Collaboration/mailbox IDs and missing/duplicate activity
IDs fail closed.

The existing canonical Collaboration overlay receives the verified DTO. If an
activity ID exists, its canonical rendered element must be visible, scroll into
view and receive focus. A subsequent animation frame confirms that the target
is still displayed and current before mark-read. Null activity still requires
the exact source and exact visible Collaboration. Failure never starts mark-read.
A mark-read failure keeps both readAt and count unchanged and shows safe feedback
above open modals. Successful reads use the returned readAt/count, including
idempotent responses. Overlapping different marks are serialized; earlier
summary/list responses cannot resurrect a successfully read row/count.

The existing owner transport had a 403 retry. Its options now support
`retryForbidden: false`, cancellation and a current-request callback for this
navigation path, including the CSRF-to-read boundary. Other callers keep their
existing retry behavior. No Collaboration backend behavior changed.

Notification copy uses only the server actor snapshot:

- Actor started a collaboration.
- Actor added you to a collaboration.
- Actor added a shared message (Cuevion user).
- Actor replied to a collaboration (external guest).
- Actor added an internal note.

No body/note/email/token field is rendered. An email-shaped display name uses
"Someone" instead of exposing the address. Safe failures cover unavailable
mailboxes/messages, provider mismatch, UIDVALIDITY change, expired/revoked
Collaboration access, missing activity and unavailable notification service.

## Reproducible local verification

New focused tests (148 assertions/cases across six suites):

- `frontend/src/lib/notificationsApi.test.ts`: 45, strict C3C DTOs, list/count/read
  envelopes, exact cursor/order, copy and safe HTTP failures.
- `frontend/src/lib/workspaceNotificationStore.test.ts`: 26, startup/dedupe,
  count, pagination, scopes, stale responses, read authority and concurrency.
- `frontend/src/lib/notificationNavigation.test.ts`: 41, loaded/cold Google and
  IMAP, C3D0 augmentation, wrong/missing targets, no fallback, races, deadline.
- `frontend/src/lib/notificationOwnerRead.test.ts`: 3, exact canonical transport,
  no 403 retry, cancellation between CSRF and read.
- `frontend/src/components/workspace/WorkspaceShell.serverNotifications.test.ts`:
  23, removal of local authority and execution of the actual display effects with
  DOM/frame fixtures, including focus-before-acknowledgement.
- `frontend/src/components/workspace/ServerNotifications.test.ts`: 10, real React
  markup, accessible unread/buttons, loading/empty/error/retry and preview bounds.

Existing `WorkspaceShell.activity.test.ts` and
`WorkspaceShell.collaborationIdentity.test.ts` assertions were updated only where
C3D intentionally replaces local notification synthesis/renderer behavior. Their
Team/identity coverage remains.

`ServerNotifications.web.test.mjs` runs the actual component/hook under React
StrictMode with local mock HTTP and installed Chrome: eight browser checks,
including one startup summary, shared first-page request, 5/50 rows with count 87,
keyboard activation, one-page Load more, error recovery, scope switch and 375px
layout. All non-local browser traffic is blocked. Run from `frontend` with
`PLAYWRIGHT_MODULE` pointing to the already installed Playwright module.

Requirement coverage: 1–18 store/API/browser; 19–26 structural removal and real
shared rendering; 27–47 navigator/C3D0/publication; 48–59 navigator/store and actual
MailboxView effects; 60–75 copy/React/browser; 76–89 existing regression suites,
canonical owner transport, full TypeScript and the local Redis/guest suites.
These are local fixtures and isolated Redis checks, not Production evidence.

C3D0 frontend regressions: 32 strict client and 21 publication tests. Existing
18-suite frontend regression set also passes (owner read/write/source, external
guest client, summaries/store/hook, Priority source/adapter/copy, mailbox identity,
polling/unread intent, Collaboration Priority, full-message modal, performance).
The additional Activity and Collaboration-identity suites pass. Performance
retains identity work 10,000 to 100 and keyword scans 29,000 to 2,900 in the existing
fixture. Mailbox polling code is unchanged. Guest browser suite: 55 scenarios.

Full frontend TypeScript: 95 application roots / 157 sources and two config roots
/ 57 sources, zero diagnostics. Root-project TypeScript and broad npm test are
excluded; the guarded frontend runner prevents protected filesystem access.

Local backend regression run uses repository `venv/bin/python` (Python 3.11),
`PYTHONDONTWRITEBYTECODE=1`, and isolated Redis Unix sockets with TCP disabled.
Selected modules: `api.notifications.test_http`, `test_rate_limit`,
`test_recipients`, `test_store`; `api.collaboration.test_notifications`,
`test_discovery_repair`, `test_summary`, `test_application`, and
`test_lua_redis_integration`. The 122 Lua cases include C2G5/C2G6 replacement,
revocation, guest session/reply, resolve/reopen and atomicity. Final result: **314 tests passed**, zero failures/errors/skips, zero protected
filesystem attempts and zero non-local socket attempts. No backend sources
are edited. The broad protected-path import-safety/OAuth tests are excluded.

Preliminary attempts found the standard Python lacked cryptography and local
socket creation required sandbox escalation. A bundled Python 3.12 run exposed
the already documented Unicode-version fixture mismatch; final verification
uses the existing repository Python 3.11 without changing fixtures or installing
anything. No Python invocation enables bytecode writes.

Detailed run outputs are in `/private/tmp/c3d-*-tests.txt`,
`/private/tmp/c3d-frontend-regressions.txt`, `/private/tmp/c3d-backend-final.txt`,
`/private/tmp/c3d-guest-browser.txt`, `/private/tmp/c3d-browser-final.txt` and
`/private/tmp/c3d-typecheck.txt`. Protection guards report zero protected-file
attempts; the Python guard rejects non-local sockets. No Production API/Redis
activity, dependency installation, push, deploy or background cleanup occurred.

## Manual post-deploy plan — prepared, NOT executed

**Production Notifications testing: BLOCKED UNTIL REDIS SERVICE IS RESTORED/UPGRADED.**
Current Upstash Free-tier monthly command quota is exhausted. No Production
notification test or Redis-dependent conclusion is justified in this task.

After service restoration and a separately authorized deployment:

1. Guest sends a Shared reply. Owner explicitly refreshes/revisits Notifications
   (use Refresh for an immediate revisit under 60 seconds). Verify badge and guest
   reply row. Click it; verify exact source mail, exact Collaboration and focused
   guest activity. Only then should readAt appear and count decrement once.
2. A second Cuevion user adds an Internal Note. Verify the other entitled user's
   notification, no self-notification for the author, no note leakage to guests,
   and exact activity opening/focus before read.
3. Repeat for both loaded/cold Google and IMAP. Exercise deleted source,
   UIDVALIDITY change, removed mailbox, revoked access, expired Collaboration and
   missing activity: no fallback, no read mutation. Repeat A→B and scope-switch
   races. Confirm another browser's arrivals appear only on explicit refresh.

Push/deploy remain **NO-GO in this task**, regardless of local test success.
