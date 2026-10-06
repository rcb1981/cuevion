# Tester Access security requirements

Tester Access is separate from Team Invite and authorizes creation of one new
standalone Cuevion owner account.

## Invite credential handling

- Public invite links use the fragment form https://app.cuevion.com/#tester_invite=<token>.
- Raw Tester Invite bearers never belong in URL paths or query strings.
- The browser scrubs any tester_invite credential with history.replaceState
  before lookup, auth navigation, analytics, external navigation, or other
  application-controlled network work.
- Query, duplicate, mixed query/fragment, malformed and non-root forms are
  invalid but are still scrubbed first.
- Raw bearers are not persisted in localStorage, sessionStorage, cookies, logs,
  analytics, exceptions, traces, metrics or support payloads.
- The raw bearer exists in memory only long enough to POST it to the reviewed
  same-origin Tester Login boundary.
- Durable invite state stores only SHA-256 token digests.
- Tester Login accepts the bearer in a POST body and never redirects with it in
  a URL.

## Authority and lifecycle

Only immutable user IDs explicitly configured as Tester Access administrators
may issue invites. Workspace role alone is insufficient. Registration and owner
provisioning must re-prove the live invite and exact Auth0-verified email.
Existing Cuevion identity or current verified-email authority is a conflict.


## Activation gates

Tester Access remains fail-closed until all environment-specific authority is configured and reviewed.

### Production

Before the first Production Tester Invite is issued:

1. `CUEVION_TESTER_ADMIN_USER_IDS` must exist for Production and contain only exact immutable Cuevion `usr_...` IDs of explicitly approved Tester Access administrators. An email address, Auth0 subject, workspace ID, or workspace role is not an acceptable substitute.
2. `CUEVION_AUTH0_REGISTRATION_AUTHORITY_SECRET`, `CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL`, `CUEVION_AUTH_ACCOUNT_WRITER_DATABASE_URL`, Auth0 client/session configuration, and the durable KV command store must all be available in the same Production trust environment.
3. The account reader and writer database URLs must remain distinct credentials.
4. The active Auth0 Pre User Registration Action must continue to call the generic Cuevion registration-authority endpoint. The endpoint, not the browser or Action metadata, decides whether a source-specific one-time grant is valid.
5. No Tester Invite may be issued until the deployment containing the Tester Access code has passed the post-deployment auth/session smoke checks.

### Preview

Preview Tester Access is disabled unless Preview has a separately reviewed, isolated Auth0 tenant/trust configuration, account reader/writer database, registration-authority secret, session authority, and Tester Invite durable store/namespace. Production Tester Invite tokens or registration grants must never authenticate Preview, and Preview credentials must never authenticate Production.

A shared generic KV integration is not sufficient evidence of isolation. If the same physical KV service is retained for multiple environments, a separately reviewed environment-specific namespace and all related session/registration boundaries must prove that cross-environment reads and writes are impossible.

Until those Preview requirements are satisfied, `CUEVION_TESTER_ADMIN_USER_IDS` must not be configured for Preview.

### Early Access handoff

A future `cuevion.com` Early Access approval service may call the same Tester Invite issuance authority with a stable `sourceRequestId`. The public Early Access submission endpoint must never issue an invite itself. Approval must be an authenticated server-side operation, idempotently bound to the approved request. It must not create a parallel registration or provisioning path.
