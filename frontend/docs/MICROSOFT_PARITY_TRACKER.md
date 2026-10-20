# Microsoft mailbox parity tracker (Cuevion)

Working agreement — 2026-10-19
- Microsoft is the design and implementation reference first.
- Keep Gmail/IMAP behavior and styling unchanged during Microsoft development.
- User is the sole Production tester. No extra Vercel Preview deployments by default.
- Develop on a separate local feature branch, test and bundle changes; **never merge or deploy to Production without explicit approval**.
- Only after Microsoft is accepted both visually and functionally may we propose migrating the design to Gmail/IMAP, requiring a separate user approval.

## Observed state in commit b93b5212 (Production)

| Area | Microsoft status | Parity target / acceptance |
|---|---|---|
| Three-column mailbox UI | Implemented and visually reviewed | Preserve compact folder rail, message list and unboxed reader |
| Compose | Visual-only modal; sending/draft saving disabled | Full compose, recipients, signatures, attachments, safe send and confirmations |
| Inbox / Filtered / Sent / Archive / Drafts / Deleted / Junk | Existing Graph read-only views | Preserve provider authority, pagination and exact message IDs |
| Search, All/Unread, sort, manual refresh | On loaded page | Clear scope, predictable empty states and filter behavior |
| Priority filter | Visible, intentionally disabled | Same meaningful Priority semantics as Gmail/IMAP |
| Sender learning | Show Less / Keep in Inbox; per-Microsoft mailbox isolated | Onboarding Low preferences, newsletters, category learning, protected mail |
| Learning menu | Not at parity | Important/not important, category label choices, Keep in Inbox and safe spam behavior |
| Context menu | Not present for Microsoft | Same relevant actions as toolbar, using the same guarded action handlers |
| Reply/Reply All/Forward/Archive/Delete/Move/read-state | Mostly visual/disabled; separately approved Auto-Read on exact Inbox message | Explicit safe actions, source/owner/folder guards, confirmations, no duplicate send |
| Message HTML | Backend deliberately converts Graph HTML to inert plain text | Safe rich HTML with tables/formatting, scripts blocked, links policy, isolated iframe |
| Images | Remote images intentionally not passed to UI | Remote images hidden until explicit consent, validated CID inline images |
| Attachments | Existing bounded metadata/read/download | Verify inline vs normal attachment parity and preview/download protections |
| Synchronization | 120-second foreground polling of the selected Microsoft folder | Eventually efficient background change processing even when app is closed |
| Gmail/IMAP design migration | Not started | Explicit user sign-off only after Microsoft acceptance and regression review |

## Rich HTML implementation phases

1. **Inert server-side projection — LOCAL ONLY, EXACT FOLDERS COMPLETE.** Bounded HTML allowlist for newsletter tables, typography, basic CSS, safe links and inert remote/CID image references. Exact Inbox, Archive, Sent, Drafts and Deleted responses now carry sanitized bodyHtml plus a plain-text fallback. List previews never carry HTML; Junk exact messages remain text-only.
2. **Second, independent frontend boundary — LOCAL ONLY, BROWSER FIXTURES VERIFIED.** Exact bodyHtml only; strict length/unsafe-element checks; separate DOM sanitizer, scriptless opaque iframe and restrictive CSP. Real headless Chromium exercised table rendering, presentation CSS, exact CID image rendering, remote-image privacy gating and message reset. This is synthetic, not live Graph/account acceptance.
3. **Images and attachments — LOCAL ONLY, CID METADATA FIRST PASS.** Per-message Show images is locally wired for validated HTTPS remote URLs after consent. Exact Graph folder attachment metadata now includes a bounded, inert `contentId` for inline file attachments (type-qualified Graph select), with matching client validation and tests. **CID image bytes now have a LOCAL exact-folder viewer path (except Junk)**: exact owner-scoped existing download endpoint after unique CID-to-attachment-ID match, max 8 images / 512 KiB each, PNG/JPEG/GIF/WebP signatures checked, no SVG, never a raw CID URL; React keyed isolation and stale in-flight results ignored. Uses transient data URLs (no object URLs). Synthetic Chromium rendering and sandbox/script rejection tests passed; actual Graph/newletter acceptance is still required; nothing is deployed. Keep tracking images blocked until opt-in.
4. **Integration and tests.** Exact Graph bodyHTML now uses server sanitization and strict client schemas for Inbox/Archive/Sent/Drafts/Deleted, with Junk and lists barred. Backend Microsoft suite and targeted frontend tests pass; synthetic Chromium rendering and privacy tests pass. The complete npm test suite still has the existing, timezone-dependent WorkspaceShell.replyQuote.test.ts failure; Gmail/IMAP were not changed. Full real-Graph mailbox/browser acceptance is still required.
5. **Live verification.** Validate an actual rich newsletter in Microsoft and check images both before and after consent, with dark/light modes and attachment variants. Get explicit approval before Production deploy.

