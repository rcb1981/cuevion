# Mailbox PostgreSQL runtime activation requirements

## Status: Preview active-read proven; Preview active-write runtime only

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

No existing Gmail or Custom IMAP route calls the active writer.

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

## Active-write validation gate

`active_write` only makes the already restricted Preview writer repository
constructible. It does not authorize a provider route write by itself.

Before any existing Gmail route may write a provider delta:

1. deploy the exact reviewed Preview head;
2. explicitly set Preview mode to `active_write`;
3. prove the real restricted Preview writer connection over pooled TLS;
4. against synthetic Preview-only mailbox state, commit one bounded
   `ProviderDeltaCommit` transaction;
5. read the committed cursor/message back through the restricted Preview reader;
6. verify the expected outbox row exists without consuming it;
7. clean up the synthetic Preview data and any temporary proof endpoint;
8. keep Production mode unable to parse `active_write`;
9. add Gmail route writes only in a separate reviewed change.

A bounded Gmail snapshot must never infer deletions solely from absence. Durable
body state must not be downgraded by a metadata refresh. Cursor and message
writes remain CAS-protected and source-generation scoped.

## Remaining activation sequence

After the synthetic active-write proof succeeds:

1. add a separately reviewed Preview-only Gmail provider-write hook;
2. keep the existing provider response authoritative while durable writes are
   observed;
3. prove repeated refreshes are idempotent and conflicts fail closed;
4. introduce provider delta/history-based synchronization;
5. only after provider writes are stable, activate outbox-to-Priority
   consumption;
6. only after the server cache is sufficiently complete, promote cache-first
   server reads to user-visible authority;
7. activate Production through a separate explicit gate.

No route may interpret the mere presence of mailbox database environment
variables as activation. Activation always requires the explicit reviewed mode.
