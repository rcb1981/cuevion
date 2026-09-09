# C3P2B0 — Canonical Collaboration author identity

Baseline: `perf-1`; local HEAD and remote main
`86812f82759a10346bef482987e56d29c148ce84`; staging empty and the exact three
pre-existing user changes present. Protected contents accessed: **NO**.

## Contract and authority

New authenticated owner and entitled Team Shared/Internal activities retain
the server's canonical `capability.actor_user_id` as `authorUserId`. Existing
authentication supplies that value from `AuthenticatedMemberContext.user_id`;
request payloads cannot supply `authorUserId`, `actorUserId`, or `userId`.
Existing exact canonical user-ID validation is reused.

The existing author representation remains: stored `authorKind` is
`owner`/`internal`/`guest`/`system`; internal DTO `authorRole` remains
`Cuevion user`/`Guest reviewer`/`System`. There is no author-object redesign.

Stored typed message shape:

```text
{id, authorKind, authorDisplayName, authorUserId, text, visibility, createdAt}
```

Authorized owner/Team read and successful internal send message shape:

```text
{id, authorDisplayName, authorRole, authorUserId, text, visibility, timestamp}
```

`authorUserId` is a canonical `usr_*` string or null. Guest/system records
cannot contain a nonnull Cuevion author ID. Historical missing identity becomes
null, regardless of matching name, owner ID, membership, or notification actor.
No migration or historical identity backfill is performed. Guest read and send
DTOs remain unchanged and do not expose internal Cuevion user IDs.

The active canonical-workspace write path rejects missing canonical author
authority. The existing inactive email-scoped legacy foundation can still carry
null; it does not supply canonical proof to the public authenticated write path.

## Serialization and atomic mutation

Thread schema version remains 2. Integer wire fields remain decimal strings.
Python builds the message JSON before the existing atomic Lua mutation. Lua's
strict message schema and equality checks now understand the additional field,
and bind a newly appended ID to the same actor argument already used by C3C.
They reject impersonation and changing, erasing, or inventing prior author proof.

Canonical nonnull author IDs are persisted inside that same JSON write. Unknown
identity is omitted on the Redis wire and restored to explicit null on decode.
Both missing and explicit null are accepted by the record schema. This prevents
null expansion from making an otherwise valid historical record exceed the
262,144-byte limit. A regression covers the exact limit, a one-byte overflow,
and the added size of real canonical identity.

Idempotent recovery returns the original activity and author proof. A historical
recovered activity remains null and is not patched. A recovered nonnull ID must
still match authenticated authority. Canonical activity IDs, fingerprints,
guest idempotency, notification recipients and self-suppression are unchanged.

No new Redis keys, commands, round trips, retention changes, or best-effort
identity writes were added. C3C receives actor authority directly as before.

Measured before any edit and again after the final serializer change, using
the existing local Redis fixture with an injected transport:

| Mutation/storage path | Baseline | Final |
| --- | --- | --- |
| Shared; identical retry | `GET`, `EVAL` | `GET`, `EVAL` |
| Internal; identical retry | `GET`, `EVAL` | `GET`, `EVAL` |
| Guest reply; identical retry | `GET`, `EVAL` | `GET`, `EVAL` |
| Thread read | `GET` | `GET` |

These counts cover the mutation/storage layer; the surrounding existing HTTP
authentication and rate-limit paths were not changed.

## Frontend and C3P2A handoff

The strict frontend internal activity type requires `authorUserId: string | null`.
Cuevion activities accept a valid canonical ID or historical null. Guest/System
activities require null. Missing DTO fields, malformed IDs and alternative
authority-shaped fields reject. Backend legacy reads explicitly emit null.
Request bodies, activity IDs, UI, refs, highlights and notification ordering
are unchanged.

C3P2A can use `authorRole === "Cuevion user"` together with
`authorUserId === currentCanonicalUserId` (with a nonnull current canonical ID)
to prove self ownership. Historical null, guests and system records stay neutral.
This slice does not implement alignment or any other chat UI.

## Verification

All Python invocations disabled bytecode. Tests use mocks and disposable local
Redis instances under `/tmp`, with TCP disabled and a Unix socket. No production
credentials, providers, Vercel operations, dependency installs, push, or deploy.

| Suite | Distinct tests |
| --- | ---: |
| Model/mutation/application/owner and guest HTTP | 199 |
| Authorization/guest session/request security/summary/discovery | 165 |
| Lifecycle/store/owner idempotency | 58 |
| Existing production Lua integration fixture | 122 |
| C3C notification suites and C3D1 route packaging | 102 |
| C3D0 backend | 58 |
| New author-identity Redis invariants | 12 |
| Frontend owner read/write contracts | 63 |
| C3D frontend | 148 |
| C3D0 frontend | 53 |

All **980 distinct counted tests pass**, plus WorkspaceShell performance and
TypeScript. The 122 existing Lua cases comprise three natural-clock tests passed
in the full run and 119 cases passed again after final typed-null fixture
corrections. Repeated runs are not added to the count. There are 32 new focused test methods/cases
across model, mutation, HTTP, Redis and frontend identity coverage. Existing
fixture updates account for explicit typed null while preserving raw historical
wire data and retention/privacy/no-write assertions.

WorkspaceShell performance assertions and full frontend application TypeScript
(`node node_modules/typescript/bin/tsc -p frontend/tsconfig.app.json --noEmit`)
pass. No WorkspaceShell or other visual production file changed.

Exact-path bytecode audit: 11 existing Collaboration `.pyc` files predate the
pre-edit baseline measurement (newest: 2026-09-03); they were left untouched.
No new bytecode was generated by the bytecode-disabled invocations.

## File inspection and scope

Production changes: `frontend/api/collaboration/{models,application,mutations,redis_store}.py`
and `frontend/src/lib/{collaborationOwnerReadApi,collaborationOwnerWriteApi}.ts`.
Direct tests, dependent contract fixtures and this handoff are the remaining changes.

Inspection included those files, `authorization.py`, `owner_http.py`,
`guest_http.py`, `guest_session.py`, `measure_c3c_notifications.py`, and the
explicitly named Collaboration suites in the table. Read-only notification
inspection included `frontend/api/notification_service/store.py` for hosted-null
fixture semantics. Frontend inspection covered the owner contract tests,
notification fixtures/navigation/store/component tests, exact-message tests and
WorkspaceShell performance tests. Existing C3D0 backend tests were executed.
Searches and content diffs were scoped to known non-protected paths.

Required commit message: `add canonical collaboration author identity`.
Required parent/unchanged remote main:
`86812f82759a10346bef482987e56d29c148ce84`.
Local commit and post-commit gates are reported in the task response.
C3P2B0: GO. C3P2A resume: GO after acceptance of this local slice.
Next action after acceptance: resume C3P2A locally with this identity contract.
Push and deployment require separate authorization.