## Non-negotiable guardrails

- Preserve existing OAuth owner and mailbox constraints; never substitute email address for identity.
- Never grant Mail.Send merely to render mail. Keep existing Server enforced read-only action restrictions.
- A browser cannot be trusted to sanitize alone; both server and client must fail closed.
- Never load third-party images/CSS/fonts merely because a message is opened.
- Do not claim full parity, background sync or image rendering before it is tested live.
- The local feature branch routes only verified exact Microsoft Inbox HTML through `microsoft_email_html_policy.py`. It is **not deployed** and does not change Gmail/IMAP. Inline CID image loading and non-Inbox HTML folder parity are not finished.

## Current local development

- Branch: `feature/microsoft-rich-html-foundation-local` (not a remote GitHub branch; no Vercel Preview).
- New server projection: `frontend/api/inboxes/microsoft_email_html_policy.py` (local-only).
- New client: `frontend/src/lib/microsoftInertHtmlTransport.ts`, `frontend/src/components/workspace/MicrosoftInertHtmlStage.tsx` (local-only).
- Tests: `frontend/api/inboxes/test_microsoft_email_html_policy.py`.
- Test result at integration checkpoint: 415 Microsoft backend tests pass; Microsoft HTML transport and iframe/fallback tests pass; Microsoft client API/UI and frontend build pass; Gmail/OAuth/Waiting/Trash regressions pass.
- Production remains on `b93b5212` until a later approved deploy.

## 2026-10-19 CID live-screenshot regression (LOCAL, NOT DEPLOYED)

- Production screenshot of Paulshine mail showed a real CID reference but the
  image remained blank. Live Graph metadata and file bytes for that one
  message have **not** been inspected; do not present its exact provider
  failure mode as confirmed.
- Verified code defects from Production: narrow Graph fileAttachment
  type matching (Graph also emits unprefixed type), CID case-sensitive matching
  (unlike Gmail/IMAP), exclusive dependence on isInline, rejected images
  above 512 KiB even though the authenticated download route supports 2 MiB,
  and strict trust in provider image MIME (octet-stream is possible).
- **Critical reproduced browser root cause:** after embedding a >512 KiB CID
  image as data:image/...;base64, the Microsoft sanitizer truncated
  parsed.body.innerHTML to 294,912 characters, splitting the img src
  attribute and discarding the image. A real 768,698-byte PNG failed in
  Chromium before the fix and loaded/decoded in Chromium afterward.
- Local correction: original server HTML remains strictly bounded, final
  HTML uses a larger overall bound but is never cut mid-tag, CID comparison
  reuses Gmail/IMAP-style canonicalization without changing those modules,
  exact authenticated Graph download still validates type and ownership,
  and only byte-verified PNG/JPEG/GIF/WebP up to 2 MiB each and 8 MiB
  aggregate are embedded. Remote tracking stays opt-in, and Junk text-only.
- **Not verified:** Paulshine's actual Graph CID list and bytes, live
  Microsoft mailbox acceptance, CID images >2 MiB, and unusual CID syntax.
  No additional GitHub push, Preview or Production release is authorized.


## 2026-10-19 automatic images UI preference (LOCAL, NOT DEPLOYED)

- User explicitly chose Apple Mail-style automatic image loading in the
  Microsoft reader, without a Show/Hide images button. The action is not a
  request to change Gmail, IMAP, Junk or mailbox write behavior.
