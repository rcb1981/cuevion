# Temporary initial OWNER dual-identity migration — Phase 1

This change prepares a migration; it does not perform a Production migration,
switch the default login connection, change Auth0 settings, or revoke sessions.
The verified initial OWNER keeps the existing Email OTP identity throughout.
Remove the two migration routes and supporting migration modules after the
separately authorized final cutover. Keep the identity inventory diagnostic.

## Safety proof completed before implementation

1. `api/auth/account_authority.py` selects candidates by exact issuer/subject,
   then revalidates that exact candidate. Its `LIMIT 2` detects ambiguous
   workspace membership for that identity, not multiple identities for a user.
   `cuevion_db/postgresql_current_account_repository.py` also joins by exact
   issuer/subject. Neither query joins all identities through a user.
2. `cuevion_db/account_schema.py` and migration `0001_account_schema_1.py`
   enforce identity ID and global issuer/subject uniqueness. They do not impose
   uniqueness on `authentication_identities.user_id`. The same-user email
   foreign key permits two identities referencing the same verified-email ID.
3. No global OWNER identity-count invariant exists. The initial-account graph
   trigger fires on an initial-operation insert; it references the original
   identity by ID. An additional identity does not rewrite that frozen graph.
4. The Team invite writer's exact-one graph check applies to invite provisioning.
   The runtime's `_require_provisioned_team_access` is conditional on stored
   `team-invite-oidc` provenance. The initial operator provenance bypasses that
   branch; the migration additionally rejects every subject-derived invite ID.
5. The callback creates ordinary sessions with the exact validated issuer and
   subject used for that login. Session restoration reads the unchanged user,
   workspace, membership and security epoch. Both identities therefore retain
   the same canonical authority while carrying their own provider binding.
6. Membership and ownership use immutable internal user/workspace IDs. An
   identity insert does not change these records. No INSERT trigger requires a
   security-epoch update. The old identity, all IDs, role, versions and epoch
   remain unchanged.

These checks were exercised against the existing decoders/session primitives
before implementation, then against a disposable PostgreSQL cluster with the
actual schema and triggers. The generic future linking/rotation discussion in
`api/auth/AUTH_ACTIVATION_REQUIREMENTS.md` describes an inactive broader design.
This explicitly scoped Phase 1 preserves the existing session; ordinary login
rotation is unchanged, and final-cutover revocation remains a separate step.

## Routes and transaction

- `POST /api/auth/passkey-migration/start`: same-origin fetch with an empty body;
  requires the canonical production host, exact Origin and `Sec-Fetch-Site:
  same-origin`. No query string, request selectors, or connection override.
- `GET /api/auth/passkey-migration/status`: OWNER-only, same-origin fetch, no
  query or body. Requires same-origin fetch metadata; Origin may be omitted by
  the browser for GET, but must match when present.
- `GET /api/auth/callback`: the existing callback has an explicit migration
  branch after the existing OAuth transaction consume and token verification.
- `GET /api/auth/identity-inventory-diagnostic`: retained without broader access.

Both new endpoints require `VERCEL_ENV=production`, a valid first-party session,
and canonical proof of one active initial OWNER, one user, one verified primary
email, one workspace and one membership. Start requires exactly one active
`email|…` identity with method `email_otp` and stored provenance
`cuevion_first_account_operator_v1`. Status permits that original identity plus
one active `auth0|…` identity with method `oidc`. No extra historical, disabled,
invited, suspended or conflicting account records are accepted. Pending Team
invitations, incomplete member provisioning, OWNER recipient invitation state
and current invite continuation cause a closed failure.

Start creates a random opaque migration ID and a normal encrypted OAuth cookie.
The cookie contains only that ID and the existing state/nonce/PKCE transaction.
The server-side, purpose/version-separated HMAC binding pins the full current
session, user ID, workspace ID, security epoch, exact old issuer/subject,
canonical email, complete account-row fingerprint and OAuth transaction digest.
The candidate expires after 600 seconds; it cannot outlive the OWNER session.
The callback uses the existing atomic Redis NX replay marker. Expired, consumed,
tampered, substituted-session, or changed-account proofs cannot insert an identity.
Only bounded expiry/replay status metadata can remain until session expiry.

The migration authorize URL always forces
`connection=Username-Password-Authentication` and `prompt=login`. The existing
signature, tenant issuer, audience, nonce, state, PKCE and verified-email checks
run unchanged. The target must be a different `auth0|…` subject in the exact same
tenant with exactly the same canonical verified email. Matching email alone
never authorizes the operation: the original OWNER session and frozen graph
must also be proven. The callback preserves the original session and redirects
to `/?passkey_migration=identity_added`; no passkey-enrollment claim is made.

## PostgreSQL write and operational prerequisites

The temporary writer uses the existing
`CUEVION_AUTH_ACCOUNT_WRITER_DATABASE_URL`. Missing configuration fails closed;
it never substitutes the reader credential, and rejects identical reader/writer
URLs. Existing TLS and connection validation are reused. No grants, schema,
credentials, or environment values are changed by this commit.

