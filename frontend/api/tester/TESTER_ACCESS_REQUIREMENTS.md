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
