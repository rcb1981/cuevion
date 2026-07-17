# Initial-account repository contract activation requirements

## Status: completely inactive contract only

`cuevion_auth.account_repository_contract` defines only a pure,
provider-independent and database-independent contract for a future transactional
initial-account repository. It creates and persists nothing. Importing it,
deploying it, constructing its records, or passing its tests cannot create an
account, grant authority, issue a session, or expose a route.

This slice defines no concrete or in-memory repository, database, Redis or KV
adapter, schema, SQL, DDL, migration, ORM, driver, environment loader, key or
digest generator, randomness, clock read, filesystem or network I/O, logging,
provider call, OAuth flow, session, cookie, route, handler, app, router, frontend,
beta-auth behavior, mailbox behavior, Team behavior, or Collaboration
integration. It performs no feature activation.

The separate inactive `cuevion_db` foundation now provides a concrete
PostgreSQL schema-one and reviewed offline Alembic revision. That foundation is
not a repository adapter and changes none of this module's pure, database-free
surface. It opens no connection and activates nothing; see
`cuevion_db/DATABASE_FOUNDATION_ACTIVATION_REQUIREMENTS.md`.

Every account, evidence, operation, receipt, and security-event timestamp in this
contract is an exact built-in Python `int`: integral Unix UTC seconds in the
inclusive range `0..253402300799`. `bool`, integer subclasses, floats, negative
values, milliseconds, microseconds, implicit defaults, and timezone-naive
semantics are rejected. Evidence preserves `verified_at <= issued_at <
expires_at`; repository-generated times remain outside caller control.

## Transactional initial-account aggregate

A future implementation must treat initial-account creation as one transaction.
The following facts must either all commit or all fail together:

- the initial Cuevion user;
- its primary verified email;
- its authentication identity;
- its initial workspace;
- its active owner membership;
- exactly one initial-account security event;
- the consumed verified-authentication evidence claim; and
- the durable operation result used for replay and reconciliation.

No subset is authoritative. Candidate record IDs, an accepted request, a returned
provider assertion, or a constructed receipt cannot prove that the aggregate was
persisted. Future relational constraints must authoritatively enforce record-ID,
identity, evidence-claim, operation-reference, membership, and other approved
uniqueness rules in the same transaction.

The schema-one security event stream name is exactly the case-sensitive
`cuevion.account.security`. Name and positive position are repository-generated,
not request, browser, or provider fields, and remain outside the immutable caller
request snapshot. Operation and event are nevertheless bound across the complete
receipt and provenance graph.

Conflict handling is closed before any adapter exists. Existing operation lookup
always precedes current policy: the exact same immutable request is
`EXACT_REPLAY`, while a mismatch is `CONFLICT` with
`OPERATION_REFERENCE_MISMATCH`. Only for a genuinely new operation whose
non-commit is known, the exact remaining precedence is
`EVIDENCE_ALREADY_CONSUMED`, then `AUTHORITY_ALREADY_CLAIMED`, then
`RECORD_ID_COLLISION`. Insert order, race order, query plans, vendor error codes,
and constraint names may never determine this result. Unknown commit status
continues to take precedence as `AMBIGUOUS`.

## Closed outcomes and caller knowledge

The contract has exactly six outcomes:

- `CREATED` means the complete aggregate, evidence claim, security event, and
  durable operation result definitively committed atomically. It returns the
  immutable creation receipt and no conflict reason.
- `EXACT_REPLAY` means an exactly replay-equivalent request under the same
  operation reference previously committed. It returns the stored historical
  receipt, performs no new write, and emits no additional security event.
- `CONFLICT` means this request definitively did not commit. It returns no receipt
  and exactly one closed conflict reason. The reason must not reveal conflicting
  authority records, identities, email addresses, IDs, or storage details.
- `AMBIGUOUS` means commit status is unknown. It returns no receipt or conflict
  reason. Unknown commit status always takes precedence over `UNAVAILABLE` and
  `INTERNAL_ERROR`.
