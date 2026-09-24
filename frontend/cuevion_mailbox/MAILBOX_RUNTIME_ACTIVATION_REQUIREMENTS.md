# Mailbox PostgreSQL runtime activation requirements

## Status: dormant Production Gmail cache-authority gate foundation

The durable mailbox schema, PostgreSQL adapter, Gmail durable projection, and
pure Gmail delta planner exist.

`cuevion_mailbox.runtime` lives outside `api/`. Its supported modes are:

- `disabled` — the default when the mode variable is absent;
- `shadow` — explicit reader/writer construction for controlled validation;
- `active_read` — Preview-only reader construction;
- `active_write` — Preview-only reader/writer construction;
- `production_read` — Production-only reader construction, additionally gated
  by an exact Production authority flag.

`active_read` and `active_write` remain rejected when
`VERCEL_ENV=production`. `production_read` is rejected outside Production.

The Gmail fetch route now has a freshness-proven Preview-only `active_read`
cache-authority path. Durable Inbox membership/order may become user-visible
authority only after prior complete reconciliation has promoted durable state
to `ready` and the Gmail cursor backfill state to `complete`. An
identity-bound Gmail `/profile` historyId must also exactly match the
persisted durable cursor.

The route then fetches only those exact durable provider message IDs for
render/body metadata, captures `/profile` again, and publishes the durable
membership only if the historyId is still unchanged. Missing readiness, cursor
mismatch, provider history change, exact-detail failure, or revalidation
failure falls back to the existing Gmail list snapshot. Durable repository
corruption/unavailability fails closed with the fixed
`mailbox_read_unavailable` response.

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

When Preview `active_write` is enabled and the bounded History reader returns
`full_sync_required`, the Gmail route now invokes the stale-History recovery
path before the normal user-visible Inbox snapshot. The route first captures a
fresh identity-bound `/profile` history baseline. Only after that succeeds may
the recovery orchestrator read the complete bounded Inbox membership, verify
the complete active durable inventory, exactly recover each provider message,
resolve exact durable projections for recovered IDs, and build one atomic
reconciliation + cursor-reset commit.

The fresh history baseline is captured before provider inventory/recovery so
changes occurring during the recovery window remain discoverable from the newly
installed cursor. Provider overflow, durable-row overflow, retryable exact
recovery, invalid inventory, missing/non-ready durable state, missing cursor,
cursor rewind, or CAS conflict never replace the stale cursor. Those failures
remain observational and the normal provider Inbox snapshot still remains the
user-visible response authority.

Bootstrap remains separate: only `bootstrap_required` may enter the existing
profile-before-snapshot bootstrap write. A stale existing cursor can no longer
fall through to the bounded bootstrap snapshot write.

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

Production read-authority requires all of the following:

- `CUEVION_MAILBOX_POSTGRES_MODE=production_read`
- `CUEVION_MAILBOX_PRODUCTION_READ_AUTHORITY=enabled`
- `VERCEL_ENV=production`
- `CUEVION_MAILBOX_READER_DATABASE_URL`

The Production route gate remains false unless both exact switches are present.
Values such as `true`, `1`, `ENABLED`, Preview `active_read`, or the
mere presence of the Production reader database URL do not activate Production
cache authority. Production reader composition never accepts or requires the
writer URL.

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

## Production cache-authority activation boundary

Production uses the same proven Gmail authority invariants as Preview but has a
separate activation boundary. The route may enter the durable cache-authority
planner in Production only when:

1. `VERCEL_ENV=production`;
2. `CUEVION_MAILBOX_POSTGRES_MODE=production_read`;
3. `CUEVION_MAILBOX_PRODUCTION_READ_AUTHORITY=enabled`;
4. the configured database credential is the exact dedicated Production
   reader role;
5. the runtime connection proves TLS, exact database identity, exact role
   identity, and enters a read-only transaction.

This gate introduces no Production writer construction and does not relax the
Preview-only `active_read` or `active_write` modes. If the gate is disabled,
the Gmail route remains provider-authoritative exactly as before.

