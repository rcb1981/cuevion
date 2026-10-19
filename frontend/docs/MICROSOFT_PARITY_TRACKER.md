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
