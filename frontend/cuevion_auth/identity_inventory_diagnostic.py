"""TEMPORARY production OWNER identity diagnostic; delete after migration.

No login, mutation, repair, pruning, token exchange, or session cleanup path is
called here. The HTTP projection is deliberately assembled field by field.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import time

from api.auth import auth0_flow, http, models, session_store
from api.team import authority as team


ROUTE = "/api/auth/identity-inventory-diagnostic"
MAX_KEYS = 256
MAX_INDEX_ENTRIES = 128
MAX_READS = 6

# As in Team authority, normal EVAL reads from the primary. EVAL_RO/replica
# fallback is intentionally absent. These fixed scripts contain only reads.
# Bound each record before GET, plus the total returned record bytes. The
# config is projected *inside Redis*: no credential/config payload leaves KV.
_SNAPSHOT_LUA = r"""
local values = {}
local total = 0
for i, key in ipairs(KEYS) do
  local length = redis.call('STRLEN', key)
  local limit = key == ARGV[1] and 262144 or 16384
  if length > limit then return redis.error_reply('diagnostic unavailable') end
  local raw = redis.call('GET', key)
  if raw and key == ARGV[1] then
    local ok, config = pcall(cjson.decode, raw)
    if not ok or type(config) ~= 'table' or type(config.email) ~= 'string'
      or type(config.managedInboxes) ~= 'table' or #config.managedInboxes > 128 then
      return redis.error_reply('diagnostic unavailable')
    end
    local ids = {}
    local seen = {}
    local count = 0
    for index, inbox in pairs(config.managedInboxes) do
      if type(index) ~= 'number' or index < 1 or index > #config.managedInboxes
        or index ~= math.floor(index) or type(inbox) ~= 'table'
        or type(inbox.id) ~= 'string' or #inbox.id < 1 or #inbox.id > 256
        or seen[inbox.id] then
        return redis.error_reply('diagnostic unavailable')
      end
      seen[inbox.id] = true
      count = count + 1
      ids[#ids + 1] = inbox.id
    end
    if count ~= #config.managedInboxes then return redis.error_reply('diagnostic unavailable') end
    table.sort(ids)
    raw = cjson.encode({email=config.email, mailboxIds=ids, mailboxCount=count,
                        fingerprint=redis.sha1hex(raw)})
  end
  total = total + (raw and #raw or 0)
  if total > 24576 then return redis.error_reply('diagnostic unavailable') end
  values[i] = raw or false
end
return values
""".strip()


class DiagnosticUnavailable(Exception):
    def __init__(self):
        super().__init__("identity diagnostic unavailable")


def _json(raw):
    if type(raw) is not str:
        raise DiagnosticUnavailable()
    return json.loads(raw, object_pairs_hook=session_store._strict_object,
                      parse_constant=session_store._reject_json_constant)


class BoundedReadOnlyKV:
    """Fixed primary reads, bounded expansion, and unchanged-record checks."""

    def __init__(self, transport, config_key=""):
        self._transport = transport
        self.config_key = config_key
        self.values = {}
        self.reads = 0

    def read(self, keys):
        selected = sorted(set(keys) | set(self.values))
        self.reads += 1
        if len(selected) > MAX_KEYS or self.reads > MAX_READS:
            raise DiagnosticUnavailable()
        command = ["EVAL", _SNAPSHOT_LUA, len(selected), *selected, self.config_key]
        # Enforce the existing transport's request bound before network I/O.
        if len(json.dumps(command, separators=(",", ":")).encode()) > 16384:
            raise DiagnosticUnavailable()
        payload = self._transport(command)
        if type(payload) is not dict or set(payload) != {"result"}:
            raise DiagnosticUnavailable()
        values = payload["result"]
        if (type(values) is not list or len(values) != len(selected)
                or any(v is not None and v is not False and type(v) is not str for v in values)):
            raise DiagnosticUnavailable()
        current = dict(zip(selected, (None if v is False else v for v in values)))
        if any(current[key] != value for key, value in self.values.items()):
            raise DiagnosticUnavailable()
        self.values = current
        return current

    def session_transport(self, command):
        if (len(command) != 2 or command[0] != "GET"
                or not command[1].startswith(session_store.SESSION_KEY_PREFIX)):
            raise DiagnosticUnavailable()
        return {"result": self.read([command[1]])[command[1]]}


def _index(raw, validator):
    values = [] if raw is None else _json(raw)
    if (type(values) is not list or len(values) > MAX_INDEX_ENTRIES
            or any(type(v) is not str or not validator(v) for v in values)
            or len(set(values)) != len(values)):
        raise DiagnosticUnavailable()
    return values


def _record(raw, normalizer):
    if raw is None:
        return None
    record = normalizer(_json(raw))
    if record is None:
        raise DiagnosticUnavailable()
    return record


def _provenance(source):
    if source == "cuevion_first_account_operator_v1":
        return "initial-account-operator"
    if re.fullmatch(r"team-invite-oidc:v1:[a-f0-9]{64}", source):
        return "team-invite-oidc"
    if source.lower().startswith("team-invite-oidc"):
        raise DiagnosticUnavailable()
    return "other-stored-verification-source"


def _subject_ids(owner):
    # Exact deterministic domains used by the invitee repository; comparison
    # only. This does not manufacture or authorize an invitation/account.
    def derive(prefix, domain, values):
        raw = json.dumps([domain, *values], separators=(",", ":"), ensure_ascii=True).encode("ascii")
        return prefix + base64.urlsafe_b64encode(
            hashlib.sha256(prefix.encode("ascii") + raw).digest()[:16]
        ).rstrip(b"=").decode("ascii")
    identity = owner.authentication_identity
    values = (identity.issuer, identity.subject)
    user_id = derive("usr_", "cuevion.canonical-user.v1", values)
    return {
        "userIdMatchesInviteeSubjectDerivation": owner.user.user_id == user_id,
        "identityIdMatchesInviteeSubjectDerivation": identity.identity_id == derive(
            "aid_", "cuevion.canonical-authentication-identity.v1", values),
        "emailIdMatchesInviteeDerivation": owner.primary_verified_email.email_id == derive(
            "vem_", "cuevion.canonical-verified-email.v1", (user_id, owner.primary_verified_email.canonical_email)),
    }


def _collaboration(environment, session, mailbox_ids):
    from api.collaboration import owner_request_security as security
    names = ("CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY", "CUEVION_COLLAB_V2_OWNER_ALLOWLIST",
             "CUEVION_COLLAB_V2_MAILBOX_ALLOWLIST")
    values = tuple(environment.get(name) for name in names)
    if not any(values):
        if environment.get("CUEVION_COLLAB_V2_HTTP_MODE") in ("owner_read", "owner_write"):
            raise DiagnosticUnavailable()
        return {"configured": False, "ownerIssuerSubjectMatch": False, "matchingMailboxBindings": 0}
    key = security.parse_allowlist_hmac_key(values[0])
    owners = security._parse_allowlist(values[1])
    mailboxes = security._parse_allowlist(values[2])
    candidate = security.derive_owner_allowlist_entry(key, session.issuer, session.schema_version, session.subject)
    matched_owner = any(hmac.compare_digest(candidate, entry) for entry in owners)
    matched_mailboxes = 0
    for mailbox_id in mailbox_ids:
        if not security.valid_allowlist_mailbox_id(mailbox_id):
            raise DiagnosticUnavailable()
        candidate = security.derive_mailbox_allowlist_entry(
            key, session.issuer, session.schema_version, session.subject, mailbox_id)
        matched_mailboxes += any(hmac.compare_digest(candidate, entry) for entry in mailboxes)
    return {"configured": True, "ownerIssuerSubjectMatch": matched_owner,
            "matchingMailboxBindings": matched_mailboxes}


def _read_team_and_config(kv, snapshot, session, secret, now):
    from api import user_config_store

    owner = snapshot.authority
    owner_email = owner.primary_verified_email.canonical_email
    kv.config_key = user_config_store.build_user_config_key(owner_email)
    emails = {e.email_id: e for e in snapshot.emails}
    users = {u.user_id: u for u in snapshot.users}
    workspace_ids = [w.workspace_id for w in snapshot.workspaces]
    binding = session_store.TeamInviteContinuationBinding(
        session.session_id, session.user_id, session.workspace_id, session.issuer,
        session.subject, session.created_at, session.expires_at)
    continuation_key = session_store._team_invite_continuation_key(binding, secret)
    seeds = [kv.config_key, continuation_key]
    for workspace_id in workspace_ids:
        seeds.extend((team._pending_index_key(workspace_id), team._members_index_key(workspace_id)))
    # Known canonical memberships give selectors even during PREPARE, before
    # the KV member pointer has been created. Never use selectors from HTTP.
    recipients = set()
    for membership in snapshot.memberships:
        user = users[membership.user_id]
        email = emails.get(user.primary_verified_email_id)
        if email is not None:
            recipients.add((membership.workspace_id, email.canonical_email))
            seeds.extend((team._member_key(membership.workspace_id, email.canonical_email),
                          team._workspace_recipient_invitation_key(membership.workspace_id, email.canonical_email),
                          team._member_user_pointer_key(membership.workspace_id, user.user_id)))
    values = kv.read(seeds)
    pending = {}
    more = []
    for workspace_id in workspace_ids:
        pending[workspace_id] = _index(values[team._pending_index_key(workspace_id)], team._valid_invitation_id)
        for invitation_id in pending[workspace_id]:
            more.append(team._workspace_invitation_key(workspace_id, invitation_id))
        # Validate the existing member index without reading legacy guest bearers.
        _index(values[team._members_index_key(workspace_id)], lambda email: bool(team._normalize_email(email)))
    for workspace_id, email in sorted(recipients):
        record = _record(values[team._workspace_recipient_invitation_key(workspace_id, email)],
                         team._normalize_invitation_record)
        if record is not None:
            if record["workspaceId"] != workspace_id or record["inviteeEmail"] != email:
                raise DiagnosticUnavailable()
            more.append(team._workspace_invitation_key(workspace_id, record["id"]))
    for membership in snapshot.memberships:
        pointer = _record(values[team._member_user_pointer_key(membership.workspace_id, membership.user_id)],
                          team._normalize_member_user_pointer) if users[membership.user_id].primary_verified_email_id else None
        if pointer is not None:
            if pointer["workspaceId"] != membership.workspace_id or pointer["memberUserId"] != membership.user_id:
                raise DiagnosticUnavailable()
            more.append(team._workspace_invitation_key(membership.workspace_id, pointer["sourceInvitationId"]))
    values = kv.read(more)
    invitations = {}
    copies = []
    for key in sorted(set(more)):
        invitation = _record(values[key], team._normalize_invitation_record)
        if invitation is None or key != team._workspace_invitation_key(invitation["workspaceId"], invitation["id"]):
            raise DiagnosticUnavailable()
        invitations[(invitation["workspaceId"], invitation["id"])] = invitation
        copies.extend((team._invitation_token_key(invitation["id"], invitation["tokenDigest"]),
                       team._workspace_recipient_invitation_key(invitation["workspaceId"], invitation["inviteeEmail"])))
    values = kv.read(copies)
    pending_count = owner_pending = 0
    for workspace_id, invitation_ids in pending.items():
        for invitation_id in invitation_ids:
            invitation = invitations[(workspace_id, invitation_id)]
            if invitation["status"] == "invited" and invitation["expiresAt"] > now * 1000:
                wire = values[team._workspace_invitation_key(workspace_id, invitation_id)]
                if any(values[key] != wire for key in (
                    team._invitation_token_key(invitation_id, invitation["tokenDigest"]),
                    team._workspace_recipient_invitation_key(workspace_id, invitation["inviteeEmail"]))):
                    raise DiagnosticUnavailable()
                pending_count += 1
                owner_pending += invitation["createdByUserId"] == session.user_id
    incomplete = _incomplete_provisioning(snapshot, values, invitations)
    continuation = values[continuation_key]
    continuation_present = False
    if continuation is not None:
        decoded = session_store._decode_team_invite_continuation(continuation)
        if decoded is None or decoded.binding != binding:
            raise DiagnosticUnavailable()
        continuation_present = decoded.created_at <= now < decoded.expires_at
    config_raw = values[kv.config_key]
    mailbox_ids = []
    if config_raw is not None:
        config = _json(config_raw)
        mailbox_ids = config.get("mailboxIds")
        # Redis cjson encodes an empty Lua table as {}, not [].
        if mailbox_ids == {} and config.get("mailboxCount") == 0:
            mailbox_ids = []
        if (set(config) != {"email", "mailboxIds", "mailboxCount", "fingerprint"}
                or type(config["email"]) is not str or team._normalize_email(config["email"]) != owner_email
                or type(mailbox_ids) is not list or len(mailbox_ids) > MAX_INDEX_ENTRIES
                or any(type(value) is not str for value in mailbox_ids)
                or len(set(mailbox_ids)) != len(mailbox_ids)
                or type(config["mailboxCount"]) is not int or config["mailboxCount"] != len(mailbox_ids)):
            raise DiagnosticUnavailable()
    return {
        "pendingTeamInvitations": pending_count,
        "suspendedOrIncompleteMemberProvisioning": incomplete,
        "ownerPendingInvitationsCreated": owner_pending,
        "ownerRecipientInvitationPresent": values[team._workspace_recipient_invitation_key(session.workspace_id, owner_email)] is not None,
        "currentSessionInviteContinuationPresent": continuation_present,
        "emailIndexedConfigPresent": config_raw is not None,
        "managedMailboxCount": len(mailbox_ids),
    }, mailbox_ids


def _incomplete_provisioning(snapshot, values, invitations):
    """Count canonical suspended members and unfinished invitee continuity.

    Removed memberships are intentional removals, not incomplete provisioning.
    Missing/removed Team access for a still-active invitee is incomplete.
    """
    from cuevion_db import postgresql_team_invitee_repository as invitee
    emails = {e.email_id: e for e in snapshot.emails}
    users = {u.user_id: u for u in snapshot.users}
    incomplete = set()
    memberships_by_user = {}
    for membership in snapshot.memberships:
        memberships_by_user.setdefault(membership.user_id, []).append(membership)
        if membership.role is not models.WorkspaceRole.MEMBER or membership.status is models.WorkspaceMembershipStatus.REMOVED:
            continue
        user = users[membership.user_id]
        email = emails.get(user.primary_verified_email_id)
        if membership.status is models.WorkspaceMembershipStatus.SUSPENDED:
            incomplete.add((membership.workspace_id, user.user_id))
            continue
        if email is None or _provenance(email.verification_source) != "team-invite-oidc":
            continue
        workspace_id = membership.workspace_id
        pointer = _record(values[team._member_user_pointer_key(workspace_id, user.user_id)], team._normalize_member_user_pointer)
        member = _record(values[team._member_key(workspace_id, email.canonical_email)], team._normalize_membership_record)
        invitation = invitations.get((workspace_id, pointer["sourceInvitationId"])) if pointer else None
        complete = False
        if pointer and member and invitation and member["status"] == "active" and invitation["status"] == "accepted":
            identities = [i for i in snapshot.identities if i.user_id == user.user_id
                          and i.status is models.AuthenticationIdentityStatus.ACTIVE]
            if len(identities) != 1:
                raise DiagnosticUnavailable()
            identity = identities[0]
            request = invitee.InviteePreparationRequest(identity.issuer, identity.subject, email.canonical_email,
                workspace_id, invitation["id"], invitation["tokenDigest"], invitation["createdByUserId"])
            wire = values[team._workspace_invitation_key(workspace_id, invitation["id"])]
            complete = (
                user.status is models.UserStatus.ACTIVE and email.status is models.VerifiedEmailStatus.VERIFIED
                and email.verification_source == invitee.derive_invitee_provenance(request)
                and (user.user_id, email.email_id, identity.identity_id) == invitee.derive_invitee_record_ids(request)
                and pointer == team._build_member_user_pointer(member)
                and member["memberUserId"] == user.user_id and member["workspaceId"] == workspace_id
                and member["email"] == email.canonical_email and member["sourceInvitationId"] == invitation["id"]
                and invitation["acceptedByUserId"] == user.user_id and invitation["acceptedByEmail"] == email.canonical_email
                and member["createdAt"] == invitation["createdAt"] and member["acceptedAt"] == invitation["acceptedAt"]
                and "removedAt" not in member and "revokedAt" not in member
                and values[team._workspace_recipient_invitation_key(workspace_id, email.canonical_email)] == wire
                and values[team._invitation_token_key(invitation["id"], invitation["tokenDigest"])] == wire
                and email.canonical_email in _index(values[team._members_index_key(workspace_id)], lambda value: bool(team._normalize_email(value)))
                and invitation["id"] not in _index(values[team._pending_index_key(workspace_id)], team._valid_invitation_id)
            )
        if not complete:
            incomplete.add((workspace_id, user.user_id))
    for user in snapshot.users:
        email = emails.get(user.primary_verified_email_id)
        if email and _provenance(email.verification_source) == "team-invite-oidc" and user.user_id not in memberships_by_user:
            incomplete.add((None, user.user_id))
    return len(incomplete)


def _failure(status):
    return http.json_response(status, {"error": {"code": "identity_diagnostic_unavailable",
                                               "message": "Identity diagnostic is unavailable."}})


def _request_boundary(method, raw_headers, path):
    http.require_method(method, "GET")
    headers = http.validate_header_pairs(raw_headers)
    http.require_canonical_host(headers)
    origin = http.get_unique_header(headers, "origin")
    site = http.get_unique_header(headers, "sec-fetch-site")
    if (origin is not None and origin != http.CANONICAL_APP_ORIGIN
            or site != "same-origin"):
        raise http.HttpBoundaryError("forbidden_origin", 403)
    if (path != ROUTE or http.get_unique_header(headers, "content-length") not in (None, "0")
            or http.get_unique_header(headers, "transfer-encoding") is not None):
        raise http.HttpBoundaryError("invalid_request", 400)
    return headers


def diagnostic_response(method, raw_headers, path, *, environment=None, now=None,
                        reader_factory=None, transport_factory=None):
    """One complete sanitized response, or a value-free failure. Never log it."""
    try:
        headers = _request_boundary(method, raw_headers, path)
        source = dict(os.environ if environment is None else environment)
        if source.get("VERCEL_ENV") != "production":
            return _failure(404)
        if http.read_cookie(headers, session_store.SESSION_COOKIE_NAME) is None:
            return _failure(401)
        timestamp = int(time.time()) if now is None else now
        if type(timestamp) is not int or timestamp < 0:
            return _failure(503)
        secret = session_store.resolve_session_secret(source)
        transport = (transport_factory or session_store.build_kv_command_transport)(source)
        kv = BoundedReadOnlyKV(transport)
        store = session_store.AuthSessionStore(kv.session_transport)
        session, _ = session_store.load_server_session(store, headers=headers, secret=secret,
                                                      now=timestamp, delete_invalid=False)
        if session is None:
            return _failure(401)
        if session.issuer != "https://" + auth0_flow.AUTH0_DOMAIN + "/":
            return _failure(401)
        from cuevion_db.identity_inventory_diagnostic import (
            InventoryDenied, build_runtime_diagnostic_reader,
        )
        reader = (reader_factory or build_runtime_diagnostic_reader)(source)
        try:
            snapshot = reader.read(session)
        except InventoryDenied:
            return _failure(403)
        state, mailbox_ids = _read_team_and_config(kv, snapshot, session, secret, timestamp)
        collaboration = _collaboration(source, session, mailbox_ids)
        # There is no cross-store distributed transaction. Bracket the KV read
        # with two complete DB snapshots and require identical canonical rows.
        verified = reader.read(session)
        fields = ("users", "emails", "identities", "workspaces", "memberships")
        if any(getattr(snapshot, field) != getattr(verified, field) for field in fields):
            return _failure(503)
        checked_at = int(time.time()) if now is None else now
        if checked_at - timestamp > 20 or checked_at < timestamp:
            return _failure(503)
        final_session, _ = session_store.load_server_session(store, headers=headers, secret=secret,
                                                            now=checked_at, delete_invalid=False)
        if final_session != session:
            return _failure(401)
        owner = snapshot.authority
        identity = owner.authentication_identity
        counts = snapshot.counts()
        counts.update({key: state[key] for key in ("pendingTeamInvitations", "suspendedOrIncompleteMemberProvisioning")})
        payload = {
            "temporaryMigrationDiagnostic": True,
            "owner": {
                "userId": owner.user.user_id, "email": owner.primary_verified_email.canonical_email,
                "workspaceId": owner.workspace.workspace_id, "workspaceRole": owner.workspace_membership.role.value,
                "issuer": identity.issuer, "subject": identity.subject,
                "authenticationMethod": identity.method.value,
                "verificationProvenance": _provenance(owner.primary_verified_email.verification_source),
                "membershipState": owner.workspace_membership.status.value,
                "isInitialOwner": owner.workspace.created_by_user_id == owner.user.user_id,
                "teamInviteProvenanceExists": _provenance(owner.primary_verified_email.verification_source) == "team-invite-oidc",
                "collaborationIssuerSubjectBindingsExist": collaboration["ownerIssuerSubjectMatch"] or collaboration["matchingMailboxBindings"] > 0,
            },
            "counts": counts,
            "subjectDependentState": {
                "authenticationIdentityBindings": sum(i.user_id == session.user_id and i.issuer == session.issuer and i.subject == session.subject for i in snapshot.identities),
                "currentAuthenticatedSessionPresent": True,
                "ownerPendingInvitationsCreated": state["ownerPendingInvitationsCreated"],
                "ownerRecipientInvitationPresent": state["ownerRecipientInvitationPresent"],
                "currentSessionInviteContinuationPresent": state["currentSessionInviteContinuationPresent"],
                "collaborationRolloutBindings": collaboration,
                "subjectDerivedIds": _subject_ids(owner),
                "emailIndexedConfigPresent": state["emailIndexedConfigPresent"],
                "managedMailboxCount": state["managedMailboxCount"],
            },
            "coverage": {
                "accounts": "all canonical rows; membership role counts include inactive memberships",
                "initialOwnerEvidence": "workspace.created_by_user_id; no operation or security-event tables read",
                "teamInvitations": "live v2 invitations in every canonical workspace pending index",
                "sessions": "current authenticated session and its continuation only; no session scan",
                "invitationReferences": "pending invitations and canonical recipients/member pointers; no historical scan",
                "collaboration": "current owner and configured mailbox rollout bindings; no message records",
                "consistency": "repeatable-read PostgreSQL snapshots bracketing unchanged primary KV reads; not a distributed snapshot",
            },
        }
        return http.json_response(200, payload)
    except http.HttpBoundaryError as error:
        return _failure(error.status)
    except Exception:
        return _failure(503)
