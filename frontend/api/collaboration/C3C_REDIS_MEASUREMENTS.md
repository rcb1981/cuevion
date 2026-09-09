# C3C local Redis command measurements

Measured on 2026-09-09 with disposable Redis **8.8.0**, Unix socket only, and the
existing repository Python 3.11 environment. These are local measurements, not
production traffic, latency or billing estimates.

## Reproduce

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=frontend /Users/rutger/cuevion-app/venv/bin/python3 -m api.collaboration.measure_c3c_notifications --baseline-store /private/tmp/c3c-baseline-redis_store.py
```

The optional baseline file is the exact pre-C3C `redis_store.py`, saved before
editing at parent `e2ab6527308141d0cbe75093ebd035dd112e87e4`. Its SHA-256 was
`b97e336ac3a8a25f21326e606ed00018bb491abde6e55e80ecc799d31a266583`.
Without `--baseline-store`, the module measures the current implementation only.
It never executes the baseline Python module: a restricted AST reader extracts
literal Lua strings, concatenations and zero-argument `.strip()` only.

Every application/store call receives the local fixture transport. Hosted
credentials are never used. The fixture proves active Team membership locally;
external Team authority requests, HTTP authentication and route rate limiting
are outside these Collaboration/notification Redis counters.

`CONFIG RESETSTAT` precedes each measured operation; `INFO commandstats` follows
it. Outer calls are recorded by the transport and subtracted from Redis command
counters to obtain nested commands. An in-memory Lua wrapper counts notification
writes by recipient without adding Redis commands. Output contains aggregate
counts and result sizes only; no source, message, notification, identity, token
or session payload is logged. Setup and fixture seeding are excluded.

## Results

The normal Collaboration fixture has one owner and one current Team participant.
Its participant already has the start notification before the measured append.
The owner has no app notification before the first guest reply. The add fixture
starts owner-only, so the added participant has an empty notification store.

| Operation | Outer calls | EVAL | Nested commands | Notification writes |
|---|---:|---:|---:|---|
| Owner Shared, before C3C | 2 | 1 | 22 | 0 |
| Owner Shared, C3C | 2 | 1 | 43 | Participant: 6 |
| Owner Internal Note, before C3C | 2 | 1 | 22 | 0 |
| Owner Internal Note, C3C | 2 | 1 | 43 | Participant: 6 |
| Owner Shared/Internal retry, before and after | 2 | 1 | 9 | 0 |
| Guest Shared reply | 2 | 1 | 56 | Owner: 6; participant: 6 |
| Guest reply retry | 2 | 1 | 11 | 0 |
| Add Team participant | 2 | 1 | 32 | Added participant: 6 |
| Add same participant again | 1 | 0 | 0 | 0 |
| Summary, 50 stored unread records | 1 | 1 | 11 | 0 |
| List first 50, 50 returned | 1 | 1 | 13 | 0 |
| Mark read | 1 | 1 | 15 | Recipient: 2 |
| Repeat mark read | 1 | 1 | 13 | 0 |
| Owner Shared at 1,000-record capacity | 2 | 1 | 46 | Participant: 9 |
| Retry that capacity-path message | 2 | 1 | 9 | 0 |

Two outer calls means one canonical thread `GET` plus one atomic `EVAL`. The
participant-add retry needs only the canonical `GET`. Summary, list and mark-read
each issue one `EVAL`.

The baseline comparison substitutes the exact baseline create/append Lua into
the same wrapper and canonical owner/participant fixture, removing the two new
owner append arguments. The baseline append wrapper still reads the thread once
and performs one EVAL. Shared and Internal Note therefore each add **21 nested
commands** in the measured normal case, with no additional outer Redis call.
The delta includes notification integrity checks and preservation of existing
canonical/source expiry; it is not just the number of notification writes.

## Write and prune details

For each newly notified recipient, insertion makes six notification commands:
`HSET` once, `ZADD` twice, and `PEXPIREAT` on the three recipient keys. These key
expiries do not alter the Collaboration thread or source pointer. Existing
records and every related index are validated before any canonical write.

A populated recipient contributes 12 notification preflight commands:
`TYPE` ×3, `HLEN` ×1, `ZCARD` ×2, `PTTL` ×3, `HGETALL` ×1 and `ZRANGE` ×2.
An empty recipient contributes six: `TYPE` ×3, `HGETALL` ×1 and `ZRANGE` ×2.
The helper also reads Redis `TIME` once and, for an existing canonical thread,
its `PTTL` once after the clock read. All record/index reads have the enforced
1,000-record bound.

At capacity, one `HDEL` plus two `ZREM` commands prune the oldest record before
inserting the new notification. The measured store remains exactly 1,000 records
and the known oldest ID is absent. Expired-record pruning uses these same three
batched commands, with at most 1,000 IDs. Ordering is deterministic by creation
timestamp and then notification ID. Capacity never fails a valid new event.

Message retries return the original canonical message and skip notification
prepare/commit. No notification, read state or notification retention is
refreshed. Guest retries still validate the live invite/session before replay.
Participant-add retries make no EVAL. A first mark-read performs one `HSET` and
one `ZREM`; its repeat makes no write and preserves the original server `readAt`.

## Read/count costs and retention

The summary path is `TYPE` ×3, `HLEN` ×1, `ZCARD` ×2, `PTTL` ×3, `TIME` ×1 and
`ZCOUNT` ×1. It fetches no record hash or ordered-index rows. Expired unread
entries are excluded by the expiry score. The measured serialized Redis result
value was **44 bytes**, compared with **35,913 bytes** for the first 50-row list.
These sizes use `json.dumps(result)` at the local transport boundary, excluding
the outer transport envelope and HTTP response; they are fixture-specific.

The list and mark-read paths make the 12-command full integrity preflight plus
one Redis clock read. List returns at most 50 DTOs; its cursor binds the current
workspace/recipient and must strictly decrease in timestamp/ID order. The
notification DTO excludes `recipientUserId`. The unread ZSET remains server
authority, atomically maintained with each event and exact mark-read.

Both notification `createdAt` and the first `readAt` use Redis server time in
milliseconds. A Collaboration activity's monotonic timestamp may be slightly
ahead without preventing immediate mark-read; `activityId` still targets the
exact canonical activity. Notification expiry is the earlier of actual
canonical thread expiry and notification commit time plus 180 days. Tests prove
exact canonical expiry preservation, hosted cjson null behavior, and immediate
mark-read when the activity timestamp is 60 seconds ahead.

There is no polling, SCAN, KEYS command, unbounded index, background cleanup or
production activity in the measurement module or new notification authority.
