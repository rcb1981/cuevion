# Auth-A activation requirements

## Status: inactive contract only

Auth-A is completely inactive. It defines provider-independent account, identity, workspace, membership, stored-session, and authenticated-session contracts for future work. It does not authenticate a request or activate any production behavior.

There is no Auth-A route, handler, authentication provider, account or session storage, cookie parsing or emission, HTTP integration, frontend integration, or Collaboration integration. Auth-A performs no environment, filesystem, network, provider, or storage access and initializes no service. It implements no repository or resolver. Its tests and importability are contract evidence only; neither is approval to activate authentication.

The deployed Python root is `frontend/`. The only successful production-module identities are `api.auth.models` and `api.auth.session_contract`. The `api.auth` directory remains an implicit namespace package: it has no `__init__.py`. Short names, repository-root dotted names, and other alternate imports fail closed rather than create a second copy of a model, enum, factory sentinel, or capability class. Each canonical module requires its executing module dictionary to be the dictionary registered under its exact canonical `sys.modules` entry and uses a once-only initialization marker. These guards reject ordinary alternate-name execution, a second spec-loaded module object using the canonical name while the original remains registered, and reload or equivalent re-execution before redefining Auth-A types or sentinels. `session_contract` also requires the exact registered canonical `models` module and therefore uses its exact class identities. Canonical modules use package-relative imports, do not mutate `sys.path` or `sys.modules`, expose no `handler`, and have no route surface.

These module-identity guards rely on ordinary Python process integrity. They are not a security boundary against arbitrary code that already controls the process and deliberately replaces or mutates `sys.modules` or Auth-A module dictionaries.

No authentication vendor is selected by Auth-A. No operational credential, key, token, digest value, account identifier, or deployment secret belongs in this document.

## Existing mechanisms are not production account authentication

Current beta authentication remains unacceptable as production identity evidence. A beta identifier, beta session, or beta cookie must not be treated as a Cuevion user, authentication identity, workspace authority, or verified production session. In particular, a beta cookie cannot be exchanged, converted, or upgraded into a production session. A future rollout must obtain fresh, independently verified account-control evidence and mint a new production session under the reviewed production contract.

Mailbox OAuth and IMAP credentials remain mailbox-resource authorization. They may authorize access to a mailbox resource, but they do not authenticate a Cuevion account, establish account ownership, select a workspace, grant a workspace role, link identities, or support account recovery. Mailbox identifiers and mailbox email addresses must never be promoted into account authority.

## Identity and workspace authority

Immutable Cuevion user IDs and workspace IDs replace mutable-email authority. A canonical verified email is an account record with an explicit lifecycle; it is not the primary key for a user or workspace. Email does not determine workspace ownership. Workspace authority comes only from the immutable workspace ID and a validated membership record for the immutable user ID.

Matching email text must never link accounts or authentication identities automatically, even when both strings are canonical or independently marked verified. There is no provider-specific alias, plus-address, or dot-address equivalence. A future managed authentication provider must prove control of the provider account and the claimed email to the assurance level approved for the operation. Provider subject binding, verified-email association, linking, and any change of authority must be explicit, transactional, and auditable.

Account linking, verified-email changes, recovery, and last-owner protection remain unresolved activation blockers. Their future designs must address reauthentication, conflict handling, notification, audit evidence, rollback and recovery behavior, concurrent requests, and prevention of orphaned workspaces. Auth-A makes no claim that those workflows are implemented.

## Future production session requirements

Future Cuevion sessions must be fully stateful, opaque, and revocable. The client-held session secret must carry no user, email, workspace, role, or provider authority by itself. Authentication must resolve current server-side records and fail when the session is missing, revoked, expired, bound to the wrong identity, or stale relative to the user's security epoch.

The production session cookie is expected to be host-only, `Secure`, `HttpOnly`, and `SameSite=Lax`. Host-only means that no `Domain` attribute is set. These are requirements for a future HTTP phase; Auth-A creates, reads, and writes no cookie.

