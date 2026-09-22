# Mailbox PostgreSQL runtime activation requirements

## Status: Preview active-read proven; bounded Preview Gmail write hook

The durable mailbox schema, PostgreSQL adapter, Gmail durable projection, and
pure Gmail delta planner exist.

`cuevion_mailbox.runtime` lives outside `api/`. Its supported modes are:

- `disabled` — the default when the mode variable is absent;
- `shadow` — explicit reader/writer construction for controlled validation;
- `active_read` — Preview-only reader construction;
- `active_write` — Preview-only reader/writer construction.

Both active modes are rejected when `VERCEL_ENV=production`.

The existing Gmail fetch route has a bounded Preview-only `active_read` hook.
That hook proves durable scope/message reads but does not make the durable cache
the user-visible response authority. The existing Gmail provider fetch remains
authoritative.

When `active_write` is explicitly enabled in Preview, the Gmail Inbox fetch
route performs one bounded durable metadata write after a successful provider
snapshot. It first binds an account-level Gmail `historyId` to the authenticated
mailbox identity using `/profile`, then bootstraps/reads current durable state,
projects the accepted Inbox snapshot, plans a CAS-protected
`ProviderDeltaCommit`, and writes it through the restricted Preview writer.

This write remains observational: any durable write/history failure emits only a
fixed Preview diagnostic and does not replace or mutate the existing Gmail API
response. Custom IMAP has no active writer hook. Production cannot parse
`active_write`.

## Configuration boundary

Shadow mode requires:

- `CUEVION_MAILBOX_POSTGRES_MODE=shadow`
- `VERCEL_ENV=production|preview`
- `CUEVION_MAILBOX_READER_DATABASE_URL`
- `CUEVION_MAILBOX_WRITER_DATABASE_URL`

Preview active-read requires:

- `CUEVION_MAILBOX_POSTGRES_MODE=active_read`
- `VERCEL_ENV=preview`
- `CUEVION_MAILBOX_READER_DATABASE_URL`

Preview active-write requires:

- `CUEVION_MAILBOX_POSTGRES_MODE=active_write`
- `VERCEL_ENV=preview`
- `CUEVION_MAILBOX_READER_DATABASE_URL`
- `CUEVION_MAILBOX_WRITER_DATABASE_URL`

Database URLs must:

- use `postgresql://`;
- contain non-empty credentials;
- require `sslmode=require` and `channel_binding=require`;
- use a pooled Neon endpoint;
- bind to the exact environment-specific mailbox role;
- for reader/writer pairs, target the same endpoint and database.

Production role names are:

- `cuevion_production_mailbox_reader_v1`
- `cuevion_production_mailbox_writer_v1`

Preview role names are:

- `cuevion_preview_mailbox_reader_v1`
- `cuevion_preview_mailbox_writer_v1`

Preview and Production credentials remain separate. Production credentials must
never be copied into Preview.

## Runtime connection boundary

Every runtime connection must prove:

- `autocommit=False`;
- TLS is in use;
- server-reported database user exactly matches the parsed dedicated role;
- server-reported database name exactly matches the parsed URL.

Reader connections additionally enter a read-only transaction before repository
queries run.

Database URLs are parser-controlled redacted objects and must not be logged,
rendered, serialized, pickled, returned from APIs, or added to exception text.

## Bounded Gmail write boundary

The Preview Gmail write hook is allowed only after the provider Inbox snapshot
has succeeded. It must:

1. request Gmail `/profile` through the existing one-refresh auth boundary;
2. require profile `emailAddress` to match the authenticated mailbox identity;
3. require a canonical numeric account-level `historyId`;
4. initialize generation 1 only when no safe current durable state exists;
5. reuse exact current state/cursor/message projections when already present;
6. project at most 100 accepted Inbox messages;
7. never infer tombstones from absence in the bounded snapshot;
8. preserve existing durable body state during metadata refreshes;
9. commit messages, cursor, state advance and outbox rows transactionally;
10. short-circuit an exact repeat with unchanged history and metadata;
11. never consume the outbox in this route;
12. never change the Gmail response authority or response payload.

Durable failures are intentionally observational in this first route hook:
Preview logs a fixed non-sensitive marker and continues returning the successful
provider snapshot.

## Remaining activation sequence

After the bounded Preview Gmail write hook is proven:

1. exercise the helper against the fixed Preview Neon branch with synthetic
   state and verify create, repeat/no-op, cursor advance and CAS conflict paths;
2. keep Preview on `active_read` except during explicit write validation;
3. introduce Gmail History delta synchronization so removals/label changes do
   not depend on full bounded snapshots;
4. only after provider writes are stable, activate outbox-to-Priority
   consumption;
5. only after the server cache is sufficiently complete, promote cache-first
   server reads to user-visible authority;
6. activate Production through a separate explicit gate.

No route may interpret the mere presence of mailbox database environment
variables as activation. Activation always requires the explicit reviewed mode.