When enabled in a later explicit rollout, Production still requires the same
`ready + complete` durable state, matching Gmail history before rendering,
strict exact-detail recovery, and matching Gmail history after rendering.
Repository uncertainty fails closed; freshness or readiness mismatch uses the
existing provider list path.

## Preview cache-authoritative Gmail read boundary

The Preview `active_read` route may use durable Gmail Inbox membership as
user-visible list authority only when all of the following hold:

1. authenticated Gmail `/profile` succeeds and returns a canonical numeric
   historyId bound to the authenticated mailbox identity;
2. exact durable current state is `ready`;
3. the persisted `gmail-account` cursor is Google, has
   `backfill_state=complete`, has no backfill cursor, and its historyId exactly
   equals the first provider historyId;
4. every durable row selected for the bounded response is active, Inbox-scoped,
   and has a canonical Gmail provider message ID;
5. Gmail exact-detail reads succeed for every durable provider message ID in
   durable order with strict Inbox membership validation;
6. a second identity-bound Gmail `/profile` request succeeds after those
   detail reads and returns the exact same historyId.

`recent_ready`, `backfilling`, incomplete cursors, and mere PostgreSQL row
presence are never sufficient authority. The cache path never infers freshness
from timestamps. Any provider history mismatch before or after exact detail
rendering returns to the provider list path.

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

## Outbox to Priority Preview consumer

The deterministic outbox-to-Priority foundation is now wrapped by a bounded
Preview-only consumer.

The consumer:

- activates only when the mailbox runtime is exactly Preview
  `active_write`; Production still rejects this mode at runtime composition;
- claims due rows only for the authenticated workspace/user/mailbox, across
  source generations, using `FOR UPDATE SKIP LOCKED` and a bounded lease;
- resolves each claim back to exact durable message state before any Priority
  side effect;
- treats stale generations, superseded events and current ineligible rows as
  terminal no-op outcomes;
- applies current `message_added` / `message_changed` rows through the
  canonical Priority candidate population path, including workflow-reference
  reconciliation;
- removes only the Priority candidate transport row for an exact current
  `message_deleted` event; workflow authority remains independent;
- marks an outbox event processed only after the downstream action succeeds;
- schedules a bounded retry with a fixed content-free error code when durable
  resolution, planning or Priority storage is uncertain;
- does not issue a retry after an uncertain processed acknowledgement or a lost
  claim, so the current lease remains the sole write authority.

The Gmail Inbox route invokes one bounded consumer drain only inside its
existing Preview `active_write` gate. Provider Inbox snapshot data remains the
user-visible authority and consumer failure is observational to the response.

## Cache authority readiness

A recent durable window is not sufficient authority for user-visible cache
reads. Gmail cache authority is eligible only after a complete bounded Inbox
reconciliation has proven the entire provider Inbox fits the recovery bound and
every current provider message has been reconciled.

A successful complete stale-recovery reconciliation now:

- advances the Gmail cursor to the fresh provider History id;
- marks the cursor backfill state `complete` with no remaining backfill cursor;
- promotes mailbox bootstrap state from `recent_ready`/backfilling to `ready`;
- performs that readiness transition transactionally with the same message,
  cursor and state CAS commit;
- still refuses promotion on provider overflow, durable overflow, incomplete
  exact recovery, cursor/state uncertainty or writer conflict.

Preview `active_read` now treats only `ready` + Gmail cursor
`backfill_state=complete` as cache-authoritative. `recent_ready` remains a
cache miss for future user-visible authority. The current Gmail route still
returns the provider snapshot in this slice; this is a readiness gate only.

## Remaining activation sequence

1. merge this dormant Production gate with no Production environment changes;
2. prove the Production reader role can connect read-only without enabling
   user-visible cache authority;
3. prove the exact Production double gate remains false for every partial or
   malformed configuration;
4. perform a Production provider-vs-cache equivalence diagnostic without
   changing the route response authority;
5. only after equivalence is proven, explicitly enable
   `production_read + enabled` in a separately reviewed rollout;
6. keep Production writer activation out of scope.

No route may interpret the mere presence of mailbox database environment
variables as activation. Activation always requires the explicit reviewed mode.
