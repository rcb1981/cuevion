# PostgreSQL account schema-one foundation activation requirements

## Status: completely inactive foundation

This directory defines an inert PostgreSQL schema foundation. It constructs
Python objects and renders offline SQL, but opens no database connection, creates
no SQLAlchemy Engine, applies no migration, reads no process environment, owns no
Neon or Vercel resource, and contains no credential. No route under
`frontend/api/**/*.py` imports or uses it. Nothing here creates an account,
authenticates a user, issues a session, grants a product entitlement, or activates
Collaboration. No deployment has been performed, no account database has been
activated, and the foundation remains functionally and at runtime inactive.

The foundation is not deployment-input-neutral. `frontend/.python-version` and
the requirements files change the future Vercel build and dependency inputs. A
deployment after commit will therefore select Python 3.12 and build or install
the new runtime packages, but that alone activates neither account authentication
nor a database connection. Build success and the import safety of existing Python
routes must be checked before staging and again after deployment.

Rutger is currently Cuevion's only active tester. The two people who previously
tested design and functionality temporarily through the legacy beta allowlist are
no longer active and have no real Cuevion production accounts. That allowlist is
not an account registry or account authority. This foundation must be
invite-ready before any new invitations are sent.

## Frozen foundation stack

The foundation selects Python 3.12 and PostgreSQL. Its `~=` dependency rules are
compatible-release bounds, not exact pins. The current development and review
versions are Psycopg binary 3.3.4, sync SQLAlchemy 2.0.51 Core, and Alembic
1.18.5; the compatible-release bounds do not exclude future compatible patch
releases. SQLAlchemy ORM, async database dependencies, `psycopg_pool`,
`create_all`, an Engine, and a repository adapter are absent. Neon is only the
future managed PostgreSQL host; no Neon project, branch, role, database,
integration, SDK, or Neon Auth behavior exists now.

Schema one is `cuevion_account` and contains exactly seven authority tables:
`users`, `verified_emails`, `authentication_identities`, `workspaces`,
`workspace_memberships`, `initial_account_operations`, and `security_events`.
The Alembic ledger is separate at
`public.cuevion_account_alembic_version`. Revision
`0001_account_schema_1` is the single base and head. Authority migrations are
forward-only; repair or rollback requires a new reviewed forward revision and a
reader/writer-compatible application rollout.

## Secret-safe configuration boundary

`cuevion_db.configuration` parses only a caller-supplied mapping. It never reads
`os.environ`. A future protected runtime or migration runner must supply:

- `CUEVION_DATABASE_URL`: pooled Neon runtime URL with a `-pooler` endpoint;
- `CUEVION_DATABASE_URL_UNPOOLED`: direct migration URL without `-pooler`;
- `CUEVION_DATABASE_TARGET`: exactly `production` or `preview`;
- `VERCEL_ENV`: exactly equal to the selected target; and
- `PSYCOPG_IMPL`: exactly `binary`.

Both URLs require `sslmode=require`; `channel_binding` may be absent or exactly
`require`. There is no pooled/direct fallback. The URLs must identify the same
logical Neon endpoint, port, and database, although future privilege separation
requires distinct usernames. Configuration failures and record rendering are
fixed and value-free. No actual environment values or example credential files
are part of this foundation.

Production and Preview require separate resources, roles, credentials, trust,
and synthetic-data policy. Preview must never inherit Production authentication
data. Development is not a database target.

## Authority types, constraints, and event stream

Canonical record and event IDs are `VARCHAR(26) COLLATE "C"` with their exact
typed base64url checks. Fixed 32-byte digests are `BYTEA`. Exact authority text
uses `COLLATE "C"`; statuses, roles, methods, and event types use text plus named
closed checks, never native enums. Snapshots are individual typed scalar columns,
not JSON, arrays, blobs, pickle, or key/value data. No authority column has a
database default.

Logical timestamps are exact built-in Python integers representing integral Unix
UTC seconds in the inclusive range `0..253402300799`. PostgreSQL stores them as
finite UTC-aware `TIMESTAMP WITH TIME ZONE` values with whole-second and matching
range checks. The later repository must implement exact integer/aware-datetime
codecs without a floating-point `datetime.timestamp()` authority round trip. It
must generate trusted `committed_at`, `event_at`, and `recorded_at` values.

The schema contains named primary keys, carrier uniques, same-user foreign keys,
the partial unique current verified-email claim, complete receipt bindings, and
the 15-column security-event-to-operation superkey foreign key. All foreign keys
use `NO ACTION`; only necessary aggregate cycles are initially deferred.

The only schema-one logical stream is the exact case-sensitive
`cuevion.account.security`. Its positive position and name are
repository-generated, never browser/provider/request input, and remain outside
the caller-controlled request snapshot. The stream tuple is unique. The
standalone sequence has no column default; a later repository must allocate a
position explicitly. Allocation can contain rollback gaps and is not itself
wall-clock or commit-order authority.

## Triggers and repository boundary

Revision 0001 installs fixed, value-free PostgreSQL functions and triggers that:

- reject UPDATE, DELETE, and TRUNCATE on operations and security events;
- protect immutable mutable-table fields;
- require each accepted mutable update to advance `row_version` by exactly one;
- prevent `security_epoch` from decreasing; and
- defer validation that the frozen operation, live initial aggregate, receipt,
  and security event form one internally consistent graph.

PostgreSQL does not implement the future repository workflow. The adapter remains
deferred and must separately prove pure request validation before dispatch,
operation lookup before current policy, authoritative exact replay and ambiguous
commit reconciliation, transaction-scoped advisory locking, row-version CAS,
last-owner protection, primary-email mutation, trusted-time codecs, conflict
mapping, and event-position allocation.

For an existing operation reference, an exact immutable request returns
`EXACT_REPLAY`; a mismatch returns `CONFLICT` with
`OPERATION_REFERENCE_MISMATCH`. For a genuinely new operation whose commit is
known not to have occurred, conflict precedence is frozen as
`EVIDENCE_ALREADY_CONSUMED`, then `AUTHORITY_ALREADY_CLAIMED`, then
`RECORD_ID_COLLISION`. Vendor error order, constraint names, insert order, query
plans, and race order may not decide it. Unknown commit status remains
`AMBIGUOUS` and cannot be downgraded by this precedence.

## Migration and test boundary

Alembic currently supports secret-free offline SQL only. Online mode fails with a
fixed inactive error and does not create an Engine or connection. A migration
must never run in a request, import, build, cold start, or ordinary Vercel
deployment. Later activation requires a protected, serialized migration runner
using only the validated direct URL, its own migration role, review/approval,
locking, audit evidence, and post-run head verification. Runtime must later use
only the pooled URL and a separate least-privilege runtime role.

Current evidence is offline and static only. No socket or database call is part
of these tests. Before any adapter or route activation, live PostgreSQL tests must
prove all constraints, collations, deferred cycles, triggers, transaction
rollback, concurrent conflicts, exact replay, CAS, sequence behavior, unknown
commit reconciliation, migration execution, and application compatibility.

Sessions and login remain separate later phases. Session lookup-key and
binding-key epochs and the closed session-error taxonomy remain explicit
activation blockers. Product entitlements remain a separate server-side
authority. Collaboration remains inactive.
