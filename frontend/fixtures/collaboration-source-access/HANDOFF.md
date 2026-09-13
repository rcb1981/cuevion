# C3P2C1 — resolved Collaboration source access

Local implementation: GO. No push, deployment or production activity.

Baseline gate passed on perf-1: local HEAD and remote main both `0982786e7781d4f00e0d6113b29346c2e679883e`, empty staging and exactly the three expected protected status entries. Protected contents, metadata, searches, diffs and history were never accessed. No installation or Python invocation.

## Proven bug and correction

Before changing implementation code, the browser reproduced:

selected source mail → Open Collaboration → exact lookup/read → Settings → Resolve collaboration → confirm Resolve → modal says Resolved → Close → same selected mail has zero Collaboration CTAs.

The canonical store retained the source binding. `useCollaborationSummaries` filtered its subscribed snapshot into an active-only `index`. WorkspaceShell's state-independent `getCollaborationSummary` nevertheless read that filtered index. On Resolve the modal held the canonical resolved DTO while both the active and history getters returned null. Close cleared only transient modal state; it exposed the already-missing CTA rather than causing the loss.

The previous collaboration-settings fixture directly queried `summaryStore.getSnapshot()`, substituting its own getter and subscription. It never mounted `useCollaborationSummaries`, the layer that removed resolved records. Its CTA/read/lifecycle checks therefore passed despite the real projection being broken. Its freeze check even preserved the faulty hook. Those prior source-CTA conclusions are superseded by this reproduction.

The production fix changes only two files:

- `useCollaborationSummaries.ts` exposes `historyIndex: summaries`, an alias of the existing canonical store snapshot. Existing `index` stays active-only, preserving Priority behavior and its empty-index fast path.
- WorkspaceShell's `getCollaborationSummary` uses `historyIndex`. The active getter continues to use `index`.

No extra map, copied snapshot, lookup request, polling, scan, persistence, lifecycle operation, or source inference was added.

## State trace A–N

| Requirement | Proven runtime path |
|---|---|
| A: active selected-mail predicate | `renderMessageCollaboration(selectedMessage)` → `getCollaborationOpenBinding` → prop `getCollaborationSummary`; exact current selection/scope/source checks precede opening. |
| B: resolved predicate | Same path, `state === resolved` selects View Collaboration. Before fix its input was filtered; after fix it uses retained canonical history. |
| C: active summary projection | Hook memoizes only active summaries into `index`; unchanged. |
| D: sourceRef lookup | `deriveCollaborationOwnerSourceLocator` and `collaborationSummaryKey` bind workspace, mailbox and exact provider source. Opening uses existing owner lookup/read with expected ID. |
| E: resolved lookup | `lookupCollaborationSummary` accepts resolved records; it now receives `historyIndex`. No fuzzy fallback. |
| F: Resolve publication | Existing `transitionCanonicalCollaboration` publishes a successful canonical DTO through `onCanonicalCollaborationMutation` → hook `acceptMutation` → store. Store preserves sourceRef and ID, changes state/updatedAt/access, cancels stale refresh and notifies subscribers. |
| G: Reopen publication | Identical callback, canonical state becomes note_only, both history and active projections update immediately. |
| H: Close | Existing close/fence resets transient modal projection, drafts, refs and UI. It never clears the workspace store or history index. Pending Resolve can still publish after Close. |
| I: state ownership | WorkspaceShell owns the account/workspace hook and passes its getter and mutation callback to MailboxView. Fixture uses the actual React hook in a parent and its returned values in the selected-mail child. |
| J: active-only filter | The exact defect was the hook's `.filter(isActiveCollaborationSummary)` being used by both consumers. This filter remains correct for Priority. |
| K: clearing | Resolve removes the entry only from the derived active index. Canonical snapshot/history remains. Close clears modal state only. |
| L: races/cache | Existing generation cancellation prevents an old list response overwriting a confirmed mutation; existing account/store ref prevents cross-scope publication. No new cache or invalidation mechanism. |
| M: hard refresh | Hook's existing effect requests bounded list_summaries, store validates/indexes results, history getter now sees resolved records from that response. No extra fetch. |
| N: reselect | Same retained snapshot is queried for the newly selected exact source. Unrelated mail has no CTA; selecting back needs no request. |

## Existing backend authority, inspected read-only

