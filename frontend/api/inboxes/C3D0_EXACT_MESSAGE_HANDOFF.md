# C3D0 exact cold-message prerequisite

## Baseline and pre-edit trace

Baseline passed on `perf-1`: local HEAD and remote main both
`353e35e79ca5f8880f00d7cd574c5ed43d35590a`, staging empty, exactly the three
pre-existing protected status entries. Only exact non-protected files were
inspected; protected implementations were not used to fill interface gaps.

The authority and frontend traces were reported before their implementation;
the provider trace was completed before provider edits.

- `api/auth/runtime.py` through the existing
  `user_config_store.resolve_authenticated_member_authority` revalidates the
  current Auth0 account/session and current workspace membership.
- `user_config_store.read_user_config_for_authenticated_member` retains that
  canonical member while reading the account's durable mailbox configuration.
  `resolve_owned_managed_inbox_record(..., include_member_authority=True)` checks
  config ownership and exact unique managed mailbox ID. Caller email, workspace,
  provider credentials and account identity are never accepted.
- `inboxes/authenticated_gmail.py:resolve_gmail_context` consumes that already
  resolved mailbox and validates current connected provider configuration and
  the durable owner-bound token. Its existing expired-token preflight may refresh
  credentials; the exact message provider request itself is never retried.
- `inboxes/authenticated_imap.py:resolve_authenticated_imap_mailbox` checks current
  mailbox configuration and credential-version agreement before returning
  connection settings. Its fresh member context must equal the initial context.
- `auth/http.py` supplies duplicate-safe headers, exact canonical Host and Origin,
  fixed no-store/nosniff JSON responses. `team/http_security.py` supplies generic
  non-simple JSON request security. `collaboration/http_adapter.py` and
  `http_boundary.py` supply bounded framing, strict UTF-8/JSON and duplicate-key
  rejection. No Collaboration owner allowlist or owner CSRF bootstrap is used.
- `collaboration/owner_rate_limit.py` supplies the existing Redis GCRA engine and
  validated HMAC configuration. The new inbox limiter uses its own domain/key,
  30 requests/minute, burst 10, scoped to current user/workspace/operation.
- `inboxes/gmail_snapshot.py` proves the existing exact Gmail raw-message GET
  and MIME conversion pattern. Its folder-specific snapshot recovery is not an
  arbitrary cold-open API. The new provider path performs one exact GET and
  checks returned message ID before conversion.
- `frontend/imap_connect_preview.py:to_message_preview` is the shared existing
  MIME-to-displayable-message adapter. Both providers reuse it. Its
  `connect_mailbox_with_settings` uses the existing public-network/TLS policy.
- `inboxes/imap_uid_validity.py` parses live UIDVALIDITY exactly;
  `imap_snapshot.py` establishes exact returned UID/literal validation. The new
  helper selects INBOX read-only and uses one bounded UID FETCH with BODY.PEEK.
- `WorkspaceShell.tsx:MailMessage`, `MailMessageSeed`, and exported
  `normalizeMailMessage` define the existing rendering model. `inboxEngine.ts`
  provides `applyLiveThreadIdentity` for canonical provider context.
- `WorkspaceShell.tsx:setSelectionState` accepts source mailbox, folder and exact
  source object. `setMailboxStore` already fences account/workspace persistence
  scope; mailbox connection keys/epochs change synchronously with configuration.
  The new helper requires canonical user/workspace IDs and an explicit current
  config revision/generation, checked within the publication updater.
- `WorkspaceShell.tsx:mergeLiveInboxMessages` has fuzzy identity fallbacks and is
  not used for exact augmentation. The new helper preserves all other messages
  and folders and deduplicates only canonical mailbox/provider/source identity.

## Endpoint

`POST /api/inboxes/fetch-exact-message`, credentials included, JSON, no-store.
No query string. Maximum request body 2,048 bytes. Closed request:

```json
{"v":1,"mailboxId":"managed-mailbox-id","sourceRef":{"provider":"google","providerMessageId":"exact-message-id"}}
```

Or `sourceRef` is exactly
`{provider:"custom_imap",folder:"INBOX",uidValidity:"456",imapUid:"123"}`.
UIDVALIDITY uses the existing positive decimal string contract (at most 20
digits); IMAP UID must be a positive uint32. No normalization or guessed IDs.

Success is exactly `{v:1,mailboxId,sourceRef,message}`. The message uses the
existing renderer seed fields, including text/HTML, attachment metadata, flags
and canonical provider identity. No raw provider response, credential, token,
account configuration, classifier sidecar or priority mutation is returned.

Failure is `{error:{code,message}}` with a fixed safe message. In particular,
`source_changed` means the IMAP UIDVALIDITY no longer matches; `message_not_found`
means the exact target is absent; `invalid_response` means identity or provider
payload validation failed. No substitute message is opened.

The route does no provider flag/label mutation, mailbox resync, notification
read, Collaboration mutation, polling or retry. Both provider payload and public
response sizes are bounded. Oversized/unverifiable messages fail safely.

## C3D integration seam

The new frontend modules are `src/lib/exactMailboxMessageApi.ts` and
`src/lib/exactMailboxMessageStore.ts` under `frontend/`. They do not import the
protected inbox client. Existing Notifications UI and local read authority are
unchanged by this prerequisite.

