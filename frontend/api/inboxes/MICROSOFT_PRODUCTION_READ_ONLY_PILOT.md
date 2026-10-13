# Microsoft Graph - first Production read-only pilot

Status: LOCAL feature-branch preparation only. No Production merge, credential
change, Entra update, mailbox connection, email send or deployment was performed.

## Baseline and scope

- Only feature/microsoft-graph-runtime, never Out of Office or main.
- Baseline Production SHA: 9deb6fef3712d482916ce7d69f4adeb75149f812.
- Baseline READY deployment: dpl_GRJZeYYDFiUccntU8desCzqsAsRQ.
- Production deployment needs NEW explicit user approval.

## Read-only security guarantees

1. In VERCEL_ENV=production, send-microsoft and mark-microsoft-read
   return HTTP 403, code microsoft_pilot_read_only, BEFORE body/header
   parsing, owner/token resolution, outbound ledger writes or Graph PATCH,
   draft creation or send. No environment option can unlock this.
2. Production OAuth requests exactly these six scopes:
   openid email profile offline_access
   https://graph.microsoft.com/User.Read
   https://graph.microsoft.com/Mail.Read
   An override via MICROSOFT_OAUTH_SCOPES cannot add Mail.ReadWrite,
   Mail.Send or other extra permissions in Production.
3. The Microsoft workspace visibly says Read-only pilot and hides New
   message, Reply and Mark as read/unread. The server is still the security
   boundary if someone calls an endpoint directly.
4. Existing Microsoft Inbox/Sent list and exact read remain read-only Graph
   GETs with explicit owner/mailbox authority. Required OAuth token and
   mailbox configuration persistence does not mutate email content.
5. The previous Preview write-service tests remain testable independently.
   Preview still requires exact pinned host; Production only allows
   app.cuevion.com as its Microsoft OAuth host.

## Required before an independently approved Production deployment

1. Add Microsoft Entra Web callback:
   https://app.cuevion.com/api/inboxes/oauth-callback
   Keep existing Preview callback for now. Verify personal Microsoft
   account support and the requested delegated reading permissions.
2. Add these four Vercel PRODUCTION-ONLY variables, while keeping all
   existing Auth0/Gmail/IMAP and Preview variables unchanged:
   - MICROSOFT_CLIENT_ID (app ID)
   - MICROSOFT_CLIENT_SECRET (secure secret Value)
   - MICROSOFT_OAUTH_TENANT = common
   - MICROSOFT_OAUTH_REDIRECT_URI =
     https://app.cuevion.com/api/inboxes/oauth-callback
   Do not copy branch Preview CUEVION_APP_URL into Production. Do not share
   any secret values in chat.
3. Reconfirm unchanged main SHA, run Python Microsoft/Gmail OAuth/Auth0
   tests, Microsoft frontend client/UI tests, npm build, and diff hygiene.
   Compare broader legacy suite failures against current main.
4. Ask for NEW explicit Production merge/deployment approval. Afterwards,
   verify real app.cuevion.com SHA, login, existing Gmail/IMAP function,
   and all four Microsoft production-only env entries (metadata only).
5. Connect the tester's personal Microsoft account and inspect consent
   for READ permissions only. Read Inbox and Sent, open one known message,
   switch folders, refresh. Do NOT send, reply, mark read/unread, move or
   delete during the pilot.

## Rollback and residual risk

- Roll back to dpl_GRJZeYYDFiUccntU8desCzqsAsRQ if production behaves
  incorrectly, then recheck Auth0/Gmail/IMAP.
- Rollback does NOT undo OAuth consent, tokens, saved mailbox connection
  records, or other durable state; revoke/reconnect separately if needed.
- No database schema migration appears in the 60-file Microsoft diff.
- Broad Gmail ownership suite has 87 identical baseline failures; the
  existing npm suite has legacy timezone and Gmail ownership failures
  reproducible on main. Neither should be misattributed to this patch.
- Enabling writes requires a separate reviewed code change and release
  authorized by the user.
