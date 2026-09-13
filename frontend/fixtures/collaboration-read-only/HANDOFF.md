# C3P2D — resolved Collaboration read-only presentation

GO for local review. No push, deploy or production activity. Backend, lifecycle/API semantics, source access, history index, Priority, C3D, mark-read, guest security, email renderer and polling remain unchanged.

Baseline gate passed on perf-1: HEAD and remote main both `070796b877b8e4e1bc8ac75b6b92373af04163b4`, empty staging and exactly the three expected protected status entries. Protected paths appeared only in git status; their contents, metadata, searches, diffs and history were not accessed. No installs, Python or backend access.

## Pre-edit trace A–P

| Area | Exact existing frontend authority / presentation |
|---|---|
| A: state | WorkspaceShell `activeCollaborationOwnerProjection` is the successful canonical owner/participant DTO; its `state` includes resolved. |
| B: composer | WorkspaceShell rendered `activeCollaborationOwnerProjection ? <CollaborationChatComposer ... /> : null`. Truthiness alone included resolved DTOs. |
| C: selector | CollaborationChatComposer owns local `mode`, initially shared; Shared/Internal buttons choose the channel. |
| D: Shared textarea | Shared channel receives parent `collaborationOwnerSharedMessageDraft` and setter. |
| E: Internal textarea | Internal channel receives separate parent `collaborationOwnerInternalNoteDraft` and setter. One textarea renders the current channel. |
| F: Send | Composer calls the channel's existing submitCollaborationOwnerSharedMessage/submitCollaborationOwnerInternalNote callback. No send handler changes. |
| G: People | Mounted CollaborationAccessPanel receives the same canonical DTO; mode/access and owner permissions determine its content. |
| H: Add Team | Owner, eligible member, participant limit and mutation-in-flight checks existed; no resolved-state check. |
| I: Invite guest | Owner, guest limit, email and mutation-in-flight checks existed; no resolved-state check. |
| J: guest history | Existing owner-only external guest rows display pending/active/logged_out/revoked/expired history. Participant visibility remains unchanged. |
| K: Revoke | Existing pending/active guest rows expose confirmation and confirmRevoke; this must remain available to owners while resolved. |
| L: secure link | Local secureLink state and secureLinkPanel own Copy, explicit close protection and secret handling; rendered independently of invite form. Existing state must survive resolution. |
| M: Reopen | Settings uses CollaborationLifecycleSettings → transitionCanonicalCollaboration. The handler selects existing reopen API only for resolved state and preserves owner checks/request guards. |
| N: status | Lifecycle status existed only in Settings; header had subject/sender with no resolved indication. |
| O: mobile | WorkspaceShell modal uses bounded viewport height, fixed header/tabs and one content scroller; composer occupied the Conversation footer. |
| P: exact activity | CollaborationChatTimeline retains canonical IDs, activity refs and highlightedId. WorkspaceShell's displayed-acknowledgement effect requires the exact activity to be focused and displayed before completion. |

Before editing, the task update reported that the composer predicate was DTO truthiness and that People did not exclude resolved state. Existing guest history, Revoke, already-created link Copy and close protection were identified as controls to retain.

## Changes

- WorkspaceShell adds one noninteractive, text-labelled Resolved header status (`role=status`) visible from every tab. Active retains the previous uncluttered header. Settings still has its original plain Status text, not a second pill.
- WorkspaceShell passes resolved presentation props into the existing composer, including the existing lifecycle handler and owner-only permission.
- CollaborationChatComposer stays mounted with its existing context key and mode state. For resolved records it returns a compact explanatory panel instead of selectors, textarea or Send. Owners get Reopen collaboration; participants receive guidance to ask the owner. Pending/error feedback uses existing lifecycle state with no timer/retry mechanism.
- CollaborationAccessPanel hides Add Team, Invite external guest and both creation forms while resolved, including forms opened before resolution. It explains “Reopen the collaboration to add people.” Both submit handlers return immediately for resolved DTOs, before mutation or secret clearing. Existing eligibility applies again after Reopen.
- Existing Team/guest rows, guest statuses, Revoke confirmation/operation and secureLinkPanel remain unchanged. The access panel is not remounted or reset on lifecycle changes.

Implementation files:

- `frontend/src/components/workspace/WorkspaceShell.tsx`
- `frontend/src/components/collaboration/CollaborationChat.tsx`
- `frontend/src/components/collaboration/CollaborationAccessPanel.tsx`

Focused unit coverage extends CollaborationChat.test.ts and CollaborationAccessPanel.test.tsx. The latter also executes the actual resolved creation handlers with no mutation dependencies supplied, proving they return before any transport, state change or secret clearing.

## Draft behavior and permissions

Within the same open Collaboration context, Shared and Internal drafts remain separately in parent state while resolved; neither is present in rendered HTML and neither can be sent from the UI. The composer component remains mounted, so mode also survives. Reopen restores the current mode and its corresponding draft; switching mode reveals only that channel's retained draft. No automatic send occurs.

Closing the modal or changing context continues to use the existing fence/reset behavior, which clears these transient drafts. No persistence was added. In-flight request semantics are unchanged.

