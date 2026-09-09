# C3P1 — Collaboration workspace presentation

UI-only work on `perf-1`, based on accepted local C3D
`4638ec238cc1387b209501a66d4cebf8e7fd9209`. Baseline remote main:
`9ac9aafda5863cbd6844ffe1a6410579e9185715`. Baseline staging was empty;
status contained exactly the three prescribed pre-existing protected entries.
The explicit baseline-gate staged-name command was used on the empty index.
Protected contents were not accessed. No push, deployment or Production access.

## Trace established before editing

| Seams | Exact source and component/function |
| --- | --- |
| Owner modal, title, status, responsive shell | `../workspace/WorkspaceShell.tsx`: `MailboxView` owner portal, `WorkspaceModalLayer`, `getCollaborationOwnerStateLabel` |
| Normal Close, duplicate footer Close, Escape, link protection | Same: header/footer, `requestCloseCollaborationOverlay`, existing `handleEscape`, secure-link `SettingsConfirmationModal` |
| Access, Team, External, create/invite, guest history/revoke | `CollaborationAccessPanel.tsx`: component, `submitStart`, `submitAddTeamMember`, `submitGuestInvitation`, `confirmRevoke`, `getExternalGuestStatusLabel` |
| Transient link and copy | Same: `secureLinkPanel`, `copySecureLink`, `clearSecureLink`, existing confirmation |
| Activity, Shared, Internal | `MailboxView`: canonical owner projection, two existing composers, `collaborationMessageRefs`, highlight attributes |
| Resolve/Reopen | `MailboxView`: lifecycle footer, `transitionCanonicalCollaboration` |
| Source context | Canonical owner Source email section |
| Selected-mail CTA | `renderMessageCollaboration`, live binding and split/full surface guard |

## Presentation changes

Compact title, small lifecycle text and subject; one normal Close action in the
header. The footer duplicate is removed; both secure-link confirmations and
Escape routing remain. Resolve/Reopen stays in the footer with a quieter text
button. The canonical lifecycle label mapping is unchanged (resolved displays
“Ended”). Close, lifecycle and composer buttons have at least 40px height and
visible keyboard focus. Text uses existing muted tokens for light/dark readability.

Source metadata is a native, keyboard-accessible disclosure. Activity precedes
composers and Participants & access in both DOM and visual order. Activity keeps
author, role, visibility/type, timestamp, body, order, canonical IDs, refs and
notification highlight. Internal notes use a subtle different surface. The
nested activity scroller is removed; one body scroll area remains. Composers
sit beside one another from `sm` upward, with smaller resizable text areas.

Team and External sections share an understated two-column layout when space
permits, and stack on mobile. Names lead compact rows; owner/team roles remain.
Active/pending guests retain prominence and explicit Revoke actions. Revoked,
expired and left guests remain present with regular-weight muted names and text
states. No filtering, capacity, entitlement or invitation behavior changes.

The create selector has two compact radio choices; the selected form alone is
shown, as before. The secure-link block has less padding, one visible heading,
concise one-time copy, its labeled read-only input and existing Copy/Close link
actions. Token storage, lifetime, clipboard behavior and loss confirmation are
unchanged. Revoke uses the existing destructive color family without a primary
filled button. The selected-email CTA is a compact outlined pill with its exact
original click handler and split/full duplicate guard.

`WorkspaceModalLayer` has one optional presentation-only `compact` prop, used
only by this owner modal. Its default layout and scroll-lock effect are unchanged.
The compact mobile layer padding fits the existing viewport-bounded modal and
avoids the old extra outer scroll.

## Verification

- **19 focused local browser checks**, using the actual modal JSX, actual modal
  layer, actual close guard, real AccessPanel and real owner API clients.
- **28 existing frontend suites** pass: 18 owner/guest/summary/Priority/mailbox/
  performance suites, plus six C3D suites (148 cases), two C3D0 suites (53 cases),
  Activity and Collaboration identity.
- Existing **55 external-guest browser scenarios** and **8 C3D notification
  browser checks** pass.
- **314 local backend regressions** pass, zero failures/errors/skips. Includes
  C3B2.1 discovery repair (20), summary (22), application (72), C3C notification
  suites and the 122 Redis/Lua cases including C2G5/C2G6. Runtime: repository
  Python 3.11, `PYTHONDONTWRITEBYTECODE=1`, isolated Redis Unix sockets. Guards
  report zero protected-file attempts and zero non-local socket attempts.
- Full guarded frontend TypeScript: 95 app roots / 157 sources and 2 config roots
  / 57 sources, **zero diagnostics**. No dependencies installed.
- Existing performance fixture remains: identity work 10,000 → 100; keyword
  family work 29,000 → 2,900.
- A TypeScript AST audit against C3D masks presentation JSX and compares the
  remaining code, plus all event/ref attributes independently. All non-JSX
  logic is unchanged after excluding the optional compact presentation prop.
  All 646 WorkspaceShell and 25 AccessPanel event/ref bindings are preserved;
  the sole removed binding is the duplicate footer Close. No new effect, timer,
  request, polling, mailbox scan, authority or notification behavior was added.

The browser fixture substitutes mailbox activation/state and composer/lifecycle
state publication with explicit fixture adapters. It is not a full authenticated
mailbox end-to-end test. API payloads go through the existing clients; the actual
WorkspaceShell behavior remains covered by the existing suites and the AST audit.
The parent overlay confirmation is represented by a fixture dialog using the
unchanged close guard; the AccessPanel link/revoke confirmations are real.
Production and real Redis/Upstash service behavior were not tested.

