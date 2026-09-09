# C3C verification and scope

Implementation is local to `perf-1`, based on
`e2ab6527308141d0cbe75093ebd035dd112e87e4`. No push, deployment, production HTTP,
production Redis, dependency installation, or persistent environment change.

## Authority and atomicity

The neutral `POST /api/notifications` route requires the generic current Cuevion
account/workspace session and current Team entitlement for non-owner members.
Exact Host/Origin plus non-simple JSON POST reuse the generic app CSRF contract.
The route accepts summary, bounded list, and exact mark-read only. Bodies are
limited to 2,048 bytes; responses are no-store/nosniff.

Active verified owner/Team append and guest reply resolve current exact Team
membership references before Redis. Inactive/re-invited grants are excluded,
authority outages fail closed, and Lua checks recipients against the canonical
thread. Owner identity is included without requiring owner Team enrollment.
The external Team check and Redis commit retain the frozen cross-store race.

Four emitting scripts change: `_CREATE_V2_THREAD_LUA`,
`_SAVE_V2_PARTICIPANTS_CAS_LUA`, `_APPEND_V2_OWNER_IDEMPOTENT_LUA`, and
`_APPEND_V2_GUEST_REPLY_LUA`. Notification integrity/type/bounds/encoding preflight
and discovery preflight both finish before any write. Notification, Collaboration,
and discovery changes commit inside the same EVAL. No best-effort split write.
Malformed stores fail through existing safe public errors, without raw Redis
errors. Expected runtime/type errors are preflighted because Lua does not roll
back earlier writes on an error.

Create/add validate Team authority through the existing application boundary.
Create-with-guest remains guest-only and emits zero app notifications. Resolve,
reopen, invite/session management, source/discovery semantics and Priority are
frozen. Historical records with no canonical ownerUserId yield no inferred
notification recipients; existing authorized discovery repair remains the
identity-enrichment authority. The inactive legacy non-idempotent append helper
is unchanged; active verified HTTP appends use the idempotent path.

Guest sends require one canonical 256-bit base64url key per logical operation.
The server derives a stable SHA-256 message ID from the session hash and key.
The bounded canonical message log supplies durable replay; live session and
invitation authorization is rechecked in Lua before returning the original
message. Changed content with the same key conflicts. No separate bearer or key
is copied into notifications. Owner fingerprint/idempotency identity is retained.

Each user/workspace has three keys in `{cuevion-collab-v2}`: record hash,
creation-order sorted set, and unread-expiry sorted set. Maximum 1,000 records;
prune expired/oldest deterministically without failing a valid new message.
Individual records contain no message body, note body, email or bearer fields.
Notification createdAt/readAt use Redis time. Expiry is at most actual canonical
expiry and 180 days; emitting updates use KEEPTTL and leave source TTL untouched.
Summary excludes expired unread scores without fetching DTOs. Full record/index
integrity checks are bounded on emission, listing and mark-read.

## Executed gates

All Python invocations explicitly used `PYTHONDONTWRITEBYTECODE=1`.
Final backend gates use the existing repository Python 3.11 with installed
cryptography. An earlier bundled Python 3.12 probe changed Unicode category
expectations for an existing fixture; it was stopped and the complete frozen
suite rerun on the repository runtime without changing that fixture.

- **765 Collaboration tests:** lifecycle, summary/discovery, migration/operator
  dry-run/apply, repair, owner idempotency, guest/session, authorization, model,
  request security, HTTP and store tests. One old guest import-safety fixture was
  adapted to the required key/result contract; the failing case and all 15 safe
  import-safety tests were rerun successfully.
- **122 complete isolated Redis Lua tests:** passed in 330.156 seconds, including
  natural-clock C2G5/C2G6 replacements, revocation/session, schema/TTL/atomicity,
  hosted cjson/null cases, and legacy compatibility.
- **135 final focused tests:** notification model/store/HTTP/rate/recipient and
  atomic event matrix, plus owner idempotency, guest HTTP and mutations. This
  includes 78 tests in the new notification/atomic modules; overlapping existing
  regressions are not added again to the unique total.
- **157 generic-auth regressions:** account graph, session, workspace, Auth0 and
  generic HTTP authority.
- **18 local command-measurement scenarios:** baseline/current append, guest,
  add, list/count/read, replay and capacity. The capacity path verifies both the
  canonical message and exact notification activity ID.
- **Frontend:** 55 guest browser scenarios, guest API/structural checks, and seven
  frozen Priority/discovery TS suites pass. The seven include 38 named Priority
  cases. Full frontend TypeScript has zero diagnostics in both unchanged configs
  (88 app roots/150 total sources; 2 configuration roots/57 total sources).

One existing test was excluded **before execution**:
`test_import_safety.CollaborationV2ImportSafetyTests.test_active_inbox_routes_and_frontend_do_not_reference_inactive_application_modules`.
It enumerates and reads every inbox backend file, including a protected path.
All other selected tests ran. Root TypeScript and broad npm test were not run;
the full frontend compiler/test checks had filesystem guards and recorded zero
protected-path attempts.

Protected paths were not read, diffed, modified or staged. One initial prohibited
`rg --files` filename listing ran in the same batch as loading the user attachment,
before its restrictions were available; it returned only package/lock filenames.
This procedural exception was immediately disclosed. No protected file contents
were read; the listing was not repeated. Existing bytecode remains untouched.

## Handoff

`C3C_MUTATION_TRACE.md` and `../notifications/AUTHORITY_TRACE.md` record pre-edit
files/functions and route/authority decisions. `C3C_REDIS_MEASUREMENTS.md`
contains command/payload accounting and reproduction. `C3C_FRONTEND_HANDOFF.md`
specifies the strict DTO, APIs, exact routing and C3D replacement points.

C3C adds no mention parsing, visible Notifications integration, sidebar/dashboard
count wiring, notification read-on-open, click routing, polling or cleanup job.
C3D is the next authorized scope to plan; C3E owns authoritative mentions.
