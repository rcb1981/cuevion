# Collaboration C3P2B — context and lifecycle UX

Frontend-only local implementation. No push, deploy, production access, backend changes, dependency installation, or new request path. Protected contents were not accessed in this slice; the three protected entries appeared only in git status --short.

Baseline gate passed on perf-1: local HEAD and remote main both f5df01eeb5b1d5c03ac2154f31352a63d498e885; staging empty; exactly the three expected protected working-tree entries. The local commit uses the requested message: polish collaboration context and lifecycle ux. Post-commit hashes and final status are reported in the task response.

## Pre-edit trace

The complete source MailMessage was already available as activeCollaborationMessage, resolved through the source-mailbox-scoped getMessageById(activeCollaborationMessageId). This includes the body, bodyHtml, sender/from, recipients, timestamp/createdAt, and attachments. No new fetch was necessary.

| Trace | Existing implementation |
|---|---|
| A: workspace | WorkspaceShell.tsx MailboxView, Collaboration portal / WorkspaceModalLayer. |
| B: timeline | CollaborationChatTimeline in CollaborationChat.tsx, canonical chronological messages and strict identity alignment. |
| C: header | WorkspaceShell Collaboration title, subject, People, Close. |
| D: former source action | isCollaborationSourceOpen and the metadata-only collaboration-source-email disclosure, replaced by Email. |
| E: source object | activeCollaborationMessage and activeCollaborationSourceMailboxId; full loaded MailMessage. |
| F: full reader | Existing renderThreadMessage(message, "full", options) in WorkspaceShell. |
| G: HTML | resolveMessageBodyRenderMode; EmailHtmlStage, native HTML, compose HTML branches. |
| H: plain text | Existing renderPlainMessageParagraph and quote logic. |
| I: inline images | Existing CID attachment resolution and remote-image opt-in. |
| J: attachments | Existing renderAttachmentItem and handleAttachmentOpen. |
| K: metadata | Loaded sender/from/to/cc/subject; resolveDesktopThreadTimestamp plus existing Collaboration date formatter. |
| L: People | CollaborationAccessPanel; existing Team/guest mutation and transient-link state. |
| M: lifecycle | transitionCanonicalCollaboration; owner guard, generation fence, existing Resolve/Reopen requests. |
| N: status | getCollaborationOwnerStateLabel; one subdued status indicator. |
| O: notification open | Existing serverNotificationDisplay useLayoutEffect and exact canonical projection. |
| P: target | canonical activity ID, collaborationMessageRefs, highlightedCollaborationMessageId, existing displayed acknowledgement effect. |
| Q: scrolling | data-collaboration-scroll-body, mounted timeline, per-activity DOM refs. |
| R: Shared draft | Existing canonical owner Shared draft and send handler. |
| S: Internal draft | Independent existing Internal draft and send handler. |
| T: responsive | Bounded modal flex layout with fixed header/composer, one active outer content scroll region. |

## Result

Exactly two primary semantic tabs: Conversation (default) and Email. Arrow keys, Home, End, roving tab focus, aria-selected and labelled panels are supported. Normal context fencing resets the tab. Both drafts, selected composer mode, timeline DOM nodes, refs, highlights, and scroll survive switching. Email initializes only on its first selection and stays mounted afterward.

Email calls the existing full reader once for the loaded source message with context: "collaboration" and no actions. Context changes are restricted to an unboxed article, scoped DOM IDs, a wrapping metadata definition list, the existing date formatter, and theme-token colors for the remote-image notice. Body rendering, sanitization, iframe policy, inline resolution, links, attachment UI/handler and remote-image opt-in remain unchanged. Reply, Forward, Archive, Trash, Priority and other mailbox actions are absent.

People stays secondary and mounted, including across start-to-access transitions. Back returns to the selected primary tab and focuses People. The existing transient secure link survives toggling and still protects overlay closure. Team add, guest invite/copy/revoke/history flows pass.

The owner-only overflow exposes Resolve or Reopen. Resolve opens a native modal dialog, focuses Cancel, cycles focus within its controls, handles Escape without closing Collaboration, restores trigger focus, and invokes the unchanged lifecycle handler only after explicit confirmation. A synchronous double activation produces one Resolve request. Reopen directly uses the existing guarded handler. Resolved composer semantics remain unchanged.

