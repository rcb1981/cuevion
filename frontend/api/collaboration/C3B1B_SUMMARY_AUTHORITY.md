# C3B1B: bounded Collaboration summary authority

This slice provides authenticated discovery and a client. It does not connect to
Priority, WorkspaceShell, notifications, a CTA, or any refresh timer.

## Trace and design decisions

- `application._canonical_owner_authority` persists the authenticated canonical
  `ownerUserId`, including `participants=[]` on guest-only creation. Internal
  creation resolves an explicit Team participant before persistence.
- Source resolution and `build_v2_source_thread_key` bind owner, mailbox and exact
  provider locator. Google uses its existing provider message ID. The existing
  IMAP contract is specifically `INBOX` plus decimal-string UIDVALIDITY and UID.
- `_save_v2_participants_if_expected` retains canonical participants and permits
  explicit add/rebinding. There is no Collaboration participant-removal callback
  from Team today. Discovery must therefore revalidate Team authority on serving.
- `RuntimeTeamAuthority.resolve_active_member_by_user_id` reads one exact user
  pointer and its membership record, checks workspace, user, active status and
  invitation binding, and returns `sourceInvitationId`. These are at most two
  Redis GETs once per request, not once per Collaboration.
- Lifecycle and append Lua preserve the existing 180-day retention behavior.
  No v2 thread deletion operation exists in this layer; physical expiry is the
  canonical removal boundary. Existing v1 scanning routes are not used.
- The legacy `get_threads_many` performs individual reads. The new path instead
  uses one EVAL containing one MGET for the candidate page.
- All Collaboration keys already share `{cuevion-collab-v2}`. Derived discovery
  keys follow that existing slot, including the existing convention of bounded
  dynamically derived keys inside Lua. No unrelated key topology is changed.
- Owner HTTP uses authenticated POST operation dispatch, origin checks, CSRF,
  owner rollout allowlisting and read rate limiting. Its existing full owner DTO
  includes content and guest state and is deliberately not used for discovery.

## Public contract

POST `/api/collaboration/owner` with exactly:

```json
{"operation":"list_summaries","cursor":null}
```

The existing `{ "ok": true, "data": ... }` response contains:

```typescript
{
  v: 1;
  workspaceId: string; // canonical wsp_ identity from authenticated authority
  summaries: Array<{
    collaborationId: string;
    workspaceId: string;
    mailboxId: string;
    sourceRef:
      | { provider: "google"; providerMessageId: string }
      | { provider: "custom_imap"; folder: "INBOX"; uidValidity: string; imapUid: string };
    state: "needs_review" | "needs_action" | "note_only" | "resolved";
    updatedAt: number; // canonical milliseconds, not a string or coerced number
    viewerAccess: "owner" | "participant";
  }>;
  nextCursor: string | null;
}
```

There are no content, invitation, guest identity, session or bearer fields.
ACTIVE is exactly `needs_review | needs_action | note_only`. Resolved entries
remain discoverable with `state=resolved`. Resolve/reopen immediately affect the
next canonical summary read without any separate active flag or projection lag.

The frontend `collaborationSummaryApi.ts` validates exact envelopes and keys,
canonical IDs, sources, workspace equality, timestamp bounds, ordering, states,
and cursor progress. It reuses the existing authenticated CSRF transport. There
is one summary HTTP operation per page (plus existing initial CSRF bootstrap).

## Index topology, capacity, ordering and retention

Exactly one new Redis key family:

```text
cuevion:collab:v2:{cuevion-collab-v2}:discovery:<workspaceId>:<userId>
  Redis HASH
  field: collaborationId
  value: JSON {ownerUserId, mailboxId, sourceRef, threadHash}
```

The same family handles owners and explicit internal participants. There are no
external-guest recipients. Values freeze immutable routing bindings and keep an integrity digest of the
fully validated canonical wire record. They
contain no duplicate lifecycle state or full thread. Every served result comes
from the canonical thread and must match these stored bindings. The digest is
computed by `redis.sha1hex(raw)` only after full validation in the same atomic
mutation. It is a byte-integrity/version check, not an authorization token.
Listing MGETs the actual records, checks this digest, decodes with native cjson,
and revalidates metadata and current owner/participant entitlement. It avoids
repeating the expensive Lua content parser for every candidate. A changed or
malformed canonical record with an old index digest fails closed.

