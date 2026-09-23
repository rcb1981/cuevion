# Mailbox PostgreSQL runtime activation requirements

## Status: Preview active-read proven; stale Gmail History recovery foundation

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
route first captures an account-level Gmail `historyId` baseline from
`/profile` and binds it to the authenticated mailbox identity. It then reads the
provider Inbox snapshot. Only after that snapshot succeeds does the route
bootstrap/read current durable state, project the accepted Inbox snapshot, plan
a CAS-protected `ProviderDeltaCommit`, and write it through the restricted
Preview writer. Capturing the history baseline first prevents changes occurring
during the snapshot from being skipped by a later History delta.

This write remains observational: any durable write/history failure emits only a
fixed Preview diagnostic and does not replace or mutate the existing Gmail API
response. Custom IMAP has no active writer hook. Production cannot parse
`active_write`.

A bounded Gmail History delta reader exists for the next activation stage. It
starts from an already-persisted account-level `historyId`, follows bounded
History pagination, deduplicates affected provider message IDs, returns a
candidate next cursor only after the final page, and maps stale History 404 to
`full_sync_required`. Provider failures, invalid payloads, repeated page
tokens, and configured bounds fail closed without a next cursor.

The History mutation foundation adds an exact durable lookup by Gmail provider
message ID, including already-tombstoned rows, so History planning never depends
on only the newest visible cache window. A pure mutation planner combines exact
recovered records with provider-verified Inbox absences into one CAS-protected
delta. Recovered records become bounded upserts; only explicitly
provider-verified absences may become tombstones. Unknown absences and rows
already tombstoned are no-ops.

When Preview `active_write` is enabled, the Gmail Inbox route now tries this
History path before reading the user-visible Inbox snapshot. If current durable
state and a Gmail cursor exist, it reads one complete bounded History window,
recovers every affected provider message exactly, resolves the exact durable
rows for those provider IDs, and performs at most one CAS-protected commit.
Retryable exact-recovery failures, stale History, invalid/overflowed History,
or CAS conflicts do not advance the cursor. The latest provider context is then
used for the normal Inbox snapshot, which remains the response authority.

If no durable state or cursor exists, History returns `bootstrap_required`.
Only that path retains the existing `/profile` baseline-before-snapshot
bootstrap write. A stale existing History cursor never falls back to a bounded
snapshot write, because a bounded snapshot cannot prove deletions outside its
window.

A stale-History recovery foundation now exists but is not route-wired. Its
recovery bound is intentionally strict:

- Gmail Inbox membership is considered complete only when one
  `labelIds=INBOX&maxResults=100` page has no `nextPageToken`;
- the durable active-row inventory is complete only when at most 100 current
  non-tombstoned rows exist; the repository reads `limit + 1` to detect
  overflow rather than returning a partial set;
- every provider Inventory ID must be accounted for by either one exactly
  recovered Inbox record or one exact terminal-absence result;
- the planner reconciles those recovered records against exact durable rows,
  including prior tombstones, and tombstones every previously active durable
  row not present in the recovered record set;
- provider Inventory, active durable Inventory, exact recovery and cursor
  transition must all be complete before one CAS-protected commit may install a
  fresh Gmail history cursor;
- Inbox overflow, durable-row overflow, malformed inventory, incomplete exact
  recovery or cursor/CAS failure leaves the stale cursor unchanged.

The intended route ordering for a later activation slice is fresh
identity-bound `/profile` history baseline first, then complete Inbox inventory
and exact recovery, then one atomic reconciliation + cursor-reset commit. Taking
the fresh history baseline before inventory means any Gmail changes occurring
during recovery remain discoverable from the newly installed cursor.

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

The Preview Gmail write hook must:

1. request Gmail `/profile` through the existing one-refresh auth boundary
   before starting the provider Inbox snapshot;
2. require profile `emailAddress` to match the authenticated mailbox identity;
3. require a canonical numeric account-level `historyId`;
4. write nothing unless the subsequent provider Inbox snapshot succeeds;
5. initialize generation 1 only when no safe current durable state exists;
6. reuse exact current state/cursor/message projections when already present;
7. project at most 100 accepted Inbox messages;
8. never infer tombstones from absence in the bounded snapshot;
9. preserve existing durable body state during metadata refreshes;
10. commit messages, cursor, state advance and outbox rows transactionally;
11. short-circuit an exact repeat with unchanged history and metadata;
12. never consume the outbox in this route;
13. never change the Gmail response authority or response payload.

Durable failures are intentionally observational in this first route hook:
Preview logs a fixed non-sensitive marker and continues returning the successful
provider snapshot.

## Remaining activation sequence

After the Preview Gmail History route integration is proven:

1. keep Preview on `active_read` except during explicit write validation;
2. validate bootstrap, cursor-only advance, recovered upsert, provider-verified
   tombstone, retry/no-advance, stale-History/no-advance and CAS conflict paths
   against the fixed Preview Neon branch;
3. wire the bounded stale-History recovery foundation into Preview
   `active_write`, capturing a fresh identity-bound history baseline before
   complete Inventory + exact recovery and refusing cursor reset on any
   overflow or incomplete recovery;
4. prove stale-cursor reconciliation, resurrection, deletions, overflow and
   no-advance failure paths against the fixed Preview Neon branch;
5. only after provider writes are stable, activate outbox-to-Priority
   consumption;
6. only after the server cache is sufficiently complete, promote cache-first
   server reads to user-visible authority;
7. activate Production through a separate explicit gate.

No route may interpret the mere presence of mailbox database environment
variables as activation. Activation always requires the explicit reviewed mode.
