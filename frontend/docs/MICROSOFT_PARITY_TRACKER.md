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
