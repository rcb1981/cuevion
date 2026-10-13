# Cuevion Microsoft 365 — controlled Preview acceptance plan

Status: fixed custom domain and local code preparation. Existing feature
branch already has an older Preview deployment; the custom-host patch has
not been pushed or deployed. No Production change or real mail send.

## 1. Preconditions and Git safety

- Worktree: `/vercel/cuevion-microsoft-graph`, branch
  `feature/microsoft-graph-runtime`. Do not merge or push OOO or main.
- Recheck remote main SHA and branch diff immediately before any push.
- A push of this feature branch triggers a Vercel Preview deployment.
  Warn the user and obtain explicit approval BEFORE the first push.
- No Preview status polling: one deployment status check per user turn;
  if building or queued, stop and await the next user turn.
- Keep all OAuth secrets in Vercel Environment Variables; never Git.

## 2. Approved fixed Microsoft Preview domain

Vercel and Cloudflare confirm valid DNS for mail-preview.cuevion.com, with the
project domain bound solely to branch feature/microsoft-graph-runtime.

Fixed Preview origin: https://mail-preview.cuevion.com
Exact new Entra Web redirect:
https://mail-preview.cuevion.com/api/inboxes/oauth-callback

Register the exact new URI in Microsoft Entra. Keep the older already registered
Preview callback until the first controlled live test works. Do NOT switch the
environment settings before the new callback registration is complete.

The new Microsoft-only code accepts this one literal custom hostname only
when VERCEL_ENV=preview, VERCEL_GIT_COMMIT_REF matches the feature branch and
three explicit environment pin values are consistent. Other Cuevion hosts
(including app.cuevion.com) never inherit this Preview permission.

## 3. Vercel Preview variables (no values in Git)

| Variable | Preview setting |
| --- | --- |
| `VERCEL_ENV` | `preview` (Vercel-generated) |
| CUEVION_MICROSOFT_PREVIEW_ORIGIN | https://mail-preview.cuevion.com (Preview branch only) |
| CUEVION_APP_URL | https://mail-preview.cuevion.com (NEW Preview-branch override; never change Production) |
| MICROSOFT_OAUTH_REDIRECT_URI | https://mail-preview.cuevion.com/api/inboxes/oauth-callback (UPDATE existing Preview branch variable) |
| `MICROSOFT_CLIENT_ID` | Microsoft Entra application (client) ID |
| `MICROSOFT_CLIENT_SECRET` | Secret stored securely in Vercel; never copy to chats/logs |
| `MICROSOFT_OAUTH_TENANT` | Approved tenant ID or appropriate tenant selector |
| `CUEVION_OAUTH_STATE_SECRET` | Existing secure signing secret, or separately configured new secret |
| VERCEL_GIT_COMMIT_REF | Vercel system variable must exactly equal feature/microsoft-graph-runtime for custom host |
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

## 6. Current configuration and release gates

- As of October 12, 2026 the four Microsoft branch-Preview variables are
  present: MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET,
  MICROSOFT_OAUTH_REDIRECT_URI and MICROSOFT_OAUTH_TENANT.
  Secret values were NOT inspected, and the current redirect still points
  to the OLD unique Vercel deployment URL.
- CUEVION_MICROSOFT_PREVIEW_ORIGIN is NOT configured for the branch.
  A new Preview-branch CUEVION_APP_URL override is also NOT configured.
  An existing Production value must remain untouched.
- The new custom hostname support is currently LOCAL CODE ONLY; no push or
  new deployment of the custom-domain change has taken place.
- Next, add the custom callback as a Web redirect in Entra. Then update the
  existing Preview-branch redirect URI, add the two missing Preview-only
  variables, verify VERCEL_GIT_COMMIT_REF exposure, and deploy only the
  feature branch after explicit user approval.
- Other open issues: real-account test, persistent send-attempt recovery,
  mobile, pagination, attachments, global unread/priority calculations.
- Broad Gmail ownership suite still has 87 known baseline failures;
  the narrow Gmail OAuth regression must stay green.
- No Production merge without separate rollback plan and user approval.

## 7. Safe rollback

Stop only the Microsoft Preview test branch or disable its exact Preview
pin (e.g. remove `CUEVION_MICROSOFT_PREVIEW_ORIGIN`) and redeploy after
explicit user approval. Without that pin the Microsoft gate fails closed.
Do not affect Gmail/IMAP, the OOO branch, or Production.
