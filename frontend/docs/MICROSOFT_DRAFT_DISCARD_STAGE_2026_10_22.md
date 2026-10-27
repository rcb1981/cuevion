# Cuevion Microsoft Drafts — staged exact soft discard

Date: 2026-10-22. **LOCAL ONLY. Not in GitHub main, not deployed.**

## Change

A draft can only be discarded into Microsoft Deleted Items after a server-side owner and OAuth-scope check, an exclusive mailbox mutation lease, and a fixed-folder Graph preflight verifying the exact immutable message ID, `isDraft = true`, and the authoritative Drafts folder ID. The single non-repeatable Graph move must return the identical ID and the actual Deleted Items folder ID. A separate read of the exact item in Deleted Items confirms the destination before the API can return `discarded: true`. No permanent deletion, arbitrary move destination, Graph path, refresh token, email body, or folder name is accepted from the client.

A fail-closed Production gate (`_PRODUCTION_DRAFT_DISCARD_RELEASED = False`) denies access **before** parsing the browser request or touching the owner/token store. The client UI is independently gated (`MICROSOFT_DRAFT_DISCARD_ACTIONS_RELEASED = false`); current Production remains unchanged. No environment variable grants write access.

## Validated locally

- 474 Microsoft Python backend tests passed, including 12 draft-discard safety tests.
- Five independent Microsoft client suites passed: Drafts, exact Draft Discard, Sent, attachment metadata, and download.
- Gmail/IMAP freeze passed: 645 protected source files byte-identical and shared API outside the Microsoft-only region unchanged.
- React/TS syntax transpilation can be run independently; full TypeScript compile did not complete here before timeout with dependencies extracted from the user's Mac ZIP. Do not claim a completed frontend build.
- No Graph requests to a real Microsoft account. No production deployment, sandbox, or tester rollout.

## Next gates

1. Complete Microsoft Drafts create/edit/resume/send, outbound attachments, search and conversation parity.
2. Clean frontend install/build on the owner's Mac without a Vercel build. Diagnose any actual TypeScript errors.
3. Re-run full provider suite and the freeze guard.
4. Independently approve the Drafts discard Production gate and run controlled mail-to-self / test drafts on the sole owner's account, before testers are allowed.

## Known limitations

- Existing Compose, Reply, Reply All and Forward outbound submission is separately blocked in Production.
- An ambiguous Graph move is never retried automatically; the owner must refresh Drafts and Deleted Items before taking another action.
- Drafts discard does not yet make Microsoft Drafts fully equivalent to Gmail/IMAP. This stage must not be described as feature-complete.