- `UNAVAILABLE` means the repository can authoritatively establish that no commit
  occurred, for example before dispatch or after a confirmed rollback. It returns
  no receipt or conflict reason.
- `INTERNAL_ERROR` means an invariant, corruption, schema, protocol, or unexpected
  implementation failure where commit status is known and not ambiguous. It
  returns no receipt, authority fact, or conflict reason.

Malformed or unsupported requests fail at the pure validation boundary and are
not repository outcomes. Current authority must always be read separately; a
historical `CREATED` or `EXACT_REPLAY` receipt is not current authorization.

## Exact replay and ambiguous-result reconciliation

The operation reference is already derived server-side before this repository
contract is called. The raw operation key never appears in repository calls,
records, receipts, logs, or errors. Operation-key generation, domain separation,
key configuration, key custody, and rotation are deferred to a separately
reviewed coordinator boundary. Auth-B1a session keys, domains, and digests must not
be reused.

Replay equivalence is exact, versioned, exact-type, and field-by-field over every
caller-controlled persisted request field. It uses no hashing, serialization,
clock, provider, or storage. `EXACT_REPLAY` is possible only after both requests
are independently valid and exactly equivalent.

After `AMBIGUOUS`, reconciliation uses the same `create_initial_account` method
with the exact same complete request. The caller must reuse the operation
reference, candidate record IDs, verified evidence, security-event request, and
all other request fields. It must not mint new entropy, evidence, IDs, or an
operation reference. If durable operation state cannot be authoritatively read
after possible dispatch, the result remains `AMBIGUOUS`; it must not be downgraded
to `UNAVAILABLE` or `INTERNAL_ERROR`.

Automatic retry is not authorized after `CONFLICT` or `INTERNAL_ERROR`. Only a
future coordinator may authorize a distinct, bounded new logical operation after
a definitive record-ID collision. Business-authority conflicts must never be
treated as random collisions.

## Verified provider-evidence boundary

The evidence record is a minimal assertion supplied by a trusted future
verification coordinator after provider verification. The coordinator must have
already verified the provider signature, trusted issuer, audience, challenge or
nonce binding, PKCE where applicable, provider expiry, subject, authentication
method, and verified email claim. The repository contract does not contact or
trust a provider and the evidence record is not independently sufficient proof of
identity.

The assertion ID is an opaque coordinator-issued replay correlator, not a raw
provider identifier. No authorization code, access token, refresh token, ID token,
cookie, header, client secret, PKCE verifier, nonce, challenge secret, provider
payload, or mailbox credential may cross this boundary.

Evidence values are bound exactly to the initial identity and verified-email
records. Issuer and opaque case-sensitive subject are compared exactly. The
authentication method, canonical verified email, and verification timestamp must
match exactly. The coordinator identity is not automatically equated to the
verified-email verification-source field.

## Structural time and future trusted now

This pure contract validates only the structural ordering
`verified_at <= issued_at < expires_at` using injected integer values. It reads no
clock and does not decide whether evidence is currently expired.

A concrete adapter must receive one explicit, exact trusted-now snapshot and the
current key and policy context through a separately reviewed execution context. On
every call, including reconciliation after `AMBIGUOUS`, it must authoritatively
resolve durable operation state before applying current evidence-expiry or key
policy. The future coordinator must not reject current evidence freshness or key
policy before that resolution. An exactly replay-equivalent committed operation
returns the stored historical receipt as `EXACT_REPLAY`, even when current policy
would reject a new operation. A non-equivalent request under the same committed
operation reference returns `CONFLICT` with `OPERATION_REFERENCE_MISMATCH`, while
inconsistent durable operation state returns `INTERNAL_ERROR`. If read or commit
status cannot be authoritatively resolved after possible dispatch, the result
remains `AMBIGUOUS` and expiry cannot downgrade it. Only after authoritatively
establishing that no prior operation state exists may current evidence freshness,
operation authorization, and key policy gate a new write. Historical
`EXACT_REPLAY` must never be blocked because the original provider evidence has
expired since the committed operation. This pure contract reads no clock and
decides no current freshness. Trusted now is neither a request field nor a replay
field. Current expiry policy and its failure mapping remain activation blockers.