- Remote HTTPS images now load when a message is opened; this means the
  remote sender/image host may learn that the user opened the message,
  unlike a Gmail proxy or consent-gated reader. HTTP/javascript/data remote
  links and active content remain blocked by server/client sanitizers.
- CID images still come exclusively from exact authenticated Graph
  attachment reads with byte-signature validation and size caps; no remote
  fetch is attempted for raw cid: references. The inline loading/error label
  appears only when a CID is unresolved and no metadata/download request is
  still in flight. No success banner or Show/Hide button remains.
- Synthetic browser validation is required after this preference change.
  Actual Outlook test-account acceptance and another Production release
  still need the user's separate approval.

## 2026-10-19 automatic images acceptance (LOCAL CHECKPOINT)

- Headless Chromium rendered a valid 805,650-byte PNG (same *size* as the
  provided live Graph inline attachment metadata) inside the isolated iframe.
  That PNG and Graph responses were synthetic, not the user's actual email.
- Confirmed automatic HTTPS remote image loading without Show/Hide controls.
- Prevented double remote downloads by waiting for the authenticated exact
  CID response before first iframe mount. Plain-text text is shown during
  CID processing. One remote fetch per Inbox and Sent test message passed.
- Cross-message state, owner/folder-scoped CID reads, scriptless iframe CSP
  and invalid HTML fallback remained verified in Chromium.
- CID and external images remain bounded to server/client policies; an
  oversized or invalid CID attachment can still fail closed. Auto-loading
  remote images can expose that an email has been opened to remote hosts.
- No Gmail/IMAP edits, GitHub feature pushes, Previews, or Production deploy.


## 2026-10-19 actual Microsoft attachment download rejection (LOCAL ONLY)

- The Outlook test account returned exact inline attachment metadata for
  Paulshine's PNG (contentId present, image/png, isInline true, 805650 bytes),
  but the separate download-microsoft-attachment endpoint returned
  microsoft_graph_invalid_response. The precise rejected Graph field has not
  been observed: **do not claim contentBytes was certainly missing**.
- The previous download endpoint depended entirely on the JSON attachment
  object including matching id, correct type, size and canonical contentBytes.
  Microsoft Graph also documents authenticated /$value for the raw contents
  of a file attached to a message. This read-only local patch retries via that
  endpoint only after invalid Graph JSON/record, first revalidating the
  attachment as one exact file in the selected authenticated folder/message's
  bounded Graph attachment listing. File byte size must equal the verified
  metadata size and be at most 2 MiB; tokens, raw response, IDs and URLs never
  enter client error messages or logs. No arbitrary URL or redirect permitted.
- New synthetic test covers the same 805650-byte metadata, invalid/missing
  contentBytes and raw /$value recovery; negative tests reject wrong IDs,
  item/reference attachments, oversize and length mismatch.
- Server logs now include only fixed low-cardinality failure stages:
  download_metadata, download_file_type, download_raw_size or download_other.
  No user-supplied text, message or attachment identifiers are logged.
- **Not yet verified:** raw /$value fallback on the user's actual Paulshine
  message, and exact failure reason behind the original 502. No deployment or
  GitHub push without explicit approval.


## 2026-10-19 image + newsletter live regression follow-up (LOCAL, NOT DEPLOYED)

- Screenshots of actual Microsoft test account still showed an unresolved
  805650-byte Paulshine CID image and a blank white body for a forwarded
  newsletter. No UI redesign is authorized.
- Live Production logs on deployment dpl_Em8EVJMoeonE7NsR65nMCXU9WpvL:
  two attachment requests at ~19:03 UTC recorded the fixed static category
  microsoft_attachment_invalid_response_stage=download_raw_size. The raw
  image bytes were not logged. Their exact size/format is still unknown.
- Microsoft Graph explicitly documents /$value for raw file-attachment bytes.
  Current production implementation rejects a raw response unless its byte
  count exactly equals the Graph attachment-metadata size. A valid PNG may
  therefore be lost even when it has all its data. Local patch allows only
  CRC-verified, complete, <=2 MiB PNG with an otherwise mismatched metadata
  size. Non-PNG data and truncated/corrupt PNG remain fail-closed. The
  Microsoft client also accepts the server-validated PNG's actual size.
