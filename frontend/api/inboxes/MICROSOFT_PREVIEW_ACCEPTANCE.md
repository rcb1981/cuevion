# Cuevion Microsoft 365 — controlled Preview acceptance plan

Status: **preparation only**. No Microsoft app credentials were configured,
no GitHub push was made, no Preview or Production deployment was started,
and no real email was sent during this preparation.

## 1. Preconditions and Git safety

- Worktree: `/vercel/cuevion-microsoft-graph`, branch
  `feature/microsoft-graph-runtime`. Do not merge or push OOO or main.
- Recheck remote main SHA and branch diff immediately before any push.
- A push of this feature branch triggers a Vercel Preview deployment.
  Warn the user and obtain explicit approval BEFORE the first push.
- No Preview status polling: one deployment status check per user turn;
  if building or queued, stop and await the next user turn.
- Keep all OAuth secrets in Vercel Environment Variables; never Git.

## 2. Pick ONE concrete and stable Preview deployment host

Preview origin must be an exact HTTPS hostname ending in `.vercel.app`.
The host must match the Vercel `VERCEL_URL` or `VERCEL_BRANCH_URL` value
provided to the deployment. No wildcards, localhost, other domains,
trailing slash, non-HTTPS URL, path, port, username, query or fragment.

Use a placeholder here, not a made-up deployed URL:

    https://<one-real-cuevion-preview-host>.vercel.app

Once the first Preview deployment URL is known, decide which deployment
or branch alias will be the ONE test URL; get the exact host from Vercel.
The first Preview may intentionally fail closed before these settings
are configured. Set exact branch-scoped Preview variables and redeploy
only after the user authorizes those changes.

## 3. Vercel Preview variables (no values in Git)

| Variable | Preview setting |
| --- | --- |
| `VERCEL_ENV` | `preview` (Vercel-generated) |
| `CUEVION_MICROSOFT_PREVIEW_ORIGIN` | Exact chosen `https://<host>.vercel.app` |
| `CUEVION_APP_URL` | Identical exact origin; used by existing callback routing |
| `MICROSOFT_OAUTH_REDIRECT_URI` | Exact `<origin>/api/inboxes/oauth-callback` |
| `MICROSOFT_CLIENT_ID` | Microsoft Entra application (client) ID |
| `MICROSOFT_CLIENT_SECRET` | Secret stored securely in Vercel; never copy to chats/logs |
| `MICROSOFT_OAUTH_TENANT` | Approved tenant ID or appropriate tenant selector |
| `CUEVION_OAUTH_STATE_SECRET` | Existing secure signing secret, or separately configured new secret |
| `VERCEL_URL` / `VERCEL_BRANCH_URL` | System-generated exact hostname; one must match the pinned host |
| `MICROSOFT_OAUTH_SCOPES` | Optional; the required default scopes are already enforced |

Preview variables should be limited to the Microsoft feature branch
where Vercel branch-scoped configuration is available. Do not overwrite
Production variables or install credentials in the local Git checkout.

The new Microsoft host gate requires every relevant value to match
exactly before Outlook OAuth or its send/status endpoints operate.
Production continues to require `https://app.cuevion.com` with the
canonical callback and Host/Origin; Preview cannot impersonate it.
Gmail/IMAP origin checks are NOT changed by this Microsoft-only gate.

## 4. Microsoft Entra app registration

- Register the OAuth redirect as a **Web** platform redirect (not SPA),
  because the server performs the code exchange with a client secret.
- Register the exact callback URL shown above; no wildcard URIs.
- Microsoft Graph delegated permissions: `User.Read`, `Mail.Read`,
  `Mail.ReadWrite`, `Mail.Send`, plus `openid`, `email`, `profile`,
  `offline_access` in the OAuth scope request.
- Request admin consent if required by the organization's tenant policies.
- Choose the supported tenant strategy deliberately; don't assume a
  single-tenant registration works with `common` or `organizations`.
- Verify the Auth0 app registration permits this same Preview origin for
  the browser session/callback as necessary. Never widen to `*.vercel.app`.
- If Vercel deployment protection interferes with the third-party
  callback, handle it using an approved scoped access flow; do not
  turn off general deployment protection without user approval.

## 5. Phased acceptance — NO real sends until separate authorization

1. **Static / security tests:** verify exact Host, optional forwarded Host,
   Origin, callback host, client ID/secret availability, redirect matching,
   auth-owner binding, code verifier/state, no unauthorized Graph calls.
2. **Browser connection:** owner signs in to Preview with an expressly
   designated Microsoft TEST mailbox; check initial consent, verified
   `/me` account identity, mailbox registration, safe reconnect and
   no credentials exposed to browser responses.
3. **Read-only:** load Inbox and Sent, open one known test message, switch
   between folders and verify stale-response cancellation.
4. **Single message mutation:** with explicit approval, mark a known TEST
   message read and unread. Inspect scope gate and actual Graph readback.
5. **Send test gate:** ask the user for explicit permission and the exact
   TEST recipient before doing any live send; confirm subject/content.
   Never send to real label contacts or artist addresses by default.
6. **Controlled send:** click Send once; check `202 accepted_by_microsoft`
   is NOT misrepresented as delivery or verified Sent placement.
7. **Sent-copy proof:** use Check Sent status on the SAME attempt ID.
   A missing copy is PENDING, not failed. When present, verify immutable
   ID, non-draft status and exact Sent parent folder. No automatic resend.
8. **Reply test:** separate approval, exact Inbox test source, verify
   recipients/Reply-To and conversation ID, single send and eventual Sent.
9. **Failure/race cases:** lost 202, rate limit, expired Microsoft token,
   reconnect while Graph is returning, duplicate attempt, unauthorized
   owner/workspace and cross-origin requests.

## 6. Release gates and blockers

- Current Vercel metadata inspection on October 12, 2026 found no
  `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`,
  `MICROSOFT_OAUTH_REDIRECT_URI`, `MICROSOFT_OAUTH_TENANT` or
  `MICROSOFT_OAUTH_SCOPES` entries. This is a presence check only;
  no secret values were decrypted. The scopes override is optional.
- A real Preview URL is not yet selected/approved for this branch;
  pin it only after a user-approved Preview push.
- The Sent status checker works while the composer stays mounted.
  A persistent outbound-attempt recovery/history view is still needed
  before Production readiness.
- Microsoft Mobile workspace, full paging, attachments, HTML fidelity,
  some message actions, and global unread/priority calculations remain
  separate implementation tasks.
- The broad Gmail ownership suite has 87 known baseline failures and
  `WorkspaceShell.inboxReadiness.test.ts` has a pre-existing baseline
  failure. Do not attribute them to Microsoft or silently waive them.
- Promotion to Production requires its own diff/PR review, browser
  acceptance, rollback plan and EXPLICIT user approval.

## 7. Safe rollback

Stop only the Microsoft Preview test branch or disable its exact Preview
pin (e.g. remove `CUEVION_MICROSOFT_PREVIEW_ORIGIN`) and redeploy after
explicit user approval. Without that pin the Microsoft gate fails closed.
Do not affect Gmail/IMAP, the OOO branch, or Production.
