# Auth record-ID activation requirements

## Status: inactive candidate generation only

This slice is completely inactive. `cuevion_auth.account_record_ids` is a pure,
provider-independent generator of unpersisted account-record ID candidates. It
creates no account, authentication identity, verified email, workspace,
membership, authorization, authenticated session, or other authority. A returned
string is only a candidate until a future transactional repository persists it
under an authoritative uniqueness constraint. Callers must never treat generation
alone as persisted authority, and an abandoned candidate is harmless.

The module defines no storage, repository, database, Redis or KV access, provider,
login flow, HTTP route, handler, app, router, cookie behavior, frontend behavior,
beta integration, mailbox integration, Team integration, Collaboration
integration, logging, retry, clock access, environment loading, network access,
service initialization, persistent state, or feature activation. Importing or
deploying it and passing its tests cannot activate authentication or expose a
route.

## Canonical module and route boundary

The only successful production-module identity is
`cuevion_auth.account_record_ids`. `cuevion_auth` remains an implicit namespace
package with no `__init__.py`. Top-level and alternate dotted imports fail closed.
The module requires its executing dictionary to be the exact dictionary registered
under its canonical `sys.modules` entry and uses a once-only initialization marker
before defining its error, sentinels, or generators. A second spec execution under
the canonical name, reload, and equivalent re-execution fail before replacing the
original security-relevant identities. The module creates no forwarding alias,
extra `sys.modules` alias, or `sys.path` mutation.

These module-identity checks reduce accidental duplicate execution in an ordinary
uncompromised interpreter. They are not a security boundary against arbitrary code
that already controls the process and deliberately replaces or mutates
`sys.modules`, module dictionaries, private symbols, or interpreter state.

The production module and its tests remain outside the configured
`frontend/api/**/*.py` function glob. They define no `handler`, route, router, or
app. No `__init__.py` or Vercel configuration change belongs to this slice.

## Exact candidate formats

Exactly four candidate types are generated:

- Cuevion user ID: `usr_` followed by a canonical base64url suffix.
- Verified-email ID: `vem_` followed by a canonical base64url suffix.
- Authentication-identity ID: `aid_` followed by a canonical base64url suffix.
- Workspace ID: `wsp_` followed by a canonical base64url suffix.

For every call, the suffix is the canonical unpadded base64url encoding of exactly
16 fresh random bytes. It is exactly 22 ASCII characters using only `A-Z`, `a-z`,
`0-9`, hyphen, and underscore. It contains no padding and must strictly decode to
16 bytes and re-encode to the identical suffix. Each complete candidate is an exact
built-in string with its fixed prefix.

This slice generates no session ID, cookie secret, session credential, credential
envelope, lookup digest, binding digest, key epoch, credential epoch, timestamp,
provider identity, or login challenge. Account-record ID candidates must never be
used as session secrets, credentials, authorization claims, or mutable-email
authority.

## Entropy boundary

Production entropy is exactly one OS-backed `secrets.token_bytes(16)` request for
each public generator call. Every call receives a fresh independent draw. Entropy
is never cached, shared between candidates, derived from another candidate, or
retried. There is no fallback to `random`, UUIDs, timestamps, counters, email
addresses, provider subjects, process IDs, request data, or any other value.

The four public functions accept no arguments and expose no entropy, prefix,
length, seed, existing-ID collection, retry, batch, or generic-token parameter.
Deterministic tests may patch only the private module entropy primitive. The
private result must be exact built-in `bytes` of length 16; subclasses and other
objects are rejected without conversion, iteration, or coercion.

An ordinary entropy, encoding, or canonicality failure is terminal for that call.
The generator performs no retry and exposes only the one fixed, value-free
`RecordIdentifierGenerationError`. `BaseException` is not swallowed.

## Persistence, uniqueness, and retry ownership

This module cannot confirm uniqueness and performs no lookup or collision retry.
Future transactional storage is the authoritative final collision guard. A future
repository may use a bounded retry only after a definitive uniqueness conflict on
the generated record-ID field. Business-identity conflicts must not be disguised
as random collisions. A timeout, uncertain commit, or other ambiguous storage
outcome must never trigger blind candidate generation or persistence retry; it
requires authoritative reconciliation through a separately reviewed operation or
idempotency contract.

Future account creation remains blocked on reviewed transactional persistence,
record uniqueness, concurrency, conflict, idempotency, partial-failure, ambiguous
outcome, and rollback semantics. This candidate generator supplies none of that
authority.

## Fixed failures and traceback-local safety

`RecordIdentifierGenerationError` is raised only through the module's supported
fixed helper. Its arguments, string, and representation are fixed and value-free;
its cause and context are `None`. It contains no entropy bytes, partially encoded
suffix, candidate ID, prefix, or private source exception. Ordinary entropy-call
exceptions, invalid private entropy values, encoding failures, and canonicality
failures all map to this same outcome without retry or diagnostic output.

Sensitive work occurs in a worker that returns either one successful exact string
or a fixed non-sensitive failure sentinel. The public error is raised only after
the worker frame is gone and the public wrapper has removed its result reference.
Consequently, module-owned traceback frames for the fixed generation error retain
no raw entropy, encoded suffix, full candidate, or private source exception. This
guarantee does not extend to arbitrary caller frames, caller telemetry, a
propagating `BaseException`, or code that has already compromised the interpreter.
Callers must not capture or log sensitive locals.

## Environment isolation and remaining gates

Production and preview execute identical code because this slice reads no
environment configuration and has no environment-specific behavior, storage, or
service connection. This is not approval to share future account databases,
provider tenants, key material, session stores, cookie namespaces, or recovery
state between environments.

Future transactional account creation remains blocked. All session-ID generation,
cookie-secret generation, credential-envelope construction, digest derivation,
session storage, cookie emission, resolver behavior, login flows, providers, and
activation remain separately blocked. No database or authentication vendor is
selected by this document.
