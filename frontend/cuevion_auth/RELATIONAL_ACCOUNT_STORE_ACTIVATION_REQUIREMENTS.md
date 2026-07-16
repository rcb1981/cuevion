# Relational account-store activation requirements

## Status: completely inactive schema and migration contract only

This document defines the required logical relational boundary for a future
implementation of `InitialAccountRepository`. It is an abstract schema,
transaction, migration, and consistent-read contract only. It creates no schema,
table, migration, connection, transaction, account, verified email,
authentication identity, workspace, membership, operation result, security
event, session, cookie, entitlement, route, or other authority.

This slice selects no database vendor, cloud provider, ORM, driver, SQL dialect,
migration system, connection library, serialization format, authentication
provider, session store, login host, or deployment topology. It contains no DDL,
SQL, generated migration, repository adapter, database configuration, environment
loader, credential, secret, network call, filesystem write, provider call, HTTP
handler, route, frontend behavior, rollout control, or feature activation. The
presence of this document is not approval to implement or activate any of those
components.

The existing immutable account records and initial-account repository request
remain the public logical source of field and aggregate semantics. A future
physical schema may represent the requirements below differently only when it
proves the same exact equality, atomicity, uniqueness, referential-integrity,
versioning, reconciliation, and fail-closed behavior. Storage convenience must
not weaken this contract.

## Schema-one relation set

Schema one has exactly seven authority relations in this boundary:

1. `users`;
2. `verified_emails`;
3. `authentication_identities`;
4. `workspaces`;
5. `workspace_memberships`;
6. `initial_account_operations`; and
7. `security_events`.

No session, challenge, provider-token, password, passkey, recovery, billing,
subscription, plan, package, seat, or entitlement relation belongs to this
schema-one account-store slice. A future physical implementation must not hide an
eighth authority relation behind an unreviewed side table, cache, Redis or KV key,
provider record, migration journal, or application-local file.

Every relation has an explicit logical record version from its first persisted
row. Mutable account records also have a separate positive `row_version` used for
optimistic concurrency. Database schema revision, logical record version,
request/contract version, row version, credential epoch, and security epoch are
different concepts and must never be substituted for one another.

### `users`

The relation contains the logical fields of `CuevionUser`:
`schema_version`, `user_id`, `status`, `primary_verified_email_id`,
`display_name`, `security_epoch`, `created_at`, `updated_at`, and `row_version`.

- `user_id` is the immutable primary key and must be globally unique within the
  isolated account authority.
- `status`, `display_name`, `security_epoch`, all timestamps, and `row_version`
  are present explicitly. `security_epoch` and `row_version` are positive.
- `created_at` is not later than `updated_at`.
- An active user has a non-null primary verified-email reference.
- The primary-email reference identifies a `verified_emails` row owned by the
  same `user_id`. This is a commit-time relational invariant, not a string-email
  comparison or application precheck.
- The initial row is `ACTIVE`, has `security_epoch == 1`, and has
  `row_version == 1`; these values are requirements of initial creation, not
  defaults for migration or recovery.

The schema contains no account type, product flag, subscription state, plan,
billing identifier, seat count, provider token, password, session credential, or
workspace role on a user row.

### `verified_emails`

The relation contains the logical fields of `VerifiedEmail`:
`schema_version`, `email_id`, `user_id`, `canonical_email`, `status`,
`verification_source`, `created_at`, `verified_at`, `retired_at`, and
`row_version`.

- `email_id` is the immutable primary key.
- `user_id` is a non-null foreign key to `users` and is immutable for the life of
  the email record.
- `canonical_email` is the exact canonical account claim supplied by the trusted
  boundary. Database comparison must not introduce locale, case, provider-alias,
  plus-address, dot-address, or other implicit equivalence.
- At most one current `VERIFIED` and non-retired row may hold a canonical email
  authority claim. The physical mechanism for conditional uniqueness is deferred,
  but application-level check-then-insert is insufficient.
- Status and timestamps obey the existing `PENDING`, `VERIFIED`, and `RETIRED`
  lifecycle. Initial creation persists `VERIFIED`, a non-null `verified_at`, a
  null `retired_at`, and `row_version == 1`.
- `verification_source` is explicit and is not automatically the provider,
  coordinator, issuer, or authentication method.

