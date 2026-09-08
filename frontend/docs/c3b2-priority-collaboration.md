# C3B2: Collaboration as active work

This change is local only. It does not authorize a push, deployment, or Production test.
Historical migration remains closed; its Production operator mode remains `off`.

## Authority and integration

`src/lib/collaborationSummaryApi.ts` uses the authenticated owner transport's
`list_summaries` operation. Its strict parser validates workspace, mailbox,
source reference, state, version, timestamps, access, and ascending cursors.
The server boundary is `api/collaboration/owner_http.py` →
`application.list_v2_summaries_for_verified_owner`; server bounds in `models.py`
are 50 per page and 1,000 discovery entries.

`useCollaborationSummaries` hydrates once per authenticated member/workspace
lifetime in `WorkspaceShell`. A new identity gets an empty store during render,
before effects run. Cleanup fences old pagination; old mutation callbacks also
check store identity. There is no persistence, polling, focus listener, or row
fetch. Empty pages with a cursor continue. Invalid/non-advancing cursors, malformed
pages, more than 1,000 results, and more than 20 requests fail closed. Only a fully
successful load replaces the projection. Same-scope refresh failure retains valid
authority; initial failure does not promote anything.

The index uses `(workspaceId, mailboxId, provider, exact source fields)`. Google
uses `providerMessageId`; custom IMAP uses `INBOX`, `uidValidity`, and `imapUid`.
The existing trusted owner source locator verifies the current managed provider,
connection, mailbox, and message context. No subject/text fallback exists.
Conflicting summaries for one source are excluded. Index construction is O(S),
S ≤ 1,000. Each source lookup is O(1), without scanning summaries.

Active states are `needs_review`, `needs_action`, and `note_only`; `resolved` is
inactive. Only the server projection provides the new work signal. Live Priority
derivation ignores old shared/local Collaboration flags as Collaboration authority.

The signal enters `normalPriorityGateCandidateEntries` before focus visibility
can discard the source, and bypasses the age, read, and ordinary importance gates
in `broadLivePriorityInboxEntries`. The existing Normal gate and reason vocabulary
are reused. Done/cleared records, manual removal, spam suppression, and normalized
noise policy win. Promotion waits for canonical workflow suppression authority.
The latest loaded conversation representative's removal/completion also prevents
backfilling an older Collaboration source.

Only exact loaded source mail is promoted; summaries never synthesize provider
messages. Existing Inbox candidate scope remains. An exact older source survives
both existing conversation deduplication steps without changing P4B2 authority.
The same mail is deduplicated by mailbox/message identity. Independent manual,
waiting, returned-reply, and review reasons retain their existing semantics.
`priorityRanking.ts`, work-state ranks, and Newest/Oldest sorting are unchanged.

`MailboxView.renderMessageCollaboration` reuses the existing selected-mail cue:
**Open Collaboration**. Split and full-message presentations are mutually exclusive.
The old live list CTA is hidden to avoid duplicate/local-snapshot cues. The existing
Collaboration menu remains the non-active/history entry point. The click rechecks
selection, scope, exact source and summary ID; the existing lookup/read modal also
requires the lookup ID to equal the captured summary ID. A disappearing binding
fails closed instead of leaving an in-flight read stuck.

The existing canonical owner footer now calls the already-implemented verified
resolve/reopen APIs. Successful lifecycle responses update an already-bound summary
directly; older responses cannot overwrite newer versions. Successful creation
refreshes once when the DTO omits routing. Failed mutations publish nothing.
Summary publication survives modal close, while modal updates remain fenced.
Account changes fence both paths. Guest UI, secure-link handling, Notifications,
mentions, backend authority, and migration code are unchanged.

## Local validation

New executable suites:

- `src/lib/collaborationSummaryStore.test.ts`: active states, strict binding,
  duplicate/ambiguous sources, Google/IMAP isolation, malformed input, pagination,
  empty pages, bounds, failure retention, cancellation, mutation/list races,
  exact CTA binding and network-free lookup.
- `src/lib/useCollaborationSummaries.test.ts`: deterministic render/effect seam
  executing the real hook/store; immediate account/workspace clearing, A→B→A,
  stale callbacks/results, and no rehydration on ordinary renders.
- `src/components/workspace/WorkspaceShell.collaborationPriority.test.ts`:
  executes actual integration expressions and renders the actual CTA, including
  old/read/Normal, filtered eligibility, manual/noise/Done gates, latest-thread
  suppression, final semantic merge, reason composition, exact lookup/read,
  stale clicks, create/resolve/reopen success/failure, and modal-close races.

Relevant existing suites (paths relative to `frontend/src`):

