# PostgreSQL current-account repository activation requirements

## Status: completely inactive read adapter

`cuevion_db.postgresql_current_account_repository` is an inactive synchronous
Psycopg 3 implementation of the current-account authority read contract. Merely
importing or constructing it does not open a connection, read configuration,
authenticate a user, authorize a workspace, or activate a feature.

There is no caller, route, handler, login flow, session integration, cookie or
token handling, OAuth flow, runtime bootstrap, DSN, environment lookup, secret
lookup, global connection, connection-pool construction, frontend integration,
Preview activation, or Production activation in this slice. The adapter accepts
only a caller-injected connection factory and owns no credentials or deployment
configuration.

## Current authority boundary

Only these five `cuevion_account` tables are current authority for this adapter:

1. `users`;
2. `verified_emails`;
3. `authentication_identities`;
4. `workspaces`; and
5. `workspace_memberships`.

`initial_account_operations` is immutable creation and replay history.
`security_events` is an audit stream. Neither table is queried by this adapter,
and neither a creation receipt nor an exact replay result can authorize current
access. The adapter also performs no sequence access and calls no database
function.

The adapter exposes exactly two public reads:

- resolve a complete current-account authority graph by an already-canonical
  exact issuer, an exact case-sensitive subject, and an explicit immutable
  workspace ID; and
- read a complete current-account authority graph by an immutable user ID and an
  explicit immutable workspace ID.

The identity operation returns current user, primary verified email,
authentication identity, workspace, and membership records. The user-ID
operation returns current user, primary verified email, workspace, and
membership records. Both operations require an active user, a current verified
and non-retired primary email, an active workspace, and an active membership.
Identity resolution additionally requires an active identity owned by the user.

Workspace ID is mandatory input. No selected or default workspace exists in the
schema, and the adapter never infers one from creation history,
`created_by_user_id`, membership ordering, or a first-workspace assumption.
Creator provenance is not owner authority; current membership role and status
are authoritative.

The repository compares issuer and subject exactly. It never trims, lowercases,
casefolds, Unicode-normalizes, or otherwise transforms either value. A reviewed
upstream boundary must supply an already-canonical issuer. Provider-specific
issuer canonicalization remains unresolved and is an activation blocker.

No migration is required for these exact keyed reads. This document does not
authorize a migration or reinterpret the completed initial-account write
foundation.

## Read-only transaction and cleanup boundary

Every call uses one caller-supplied non-autocommit connection and one
`REPEATABLE READ READ ONLY` transaction. Transaction configuration is the first
SQL statement. Exactly one fixed aggregate `SELECT` follows. Each query begins
with one parameterized synthetic `VALUES` request row and reaches current tables
through explicit `LEFT JOIN`s, producing one deterministic tuple even for normal
authority absence.

There is no `COMMIT`, write, row lock, advisory lock, retry loop, sequence
allocation, function call, second independently timed authority lookup, or
historical-table fallback. The cursor is closed, every read transaction is asked
to roll back even after success, and the connection is closed or returned
exactly once. Confirmed rollback is part of safe pooled-connection hygiene.

A committed-before snapshot is visible and a committed-after snapshot is not.
The result proves authority only at that consistency point; a later mutating
operation must reauthorize within its own transaction.

## Closed outcomes and value-free failures

The public outcome set is exactly:

- `FOUND`: one complete, structurally valid, internally consistent, active
  aggregate is returned;
- `NOT_AUTHORIZED`: expected absence or valid but inactive current authority;
- `UNAVAILABLE`: operational connection, transport, timeout, disconnect,
  serialization, or rollback failure; and
- `INTERNAL_ERROR`: storage corruption, unsupported values, malformed driver
  protocol, wrong row shape/count, schema or permission failure, or unexpected
  implementation failure.

Only `FOUND` carries an aggregate. All other outcomes are value-free. They expose
no identifier, email, issuer, subject, SQL text, row value, reason string,
database diagnostic, constraint, table name, hostname, URL, credential, or
exception message. There is no read-side `AMBIGUOUS` result because this adapter
never commits a mutation.

Invalid caller inputs fail before connection acquisition with one fixed,
value-free contract-validation exception. The adapter performs no logging.

## Strict storage protocol

The adapter accepts only an exact built-in list from `fetchall()`, containing
exactly one exact built-in tuple of the expected width. Zero, multiple,
list-shaped, mapping-shaped, named-tuple, tuple-subclass, or partial rows are
internal errors. `LIMIT` is forbidden because it could hide broken uniqueness.

Text, integer, and datetime columns require exact built-in scalar types. Boolean
values and scalar subclasses are not integers or valid storage values. IDs remain
canonical typed `VARCHAR` strings and are never UUID objects. Timestamps must be
timezone-aware, zero-offset UTC, whole-second, in range, and losslessly
convertible to the model's Unix-second representation. Schema versions, row
versions, security epoch, enum values, nullability, identifiers, canonical email,
lifecycle ordering, and cross-record immutable relationships are independently
validated before the existing immutable models and public graph validators are
used as a second validation layer. Driver values are never coerced.

## Privilege boundary

This slice creates no role and applies no grant. A future dedicated read-only
runtime role should receive only:

- database `CONNECT`, as the deployment topology requires;
- `USAGE` on schema `cuevion_account`; and
- `SELECT` on exactly `users`, `verified_emails`,
  `authentication_identities`, `workspaces`, and `workspace_memberships`.

It does not need or receive:

- `SELECT` on `initial_account_operations` or `security_events`;
- sequence `USAGE`, `SELECT`, or `UPDATE`;
- function `EXECUTE`;
- `INSERT`, `UPDATE`, or `DELETE` on any table;
- `REFERENCES`, `TRIGGER`, or temporary-table privileges.

Sharing a write-capable runtime role would weaken least privilege. Role
separation and live grants remain later activation decisions and require an
independent review.

## Required Preview evidence before activation

Future validation may approach only a separately approved, synthetic Preview
resource and must use the proposed read-only runtime role. It must prove:

- the exact five-table privileges and absence of every excluded privilege,
  including privileges inherited through memberships or `PUBLIC`;
- fixed query shape and exact Psycopg binary row/scalar decoding;
- one-snapshot visibility across concurrent user, email, identity, workspace,
  membership, role, and status changes;
- normal absence, inactive authority, corruption, unsupported versions, and
  duplicate-row failure behavior;
- transport, timeout, disconnect, serialization, rollback, cursor, connection,
  and pool-return cleanup behavior;
- one aggregate authority `SELECT` per call and no writes, sequence use,
  function execution, historical-table access, hidden retry, or logging;
- process restart and configured pooler behavior; and
- zero authority enumeration through results, exceptions, logs, metrics, traces,
  or support output.

Production must never be approached during this validation. The current Preview
authority rows and sequence position must not be changed by this slice. The
existing live rollback probe must not be run because every execution consumes a
sequence position. No Preview or Production test, migration, grant, route,
bootstrap, or activation is performed here.

## Remaining activation gates

Activation still requires a reviewed upstream issuer-canonicalization boundary,
an explicit workspace-selection design where a product needs one, secure
configuration and connection/pool ownership, timeouts, operational telemetry,
dedicated least-privilege roles, synthetic Preview evidence, an independent
Production rollout plan, and an explicit activation decision.

Login, sessions, cookies, tokens, OAuth, provider verification, account linking,
email replacement, recovery, last-owner mutation protection, product
entitlements, routes, handlers, frontend behavior, monitoring, rollout, and
rollback remain separate boundaries. Nothing in this file activates or approves
them.
