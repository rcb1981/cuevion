"""Temporary, production-only dual-proof initial OWNER identity migration.

The existing session is evidence, never exchanged or rotated by this flow.
Only the explicit migration callback can invoke the insert-only repository.
Remove this module and its two routes after the final cutover.
"""

from dataclasses import asdict, replace
import hashlib
import hmac
import json
import os
import re
import secrets
import time

from api.auth import auth0_flow, http, models, runtime, session_store
from cuevion_auth import identity_inventory_diagnostic as diagnostic


START_ROUTE = "/api/auth/passkey-migration/start"
STATUS_ROUTE = "/api/auth/passkey-migration/status"
PURPOSE = "cuevion.initial-owner.database-identity.v1"
FIELDS = ("users", "emails", "identities", "workspaces", "memberships")


class MigrationDenied(Exception):
    def __init__(self):
        super().__init__("owner identity migration unavailable")


def _wire(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def graph_digest(snapshot):
    return hashlib.sha256(_wire({name: [asdict(row) for row in getattr(snapshot, name)]
                                for name in FIELDS}).encode("ascii")).hexdigest()


def require_envelope(snapshot, session, *, adding=False):
    """Exact original one-owner graph, or that graph plus one OIDC identity."""
    from cuevion_db.identity_inventory_diagnostic import _validate_snapshot

    _validate_snapshot(snapshot)
    owner = snapshot.authority
    if (any(len(getattr(snapshot, field)) != 1 for field in FIELDS if field != "identities")
            or len(snapshot.identities) not in ((1,) if adding else (1, 2))
            or runtime._current_authority_member_context(session, owner) is None
            or owner.workspace_membership.role is not models.WorkspaceRole.OWNER
            or owner.workspace.created_by_user_id != owner.user.user_id
            or owner.primary_verified_email.verification_source != "cuevion_first_account_operator_v1"
            or session.issuer != auth0_flow.AUTH0_ISSUER
            or owner.authentication_identity.issuer != session.issuer
            or owner.authentication_identity.subject != session.subject):
        raise MigrationDenied()
    old = [identity for identity in snapshot.identities
           if identity.method is models.AuthenticationMethod.EMAIL_OTP
           and re.fullmatch(r"email\|[!-~]+", identity.subject)]
    if len(old) != 1:
        raise MigrationDenied()
    old = old[0]
    # Exclude the subject-derived invited account model independently of role.
    from cuevion_auth.current_account_repository_contract import CurrentAccountAuthority
    old_authority = CurrentAccountAuthority(owner.user, owner.primary_verified_email, old,
                                            owner.workspace, owner.workspace_membership)
    if any(diagnostic._subject_ids(old_authority).values()):
        raise MigrationDenied()
    for identity in snapshot.identities:
        if (identity.user_id != owner.user.user_id
                or identity.verified_email_id != owner.primary_verified_email.email_id
                or identity.issuer != auth0_flow.AUTH0_ISSUER
                or identity.status is not models.AuthenticationIdentityStatus.ACTIVE):
            raise MigrationDenied()
    new = next((i for i in snapshot.identities if i != old), None)
    if new is not None and (new.method is not models.AuthenticationMethod.OIDC
                           or re.fullmatch(r"auth0\|[!-~]+", new.subject) is None):
        raise MigrationDenied()
    if adding and owner.authentication_identity != old:
        raise MigrationDenied()
    return old, new


def require_target(identity, snapshot):
    old = snapshot.authority.authentication_identity
    # ValidatedIdentityEvidence can only reach here after signature, issuer,
    # audience, nonce and email_verified == true checks in the existing callback.
    if (type(identity) is not auth0_flow.ValidatedIdentityEvidence
            or identity.issuer != auth0_flow.AUTH0_ISSUER
            or identity.issuer != old.issuer
            or identity.subject == old.subject
            or re.fullmatch(r"auth0\|[!-~]+", identity.subject) is None
            or identity.email != snapshot.authority.primary_verified_email.canonical_email):
        raise MigrationDenied()


def _key(secret, kind, value):
    digest = hmac.new(secret.encode("utf-8"),
        (PURPOSE + "\x00" + kind + "\x00" + value).encode("ascii"), hashlib.sha256).hexdigest()
    return "cuevion:auth:v1:owner-migration:" + kind + ":" + digest


def _candidate(transaction, session, snapshot):
    owner = snapshot.authority
    return {"purpose": PURPOSE, "version": 1, "session": asdict(session),
            "userId": owner.user.user_id, "workspaceId": owner.workspace.workspace_id,
            "securityEpoch": owner.user.security_epoch,
            "issuer": session.issuer, "subject": session.subject,
            "email": owner.primary_verified_email.canonical_email,
            "graphDigest": graph_digest(snapshot),
            "transactionDigest": hashlib.sha256(auth0_flow._transaction_plaintext(transaction)).hexdigest(),
            "issuedAt": transaction.issued_at, "expiresAt": transaction.expires_at}


def _signed_candidate(value, secret):
    wire = _wire(value)
    mac = hmac.new(secret.encode("utf-8"), (PURPOSE + "\x00candidate\x00" + wire).encode("ascii"),
                   hashlib.sha256).hexdigest()
    return _wire({"binding": value, "mac": mac})


def _save_candidate(store, secret, transaction, session, snapshot):
    if transaction.expires_at > session.expires_at:
        raise MigrationDenied()
    candidate = _signed_candidate(_candidate(transaction, session, snapshot), secret)
    if store._command(["SET", _key(secret, "candidate", transaction.owner_migration_id),
                       candidate, "EX", 600, "NX"]) != "OK":
        raise MigrationDenied()
    # Only expiry/replay metadata outlives the ten-minute candidate, scoped to
    # this session. It is not usable as authentication or migration evidence.
    marker = _wire({"issuedAt": transaction.issued_at, "expiresAt": transaction.expires_at,
                    "state": transaction.state, "migrationId": transaction.owner_migration_id})
    if store._command(["SET", _key(secret, "status", session.session_id), marker,
                       "EX", session.expires_at - transaction.issued_at]) != "OK":
        raise MigrationDenied()


def _candidate_status(store, secret, session, now):
    value = store._command(["GET", _key(secret, "status", session.session_id)])
    if value is None:
        return "absent"
    value = diagnostic._json(value)
    if (set(value) != {"issuedAt", "expiresAt", "state", "migrationId"}
            or not auth0_flow._is_exact_opaque_value(value["state"])
            or not auth0_flow._is_exact_opaque_value(value["migrationId"])
            or any(type(value[k]) is not int for k in ("issuedAt", "expiresAt"))
            or not session.created_at <= value["issuedAt"] <= now
            or value["expiresAt"] - value["issuedAt"] != 600
            or value["expiresAt"] > session.expires_at):
        raise MigrationDenied()
    if now >= value["expiresAt"]:
        return "expired"
    if store.transaction_consumed(value["state"], secret):
        return "consumed"
    candidate = store._command(["GET", _key(secret, "candidate", value["migrationId"])])
    return "absent" if candidate is None else "pending"


def _load_session(store, headers, secret, now):
    session, _ = session_store.load_server_session(store, headers=headers, secret=secret,
                                                  now=now, delete_invalid=False)
    if session is None or session.issuer != auth0_flow.AUTH0_ISSUER:
        raise http.HttpBoundaryError("authentication_required", 401)
    if session.workspace_role != "owner":
        raise http.HttpBoundaryError("forbidden", 403)
    return session


def _state(source, headers, now, *, adding, reader_factory=None, transport_factory=None):
    from cuevion_db.identity_inventory_diagnostic import build_runtime_diagnostic_reader

    secret = session_store.resolve_session_secret(source)
    transport = (transport_factory or session_store.build_kv_command_transport)(source)
    store = session_store.AuthSessionStore(transport)
    session = _load_session(store, headers, secret, now)
    reader = (reader_factory or build_runtime_diagnostic_reader)(source)
    snapshot = reader.read(session)
    require_envelope(snapshot, session, adding=adding)
    kv = diagnostic.BoundedReadOnlyKV(transport)
    state, mailboxes = diagnostic._read_team_and_config(kv, snapshot, session, secret, now)
    if (state["pendingTeamInvitations"] != 0
            or state["suspendedOrIncompleteMemberProvisioning"] != 0
            or state["ownerRecipientInvitationPresent"]
            or state["currentSessionInviteContinuationPresent"]):
        raise MigrationDenied()
    verified = reader.read(session)
    if any(getattr(snapshot, field) != getattr(verified, field) for field in FIELDS):
        raise MigrationDenied()
    if not adding:
        _, new = require_envelope(snapshot, session)
        if new is not None:
            proof = reader.read(replace(session, issuer=new.issuer, subject=new.subject))
            if (proof.authority.authentication_identity != new
                    or any(getattr(snapshot, field) != getattr(proof, field) for field in FIELDS)):
                raise MigrationDenied()
    if _load_session(store, headers, secret, now) != session:
        raise MigrationDenied()
    return secret, store, session, snapshot, kv, mailboxes


def _boundary(method, raw_headers, path, expected_method, expected_path):
    http.require_method(method, expected_method)
    headers = http.validate_header_pairs(raw_headers)
    http.require_canonical_host(headers)
    origin = http.get_unique_header(headers, "origin")
    if ((origin != http.CANONICAL_APP_ORIGIN if expected_method == "POST"
         else origin is not None and origin != http.CANONICAL_APP_ORIGIN)
            or http.get_unique_header(headers, "sec-fetch-site") != "same-origin"):
        raise http.HttpBoundaryError("forbidden_origin", 403)
    if (path != expected_path
            or http.get_unique_header(headers, "content-length") not in (None, "0")
            or http.get_unique_header(headers, "transfer-encoding") is not None):
        raise http.HttpBoundaryError("invalid_request", 400)
    return headers


def _failure(status):
    return http.json_response(status, {"error": {"code": "owner_migration_unavailable",
                                                "message": "Owner identity migration is unavailable."}})


def migration_response(method, raw_headers, path, *, operation, environment=None, now=None,
                       reader_factory=None, transport_factory=None, random_bytes=secrets.token_bytes):
    try:
        adding = operation == "start"
        if operation not in ("start", "status"):
            raise MigrationDenied()
        headers = _boundary(method, raw_headers, path, "POST" if adding else "GET",
                            START_ROUTE if adding else STATUS_ROUTE)
        source = dict(os.environ if environment is None else environment)
        if source.get("VERCEL_ENV") != "production":
            return _failure(404)
        if http.read_cookie(headers, session_store.SESSION_COOKIE_NAME) is None:
            return _failure(401)
        timestamp = int(time.time()) if now is None else now
        auth0_flow._require_timestamp(timestamp, error_code="invalid_transaction")
        connection = auth0_flow.parse_login_connection(source)
        secret, store, session, snapshot, kv, mailboxes = _state(
            source, headers, timestamp, adding=adding, reader_factory=reader_factory,
            transport_factory=transport_factory)
        if adding:
            migration_id = auth0_flow._base64url_encode(auth0_flow._random_bytes(random_bytes, 32))
            request = auth0_flow.build_authorization_request(auth0_flow.parse_auth0_configuration(source),
                timestamp, random_bytes=random_bytes, owner_migration_id=migration_id)
            _save_candidate(store, secret, request.transaction, session, snapshot)
            response = http.json_response(200, {"authorizationUrl": request.authorization_url})
            return http.PublicResponse(response.status, response.headers +
                (("Set-Cookie", request.transaction_cookie),), response.body)
        old, new = require_envelope(snapshot, session)
        candidate = _candidate_status(store, secret, session, timestamp)
        if new is not None:
            candidate = "consumed" if candidate != "absent" else "absent"
        collaboration = collaboration_additions(source, old, new, mailboxes)
        kv.read([])
        checked_at = int(time.time()) if now is None else now
        if (checked_at < timestamp or checked_at - timestamp > 20
                or _load_session(store, headers, secret, checked_at) != session):
            raise MigrationDenied()
        if new is None:
            phase = "candidate_" + candidate
        elif not collaboration["allRequiredEntriesPresent"]:
            phase = "database_identity_added_collaboration_pending"
        elif connection == "email":
            phase = "ready_for_normal_login_cutover"
        else:
            phase = "database_login_configured_final_cutover_pending"
        return http.json_response(200, {"temporaryOwnerMigration": True,
            "candidate": candidate, "databaseIdentityAdded": new is not None,
            "newIdentityResolvesSameUser": new is not None,
            "oldEmailIdentityActive": True, "normalLoginConnection": connection,
            "phase": phase, "collaboration": collaboration})
    except http.HttpBoundaryError as error:
        return _failure(error.status)
    except MigrationDenied:
        return _failure(403)
    except Exception:
        return _failure(503)


def complete_migration(transaction, identity, headers, source, now, *, reader_factory=None,
                       transport_factory=None, repository_factory=None, clock=None):
    """Called only after the normal OAuth consume and cryptographic validation."""
    if source.get("VERCEL_ENV") != "production" or transaction.owner_migration_id is None:
        raise MigrationDenied()
    if transaction.team_invite_token is not None or not transaction.issued_at <= now < transaction.expires_at:
        raise MigrationDenied()
    secret, store, session, snapshot, kv, _ = _state(source, headers, now, adding=True,
        reader_factory=reader_factory, transport_factory=transport_factory)
    expected = _signed_candidate(_candidate(transaction, session, snapshot), secret)
    actual = store._command(["GET", _key(secret, "candidate", transaction.owner_migration_id)])
    if type(actual) is not str or not hmac.compare_digest(actual, expected):
        raise MigrationDenied()
    require_target(identity, snapshot)

    def revalidate():
        checked_at = int(time.time()) if clock is None else clock()
        if (type(checked_at) is not int or not now <= checked_at < transaction.expires_at
                or checked_at - now > 20
                or _load_session(store, headers, secret, checked_at) != session):
            raise MigrationDenied()
        kv.read([])  # Primary, compare against every captured Team/config key.

    from cuevion_db.postgresql_owner_passkey_migration import build_runtime_repository
    repository = (repository_factory or build_runtime_repository)(source)
    repository.attach(snapshot, session, identity, now=now, revalidate=revalidate)
    # Do not rotate, delete or mint sessions here. A later normal login resolves
    # the new subject using the unchanged canonical account authority path.
    return http.redirect_response("/?passkey_migration=identity_added",
                                   set_cookies=(auth0_flow.clear_transaction_cookie(),))


def collaboration_additions(source, old, new, mailboxes):
    from api.collaboration import owner_request_security as security

    result = {"ownerEntriesToAdd": [], "mailboxEntriesToAdd": [],
              "authorizedMailboxBindings": 0, "allRequiredEntriesPresent": False}
    if new is None:
        return result
    key = security.parse_allowlist_hmac_key(source.get("CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY"))
    owners = security._parse_allowlist(source.get("CUEVION_COLLAB_V2_OWNER_ALLOWLIST"))
    allowed = security._parse_allowlist(source.get("CUEVION_COLLAB_V2_MAILBOX_ALLOWLIST"))
    old_owner = security.derive_owner_allowlist_entry(key, old.issuer, 1, old.subject)
    if not any(hmac.compare_digest(old_owner, entry) for entry in owners):
        raise MigrationDenied()
    owner = security.derive_owner_allowlist_entry(key, new.issuer, 1, new.subject)
    if owner not in owners:
        result["ownerEntriesToAdd"].append(owner)
    for mailbox in mailboxes:
        if not security.valid_allowlist_mailbox_id(mailbox):
            raise MigrationDenied()
        previous = security.derive_mailbox_allowlist_entry(key, old.issuer, 1, old.subject, mailbox)
        if any(hmac.compare_digest(previous, entry) for entry in allowed):
            result["authorizedMailboxBindings"] += 1
            candidate = security.derive_mailbox_allowlist_entry(key, new.issuer, 1, new.subject, mailbox)
            if candidate not in allowed:
                result["mailboxEntriesToAdd"].append(candidate)
    result["mailboxEntriesToAdd"].sort()
    result["allRequiredEntriesPresent"] = not (result["ownerEntriesToAdd"] or result["mailboxEntriesToAdd"])
    return result
