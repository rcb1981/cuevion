# Mailbox PostgreSQL runtime activation requirements

## Status: shadow wiring only

The durable mailbox schema and PostgreSQL adapter exist, but mailbox runtime
activation is not authorized by this slice.

`cuevion_mailbox.runtime` lives outside `api/` and exposes no route. Its only
supported modes are:

- `disabled` — the default when the mode variable is absent;
- `shadow` — explicit construction for controlled repository validation.

There is intentionally no `active` mode.

## Configuration boundary

Shadow mode requires all of the following caller-supplied values:

- `CUEVION_MAILBOX_POSTGRES_MODE=shadow`
- `VERCEL_ENV=production|preview`
- `CUEVION_MAILBOX_READER_DATABASE_URL`
- `CUEVION_MAILBOX_WRITER_DATABASE_URL`

The two URLs must:

- use `postgresql://`;
- contain non-empty credentials;
- require `sslmode=require` and `channel_binding=require`;
- use a pooled Neon endpoint;
- target the same endpoint and database;
- bind to the exact environment-specific mailbox reader/writer role names.

Production role names are:

- `cuevion_production_mailbox_reader_v1`
- `cuevion_production_mailbox_writer_v1`

Preview must use separately provisioned Preview roles and credentials. Production
credentials must never be copied into Preview.

## Runtime connection boundary

A shadow connection must prove:

- `autocommit=False`;
- TLS is in use;
- server-reported database user exactly matches the parsed dedicated role;
- server-reported database name exactly matches the parsed URL.

Reader connections additionally enter a read-only transaction before repository
queries run.

Database URLs are parser-controlled redacted objects and must not be logged,
rendered, serialized, pickled, returned from APIs, or added to exception text.

## Activation blockers

Before any existing Gmail or Custom IMAP route can read from or write to this
repository, all of the following require a separate reviewed change:

1. create dedicated Preview mailbox reader/writer roles with the same proven
   least-privilege matrix as Production;
2. provision independent strong credentials for Preview and Production through
   a secret-aware path;
3. install encrypted environment variables in the matching Vercel environments;
4. prove pooled connections using the real roles, including TLS, current user,
   database binding and read-only reader behavior;
5. prove transaction rollback, CAS conflict handling, source-generation
   isolation and outbox lease/reclaim behavior through the actual adapter;
6. add a separately reviewed `active` state;
7. cut over one bounded read path first, with immediate fail-closed fallback or
   rollback;
8. only then move provider delta writes;
9. only after provider writes are stable, activate outbox-to-Priority
   consumption;
10. remove browser/local snapshot authority only after the server cache is proven
    complete enough for the intended UX.

No route may interpret the mere presence of mailbox database environment
variables as activation. Activation must require the explicit reviewed mode.

## Current provider behavior

Until those gates are completed, Gmail and Custom IMAP continue using their
existing production paths. The durable mailbox repository may be instantiated
only in explicit shadow validation and must not alter provider cursors, inbox UI
authority, Priority behavior, or user-visible mailbox state.