Schema one intentionally does not decide whether a retired canonical email may
ever be claimed again. Historical reuse, retention, deletion, privacy, recovery,
email change, and notification policy require a separate authority review. A
physical unique constraint must protect the current claim without silently
choosing a permanent-retention or reuse policy.

### `authentication_identities`

The relation contains the logical fields of `AuthenticationIdentity`:
`schema_version`, `identity_id`, `user_id`, `issuer`, `subject`,
`authentication_method`, `status`, `verified_email_id`, `created_at`,
`last_used_at`, and `row_version`. The logical `authentication_method` field
reuses the enum semantics of the existing Python record's `method` field; it does
not define a second authentication model.

- `identity_id` is the immutable primary key.
- `user_id` is a non-null foreign key to `users` and is immutable.
- The normalized, canonical issuer equality value together with the exact,
  case-sensitive stable subject is unique. The physical collation or index must
  preserve the contract's exact equality and must not normalize the subject.
- `authentication_method` is explicit and closed. It is not inferred from issuer,
  email, or provider-specific payload.
- A non-null `verified_email_id` is a foreign key to a verified-email record owned
  by the same `user_id`. Initial creation requires this link.
- Status and timestamp checks follow the account model. Initial creation persists
  `ACTIVE`, `last_used_at` exactly as supplied by the valid request, and
  `row_version == 1`.

The relation contains no authorization code, access token, refresh token, ID
token, provider payload, nonce, challenge secret, PKCE verifier, mailbox
credential, recovery secret, cookie, or session secret.

### `workspaces`

The relation contains the logical fields of `Workspace`: `schema_version`,
`workspace_id`, `status`, `created_by_user_id`, `created_at`, `updated_at`, and
`row_version`.

- `workspace_id` is the immutable primary key.
- `created_by_user_id` is a non-null foreign key to `users` and is immutable
  historical provenance, not current ownership by itself.
- Status is closed, timestamps are ordered, and `row_version` is positive.
- Initial creation persists `ACTIVE` and `row_version == 1`.

The workspace row has no product, bundle, plan, billing, subscription, seat, or
entitlement authority. Current workspace authority is obtained from a current
membership and, later, a separate entitlement authority.

### `workspace_memberships`

The relation contains the logical fields of `WorkspaceMembership`:
`schema_version`, `workspace_id`, `user_id`, `role`, `status`, `created_at`,
`updated_at`, and `row_version`.

- `(workspace_id, user_id)` is the immutable composite primary key.
- `workspace_id` and `user_id` are non-null foreign keys to `workspaces` and
  `users`.
- Role and status are closed, timestamps are ordered, and `row_version` is
  positive.
- Initial creation persists exactly one `OWNER`, `ACTIVE` membership with
  `row_version == 1` for the newly created user and workspace.

Last-owner protection is a future transactional mutation invariant. A row-level
check cannot prove that another active owner exists, so schema one must not claim
that role/status checks alone solve last-owner races.

### `initial_account_operations`

This append-only relation is the durable idempotency and reconciliation authority.
Its logical operation reference is the exact tuple `schema_version`,
`derivation_key_epoch`, and `operation_digest`; that complete reference is unique.
The raw operation key is never stored.

Each committed row contains, at minimum:

- an operation-record version and the request version;
- the complete operation reference;
- an immutable, lossless, versioned historical snapshot of every
  caller-controlled persisted field in the validated
  `InitialAccountCreationRequest`;
- the evidence schema version, trust domain, verification coordinator ID,
  assertion ID, issuer, subject, authentication method, canonical verified email,
  and `verified_at`, `issued_at`, and `expires_at` values;
- the historical committed result state;
- receipt schema version and the immutable user, verified-email,
  authentication-identity, workspace, and security-event IDs;
- a repository-generated committed timestamp; and
- an append-only record/row version fixed at one for schema one.

The exact request snapshot must remain independently comparable after the current
user, email, identity, workspace, or membership rows later change. Reconstructing
historical request equality from mutable current rows, comparing only selected
fields, or relying only on a digest is insufficient. The future physical storage
format for the lossless snapshot is deferred; it must have an explicit format
version and an injective, deterministic mapping to all request fields before use.

