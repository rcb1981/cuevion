# C3B1C historical discovery migration

This module implements the explicitly approved, manual-only historical SCAN
exception. Importing it performs no I/O. No route, application entry point,
startup hook, mailbox/focus handler, timer, or summary request imports or calls
it. Normal Collaboration behavior and frontend contracts are unchanged.

## Enumeration and scope

Before C3B1B, creation persisted a canonical thread and a source pointer but no
enumerable ID registry. Source pointers are exact HMAC lookups requiring a known
owner, mailbox and source. Guest indexes require a known Collaboration ID; Team
indexes enumerate people; C3B1B discovery hashes cover enrolled IDs only.

The manual path uses exactly:

```text
SCAN <cursor> MATCH cuevion:collab:v2:{cuevion-collab-v2}:thread:* COUNT 100
```

Production cardinality is unknown; no Production access was made. The 1,000-ID
discovery limit does not bound unindexed historical population. SCAN can inspect
the wider database/slot even though MATCH limits returned names. COUNT is a work
hint, not a maximum result count. Empty batches and duplicate keys are expected.
A completed pass covers keys continuously present throughout that pass. It is
not a snapshot; changed entitlement, skipped invalid records, and repaired source
bindings require another explicitly invoked pass. New writes continue to use
C3B1B prospective enrollment. See https://redis.io/docs/latest/commands/scan/.

The operation is a trusted internal function, not an owner-accessible enumeration
endpoint. Its Redis transport and private checkpoint are operator resources.
Only aggregate counts and an opaque continuation token leave the function.
Cross-workspace/unauthorized thread metadata is omitted by the Redis probe.

## Manual invocation contract

`api.collaboration.discovery_migration.run_page` requires:

- `enabled=True` on every invocation; default is disabled.
- A genuine verified owner request context, current authenticated request headers,
  and the existing owner security configuration. Context/headers are revalidated
  on every page and even on a cached response replay. There is no service-owner
  impersonation, email-to-ID inference, or guest entry point.
- An absolute `checkpoint_path` in an existing private directory owned by the
  operator (mode 0700). Checkpoint and lock files must be private regular files
  owned by the operator (mode 0600); symlink files are rejected.
- `dry_run=True` by default. An apply run requires `dry_run=False` and a separate
  checkpoint. Mode cannot be switched on an existing checkpoint.
- Optional `owner_mailbox_id`: verifies ownership of exactly that managed mailbox
  using the existing C3B1A compatibility contract, once per page. That pass can
  enroll its owner threads plus the viewer's entitled participant threads.
  `None` runs participant-only enrollment. To cover multiple owned mailboxes,
  explicitly run a separate checkpoint for each mailbox. These passes repeat
  enumeration, so include their cost in the operational quota budget.
- `cursor=None` when creating a new run; subsequently pass the returned
  `nextCursor`. Retry the same cursor after a lost response. Use the newly returned
  cursor to retry a reported capacity/CAS blockage.
- `scan_budget=1000` by default, bounded to 1..100000 and fixed for the run. This
  counts attempts, including failed/interrupted attempts, before issuing SCAN.
  Budget exhaustion is an explicit incomplete result. A larger-budget new pass
  is an operator decision; no automatic restart or budget escalation exists.

The default transport is the existing configured Collaboration command transport;
it is used only after explicit invocation and authorization. Local tests inject
an isolated temporary Unix-socket Redis transport and fake current-account and
mailbox/Team authorities. No environment file or production setting is changed.

## Checkpoint and completion semantics

A call executes at most one SCAN and examines at most five pending keys. A SCAN
reply may contain up to 1,000 keys, each at most 256 printable ASCII bytes with the
exact namespace prefix. Every returned key is validated, batch duplicates are
collapsed, and the complete batch is checkpointed before enrollment. Excess
pending keys are retained for later calls. Responses exceeding those limits, or
the existing 512 KiB transport limit, fail without advancing the SCAN cursor.
This bounds application memory/work; it cannot turn Redis COUNT into a hard
server-work or response-size guarantee. There is no internal scan-draining loop.

The checkpoint binds user ID, workspace, email, owned mailbox/provider, mode and
scan budget. It stores the exact Redis cursor, pending keys, dry-run reservations,
and the latest acknowledged result/token. Tokens contain no IDs or content.
Strict parsing rejects duplicate JSON keys, extra fields, malformed counts,
out-of-scope keys, and inconsistent completion state. Checkpoints are local
operational state, never an authorization source.