- Gmail/IMAP in WorkspaceShell already have a richer presentation sanitizer,
  CID source resolution, text visibility adjustments and renderable-content
  fallback. Microsoft remains isolated and uses a restricted independent
  sanitizer; it now corrects very light text that would disappear against a
  white iframe background, returns readable text if HTML contains no visible
  text/image, and falls back to bounded bodyPreview if full text is absent.
- For Microsoft list rows with Graph hasAttachments=true, start the
  owner-scoped metadata request concurrently with exact-body loading rather
  than serially; inline-only rows with false hasAttachments still wait for
  exact HTML CID discovery. This reduces a known sequential round trip
  without changing total provider authority, writes or Gmail/IMAP.
- While CID bytes are pending, display the body text on the same white
  reading surface used by the iframe. Never abruptly flip from dark plain
  text to white rich HTML due to background choice.
- A real Chromium fixture verified a valid 805650-byte PNG despite Graph
  metadata claiming 805690 bytes, without releasing client mail state.
  Another real Chromium fixture verified legible light-on-white newsletter
  text and readable fallback instead of a blank iframe. Synthetic only.
- **Still outstanding:** accepting the actual user email (PNG format
  integrity, server byte response) and determining whether the particular
  energy newsletter includes style-only markup or empty sanitized HTML.
  No further GitHub push, Preview or Production deploy without approval.


## 2026-10-19 real-mail comparison and regression fixes (LOCAL ONLY)

- User-provided screenshots after Production deployment aa3c4669 show two
  independent failures: the Paulshine inline image remains broken, and a
  forwarded energy newsletter appears as an empty white reader. Opening the
  Paulshine message shows plain text followed by a delayed iframe swap.
- Vercel production logs on deployment dpl_Em8EVJMoeonE7NsR65nMCXU9WpvL
  recorded download_raw_size twice (19:03:08 and 19:03:54 UTC).
  **Verified:** Graph raw download fails server's exact size equality against
  metadata. **Unknown:** the actual returned byte count and whether those
  bytes contain a complete PNG; no raw mail, image bytes or tokens were logged.
- Microsoft Graph documents that fileAttachment size is the attachment
  size in bytes, and /$value provides raw bytes. The local recovery now checks
  the complete PNG signature, IHDR, CRC for *every* chunk, IDAT, exact final
  IEND, at most 4096 chunks and <=2 MiB. A different byte count is accepted
  ONLY for a complete PNG already verified by exact message/attachment owner
  and folder. Other formats and truncated PNG still fail closed. The client
  consumes server-confirmed actual PNG bytes, not the potentially inaccurate
  metadata size. Static operational error categories distinguish unverified
  PNG versus other raw payload formats without logging user data.
- Gmail/IMAP WorkspaceShell has a more mature HTML pipeline: CID to inlineSrc
  matching, renderable-content fallback, presentation CSS sanitization,
  light-mode contrast repairs and cached projections. Microsoft retains a
  provider-specific isolated HTML sandbox and now independently protects
  against blank sanitized bodies and white text on a white background.
  Empty HTML returns readable text or a bounded message snippet.
- Microsoft avoids dark-text-to-white-iframe background flash by showing the
  same white reading surface while waiting for CID bytes. When a list row's
  Graph hasAttachments is true, attachment metadata starts in parallel with
  exact-message retrieval; inline-only messages still get CID detection from
  the exact HTML before requesting metadata. No Outlook mutation added.
- Headless Chromium confirmed a synthetic complete 805650-byte PNG with a
  different metadata size resolves and displays, and confirmed the contrast
  repair/blank-HTML fallback. This is not yet a successful live Paulshine or
  newsletter rendering acceptance.
- No Gmail/IMAP implementation changed. No remote GitHub push, Vercel Preview
  or Production deployment is authorized for this local WIP without a fresh,
  explicit user approval.


## 2026-10-19 product decision: complete Microsoft functionality before UI sign-off

The user approved the existing three-column Microsoft layout as a working
design direction but does NOT consider the Microsoft inbox accepted
or finished. Do not redesign it or treat an HTML test as full parity.
Acceptance requires functional completeness before final visual review:

