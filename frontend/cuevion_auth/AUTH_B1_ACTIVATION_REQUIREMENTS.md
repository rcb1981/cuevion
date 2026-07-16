# Auth-B1 activation requirements

## Status: Auth-B1a is inactive

Auth-B1a is a pure, provider-independent session-credential boundary. It validates an explicitly injected trusted key-configuration snapshot, validates duplicate-preserving raw HTTP headers, parses exactly one production session cookie, validates its canonical versioned envelope, and derives independent server-side lookup and binding digests. Its only successful request result is opaque derived credential material.

This slice is completely inactive. It defines no resolver, account repository, session repository, storage adapter, database, Redis or KV access, environment-variable loader, identifier or credential generator, session creation, rotation, revocation, idle touch, login challenge, authentication provider, email OTP, password, passkey, HTTP route, handler, app, router, cookie emitter, frontend integration, beta integration, mailbox integration, Team integration, Collaboration integration, or feature activation. It performs no logging, retry, fallback, clock read, random generation, process or thread startup, socket creation, URL call, network access, service initialization, persistent-state creation, or filesystem access beyond normal module loading. It imports standard-library modules only and reads no live request automatically.

The production module is outside the deployed `api/**/*.py` function glob. Importing it, deploying it, or passing its tests cannot make an endpoint reachable and is not approval to activate authentication.

## Canonical module identity

The only successful production-module identity is `cuevion_auth.session_credentials`. `cuevion_auth` remains an implicit namespace package with no `__init__.py`. Top-level and alternate dotted imports fail closed. The module requires its executing dictionary to be the dictionary registered under the exact canonical `sys.modules` entry and uses a once-only initialization marker before defining errors, sentinels, configuration objects, or derived-credential objects. This rejects ordinary duplicate execution through a second canonical spec and rejects reload or equivalent re-execution while preserving the original canonical class and sentinel identities. The module creates no forwarding alias, extra `sys.modules` alias, environment registry, or `sys.path` mutation.

These module-identity and opacity guards reduce accidental misuse inside an ordinary uncompromised interpreter. They are not a security boundary against arbitrary code that already controls the Python process and deliberately replaces or mutates `sys.modules`, module dictionaries, object slots, or other interpreter state.

## Trusted key-configuration snapshot

`parse_session_key_configuration(values)` accepts only an exact built-in `dict` whose keys and values are exact built-in strings. It never reads configuration from the environment. Unknown keys, missing required keys, partial optional pairs, subclasses, mapping substitutes, and non-string contents fail with the one fixed `SessionKeyConfigurationError`. Its args, repr, and str are value-free, and its `__cause__` and `__context__` are `None`. Module-owned configuration-error traceback frames are designed not to retain the supplied configuration dictionary, supplied encoded keys, decoded key bytes, or a private underlying exception. This guarantee does not extend to arbitrary caller frames outside the module. Configuration callers and telemetry must never capture or log caller locals containing configuration secrets.

The exact configuration keys are:

- `lookup_current_epoch`
- `lookup_current_key`
- `lookup_previous_epoch`
- `lookup_previous_key`
- `binding_current_epoch`
- `binding_current_key`
- `binding_previous_epoch`
- `binding_previous_key`

The current lookup and binding epoch/key pairs are required. Each previous epoch/key pair is independently optional, but each optional pair must be wholly present or wholly absent. Epochs are canonical positive ASCII decimal strings in the inclusive range 1 through 2147483647. A current epoch must be greater than its configured previous epoch within the same family. Lookup and binding families may use the same epoch number because their key selection is independent.

Every key is canonical unpadded base64url text that decodes to exactly 32 bytes and re-encodes identically. Padding, alternate alphabets, Unicode, malformed input, and noncanonical trailing pad-bit aliases are rejected. All configured raw keys must be pairwise different across every present lookup and binding slot; lookup key material can never equal binding key material.