Notification navigation selects Conversation and closes People in the existing layout effect. The display/scroll/focus/paint acknowledgement effect is byte-for-byte unchanged. Exact IDs, refs, missing-target behavior, and mark-read ordering pass both the existing VM integration tests and local browser checks.

## Verification

- 36 browser scenario groups passed in installed Chrome via installed Playwright; zero browser errors and zero external requests.
- 26 frontend regression suites passed, including Collaboration owner read/write, Shared/Internal, identity, guest API/view/route, invites/links, summaries, notification API/navigation/store, exact-message API/store, performance, HTML sandbox/security, mail disclosure, attachments, timestamps, and full message modal.
- 30 baseline freeze comparisons passed: 17 exact function bodies, the complete displayed acknowledgement effect, and 12 authority/chat/access source files.
- Full frontend TypeScript passed: tsc -p frontend/tsconfig.app.json --noEmit and tsc -p frontend/tsconfig.node.json --noEmit.
- Visual inspection passed at 1600×1000, 1280×800, 768×1024, 375×812. Conversation/Email, People, overflow and confirmation were captured and inspected in dark mode, including mobile. Header controls fit, metadata wraps, composer remains reachable, and no horizontal/page overflow was found.
- Focus checks cover keyboard tabs/menu, safe default, reverse and forward confirmation cycling, Escape isolation and focus restoration. Existing C3P2A own/null historical alignment tests and fixtures are retained unchanged.
- React review: hooks are unconditional, event listeners/dialog cleanup are bounded, keys follow the canonical context, no authority moves into the new controls, and no polling or timer was added.

All Python edit helpers used PYTHONDONTWRITEBYTECODE=1. No backend Python execution or bytecode-generating workflow was run. No broad .pyc scan was performed because path access is constrained.

## Verification limits

The browser fixture extracts actual modal JSX, reader, owner send/lifecycle handlers and displayed acknowledgement effect. It uses an in-memory canonical API stub with realistic shapes. Attachment activation verifies the exact existing message/attachment dispatch; it does not download a real attachment. Browser tests do not replace live backend verification. C3C and C3D1 backend suites were not run or accessed; their frontend contracts/regressions pass and their implementations are unchanged by this frontend-only change.

The existing external-HTML reader deliberately uses a bounded, opaque sandbox iframe with its own native internal scrolling. This remains intact; the Email panel supplies the single outer context scroller and no additional body or page scroller. Removing iframe scrolling would violate the existing security/layout contract. No physical-device keyboard, screen reader, Safari or Firefox session was run.

The fixture blocks external traffic and tests script/onerror removal, safe links, remote-image blocking and CID display. The small red artwork block is a synthetic inline PNG fixture. The email uses the sender-provided light canvas inside the dark app, consistent with the existing reader.

## Reproduce locally

From the repository root, use the installed dependencies:

```sh
node frontend/fixtures/collaboration-context/build.mjs
node frontend/fixtures/collaboration-context/serve.cjs
# In a second terminal:
node frontend/fixtures/collaboration-context/verify.cjs
node frontend/fixtures/collaboration-context/regressions.cjs
node frontend/fixtures/collaboration-context/verify-freeze.cjs
./node_modules/.bin/tsc -p frontend/tsconfig.app.json --noEmit
./node_modules/.bin/tsc -p frontend/tsconfig.node.json --noEmit
```

The fixture binds only 127.0.0.1:4176; its generated bundle is in /private/tmp/cuevion-c3p2b. The browser runner names the preinstalled Playwright module and Google Chrome paths. No install is needed. Build emitted the existing stale Browserslist data advisory; it did not block the build and no package update was attempted.

## Files inspected / validated

Application entry points inspected were WorkspaceShell.tsx, CollaborationChat.tsx, CollaborationAccessPanel.tsx, the existing confirmation pattern in WorkspaceShell, and the source/context/date/image/attachment/C3D code above. Existing collaboration-chat fixture scripts were read as the baseline for the new fixture. The frontend package/TypeScript/Tailwind configuration and theme CSS were inspected for local runtime and styling. Exact regression paths follow; their directly imported frontend dependencies were used by the checks:

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