The browser skill CLI is not installed. The verification uses already installed
Playwright and Chrome; all non-local browser requests are blocked. Initial test
failures were outdated copy expectations and fixture timing/binding assumptions;
those were corrected without changing product behavior.

### Comparable layout measurements

Same fixture: two activity entries, two Team participants and five guests.
Values are scroll-body content height, not a promise of shorter outer bounds
when a long collaboration fills the viewport.

| Viewport | C3D content | C3P1 content | Reduction |
| --- | ---: | ---: | ---: |
| Desktop 1600×1000 | 1544px | 891px | 42% |
| Laptop 1280×800 | 1544px | 891px | 42% |
| Tablet 768×1024 | 1544px | 984px | 36% |
| Mobile 375×812 | 1912px | 1410px | 26% |

All four: no horizontal overflow, reachable footer/actions, clean wrapping and
one body scroller. The old mobile fixture had two inner scroll areas and an
828px layer in an 812px viewport; the new layer fits 812px. Long unbroken content,
25 activity rows, exact activity focus/highlight, mobile secure link and mobile
create form were also checked. Screenshots in both themes were inspected.
Keyboard checks cover Close/Escape, native source disclosure, logical tab order,
Team trigger, Revoke trigger, CTA and visible focus. Guest/status text is not
color-only; destructive actions retain explicit labels.

Run the new browser fixture from `frontend` with `PLAYWRIGHT_MODULE` pointing to
the already installed Playwright module. Its default needs no baseline files.
Optional `C3P1_BASELINE=1` uses the two exact pre-edit snapshots under
`/private/tmp/c3p1-workspace-before.tsx` and `/private/tmp/c3p1-access-before.tsx`.
They can be reproduced with exact-path `git show` at the C3D commit.

Logs: `/private/tmp/c3p1-browser-final.txt`,
`/private/tmp/c3p1-before-browser.txt`, `/private/tmp/c3p1-typecheck.txt`,
`/private/tmp/c3p1-frontend-regressions-final.txt`, `/private/tmp/c3p1-overlap.txt`,
`/private/tmp/c3p1-guest-browser.txt`, `/private/tmp/c3p1-notifications-browser.txt`,
`/private/tmp/c3p1-backend-regressions.txt`, `/private/tmp/c3p1-freeze-audit.txt`.
The browser log prints the exact local screenshot directory.

## Inspected file scope

Direct product/source/test reads (repository-relative):

- `frontend/src/components/workspace/WorkspaceShell.tsx`
- `frontend/src/components/workspace/WorkspaceShell.collaborationOwnerRead.test.ts`
- `frontend/src/components/workspace/C3D_NOTIFICATIONS_HANDOFF.md`
- `frontend/src/components/workspace/ServerNotifications.web.test.mjs`
- `frontend/src/components/collaboration/CollaborationAccessPanel.tsx`
- `frontend/src/components/collaboration/ExternalCollaborationGuestView.web.test.mjs`
- `frontend/src/components/collaboration/CollaborationWorkspace.polish.web.test.mjs`
- `frontend/src/lib/collaborationOwnerWriteApi.externalGuest.test.ts`
- `frontend/src/lib/collaborationOwnerReadApi.test.ts`
- `frontend/src/lib/collaborationOwnerWriteApi.ts`
- `frontend/src/lib/collaborationOwnerApiTransport.ts`
- `frontend/src/index.css`
- `frontend/tailwind.config.js`
- `frontend/api/collaboration/test_lua_redis_integration.py` (test timing only)

Existing exact temporary validation runners and their output logs were also
read. Tests/compiler load their normal non-protected dependencies under the
existing guards. No backend authority implementation file was manually opened
for this presentation work; no backend file was edited.

Changed files: the two owner presentation files, the existing owner-read test
(copy expectations), the new browser test, and this handoff. Notifications,
Priority, C3D0, guest UI, client transports and backend implementation files
remain unchanged. Bytecode writing was disabled for every Python invocation;
no new `.pyc` entry appears in git status.

## Prepared manual feeling test — not deployed

After a separately authorized deployment:

1. Open an active Collaboration from selected mail. Judge CTA size/discoverability,
   exact target, compact header, lifecycle context and one normal Close action.
2. Inspect Team and External lists, including active/pending/revoked/expired/left
   guests. Add one eligible Team member and invite a guest. Judge whether the
   difference is obvious and any essential action feels hidden.
3. Inspect/copy the transient link. Try Close and Escape, go Back, then explicitly
   confirm losing the link. Reopen and confirm the unavailable token is not shown.
4. Post a Shared Message and Internal Note. Inspect author/type/time/content and
   verify from the external guest that the Internal Note remains unavailable.
5. Revoke the guest; retain history. Resolve, inspect the ended collaboration,
   then Reopen using the quieter footer action.
6. Repeat the visual pass on desktop, tablet and mobile, in light/dark themes.
   Judge calmness, scan effort, vertical length, clear primary actions, quieter
   secondary actions, form/link usability and scroll behavior. Open a notification
   targeting old activity and verify exact highlight/navigation still feels right.

Local C3P1 implementation/test gate: GO after the commit/post-commit checks.
Push/deploy gate: NO-GO in this task. Next action: review the local UI-only commit
on top of C3D; obtain a separate instruction before publishing either commit.