Each index has at most **1,000** retained IDs, including resolved IDs. Enrollment
at capacity can remove only references whose exact canonical key is absent;
otherwise the entire mutation returns `discovery_capacity_reached` (public 409).
Live active and resolved work is never silently evicted. Capacity repair inspects
at most 1,000 exact IDs per recipient and does not enumerate the Redis keyspace.

A page examines at most **50** candidates, in ascending ASCII Collaboration-ID
order. The cursor is the last examined ID, not the last authorized result. A page
can be empty and still have a next cursor. Consumers must drain to null before
using a set as complete. This covers the whole retained index in at most 20 pages
when enrollment is unchanged. Lifecycle updates do not move IDs across cursors.
Pagination is not a multi-request snapshot: concurrent new enrollment before a
cursor is found on the next revalidation from null. Startup, foreground, explicit
refresh and mutation-completion fetching can use this contract; no polling exists.

Every index key has a positive TTL of at most 180 days. New enrollment or a
canonical mutation that already refreshes retention keeps the index alive for
that referenced thread. An existing longer index TTL can cover other referenced
threads, but can never make an expired canonical thread readable. Serving skips
missing, immortal, malformed and mismatched canonical records. Only proven
missing keys are lazily pruned; malformed existing records are not repaired.
Listing never refreshes TTL. Lazy enrichment uses remaining PTTL and never
extends the thread or source-pointer lifetime. Existing canonical mutation
retention is unchanged; no new operation extends canonical retention.

## Authorization and atomicity

The boundary revalidates Auth0 session/account authority. Summary authorization
also resolves the canonical member once and requires it to match the verified
context's workspace, email and display name. It takes one exact current Team
snapshot for this user. An absent/removed membership grants no participant access
but does not remove independent canonical owner authority. Unavailable Team
storage fails the request closed; no cross-request Team cache is introduced.

Owner access requires matching canonical `ownerUserId` and owner email, current
workspace, plus the existing mailbox rollout check (in memory). Participant access
requires an explicit participant with this user's ID and a `membershipRef` equal
to the current Team `sourceInvitationId`. Workspace membership or index presence
alone is insufficient. The storage Lua repeats the per-candidate authority and
binding checks against the canonical record. Removed/reinvited Team memberships,
unrelated users, forged index copies and wrong workspaces do not authorize results.

Create, create-with-guest, participant add, resolve/reopen, and successful thread
appends preflight all discovery key types, capacities, bindings and serialized
values before any write. Their existing Lua commits thread/source changes and
index transitions together. All keys share the same Redis slot. Failed CAS or
preflight leaves discovery untouched. Appends also maintain retention because
existing appends refresh canonical thread TTL and updatedAt.

Unchanged entries do not receive another HSET. Successful canonical changes
update the digest in every recipient reference atomically. Lifecycle no-ops,
participant duplicate no-ops and idempotent append recovery do not rewrite index
values. Guest invite creation/exchange/revoke/logout do not maintain discovery;
these operations do not change Cuevion entitlement or thread lifecycle. Guest
reply is a canonical thread append and follows the same retention rule.

## Compatibility and remaining migration

Pre-index valid records remain readable. Exact authenticated owner source lookup
performs strict semantic CAS enrichment against the current thread and its exact
source pointer. Existing canonical owner identity must match; historical records
without the complete owner authority group gain the verified owner and an empty
participant array. No old participant identities are guessed. No content, source,
lifecycle timestamp or retention changes during this enrichment. Repeated
unchanged enrichment is read-only. Successful lifecycle/participant transitions
also enroll historical records when existing authorization proves canonical owner
identity. Email-as-workspace foundation records remain ineligible.