Replay comparison must fail closed before testing equality unless both the stored
snapshot and the supplied request independently have a supported version, the
exact required field set, and the exact logical type and canonical representation
for every field. Missing and null are distinct, booleans are not integers, numeric
and textual representations are not interchangeable, and a raw string is not an
enum member merely because its characters match. A future adapter must not coerce,
default, normalize, decode loosely, or partially compare malformed authority. An
invalid supplied request fails at the pure validation boundary; an invalid stored
snapshot or operation graph is `INTERNAL_ERROR`. Exact field-by-field equality is
evaluated only after those independent validations succeed.

The inactive schema and request-snapshot manifest validators apply the same order:
they first require every record, tuple, tuple element, enum, boolean, integer, and
string to have its exact canonical type, and only then compare canonical values.
Caller-controlled equality, container subclasses, mappings, lists, sets, and duck
types cannot establish manifest validity.

These two public helpers are boolean validators for controlled manifest
invalidity. Expected malformed, unsupported, or non-canonical candidate manifest
types, shapes, fields, and values return `False`. An unexpected dependency,
programming, or implementation `Exception` is not a manifest mismatch and the
same exception instance propagates unchanged; every `BaseException` that is not
an ordinary `Exception` likewise propagates unchanged. A future concrete adapter
may interpret `False` only as a controlled invalid contract manifest, never as an
infrastructure or internal failure.

The tuple `(trust_domain, verification_coordinator_id, assertion_id)` is unique so
the same coordinator assertion cannot be consumed twice in its trust context.
Operation rows reference the complete receipt graph and exactly one security
event. A visible `PENDING`, `STARTED`, `AMBIGUOUS`, `UNAVAILABLE`, or
`INTERNAL_ERROR` operation row is not part of schema one. Only an atomically
committed historical creation result is durable; `AMBIGUOUS` describes caller
knowledge about a possibly committed transaction, not stored authority state.

Every receipt ID is provenance-bound to the corresponding immutable candidate ID
in the frozen request snapshot: user, verified email, authentication identity,
workspace, and security event must match exactly. The initial membership reference
is the exact snapshot pair `(workspace_id, user_id)`. The security event is likewise
bound to that same snapshot and operation: its event ID and event type, complete
operation reference, coordinator/actor context, affected aggregate IDs, initial
membership pair, and relevant security epoch must match the snapshot and committed
aggregate facts from which they are derived. Receipt or audit provenance must not
be reconstructed later from mutable current rows or accepted as an independent,
caller-selectable graph.

A committed operation is valid only as one internally consistent operation,
snapshot, evidence, receipt, security-event, and aggregate graph. A present
operation row with any missing, extra, differently typed, differently versioned,
or cross-linked component is corruption, not an exact replay or partial success.

### `security_events`

This append-only relation contains, at minimum:

- an immutable, unique `event_id`;
- an event-record and payload version;
- the closed `INITIAL_ACCOUNT_CREATED` event type;
- a unique reference to the initial-account operation;
- the derived actor/coordinator context;
- foreign keys to the affected user, verified email, authentication identity,
  workspace, and composite initial membership;
- a repository-generated event time and recorded time; and
- a positive immutable append position unique in its defined event stream.

The operation and event form a mandatory one-to-one relationship at commit: a
committed initial-account operation cannot exist without its event, and an
initial-account-created event cannot exist without its operation. The physical
commit-time foreign-key technique is deferred, but compensating writes, an
out-of-transaction event, or a best-effort event sink are not equivalent.

The event contains no raw operation key, provider token or payload, session
credential, cookie, challenge secret, mailbox credential, database error, or
conflicting authority value. It is immutable after append.

## Database-enforced invariants

A future relational implementation must make the database authoritative for all
of the following, including under genuine concurrent writers:

- primary-key uniqueness for every immutable record ID;
- exact logical operation-reference uniqueness;
- unique current canonical verified-email authority claims;
- unique normalized issuer plus exact stable subject;
- unique evidence assertion within trust-domain and coordinator context;
- the composite workspace-membership key;
- unique security-event IDs and the one-to-one operation/event relation;
- every declared foreign key and same-user composite relationship;
- non-null authority fields;
- exact supported status, role, method, event-type, and version domains;
- positive row versions, operation/event record versions, key epochs, and
  security epochs;
- canonical identifier shapes and exact equality semantics;
- valid lifecycle-dependent nullability; and
- required timestamp ordering.