An exclusive nonblocking file lock prevents concurrent advancement of the same
checkpoint. Updates use a private temporary file, fsync, atomic replacement and
directory fsync. Before enrollment, pending keys are durable. A crash after Redis
commit but before checkpoint completion replays those keys; canonical/index
validation recognizes completed enrollment and does not rewrite it. The returned
counts describe that attempt, so an ambiguous earlier commit may subsequently
appear as `alreadyPresent`. There is no claim of cross-system transactional totals.

`done` means enumeration finished and no capacity/CAS candidates remain pending.
It does not mean that invalid, unauthorized or unresolved-identity records were
enrolled. Such counts require review. Completed checkpoints are sealed; replay
of the last request returns its saved result without Redis discovery work.
Rerunning or applying after dry-run requires a new explicitly named checkpoint.

Progress fields are `examined`, `enrolled`, `wouldEnroll`, `alreadyPresent`,
`alreadyPlanned`, `skippedInvalid`, `skippedUnauthorized`, `unresolvedIdentity`,
`staleEntitlement`, `missing`, `capacity`, `retry`, `deferred`, `scanCalls`, `nextCursor`,
`done`, `dryRun`, `status`, and a fixed safe error code. There are no message,
source-body, guest, invite, session, bearer, or raw-key fields in the result.

## Canonical authority and enrollment

The first Lua call batches bounded canonical reads and projects only routing
metadata plus an exact-byte hash to the trusted Python wrapper. The wrapper
derives current/previous HMAC source-pointer keys. The second Lua call rereads
canonical records in a batch and checks the hash, full strict wire/schema,
workspace, Collaboration ID, exact mailbox/source, positive bounded expiry,
owner/participant authority, and source pointers before preparing any write.
TYPE/STRLEN checks precede MGET so oversized and wrong-type records are not
loaded into an unbounded content batch. At most five 262,144-byte records are
read per phase. Message bodies never leave Redis.

Strict content validation has a second, aggregate bound: at most 262,144 bytes
of original canonical input per invocation. Candidates exceeding the remaining
byte budget are `deferred` and retained for the next explicit call; this is normal
progress, not an error. One maximum-sized valid candidate always fits an empty
step. Five maximum-sized records are therefore validated across five calls.
This bound was added after the five-record-only limit exceeded the local Redis
script time limit; increasing the client timeout would not solve server blocking.

Legacy owner authority is added only with the genuine existing owned-mailbox
capability matching the current canonical account, email, workspace and provider.
Missing proof produces `unresolvedIdentity`; source visibility cannot create
ownership. Partial owner groups, email-as-workspace records, guessed IMAP
locators and malformed participant arrays remain ineligible.

Participant enrollment requires this explicit canonical user and the current
Team invitation reference. One current Team snapshot is taken per page, using
the existing at-most-two-GET resolver. Removed, stale, rebound and unrelated
members receive no enrollment. A snapshot is not a cross-store transaction;
normal summaries continue to revalidate current Team membership on every read.

The shared C3B1B discovery preparation helper accepts an optional verified
single-recipient subset and a per-EVAL capacity cache. Existing callers omit
both arguments and retain their original behavior. Migration uses exactly its
binding format, SHA1 byte-integrity digest, validation, capacity and TTL rules;
it publishes only the verified viewer, never other unverified participants.

All successful candidate plans are prepared before writes. Capacity reservations
within the page prevent exceeding 1,000 entries. Available candidates may commit
while capacity/CAS-blocked candidates stay pending; an individual thread's
enrollment remains atomic. Live or corrupt-but-existing canonical references are
never evicted. Only proven absent canonical keys can be pruned. A full-index
absence check is shared across the page, with at most 1,000 EXISTS checks.

Dry-run executes the same validation/preparation but skips all commits and canonical
writes. It also reserves up to 1,000 predicted IDs in the private checkpoint so
later pages cannot each reuse the same free capacity. Duplicate predictions are
`alreadyPlanned`. Reservations are conservative if earlier candidates expire or
external state changes during the run; a fresh dry-run recomputes the plan.
Dry-run is advisory and is never accepted as apply-time authorization.

Unchanged canonical index entries do not receive HSET or TTL refresh. If an
otherwise matching index has an insufficient TTL, preparation repairs that TTL
and reports enrollment. New/changed entries receive the same binding/digest as
C3B1B. Existing canonical records remain byte-for-byte unchanged. A legacy owner
group is appended only after strict validation, preserving every existing field,
array and source value; SET KEEPTTL retains the exact canonical expiry. Source
pointers are never rotated, repaired, deleted or refreshed here. Resolved state,
all other lifecycle states and both timestamps are preserved.

