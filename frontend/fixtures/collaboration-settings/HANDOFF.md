# Collaboration C3P2C — Settings and resolved access

Local frontend implementation: GO. No push, deployment, production access, backend changes, dependency installation or new request flow. Protected paths were never read, searched, diffed, changed or staged; they appeared only in git status --short. All Python edit helpers used PYTHONDONTWRITEBYTECODE=1.

Baseline gate passed on perf-1: HEAD and remote main both 246648811df8c64e34b5e6bcbf9738e36218d7f1, staging empty, exactly the three expected protected status entries. Commit message: add collaboration settings and resolved access. Final hashes and post-commit gates are reported in the task response.

## Resolved rediscovery and one-record proof

Resolved rediscovery is available through existing authority. The selected-mail CTA called getActiveCollaborationSummary; lookupActiveCollaborationSummary deliberately excluded resolved state, and isCurrentCollaborationOpenBinding also required active state. Resolution published the resolved state into the existing summary store, making that CTA disappear even though the canonical record and exact source binding remained.

The store already retains resolved summaries. The existing list_summaries contract supplies workspaceId, mailboxId, sourceRef, collaborationId and state. Atomic resolution preserves the ID and source pointer; the normal lookup/read sequence works for resolved records. No additional fetch is needed at row render, tab change, resolution, or discovery hydration.

One retained canonical Collaboration per exact source is enforced by the existing source pointer and atomic create contract. This is a retained-record invariant, not an infinite archival-retention promise. Both Team creation and create-with-guest reuse the existing pointer target, including resolved state; they reject conflicting pointers rather than selecting by time. Once records expire under existing retention, they are outside the available canonical history. No expired records or operator/migration paths are revived.

Proof inspected read-only in exact non-protected backend files:

- frontend/api/collaboration/owner.py: the public owner entry point delegates to owner_http.
- frontend/api/collaboration/owner_http.py: list_summaries, lookup and read retain the existing authenticated operations and exact payloads.
- frontend/api/collaboration/application.py: lookup_v2_collaboration_for_verified_owner obtains the authenticated workspace/mailbox capability, normalizes exact provider sourceRef, and invokes _load_v2_thread_by_source; summary listing is authenticated and bounded.
- [redis_store.py:2423](/Users/rutger/cuevion-app/frontend/api/collaboration/redis_store.py:2423): _CREATE_V2_THREAD_LUA checks both source pointers, validates owner/workspace/mailbox/source, and returns duplicate with the existing ID. No active-state restriction.
- [redis_store.py:2692](/Users/rutger/cuevion-app/frontend/api/collaboration/redis_store.py:2692): create-with-guest has the same source conflict check and returns existing with the retained ID.
- [redis_store.py:3171](/Users/rutger/cuevion-app/frontend/api/collaboration/redis_store.py:3171): the existing source loader validates the exact workspace/mailbox/source and pointer target; no fuzzy fallback.
- [redis_store.py:3300](/Users/rutger/cuevion-app/frontend/api/collaboration/redis_store.py:3300): Resolve/Reopen preserves source/ID, updates the same record and refreshes the same pointer; Reopen target remains note_only.
- [redis_store.py:5418](/Users/rutger/cuevion-app/frontend/api/collaboration/redis_store.py:5418): bounded discovery explicitly includes resolved records.

Frontend ambiguity handling remains unchanged: conflicting summaries for one exact source are removed from the index. No latest-by-timestamp choice, subject matching or sender matching was introduced.

## Pre-edit trace A–Q

| Area | Existing code / retained authority |
|---|---|
| A–C: tab state, Conversation, Email | WorkspaceShell MailboxView; CollaborationContextTabs; mounted CollaborationChatTimeline/Composer; renderThreadMessage for the loaded exact source. |
| D–G: People, Team, guests, secure link | CollaborationAccessPanel and its existing callbacks/state. All access actions and transient-link handling are reused. |
| H–I: lifecycle and confirmation | CollaborationLifecycleMenu UI and unchanged transitionCanonicalCollaboration; native modal confirmation with Cancel focus and trap. |
| J: selected-mail CTA | renderMessageCollaboration → getCollaborationOpenBinding → summary getter; click validates captured/current exact binding before existing openCollaborationOverlay. |
| K–L: active/history lookup | Existing summary index contains both states; active-only lookup and binding guard caused history disappearance. Existing owner lookup/read returns the retained resolved record. |
| M: exact source | deriveCollaborationOwnerSourceLocator and collaborationSummaryKey scope workspace/mailbox/provider message identity; IMAP additionally requires INBOX, UIDVALIDITY and UID. |
| N: discovery | createCollaborationSummaryStore / useCollaborationSummaries; bounded existing hydration and mutation publication. |
| O: Priority | getActiveCollaborationSummary, hasActiveCollaborationPriority, isCollaborationPriorityEntrySuppressed and collaborationPriority.ts. |
| P: publication | transitionCanonicalCollaboration publishes via onCanonicalCollaborationMutation and acceptMutation, including if the modal closed. |
| Q: C3D | Existing serverNotificationDisplay layout effect forces Conversation; displayed acknowledgement still requires exact source, canonical ID, activity ref, focus and painted frame. |