The resulting `SessionKeyConfiguration` is an exact-type, parser-minted, immutable, non-dataclass object with exact private slots, identity equality and hashing, fixed value-free rendering, blocked serialization, and no public raw-key accessor. It stores independent exact immutable byte and integer values rather than the caller's dictionary or encoded strings. Derivation revalidates the exact type, factory sentinel, slot completeness, types, ordering, lengths, and pairwise key separation. A forged, partial, or corrupted trusted configuration raises the fixed configuration error and is never represented as a request-level `None`.

## Raw-header boundary

`derive_request_session_credential(raw_headers, configuration)` accepts untrusted request headers only as an exact built-in `tuple` of exact two-entry built-in tuples containing exact built-in strings. The tuple preserves header order and duplicate occurrences but conveys no trust. Dictionaries, lists, namespaces, subclasses, arbitrary mappings, and duck-typed substitutes are rejected without invoking their equality, hashing, conversion, or representation behavior.

The complete header structure is validated before Cookie interpretation, key selection, or HMAC:

- at most 64 header pairs;
- each header name is 1 through 128 ASCII characters and uses RFC token characters only;
- each value is at most 8192 UTF-8 bytes;
- the sum of encoded name and value bytes is at most 32768;
- CR, LF, NUL, every C0 control, and DEL are forbidden in every value;
- lone surrogates and other strict UTF-8 encoding failures are rejected; and
- header-name comparison is ASCII case-insensitive while header values are never normalized or altered.

Malformed header structure returns exactly `None`. It performs no key selection and no HMAC.

## Production Cookie contract

The only accepted credential source is exactly one case-insensitive `Cookie` header containing exactly one case-sensitive cookie named `__Host-cuevion_session`. The module parses this cookie but never emits it. Future emission must set `Secure`, `HttpOnly`, `Path=/`, and `SameSite=Lax`, and must omit `Domain` so the cookie remains host-only.

Zero or multiple Cookie headers return `None`; headers are never combined. The Cookie header must be ASCII and at most 8192 bytes. Commas, tabs, quoted values, backslashes, empty segments, whitespace before a semicolon, and whitespace before or around `=` are rejected. Pairs use exactly `;` followed by either no space or one ASCII space. Two or more following spaces are rejected. Names use RFC token characters and are case-sensitive. Values use the conservative RFC cookie-octet ranges. Each pair splits only at its first `=`. Canonically formed unknown cookies are allowed, including an empty unknown value, but every repeated cookie name is rejected, including repeated unknown names. A differently cased production name is only an unknown cookie and cannot supply the credential.

Authorization data, any other cookie, query parameters, request bodies, localStorage, provider tokens, and mailbox credentials can never supply or replace this credential. There is no beta, bearer, provider, mailbox, or stateless fallback.

## Canonical credential envelope

The exact envelope grammar is `v1.<lookup-key-epoch>.<binding-key-epoch>.<credential-epoch>.<secret>`. It contains exactly five dot-separated ASCII components and is at most 128 bytes. The version is exactly `v1`. All three epochs use the canonical positive decimal grammar and range defined for configuration. Signs, whitespace, leading zeroes, zero, overflow, Unicode digits, and coercion are rejected.

The secret component is exactly 43 unpadded base64url characters, decodes to exactly 32 bytes, and must round-trip through strict decode and re-encode without change. Padding, `+`, `/`, Unicode, malformed alphabet characters, and noncanonical trailing pad-bit aliases are rejected. Unknown versions and unconfigured or retired lookup or binding epochs return exactly `None`.

## Independent digest derivation

Accepted envelope fields are framed in this exact order:

1. ASCII `v1`;
2. the canonical ASCII lookup key epoch;
3. the canonical ASCII binding key epoch;
4. the canonical ASCII credential epoch; and
5. the raw 32-byte cookie secret.

Each field is prefixed by its length encoded as an unsigned 32-bit big-endian integer. A field whose length is not representable by that encoding is rejected, even though accepted request fields are much smaller.