Application prechecks may improve error handling but are not uniqueness or
referential-integrity evidence. Logical constraints must have stable, reviewed
identities so a future adapter can map a database violation to one closed conflict
class without exposing vendor codes, constraint names, record values, or storage
details to callers.

The physical implementation must prove commit-time integrity for the cyclic
user/primary-email and operation/security-event references. Whether that uses
deferred constraints or another relationally equivalent layout is a later
vendor-specific decision.

Snapshot-to-receipt, snapshot-to-event, operation-to-event, evidence-to-operation,
and receipt-to-aggregate bindings must be enforced with database primary, foreign,
unique, and check constraints wherever the selected relational system can express
them. Any cross-record or exact-type invariant that cannot be expressed there must
be validated by the repository against one authoritative transaction snapshot
before commit. This is a vendor-neutral allocation of responsibility, not approval
for a particular SQL dialect, constraint mechanism, trigger, ORM, or write order.
Application prechecks outside the transaction, post-commit comparison, asynchronous
audit repair, or eventual reconciliation do not enforce the committed graph.

## Repository-enforced invariants

The concrete repository, when separately approved, is responsible for invariants
that cannot be reduced to independent row constraints:

- exact validation of the complete initial aggregate;
- operation-reference resolution before current-policy evaluation;
- exact, versioned request replay equivalence;
- deterministic conflict classification when more than one constraint could
  reject a request;
- one atomic transaction for the complete aggregate, operation, evidence claim,
  and event;
- authoritative ambiguous-result reconciliation;
- compare-and-swap on expected `row_version` for later mutations;
- exactly one row-version advance for each successful mutation;
- monotone security-epoch updates that cannot be lost to a race;
- current primary-email consistency across user and email lifecycle changes;
- last-owner protection for later membership or workspace mutations; and
- fixed, value-free failure mapping that does not enumerate existing authority.

For a request capable of violating multiple constraints, operation-reference
mismatch is resolved first. A future contract revision must freeze the remaining
closed conflict precedence before an adapter is implemented; it must not be left
to nondeterministic statement order or vendor error ordering.

## Coordinator and policy invariants outside storage

The account store does not prove or perform provider authentication. Before a new
write, a separately reviewed trusted coordinator must verify the provider
signature and issuer trust, audience, subject, authentication method, verified
email claim, challenge or nonce binding, PKCE where applicable, evidence expiry,
and every operation-specific assurance requirement.

The coordinator also owns operation-key generation, domain separation, key
configuration, custody and rotation; exact trusted-now injection; login and
account-linking policy; bounded authorization of a genuinely new logical operation
after a definitive random record-ID collision; and production/preview trust
isolation. No provider token, raw provider payload, operation key, key material, or
current policy object crosses the relational repository request boundary.

Session issuance, cookie emission, HTTP redirects, product access, UI behavior,
notifications, recovery, linking, email change, deletion, retention, and rollout
remain outside this storage contract.

## Atomic initial-account creation

One future relational transaction must make the following facts visible together
or make none of them visible:

- the durable operation result and exact historical request snapshot;
- the consumed verified-authentication evidence claim;
- one user;
- one current primary verified email;
- one active authentication identity;
- one active workspace;
- one active owner membership; and
- exactly one `INITIAL_ACCOUNT_CREATED` security event.

The repository first validates the complete request without storage side effects.
Inside the transactional boundary it resolves the operation reference using an
authoritative view. If a committed operation already exists, reconciliation occurs
without applying current evidence-expiry or key policy and without any new write.
Only after authoritative absence is established may the coordinator's exact
trusted-now and current policy authorize a new operation.

The operation reference may be claimed early in the uncommitted transaction to
serialize concurrent calls. It may already carry its final committed result state
inside that invisible transaction: if any later insert, event append, constraint,
or commit step fails, the operation row rolls back with every other row. No
separate reservation store or visible incomplete state is allowed.

Candidate IDs are never authority before commit. Database constraints, not a
prior existence query, decide collisions under concurrency. An event write failure
fails the transaction. A durable-operation write failure fails the transaction.
There is no compensating-delete substitute for rollback and no later best-effort
event repair path for initial creation.

A normal definitively acknowledged commit returns `CREATED`. A concurrent caller
that loses the operation-reference race must read and reconcile the committed
operation in a new authoritative transaction; it must not blindly rerun the
aggregate inserts.