C3D must capture current canonical account/workspace, exact mailbox config
revision and navigation generation, then:

1. Reuse an already loaded exact provider source if available.
2. Otherwise call `fetchExactMailboxMessage(mailboxId, sourceRef, {signal})` once.
3. Revalidate response source and normalize the returned existing mail seed with
   `normalizeMailMessage` and `applyLiveThreadIdentity` in the current workspace.
4. Use the new reducer inside the current state publication updater, providing
   the current scope/generation. A stale ticket returns unchanged state.
5. Select the reducer's exact message/folder/mailbox result. The helper never
   replaces a whole mailbox with the fetched item or guesses by subject/sender.
6. C3D then owns exact Collaboration read, activity display and only afterwards
   server notification mark-read. None of those steps is wired in C3D0.

## Bounds, local measurements and verification

Provider MIME payloads are limited to 6 MiB and Gmail transport JSON to 8 MiB.
The HTTP success envelope is limited to 8 MiB. Display metadata is allowlisted:
at most 100 attachments, 1,000 unique Gmail labels and 10,000 body paragraphs.
Oversized or incomplete messages return a safe failure. Network timeout is
20 seconds; there is no provider retry or list fallback.

Google uses exactly one
`GET /gmail/v1/users/me/messages/<percent-encoded exact ID>?format=raw`.
It accepts no input thread ID; a returned thread ID enriches rendering only.
Returned ID equality is checked before the existing MIME adapter runs. Current
Gmail labels select the existing display folder. The canonical MIME/RFC display
ID is preserved to match existing snapshots; it is never source authority.

IMAP uses one existing public-destination TLS connection/login, one read-only
INBOX selection (`imaplib.select(..., readonly=True)`, i.e. EXAMINE), one
UIDVALIDITY response-code read, one
`UID FETCH <uid> (UID FLAGS RFC822.SIZE BODY.PEEK[]<0.6291457>)`, and logout.
The parser accepts UID/FLAGS/SIZE before or after the single body literal, requires
each exactly once, verifies the returned UID and complete RFC822.SIZE/literal
length, and rejects truncation. A UIDVALIDITY mismatch performs zero FETCH calls.
No SEARCH, sequence-number fetch, flag store, label mutation or preview occurs.

Auxiliary call counts were measured with real existing authority resolvers and
mocked durable stores/provider boundaries:

| Successful path | Current-member resolver | Account config reads | Credential reads | Exact provider fetches |
| --- | ---: | ---: | ---: | ---: |
| Google, valid token | 1 | 1 | 1 owner-bound token record | 1 |
| IMAP | 2 | 2 | 1 version-checked secret record | 1 |

The IMAP second resolution is intentional reuse of its existing authority, with
full member equality checked before fetch. These are boundary-call counts, not
claims about the lower-level session/account graph's individual Redis commands.
An expired Gmail token can use the existing credential refresh before the sole
message GET; provider errors never trigger a second message GET.

The limiter performs one outer EVAL. A new allowed bucket executes TIME, GET,
SET internally; an existing allowed bucket adds PTTL. Isolated Redis tests prove
ten immediate requests, then denial, and independent workspace budgets. This
adds a separate inbox-rate key without changing notification storage or policy.

Validation used only mocks and temporary Unix-socket Redis instances:

- New backend: 58 tests (26 HTTP/current-authority, 4 limiter, 28 provider).
- New frontend: 53 tests (32 strict API and 21 publication), plus two actual mocked-provider
  MIME outputs passed through the TypeScript parser and canonical seed adapter.
- Full frontend TypeScript: application 90 roots/152 sources, node config
  2 roots/57 sources, zero diagnostics. A source-level `satisfies` check proves
  the canonical seed fits `normalizeMailMessage` without a cast or runtime import.
- Existing backend: 391 tests. This includes all 78 C3C notification tests, all
  122 local Lua regressions, C3B2/C3B2.1 discovery repair/summaries, account/workspace
  application authority, and Gmail/IMAP message conversion.
- Existing frontend: 18 suites covering owner source/read/write/guest flows,
  summaries, Priority, mailbox identity/polling, full-message modal and performance.
  Existing performance assertions retain identity work 10,000 to 100 and keyword
  scans 29,000 to 2,900 in their fixture. No existing production source was edited.
- The guards reported zero protected filesystem attempts and zero non-local
  network attempts. Every Python invocation disabled bytecode writes. OAuth and
  the known broad import-safety test that touches protected paths were excluded
  before execution; broad root TypeScript and broad npm test were not run.

Detailed local logs and guard runners are in `/private/tmp/c3d0-regressions.txt`,
`/private/tmp/c3d0-python-regressions.py` and
`/private/tmp/c3d0-frontend-validation.cjs`. Direct new backend modules are
`api.inboxes.test_exact_message_http`, `api.inboxes.test_exact_message_rate_limit`
and `api.inboxes.test_exact_message_provider`; frontend tests are the two new
`exactMailboxMessage*.test.ts` files. The Redis test class uses an isolated
temporary socket with TCP disabled, despite the inherited fixture class name.

No production provider calls, notification integration, push, deployment,
dependency installation or environment changes were performed. C3D can resume
against this local capability for both providers, with the payload bounds and
explicit current-scope publication requirements above.
