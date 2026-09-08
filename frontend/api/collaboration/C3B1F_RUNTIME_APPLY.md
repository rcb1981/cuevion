# C3B1F temporary runtime historical discovery apply

The existing `/api/collaboration/owner` POST dispatcher supports one temporary
apply operation. It reuses C3B1C's canonical one-page engine and Lua, and the
current authority checks introduced for C3B1E. This slice adds no route,
checkpoint, continuation, frontend UI, polling, scheduled job, or secret export.

## Exclusive feature modes

`CUEVION_COLLAB_DISCOVERY_MIGRATION_OPERATOR_MODE` accepts exact values:

| Mode | Grant issuance | Runtime dry-run | Runtime apply |
| --- | --- | --- | --- |
| absent / `off` / unknown | Denied | Denied | Denied |
| `dry_run_grant` | Existing behavior | Allowed | Denied |
| `runtime_apply` | Denied | Denied | Allowed |

The apply operation additionally requires the existing `owner_write` HTTP mode.
No mode value is normalized. This code change does not activate any Production
mode or authorize a Production request.

## Exact request and authority

```json
{
  "operation": "apply_discovery_migration_page",
  "ownerMailboxId": "<exact owned allowlisted mailbox>",
  "confirmation": "APPLY_HISTORICAL_DISCOVERY"
}
```

The confirmation must match byte for byte, with no whitespace or case
normalization. These three fields are the entire request schema. Duplicate
fields, caller identity, dryRun/apply flags, cursors, budgets, page sizes, Redis
keys, and checkpoint inputs are rejected before migration work.

The shared owner boundary requires a current genuine owner session, active
canonical account/workspace, exact trusted Origin, session-bound CSRF, and owner
allowlist membership. The exact mailbox must be currently owned, allowlisted,
and supported (`google` or `custom_imap`). User ID, workspace, verified email,
display name, mailbox provider, and Team entitlement come from current trusted
authorities. Owners do not need Team enrollment. Participant candidates require
their exact current membership reference; stale or rebound membership does not
qualify. Authority failures stop before SCAN.

The dedicated apply GCRA policy permits a burst of one and replenishes one
request per 900 seconds. It uses a separate HMAC purpose for canonical user and
workspace within the existing owner security-counter family. Existing policy
values, key bytes, and behavior are unchanged. The public 429 `Retry-After`
range stays 1–60 seconds; it does not promise admission after that delay.

## Fixed page and permitted writes

Only the exact authenticated, confirmed, feature-gated, rate-limited branch
lazily imports the engine and invokes `run_runtime_apply_page` once. That public
adapter hardcodes `dry_run=False`; the existing dry-run adapter hardcodes
`dry_run=True`. Both share runtime authority setup and `_run_one_page` with the
manual adapter. Neither runtime adapter accepts execution options.

Execution starts at cursor `0`, permits one SCAN attempt with COUNT 100, and
processes at most five candidates using at most two bounded EVALs. Existing
262,144-byte aggregate canonical bounds remain. There is no internal retry,
second page, cursor drain, compensation, persistence, or continuation token.

The unchanged C3B1C Lua permits only:

- Discovery HSET for required enrollment and bounded PEXPIRE when required.
- Discovery HDEL for canonically proven absent references under existing rules.
- Proven legacy owner authority enrichment using SET KEEPTTL.

Before committing a candidate, the existing Lua revalidates canonical bytes and
digest, source pointer authority, recipient entitlement, TTL, and capacity.
Canonical absolute expiry is preserved. Source pointers and their expiries,
lifecycle state, existing participant arrays, messages, guest/invitation/session state, and
canonical retention are not changed. No PERSIST or live Collaboration deletion
is introduced. Discovery expiry follows the existing bounded canonical lifetime.

A repeat against unchanged state returns `alreadyPresent`, with no duplicate
enrollment, canonical rewrite, or unnecessary discovery TTL refresh. The intended
Production procedure still permits one separately authorized apply request.

## Result and operator interpretation

The existing `ok`/`data` envelope contains exactly `v`, `dryRun`, `examined`,
`enrolled`, `alreadyPresent`, `skippedInvalid`, `skippedUnauthorized`,
`unresolvedIdentity`, `staleEntitlement`, `missing`, `capacity`, `retry`,
`deferred`, `scanCalls`, `hasMore`, `done`, and `status`. Version is 1, `dryRun`
is false, counters are bounded integers, and `hasMore`/`done` are booleans.
Status is the existing fixed `ok`/`blocked` enum. Capacity and retry are aggregate
outcomes. Other operational failures use fixed safe error codes, never raw
exceptions. No raw keys, cursor, Collaboration IDs, source references, messages,
participant/guest data, identities, credentials, or secrets are returned or
logged. No migration logging is added.

The reported prior Production dry-run examined three candidates with three
would-enroll outcomes and completed its page. Those counts are observations,
not hardcoded authority or expected-result assertions in product code. Apply
reports the actual current data. If any skip, unresolved identity, stale
entitlement, capacity, retry, or deferred count is nonzero, or `hasMore` is true
or `done` false, the operator stops and decides the next action. The operation
never launches another page. A transport failure can leave an ambiguous commit
outcome; it is not automatically retried or compensated.

## Local verification

The focused matrix passed 330 distinct tests. It includes C3B1A lifecycle, C3B1B
summaries, C3B1C manual migration, C3B1D grants, C3B1E dry-run, owner/guest HTTP,
replacement/session regressions, and direct apply/limiter tests. Tests that
inspect protected V1 paths were excluded. Redis fixtures use disposable local
Unix sockets; no Production Redis was contacted.

A healthy three-candidate fixture (one legacy, two modern) returned examined 3,
enrolled 3, all other counters 0, scanCalls 1, hasMore false, done true, status
ok. Migration-side command measurements were:

| Command | Count |
| --- | ---: |
| SCAN | 1 |
| EVAL | 2 |
| Nested reads | 45 |
| HSET | 3 |
| PEXPIRE | 1 |
| HDEL | 0 |
| SET KEEPTTL | 1 |
| Other writes | 0 |

Nested reads were TYPE 13, STRLEN 9, MGET 2, PTTL 15, GET 3, and HGET 3;
HKEYS, HLEN, and EXISTS were zero. These are local fixture measurements, not
Production measurements. Test-only command observation verifies actual write
targets and the KEEPTTL flag. A repeat returned alreadyPresent 3 with zero
writes and unchanged serialized state and absolute expiries. Additional tests
prove absent-only pruning, live capacity protection, digest-race rejection,
malformed/unauthorized/stale candidates, byte-bound deferral, and one-page bounds.

The scoped trace inspected `owner_http.py`, `owner_authentication.py`,
`owner_request_security.py`, `owner_rate_limit.py`, `operator_grant.py`,
`discovery_migration.py`, `authorization.py`, the canonical discovery Lua helpers
in `redis_store.py`, current mailbox authority in `../user_config_store.py`,
account authority in `../auth/account_authority.py`, Team authority in
`../team/authority.py`, and the direct/regression test files named above.

Production push, deployment, temporary mode activation, the single apply request,
and subsequent disabling of temporary migration mode require separate
authorization. This implementation step performs only local isolated tests and
the requested local commit.
