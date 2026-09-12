# Notification controls and Collaboration sender metadata

Local frontend polish only. No push, deployment, production access, backend changes, dependency installation, new request path, polling change, or authority change.

Baseline passed: perf-1; HEAD and remote main both 611b971e50b1bec17cb2b69237a5b490f5b1f99d; staging empty; exactly the three required protected working-tree entries. Protected contents were not accessed. All path reads and diffs were scoped to known non-protected frontend files. Python helpers used PYTHONDONTWRITEBYTECODE=1.

## Trace and changes

- Dashboard preview and Notifications page both render ServerNotifications.tsx, with preview=true selecting Dashboard. Refresh uses the same button and unchanged store.refresh handler in both. Refresh lacked explicit text/background colors and useful hover styling. Retry likewise inherited text color. Both now use one local secondary-action class string with existing workspace text/card/border/hover/accent tokens. Loading still disables Refresh through the exact prior expression; muted text remains readable without opacity reduction. Retry behavior is unchanged.
- Theme tokens were traced in frontend/src/index.css and compared with frontend/src/components/ui/DesktopActionButton.tsx. No global CSS or shared button component changed. Load more and notification rows are outside this narrow change.
- Collaboration Email metadata was assembled as sender + " <" + from + ">" in renderThreadMessage. The already-loaded from field can contain a complete formatted display name and address, so this nested the same name and brackets.
- The normal mail reader displays the canonical sender field directly; it has no combined display formatter. Existing getReturnedReplySenderAddress in returnedReplyEvidence.ts already extracts/normalizes the sender address. The new presentation-only formatCollaborationEmailSender reuses it, keeps the canonical sender display prefix, omits duplicate email-as-name, and avoids nested brackets. No new raw-header parser or source mutation was introduced.
- Only the Collaboration From definition-list value calls the new helper. Other metadata, email body, sanitization, inline images, links, attachments, normal reader, tabs, chat, People and Resolve/Reopen remain unchanged.

## Verification

- 10 sender cases pass: name+email, email-only, name-only, blank, already formatted, nested malformed input, and immutable source fields. The actual extracted Email renderer also displays a formatted canonical from value cleanly in the browser.
- 8 browser scenario groups pass in preinstalled Chrome via preinstalled Playwright. Both notification variants were exercised in ready/loading/retry states, light and dark themes. Hover/focus are visible; Refresh/Retry each preserve one handler invocation. No browser errors or external requests occurred.
- Computed button-text contrast after compositing alpha colors: light 5.21–14.75:1; dark 7.64–12.24:1, including disabled Refresh. Retry passes the 4.5:1 check. Three screenshots were inspected visually.
- 27 frontend regression suites pass, including the sender test, relevant C3D/exact-message/store tests, Collaboration read/write/guest tests, email security/disclosure/attachment tests, and WorkspaceShell performance.
- 33 exact baseline comparisons pass, including unchanged notification code outside classes, the full email renderer except its From value, CollaborationContextControls, chat/access/guest components, lifecycle/send handlers, sanitizer/iframe code, notification authority and displayed acknowledgement ordering.
- Full frontend app and node TypeScript configurations pass with --noEmit.
- React review found unconditional hooks, unchanged effects/event handlers, a pure presentation helper and no new state or effects.

Browser checks use local isolated fixtures and an in-memory store; no real inbox, attachment download, backend or production was accessed. This is not a live backend or assistive-technology test. Existing Browserslist data-age advisory was non-blocking; no packages were updated.

## Reproduce

From the repository root:

```sh
node frontend/fixtures/notification-controls/build.mjs
node frontend/fixtures/collaboration-context/build.mjs
node frontend/fixtures/notification-controls/serve.cjs
# Separate terminal:
node frontend/fixtures/collaboration-context/serve.cjs
# Separate terminal:
node frontend/fixtures/notification-controls/verify.cjs
node frontend/fixtures/notification-controls/regressions.cjs
node frontend/fixtures/notification-controls/verify-freeze.cjs
./node_modules/.bin/tsc -p frontend/tsconfig.app.json --noEmit
./node_modules/.bin/tsc -p frontend/tsconfig.node.json --noEmit
```

Loopback servers use ports 4180 and 4176. Bundles are written only to /private/tmp. No installation is needed.