Session cookie secrets must never be stored or logged raw. Raw values must also be excluded from exceptions, traces, metrics, analytics, audit payloads, support tooling, and diagnostic output. Server-side lookup and credential-binding digests must use separate cryptographic keys and explicit, distinct derivation domains. A lookup digest must not be reused as a binding digest, identity, authorization claim, or cross-system correlation value.

### Trusted request-credential boundary

`AuthenticatedSessionResolver` receives the original duplicate-preserving tuple of raw header-name/value pairs from the reviewed HTTP boundary together with an exact integer time. These raw headers are untrusted request input; using a tuple preserves order and duplicate occurrences but does not make the caller or its data trusted. The future resolver must validate the exact container and element types before using them. It owns strict production-session credential parsing and must reject missing, malformed, duplicate, ambiguous, oversized, or otherwise noncanonical credential and header representations.

Browser or request data may never precompute or supply a trusted credential lookup digest or credential binding digest. The future trusted resolver must derive the lookup digest with a server-only lookup key and dedicated lookup domain. It must independently derive or verify the expected binding digest with a different server-only binding key and distinct binding domain. It may pass only the resolver-derived canonical lookup digest to `SessionRecordRepository.get_session_by_lookup_digest(...)`. The authoritative stored binding digest must be compared with the independently derived expected binding value inside the future reviewed session authority. Repository lookup remains digest-only; the repository must not parse headers or cookies, and no raw cookie or header value may reach it.

The resolver must never expose or return the raw session cookie, complete Cookie header values, lookup keys, binding keys, raw provider credentials, or raw mailbox credentials. It must never log or persist raw request credentials. Missing, malformed, ambiguous, expired, revoked, or authoritatively absent authentication maps to `authentication_required`; a session or account authority outage maps to `authentication_unavailable`; persisted invariant corruption or an unexpected internal failure maps to `internal_error`. Fixed resolver failures must use `raise_session_resolution_error(...)`.

There is no beta-session fallback, mailbox OAuth fallback, IMAP fallback, localStorage fallback, stateless fallback, or workspace selection. The resolver authenticates only. Workspace membership and authorization remain separate future boundaries. Auth-A implements no resolver, parser, cookie handling, digest derivation, key access, repository, or storage behavior; this contract revision activates nothing.

Every stored session must satisfy `authenticated_at <= issued_at <= last_used_at < idle_expires_at <= absolute_expires_at`. A revoked session must additionally satisfy `last_used_at <= revoked_at <= absolute_expires_at`. Equality is allowed between `authenticated_at` and `issued_at`, between `issued_at` and `last_used_at`, between idle and absolute expiry, between last use and revocation, and between revocation and absolute expiry; last use at idle expiry is invalid. An active session may authenticate only when `issued_at <= now`, `last_used_at <= now`, `now < idle_expires_at`, and `now < absolute_expires_at`. A future `last_used_at` therefore fails closed.

Every session requires both idle expiry and fixed absolute expiry. Activity may update idle state only within the absolute lifetime and must never revive an expired or revoked session. Login, authentication-strength or privilege changes, recovery, account linking, email or identity security changes, and other security-epoch changes require session rotation; changes that terminate account authority require revocation as well. Rotation must invalidate the superseded credential without creating an overlap that silently preserves old authority.

Future resolvers must emit session-resolution failures only through `raise_session_resolution_error` with an exact approved `SessionResolutionReason`. Direct construction or raising of `SessionResolutionError` is not the supported contract. The raising function produces a fixed, value-free error without retaining an active underlying exception.

For future repository adapters, returning `None` means only that an authoritative, successful lookup found no record. Storage unavailability must never be represented as `None`; it must use `raise_session_resolution_error(SessionResolutionReason.AUTHENTICATION_UNAVAILABLE)`. Persisted invariant corruption or an unexpected internal failure must use the same fixed pathway with `SessionResolutionReason.INTERNAL_ERROR`. `SessionResolutionReason.AUTHENTICATION_REQUIRED` is reserved for authoritative absence or invalid, revoked, or expired authentication, not infrastructure outage. Repository failures must remain fixed and value-free and must not contain user, email, identity, workspace, or session IDs, credential lookup digests, or private storage details.

Authentication must fail closed during an account-database or session-store outage. No cache, beta mechanism, mailbox credential, request-supplied identity, or workspace email may act as a fallback.