**Historical population is not complete.** Threads never accessed through these
bounded paths remain undiscoverable until a separately authorized one-time
migration supplies known canonical IDs/source bindings and owner proof. No safe
complete historical enumeration authority was found in this layer. No global
SCAN, mailbox-wide per-message lookup, browser snapshot or approximate source
matching is introduced to hide this migration need.

## Redis work and quota accounting

Let U be the canonical owner plus explicit participants (1..16), M the retained
index count (0..1,000), and N the page candidates (0..50). Counts below distinguish
network commands from nested Redis commands; they do not assume Lua subcommands
are free under the service's billing policy.

| Operation | Network/command shape introduced by discovery |
| --- | --- |
| Create | No additional round trip: existing EVAL. Typically 3-4 index reads and 2 writes per recipient (5-6U nested commands), plus the existing fresh-create four source/thread commands. |
| Create with guest | Same single existing atomic EVAL and 5-6U index work; existing invitation graph checks/writes remain. |
| Resolve/reopen | Same existing EVAL; 4 index reads, one digest HSET and at most one PEXPIRE per already indexed recipient. State is derived; no duplicate lifecycle state is stored. No-op adds no discovery work. |
| Participant add | Same existing EVAL; existing recipients need up to 6 commands each, new enrollment 5-6. All are bounded by U<=16. Duplicate no-op adds none. |
| Summary page | One EVAL; TYPE, HLEN, PTTL, HGETALL; one true MGET; at most N PTTL/EXISTS checks; optional single HDEL for proven missing records. For healthy nonempty pages: 5+N nested commands, plus the EVAL. No per-row network requests. |
| At capacity | Rare bounded preflight: HKEYS plus at most M EXISTS checks for each full recipient requiring enrollment, followed by one HDEL only if the whole mutation commits. |
| Exact lazy enrichment | One extra EVAL after existing exact source lookup; positive remaining TTL, semantic CAS, bounded index work. No HSET or expiry refresh on unchanged enrollment. |

Existing authentication, source resolution, mutation readback, and full DTO costs
for explicit content reads/writes are unchanged and additional to these counts.
The summary request uses fixed account/session work, existing read rate limiting,
and at most two Team GETs total. It never hydrates guest/session records or builds
full owner DTOs. Canonical thread bytes **are** read and decoded inside Redis for up to N
candidates; only summaries leave Redis. Full strict schema/content validation
runs on mutation; listing verifies the recorded byte digest before decoding and
rechecking routing and entitlement. An initial implementation that repeated
strict Lua content validation timed out at the large-record page bound; the
digest check removes that repeated work without maintaining a second thread. This deliberately follows the
allowed bounded canonical-batch derivation approach instead of maintaining a
second lifecycle-authoritative copy.

## Validation scope

Direct tests cover the schema, sources, bounded pages, authorization including
actual Team pointer/membership resolution, atomic enrollment/capacity failures,
CAS, TTL, lazy legacy enrichment, guest-only ownership and content-free batching.
A full 50-candidate page with approximately 245 KB canonical records checks the
actual batch response. C3B1A lifecycle and selected existing participant, append,
source/HMAC, guest lifecycle and C2G5/C2G6-related projection regressions are run
against isolated local Redis. All Python runs set PYTHONDONTWRITEBYTECODE=1.
Protected-file-reading tests and broad repository suites are excluded. No
dependencies are installed and no production HTTP/Redis, push or deployment runs.

Final local validation: 327 Python tests passed in the combined permitted run;
three targeted TTL/enrichment tests then passed after the final empty-hash expiry
fix (328 distinct tests overall). The final large-page read took 0.097 seconds and
returned 13,728 bytes locally; this is not a production latency measurement.
Summary, owner-read, source-locator and owner-write TypeScript suites passed, as
did a scoped TypeScript no-emit check. Whole-project builds and protected-reading
suites were intentionally excluded. Existing bytecode caches were not deleted;
all Python commands disabled bytecode generation.

The public `v: 1` versions this new summary envelope only. It does not use or
activate legacy Collaboration v1 routes. Page results are not a cross-request
snapshot; consumers must discard partial/error results and revalidate after
relevant mutations rather than treating a partial page as complete authority.