## Files inspected

Primary reads: WorkspaceShell.tsx (notification mounting, metadata, normal reader and sender/recipient helpers), ServerNotifications.tsx, DesktopActionButton.tsx, returnedReplyEvidence.ts, exactMailboxMessageApi.ts, liveInboxSnapshots.ts, index.css, and the existing collaboration-context build/harness/serve/verify/regressions/freeze scripts. Existing frontend configuration and directly imported frontend dependencies were used by compilation. Exact regression suite inventory:

- frontend/src/components/workspace/WorkspaceShell.collaborationEmailSender.test.ts
- frontend/src/components/collaboration/CollaborationChat.test.ts
- frontend/src/components/collaboration/CollaborationAccessPanel.test.tsx
- frontend/src/components/workspace/WorkspaceShell.collaborationOwnerRead.test.ts
- frontend/src/lib/collaborationOwnerSourceLocator.test.ts
- frontend/src/lib/collaborationOwnerReadApi.test.ts
- frontend/src/lib/collaborationOwnerWriteApi.test.ts
- frontend/src/lib/collaborationGuestApi.test.ts
- frontend/src/App.collaborationGuestRoute.test.ts
- frontend/src/lib/collaborationSummaryApi.test.ts
- frontend/src/lib/collaborationSummaryStore.test.ts
- frontend/src/components/workspace/WorkspaceShell.collaborationIdentity.test.ts
- frontend/src/lib/notificationsApi.test.ts
- frontend/src/lib/notificationNavigation.test.ts
- frontend/src/lib/workspaceNotificationStore.test.ts
- frontend/src/components/workspace/WorkspaceShell.serverNotifications.test.ts
- frontend/src/lib/collaborationGuestInviteLink.test.ts
- frontend/src/components/collaboration/ExternalCollaborationGuestView.test.tsx
- frontend/src/lib/exactMailboxMessageApi.test.ts
- frontend/src/lib/exactMailboxMessageStore.test.ts
- frontend/src/components/workspace/WorkspaceShell.performance.test.ts
- frontend/src/components/workspace/WorkspaceShell.emailHtmlStage.test.ts
- frontend/src/components/workspace/WorkspaceShell.threadAppleMailStructure.test.ts
- frontend/src/components/workspace/WorkspaceShell.threadAppleMailDisclosure.test.ts
- frontend/src/components/workspace/WorkspaceShell.fullMessageModal.test.ts
- frontend/src/components/workspace/WorkspaceShell.sentTimestamp.test.ts
- frontend/src/components/workspace/WorkspaceShell.replyAttachments.test.ts

## Changed files

- frontend/src/components/workspace/ServerNotifications.tsx
- frontend/src/components/workspace/WorkspaceShell.tsx
- frontend/src/components/workspace/WorkspaceShell.collaborationEmailSender.test.ts
- frontend/fixtures/collaboration-context/harness.tsx
- frontend/fixtures/notification-controls/harness.tsx
- frontend/fixtures/notification-controls/build.mjs
- frontend/fixtures/notification-controls/serve.cjs
- frontend/fixtures/notification-controls/verify.cjs
- frontend/fixtures/notification-controls/regressions.cjs
- frontend/fixtures/notification-controls/verify-freeze.cjs
- frontend/fixtures/notification-controls/browser-results.json
- frontend/fixtures/notification-controls/regression-results.json
- frontend/fixtures/notification-controls/freeze-results.json
- frontend/fixtures/notification-controls/notifications-dark.png
- frontend/fixtures/notification-controls/notifications-light.png
- frontend/fixtures/notification-controls/email-sender.png
- frontend/fixtures/notification-controls/HANDOFF.md

## Screenshots

- [notifications-dark.png](/Users/rutger/cuevion-app/frontend/fixtures/notification-controls/notifications-dark.png)
- [notifications-light.png](/Users/rutger/cuevion-app/frontend/fixtures/notification-controls/notifications-light.png)
- [email-sender.png](/Users/rutger/cuevion-app/frontend/fixtures/notification-controls/email-sender.png)

Local review: GO. Push/deploy: NO-GO per instruction. Requested commit message: polish notification controls and email metadata. Post-commit hashes, empty staging and final status are reported in the task response.
