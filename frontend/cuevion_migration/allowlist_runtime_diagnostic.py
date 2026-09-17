"""Temporary, read-only OWNER allowlist runtime diagnostic.

This module exposes only counts/booleans and a first-mismatch position. It never
returns the raw allowlist, HMAC key, subjects, database identifiers, or mailbox
identifiers, and it reuses the production OWNER migration authorization boundary.
"""

import hmac
import os
import time

from api.auth import auth0_flow, http, session_store
from api.collaboration import owner_request_security as security
from cuevion_migration import owner_passkey as migration


ROUTE = "/api/auth/passkey-migration/allowlist-diagnostic"


def _first_mismatch_position(expected: str, actual: str) -> int | None:
    """Return a human-friendly 1-based first mismatch position, or None."""
    for index, (left, right) in enumerate(zip(expected, actual), start=1):
        if left != right:
            return index
    if len(expected) != len(actual):
        return min(len(expected), len(actual)) + 1
    return None


def diagnostic_response(method, raw_headers, path, *, environment=None, now=None,
                        reader_factory=None, transport_factory=None):
    try:
        headers = migration._boundary(method, raw_headers, path, "GET", ROUTE)
        source = dict(os.environ if environment is None else environment)
        if source.get("VERCEL_ENV") != "production":
            return migration._failure(404)
        if http.read_cookie(headers, session_store.SESSION_COOKIE_NAME) is None:
            return migration._failure(401)

        timestamp = int(time.time()) if now is None else now
        auth0_flow._require_timestamp(timestamp, error_code="invalid_transaction")
        secret, store, session, snapshot, kv, _mailboxes = migration._state(
            source, headers, timestamp, adding=False,
            reader_factory=reader_factory, transport_factory=transport_factory)
        old, new = migration.require_envelope(snapshot, session)
        if new is None:
            raise migration.MigrationDenied()

        key = security.parse_allowlist_hmac_key(
            source.get("CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY"))
        owners = security._parse_allowlist(
            source.get("CUEVION_COLLAB_V2_OWNER_ALLOWLIST"))
        old_owner = security.derive_owner_allowlist_entry(
            key, old.issuer, 1, old.subject)
        new_owner = security.derive_owner_allowlist_entry(
            key, new.issuer, 1, new.subject)

        old_present = any(hmac.compare_digest(old_owner, entry) for entry in owners)
        if not old_present:
            raise migration.MigrationDenied()
        new_present = any(hmac.compare_digest(new_owner, entry) for entry in owners)
        non_old = tuple(
            entry for entry in owners
            if not hmac.compare_digest(old_owner, entry)
        )

        mismatch_position = None
        single_non_old_length_matches = None
        if not new_present and len(non_old) == 1:
            mismatch_position = _first_mismatch_position(new_owner, non_old[0])
            single_non_old_length_matches = len(new_owner) == len(non_old[0])

        kv.read([])
        checked_at = int(time.time()) if now is None else now
        if (checked_at < timestamp or checked_at - timestamp > 20
                or migration._load_session(store, headers, secret, checked_at) != session):
            raise migration.MigrationDenied()

        return http.json_response(200, {
            "temporaryOwnerMigration": True,
            "diagnostic": "owner_allowlist_runtime_v1",
            "ownerAllowlistEntryCount": len(owners),
            "oldOwnerPresent": old_present,
            "newOwnerPresent": new_present,
            "nonOldOwnerEntryCount": len(non_old),
            "singleNonOldEntryLengthMatchesExpected": single_non_old_length_matches,
            "singleNonOldEntryFirstMismatchPosition": mismatch_position,
        })
    except http.HttpBoundaryError as error:
        return migration._failure(error.status)
    except migration.MigrationDenied:
        return migration._failure(403)
    except Exception:
        return migration._failure(503)