## Implementation

- Header now contains Collaboration, subject, sender and Close. People and lifecycle overflow were removed. Settings owns the single visible lifecycle status.
- Three semantic tabs: Conversation (default), Email, Settings. Arrow keys wrap across all three, Home/End work, and aria-selected/controls relationships remain explicit. Conversation/Email/Settings panels stay mounted, preserving drafts, composer mode, scroll, refs/highlights and access state.
- Settings reuses CollaborationAccessPanel and shows a separate Collaboration lifecycle group. Owners get direct Resolve/Reopen; participants keep status visibility without gaining lifecycle authority. The exact existing confirmation markup/handlers are retained, including safe default Cancel, focus cycling, Escape isolation and duplicate confirmation guard.
- Secure-link visibility now reveals Settings. The same transient link survives tab changes and the existing overlay-close protection still applies.
- lookupCollaborationSummary is the exact state-independent access lookup. lookupActiveCollaborationSummary remains its active-only wrapper, so Priority eligibility is unchanged. Only MailboxView receives the state-independent getter.
- Source-mail copy is Open Collaboration when active and View Collaboration when resolved. Both use the same exact binding guard, source lookup, expected-ID check and canonical read. No competing CTA or new endpoint.

## Verification

- 39 local Chrome browser scenario groups pass, including the actual extracted CTA, source binding, canonical owner read, lifecycle/send handlers, existing mail renderer and displayed acknowledgement effect.
- Full flow passes: active source CTA → Resolve exactly once → close → View Collaboration → exact lookup/read of the same ID → readable Conversation/Email/Settings → Reopen → active CTA. The actual summary-store active lookup is false while resolved and true after Reopen; wrong-source lookup is null.
- 28 frontend regression suites pass. The focused WorkspaceShell Priority integration suite executes actual candidate gates/reasons, resolved suppression, reopen eligibility, old local snapshot rejection, current-selection guards, wrong-ID rejection, lifecycle publication and read behavior for active/resolved records.
- C3D active and resolved targets pass exact focus/highlight-before-display acknowledgement; unread/mark-read/store suites pass.
- 38 exact baseline comparisons pass: unchanged read/send/lifecycle/attachment/security functions, full renderThreadMessage, active Priority expressions, display acknowledgement effect, authority/chat/access files and confirmation markup/handlers.
- Both frontend TypeScript configurations pass with --noEmit.
- 1600×1000, 1280×800, 768×1024 and 375×812 pass geometry and control reachability checks. Settings desktop/mobile and dark mode screenshots were visually inspected. Tabs fit, controls are reachable and there is one active outer content scroll region.
- Email retains the existing sandbox iframe internal scrolling; no extra body/page scroller was introduced. Chat alignment and Shared/Internal drafts/mode remain unchanged.
- React review: unconditional hooks, stable mounted panels/access component, exact context keys, preserved cleanup/focus behavior, pure in-memory lookup and no added timers/listeners/polling.

Limits: fixtures use an in-memory canonical API and summary loader, not a live backend. The resolved-mail screenshot uses the actual CTA inside a minimal source-mail holder. Backend authority was traced read-only; backend C3C/C3D1/C2G suites were not executed. Their relevant frontend contract suites pass, and no backend files were modified. No physical device, screen-reader, Safari or Firefox session was run. Existing discovery caps/retention/fail-closed behavior remain; this does not add a full historical scan.

## Reproduce

From the repository root, using installed dependencies:

```sh
node frontend/fixtures/collaboration-settings/build.mjs
node frontend/fixtures/collaboration-settings/serve.cjs
# In another terminal:
node frontend/fixtures/collaboration-settings/verify.cjs
node frontend/fixtures/collaboration-settings/regressions.cjs
node frontend/fixtures/collaboration-settings/verify-freeze.cjs
./node_modules/.bin/tsc -p frontend/tsconfig.app.json --noEmit
./node_modules/.bin/tsc -p frontend/tsconfig.node.json --noEmit
```

