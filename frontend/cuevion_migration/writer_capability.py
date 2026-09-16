"""Temporary OWNER-only writer diagnosis. Never starts or retries migration."""

import os
import time

from api.auth import auth0_flow, http, session_store
from cuevion_migration import owner_passkey as migration
from cuevion_db import passkey_writer_capability as capability


ROUTE = "/api/auth/passkey-migration/writer-capability"
_TARGET_STATE = "not_recoverable_from_consumed_candidate"


def failure(status, classification="unavailable"):
    if classification not in capability.CLASSIFICATIONS:
        classification = "unavailable"
    return http.json_response(status, {"classification": classification})


def capability_response(method, raw_headers, path, *, environment=None, now=None,
                        reader_factory=None, transport_factory=None, connect=None):
    try:
        headers = migration._boundary(method, raw_headers, path, "GET", ROUTE)
        source = dict(os.environ if environment is None else environment)
        if source.get("VERCEL_ENV") != "production":
            return failure(404)
        if http.read_cookie(headers, session_store.SESSION_COOKIE_NAME) is None:
            return failure(401)
        timestamp = int(time.time()) if now is None else now
        auth0_flow._require_timestamp(timestamp, error_code="invalid_transaction")
        auth0_flow.parse_login_connection(source)
        dependencies = dict(reader_factory=reader_factory, transport_factory=transport_factory)
        secret, store, session, snapshot, kv, _ = migration._state(
            source, headers, timestamp, adding=False, **dependencies)
        _, added = migration.require_envelope(snapshot, session)
        single = len(snapshot.identities) == 1
        if added is not None:
            # A second identity closes the retry envelope. It cannot be equated
            # with an unknown consumed target merely because it is OIDC.
            result = {"classification": "owner_envelope_changed"}
            safe = False
        else:
            migration.require_envelope(snapshot, session, adding=True)
            safe = True
            result = capability.probe(source, connect=connect)
        # The consumed Phase 1 candidate stores only the OLD owner binding and
        # transaction digest, never the validated target subject. Do not infer
        # that subject or add new storage just to diagnose an earlier attempt.
        result.update(targetState=_TARGET_STATE,
            currentOwnerStillSingleCanonicalIdentity=single,
            ownerEnvelopeStillSafeForRetry=safe)
        checked_at = int(time.time()) if now is None else now
        if not timestamp <= checked_at <= timestamp + 20:
            return failure(503)
        _, _, checked_session, checked_snapshot, _, _ = migration._state(
            source, headers, checked_at, adding=False, **dependencies)
        if checked_session != session or any(getattr(snapshot, f) != getattr(checked_snapshot, f)
                                             for f in migration.FIELDS):
            return failure(403, "owner_envelope_changed")
        kv.read([])
        finished_at = int(time.time()) if now is None else now
        if not timestamp <= finished_at <= timestamp + 20:
            return failure(503)
        if migration._load_session(store, headers, secret, finished_at) != session:
            return failure(403)
        return http.json_response(200, result)
    except http.HttpBoundaryError as error:
        return failure(error.status)
    except migration.MigrationDenied:
        return failure(403, "owner_envelope_changed")
    except Exception:
        return failure(503)