Exact unchanged source-file comparisons:

- frontend/src/components/collaboration/CollaborationChat.tsx
- frontend/src/components/collaboration/CollaborationAccessPanel.tsx
- frontend/src/components/collaboration/ExternalCollaborationGuestView.tsx
- frontend/src/lib/collaborationOwnerReadApi.ts
- frontend/src/lib/collaborationOwnerWriteApi.ts
- frontend/src/lib/collaborationGuestApi.ts
- frontend/src/lib/collaborationGuestInviteLink.ts
- frontend/src/lib/notificationsApi.ts
- frontend/src/lib/notificationNavigation.ts
- frontend/src/lib/workspaceNotificationStore.ts
- frontend/src/lib/exactMailboxMessageApi.ts
- frontend/src/lib/exactMailboxMessageStore.ts

## Changed files

- frontend/src/components/collaboration/CollaborationContextControls.tsx
- frontend/src/components/workspace/WorkspaceShell.tsx
- frontend/src/components/workspace/WorkspaceShell.collaborationOwnerRead.test.ts
- frontend/src/components/workspace/WorkspaceShell.serverNotifications.test.ts
- frontend/src/components/workspace/WorkspaceShell.threadAppleMailStructure.test.ts
- frontend/fixtures/collaboration-context/build.mjs
- frontend/fixtures/collaboration-context/harness.tsx
- frontend/fixtures/collaboration-context/serve.cjs
- frontend/fixtures/collaboration-context/verify.cjs
- frontend/fixtures/collaboration-context/regressions.cjs
- frontend/fixtures/collaboration-context/verify-freeze.cjs
- frontend/fixtures/collaboration-context/browser-results.json
- frontend/fixtures/collaboration-context/regression-results.json
- frontend/fixtures/collaboration-context/freeze-results.json
- frontend/fixtures/collaboration-context/HANDOFF.md
- frontend/fixtures/collaboration-context/conversation-desktop.png
- frontend/fixtures/collaboration-context/conversation-laptop.png
- frontend/fixtures/collaboration-context/conversation-tablet.png
- frontend/fixtures/collaboration-context/conversation-mobile.png
- frontend/fixtures/collaboration-context/email-desktop.png
- frontend/fixtures/collaboration-context/email-laptop.png
- frontend/fixtures/collaboration-context/email-tablet.png
- frontend/fixtures/collaboration-context/email-mobile.png
- frontend/fixtures/collaboration-context/resolve-confirmation.png
- frontend/fixtures/collaboration-context/dark-conversation-desktop.png
- frontend/fixtures/collaboration-context/dark-email-desktop.png
- frontend/fixtures/collaboration-context/dark-people-desktop.png
- frontend/fixtures/collaboration-context/dark-menu-desktop.png
- frontend/fixtures/collaboration-context/dark-confirmation-desktop.png
- frontend/fixtures/collaboration-context/dark-conversation-mobile.png
- frontend/fixtures/collaboration-context/dark-email-mobile.png
- frontend/fixtures/collaboration-context/dark-people-mobile.png
- frontend/fixtures/collaboration-context/dark-menu-mobile.png
- frontend/fixtures/collaboration-context/dark-confirmation-mobile.png

## Required screenshots

- [conversation-desktop.png](/Users/rutger/cuevion-app/frontend/fixtures/collaboration-context/conversation-desktop.png)
- [email-desktop.png](/Users/rutger/cuevion-app/frontend/fixtures/collaboration-context/email-desktop.png)
- [resolve-confirmation.png](/Users/rutger/cuevion-app/frontend/fixtures/collaboration-context/resolve-confirmation.png)
- [conversation-mobile.png](/Users/rutger/cuevion-app/frontend/fixtures/collaboration-context/conversation-mobile.png)
- [email-mobile.png](/Users/rutger/cuevion-app/frontend/fixtures/collaboration-context/email-mobile.png)

Local implementation/validation: GO. Push/deploy: NO-GO per user instruction. Next action: review this local commit and these screenshots; any push or deployment requires a subsequent instruction.
