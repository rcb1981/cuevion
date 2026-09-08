"""Short-lived, dry-run-only operator attestations. Importing performs no I/O.

This module never imports or invokes the migration. The HTTP boundary authenticates
the browser; the explicit local migration adapter separately revalidates authority.
"""
from __future__ import annotations

if __name__ != "api.collaboration.operator_grant":
    raise ImportError("Import as api.collaboration.operator_grant")

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time

from . import owner_request_security as security
from .http_boundary import parse_json_object

MODE_ENV = "CUEVION_COLLAB_DISCOVERY_MIGRATION_OPERATOR_MODE"
PURPOSE = "collaboration_discovery_migration_dry_run"
LIFETIME_SECONDS = 600
MAX_MAILBOXES = 32
MAX_GRANT_BYTES = 16_384
_PREFIX = "mog1"
_KEY_DOMAIN = b"cuevion/collaboration/migration-operator-grant/v1"
_CONFIG_DOMAIN = "cuevion/collaboration/migration-operator-configuration/v1"
_FIELDS = frozenset({"version", "purpose", "userId", "workspaceId", "issuedAt",
                     "expiresAt", "nonce", "mailboxes", "configurationBinding"})
_B64 = re.compile(r"^[A-Za-z0-9_-]+$")
_CODES = frozenset({"operator_mode_off", "authentication_required", "owner_not_authorized",
                    "no_owned_mailboxes", "grant_invalid", "grant_expired",
                    "grant_scope_invalid", "authority_unavailable", "grant_file_invalid"})
_VERIFIED = object()


class OperatorGrantError(Exception):
    """Only a fixed code; never retain rejected payloads or provider errors."""

    def __init__(self, code="grant_invalid"):
        self.code = code if type(code) is str and code in _CODES else "grant_invalid"
        super().__init__(self.code)


class VerifiedMigrationOperatorContext:
    """Verifier-minted attestation, deliberately incompatible with owner sessions."""

    __slots__ = ("_sentinel", "user_id", "workspace_id", "issued_at", "expires_at",
                 "mailboxes", "config_binding")

    def __new__(cls, *args, **kwargs):
        raise TypeError("Migration operator contexts are verifier-minted")

    def __setattr__(self, name, value):
        raise TypeError("Migration operator contexts are immutable")

    def __delattr__(self, name):
        raise TypeError("Migration operator contexts are immutable")

    def __repr__(self):
        return "<VerifiedMigrationOperatorContext>"

    def __reduce__(self):
        raise TypeError("Migration operator contexts are not serializable")

    def __reduce_ex__(self, protocol):
        raise TypeError("Migration operator contexts are not serializable")


def operator_mode(environment):
    try:
        value = environment.get(MODE_ENV)
        return value if type(value) is str and value in {"dry_run_grant", "runtime_apply"} else "off"
    except Exception:
        return "off"


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value):
    if type(value) is not str or not _B64.fullmatch(value) or len(value) % 4 == 1:
        raise OperatorGrantError()
    decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if _b64(decoded) != value:
        raise OperatorGrantError()
    return decoded


def _configuration_binding(configuration):
    cfg = security._require_configuration(configuration)
    # Bind the complete rollout snapshot without exporting provider/session facts
    # or allowlist entries. Any rollout removal/change invalidates existing grants.
    key = hmac.new(cfg._allowlist_hmac_key, _CONFIG_DOMAIN.encode("ascii"), hashlib.sha256).digest()
    return _b64(hmac.new(key, _json({
        "domain": _CONFIG_DOMAIN, "origin": cfg.app_origin,
        "owners": sorted(cfg.owner_allowlist), "mailboxes": sorted(cfg.mailbox_allowlist),
    }).encode("ascii"), hashlib.sha256).digest())


def _signature(configuration, segment):
    cfg = security._require_configuration(configuration)
    key = hmac.new(cfg._csrf_key, _KEY_DOMAIN, hashlib.sha256).digest()
    return hmac.new(key, (_PREFIX + "." + segment).encode("ascii"), hashlib.sha256).digest()


def _validate_ids(user_id, workspace_id):
    from cuevion_auth import current_account_repository_contract as contract
    contract.validate_current_account_user_id(user_id)
    contract.validate_current_account_workspace_id(workspace_id)


