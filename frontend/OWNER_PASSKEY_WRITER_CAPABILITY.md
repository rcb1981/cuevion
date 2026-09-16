# Temporary passkey writer capability diagnostic

Baseline: `efe50d08e120afb1cc1558729a04fe7af595b8a7`.

`GET /api/auth/passkey-migration/writer-capability` diagnoses the configured
writer without starting, retrying, or completing a migration. It changes no
Auth0 setting, database row, grant, schema, session, or KV record.

The existing callback consumes the OAuth transaction before token exchange and
identity validation. A consumed candidate with no new identity does not establish
which later step failed. This diagnostic tests current writer capabilities; it
does not reconstruct the failure of an earlier callback.

## Authorization and safe output

The route reuses the migration status boundary: production only, canonical host,
same-origin GET, no query/body/selectors, valid first-party session, current
canonical OWNER, initial-owner provenance, and the strict migration envelope.
GET may omit Origin; any supplied Origin must match, and Sec-Fetch-Site must be
same-origin. The same pending-invitation/provisioning/continuation checks apply.
Canonical state, session and read-only KV snapshots are rechecked after the writer
has rolled back and closed. Total response verification is bounded to 20 seconds.

Responses use `Cache-Control: no-store`. They contain booleans and the fixed enums
below only. No URLs, DSNs, provider subjects, internal IDs, hostnames, database or
role names, credentials, SQLSTATE, exception text or mailbox data are returned.
The route suppresses default request/error logging and sends no cookies.

Once current canonical authorization succeeds, the capability result includes:

- `writerDatabaseUrlConfigured`, `readerDatabaseUrlConfigured`,
  `writerDistinctFromReader`.
- `writerConnectionEstablished`, `tlsInUse`, `autocommitFalse`.
- `selectUsers`, `selectVerifiedEmails`, `selectAuthenticationIdentities`,
  `selectWorkspaces`, `selectWorkspaceMemberships`.
- `insertIdentityPrivilege`, `exactPhase1LockAcquired`.
- `transactionRolledBack`, `connectionClosed`.
- `currentOwnerStillSingleCanonicalIdentity`, `ownerEnvelopeStillSafeForRetry`.
- `targetState = "not_recoverable_from_consumed_candidate"`.

A false capability flag means that capability was not proven; checks stop at the
first failure. Configuration presence and raw URL distinctness match Phase 1's
configuration checks; distinct URL strings do not prove distinct database roles.
Malformed writer configuration is classified as `writer_configuration_missing`.
If reader/session authorization cannot be established, only a generic fixed
classification is returned, without bypassing the canonical OWNER boundary.
An owner whose second canonical identity is already present gets
`owner_envelope_changed`, the target-state fallback and false retry/single-identity
flags; the writer is not opened in that case.

The existing Phase 1 candidate and consumed-status metadata do not retain the
newly validated Auth0 subject. They bind the OLD owner and OAuth transaction.
The diagnostic neither adds target storage nor guesses from another OIDC row.
Consequently it omits `targetIdentityAlreadyCanonical` and always returns the
required target-state fallback after successful canonical authorization.

`ready` means that the writer capabilities were proven and cleanup succeeded.
It does not prove that the previous callback passed OAuth, email, binding, or
target-identity checks, or authorize a migration retry. The retry-envelope flag
describes the checked canonical/Team state, not a recovered target proof.

## Closed classification enum

| Classification | Meaning |
| --- | --- |
| `ready` | All capability checks and cleanup succeeded. |
| `writer_configuration_missing` | Writer/reader configuration absent or writer URL invalid. |
| `writer_not_distinct` | Writer and reader URL strings are identical. |
| `writer_connection_failed` | No writer connection was returned; includes handshake failures. |
| `writer_tls_invalid` | An established connection did not prove TLS in use. |
| `writer_select_privilege_missing` | PostgreSQL denied a SELECT-stage operation for insufficient privilege. |
| `writer_lock_privilege_missing` | PostgreSQL denied the exact Phase 1 lock for insufficient privilege. |
| `writer_insert_privilege_missing` | INSERT privilege introspection returned false; the lock check succeeded. |
| `owner_envelope_changed` | The initial-owner/retry envelope no longer holds, or changed during diagnosis. |
| `target_already_attached` | Reserved allowed classification; not emitted without recoverable target metadata. |
| `unavailable` | Contention, timeout, non-idle/autocommit connection, unexpected response/error, cleanup failure, or unavailable authorization. |

Only the driver's typed `InsufficientPrivilege` exception determines a missing
SELECT/lock privilege. Contention is not misreported as missing permission.
Driver error messages and SQLSTATE are never copied into a response.

## Exact writer operations and zero-write proof

The writer uses the existing strict URL parser, preserving `sslmode=require` and
`channel_binding=require`, with a three-second connection timeout and
`autocommit=False`. The live driver's `pgconn.ssl_in_use` must be exactly true,
and its transaction status must be idle before any SQL.