`frontend/api/collaboration/owner_http.py:650` delegates list_summaries to the authenticated application service. `frontend/api/collaboration/application.py:2117` calls the bounded canonical summary operation and retains existing entitlement/mailbox filters. `frontend/api/collaboration/redis_store.py:5418` explicitly accepts resolved state and publishes the same sourceRef, collaborationId and state. The contract supports resolved hydration without new networking. Existing retention and discovery bounds still apply; this is access to retained authorized records, not unlimited archival history.

Backend code was not changed or executed. C3B2/C3B2.1 frontend discovery, summary-store, exact-source and Priority integration suites pass; backend C3B2/C3B2.1 suites were not rerun.

## Test fidelity and limits

This is a local browser integration harness, not the entire signed-in application or a production test. It mounts the real `useCollaborationSummaries` hook and real store/transport parser. The build extracts unchanged WorkspaceShell declarations for both source getters, source binding, CTA, open/read, lifecycle, canonical apply, close and fence callbacks. It renders the actual Collaboration modal, access components and email reader. The source-mail holder uses controlled selection between two loaded messages; unrelated legacy mailbox/draft setters are stubs. Source screenshots therefore show a minimal mail holder, not the full inbox chrome.

Unlike the previous fixture, neither the history getter nor the summary hook is replaced. The exact missing boundary now runs in React and fails on baseline sources. Network transport is an in-memory canonical server fixture; a sessionStorage fixture key models retained server state across browser reload. Production code adds no storage. No credentials, live backend, mailbox sync or external requests are involved.

## Results

- Baseline bug: reproduced before the fix; reproduced again from immutable baseline files with the same final harness. Five pre-fix checks; zero browser errors/external requests.
- 18 focused browser runtime groups: active CTA, canonical lookup/read ID, real Resolve, publication before close, actual close/fence, resolved CTA, Priority suppression, Manual Priority independence, wrong-source rejection, reselect, hard refresh, resolved tabs/Reopen, immediate Reopen publication, pending Resolve after close, exact request ledger and browser errors.
- 39 additional browser regression groups through the hook-backed harness: three tabs, responsive/light/dark views, drafts, Shared/Internal sends, idempotent retry, Team, guests, transient secure links, safe Resolve confirmation, C3D active/resolved activity focus before acknowledgement.
- 28 frontend suites pass, including Collaboration source/summary/read/write/access/identity, C3D notification/exact-message, WorkspaceShell performance and reader regressions.
- 42 baseline freeze checks pass. Whole WorkspaceShell is byte-identical after accounting for the single getter argument change. Hook hydration, active memoization, subscriptions and scope fencing remain byte-identical. Summary store/API, lifecycle, close/read/send, C3D, renderer and access controls remain unchanged.
- Full frontend app and node TypeScript checks pass.
- No new background requests. Selected-mail reselect/close/tab changes add no requests. Reload uses existing list_summaries; CTA uses existing lookup/read; lifecycle uses existing resolve/reopen.

Machine-readable results: `pre-fix-results.json`, `runtime-results.json`, `ui-results.json`, `regression-results.json`, `freeze-results.json` in this directory. Focused screenshots cover active, missing resolved CTA before fix, resolved after fix, rehydration, resolved Settings and reopened source. Additional UI screenshots are generated under `/private/tmp/cuevion-c3p2c1-ui`.

## Reproduction commands

From `/Users/rutger/cuevion-app`, using installed dependencies only:

```sh
node frontend/fixtures/collaboration-source-access/build.mjs --baseline
node frontend/fixtures/collaboration-source-access/serve.cjs
```

In another terminal:

```sh
node frontend/fixtures/collaboration-source-access/verify-runtime.cjs --pre-fix
node frontend/fixtures/collaboration-source-access/build.mjs
node frontend/fixtures/collaboration-source-access/verify-runtime.cjs
node frontend/fixtures/collaboration-source-access/verify-ui.cjs
node frontend/fixtures/collaboration-source-access/regressions.cjs
node frontend/fixtures/collaboration-source-access/verify-freeze.cjs
./node_modules/.bin/tsc -p frontend/tsconfig.app.json --noEmit
./node_modules/.bin/tsc -p frontend/tsconfig.node.json --noEmit
```

The baseline build uses scoped git reads of only the two changed implementation files; it does not revert the checkout. The server binds only 127.0.0.1:4178 and serves generated artifacts from `/private/tmp/cuevion-c3p2c1`. Stop it with Ctrl-C after verification. Agent-browser was unavailable; installed Playwright/Chrome provided the browser verification without installation.

Next action: review the local commit `fix resolved collaboration source access` and this evidence. Push/deploy remain NO-GO under the current instruction. Post-commit SHA, parent, remote, staging and status gates are reported in the task response.
