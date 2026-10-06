# Phase T1 — Tester Access Security Requirements

## Status

T1 tester access is a separate authority from Team membership. This document is
normative for the tester-invite implementation. It does not activate a route,
Auth0 registration source, account writer, or onboarding behavior by itself.

## Account-provisioning boundary

A tester invite authorizes creation of one new standalone Cuevion account with:

- one new Cuevion user;
- one current verified email;
- one Auth0 OIDC authentication identity;
- one new workspace created by that user;
- one active `owner` workspace membership; and
- one `INITIAL_ACCOUNT_CREATED` security event.

Provisioning must use the existing `PostgreSQLInitialAccountRepository`
transactional aggregate. It must not use Team invitee provisioning, which creates
a `member` membership in an existing workspace.

The callback must re-prove the exact live tester invitation and the exact Auth0
verified email before account creation. A pre-existing current verified-email
claim or Auth0 `(issuer, subject)` authority must fail closed and must never create
a second owner workspace.

## Tester-admin authority

A workspace `owner` role is not tester-admin authority. Tester invite issuance
and cancellation require a separate server-side allowlist of immutable Cuevion
user IDs. Newly provisioned tester owners therefore cannot issue further tester
accounts unless their immutable user ID is separately allowlisted.

## Raw invite bearer handling

A raw tester invite token is a credential.

The canonical invite link uses fragment-only transport:

`https://app.cuevion.com/#tester_invite=<raw-token>`

The raw bearer MUST NOT be accepted from the query string. Fragment transport is
required so the first HTTP request to Cuevion does not contain the bearer in the
request target.

On the first client parse:

1. the credential must be validated for canonical shape;
2. every `tester_invite` occurrence must be removed with `history.replaceState`
   synchronously before any tester-invite network request;
3. duplicate, mixed, malformed, or non-canonical transport must fail closed after
   scrubbing; and
4. the raw token may exist only in ephemeral in-memory state needed for the next
   same-origin exchange.

The raw bearer MUST NOT be written to:

- browser history after first parsing;
- `localStorage` or `sessionStorage`;
- application state persistence;
- analytics, telemetry, breadcrumbs, crash reports, or diagnostics;
- application logs or exception text;
- URL query parameters;
- Auth0 authorization parameters; or
- any redirect or referrer-bearing URL.

The future auth handoff must exchange the in-memory raw token through a
same-origin, `no-store` server request for server-bound intent. Auth0 may receive
only the existing short-lived opaque registration grant, never the raw tester
invite bearer.

Server-side invitation storage must retain only the SHA-256 digest of the raw
bearer. The raw bearer may be returned once to the tester-admin caller when the
invite is created, but must not be persisted.

## Invite lifecycle

Tester invitation states are closed:

- `invited`
- `cancelled`
- `provisioned`

Only one live `invited` record may occupy a canonical recipient slot. A
`provisioned` recipient is terminal for that tester-invite authority. Cancel and
provision transitions must compare the exact current stored record and mutate the
token, invitation, and recipient projections atomically.

Invite IDs use the `tsti_` namespace and must never be accepted as Team `tinv_`
authority.

## Auth0 registration authority

The existing single-use Auth0 registration-grant mechanism is the required
registration primitive. Tester support must be source-specific:

- `team_invite` accepts only Team invitation identifiers;
- `tester_invite` accepts only Tester invitation identifiers.

Adding `tester_invite` to a shared source set without source-specific authority-ID
validation is not sufficient.

Every grant remains bound to the exact canonical email and Cuevion Auth0 client,
has a bounded lifetime, and is atomically consumed once.

## Environment separation

Production and Preview tester invitations, registration grants, Auth0
configuration, session state, and account databases must remain isolated. No
tester credential or grant from one environment may authorize registration in
another.
