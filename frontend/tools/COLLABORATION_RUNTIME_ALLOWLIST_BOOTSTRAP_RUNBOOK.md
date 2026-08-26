# Collaboration runtime allowlist bootstrap runbook

## Purpose and security boundary

This is a temporary, one-shot operator procedure for deriving the first
Collaboration v2 owner and explicitly selected mailbox allowlist digests inside
the deployed production runtime.

The offline authority tool is correct, but it cannot safely reach production
authority: `CUEVION_AUTH_SESSION_SECRET` and
`CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL` are Vercel Sensitive values. Vercel
intentionally makes them available to the deployed runtime without making them
decryptable or exportable to local CLI execution. Do not change their type,
rotate them for local readability, duplicate them into exportable values, or
inspect Vercel credential files or CLI login tokens.

The runtime action preserves that boundary. It uses the existing server-side
session/current-account authority and returns only two canonical HMAC digests
plus safe counts. It is not a general identity-introspection endpoint and must
never remain enabled during ordinary operation.

This runbook does not claim that the action has been executed. It contains no
real production token, key, digest, session, mailbox authority, or Vercel
value.

## Closed route contract

- Route: `POST /api/collaboration/allowlist_bootstrap`
- Runtime classification: exact `VERCEL_ENV=production`
- Activation: `CUEVION_COLLAB_V2_HTTP_MODE=allowlist_bootstrap`
- Independent secret:
  `CUEVION_COLLAB_V2_ALLOWLIST_BOOTSTRAP_TOKEN`
- Dedicated request header: `X-Cuevion-Allowlist-Bootstrap`
- Required origin: the exact configured
  `CUEVION_APP_ORIGIN=https://app.cuevion.com`
- Required Host boundary: `app.cuevion.com`, including an exact forwarded Host
  when one is present
- Exact body: `{"mailboxId":"<one-canonical-mailbox-selector>"}`

Preview, development, missing, and malformed runtime classifications are
not-found before the bootstrap service is imported. The token and allowlist
HMAC key are canonical unpadded base64url encodings of
at least 32 random bytes and must be cryptographically distinct. The token is
accepted only from that header. It is never accepted from the URL, query
string, request body, cookie, or another header.

While the mode is `allowlist_bootstrap`, the normal
`POST /api/collaboration/owner` route remains not-found. Bootstrap mode does not
imply `owner_read` or `owner_write`. Guest, Team, frontend, and legacy v1 remain
unchanged and inactive.

Success is exactly this top-level JSON shape:

```json
{
  "owners": 1,
  "mailboxes": 1,
  "ownerDigests": 1,
  "mailboxDigests": 1,
  "ownerAllowlist": "<SYNTHETIC_OWNER_DIGEST>",
  "mailboxAllowlist": "<SYNTHETIC_MAILBOX_DIGEST>"
}
```

No issuer, subject, authentication version, email, user ID, workspace ID,
session ID, session cookie, database URL, session secret, HMAC key, bootstrap
token, mailbox ID, or raw mailbox authority is returned.

## Secret hygiene

Generate the real bootstrap token locally into a task-specific shell variable
without terminal output. Use a cryptographically secure local generator and
capture canonical unpadded base64url directly in memory. Add it to Vercel
Production as a Sensitive value through an interactive secret-input path; do
not put it on a command line, in shell history, a file, source control, chat,
logs, or this runbook.

Copy the variable directly to the clipboard without printing it. Paste it only
into the browser DevTools request header. After the single successful request:

1. Clear the clipboard.
2. Unset the task-specific local shell variable.
3. Remove `CUEVION_COLLAB_V2_ALLOWLIST_BOOTSTRAP_TOKEN` from Vercel Production.
4. Remove or set off `CUEVION_COLLAB_V2_HTTP_MODE` and redeploy immediately so
   the already deployed runtime no longer has the former activation values.