The driver implicitly begins one transaction before the first statement. That
transaction uses only these statements, in order:

```sql
SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
SET LOCAL statement_timeout = '1000ms';
SET LOCAL lock_timeout = '200ms';
SET LOCAL idle_in_transaction_session_timeout = '2000ms';

LOCK TABLE cuevion_account.users, cuevion_account.verified_emails,
  cuevion_account.authentication_identities, cuevion_account.workspaces,
  cuevion_account.workspace_memberships IN ACCESS SHARE MODE NOWAIT;

SELECT * FROM cuevion_account.users LIMIT 0;
SELECT * FROM cuevion_account.verified_emails LIMIT 0;
SELECT * FROM cuevion_account.authentication_identities LIMIT 0;
SELECT * FROM cuevion_account.workspaces LIMIT 0;
SELECT * FROM cuevion_account.workspace_memberships LIMIT 0;

SELECT pg_catalog.has_table_privilege(
  'cuevion_account.authentication_identities', 'INSERT');

LOCK TABLE cuevion_account.users, cuevion_account.verified_emails,
  cuevion_account.authentication_identities, cuevion_account.workspaces,
  cuevion_account.workspace_memberships IN SHARE ROW EXCLUSIVE MODE NOWAIT;

ROLLBACK;
```

The final lock imports Phase 1's exact table list, order and mode, adding only
NOWAIT. The weaker preflight prevents SELECT from queueing behind DDL; NOWAIT
also prevents the final lock from waiting behind active writes. No SQL or network
check occurs between successful strong-lock acquisition and rollback. The local
200ms lock timeout is below the requested 250ms cap. PostgreSQL documents
[NOWAIT and transaction-scoped locks](https://www.postgresql.org/docs/current/sql-lock.html)
and [privilege introspection](https://www.postgresql.org/docs/current/functions-info.html#FUNCTIONS-INFO-ACCESS-TABLE).

The transaction cannot be marked READ ONLY because the requested strong lock
requires a transaction permitting that lock mode. Zero data writes are established
by the fixed statement list and SQL-recording tests: no INSERT, UPDATE, DELETE,
TRUNCATE, schema/grant command, test insert, or COMMIT exists in the probe.

Every returned connection reaches rollback in `finally`, including TLS/property
and SQL failure paths. Cursor and connection cleanup remain attempted when rollback
fails. Connection close releases any remaining server transaction; a failed close
also attempts native `pgconn.finish()`. Cleanup failures yield `unavailable`, never
`ready`. The server-side idle transaction timeout additionally bounds idle locks
if the client transport is lost. The probe uses no connection pool.

The existing canonical reader uses its own rolled-back read-only transactions.
The endpoint's KV path uses GET and the existing bounded read-only snapshot Lua
only. It never calls candidate consumption, session mutation, or the migration
writer. Invalid sessions are loaded with `delete_invalid=False`.

## Verification

The new unit tests cover unauthorized/member/admin/foreign-origin requests,
production/host/body/query boundaries, every configuration and writer failure,
safe output, every statement failure, cleanup fallback, metadata limitations,
changed canonical state and unchanged sessions/KV/identities.

The PostgreSQL tests use an isolated temporary cluster and synthetic local roles.
They prove actual SELECT/lock/INSERT privilege outcomes, NOWAIT behavior against
active write/DDL locks, unchanged persisted columns in all five authority tables,
and rollback/release after a failure injected immediately after strong-lock
acquisition. A separate connection can acquire ACCESS EXCLUSIVE NOWAIT afterwards.
All fixture DDL/grants/inserts occur only in that disposable cluster.

The available PostgreSQL 16.2 test binary has no SSL support. Successful real-SQL
tests therefore use an explicitly marked test-only TLS attestation over a local
Unix socket. A real plaintext connection is separately rejected, and unit tests
verify TLS checks plus strict connector options. No live TLS/Production capability
result is claimed. Local Python is 3.11.1; repository configuration remains 3.12.

Existing Phase 1 start/status/callback implementation and all legacy tests remain
unchanged. The focused regression selection includes auth/session/account-authority,
both migrations, canonical PostgreSQL reads/schema, and the retained diagnostic.
The known unrelated broad-suite baseline failures are not repaired by this work.

Final local verification: **389 tests and 1,237 subtests passed**, including all
26 newly added diagnostic tests. `npm run test:base`, `npm run build`, and the
scoped `git diff --check` passed. Build retained the existing browser-data-age
and bundle-size warnings.

Focused command (with the disposable PostgreSQL binary path configured):

```text
python -m pytest --import-mode=importlib -q --tb=short
  api/auth tests/cuevion_migration
  tests/cuevion_db/test_postgresql_owner_passkey_migration.py
  tests/cuevion_db/test_passkey_writer_capability.py
  tests/cuevion_db/test_postgresql_current_account_repository.py
  tests/cuevion_db/test_account_schema.py
  tests/cuevion_auth/test_identity_inventory_diagnostic.py
```