Only 127.0.0.1:4177 is served. Generated bundle: /private/tmp/cuevion-c3p2c. Browser runner uses the already-installed Playwright/Chrome. The existing Browserslist data-age advisory was non-blocking; no package update was attempted. Earlier fixture artifacts document earlier product slices; collaboration-settings is the current fixture.

## Inspected frontend files and regression inventory

Primary reads: WorkspaceShell.tsx, CollaborationContextControls.tsx, CollaborationAccessPanel/Chat from the existing accepted implementation, collaborationSummaryApi/Store, collaborationOwnerReadApi/WriteApi tests, collaborationPriority.ts, existing collaboration-context fixture scripts, and the exact frontend regression files below. Build and TypeScript also use their directly imported non-protected frontend dependencies and existing configuration.

- frontend/src/components/workspace/WorkspaceShell.collaborationPriority.test.ts
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

- frontend/src/components/workspace/WorkspaceShell.tsx
- frontend/src/components/collaboration/CollaborationContextControls.tsx
- frontend/src/lib/collaborationSummaryStore.ts
- frontend/src/lib/collaborationSummaryStore.test.ts
- frontend/src/components/workspace/WorkspaceShell.collaborationPriority.test.ts
- frontend/src/components/workspace/WorkspaceShell.collaborationOwnerRead.test.ts
- frontend/src/components/workspace/WorkspaceShell.serverNotifications.test.ts
- frontend/fixtures/collaboration-settings/build.mjs
- frontend/fixtures/collaboration-settings/harness.tsx
- frontend/fixtures/collaboration-settings/serve.cjs
- frontend/fixtures/collaboration-settings/verify.cjs
- frontend/fixtures/collaboration-settings/regressions.cjs
- frontend/fixtures/collaboration-settings/verify-freeze.cjs
- frontend/fixtures/collaboration-settings/browser-results.json
- frontend/fixtures/collaboration-settings/regression-results.json
- frontend/fixtures/collaboration-settings/freeze-results.json
- frontend/fixtures/collaboration-settings/HANDOFF.md
- frontend/fixtures/collaboration-settings/conversation-desktop.png
- frontend/fixtures/collaboration-settings/email-desktop.png
- frontend/fixtures/collaboration-settings/conversation-laptop.png
- frontend/fixtures/collaboration-settings/email-laptop.png
- frontend/fixtures/collaboration-settings/conversation-tablet.png
- frontend/fixtures/collaboration-settings/email-tablet.png
- frontend/fixtures/collaboration-settings/conversation-mobile.png
- frontend/fixtures/collaboration-settings/email-mobile.png
- frontend/fixtures/collaboration-settings/resolve-confirmation.png
- frontend/fixtures/collaboration-settings/dark-conversation-desktop.png
- frontend/fixtures/collaboration-settings/dark-email-desktop.png
- frontend/fixtures/collaboration-settings/dark-settings-desktop.png
- frontend/fixtures/collaboration-settings/dark-confirmation-desktop.png
- frontend/fixtures/collaboration-settings/dark-conversation-mobile.png
- frontend/fixtures/collaboration-settings/dark-email-mobile.png
- frontend/fixtures/collaboration-settings/dark-settings-mobile.png
- frontend/fixtures/collaboration-settings/dark-confirmation-mobile.png
- frontend/fixtures/collaboration-settings/settings-active-desktop.png
- frontend/fixtures/collaboration-settings/settings-resolved-desktop.png
- frontend/fixtures/collaboration-settings/settings-mobile.png
- frontend/fixtures/collaboration-settings/resolved-mail-cta.png

## Required screenshots

- [settings-active-desktop.png](/Users/rutger/cuevion-app/frontend/fixtures/collaboration-settings/settings-active-desktop.png)
- [settings-resolved-desktop.png](/Users/rutger/cuevion-app/frontend/fixtures/collaboration-settings/settings-resolved-desktop.png)
- [settings-mobile.png](/Users/rutger/cuevion-app/frontend/fixtures/collaboration-settings/settings-mobile.png)
- [resolved-mail-cta.png](/Users/rutger/cuevion-app/frontend/fixtures/collaboration-settings/resolved-mail-cta.png)

Next action: review the local commit and screenshots. Push/deploy remains NO-GO until a subsequent explicit instruction.
