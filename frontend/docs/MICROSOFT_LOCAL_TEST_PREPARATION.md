> Actuele keuze (26 oktober 2026): de eerste conceptacceptatietest gebruikt de
> bestaande Cuevion-app en Microsoft-koppeling. Er wordt geen lokale HTTPS-login,
> nieuw account of aparte opslag gebouwd. De eerdere lokale Send-foundation
> hieronder blijft uitgeschakeld en wordt hiervoor niet gebruikt. Zie de actuele
> uitrolvoorbereiding onderaan `MICROSOFT_RESPONSE_DRAFTS.md`.

# Microsoft local test preparation 1

Server-side foundation only, on `codex/microsoft-drafts-recovery`. No live test,
OAuth enrollment, consent, store provisioning or release activation is included.
The JSON example is deliberately invalid/inert: unknown IDs are null and the
recipient allowlist is empty. No application code loads this example file.

## Enforced authorization

Every new existing-draft Send requires the local policy, in addition to all
existing release gates, mailbox lease, credential generation checks, immutable
message index, durable manifest, mutation barriers and exact Sent verification.
The legacy send route remains independently closed.

The local policy requires the authenticated Auth0 member to be the exact owner
user ID, workspace ID and canonical owner email in the policy. It compares the
owned inbox ID, OAuth account ID, bound OAuth owner/workspace and mailbox address.
The mailbox address is fixed in source to `carltricks@live.nl`. Existing Send also
checks Graph `/me` against the account ID and mailbox address before submission;
a matching email alone is insufficient. `mailboxId` denotes the managed Cuevion
mailbox record; `accountId` denotes the verified Microsoft account identity.
Reconnect to another account is rejected. Same-account credential rotation does
not erase historical receipts or consume a new authorization.

The grant permits one exact immutable draft ID, one exact recipient, no Cc/Bcc,
and no attachments. Both the current and final Graph observations are checked;
raw recipient addresses are checked before relying on normalized display data.
Authorization requires integer Unix times, lasts at most 1,800 seconds and is
checked again after storage latency. A monotonic deadline also bounds an active
attempt. No default recipient exists in executable configuration.

A global permanent CAS record, `cuevion:microsoft:local-test-attempt:v1`, consumes
the authorization. It is not keyed by request, draft, mailbox, policy revision or
process. Changing configuration cannot reset it. It persists even after a proven
non-start. A new request ID or draft cannot cause a second attempt. The existing
index is reserved before this claim: a lost claim acknowledgment leaves visible
uncertainty and never authorizes dispatch. No reset/delete/unlock API is added.
Only hashes, attempt ID and the random store identity are added to the ledger;
no recipient address, full policy, body or attachment bytes are stored there.

## Environment and storage boundary

`VERCEL_ENV` must be exactly `development`. Vercel cloud indicators, production
and preview environments, non-loopback Host/Origin and forwarded-host mismatches
are denied. Proxy environment variables are denied. `CUEVION_APP_URL` must equal
the configured HTTPS loopback origin with explicit port. This does not change
existing HTTP origin validation, Auth0 callbacks or OAuth configuration.

`KV_REST_API_URL` must equal the explicit HTTP `127.0.0.1:<port>` policy store URL;
`KV_REST_API_TOKEN` must be present. Remote URLs, fallback stores and mismatched
adapters are denied before Microsoft owner-record access. The store must already
contain the exact marker under `cuevion:microsoft:local-test-store:v1`:

```json
{"v":1,"purpose":"microsoft-local-test-only","storeId":"<64 lowercase hex characters>"}
```

The server does not create this marker or infer that a store is isolated. The
operator must later provision a genuinely separate local durable store, with no
Production credentials, data, backend proxy or shared volume. A loopback address
and marker cannot prove the physical backend of an operator-controlled proxy.
Use persistent, non-evicting CAS storage; retain the marker, claim, index, ledger,
manifest, leases and identity records together. Do not use a fresh database,
restore an older snapshot or delete records to obtain another attempt. If data
is lost or restored ambiguously, leave the stop asserted and investigate offline.
Actual REST CAS interoperability, durability, disk capacity and latency still
require validation against the selected local implementation. Tests use isolated
memory CAS and transactional temporary SQLite; no local REST server was created.

