# Collaboration owner-write first-run monitoring and rollback

## Scope

This runbook is only for the first controlled `owner_write` verification for
the existing single owner and the allowlisted `gmail-carltricksmusic` mailbox.
It does not authorize a deployment, configuration change, additional tester,
multi-user access, public beta, guest access, Team access, frontend migration,
or any append operation. The owner route remains
`CUEVION_COLLAB_V2_HTTP_MODE=owner_read` until a separately authorized change
reaches the activation step below.

## Before the window

Stop unless every item is evidenced:

1. The intended Production deployment is `READY`, with the expected commit and
   no unexpected build or runtime error.
2. `CUEVION_COLLAB_V2_HTTP_MODE=owner_read`; the allowlist-bootstrap and
   owner-write-readiness routes and their temporary tokens/modes are absent or
   disabled.
3. The existing Collaboration v2 Production keyspace-presence gate has been
   rerun and passed. Do not enumerate, scan, print, export, or delete keys.
4. The Production runtime readiness verifier has returned HTTP 200 with exactly
   the three documented `true` booleans while the owner route remained
   `owner_read`. Its temporary mode and token were then removed, a replacement
   deployment reached `READY`, and the readiness route was verified not-found.
5. The allowlists still contain exactly the approved owner and the canonical
   `gmail-carltricksmusic` mailbox. Use one real Gmail source message in that
   mailbox whose provider message ID has not previously been used for this
   Collaboration test.
6. One operator owns the clock, requests, and rollback. Production logs are
   open and filtered to the owner endpoint. No other owner-write testing runs
   concurrently.

Record only deployment/commit identifiers, timestamps, HTTP status, the safe
`created` boolean, collaboration ID, revision, and operation result. Do not
record cookies, CSRF or idempotency headers, owner identity, mailbox secrets,
source message content, HMAC material, or request headers.

## Controlled window: maximum of 10 minutes

1. Set `CUEVION_COLLAB_V2_HTTP_MODE=owner_write` only in Production through the
   approved Sensitive/configuration path and deploy. Start the clock only when
   that deployment is `READY`. End and roll back at 10 minutes even if the
   evidence set is incomplete.
2. Confirm CSRF bootstrap is HTTP 200. Monitor Production logs continuously for
   owner endpoint HTTP 201, 200, 409, 429, 500, and 503 outcomes and provider,
   authentication, storage, or timeout errors.
3. Send one `create` for the approved real Gmail source. Expected evidence:
   HTTP 201, `created=true`, one canonical collaboration ID, and a valid initial
   revision.
4. Repeat the exact same `create` request for the same owner, mailbox, source,
   and state. Expected evidence: HTTP 200, `created=false`, the same
   collaboration ID, and no new record.
5. Perform the canonical source lookup for that same owner/mailbox/source.
   Expected evidence: it resolves to the same collaboration ID.
6. Perform the owner `read` for that collaboration ID. Expected evidence: HTTP
   200, the same canonical source reference and collaboration ID, the expected
   initial state/revision, and no unexpected message.
7. Do not append a shared reply or internal note. Do not exercise
   `append_shared` or `append_internal` in this first window.

Roll back immediately—without waiting for 10 minutes—on any unexpected 5xx,
provider or authentication error, storage error, repeated write, changed
collaboration ID, duplicate response other than HTTP 200/`created=false`, source
lookup mismatch, unexpected 409 or 429, contradictory read, or inability to
observe Production logs.

## Exact rollback

1. Set `CUEVION_COLLAB_V2_HTTP_MODE=owner_read` in Production and deploy.
2. Wait until the rollback deployment is `READY`; do not treat configuration
   submission or build start as completion.
3. Verify `GET /api/collaboration/owner` returns HTTP 405 with `Allow: POST`.
4. Verify an authenticated owner CSRF bootstrap returns HTTP 200.
5. Verify the created collaboration remains available through canonical source
   lookup and owner `read`, with the same collaboration ID.
6. Send one otherwise valid `create` shape while in `owner_read` and verify the
   write is masked as HTTP 404 before write-rate limiting or mutation. Do not
   send an append.
7. Verify both temporary operator routes are disabled/not-found and their
   temporary tokens and modes are absent.
8. Review the complete activation and rollback log window for unexpected 5xx,
   409, 429, provider/authentication errors, repeated writes, or post-rollback
   mutations. Escalate any discrepancy; do not re-enable writes.

Keep the created test Collaboration record as the audit and idempotency
evidence. Do not manually delete Redis keys, source pointers, idempotency state,
or the thread. The approved 180-day single-user private-beta TTL governs their
natural expiry.