- Rendering: actual Outlook-test-account Paulshine CID renders; newsletters
  show legible HTML; no text-to-white flash or long serial attachment wait;
  all folders handled.
- Learning: categories and Important/Not Important; protected senders;
  scoped sender preferences, Filtered/Inbox and priority decisions.
- Interactions: context menu with real shared toolbar actions;
  All/Unread/Priority, folder switches, search, sort, pagination, refresh,
  read states and unread counts.
- Writes: Compose/Reply/Reply All/Forward, recipients/CC/BCC, attachments,
  drafts, read/unread, archive/delete/restore/move, bookmark/flag and
  confirmations; exact owner/ID scoped, permission-gated and separately
  reviewed before Production enablement.
- Sync: safe background/foreground refresh and persisted learning,
  including loading/error/reconnect states.
- Final review: unit/API and real Chromium/Graph acceptance using the sole
  Outlook tester's account. Inspect every button and preserve approved
  three-column layout. ONLY after user approval of the completed Microsoft
  inbox, port its visual appearance to Gmail/IMAP without altering their
  already-stable provider behavior.

Current code still explicitly enables MICROSOFT_READ_ONLY_PILOT, which blocks
most Microsoft writes. The UI controls and Compose remain previews;
automatic Inbox unread-to-read is separately approved. Past approval for
a Microsoft HTML-only deployment is not general consent to enable writes.

No GitHub push, Vercel Preview or Production deploy without explicit approval.
Gmail/IMAP implementation remains untouched throughout Microsoft completion.


## Wednesday October 21 visual-demo milestone (LOCAL ONLY)

- The user wants to show a common inbox appearance on Wednesday, while
  Vercel usage is near its limit. Do not introduce Vercel Previews or multiple
  deployments. Prepare and verify one bundled local candidate, then require
  separate explicit Production approval.
- Gmail and IMAP already share the existing MailboxView. Apply the Microsoft
  presentation language to that shared view in a narrow CSS/JSX pass:
  flatter header, folder rail, list rows, border separators, and reading pane.
  Keep every Gmail/IMAP provider action, Learning and context-menu handler
  intact; do not replace their proven transport/auth/sync engines.
- Microsoft still needs remaining Graph write workflows, Learning and Priority,
  context menu and background sync before being described as functionally
  complete. A unified visual demo is NOT a full-mailbox acceptance or
  permission to enable write actions.
- Visual parity is provisionally source/build/synthetic-browser tested. Real
  Gmail, IMAP and Microsoft accounts must be inspected by the sole Production
  tester before final visual sign-off.


## 2026-10-19 visual-header and action-preservation gate (LOCAL)

- The user requires that Gmail/IMAP functionality and CTA semantics stay
  EXACTLY as before. Only visual arrangement may change. Neither existing
  Gmail/IMAP action handlers, keyboard shortcuts, drag-and-drop logic,
  button disabled states, nor mail transport/sync/learning should change.
- Header placement for all provider mailboxes: Back on the left, mailbox
  display name and address on the right. The Microsoft-specific status
  banner (Microsoft 365 Mail / Auto-read enabled / other actions disabled)
  is removed from the top-right presentation; true underlying read-only
  guards remain active and the action buttons remain disabled as before.
- Static TypeScript AST audit compared the pre-visual Gmail/IMAP MailboxView
  with the local updated view: 195 event handler bindings and 26 disabled/
  draggable/href/haspopup bindings before and after; **zero differences**.
  This is strong static preservation evidence, not real account E2E proof.
- No new deployment without explicit user approval. Do NOT describe Microsoft
  as fully functional until real Graph actions are separately enabled/tested.


## 2026-10-19 post-Production visual QA follow-up (LOCAL ONLY)

The user inspected Gmail and Microsoft live and found that the initial shared
styling pass was incomplete. The follow-up must not reimplement Gmail/IMAP
behavior. Correct in the existing shared MailboxView:
- Align Compose with the left folder column; Sync in the list column;
  message actions above the right reader, preserving original callbacks.
- Match Microsoft's accent-lined selected Inbox/folder presentation and
  reduce its heavier Gmail styling and oversized title.
- Render **one-message threads** without an outer rounded/bordered article
  while keeping the remote-image "Show images" behavior unchanged.
