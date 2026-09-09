# C3C pre-edit mutation trace

Baseline: `perf-1`, local HEAD and remote main
`e2ab6527308141d0cbe75093ebd035dd112e87e4`; index empty; only the three
specified pre-existing status entries. Remote lookup was read-only.

Exact inspected implementation before edits:

- `application.py`: `_create_v2_collaboration_for_owner`,
  `create_v2_collaboration_with_guest_for_verified_owner`,
  `_canonical_owner_authority`, `_resolve_participant_authority`,
  `add_v2_participant_for_verified_owner`, owner shared/internal append wrappers,
  `append_v2_shared_reply_for_guest`, guest read and DTO builders.
- `authorization.py`: `resolve_verified_owner_collaboration_context`,
  `resolve_verified_summary_viewer`, `_resolve_active_team_member`. Canonical
  account `usr_*`, workspace, and display name come from authenticated current
  member authority. Owner mailbox ownership is checked. Participant actions are
  read/reply/internal_note; current Team membership must match the stored
  `membershipRef`. Create/add use current Team authority before Redis CAS.
- `models.py`: strict v2 thread, participant, source, message and idempotency
  validators. Owner plus at most 15 explicit participants; guests live in separate
  invite/session records. Source is exact Google providerMessageId or custom IMAP
  INBOX/uidValidity/imapUid. Messages contain author kind/name but no author user
  ID, so notification actor user ID must be passed from the capability.
- `mutations.py`: `append_owner_v2_message_idempotently` fingerprints logical
  owner/participant operation including canonical actor ID when present;
  `add_v2_participant` has four bounded CAS attempts and unchanged-participant
  short circuit; `transition_v2_lifecycle` uses exact lifecycle CAS.
  `append_guest_v2_reply` currently appends a newly generated message each call
  through `_append_message`; no stable guest idempotency key exists.
- `guest_session.py`: private guest mutation capability binds session hash,
  invitation, exact thread/workspace/mailbox, display name and session lifetime.
  Guest request origin/CSRF and live session/invitation checks precede mutation.
- `guest_http.py`: reply currently accepts exactly operation/text; no key.
- `redis_store.py`: `_CREATE_V2_THREAD_LUA`,
  `_CREATE_V2_THREAD_WITH_GUEST_LUA`, `_SAVE_V2_PARTICIPANTS_CAS_LUA`,
  `_APPEND_V2_OWNER_IDEMPOTENT_LUA`, `_APPEND_V2_GUEST_REPLY_LUA`, and
  `_TRANSITION_V2_LIFECYCLE_LUA` are canonical mutation boundaries. Owner replay
  recovers the exact message before CAS and supports current/previous HMAC keys.
  Guest script checks live invitation/session inside its atomic mutation.
  `_V2_DISCOVERY_LUA` preflights before `discoveryCommit` and uses bounded indexes.
  `_V2_LUA_COMMON` uses decimal-string wire integers and explicit nullable JSON
  handling for hosted cjson compatibility. Namespace hash tag is
  `{cuevion-collab-v2}`; retention is 180 days. Source/idempotency indexes use
  domain-separated HMAC under CUEVION_COLLAB_INDEX_HMAC_KEY with previous-key
  rotation. Existing append/add scripts refresh thread/source retention; C3C
  emitting mutations must preserve actual PTTL and source expiry instead.
- `test_lua_redis_integration.py`: existing isolated local Redis harness and
  hosted Lua/null regression fixtures; no production connection is needed.

Decision: preflight bounded notification writes inside each emitting Lua script,
before any canonical/discovery write; then commit in the same EVAL. Lua runtime
errors do not roll back earlier commands, so all store types, records and encoded
values must be validated before commit. Cross-store Team validation retains the
existing pre-Redis race semantics; no cross-database atomicity is claimed.

Guest reply will require a canonical 256-bit base64url idempotency key, bind it to
the exact session/thread, validate content fingerprint, and recover the original
message after live guest authorization on retry. No client retry loop.

Resolve/reopen, guest invitation/session management, discovery, Priority and
visible Notifications remain outside the event emission scope. Frontend and
neutral HTTP pre-edit traces are recorded in companion handoff/authority notes.

Process exception: before the request attachment completed loading, the initial
tool batch ran a forbidden `rg --files` filename listing restricted to package,
lock and instruction/config names. It returned only package/lock filenames;
no protected contents were accessed. This was disclosed immediately and is not
repeated. Protected paths are otherwise excluded from inspection and changes.
