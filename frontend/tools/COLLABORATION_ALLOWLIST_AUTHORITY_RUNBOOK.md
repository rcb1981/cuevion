# Collaboration v2 allowlist authority runbook

## Purpose

`tools.collaboration_allowlist_authority` is an operator-only command that
revalidates one existing Cuevion/Auth0 server session, verifies one to five
explicitly selected currently owned mailbox IDs, and emits only the canonical
owner and mailbox allowlist digests plus safe counts. It is not an HTTP handler,
is not imported by application startup or owner routes, and performs work only
when invoked as a command.

This command resolves authority; it does not activate `owner_read`, change
Vercel, generate a key, deploy, or call a production Collaboration route.

## Why browser identity is not authority

Browser localStorage, JSON, issuer, subject, user ID, workspace ID, owner email,
and browser-visible session fields are not accepted. The only authentication
input is an opaque existing server-session cookie value. The command places it
under the canonical `api.auth.session_store.SESSION_COOKIE_NAME` in one minimal
in-process `Cookie` header and calls
`api.auth.runtime.resolve_authenticated_member_session`. Only the resulting
exact `AuthenticatedMemberSessionContext` supplies issuer, session schema
authentication version, subject, and the current account member/workspace
binding.

## Preconditions

- Run from the deployed serverless project root, `frontend/`, in the reviewed
  operator environment.
- Use an existing Cuevion/Auth0 server session for the one intended owner.
- Select between one and five canonical mailbox IDs. A supplied ID is only a
  selector and must resolve as currently owned.
- Make the existing production session-store, account-reader, user-config/KV,
  and `CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY` configuration available through
  the approved secret-management path.
- Ensure the allowlist HMAC key is distinct from owner-CSRF, rate-limit,
  Collaboration-index, mailbox-encryption, Auth-session, and guest/invitation
  secrets. The command does not create or compare those other secrets.

Do not proceed if the operator environment cannot provide the existing reviewed
read/revalidation authorities or protected secret injection.

## Dry run

Invoke:

```sh
python -m tools.collaboration_allowlist_authority --dry-run
```

Then enter one closed JSON object on standard input and finish input with EOF:

```json
{"mailboxIds":["synthetic.mailbox-1"]}
```

The example is synthetic. For an operational dry run, enter only the explicitly
reviewed canonical mailbox IDs. Unknown fields, duplicate or empty selectors,
malformed IDs, and more than five IDs fail closed.

Dry run validates selector cardinality, command syntax, and required import
contracts. It does not read the session-cookie environment variable, the
allowlist key, session KV, account database, or mailbox configuration, and it
makes no network call.

## Secure session credential injection

Never pass the session credential as a command argument, paste it into a file,
or include it in a diagnostic command. The command has no `--cookie` option.
Inject it through the dedicated environment variable without echoing the value,
for example by reading it silently in the current protected operator shell:

```sh
read -r -s CUEVION_COLLAB_OPERATOR_SESSION_COOKIE
export CUEVION_COLLAB_OPERATOR_SESSION_COOKIE
```

Use the approved secret-management mechanism to inject the existing
`CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY`; do not type or print that key in command
history. Do not redirect the process environment, standard input, standard
output, or standard error to a persistent file.

## Explicit mailbox selection and server-side revalidation

Real resolution requires both independent guards:

```sh
export CUEVION_COLLAB_AUTHORITY_CONFIRM=RESOLVE_CANONICAL_COLLAB_V2_ALLOWLIST_AUTHORITY
python -m tools.collaboration_allowlist_authority --execute-authority
```

Enter the same closed `mailboxIds` JSON shape on standard input and finish with
EOF. There is no wildcard, all-mailboxes option, email-based identity input, or
automatic enrollment.

For each selector, the command uses the same
`api.user_config_store.resolve_owned_managed_inbox_record` call used by the
Collaboration verified-owner authorization path, including retained member
authority. The returned member must exactly match the freshly revalidated
session member, and the returned canonical mailbox ID and supported provider
must match the selector. An unknown, ambiguous, malformed, unavailable, or
not-owned mailbox aborts the entire command.

Successful session resolution is read-only: it loads the server session and
re-reads current-account authority. Existing canonical failure cleanup is not
read-only. A missing, expired, binding-invalid, revoked, stale-account, or
otherwise invalid session can cause the existing runtime to delete its session
record and return a clear-cookie instruction. The command adds no mutation and
does not emit or apply that response cookie.

## Generate digests without displaying raw identity

After all authority checks succeed, the command builds the existing
`tools.collaboration_allowlist` input only in process memory and calls its
canonical parser, HMAC-key parser, and allowlist generator. It does not write an
identity file or copy the digest derivation.

Expected successful output contains only:

```text
owners: 1
mailboxes: <safe-count>
ownerDigests: 1
mailboxDigests: <safe-count>
CUEVION_COLLAB_V2_OWNER_ALLOWLIST=<digest-list>
CUEVION_COLLAB_V2_MAILBOX_ALLOWLIST=<digest-list>
```

It does not print issuer, subject, owner email, user/workspace/session IDs,
credential digest, session credential, canonical mailbox IDs, or the HMAC key.
Failures are fixed redacted error codes and contain no provider, database, host,
identity, mailbox, session, or secret value.

## Abort conditions

Abort without using output if any guard, selector, runtime contract, session
revalidation, current-account check, mailbox ownership check, HMAC-key parse, or
canonical generation step fails. Also abort if output counts differ from the
reviewed one-owner and one-to-five-mailbox scope. Never work around a failure by
supplying browser identity fields or by manually constructing a digest.

## Session and HMAC-key hygiene

- Keep the session credential and canonical identity fields only in process
  memory; never log, echo, persist, or place them in subprocess arguments.
- Do not create `.env`, `identity.json`, `owner.json`, `mailboxes.json`, or a
  temporary repository file.
- Do not print, rotate, generate, or persist the allowlist HMAC key here.
- Do not save standard input or shell environment dumps.
- Treat the digest output as deployment configuration and send it only through
  the approved protected operator channel.

## Post-run cleanup

Immediately clear the operator-only credential and confirmation from the shell;
clear the allowlist key according to the approved secret-injection mechanism:

```sh
unset CUEVION_COLLAB_OPERATOR_SESSION_COOKIE
unset CUEVION_COLLAB_AUTHORITY_CONFIRM
unset CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY
```

Do not preserve command input, environment snapshots, or raw identity notes.

## Next step

Actual production authority execution, production digest generation, and a
matched Vercel `owner_read` configuration remain separately authorized
operations. Deploy the exact allowlist key and digest lists together with the
reviewed owner-read configuration, then verify allowed and denied cases. Do not
enable `owner_read` from this runbook step alone.