The shared index keeps the longest necessary remaining TTL across the candidate
plans, including when pruning removes the old hash completely. It may outlive an
individual shorter-lived thread, but summary reads always require live canonical
authority. No PERSIST or fresh 180-day canonical retention is introduced.

## Command budget per page

Let N <= 5 be probe candidates and R <= N be candidates requiring strict
enrollment revalidation. Authentication/account and one owned-mailbox verification
are fixed per page; they use existing adapters, not per-candidate calls. Team
authority adds at most two exact GETs per page.

| Work | Actual command shape |
| --- | --- |
| Enumeration | Zero or one SCAN; attempts are checkpointed against the explicit run budget. No Redis checkpoint writes. |
| Probe | One EVAL when N > 0; for healthy records, N TYPE + N STRLEN + one MGET + N PTTL: 3N+1 nested reads. |
| Enrollment preflight | One EVAL when R > 0; index TYPE, optional HLEN/HKEYS; a second bounded MGET with TYPE/STRLEN/PTTL guards. |
| Source authority | Per candidate: TYPE per distinct current/previous pointer; each present string also receives STRLEN, GET and PTTL. One or two pointer keys, no per-candidate HTTP. |
| Discovery preparation | Per eligible candidate: canonical PTTL, then TYPE, optional HLEN, PTTL and HGET on the one viewer index. |
| Healthy combined reads | With an existing index and one current pointer per candidate: probe 3N+1 plus enrollment 4+12R nested reads. For N=R=5 this is 80 nested reads, plus the two EVAL commands and at most one SCAN. |
| Initially absent index | Enrollment has 2+11R nested reads under the same healthy assumptions. |
| Capacity boundary | At most one additional HKEYS plus at most 1,000 EXISTS for the full index, shared by all candidates in the page. |
| Apply writes | At most R HSET, at most R necessary PEXPIRE, one HDEL for proven missing references, and at most R SET KEEPTTL for verified legacy owner enrichment. |
| Unchanged enrollment | Zero Redis writes; verified by Redis command counters as well as serialized-value and absolute-expiry snapshots. |
| Dry-run | Zero Redis writes, including no pruning, source-pointer migration or TTL refresh. Local private checkpoint writes still occur. |

Lua SHA1/schema parsing is CPU work, not a Redis command, and must still fit the
bounded invocation. No statement here assumes nested Lua commands are free for
provider billing. Whole-run costs depend on database size, retries and number of
explicit user/mailbox passes; they cannot be inferred from unknown Production
cardinality. No Production quota/latency measurement has been performed.

## Verification and retirement

Focused local tests cover historical owner/guest-only compatibility, participant
entitlement, all states/sources, corruption, source HMAC rotation, wrong scopes,
dry-run capacity across pages, no-write command counters, expiry preservation,
SCAN overshoot/empty/duplicate batches, explicit quota exhaustion, concurrent
runs, checkpoint locks/permissions, and interruptions before/after commit.
An isolated subprocess confirms the normal owner entry point does not import the
migration; an authenticated HTTP request cannot select a migration operation.

The full bounded large-record batch is exercised against isolated Redis. Existing
C3B1A lifecycle, C3B1B summary, authorization, and relevant guest regressions are
run separately. Python bytecode writing is disabled. Protected-file-reading tests,
unrelated large timing suites, dependency installs and frontend builds are excluded.

Final combined local gate: **358 tests passed in 48.302 seconds**, including 36
migration tests, the complete summary/lifecycle/authorization suites, scoped
model/storage/application/HTTP/guest/idempotency/source suites, and eight selected
Redis integration cases covering C2G5 replacement, guest exchange/session/logout,
owner idempotency and participant concurrency. The five-large-record migration
test resumed over five explicit steps; the slowest step was 1.088 seconds and the
largest Redis response was 1,634 bytes. The existing 50-summary large-page test
returned 13,728 bytes in 0.142 seconds. These are local observations, not Production
latency claims. No new migration bytecode files were generated.

After separately authorized operational migration and review of skipped/blocked
counts, stop invoking this module. Its default stays disabled, completed run files
remain sealed, and no scheduled/runtime caller exists. Remove the manual entry
point in a later reviewed cleanup; the shared C3B1B runtime continues normally.
This change grants no Production, push, deploy or environment-change permission.