## Closed outcome mapping

Malformed or unsupported requests fail at the pure validation boundary and are
not repository outcomes. A future adapter maps only the following six closed
results:

- `CREATED`: the complete transaction definitively committed and the immutable
  receipt is known.
- `EXACT_REPLAY`: a previously committed operation under the same reference has
  an exactly replay-equivalent historical request and a complete, internally
  consistent receipt/event graph. It returns the stored receipt, performs no
  write, and emits no event.
- `CONFLICT`: the request definitively did not commit. The closed reason is
  `OPERATION_REFERENCE_MISMATCH` for a non-equivalent request under an existing
  reference, `AUTHORITY_ALREADY_CLAIMED` for current email or issuer/subject
  authority, `EVIDENCE_ALREADY_CONSUMED` for the evidence uniqueness boundary, or
  `RECORD_ID_COLLISION` for an immutable candidate-record/event ID collision.
- `AMBIGUOUS`: dispatch may have reached commit, but neither transaction outcome
  nor durable operation state can be authoritatively established. It has priority
  over `UNAVAILABLE` and `INTERNAL_ERROR`.
- `UNAVAILABLE`: the repository authoritatively knows that no commit occurred,
  such as failure before dispatch or after a confirmed rollback, and storage is
  unavailable.
- `INTERNAL_ERROR`: commit status is known and non-ambiguous, but an unsupported
  schema/version, corrupt graph, invariant failure, storage-protocol failure, or
  unexpected implementation failure prevents a safe result.

Vendor error strings, SQL states, constraint names, row counts, conflicting
values, IDs, email addresses, issuers, subjects, or private storage details must
never cross this outcome boundary. A later adapter must translate reviewed
vendor-specific evidence into these closed results.

## Ambiguous commit reconciliation

After any possible dispatch, connection loss, timeout, cancellation race, failover,
protocol truncation, or unknown commit acknowledgement, the repository must not
assume rollback and must not generate new evidence, entropy, IDs, an event, or an
operation reference.

Reconciliation calls the same `create_initial_account` operation with the exact
same immutable request. It first reads the exact operation reference from the
authoritative write authority using consistency semantics that prove the read is
ordered after the possibly dispatched transaction has terminated. A cache,
eventually consistent replica, stale snapshot, or successful ordinary lookup that
cannot establish this ordering is insufficient.

- A complete supported-version row whose frozen request is exactly equivalent and
  whose receipt, aggregate, evidence, and event graph is consistent returns
  `EXACT_REPLAY`.
- The same operation reference with a non-equivalent request returns `CONFLICT`
  with `OPERATION_REFERENCE_MISMATCH`.
- A present but incomplete, unsupported-version, or inconsistent graph returns
  `INTERNAL_ERROR` when commit status is authoritatively known.
- Authoritatively proven absence after the prior transaction has terminated may
  allow the same logical request to proceed as a new attempt, subject to current
  evidence and key policy.
- If presence or absence cannot be authoritatively resolved, the result remains
  `AMBIGUOUS`. Expired evidence, an outage, or an internal decoder failure cannot
  downgrade that uncertainty.

The durable operation result, not reconstructed current account state, is the
reconciliation anchor. A historical receipt is not current authentication,
workspace authorization, or product entitlement.

## Versioning and migration boundary

The following version axes are mandatory and independent from schema one:

- a database schema revision maintained by the future migration system;
- the public request and receipt contract versions;
- a logical record/schema version on every account entity;
- a positive mutable `row_version` on user, email, identity, workspace, and
  membership rows;
- an operation-record version and exact-request-snapshot format version;
- a provider-evidence record version;
- a security-event record/payload version; and
- credential, lookup-key, binding-key, and security epochs with their own
  semantics.

An unknown newer database, record, request, snapshot, evidence, event, or
credential version fails closed. Readers must not coerce it to version one, ignore
authority-bearing fields, return it as absent, or overwrite it. In operation
reconciliation, an authoritatively readable unknown version is `INTERNAL_ERROR`,
not replay, conflict, or missing.

Because no production account authority is active, the safest schema-one launch
creates every authority-critical field and constraint before the first account
write. Required immutable IDs, canonical equality values, foreign keys, statuses,
roles, security epoch, row versions, timestamps, operation snapshot and receipt,
evidence context, event actor/context, event time, and append ordering must not be
deferred to an unverifiable backfill.