def _validate_payload(payload, now):
    if (type(payload) is not dict or set(payload) != _FIELDS
        or type(payload["version"]) is not int or payload["version"] != 1
        or payload["purpose"] != PURPOSE):
        raise OperatorGrantError()
    _validate_ids(payload["userId"], payload["workspaceId"])
    issued, expires = payload["issuedAt"], payload["expiresAt"]
    if (type(now) is not int or type(issued) is not int or type(expires) is not int
        or not 0 <= issued < expires <= (2**53 - 1)
        or expires - issued > LIFETIME_SECONDS or issued > now):
        raise OperatorGrantError()
    if expires <= now:
        raise OperatorGrantError("grant_expired")
    if len(_unb64(payload["nonce"])) != 32 or len(_unb64(payload["configurationBinding"])) != 32:
        raise OperatorGrantError()
    scopes = payload["mailboxes"]
    if type(scopes) is not list or not 1 <= len(scopes) <= MAX_MAILBOXES:
        raise OperatorGrantError("grant_scope_invalid")
    previous = ""
    for scope in scopes:
        if (type(scope) is not dict or set(scope) != {"mailboxId", "provider"}
            or not security.valid_allowlist_mailbox_id(scope["mailboxId"])
            or scope["provider"] not in ("google", "custom_imap")
            or type(scope["provider"]) is not str or scope["mailboxId"] <= previous):
            raise OperatorGrantError("grant_scope_invalid")
        previous = scope["mailboxId"]


def verify_operator_grant(grant, *, owner_security_configuration, now=None):
    """Verify the entire grant, strict wire format, time and current rollout. No I/O."""
    try:
        timestamp = int(time.time()) if now is None else now
        if type(grant) is not str or not grant.isascii() or len(grant) > MAX_GRANT_BYTES:
            raise OperatorGrantError()
        parts = grant.split(".")
        if len(parts) != 3 or parts[0] != _PREFIX:
            raise OperatorGrantError()
        raw, signature = _unb64(parts[1]), _unb64(parts[2])
        if len(signature) != 32 or not hmac.compare_digest(
            _signature(owner_security_configuration, parts[1]), signature
        ):
            raise OperatorGrantError()
        payload = parse_json_object(raw.decode("ascii"), allowed_fields=_FIELDS,
                                    required_fields=_FIELDS, reject_numbers=False)
        _validate_payload(payload, timestamp)
        if raw != _json(payload).encode("ascii"):
            raise OperatorGrantError()
        if not hmac.compare_digest(payload["configurationBinding"],
                                   _configuration_binding(owner_security_configuration)):
            raise OperatorGrantError("owner_not_authorized")
        context = object.__new__(VerifiedMigrationOperatorContext)
        for name, value in {
            "_sentinel": _VERIFIED, "user_id": payload["userId"],
            "workspace_id": payload["workspaceId"], "issued_at": payload["issuedAt"],
            "expires_at": payload["expiresAt"], "config_binding": payload["configurationBinding"],
            "mailboxes": tuple((s["mailboxId"], s["provider"]) for s in payload["mailboxes"]),
        }.items():
            object.__setattr__(context, name, value)
        return context
    except OperatorGrantError:
        raise
    except Exception:
        raise OperatorGrantError() from None


def _validated_config_record(result, email):
    from api import user_config_store as configs
    if type(result) is not dict or result.get("status") != "ok" or type(result.get("config")) is not dict:
        raise OperatorGrantError("authority_unavailable")
    config = result["config"]
    stored_email = config.get("email")
    if stored_email is not None and (type(stored_email) is not str
                                     or configs.normalize_auth_email(stored_email) != email):
        raise OperatorGrantError("owner_not_authorized")
    return config