- `lib/prioritySource.test.ts`, `returnedReplyEvidence.test.ts`,
  `priorityRuntimeAdapter.test.ts`, `priorityRuntimeSignals.test.ts`,
  `priorityReasonCopy.test.ts`, `normalPriorityGate.test.ts`,
  `normalPriorityGateAdapter.test.ts`, `messageNoiseGate.test.ts`,
  `priorityWorkflowAuthority.test.ts`, `prioritySemanticState.test.ts`,
  `prioritySemanticNewInbound.test.ts`, `mailboxMessageIdentity.test.ts`,
  `inboxEngine.test.ts`.
- `components/workspace/WorkspaceShell.priorityRanking.test.ts`,
  `WorkspaceShell.priorityWorkflowAuthority.test.ts`,
  `WorkspaceShell.prioritySemanticShadow.test.ts`,
  `WorkspaceShell.prioritySemanticNewInbound.test.ts`,
  `WorkspaceShell.waitingOnOther.test.ts`, `WorkspaceShell.mailboxPolling.test.ts`,
  `WorkspaceShell.performance.test.ts`, `WorkspaceShell.customImapReplyContext.test.ts`,
  `WorkspaceShell.classification.test.ts`.
- `lib/collaborationSummaryApi.test.ts`, `collaborationOwnerSourceLocator.test.ts`,
  `collaborationOwnerReadApi.test.ts`, `collaborationOwnerWriteApi.test.ts`
  (including resolve/reopen CAS and failures),
  `collaborationOwnerWriteApi.externalGuest.test.ts`, `collaborationGuestApi.test.ts`,
  `collaborationGuestInviteLink.test.ts`.
- `components/collaboration/CollaborationAccessPanel.test.tsx`,
  `ExternalCollaborationGuestView.test.tsx`,
  `components/workspace/WorkspaceShell.collaborationOwnerRead.test.ts`,
  `WorkspaceShell.collaborationIdentity.test.ts`, `App.collaborationGuestRoute.test.ts`.

The P4B2 source-contract assertion recognizes the final composition wrapper;
its underlying runtime/authority assertions remain unchanged. Full frontend
TypeScript checking is required. Tests use mocked fetch, with unmocked network
disabled and the three protected repository paths blocked from reads. Provider
API/OAuth test bundles are excluded. No Python test or migration is executed;
no bytecode is generated by these changes. No dependencies are installed.

Additional pre-edit trace: `prioritySource.ts`, `priorityRuntimeAdapter.ts`,
`priorityRuntimeSignals.ts`, `normalPriorityGate.ts`, `normalPriorityGateAdapter.ts`,
`priorityReasonCopy.ts`, `priorityRanking.ts`, `mailboxMessageIdentity.ts`,
`inboxEngine.ts`, `collaborationOwnerApiTransport.ts`, `collaborationOwnerReadApi.ts`,
`collaborationOwnerWriteApi.ts`, `collaborationOwnerSourceLocator.ts`,
`CollaborationAccessPanel.tsx`, `WorkspaceShell.tsx`, and the frontend package/TS
configuration. Existing mailbox startup and `createActiveMailboxPollingController`
integration are unchanged.

## Post-deploy feeling test — only after separate authorization

1. For both Google and custom IMAP, select a loaded old, read, Normal mail with
   no independent Priority reason. Create an active Collaboration. Confirm one
   exact mail row appears in Priority and remains there when read.
2. Open that Priority row. Confirm one **Open Collaboration** action and the
   correct source body/participants in the existing modal. Test another mailbox
   with identical subject and a different exact source; it must not share the cue.
3. Resolve. Confirm the Collaboration-only row and active cue disappear; history
   remains accessible through the existing Collaboration menu. Reopen there and
   confirm the signal and cue return without reloading.
4. Repeat with waiting-on-other or a manual Priority reason. Resolving must remove
   only Collaboration authority; the independent reason must keep the mail eligible.
5. Explicitly Done/remove the latest conversation mail. Confirm reopening an
   older Collaboration does not undo that suppression. Check spam/noise safeguards.
6. Switch selected mail while a Collaboration read is pending; switch account and
   workspace while summaries load. No stale mail or previous-account cue may open.
   Check failed create/resolve/reopen leaves current authority unchanged.
7. Check an empty intermediate summary page and a transient same-account refresh
   failure in a controlled test environment. Check initial failure has no promotion.
8. Compare Priority Newest/Oldest ordering, normal scroll/open responsiveness,
   startup request count, and focus behavior. There should be no row requests,
   new polling, scheduling tricks, or arbitrary Collaboration rank boost.

Do not execute this plan during the implementation task.
