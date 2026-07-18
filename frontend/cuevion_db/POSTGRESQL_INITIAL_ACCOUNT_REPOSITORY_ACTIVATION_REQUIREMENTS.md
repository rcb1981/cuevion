# PostgreSQL initial-account repository activation requirements

## Status: completely inactive concrete adapter

`cuevion_db.postgresql_initial_account_repository` is the first concrete
Psycopg 3 implementation of the frozen `InitialAccountRepository` protocol and
PostgreSQL schema one. It is completely inactive. No bootstrap or caller exists,
no active code constructs its connection factory or new-operation authorizer,
and no route imports it. Import and construction open no connection and perform
no filesystem, network, environment, clock, random, logging, provider, or secret
access.

This slice adds no runtime configuration or environment variable. It knows no
DSN or credential, creates no Engine or pool, and grants no schema, table,
sequence, or function privilege. No login, session, cookie, entitlement,
mailbox, Collaboration, account UI, handler, router, or application integration
is included. Its presence is not an activation decision.

The adapter accepts only two caller-injected boundaries:

- a narrow factory returning one fresh synchronous Psycopg-compatible
  connection for each normal call and each required reconciliation; and
- a pure authorizer that is invoked exactly once only after authoritative
  operation absence has been established.

The authorizer owns current provider verification, key, evidence-freshness,
invitation, plan, and business-policy decisions. Returning `None` denies a new
write as `UNAVAILABLE`. An unexpected authorizer failure or a context that is
not exactly bound to the validated request produces the fixed `INTERNAL_ERROR`
outcome before database writes.

## Exact replay and closed conflict precedence

The adapter first runs the existing pure request validator. An invalid request
raises the existing fixed contract validation error before either injected
dependency is called.

For a valid call it takes the operation-reference advisory lock and reads the
complete immutable `initial_account_operations` snapshot before applying current
policy. A stored operation is decoded with exact type, version, enum, digest,
identifier, and timestamp codecs. The stored receipt and immutable security event
must form the same complete operation, snapshot, evidence, receipt, and event
graph. Database foreign keys bind the historical receipt and event to the
original aggregate IDs.

An exact field-by-field stored request returns `EXACT_REPLAY` with the historical
receipt and performs no write or new event. A valid mismatch returns `CONFLICT`
with `OPERATION_REFERENCE_MISMATCH`. Malformed, unsupported, incomplete, extra,
or inconsistent durable graph state returns `INTERNAL_ERROR`. Replay never uses
only the operation digest and never calls the new-operation authorizer.

Historical replay does not require current mutable account rows to remain equal
to their creation values. User status, primary email, display name, security
epoch, update timestamp, and row version; verified-email lifecycle state;
identity lifecycle and last-use state; workspace lifecycle state; and membership
role or lifecycle state may change through separately reviewed repositories.
Those changes neither rewrite the frozen operation snapshot nor invalidate an
otherwise exact replay. A historical receipt is not a current authorization
decision. Current account, identity, workspace, membership, and product authority
must be read and evaluated through separate current-authority boundaries.

For a genuinely new operation with known non-commit, conflict checks remain in
this exact order:

1. `EVIDENCE_ALREADY_CONSUMED` for the trust-domain, verification-coordinator,
   and assertion-ID claim;
2. `AUTHORITY_ALREADY_CLAIMED` for either the current verified/non-retired
   canonical-email claim or exact case-sensitive issuer and subject claim; and
3. `RECORD_ID_COLLISION` for a candidate user, verified-email, authentication-
   identity, workspace, membership-pair, or security-event ID.

Constraint names, insert order, race order, query plans, row counts, vendor
messages, and conflicting authority values never determine or appear in the
public result.

Every evidence, authority, and record-ID existence query uses one fixed
`SELECT EXISTS` statement. Its result must be exactly one tuple containing one
exact built-in boolean. Any other shape or scalar type is storage-protocol
corruption and produces `INTERNAL_ERROR`, never a business conflict.

## SERIALIZABLE transaction and advisory locks

Every normal repository call uses one fresh connection with `autocommit` exactly
false and one `READ WRITE SERIALIZABLE` transaction. All SQL is module-private,
fixed, explicitly columned, and parameterized. There are no dynamic identifiers,
`SELECT *`, ORM operations, DDL, grants, savepoints, partial receipts, pending
operation rows, compensating writes, or interpolated authority values.

Transaction-scoped advisory locks use `hashtextextended` with versioned,
length-framed, domain-separated material. Their fixed order is:

1. operation reference;
2. evidence assertion claim;
3. canonical verified-email authority claim;
4. issuer and exact case-sensitive subject identity claim;
5. candidate user ID;
6. candidate verified-email ID;
7. candidate authentication-identity ID;
8. candidate workspace ID;
9. candidate membership pair; and
10. candidate security-event ID.