## Independent emergency stop

`CUEVION_MICROSOFT_LOCAL_TEST_STOP` defaults to stopped. Only exact `0` allows a
claim, and only when the durable shared control record is also armed. Its key is
`cuevion:microsoft:local-test-control:v1`; its exact shape is:

```json
{"v":1,"storeId":"<same store ID>","policyHash":"<canonical policy SHA-256>","stopped":false}
```

`policyHash` uses the existing `microsoft_draft_attachment_snapshot.digest`
canonical JSON hashing function. Missing, malformed, stale or unreadable control
records block. Arming/control provisioning is a future explicitly approved
operator action; there is no application endpoint for it. The shared record
allows every process to observe a stop without relying on environment reloads.
Set `stopped` to true durably for an immediate shared stop; use the environment
stop as an additional per-process startup guard. Never delete evidence to stop.

The shared control is read during claim, content checks and immediately before
submission, followed by the existing lease check and a final local time/stop
check. If stop is observed after the send-start CAS, status remains uncertain
even if this worker made zero send calls. Status/history paths do not require an
armed control, live TTL, recipient grant or Send release flag. They still require
the verified owner/account and readable isolated storage. A status verification
may save evidence but performs no Graph mutation and cannot resend.

A stop cannot retract a request already dispatched, or atomically cover the
last control-read-to-Graph-call interval. Likewise external Outlook edits can
race the final observation. Sent verification detects differences afterward; it
does not prevent sending modified content. Missing/non-verifiable MIME or body
proof blocks or remains uncertain under the existing policy. No automatic retry.

## Configuration still required before a later local test

1. Independently verify the real Cuevion owner ID/email, workspace, managed
   mailbox ID and Microsoft account ID. Do not substitute an email for an ID.
2. Obtain explicit approval for the sole recipient. Keep the example's list
   empty until then; approve the exact draft ID separately, without attachments.
3. Provide an isolated durable loopback CAS REST service, a fresh random store ID,
   marker and stopped control, local-only store credentials and authenticated
   owner records. Validate persistence and CAS under restarts before arming.
4. Review a dedicated local HTTPS/authentication/OAuth route setup. Current
   canonical/preview HTTP origin and Auth0 restrictions still reject localhost;
   this implementation intentionally does not weaken them. Microsoft OAuth
   still strips `Mail.Send`, and every relevant source release flag remains off.
   Local login, consent and selective release need a separate approved change.
5. Configure the full policy in `CUEVION_MICROSOFT_LOCAL_TEST_CONFIG`, a short UTC
   time window and exact local origin. Arm only after independent review and
   explicit permission for the one live test. Do not print credentials or policy
   contents in server logs. No actual config or credentials were created here.
6. On uncertainty, discrepancy, timeout or storage failure: assert the shared
   stop, keep evidence and configuration, use status checks only, and investigate.
   Never change request ID, reconnect to reset, or recreate a store to retry.

## Offline verification

- Microsoft backend: 812 tests, including 25 new local-policy tests.
- Independent-process tests: 3 passed (existing Send/migration plus new local
  authorization race and real interpreter restarts).
- Auth/OAuth and Gmail OAuth regressions: 185 tests passed in 5 isolated suites.
- Microsoft client: 46/48 suites passed; the existing failures remain
  `MicrosoftFilteredSenderAction.test.ts` (missing component source pattern)
  and `MicrosoftMailboxFolderRail.test.ts` (`lg:flex` versus `xl:flex`). Neither
  component/test was changed in this round. Send 32/32 and Compose 21/21 passed.
- TypeScript app and node checks, full frontend build, diff whitespace check and
  the 645-file Gmail/IMAP source freeze passed. Build retains non-fatal outdated
  Browserslist and large-chunk warnings.

The original Send fixture stubs the new outer permit to continue testing its
existing state machine in isolation. The new local-policy suite restores the
real permit, owned-mailbox resolver, token-bound authority and generation checks;
only authenticated-member storage, tokens, Graph transport and CAS are fake.
Offline runners remove inherited credentials and deny networking. Test-only
in-memory release and authorization values do not change source flags or a live
environment. Evidence logs are under `/private/tmp/cuevion-local-prep1-*`.
