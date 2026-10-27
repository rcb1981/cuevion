# Cuevion — Microsoft parity towards external testers

Status 22 October 2026. **LOCAL STAGING ONLY; not merged into GitHub main or deployed.**

## Non-negotiable reference

- Gmail and custom IMAP are the user-accepted functional and visual baseline. Do not change them to accommodate Microsoft.
- `frontend/tests/microsoft_verify_provider_freeze.py` and the baseline SHA256 manifest enforce unchanged source bytes for 645 protected files and the shared API outside the Microsoft-only block. Passing the guard is necessary, not sufficient; runtime regressions still require tests.
- No Vercel Preview, extra sandbox or Production build while finalizing the local batch. One owner-approved Production release after local build and gating review.

## Confirmed Production baseline

Microsoft Graph connection status, mailbox navigation, full-message reading, virtual priority/Filtered views, exact Inbox read/unread, exact Inbox-to-Archive and Inbox-to-Deleted Items have been released and manually checked by the owner. Remote image handling is opt-in, and some original images are broken even in Outlook.

## Local development in this batch

- Microsoft Compose, Reply, Reply All and Forward request preparation, selected-source identity checks and single-attempt Graph draft/send orchestration remain staged behind frontend **and** server-side Production gates.
- **Reply quote preservation improvement:** Graph creates the reply with a `comment` rather than overwriting its whole body using `PATCH`. The exact Inbox source and conversation are verified, and the resulting draft must visibly contain both the user's comment and an excerpt of the original message. Missing, hidden or forged quote evidence fails closed. The module never sends a draft during preparation.
- Forward continues to require verified original text and explicit recipients. Sources with original attachments and otherwise ambiguous cases remain blocked until attachment fidelity can be proved.
- The new Microsoft-specific source freeze verifier and manifest are included in the source tree for repeatable checks.

## Tests run on this complete local snapshot

- 462 no-network Microsoft Python tests passed (`python3 -m unittest discover -s api/inboxes -p 'test_microsoft_*.py'`).
- Full frontend TypeScript compilation passed (`tsc -b`).
- Microsoft compose request tests: 8 passed (`node --experimental-strip-types src/lib/microsoftComposeRequest.test.mjs`).
- Microsoft ComposePanel, MicrosoftMailboxReadOnlyView, Microsoft outbound client contract checks passed (Sucrase).
- Gmail OAuth, Collaboration Owner, Waiting/Priority, Team Roster and three Trash test groups: all passed.
- Source freeze: 645 protected files identical and approved shared code outside Microsoft-only region unchanged.
- Two **pre-existing** tests still fail: `mailboxDisplayName.test.ts` long email identity/truncation assertion, and `inboxConnectionApi.gmailOwnership.test.ts` expectations lacking the existing `knownBodyProviderMessageIds` wire field. Do not silently alter Gmail/IMAP code/tests to make them green.
- Full `vite build` remains unverified in this Linux session: the user ZIP contains Mac native dependency artifacts; full `tsc -b` passes, but Linux Rollup/esbuild native optional binaries are missing, and offline package installation is unavailable. A clean build on the user's Mac is still needed.

## Remaining Microsoft parity blockers — not ready for the testers yet

| Area | Actual gap before external testing |
|---|---|
| Send/new message | UI and server Production gates still block sends; verified mail-to-self owner acceptance required |
| Reply / Reply All | Quoted-message transport locally improved; exact recipient/Reply-To handling and real Outlook behavior still need mail-to-self tests |
| Forward | Reliable attachment preservation and non-quoted short/HTML message cases not yet supported |
| Drafts | Read-only; users cannot create/edit/resume/discard/send drafts like reference providers |
| Sent | Read-only browsing; send attempt confirmation does not mean recipient delivery |
| Conversations | Only currently loaded page grouped, not entire Microsoft mailbox |
| Search | Filters currently loaded pages, not historical server-side Microsoft search |
| Folders | Move to arbitrary folder not yet implemented |
| Attachments | Read/download restrictions; large attachments and outbound attachments not at Gmail/IMAP parity |
| Collaboration | Needs a targeted parity audit against the approved providers |
| End-to-end | Clean Mac build and owner real account testing still pending |

## Release and tester acceptance sequence

1. Continue Microsoft-only development and targeted Graph mocks for the remaining gaps.
2. Run the reference freeze and all tests; record known baseline failures separately.
3. Execute a clean `npm ci && npm run build` on the Mac checkout (not Vercel).
4. Ask owner approval for **one** Production release; do not push `main` until this approval, because GitHub may independently trigger deployments.
5. Owner tests controlled mail-to-self sends and mailbox operations, one action at a time; verify Sent, Drafts and thread behavior in Microsoft.
6. Audit all remaining Cuevion-wide product work. Only then invite testers.