Generate `CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY` locally with the same no-output,
in-memory, Sensitive-value discipline. It must be different from the bootstrap
token and every other application secret. Unlike the temporary token, the
allowlist HMAC key remains after bootstrap because `owner_read` needs that same
key to validate the returned digest lists. Never print or return the HMAC key.

## One-shot lifecycle

Every step requires the appropriate separate production-change authorization.
The sequence is intentionally serial:

1. Deploy the bootstrap code while `CUEVION_COLLAB_V2_HTTP_MODE` is absent or
   off and the bootstrap token is absent.
2. Verify the normal application and verify the bootstrap route is not-found.
3. Configure Production with:
   `CUEVION_APP_ORIGIN=https://app.cuevion.com`, the new Sensitive
   `CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY`, the new distinct Sensitive
   `CUEVION_COLLAB_V2_ALLOWLIST_BOOTSTRAP_TOKEN`, and
   `CUEVION_COLLAB_V2_HTTP_MODE=allowlist_bootstrap`.
4. Redeploy so those values exist only in the intended production runtime.
5. Verify `/api/collaboration/owner` remains not-found. Do not continue if it
   exposes `owner_read` or `owner_write` behavior.
6. From one already authenticated browser at `https://app.cuevion.com`, make
   exactly one bootstrap request for exactly one approved canonical mailbox.
   Let the browser send its HttpOnly Cuevion session cookie automatically;
   never inspect, copy, paste, or export that cookie.
7. Confirm all four returned counts are exactly one. Capture only the two
   returned digest values. Do not capture raw DevTools request headers or any
   session data.
8. Clear the clipboard, unset the local bootstrap-token variable, and remove
   the Vercel bootstrap token immediately.
9. Remove/set off the bootstrap HTTP mode and redeploy immediately. Verify both
   the bootstrap route and owner route are not-found.
10. Configure the captured owner and mailbox digests as the respective
    production allowlist values while retaining the same Sensitive allowlist
    HMAC key.
11. Configure the remaining separately generated and reviewed `owner_read`
    secrets only after their own readiness gate.
12. In a later authorized deployment only, set the mode to `owner_read` and
    perform its activation verification. Bootstrap mode must not be reused as
    an owner-read activation step.

If any count differs from one, any response contains an unexpected field, the
selected mailbox is rejected, the owner route is reachable, or deactivation
cannot be verified, stop. Do not retry with broader selectors, identity fields,
wildcards, multiple mailboxes, or another authority path.

## Synthetic browser invocation

Run the following only from DevTools on an already authenticated
`https://app.cuevion.com` page. Every visible value below is synthetic. Replace
the two placeholders in browser memory only; do not save the snippet with real
values.

```js
const syntheticBootstrapToken = "<SYNTHETIC_BOOTSTRAP_TOKEN>";

const bootstrapResponse = await fetch(
  "/api/collaboration/allowlist_bootstrap",
  {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Cuevion-Allowlist-Bootstrap": syntheticBootstrapToken,
    },
    body: JSON.stringify({
      mailboxId: "synthetic.mailbox-selector",
    }),
  },
);

await bootstrapResponse.json();
```

Do not add a `Cookie` header. Browser fetch supplies the existing HttpOnly
session cookie through the same-origin request, and JavaScript never needs to
read it. Do not add issuer, subject, email, user/workspace/session IDs, or
authentication version to the request.

## Failure handling and write containment

Inactive mode and operator-token failures are fixed not-found responses.
Unauthenticated or stale/revoked sessions are fixed unauthorized responses.
Invalid Host/Origin/body/selector requests use fixed boundary errors. Session
store, current-account authority, mailbox authority, or HMAC configuration
unavailability uses a fixed service-unavailable response. No response contains
raw exception text.

The successful action reloads the server session, revalidates the current
account, revalidates one owned mailbox, and derives two digests. It does not
create Collaboration records or source indexes, append messages, refresh
Collaboration TTLs, issue invitations, write Team state, or alter mailbox or
current-account configuration. Existing invalid-session cleanup behavior is
unchanged.