- For conversations of two or more messages, preserve each separately boxed
  member, expand/collapse/quote behavior and per-message controls.
- Hide only the duplicate lower reply/action strip in the *single-message
  split reader*. It remains present in source for the original behavior and
  available through the established toolbar; multiple-message threads and
  the full-message modal retain their original action presentation.
- Preserve all DoubleClick, context menu, Collaboration, Learning,
  ownership/send/delete/archive guards, drag/drop, mail transport and sync
  from Gmail/IMAP. Never infer correctness from visual appearance alone.
- Tests: source AST regression hashes for 195 event bindings and 26 action
  guards, original thread structure/full-message contracts and Chromium
  grid/single-vs-thread CSS. No new Production build until the user approves
  the final locally tested candidate.


## 2026-10-20 Gmail/IMAP HTML reader viewport-height correction (LOCAL ONLY)

- User's live Gmail screenshot showed a tall invoice/message cut short at an
  iframe stage only ~half the viewport high, leaving a large unused dark area
  below it in the right reader.
- Root cause: EmailHtmlStage height was limited to
  clamp(320px, 50dvh, 600px); the HTML itself was not proven missing.
- Keep Gmail/IMAP provider functionality, remote-image opt-in, sanitization,
  link policy and the secure iframe sandbox unchanged. Do not enable
  allow-scripts or allow-same-origin and do not expose HTML to parent DOM.
- Set the iframe stage to fill nearly all available reading height:
  max(320px, calc(100dvh - 250px)) with existing viewport safety cap.
  Long messages retain safe internal scrolling; this is NOT content-aware
  full-document auto-height and must not be represented as such.
- Chromium smoke: sandbox remains opaque, scriptless, stage heights:
  1046px viewport => 796px, 900=>650, 720=>470, 360=>264.
- Do not deploy until user separately approves another Production build,
  given near-exhausted Vercel on-demand budget.


## 2026-10-20 final header-detail local batch (NOT DEPLOYED)

- User's side-by-side Gmail/Microsoft screenshots: keep Gmail/IMAP's more
  clearly placed Sync icon at the left of the message-list toolbar; add
  Microsoft-style folder heading and visible conversation/unread counts.
- Counts are sourced from sortedMessages (post-search, active filter,
  thread-deduplicated rows), not inferred provider-wide total counts.
  The displayed text explicitly identifies these as shown.
- Preserve existing shared-mailbox sync feedback: ephemeral Updated or
  Syncing continues via mailboxSyncPresentation.message. No timestamp
  is invented; Microsoft's real last-updated clock is not available here.
- Keep header separators aligned with the three visual columns using
  existing border-b/border-r classes. Do not change split resizing.
- Filtered and Shared have the same selected green border/background as
  ordinary folders; click/drag/drop/state actions are unchanged.
- User-approved visual changes are separate from Production release approval.
  Bundle this with the local HTML-height fix and do not deploy without a
  separate explicit Production approval, given Vercel usage limits.


## 2026-10-20 — unified mailbox visual parity candidate

- Microsoft desktop uses the same 152 px folder / 37% message list / reader grid and full-width Compose / Sync / guarded message-action toolbar as Gmail/Custom IMAP; redundant refresh in the Microsoft list controls is hidden.
- The existing Microsoft folder selection, list fetching, sort, pagination, sender learning, and server-confirmed auto-read remain untouched. Microsoft write operations remain read-only gated and nonfunctional actions stay disabled.
- Microsoft HTTPS remote images now require explicit **Show images** consent, with the iframe CSP blocking remote images beforehand. Authenticated CID images remain separately bounded and supported, including remote-image-only HTML bodies.
- Microsoft HTML iframe height now matches the Gmail/IMAP viewport height. The existing scriptless opaque iframe, server HTML trust checks, and attachment owner gating remain in force.
- The **Filtered** child folder gains an extra 16 px indent in Gmail/IMAP and Microsoft, preserving the green active border.
- Gmail/IMAP full-message modal defaults become narrower (max 850 px, width 56vw) and shorter (62dvh), retaining keyboard and pointer resize.
- Build and focused Microsoft/Gmail/IMAP contracts passed locally. Full npm test baseline has two pre-existing failures in different environments: replyQuote timezone sensitivity (UTC) and gmailOwnership extra knownBodyProviderMessageIds field (Europe/Amsterdam).
- **Pending after deployment:** real-browser acceptance of Microsoft visuals, CID PNG test mail, previously blank HTML newsletter, and subsequent functional Microsoft parity rollout (Collaboration, threads, pop-up, Graph writes).