def resolve_issuance_authority(owner_context, headers, *, owner_security_configuration):
    """Current session/account plus one current config read; never accept submitted scope."""
    from api.auth.runtime import AuthenticatedMemberContext
    from api import user_config_store as configs
    try:
        if not security.owner_is_allowlisted(owner_context, owner_security_configuration):
            raise OperatorGrantError("owner_not_authorized")
        member, user, result = configs.read_user_config_for_authenticated_member(headers)
        if (type(member) is not AuthenticatedMemberContext or member.auth_source != "auth0"
            or member.user_type != "member" or type(user) is not dict):
            raise OperatorGrantError("authentication_required")
        _validate_ids(member.user_id, member.workspace_id)
        if (member.workspace_id != owner_context.workspace_id or member.email != owner_context.owner_email
            or member.name != owner_context.display_name or user.get("email") != member.email):
            raise OperatorGrantError("owner_not_authorized")
        config = _validated_config_record(result, member.email)
        entries = config.get("managedInboxes")
        if type(entries) is not list or len(entries) > MAX_MAILBOXES:
            raise OperatorGrantError("grant_scope_invalid")
        scopes, seen = [], set()
        for entry in entries:
            mailbox_id = entry.get("id") if type(entry) is dict else None
            if not security.valid_allowlist_mailbox_id(mailbox_id) or mailbox_id in seen:
                raise OperatorGrantError("grant_scope_invalid")
            seen.add(mailbox_id)
            resolved = configs.resolve_managed_inbox(config, mailbox_id)
            if resolved.get("status") != "ok":
                raise OperatorGrantError("grant_scope_invalid")
            provider = resolved["inbox"]["provider"]
            if provider in ("google", "custom_imap") and security.mailbox_is_allowlisted(
                owner_context, mailbox_id, owner_security_configuration
            ):
                scopes.append({"mailboxId": mailbox_id, "provider": provider})
        if not scopes:
            raise OperatorGrantError("no_owned_mailboxes")
        return member, sorted(scopes, key=lambda item: item["mailboxId"])
    except OperatorGrantError:
        raise
    except Exception:
        raise OperatorGrantError("authority_unavailable") from None


def _issue_operator_grant(member, scopes, *, owner_security_configuration, now):
    """Private issuance primitive; caller must finish browser auth/CSRF/rate checks."""
    try:
        payload = {"version": 1, "purpose": PURPOSE, "userId": member.user_id,
                   "workspaceId": member.workspace_id, "issuedAt": now,
                   "expiresAt": now + LIFETIME_SECONDS, "nonce": secrets.token_urlsafe(32),
                   "mailboxes": scopes, "configurationBinding": _configuration_binding(owner_security_configuration)}
        _validate_payload(payload, now)
        segment = _b64(_json(payload).encode("ascii"))
        grant = _PREFIX + "." + segment + "." + _b64(_signature(owner_security_configuration, segment))
        if len(grant) > MAX_GRANT_BYTES:
            raise OperatorGrantError("grant_scope_invalid")
        return {"grant": grant, "expiresAt": payload["expiresAt"]}
    except OperatorGrantError:
        raise
    except Exception:
        raise OperatorGrantError("authority_unavailable") from None


def _read_current_account(user_id, workspace_id):
    from api.auth import account_authority
    return account_authority.build_runtime_account_authority(os.environ).read_current_account_by_user(
        user_id, workspace_id)


def _read_current_mailboxes(email):
    from api import user_config_store as configs
    store, error = configs.resolve_user_config_store()
    if error is not None or store is None:
        raise OperatorGrantError("authority_unavailable")
    return configs.read_user_config_record(store, email)


