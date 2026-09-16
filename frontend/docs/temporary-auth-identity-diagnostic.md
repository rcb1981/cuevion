# Temporary production identity diagnostic

`GET /api/auth/identity-inventory-diagnostic` is implemented by
`api/auth/identity-inventory-diagnostic.py` under the existing Vercel
`frontend/` root. No rewrite, environment change, new secret, permission grant,
or external service is needed. This change does not deploy or call the route.

## Authority and HTTP boundary

The route is available only with `VERCEL_ENV=production`. It requires the exact
canonical Host (and forwarded Host, if present), `Sec-Fetch-Site: same-origin`,
and the canonical Origin whenever an Origin header is present. GET browser
fetches commonly omit Origin; same-origin Fetch Metadata is still mandatory.
Direct address-bar navigation is intentionally rejected. Query strings,
request bodies, other methods, and duplicate security headers are rejected.

An existing `__Host-cuevion_session` credential is validated with the canonical
session parser, keyed lookup/binding digests, session decoder, and expiry
checks. `load_server_session(delete_invalid=False)` disables cleanup only for
this route; ordinary callers retain their existing cleanup behavior.

The existing PostgreSQL identity query and strict graph decoder must resolve
the session's exact issuer/subject. The canonical member-context validator
checks internal user ID, workspace, security epoch, role, and active user,
email, workspace, and membership state. The current role must be OWNER and
the identity must be active with the exact primary verified email. ADMIN,
MEMBER, revoked/stale/mismatched identities, invite-provisioned OWNER records,
and multiple active workspace candidates fail closed. Browser-provided
identity or authority fields are never used.

All responses, including errors and unsupported methods, have `no-store`.
No response sets a cookie. There is no request or response logging. Failures
contain one fixed generic error and never a partial inventory.

## Data and scope

The OWNER object includes `userId`, canonical `email`, `workspaceId`,
`workspaceRole`, `issuer`, `subject`, stored `authenticationMethod`, a safe
`verificationProvenance` category, `membershipState`, `isInitialOwner`,
`teamInviteProvenanceExists`, and `collaborationIssuerSubjectBindingsExist`.
Unknown verification-source strings are categorized rather than echoed.
Initial-owner evidence is the immutable workspace creator ID; this is not a
claim to have inspected initial-operation or security-event history.

Counts include all canonical users and authentication identities, memberships
by OWNER/ADMIN/MEMBER role (including inactive memberships), active workspaces,
unexpired invited v2 records in every canonical workspace's pending index,
and suspended/incomplete canonical member provisioning. Intentional removed
memberships are excluded from incomplete provisioning. Active invitees have
their deterministic IDs, stored provenance, invitation copies, member record,
user pointer, and indexes checked; missing continuity counts as incomplete.
Malformed evidence produces an error.

Subject-dependent results contain only booleans/counts: exact identity binding
count, current live session presence, pending invitations created by the OWNER,
the OWNER's recipient-invitation presence, the current session's invite
continuation, matching Collaboration owner/mailbox allowlist bindings,
comparisons against the existing invitee ID derivations, email-keyed config
presence, and managed mailbox count. No mailbox identifier is returned.

Session presence is scoped to the current authenticated session. Historical
invitations and other sessions are not enumerated. Collaboration checks cover
the issuer/subject-dependent rollout allowlists, not message records. The JSON
includes these scope statements; absence is never presented as a complete
historical audit.

## Exact stores and read-only guarantees

PostgreSQL uses the existing `AccountReaderConnectionFactory` and its existing
reader credential, accessing only these five `cuevion_account` tables:
`users`, `verified_emails`, `authentication_identities`, `workspaces`, and
`workspace_memberships`. Each read opens a fresh
`REPEATABLE READ READ ONLY` transaction, sets local statement/lock timeouts,
and ends with rollback and close. There is no commit, row lock, writer client,
operation/event-table access, function call, schema change, or new grant.
The bound is 128 records per table; exceeding it is an error, not truncation.

KV uses the existing session-store command transport and runtime KV service.
Like the Team authority's existing primary reads, it uses normal EVAL with a
fixed script containing only GET and STRLEN. It does not use replica fallback.
Keys are selected from canonical rows and validated indexes, with no scans:

- `cuevion:auth:v1:session:<current-lookup-digest>`
- `cuevion:auth:v1:team-invite-continuation:<current-session-derived-digest>`
- `cuevion:team:v2:pending-index:<workspace>`
- `cuevion:team:v1:members-index:<workspace>`
- `cuevion:team:v1:member:<workspace>:<canonical-email>`
- `cuevion:team:v2:member-user:<workspace>:<canonical-user>`
- `cuevion:team:v2:recipient-invite:<workspace>:<recipient>`
- `cuevion:team:v2:workspace-invite:<workspace>:<invitation>`
- `cuevion:team:v2:invite-token:<invitation>:<digest>` (stored evidence only)
- `cuevion:user:v1:<canonical-owner-email>`

No secret key suffix, token digest, session ID, cookie, config payload, or
credential is returned. The config is projected inside Redis to mailbox IDs,
owner email, count, and an internal change fingerprint; only presence and count
reach the HTTP response. No OAuth credential or mailbox content leaves that
config read. Collaboration allowlist values and their existing HMAC key are
used only for internal comparisons; only match booleans/counts are returned.

Bounds: at most 256 distinct keys, six read calls, 128 entries per index,
16 KiB per ordinary record, 256 KiB input config, and 24 KiB total projected
record bytes per snapshot, plus the existing transport's request/response
limits. There are no SET/DEL/EXPIRE operations, TTL changes, index pruning,
session invalidations, or calls to Team mutation/provisioning APIs.

All KV expansion reads atomically reread previously selected keys and reject
changes. Two complete PostgreSQL snapshots bracket collection and must have
identical canonical records. A final KV/session check detects revocation,
record changes, or expiry before projection; requests taking over 20 seconds
fail. This provides stable observed snapshots, not a distributed transaction
or a guarantee against changes after the final read.

## Use and removal

After a separately authorized deployment, invoke from an already authenticated
OWNER page at `https://app.cuevion.com`:

```js
const response = await fetch('/api/auth/identity-inventory-diagnostic', {
  method: 'GET', credentials: 'same-origin', cache: 'no-store'
});
const inventory = await response.json();
```

The browser supplies its HttpOnly cookie automatically. Do not copy the cookie
or add identity selectors. The returned OWNER email/issuer/subject are private
identity data; the endpoint does not log them.

Remove the route, both `identity_inventory_diagnostic.py` helper modules, this
document, and their dedicated tests after migration. Remove the temporary route
name from the existing auth packaging test and revert the optional session
cleanup parameter if it has no remaining callers.

Local validation includes synthetic PostgreSQL connection/transaction checks,
real disposable Redis script/content/TTL checks, the existing auth, Team,
canonical-reader and Collaboration suites, `npm run test:base`, `npm run build`,
and a diff whitespace check. Live production permissions, records, and endpoint
behavior remain untested until a separately authorized deployment and request.