The configured writer must already have SELECT access on the five account
tables, INSERT on `authentication_identities`, and privileges permitting
`LOCK TABLE … IN SHARE ROW EXCLUSIVE MODE` on those five tables. PostgreSQL
permits that lock mode with UPDATE, DELETE or TRUNCATE privilege; PostgreSQL
versions supporting MAINTAIN also accept that privilege. See the official
[LOCK privilege rules](https://www.postgresql.org/docs/current/sql-lock.html).
This is an operator prerequisite to check; it is not authorization to broaden grants.
If existing writer grants are insufficient, the operation fails without a write.

One non-autocommit READ COMMITTED transaction locks those tables in a fixed
order. Ordinary SELECTs continue; competing account writes wait or time out.
The locked snapshot must exactly match every captured row, including all row
versions and the security epoch. The target issuer/subject must be globally
unclaimed. The writer inserts exactly one active identity, using the standard
OS-random canonical `aid_…` generator, the original user/email IDs, `oidc`,
`last_used_at=NULL`, and `row_version=1`. OIDC is the existing correct enum for
Auth0 authentication; this does not assert that the provider used a passkey.

Before commit, the existing current-account SQL/decoder resolves the NEW
issuer/subject and must return the same complete user, email, workspace and
OWNER membership. A second inventory proves every original row unchanged and
exactly one identity added. A failed post-write proof rolls the insert back.
Concurrent migrations cannot both commit; retries cannot duplicate an identity.
There is no UPDATE, DELETE, rebind, epoch bump, or session mutation in this writer.
An uncertain commit is never automatically retried; read status before restarting.

Primary Redis reads bracket Team/config checks and are repeated before the
insert and before commit. The original session is reread at both points with
fresh time and a 20-second callback work bound. PostgreSQL and Redis do not form
a distributed transaction: this proves unchanged Redis state at the checked
boundaries, not that no future invitation or session revocation can occur. The
PostgreSQL account-graph preconditions and post-write proof are fully atomic.

The existing canonical security-event schema supports only
`initial_account_created`, requires an immutable initial-operation reference,
and has an append-only initial-event graph. It cannot represent this migration
without inventing an event enum/operation. No misleading initial-account event
or generic logging framework is added. The new canonical identity row and the
temporary bounded transaction/status record provide the available evidence.
No token, authorization code, password, passkey, or cookie is logged.

## Normal login configuration

`CUEVION_AUTH0_LOGIN_CONNECTION` accepts exactly these case-sensitive values:

| Value | Normal and Team-invite login |
| --- | --- |
| Absent | `email` (existing behavior) |
| `email` | Email OTP |
| `Username-Password-Authentication` | Auth0 database connection |
| Empty, whitespace, malformed or other value | Fail closed |

Browser/query parameters cannot choose a connection. This commit sets no
Production value and enables no Auth0 connection. With the currently disabled
database application connection, Auth0 will reject the migration authorization
until the operator manually enables it in the later rollout. Password/passkey
settings and enrollment remain Auth0 responsibilities.

## Operator verification after a separately authorized deployment

From the signed-in OWNER tab at `https://app.cuevion.com`, start with:

```javascript
const response = await fetch('/api/auth/passkey-migration/start', {method: 'POST'});
if (!response.ok) throw new Error('Migration unavailable');
const result = await response.json();
location.assign(result.authorizationUrl);
```

After the callback, inspect only the sanitized status:

```javascript
const response = await fetch('/api/auth/passkey-migration/status', {cache: 'no-store'});
if (!response.ok) throw new Error('Migration status unavailable');
const status = await response.json();
```

Status reports candidate absent/pending/expired/consumed, database identity
present, exact new-identity canonical resolution, old Email identity active,
normal connection, migration phase and Collaboration additions/presence. It
never returns session IDs, transaction cookies, raw subjects, emails, OAuth
codes, credentials, HMAC keys or secret environment values.

Only after the new identity is attached, status computes the new OWNER digest
and new mailbox digests using the existing HMAC key. It requires the OLD owner
digest to remain authorized and derives mailbox additions only for managed
mailbox IDs whose OLD issuer/subject digest is currently allowlisted. Unapproved
mailboxes are excluded. No mailbox credentials or content leave KV; the existing
diagnostic's bounded Redis-side config projection is reused.

Manually append `collaboration.ownerEntriesToAdd` to
`CUEVION_COLLAB_V2_OWNER_ALLOWLIST` and `collaboration.mailboxEntriesToAdd` to
`CUEVION_COLLAB_V2_MAILBOX_ALLOWLIST`, using the existing comma-separated format.
Preserve every OLD entry. The endpoint changes neither environment nor authority.
It returns only missing opaque additions, counts and presence booleans.

`ready_for_normal_login_cutover` means canonical linking and current Collaboration
additions are ready; it does not prove a Production database/passkey login.
Switching normal login, proving password/passkey behavior, revoking sessions,
disabling Email OTP and removing old digests belong to final cutover.

## Verification

`tests/cuevion_migration/test_owner_passkey_migration.py` covers authorization, strict
connections, signed OAuth callbacks, state/nonce/PKCE, replay, stale and substituted
bindings, target-email/issuer/subject checks, status sanitization, and old-to-new
Collaboration authority preservation. Existing auth/Team/diagnostic tests remain.

`tests/cuevion_db/test_postgresql_owner_passkey_migration.py` creates its own
temporary PostgreSQL cluster over a Unix socket, applies the real schema/triggers,
and proves normal resolution for both identities, immutable original columns,
concurrent migration exclusion, replay, conflicts, stale rows and rollback.
Set `CUEVION_TEST_POSTGRES_BIN` to a local PostgreSQL bin directory to run it.
No application database URL is accepted by that test harness.