The lookup digest is HMAC-SHA-256 under the independently selected lookup key over the exact domain `cuevion/auth/session-lookup/v1` followed by a NUL byte and the framed fields. The binding digest is HMAC-SHA-256 under the independently selected binding key over the exact domain `cuevion/auth/session-binding/v1` followed by a NUL byte and the same framed fields. Lookup and binding current/previous selection occurs independently. Each 32-byte output is returned as canonical 43-character unpadded base64url text.

The request never supplies a trusted digest. The returned `DerivedSessionCredential` retains only exact integer lookup, binding, and credential epochs plus canonical lookup and binding digests. It retains no raw secret, cookie value, Cookie header, raw headers, or key material. Temporary raw bytes remain local to derivation. This slice performs no digest comparison. A future trusted resolver and session authority must retrieve authoritative state by the independently derived lookup digest and compare the authoritative stored binding digest with the independently derived expected binding digest in constant time.

`DerivedSessionCredential` is an exact-type, factory-minted, immutable, non-dataclass object with exact private slots, identity equality and hashing, five read-only properties, fixed value-free rendering, blocked serialization, and no session ID, user ID, email, workspace, role, provider, secret, header, or key surface.

All missing, malformed, ambiguous, oversized, noncanonical, unknown-version, unknown-epoch, or otherwise invalid request credentials return exactly `None` without request-derived detail, logging, retry, or fallback. Expected malformed request-controlled base64url encoding also returns exactly `None`. An unexpected request decoder failure is an internal implementation failure: it propagates from this inactive boundary without logging and must not be classified as missing authentication. A future resolver must map such an unexpected request-boundary failure to fixed `internal_error`. An unexpected decoder failure while parsing trusted configuration is sanitized to the fixed value-free `SessionKeyConfigurationError`; invalid trusted configuration never becomes a request-level `None`.

## Operational key policy for future activation

This module enforces syntax and injected snapshot consistency but uses no clock. Operations must enforce the following policy before activation:

- maintain one current and at most one previous key per lookup or binding domain;
- later issuance must use both current epochs;
- verification may independently accept a configured current or previous epoch in each domain;
- rotate keys on a recommended schedule of every 90 days;
- retain previous keys for at least the maximum absolute session lifetime plus deployment overlap;
- when only one previous key is retained, keep the maximum absolute session lifetime shorter than the rotation interval;
- deploy the new current plus old previous configuration everywhere before later issuing credentials under the new current epochs;
- remove a previous key only after no affected credential can remain valid;
- on emergency compromise, reject the affected epoch and require fresh login and session invalidation; and
- keep production and preview keys, epochs, and namespaces fully separate.

Actual production keys, deployable credentials, and production configuration values must never appear in source, tests, documentation, logs, metrics, analytics, traces, audit payloads, support output, or examples. Tests may use only clearly synthetic, fixed non-production fixtures.

## Remaining review gates before a future trusted resolver and Auth-B2

Auth-B1a does not revise or activate Auth-A. A future trusted resolver may own and call this helper, but this helper is not itself a resolver or session authority.

A future trusted resolver requires a separate review of the resolver and authoritative session semantics: exact key-epoch persistence, digest-only lookup, constant-time binding verification, account and session state validation, expiry and security-epoch behavior, outage classification, and fixed error mapping. It must prove that raw headers, cookie values, and key material never reach repositories or observability surfaces.

Auth-B2 requires separately reviewed repositories and production storage with uniqueness, transactions, atomic rotation and revocation, idle-touch and absolute-expiry enforcement, TTL behavior, concurrency, retry, partial-failure, corruption, and outage evidence. Secure operational configuration loading, key custody, scheduled and emergency rotation, and production/preview isolation remain unresolved.

HTTP cookie emission, request adapters, login and challenge flows, authentication providers, session issuance and lifecycle endpoints, frontend behavior, beta migration, mailbox boundaries, Team authorization, Collaboration authorization, monitoring, rollout controls, rollback, and an explicit activation decision all remain later scoped work. No future phase may treat the presence or importability of Auth-B1a as authorization to bypass these gates.