def resolve_current_operator_config(context, owner_mailbox_id, *, owner_security_configuration):
    """Revalidate by canonical IDs, owned mailbox, rollout and current Team snapshot.

    No owner/session capabilities are fabricated. Current account email is only
    a compatibility lookup after canonical account authority has been established.
    """
    from api.auth import models, runtime
    from api import user_config_store as configs
    from cuevion_auth import current_account_repository_contract as contract
    from . import authorization as auth
    from .models import normalize_v2_team_membership_ref
    try:
        if (type(context) is not VerifiedMigrationOperatorContext or context._sentinel is not _VERIFIED
            or context.issued_at > int(time.time()) or context.expires_at <= int(time.time())):
            raise OperatorGrantError("grant_expired")
        if not hmac.compare_digest(context.config_binding, _configuration_binding(owner_security_configuration)):
            raise OperatorGrantError("owner_not_authorized")
        if not security.valid_allowlist_mailbox_id(owner_mailbox_id):
            raise OperatorGrantError("grant_scope_invalid")
        provider = dict(context.mailboxes).get(owner_mailbox_id)
        if provider is None:
            raise OperatorGrantError("grant_scope_invalid")
        result = _read_current_account(context.user_id, context.workspace_id)
        if (type(result) is not contract.CurrentAccountByUserAuthorityResult
            or result.outcome is not contract.CurrentAccountReadOutcome.FOUND
            or type(result.authority) is not contract.CurrentAccountByUserAuthority):
            raise OperatorGrantError("owner_not_authorized")
        account = result.authority
        user, email = account.user, account.primary_verified_email
        workspace, membership = account.workspace, account.workspace_membership
        if (type(user) is not models.CuevionUser or user.user_id != context.user_id
            or user.status is not models.UserStatus.ACTIVE
            or type(email) is not models.VerifiedEmail or email.user_id != user.user_id
            or email.email_id != user.primary_verified_email_id or email.status is not models.VerifiedEmailStatus.VERIFIED
            or type(workspace) is not models.Workspace or workspace.workspace_id != context.workspace_id
            or workspace.status is not models.WorkspaceStatus.ACTIVE
            or type(membership) is not models.WorkspaceMembership or membership.user_id != user.user_id
            or membership.workspace_id != workspace.workspace_id or membership.status is not models.WorkspaceMembershipStatus.ACTIVE
            or type(membership.role) is not models.WorkspaceRole):
            raise OperatorGrantError("owner_not_authorized")
        member = runtime.AuthenticatedMemberContext(user.user_id, email.canonical_email, user.display_name,
                                                    workspace.workspace_id, membership.role.value)
        config = _validated_config_record(_read_current_mailboxes(member.email), member.email)
        mailbox = configs.resolve_managed_inbox(config, owner_mailbox_id)
        if (mailbox.get("status") != "ok" or mailbox["inbox"]["id"] != owner_mailbox_id
            or mailbox["inbox"]["provider"] != provider):
            raise OperatorGrantError("grant_scope_invalid")
        team, error = auth._resolve_active_team_member(member.workspace_id, member.user_id)
        if error == "not_active" and team is None:
            membership_ref = ""
        elif error is None and type(team) is dict and team.get("memberUserId") == member.user_id:
            membership_ref = normalize_v2_team_membership_ref(team.get("sourceInvitationId"))
            if membership_ref is None:
                raise OperatorGrantError("authority_unavailable")
        else:
            raise OperatorGrantError("authority_unavailable")
        return {"workspaceId": member.workspace_id, "userId": member.user_id, "email": member.email,
                "membershipRef": membership_ref, "ownerMailboxId": owner_mailbox_id,
                "ownerProvider": provider, "ownerDisplayName": member.name}
    except OperatorGrantError:
        raise
    except Exception:
        raise OperatorGrantError("authority_unavailable") from None


def read_private_operator_grant(path):
    """Read a directly captured HTTP response from a private file; never print it.

    The caller deletes the temporary grant file after use. This reader neither
    persists credentials nor manages checkpoint files.
    """
    fd = None
    directory_fd = None
    try:
        candidate = Path(path)
        if not candidate.is_absolute() or ".." in candidate.parts:
            raise OperatorGrantError("grant_file_invalid")
        # Pin each directory before descending; pathname lstat checks followed
        # by a fresh full-path open would permit an ancestor-symlink race.
        directory_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in candidate.parts[1:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        parent = os.fstat(directory_fd)
        if (not stat.S_ISDIR(parent.st_mode) or stat.S_IMODE(parent.st_mode) != 0o700
            or parent.st_uid != os.getuid()):
            raise OperatorGrantError("grant_file_invalid")
        fd = os.open(candidate.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.getuid() or info.st_size > MAX_GRANT_BYTES + 256):
            raise OperatorGrantError("grant_file_invalid")
        raw = os.read(fd, MAX_GRANT_BYTES + 257)
        if len(raw) > MAX_GRANT_BYTES + 256:
            raise OperatorGrantError("grant_file_invalid")
        envelope = parse_json_object(raw.decode("ascii"), allowed_fields={"ok", "data"},
                                     required_fields={"ok", "data"}, reject_numbers=False)
        data = envelope["data"]
        if (envelope["ok"] is not True or type(data) is not dict or set(data) != {"grant", "expiresAt"}
            or type(data["grant"]) is not str or len(data["grant"]) > MAX_GRANT_BYTES
            or type(data["expiresAt"]) is not int):
            raise OperatorGrantError("grant_file_invalid")
        return data["grant"]
    except Exception:
        raise OperatorGrantError("grant_file_invalid") from None
    finally:
        if fd is not None:
            os.close(fd)
        if directory_fd is not None:
            os.close(directory_fd)
