# C3B1E temporary runtime dry-run sample

The existing `/api/collaboration/owner` POST dispatcher accepts one temporary
operation when `CUEVION_COLLAB_DISCOVERY_MIGRATION_OPERATOR_MODE` is exactly
`dry_run_grant`. Absent, `off`, and unknown values deny it. This reuses the C3B1D
mode; grant issuance and verification are unchanged. The operation itself does
not require, issue, synthesize, or export a grant.

```json
{
  "operation": "run_discovery_migration_dry_run_page",
  "ownerMailboxId": "<exact allowlisted owned mailbox id>"
}
```

These are the only accepted fields. Duplicate fields, execution options,
identity fields, cursors, budgets, and checkpoint paths are rejected. No route,
Vercel function, frontend integration, polling, or scheduled work is added.

## Authority and request protection

The existing owner boundary requires POST, the exact trusted Origin, a current
owner session, owner allowlist membership, and session-bound CSRF. Both existing
`owner_read` and `owner_write` modes permit this read-only operation. An external
guest cookie grants no owner authority.

The mailbox must be allowlisted and resolve through the current managed inbox
authority to the canonical account's exact owned `google` or `custom_imap`
mailbox. Canonical `usr_*` identity, workspace, verified email, and display name
come from current account authority. The engine rechecks those authorities and
current Team entitlement before SCAN. Owners need no Team enrollment; participant
candidates require their exact current membership reference. Stale or rebound
participant membership does not qualify. Unavailable authorities fail closed.

A dedicated existing GCRA rate-limit policy allows a burst of two, replenishing
one request per 300 seconds. Its purpose-separated HMAC key binds the canonical
user and workspace within the existing owner counter family. Existing policies
and their key bytes are unchanged. The public 429 response retains the existing
`Retry-After` range of 1–60 seconds; that header does not promise admission after
that delay. The operation never retries automatically. Security counter writes
are separate from migration work.

## Fixed execution and output

The authenticated, authorized, rate-limited branch lazily imports the engine and
calls `run_runtime_dry_run_page` once. The runtime adapter has no apply, mode,
cursor, budget, or checkpoint parameter. It hardcodes dry-run, starts at SCAN
cursor `0`, and permits one SCAN attempt with the existing COUNT of 100, at most
five candidates, and at most two bounded EVALs. Existing canonical byte bounds
remain 262,144 aggregate bytes. An empty nonterminal SCAN is returned immediately.

Both manual C3B1C execution and runtime execution use `_run_one_page` and the same
unchanged validation/Lua. The manual adapter keeps its existing checkpoint,
locking, replay, budget, and explicit apply behavior. The runtime adapter uses
only transient memory. It performs no checkpoint or filesystem access and has
no continuation token or page-two path.

Successful responses use the existing `ok`/`data` envelope. The exact version-one
data fields are `v`, `dryRun`, `examined`, `wouldEnroll`, `alreadyPresent`,
`alreadyPlanned`, `skippedInvalid`, `skippedUnauthorized`, `unresolvedIdentity`,
`staleEntitlement`, `missing`, `capacity`, `retry`, `deferred`, `scanCalls`,
`hasMore`, `done`, and `status`. Counters are bounded integers, `dryRun` is always
true, `hasMore` and `done` are booleans, and status is `ok` or `blocked`.
Capacity/retry outcomes are aggregates, with no internal retry. Operational
failures use fixed public error codes and no raw exception text.

No cursor, Redis key, Collaboration ID, source reference, message, participant,
guest, identity, or secret is returned or logged by this operation. No migration
logging is added. Strict projection rejects unexpected engine fields and values.

This is a first-page sample, not a completeness guarantee: SCAN may return an
empty or partial batch. `hasMore` reports remaining scan/candidate work without
exposing resumable state. Repeating the operation starts another sample at `0`.
Decide whether continuation is needed separately after an authorized Production
sample is observed.

## Local verification

The direct runtime Redis fixture uses a disposable private Unix socket. A
healthy five-candidate page measured SCAN 1, EVAL 2, nested reads 73, HKEYS 0,
EXISTS 0, and migration writes 0. Nested reads were TYPE 21, STRLEN 15, MGET 2,
PTTL 25, GET 5, and HGET 5. These are local measurements, not Production numbers.

The unchanged natural capacity path measured 1,000 EXISTS and two HKEYS calls;
it returned capacity aggregates without writes. Full and prunable indexes were
tested. Serialized canonical threads, source pointers, and discovery indexes,
plus their absolute expiries, remained unchanged. Tests include legacy owner
records, all lifecycle states, participant records, malformed historical records,
byte-bound deferral, and current/stale Team membership.

Direct HTTP tests cover security, exact input, fixed invocation count, privacy,
failures, and startup/summary/guest import isolation. Dedicated limiter tests
cover the new policy and regressions for existing policies. The focused matrix
passed 292 distinct tests, including manual migration, grant, lifecycle, summary,
owner, guest, replacement, and session regressions. Tests that inspect protected
V1 paths are excluded. Sandbox restrictions initially blocked disposable Redis
startup; the affected fixture tests passed with local Unix-socket access.

This slice authorizes a local code commit only. Production activation, request
execution, environment changes, push, and deployment require separate work.