Production and preview authentication environments must be isolated. They must not share provider tenants or trust configuration, signing or derivation keys, cookie namespaces, session stores, account databases, redirect authority, or recovery state. Evidence from one environment must never authenticate or authorize access in another.

The process-internal `AuthenticatedAccountSession` capability reduces accidental misuse between trusted components; it is not a sandbox or protection against arbitrary hostile Python code already executing in the process.

## Storage and concurrency evidence required before activation

A future account database requires reviewed uniqueness constraints and transaction evidence for every authoritative relationship. This includes immutable record IDs, external issuer-and-subject bindings, verified-email ownership policy, primary-email references, and workspace memberships. Tests must demonstrate fail-closed behavior under duplicate creation, conflicting links, concurrent email or status changes, membership and last-owner races, stale row versions, partial failure, retry, and rollback. Application-level prechecks alone are not uniqueness or transaction evidence.

A future session store requires evidence that credential consume, rotation, revocation, activity touch, and TTL handling are atomic under genuine competing clients. Rotation must retire the old credential and establish the successor as one operation. Revocation must be terminal and idempotent. Touch must not extend absolute expiry, revive a terminal session, or overwrite a newer security decision. Store TTL must enforce, rather than merely document, the applicable idle and absolute bounds. Failure, retry, timeout, and race behavior must be verified against the actual production storage semantics before any route is enabled.

No storage implementation or serialization format is selected or created by Auth-A.

## Integration and migration blockers

Beta account data and migration from mutable email-keyed workspaces remain unresolved. Migration requires a separately reviewed mapping to immutable users, verified emails, authentication identities, workspaces, and memberships; deterministic conflict handling; rollback and audit evidence; and proof that a mutable or duplicated email cannot acquire authority. Existing beta state must not be silently accepted as production authentication evidence during migration.

Legacy Collaboration routes remain disabled. Collaboration v2 remains inactive. The existing `owner_request_security` foundation must not consume beta authentication or be adapted directly to Auth-A records; its integration remains blocked until a later phase supplies the exact reviewed authenticated-session capability and immutable workspace authority. Nothing in Auth-A enables a Collaboration route, owner route, guest route, or frontend behavior.

No Auth-B, Auth-C, Auth-D, Auth-E, or Auth-F phase may activate accidentally. Importing a module, deploying these files, adding storage code, setting unrelated configuration, or passing local tests must not enable a route or login path. Each phase requires a separate scoped change, default-off controls where applicable, its own security review and evidence, and an explicit rollout decision. A later phase must not bypass the gates of an earlier phase.

## Intended future phases

- **Auth-B — inactive storage and verified-session adapter.** Add reviewed account and session persistence plus an adapter that can resolve only verified sessions. It remains inactive, performs no route activation, and must supply the uniqueness, transaction, atomicity, outage, TTL, and environment-isolation evidence above. Auth-B must independently review duplicate Cookie handling, credential size limits, canonical credential encoding, separate lookup and binding keys and derivation domains, constant-time binding verification, key rotation, revocation, outage handling, and production/preview separation before any implementation can be approved.

- **Auth-C — default-off HTTP route shells.** Add transport boundaries for login and session lifecycle operations behind explicit default-off controls. Route presence must not make the routes reachable or accept beta or mailbox authentication.

- **Auth-D — controlled verified-login rollout.** Enable verified login only through an approved, monitored, reversible rollout. It must mint fresh stateful production sessions and must not upgrade beta cookies.

- **Auth-E — owner-security and immutable-workspace integration.** Integrate the authenticated-session capability with owner security and immutable workspace memberships. Email remains display and contact data, never workspace authority; account linking, recovery, email changes, and last-owner protection must be resolved before this phase activates.

- **Auth-F — allowlisted Collaboration v2 owner routes.** Enable only the separately reviewed, explicitly allowlisted Collaboration v2 owner routes after all preceding authentication, authorization, storage, migration, owner-security, and Collaboration activation gates have passed. Collaboration v2 remains inactive until then.

This phase sequence is descriptive, not approval to implement or activate any later phase.
