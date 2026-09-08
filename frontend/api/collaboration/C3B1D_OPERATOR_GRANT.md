# C3B1D dry-run operator grant

The existing authenticated owner POST route `/api/collaboration/owner` can issue
one short-lived identity attestation through the exact operation
`issue_migration_operator_grant`. It never runs or imports migration code. No
permanent UI, generic API credential, session export, or apply authority is added.

## Gates and issuance

`CUEVION_COLLAB_DISCOVERY_MIGRATION_OPERATOR_MODE` accepts the exact values `off`
and `dry_run_grant`. Absent, malformed and unknown values fail closed to `off`.
The existing HTTP mode must also permit the owner route (`owner_read` or
`owner_write`). Neither setting is enabled by this change.

Issuance retains the existing POST, exact-origin, authenticated Auth0 owner,
owner allowlist, session-bound CSRF, bounded JSON, no-store and nosniff protections.
The request body contains only `{"operation":"issue_migration_operator_grant"}`.
Query parameters cannot supply the grant, identity, scope or migration options.
Guests and missing/expired/revoked owner sessions cannot issue grants.

The existing owner allowlist binds the verified `(issuer, authentication_version,
subject)` tuple, not an email. The mailbox allowlist extends that tuple with the
exact mailbox ID. Issuance additionally obtains the current canonical `usr_*`
account/member and workspace from the authenticated authority, checks the exact
owner context, and reads managed-mailbox configuration server-side. It projects
only unique, valid, allowlisted Google/custom-IMAP mailbox IDs and providers.
The bounded scope is at most 32 mailboxes; missing, ambiguous or malformed
authority fails closed. No submitted user/workspace/mailbox is accepted.

A dedicated policy reuses the existing Redis GCRA security counter infrastructure:
burst three, then one additional issuance per 300 seconds, per canonical user and
workspace. Thus an idle bucket can admit five requests in a half-open 15-minute
interval, including its initial burst. The counter uses the existing `owner-rate`
key family with the `operator_grant` purpose and a distinct HMAC domain. Existing
bootstrap/read/write policies and keys are unchanged. The limiter returns a
maximum 60-second public Retry-After hint to retain the existing HTTP contract;
server enforcement remains 300 seconds. Clients must not poll or automatically
retry. There is no grant store, nonce store, cleanup job or new key family.

The existing security limiter uses EVAL/TIME/GET/PTTL/SET PX. These are security
counter operations, not migration operations. Issuance performs no SCAN,
discovery enrollment, canonical-thread mutation or source-pointer mutation.

## Wire and cryptography

The token is `mog1.<canonical-base64url-payload>.<canonical-base64url-signature>`.
The complete canonical ASCII JSON payload contains exactly:

| Field | Meaning |
| --- | --- |
| `version` | Integer `1` |
| `purpose` | `collaboration_discovery_migration_dry_run` |
| `userId`, `workspaceId` | Canonical account identifiers from current authority |
| `issuedAt`, `expiresAt` | Integer server Unix seconds; lifetime 600 seconds |
| `nonce` | 32 random bytes in canonical unpadded base64url |
| `mailboxes` | Sorted unique objects containing only `mailboxId`, `provider` |
| `configurationBinding` | Keyed binding to the current origin and owner/mailbox rollout configuration |

No emails, display names, provider subjects, session facts, cookies, Auth0 tokens,
CSRF material, Redis/mailbox credentials, HMAC keys, guest links, message content,
source references or participant details enter the payload.

The existing owner-CSRF root is an already-required canonical base64url secret
of at least 32 bytes. It already derives separate HMAC-SHA256 subkeys for CSRF
signing and session binding. This bridge derives a new subkey with
`HMAC-SHA256(root, "cuevion/collaboration/migration-operator-grant/v1")`, then signs
`mog1.` plus the complete payload segment. It does not change any CSRF primitive,
key or existing derivation. No new Production secret is required by this model;
this implementation has not inspected Production secret values.

The configuration binding derives a separate HMAC-SHA256 subkey from the current
allowlist root using `cuevion/collaboration/migration-operator-configuration/v1`,
then authenticates the origin and sorted complete owner/mailbox allowlist
snapshot. Rollout entry changes or allowlist-root rotation invalidate grants.
The verifier accepts only the current CSRF root, so signing-root rotation also
invalidates grants; it does not accept the previous CSRF key as a grant key.

Verification uses constant-time signature comparison, strict duplicate/unknown
field rejection at every object level, canonical reserialization, canonical
base64url, exact integer types and canonical account IDs. Expired, future-issued,
excessively long-lived, unsigned, wrong-version and wrong-purpose tokens fail.
There is no clock-skew allowance or automatic refresh. Tokens are limited to
16 KiB. Error contracts contain fixed codes only and never rejected values.