Future changes use a forward-only expand, migrate, and contract process:

1. expand with backward-compatible structures while old readers and writers
   remain safe;
2. deploy readers that understand both old and new supported records;
3. deploy compatible writers without erasing fields unknown to an old version;
4. migrate deterministically with explicit conflict quarantine and audit evidence;
5. validate new constraints and record-version coverage;
6. switch authoritative writers and retain a proven application rollback window;
   and
7. contract only after old writers can no longer run and rollback no longer
   depends on removed structure.

Application rollback is unsafe if the old application cannot read every record
version already written or could overwrite newer authority fields. The future
deployment boundary must therefore check explicit reader/writer compatibility
with the live schema revision before serving authority.

No migration may fabricate or default an immutable account ID, primary email,
verified status, issuer, subject, method, creator, owner role, verification source,
evidence claim, security epoch, row version, event actor, event time, append
position, historical timestamp, operation request, receipt, or entitlement. In
particular, `ACTIVE`, `VERIFIED`, `OWNER`, `security_epoch == 1`,
`row_version == 1`, or current time are valid initial-creation facts only when
explicitly established by the reviewed operation; they are forbidden generic
backfill defaults.

Beta-auth, mutable email-keyed workspace state, mailbox OAuth, mailbox secrets,
user-config KV, and Collaboration records are not production identity evidence
and must not be silently imported into this schema. Any legacy account migration
requires a separate mapping, evidence, conflict, rollback, and audit review.

## Consistent account-authentication read boundary

The current session contract exposes separate record getters. A future relational
resolver must not assemble authentication authority from independently timed
reads that can observe different user, identity, email, session, security-epoch,
or row-version states.

Before session resolution is activated, a separately reviewed contract must
define one immutable authoritative authentication snapshot containing only:

- the stored session record;
- the current user;
- the session-linked authentication identity;
- the user's exact current primary verified email;
- every relevant status;
- user and session security epochs;
- record schema and row versions;
- credential lookup and binding digests;
- credential, lookup-key, and binding-key epochs; and
- authenticated, issued, last-used, idle-expiry, absolute-expiry, revocation, and
  other required session lifecycle times.

The snapshot is read with one relational consistency point and validates all
foreign-key links and epoch equality before a resolver can mint an authenticated
capability. Returning `None` means only an authoritative successful absence.
Storage outage remains authentication unavailable, and an unknown version or
corrupt snapshot remains an internal error.

`StoredSessionSnapshot` currently carries `credential_epoch` but not the
lookup-key and binding-key epochs returned by the session-credential boundary. Exact
storage and readback of those key epochs is a future session-resolver activation
blocker. This document does not modify the session contract, choose a session
store, or decide whether account and session records share one physical database.
If they do not share one transactional store, a later design must prove an
equivalent cross-store consistency and revalidation protocol; ordinary sequential
reads are not sufficient.

The current session-contract validation path also does not yet prove a closed,
typed distinction between normal missing, expired, revoked, or otherwise invalid
authentication and an unsupported stored version or corrupt persisted graph.
Before session activation, a separately reviewed session-contract and resolver
error taxonomy must preserve `AUTHENTICATION_REQUIRED` for ordinary invalid
authentication, `AUTHENTICATION_UNAVAILABLE` for authority outage, and
`INTERNAL_ERROR` for known non-ambiguous version or integrity failure. This
relation slice neither changes that contract nor treats its future correction as
implicitly approved.

Workspace, membership, role, selected workspace, product, plan, and entitlement
facts are explicitly absent from the account-authentication snapshot. After
authentication, a separate future authorization read may take an immutable
`workspace_id` and `user_id` and return the current workspace and membership
records, including status, role, schema version, and row version. Product access
then requires a separate authoritative entitlement decision.

## Product entitlement boundary

The account and login model is product-neutral. No account, email, identity,
operation, event, session, or cookie receives an Email Client, Organizer, Bundle,
plan, package, subscription, billing, seat, or entitlement account type.

- Email Client later corresponds to the `email_client` workspace entitlement.
- Organizer later corresponds to the `organizer` workspace entitlement.
- Bundle is the commercial combination of those two entitlements, not a third
  identity, account, session, or workspace type.