## Email lifecycle boundary

Initial creation requires a current primary `VERIFIED` email whose `retired_at` is
`None`. This contract intentionally defines no permanent retired-email reuse
policy. Reuse of a historical retired claim, linking, email change, recovery,
deletion, privacy, retention, and authoritative email ownership remain separate
policy and repository reviews. A future repository must apply the then-approved
policy without inferring it from this initial-state invariant.

## Receipt and security-event boundaries

The receipt contains only immutable historical creation IDs: user, verified
email, authentication identity, workspace, and security event. The workspace ID
and user ID together identify the immutable initial membership. The receipt
contains no commit or storage reference, current status, role, row version,
security epoch, canonical email, timestamp, or other current authority.

The caller supplies only the versioned security-event ID and closed event type.
The actor or coordinator, operation reference, and affected aggregate IDs are
derived later from the enclosing validated request rather than duplicated.
Actual event time, event ordering, event sequence, append metadata, and commit
metadata are repository-generated. They are not request fields, receipt fields,
or replay-equivalence fields.

## Persistence and activation blockers

Activation requires a separately reviewed concrete adapter and storage design
that proves:

- relational schemas and exact authoritative constraints;
- atomic transaction and rollback behavior for the whole aggregate;
- durable operation-result and evidence-claim persistence;
- exact replay under concurrency and process restarts;
- definitive conflict classification without authority enumeration;
- ambiguous commit reconciliation and safe outage classification;
- bounded handling of genuine record-ID collisions;
- corruption, isolation, migration, partial-failure, and recovery behavior; and
- production and preview data, namespace, configuration, and key isolation.

No database or authentication vendor is selected here. Resolver behavior,
session issuance, session persistence, cookie emission, trusted configuration,
provider and challenge flows, routes, frontend integration, monitoring, rollout,
rollback, and an explicit activation decision all require later review. No future
phase may treat this contract's presence or importability as permission to bypass
those gates.

The separate `RELATIONAL_ACCOUNT_STORE_ACTIVATION_REQUIREMENTS.md` freezes the
inactive logical schema, migration-compatibility, and consistent-read requirements
that a future relational implementation must satisfy.

## Product entitlement boundary

One Cuevion account can later use multiple products. Product access is determined
server-side at the workspace-entitlement level by a future authoritative
entitlement layer.

- Email Client later corresponds to an `email_client` entitlement.
- Organizer later corresponds to an `organizer` entitlement.
- Bundle is a later commercial combination of those two entitlements, not a
  separate identity type or account type.

Product rights must never be trusted as an authoritative claim from browser input,
account identity, login credentials, or a session cookie. Plan, package,
subscription, billing, entitlement, and seat limits are not fields or authority in
the initial-account contract. The future entitlement layer is completely outside
this slice and cannot extend the frozen public Python contract surface here.

## Canonical import and Vercel boundary

The only supported module identity is
`cuevion_auth.account_repository_contract`. `cuevion_auth` remains an implicit
namespace package with no `__init__.py`, forwarding alias, or `sys.path` mutation.
The contract imports Auth-A records and validators only through canonical
`api.auth.models`; it does not import session, record-ID generator, provider,
storage, route, or application modules.

The module lives under `frontend/cuevion_auth/`, outside the configured Vercel
Python-function glob `api/**/*.py`. It defines no handler, route, router, app,
server, service, or concrete protocol implementation, and no active route imports
it. Deploying this source therefore exposes no endpoint and activates no database,
provider, session, frontend, beta-auth, mailbox, Team, or Collaboration behavior.