## 2026-10-20 — Microsoft functional parity increment (local, not yet deployed)
- Real Microsoft sender Learning replaces the ad hoc Show Less button in Inbox/Filtered. Menu offers Important / Normal / Show Less and Demo, Promo, Business, Finance, Update, Reply, Other categories. Saving persists only within authenticated workspace-user-mailbox scope; the Gmail/IMAP learning store is not mutated by these new actions.
- Priority filter is now live for loaded Microsoft rows, based on explicit Important sender rules and available trusted message-priority fields; it does not claim provider-wide Priority analysis or full Gmail/IMAP classifier parity.
- Learning routing preserves protected/uncertain messages. When learning changes the current folder partition, stale selected-message and attachment requests are revoked, with informative feedback.
- Microsoft now supports double-click to open a modal for the exact selected message, retaining scriptless HTML, image consent and CID metadata. A non-mutating right-click menu opens messages, the full-message modal, or Learning.
- No new Graph writes, permissions, or mailbox mutations were enabled. Compose/reply/forward/delete/archive/move remain disabled in Production. Collaboration, full thread grouping, comprehensive priority intelligence and background sync still require dedicated implementation.
- Focused Microsoft and Gmail/IMAP source/regression tests passed. Frontend TypeScript/Vite builds passed. Full npm test baseline still fails on pre-existing Gmail ownership DTO expectation (Europe/Amsterdam timezone); the UTC reply-quote expectation also remains timezone-sensitive.
- Real Microsoft-browser acceptance is required after a separately approved production deployment.

## 2026-10-20 — compact cross-provider Learning menu (local only)
- Microsoft Learning adopts Gmail/IMAP's compact 244 px direct-action menu: Mark as important, Mark as not important, expandable Change label..., Keep in inbox and Move to spam. Existing Microsoft category and priority rules remain saved under the authenticated workspace-user-mailbox key. Current priority/label may show a checkmark.
- No new Microsoft Graph writes were enabled: Move to spam is shown but disabled with an explicit reason, unlike its available Gmail/IMAP counterpart. This is *visual and supported-action parity*, not full functional provider parity.
- Microsoft security notification sender domain safeguards prevent outdated Show Less preferences from hiding account-security mail.
- This menu no longer exposes a new Show Less selection. Previously saved Show Less rules still apply; full cross-provider Show Less flow requires a follow-up design/behavior decision.
- Local frontend build and focused Learning/Filtered/Microsoft/Gmail/IMAP contracts passed. A live Microsoft browser acceptance check remains pending after a separately approved deploy.


## 2026-10-20 — Microsoft provider-ID conversation navigation (local checkpoint)
- A selected Microsoft message can display other messages from the **same Graph providerThreadId**, but only when both are present in the currently loaded, visible folder page (max 25). The projection requires the same serverMailboxId and providerFolder; it never infers threads from subjects, senders or quoted body text.
- The right-side Microsoft reader and full-message modal show a compact oldest-first conversation navigator when two or more matching messages are present. Selecting another item calls the existing exact-message read path with current selection/cancellation guards; no bulk fetch, send or Graph mutation is introduced.
- This is **page-scoped conversation navigation**, not cross-page/full-history thread parity. Gmail/Custom IMAP code and persistence are unchanged.
- Microsoft Learning chooser now matches Gmail/IMAP category order (Promo, Update, Finance, Business, Demo, Other), removes the unnecessary expand caret, and retains the legacy Reply category in stored rule validation so previous preferences remain readable.
- New tests cover cross-owner, cross-folder, duplicate provider IDs, invalid/missing thread IDs, 25-message bound, chronological order, single-message state, modal integration, and maintained read-only guards.
- Production is **not** updated until a separate approved deployment. A real Microsoft mailbox/browser check must confirm behavior with an actual two-message Graph conversation.