The immutable `user_id` and `workspace_id` are sufficient logical references for
a later server-side entitlement authority. Adding that authority must not require
changing authentication-identity keys or treating email as workspace ownership.
Subscription, billing, seat, and entitlement relations are outside this slice.
Browser input, a login credential, account identity, or a session cookie is never
authoritative for product access.

## Multi-host login and session boundary

Cuevion uses at least `app.cuevion.com` and `organizer.cuevion.com`. The reviewed
session-credential boundary uses a host-only `__Host-` cookie. A broad shared
parent-domain cookie would remove that host isolation, expand the effect of a
compromised sibling host, and cannot be assumed safe merely because both hosts
belong to Cuevion.

One immutable Cuevion account may later participate in a central login flow while
each product host receives its own opaque, stateful, revocable, host-only product
session. The account store needs only current user, identity, primary-email,
status, security-epoch, and version facts to support that future flow. Product
rights remain a server-side workspace-entitlement decision and are never copied
from or trusted in a cookie.

The login host, redirect topology, state/nonce/challenge protocol, one-time code or
token exchange, session issuance, audience binding, host binding, cookie names and
emission, logout propagation, account switching, SSO error handling, and rollout
remain separately reviewed activation boundaries. This relation slice implements
none of them.

## Existing stores are not account authority templates

Existing user-config, mailbox OAuth-token, mailbox-secret, and Collaboration
stores have narrower product scopes. Their use of mutable email keys, JSON/KV
records, runtime files, raw provider tokens, separate writes, check-then-set,
compensating rollback, TTLs, Redis scripts, or limited outcome vocabularies does
not make those patterns approved for account authority.

Atomic Redis scripts may demonstrate the general need for atomic multi-record
state transitions and compare-and-swap, but they do not provide relational foreign
keys, schema-one account uniqueness, durable initial-account operation history,
one-to-one security events, authoritative migration versioning, or unknown-commit
reconciliation. No existing cache, beta store, mailbox store, or Collaboration
store may become the only authority or a fail-open fallback for this boundary.

This statement does not claim a defect in those components within their existing
limited scopes.

## Deferred implementation decisions and activation blockers

Only after a database vendor and migration system are selected and separately
reviewed may a later phase decide or implement:

- physical column types, collations, encodings, timestamp units, and size limits;
- the concrete conditional-uniqueness mechanism;
- commit-time handling of cyclic foreign keys;
- transaction isolation, locking, serialization, and deadlock behavior;
- constraint names and vendor-error translation;
- DDL, schema ledger, generated or handwritten migrations, online index creation,
  validation, and rollback mechanics;
- the exact physical encoding of historical request snapshots and event payloads;
- event append-position generation;
- connection configuration, pooling, credentials, TLS, timeouts, cancellation,
  retries, and failover;
- authoritative post-timeout commit-status resolution;
- production/preview databases, namespaces, credentials, and key isolation;
- a concrete repository adapter and session/account snapshot adapter; and
- real-database tests for concurrency, duplicate creation, exact replay, partial
  failure, event failure, constraint races, stale row versions, isolation,
  ambiguous commit, failover, corruption, migration, application rollback, and
  recovery.

Choosing or implementing any item in that list is outside this slice. A concrete
adapter must not be written merely because this abstract contract exists.

## Required evidence before any future activation

Activation remains blocked until a later reviewed implementation proves, against
the selected production storage semantics:

- all schema-one constraints and exact equality behavior;
- all-or-nothing initial creation and event persistence;
- exact replay across concurrent processes and restarts;
- durable evidence consumption and operation reconciliation;
- correct closed conflict classification without authority enumeration;
- `AMBIGUOUS` precedence after every possible-dispatch failure mode;
- authoritative absence and safe retry behavior;
- corruption and unknown-version failure behavior;
- compare-and-swap, monotone security epoch, primary-email consistency, and
  last-owner behavior for every later mutation that is enabled;
- expand/migrate/contract and application rollback compatibility;
- one-point authentication snapshot consistency or a separately proven
  equivalent; and
- complete production/preview isolation.

Provider verification, session lifecycle, cookie handling, product entitlement,
HTTP integration, migration of legacy users, monitoring, operational response,
rollout controls, rollback, and an explicit activation decision remain additional
independent gates. Nothing in this document activates authentication or grants
authority.