With Psycopg 3's binary implementation, PostgreSQL's `void` result from
`pg_advisory_xact_lock()` decodes as an exact built-in empty string. The adapter
therefore accepts only exactly one row shaped as `("",)`; every other row shape
or scalar is storage-protocol corruption. This decoding correction activates no
route, connection bootstrap, or database connection.

The operation is read again after all locks. Hash collisions can only add
serialization; they do not merge claims or weaken exact database equality. Only
the existing operation digest/reference crosses this boundary; a raw operation
key is never accepted, stored, locked, logged, or rendered.

For an authorized conflict-free operation, all constraints are deferred, one
`cuevion.account.security` stream position is explicitly allocated from
`cuevion_account.security_event_stream_position_seq`, and exactly seven rows are
inserted in schema order: user, verified email, authentication identity,
workspace, owner membership, operation, and security event. All constraints are
forced immediate before commit. No column default supplies an authority fact.

Sequence gaps caused by rollback are allowed. A stream position is ordering
metadata, not wall-clock or commit-order authority.

## Trusted-now and codecs

`InitialAccountWriteContext` is an immutable schema-one adapter value containing
an exact built-in integer `trusted_now` and exact request-bound operation,
assertion, trust-domain, and coordinator values. It is neither a public account
repository contract extension nor a replay field.

The adapter uses the same `trusted_now`, without float conversion, for
`committed_at`, `event_at`, and `recorded_at`. Integer and UTC-aware datetime
conversion uses the Unix epoch plus `timedelta`; `datetime.timestamp()` is not
used. Naive, non-UTC, fractional, non-built-in, boolean, negative, and
out-of-domain timestamp values fail closed. Base64url operation digests and
assertion IDs are canonicalized losslessly to exactly 32 database bytes and back.

## Integrity reconciliation and commit ambiguity

An unexpected integrity failure is never mapped from a constraint name. After a
confirmed rollback, a fresh connection first resolves the operation again. If it
is absent, the same advisory locks and frozen evidence, authority, and record-ID
checks are repeated. A valid classification is returned; otherwise the result is
`INTERNAL_ERROR` or, for an availability failure with known non-commit,
`UNAVAILABLE`.

Any exception during or directly around commit is treated as possible commit.
A fresh reconciliation connection takes the operation lock and reads the full
durable graph:

- exact consistent durable state returns `CREATED` with the stored receipt;
- a valid request mismatch returns `OPERATION_REFERENCE_MISMATCH`;
- corrupt durable state returns `INTERNAL_ERROR`;
- authoritative operation absence returns `UNAVAILABLE`; and
- inability to reach an authoritative conclusion returns `AMBIGUOUS`.

Commit ambiguity always takes precedence over ordinary availability and internal
classification while status remains unknown. A later ordinary call with the
same exact committed request returns `EXACT_REPLAY`, not `CREATED`.

Connections and cursors are closed on every path; every non-committed transaction
is asked to roll back. Results and controlled exceptions contain no request,
email, identity, subject, digest, record, table, constraint, or PostgreSQL error
detail.

The known-non-commit availability allowlist contains Psycopg operational,
serialization, and deadlock failures. `InterfaceError` is not generic evidence of
an outage: before commit it is treated as an internal interface or protocol
failure. Any exception during or around commit still follows fresh durable-state
reconciliation. `KeyboardInterrupt`, `SystemExit`, `GeneratorExit`, and other
non-`Exception` `BaseException` instances are never converted into repository
outcomes.

## Remaining activation gates

No minimal runtime DML or sequence privileges have yet been established or
granted. Before activation, Preview must use the real proposed runtime role and
prove the least-privilege table and sequence grants, the absence of function
`EXECUTE` through direct grants, `PUBLIC`, or inherited memberships, and the
continued operation of schema triggers without runtime function-`EXECUTE`
privileges.

Preview testing must use a real PostgreSQL instance and independently prove:

- forward migration and exact schema-head verification;
- process restart, pooled and direct connection behavior, and pooler semantics;
- connect, statement, lock, and transaction timeouts;
- disconnect before, during, and after commit;
- deadlock and serialization rollback/retry classification;
- every constraint and deferred graph trigger;
- exact replay, mismatch, corruption, and unknown-version handling;
- concurrent identical and conflicting operations across processes;
- evidence, email, identity, record-ID, event-ID, and advisory-hash collisions;
- sequence allocation and permitted rollback gaps;
- trigger behavior without function `EXECUTE` privilege; and
- zero authority enumeration in application errors, logs, metrics, and traces.

The complete migration, privilege, concurrency, corruption, timeout, pooler,
failover, disconnect-at-commit, rollback, and recovery evidence must be repeated
independently in Production before activation. Production and Preview must have
separate resources, roles, credentials, and data.

No route, connection bootstrap, authorizer construction, runtime configuration,
grant, login/session flow, monitoring rollout, or retry coordinator may be added
without a later explicit activation decision and review.