Reopen remains owner-only in Conversation and Settings. Participants do not gain lifecycle or guest-management permissions. Existing authorized owner Revoke remains available while resolved. An existing transient link remains copyable and protected against accidental close; revoking its matching invitation clears it through the unchanged security handler.

## Verification

- 24 focused browser groups pass: active composer, resolved DOM removal, header/copy, stable activity IDs and nodes, Conversation and Settings at all four sizes, Email readability, resolved C3D focus/highlight/acknowledgement, failed/pending/keyboard Reopen, draft and mode retention, preserved scroll node/position, active controls/Priority, secure-link resolution/copy/close protection, resolved Revoke, source CTA and request counts.
- 18 source-access runtime groups pass through the actual summary hook and extracted WorkspaceShell getters/open/read/lifecycle/close paths, including hard refresh, reselect, exact ID, Priority suppression, Manual Priority independence and lifecycle publication after close.
- 39 existing UI browser regression groups pass, including active and resolved exact notification activity, three tabs, Team/guest access, confirmation, sends/retries, responsive layouts, dark/light views and reader security.
- 28 frontend regression suites pass, including the extended unit suites, WorkspaceShell performance, C3B2 resolved suppression/reopen eligibility, summary store/lookup, notification and guest security contracts.
- 52 baseline freeze checks pass. WorkspaceShell is byte-identical except the header line and resolved composer props. Lifecycle/read/send/close callbacks, source CTA/getters, history hook/store, Priority, email rendering, C3D and mark-read are frozen. Timeline/identity, guest Revoke, secret Copy/clear/panel, invitation-result handling and start behavior are also frozen.
- Full frontend TypeScript app and node checks pass.
- Zero browser errors or external requests in all browser runs. No added discovery, per-tab/per-row fetches, polling, retries or timers. The focused ledger uses only the existing initial list, user-triggered lookup/read, lifecycle and guest operations.
- React review: hook order unchanged, lifecycle state derived during render, mode preserved without an effect, hidden writable controls absent from DOM, owner permissions retained, existing components and callbacks reused, no new dependency or subscription.

Resolved Conversation and Settings passed at 1600×1000, 1280×800, 768×1024 and 375×812. History remains readable with one scroller; Reopen and applicable Revoke controls are reachable. Resolved status is text and semantically exposed; keyboard Reopen works, pending disables duplicate submission, and removed composer controls cannot receive focus. Existing tab and highlight semantics are unchanged.

These are local integration fixtures with in-memory canonical transport, not a production or full signed-in inbox test. They mount the real summary hook and actual extracted WorkspaceShell modal/getter/open/read/lifecycle/close code, actual chat/access components and actual email renderer. Loaded-mail selection and unrelated legacy shell callbacks are fixture scaffolding. C3D displayed acknowledgement is exercised here; the full exact navigator/store contracts run in the existing frontend suites. No backend suites or production systems were accessed.

## Screenshots and result artifacts

Required screenshots in this directory:

- [resolved-conversation-desktop.png](/Users/rutger/cuevion-app/frontend/fixtures/collaboration-read-only/resolved-conversation-desktop.png)
- [resolved-conversation-mobile.png](/Users/rutger/cuevion-app/frontend/fixtures/collaboration-read-only/resolved-conversation-mobile.png)
- [resolved-settings-desktop.png](/Users/rutger/cuevion-app/frontend/fixtures/collaboration-read-only/resolved-settings-desktop.png)
- [reopened-conversation-desktop.png](/Users/rutger/cuevion-app/frontend/fixtures/collaboration-read-only/reopened-conversation-desktop.png)

Additional source-access screenshots: active-source.png, resolved-source.png, resolved-rehydrated.png, resolved-settings.png, reopened-source.png. Existing UI regression screenshots go to `/private/tmp/cuevion-c3p2d-ui`.

Machine-readable results: read-only-results.json, runtime-results.json, ui-results.json, regression-results.json, freeze-results.json.

## Reproduce locally

From `/Users/rutger/cuevion-app`:

```sh
node frontend/fixtures/collaboration-read-only/build.mjs
node frontend/fixtures/collaboration-read-only/serve.cjs
```

Then, in another terminal:

```sh
node frontend/fixtures/collaboration-read-only/verify-read-only.cjs
node frontend/fixtures/collaboration-read-only/verify-runtime.cjs
node frontend/fixtures/collaboration-read-only/verify-ui.cjs
node frontend/fixtures/collaboration-read-only/regressions.cjs
node frontend/fixtures/collaboration-read-only/verify-freeze.cjs
./node_modules/.bin/tsc -p frontend/tsconfig.app.json --noEmit
./node_modules/.bin/tsc -p frontend/tsconfig.node.json --noEmit
```

Server: loopback-only 127.0.0.1:4179, generated files in `/private/tmp/cuevion-c3p2d`. Stop with Ctrl-C. Installed Playwright/Chrome substitute for unavailable agent-browser; no installation. Fixture sessionStorage models server-retained lifecycle state across reload and contains no guest token; production persistence is unchanged.

Local commit message: `polish resolved collaboration read only state`. Parent and unchanged remote main: `070796b877b8e4e1bc8ac75b6b92373af04163b4`. Post-commit SHA and gates are reported in the task response. Next action: review this local commit and screenshots. Push/deploy remain NO-GO under the current instruction.