## Replay and current authority

A grant is a bearer attestation during its ten-minute lifetime. Bounded replay is
accepted for this dry-run-only purpose. It carries no write/apply authority and
cannot be submitted as an owner cookie, CSRF token or general owner capability.
Session logout alone does not revoke an already issued grant; expiry and current
account/mailbox/Team/rollout revalidation bound its authority. A future apply
mechanism requires a separate review, purpose and replay policy.

`verify_operator_grant` performs no storage I/O and returns an opaque immutable
`VerifiedMigrationOperatorContext`. Only explicit
`discovery_migration.run_page_with_operator_grant` enters the manual engine. Every
invocation, including checkpoint response replay, verifies the raw token afresh
and revalidates:

- Current active canonical account, verified primary email, workspace and exact
  active workspace membership, via `read_current_account_by_user` and the existing
  read-only PostgreSQL authority.
- The selected mailbox is inside the signed scope and still belongs to that
  account through the existing current managed-mailbox compatibility contract.
  The current canonical email is used only after by-ID account authorization.
- Current Team entitlement and invitation reference through the existing bounded
  resolver. An owner need not be enrolled in Team; malformed/unavailable Team
  authority fails closed.
- The unchanged C3B1C Lua checks for canonical threads, exact workspace/user,
  source binding, current entitlement, discovery capacity, TTLs and byte bounds.

No browser-session facts or owner-session capabilities are synthesized. The
existing browser-context `run_page` retains its authentication contract. Both
entry points use the same private one-page engine and unchanged Lua.

The grant adapter requires explicit `enabled=True`, exact `dry_run=True`, and an
integer `scan_budget` between 1 and 25 (default 25). Each invocation still executes
at most one SCAN, processes at most five candidates and retains the C3B1C byte
bound. It cannot accept an apply checkpoint. A token does not raise these limits,
authorize a second page, or schedule further work.

## Future private transport and manual operation

This change performs no Production activity. A separately authorized operator
will obtain the existing Production environment through authenticated Vercel CLI
into a private local file, without printing it. No env-pull code is added here.

The user signs into Cuevion normally. A connected browser operator requests the
existing owner CSRF bootstrap and then the grant operation using that same
session. Capture the HTTP response directly into a new private local file, without
rendering its grant in ordinary UI or returning it to chat/terminal/log output.
Never use query strings, clipboard copy/paste, shell arguments or console logging
to transfer the grant. If direct private capture is unavailable, stop.

Use a new absolute operator directory outside the repository, mode 0700, with no
symlink components. The captured grant response must be a regular owned file,
mode 0600. `read_private_operator_grant(path)` validates this and reads the exact
`{"ok":true,"data":{"grant":...,"expiresAt":...}}` response envelope. It neither
prints nor persists the token. The signed expiry is authoritative; the envelope
expiry is only response metadata. Keep all parent directories trusted against
concurrent replacement by another process with the operator's own privileges.

In the already configured operator process, read the private response and pass
the returned token directly in memory to `run_page_with_operator_grant`, supplying
the chosen verified mailbox and existing parsed owner-security configuration.
Use a separate new DRY-RUN checkpoint and the original C3B1C private-checkpoint
rules. Delete the temporary grant file in a `finally` block after the attempt;
never save the grant in a checkpoint. Do not delete the checkpoint.

The first Production experiment still requires separate authorization for
exactly one invocation with cursor `None` and budget 25, then an unconditional
stop even for an empty page, retry, capacity result or non-null next cursor.
Only privacy-safe aggregate results and actual command accounting may be reported.
Read the authoritative `C3B1C_DISCOVERY_MIGRATION.md` for checkpoint, candidate,
byte, quota and zero-write proof details. This bridge does not authorize apply.

## Local verification

Direct tests cover HTTP issuance/authentication/origin/CSRF/default-off behavior,
strict crypto and parsing, private response capture, current account/mailbox/Team
authority, rotation invalidation, and the manual grant adapter. Disposable local
Redis tests cover rate-limit concurrency/refill and unchanged old policies, plus
real C3B1C Lua no-write counters, serialized values and absolute TTL preservation.
Focused owner identity/lifecycle, summaries, migration, owner/guest HTTP/security,
and guest replacement/idempotency/concurrency regressions are run separately.
No protected-file-reading tests, dependency installs, frontend build, Production
grant issuance, Production Redis, push or deploy are part of this slice.
