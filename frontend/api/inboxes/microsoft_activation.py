"""Two-phase, fail-closed Microsoft mailbox activation.

The callback first authenticates the current Auth0 workspace owner and checks
Microsoft Graph /me. This service then stages ONE disconnected mailbox by CAS,
persists an owner/mailbox-scoped token by CAS, and atomically activates only
after exact durable token and config readback. No request body can grant owner
authority and no provisional credential may make a mailbox live.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone

from api.auth.email_address import is_valid_auth_email, normalize_auth_email
from api.auth.runtime import AuthenticatedMemberContext
from api.inboxes import microsoft_token_authority as token_authority
from api.inboxes.microsoft_token_authority import MicrosoftMailboxAuthority, MicrosoftTokenError
from api import user_config_store

MAX_CONFIG_ATTEMPTS = 3
CREDENTIAL_GENERATION = re.compile(r"^[A-Za-z0-9_-]{43}$")
AUTHORIZATION_NONCE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
PENDING_STATUS = "authenticated_pending_activation"
ACTIVE_STATUS = "connected"
PENDING_MESSAGE = "Microsoft authorization is being verified."
ACTIVE_MESSAGE = "Microsoft account connected."


class MicrosoftActivationError(Exception):
    """Safe fixed public failure code, never provider content or secrets."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _exact(left: object, right: object) -> bool:
    try:
        return json.dumps(left, allow_nan=False, sort_keys=True, separators=(",", ":")) == (
            json.dumps(right, allow_nan=False, sort_keys=True, separators=(",", ":"))
        )
    except (TypeError, ValueError):
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _member_owner(member: object) -> AuthenticatedMemberContext:
    if (
        type(member) is not AuthenticatedMemberContext
        or member.membership_role != "owner"
        or member.auth_source != "auth0"
        or member.user_type != "member"
        or not isinstance(member.user_id, str)
        or not isinstance(member.workspace_id, str)
        or not is_valid_auth_email(member.email)
    ):
        raise MicrosoftActivationError("microsoft_owner_required")
    return member


def _mailbox_id(owner: str, email: str) -> str:
    digest = hashlib.sha256((owner + "\0" + email).encode("utf-8")).hexdigest()
    return "microsoft-" + digest[:28]


def _prepare(
    member: AuthenticatedMemberContext,
    identity: object,
    credential_generation: object,
    token_payload: object,
) -> tuple[MicrosoftMailboxAuthority, str | None]:
    owner = _member_owner(member)
    if (
        not isinstance(identity, dict)
        or type(identity.get("email")) is not str
        or not is_valid_auth_email(identity["email"])
        or identity["email"] != normalize_auth_email(identity["email"])
        or type(identity.get("id")) is not str
        or type(credential_generation) is not str
        or CREDENTIAL_GENERATION.fullmatch(credential_generation) is None
    ):
        raise MicrosoftActivationError("microsoft_identity_invalid")
    email = identity["email"]
    acc = identity["id"]
    auth = MicrosoftMailboxAuthority(
        owner_email=normalize_auth_email(owner.email),
        mailbox_id=_mailbox_id(normalize_auth_email(owner.email), email),
        email=email,
        account_id=acc,
        credential_generation=credential_generation,
    )
    try:
        token_authority._canonical_authority(auth)
        token_authority._validated_payload(token_payload)
    except MicrosoftTokenError:
        raise MicrosoftActivationError("microsoft_token_payload_invalid") from None

    display = identity.get("display_name")
    safe_display = (
        display.strip()[:160]
        if type(display) is str and display.strip()
        and not any(ord(c) < 32 or ord(c) == 127 for c in display)
        else None
    )
    return auth, safe_display


def _config_store() -> dict:
    try:
        store = user_config_store.resolve_user_config_store()
    except Exception:
        raise MicrosoftActivationError("microsoft_config_unavailable") from None
    if not isinstance(store, tuple) or len(store) != 2:
        raise MicrosoftActivationError("microsoft_config_unavailable")
    config, error = store
    if error is not None or not isinstance(config, dict):
        raise MicrosoftActivationError("microsoft_config_unavailable")
    return config


def _read(store: dict, owner: str, *, allow_missing: bool) -> tuple[str, dict]:
    try:
        result = user_config_store.read_user_config_record(store, owner)
    except Exception:
        raise MicrosoftActivationError("microsoft_config_unavailable") from None
    if not isinstance(result, dict):
        raise MicrosoftActivationError("microsoft_config_unavailable")
    status = result.get("status")
    if status == "missing" and allow_missing and result.get("config") is None:
        return status, {}
    config = result.get("config")
    if status != "ok" or type(config) is not dict:
        raise MicrosoftActivationError(
            "microsoft_config_conflict" if status == "missing"
            else "microsoft_config_unavailable"
        )
    if config.get("email") != owner:
        raise MicrosoftActivationError("microsoft_owner_mismatch")
    managed = config.get("managedInboxes", [])
    if type(managed) is not list or any(type(box) is not dict for box in managed):
        raise MicrosoftActivationError("microsoft_config_invalid")
    return status, config


def _find(config: dict, auth: MicrosoftMailboxAuthority) -> tuple[dict | None, list]:
    managed = config.get("managedInboxes", [])
    if type(managed) is not list or any(type(box) is not dict for box in managed):
        raise MicrosoftActivationError("microsoft_config_invalid")
    matches = [
        box for box in managed
        if box.get("id") == auth.mailbox_id
        or (
            isinstance(box.get("id"), str)
            and box["id"].casefold() == auth.mailbox_id.casefold()
        )
        or (
            isinstance(box.get("email"), str)
            and normalize_auth_email(box["email"]) == auth.email
        )
        or (
            box.get("provider") == "microsoft"
            and box.get("oauthAccountId") == auth.account_id
        )
    ]
    if len(matches) > 1:
        raise MicrosoftActivationError("microsoft_mailbox_conflict")
    if matches and matches[0].get("id") != auth.mailbox_id:
        raise MicrosoftActivationError("microsoft_mailbox_conflict")
    return (matches[0] if matches else None), managed


def _stable_identity_matches(
    box: dict, auth: MicrosoftMailboxAuthority, member: AuthenticatedMemberContext
) -> bool:
    return all((
        box.get("id") == auth.mailbox_id,
        box.get("email") == auth.email,
        box.get("provider") == "microsoft",
        box.get("oauthOwnerEmail") == auth.owner_email,
        box.get("oauthOwnerUserId") == member.user_id,
        box.get("oauthWorkspaceId") == member.workspace_id,
        box.get("oauthAccountId") == auth.account_id,
        box.get("connectionMethod") == "oauth",
    ))


def _identity_matches(
    box: dict, auth: MicrosoftMailboxAuthority, member: AuthenticatedMemberContext
) -> bool:
    return (
        _stable_identity_matches(box, auth, member)
        and box.get("oauthCredentialGeneration") == auth.credential_generation
        and box.get("oauthReconnectGeneration") is None
    )


def _pending_box(
    auth: MicrosoftMailboxAuthority, member: AuthenticatedMemberContext,
    name: str | None, *, issued_at: int, nonce: str,
) -> dict:
    return {
        "id": auth.mailbox_id,
        "title": name or auth.email,
        "email": auth.email,
        "provider": "microsoft",
        "oauthOwnerEmail": auth.owner_email,
        "oauthOwnerUserId": member.user_id,
        "oauthWorkspaceId": member.workspace_id,
        "oauthAccountId": auth.account_id,
        "oauthCredentialGeneration": auth.credential_generation,
        "oauthAuthorizationIssuedAt": issued_at,
        "oauthAuthorizationNonce": nonce,
        "connected": False,
        "connectionMethod": "oauth",
        "connectionType": "oauth",
        "connectionStatus": PENDING_STATUS,
        "connectionMessage": PENDING_MESSAGE,
        "oauthAuthorizationUrl": None,
        "customImap": {
            "host": "", "port": "", "ssl": True, "username": ""
        },
        "customSmtp": {
            "host": "", "port": "", "security": "starttls",
            "username": "", "useSameCredentials": True
        },
    }


def _config_mutation(original: dict, owner: str, box: dict, *, replace: bool) -> dict:
    base = {
        "v": 1,
        "email": owner,
        "updatedAt": _now(),
        "onboardingSession": {},
        "managedInboxes": [],
        "mailboxTitleOverrides": {},
        "primaryManagedInboxId": None,
        "mailboxFocusPreferenceOverrides": {},
        "inboxSignatures": {},
        "smartFolders": [],
        "uiPreferences": {},
        "displayNameOverrides": {},
        **deepcopy(original),
    }
    base["email"] = owner
    base["v"] = 1
    base["updatedAt"] = _now()
    items = base["managedInboxes"]
    if replace:
        found = [index for index, current in enumerate(items) if current.get("id") == box["id"]]
        if len(found) != 1:
            raise MicrosoftActivationError("microsoft_mailbox_conflict")
        items[found[0]] = deepcopy(box)
    else:
        items.append(deepcopy(box))
    base["managedInboxes"] = items
    return base


def _write_and_verify(
    store: dict, owner: str, status: str, original: dict, intended: dict
) -> str:
    try:
        if status == "missing":
            result = user_config_store.write_user_config_record_if_missing(
                store, owner, intended
            )
        else:
            result = user_config_store.write_user_config_record_if_unchanged(
                store, owner, original, intended
            )
    except Exception:
        raise MicrosoftActivationError("microsoft_config_unavailable") from None
    if not isinstance(result, dict):
        raise MicrosoftActivationError("microsoft_config_unavailable")
    write_status = result.get("status")
    if write_status in {"conflict", "missing"}:
        return "retry"
    if write_status != "ok" or not _exact(result.get("record"), intended):
        raise MicrosoftActivationError("microsoft_config_unavailable")
    _read_status, verified = _read(store, owner, allow_missing=False)
    if not _exact(verified, intended):
        raise MicrosoftActivationError("microsoft_config_unavailable")
    return "saved"


def _stage(
    store: dict, member: AuthenticatedMemberContext, auth: MicrosoftMailboxAuthority,
    title: str | None, *, issued_at: int, nonce: str,
) -> None:
    for _ in range(MAX_CONFIG_ATTEMPTS):
        status, config = _read(store, auth.owner_email, allow_missing=True)
        current, _managed = _find(config, auth)
        if current is not None:
            if (
                not _stable_identity_matches(current, auth, member)
                or current.get("connected") is not False
                or current.get("connectionStatus") != PENDING_STATUS
                or current.get("oauthReconnectGeneration") is not None
            ):
                raise MicrosoftActivationError("microsoft_mailbox_conflict")
            previous_issue = current.get("oauthAuthorizationIssuedAt")
            previous_nonce = current.get("oauthAuthorizationNonce")
            if current.get("oauthCredentialGeneration") == auth.credential_generation:
                if previous_issue == issued_at and previous_nonce == nonce:
                    return
                raise MicrosoftActivationError("microsoft_authorization_stale")
            if (
                type(previous_issue) is not int
                or previous_issue >= issued_at
                or type(previous_nonce) is not str
                or AUTHORIZATION_NONCE.fullmatch(previous_nonce) is None
            ):
                raise MicrosoftActivationError("microsoft_authorization_stale")
            # A previous initial OAuth callback stopped after staging or token
            # persistence. A fresh *verified* grant may supersede ONLY that
            # inactive exact-owner/account registration, never a live mailbox.
            if (
                type(current.get("oauthCredentialGeneration")) is not str
                or CREDENTIAL_GENERATION.fullmatch(current["oauthCredentialGeneration"]) is None
            ):
                raise MicrosoftActivationError("microsoft_mailbox_conflict")
            replacement = {
                **current,
                "oauthCredentialGeneration": auth.credential_generation,
                "oauthAuthorizationIssuedAt": issued_at,
                "oauthAuthorizationNonce": nonce,
                "connected": False,
                "connectionStatus": PENDING_STATUS,
                "connectionMessage": PENDING_MESSAGE,
            }
            intended = _config_mutation(
                config, auth.owner_email, replacement, replace=True
            )
        else:
            intended = _config_mutation(
                config, auth.owner_email,
                _pending_box(auth, member, title, issued_at=issued_at, nonce=nonce),
                replace=False
            )
        if _write_and_verify(store, auth.owner_email, status, config, intended) == "saved":
            return
    raise MicrosoftActivationError("microsoft_config_conflict")


def _ensure_token(auth: MicrosoftMailboxAuthority, token_payload: dict) -> None:
    try:
        stored_generation = token_authority.read_owned_microsoft_token_generation(auth)
        if stored_generation is None:
            token_authority.persist_owned_microsoft_token(
                auth, token_payload, mode="connect",
            )
        elif stored_generation != auth.credential_generation:
            token_authority.persist_owned_microsoft_token(
                auth, token_payload, mode="reconnect",
                previous_generation=stored_generation,
            )
        # A previous attempt may have saved the same generation already.
        # The exact authority readback is still mandatory.
    except MicrosoftTokenError as error:
        if error.code != "microsoft_token_write_conflict":
            raise MicrosoftActivationError(error.code) from None
        try:
            token_authority.load_owned_microsoft_token(auth)
        except MicrosoftTokenError:
            raise MicrosoftActivationError("microsoft_token_write_conflict") from None
        except Exception:
            raise MicrosoftActivationError("microsoft_token_store_unavailable") from None
    except Exception:
        raise MicrosoftActivationError("microsoft_token_store_unavailable") from None
    try:
        token_authority.load_owned_microsoft_token(auth)
    except MicrosoftTokenError as error:
        raise MicrosoftActivationError(error.code) from None
    except Exception:
        raise MicrosoftActivationError("microsoft_token_store_unavailable") from None


def _activate(
    store: dict, member: AuthenticatedMemberContext, auth: MicrosoftMailboxAuthority,
    *, reconnect: bool = False,
) -> dict:
    for _ in range(MAX_CONFIG_ATTEMPTS):
        _status, config = _read(store, auth.owner_email, allow_missing=False)
        current, _managed = _find(config, auth)
        if (
            not isinstance(current, dict)
            or not _stable_identity_matches(current, auth, member)
            or current.get("oauthCredentialGeneration") != auth.credential_generation
            or current.get("oauthReconnectGeneration") != (
                auth.credential_generation if reconnect else None
            )
        ):
            raise MicrosoftActivationError("microsoft_mailbox_conflict")
        if current.get("connected") is True and current.get("connectionStatus") == ACTIVE_STATUS:
            return deepcopy(current)
        if current.get("connected") is not False or current.get("connectionStatus") != PENDING_STATUS:
            raise MicrosoftActivationError("microsoft_mailbox_conflict")

        # The credential must remain present and exact while config transitions
        # to active. If any read fails, the mailbox remains disconnected.
        try:
            token_authority.load_owned_microsoft_token(auth)
        except MicrosoftTokenError as error:
            raise MicrosoftActivationError(error.code) from None
        except Exception:
            raise MicrosoftActivationError("microsoft_token_store_unavailable") from None
        box = {
            **current,
            "connected": True,
            "connectionStatus": ACTIVE_STATUS,
            "connectionMessage": ACTIVE_MESSAGE,
        }
        box.pop("oauthReconnectGeneration", None)
        box.pop("oauthAuthorizationIssuedAt", None)
        box.pop("oauthAuthorizationNonce", None)
        intended = _config_mutation(config, auth.owner_email, box, replace=True)
        if _write_and_verify(store, auth.owner_email, "ok", config, intended) == "saved":
            return deepcopy(box)
    raise MicrosoftActivationError("microsoft_config_conflict")


def activate_initial_microsoft_mailbox(
    member: AuthenticatedMemberContext,
    identity: dict,
    credential_generation: str,
    token_payload: dict,
    *,
    authorization_issued_at: int,
    authorization_nonce: str,
) -> dict:
    """Initial connection, never reconnect or arbitrary browser mailbox IDs.

    Partial failure can leave a *disconnected* pending mailbox or credential,
    but never a falsely Connected mailbox. Reauthorization recovery will be
    handled separately before turning on the Microsoft UI.
    """
    if (
        type(authorization_issued_at) is not int
        or authorization_issued_at < 0
        or type(authorization_nonce) is not str
        or AUTHORIZATION_NONCE.fullmatch(authorization_nonce) is None
    ):
        raise MicrosoftActivationError("microsoft_authorization_invalid")
    auth, title = _prepare(member, identity, credential_generation, token_payload)
    store = _config_store()
    _stage(
        store, member, auth, title,
        issued_at=authorization_issued_at, nonce=authorization_nonce,
    )
    _ensure_token(auth, token_payload)
    result = _activate(store, member, auth)
    return result


def reserve_authoritative_microsoft_reconnect(
    headers,
    member: AuthenticatedMemberContext,
    mailbox_id: str,
    asserted_email: str,
    credential_generation: str,
) -> str:
    """CAS-reserve one EXACT owned mailbox before an OAuth reconnect redirect.

    The reservation blocks Graph operations immediately. Only a new verified
    OAuth grant for the same owner, workspace, mailbox and Graph account can
    re-enable the mailbox. A second reconnect may supersede an abandoned one.
    """
    owner = _member_owner(member)
    if (
        type(mailbox_id) is not str
        or not mailbox_id.startswith("microsoft-")
        or len(mailbox_id) != len("microsoft-") + 28
        or type(asserted_email) is not str
        or not is_valid_auth_email(asserted_email)
        or type(credential_generation) is not str
        or CREDENTIAL_GENERATION.fullmatch(credential_generation) is None
    ):
        raise MicrosoftActivationError("microsoft_mailbox_conflict")
    try:
        resolved = user_config_store.resolve_owned_managed_inbox_record(
            headers, mailbox_id, include_member_authority=True,
        )
    except Exception:
        raise MicrosoftActivationError("microsoft_config_unavailable") from None
    if not isinstance(resolved, dict):
        raise MicrosoftActivationError("microsoft_config_unavailable")
    status = resolved.get("status")
    if status != "ok":
        raise MicrosoftActivationError(
            "microsoft_unauthorized" if status == "unauthorized"
            else "microsoft_mailbox_not_found" if status == "not_found"
            else "microsoft_config_unavailable"
        )
    current_member = resolved.get("memberAuthority")
    user = resolved.get("user")
    stored_config = resolved.get("config")
    stored_mailbox = resolved.get("inbox")
    if (
        type(current_member) is not AuthenticatedMemberContext
        or current_member.membership_role != "owner"
        or current_member.user_id != owner.user_id
        or current_member.workspace_id != owner.workspace_id
        or current_member.email != owner.email
        or not isinstance(user, dict)
        or user.get("email") != normalize_auth_email(owner.email)
        or not isinstance(stored_config, dict)
        or stored_config.get("email") != normalize_auth_email(owner.email)
        or not isinstance(stored_mailbox, dict)
        or stored_mailbox.get("id") != mailbox_id
    ):
        raise MicrosoftActivationError("microsoft_mailbox_conflict")

    mailbox_email = stored_mailbox.get("email")
    account_id = stored_mailbox.get("oauthAccountId")
    previous_generation = stored_mailbox.get("oauthCredentialGeneration")
    if (
        type(mailbox_email) is not str
        or mailbox_email != normalize_auth_email(mailbox_email)
        or mailbox_email != normalize_auth_email(asserted_email)
        or type(account_id) is not str
        or type(previous_generation) is not str
        or CREDENTIAL_GENERATION.fullmatch(previous_generation) is None
    ):
        raise MicrosoftActivationError("microsoft_mailbox_conflict")
    auth = MicrosoftMailboxAuthority(
        normalize_auth_email(owner.email), mailbox_id, mailbox_email,
        account_id, credential_generation,
    )
    try:
        token_authority._canonical_authority(auth)
    except MicrosoftTokenError:
        raise MicrosoftActivationError("microsoft_mailbox_conflict") from None
    if mailbox_id != _mailbox_id(auth.owner_email, auth.email):
        raise MicrosoftActivationError("microsoft_mailbox_conflict")

    store = _config_store()
    for _ in range(MAX_CONFIG_ATTEMPTS):
        read_status, config = _read(store, auth.owner_email, allow_missing=False)
        current, _ = _find(config, auth)
        if (
            read_status != "ok"
            or not isinstance(current, dict)
            or not _stable_identity_matches(current, auth, owner)
        ):
            raise MicrosoftActivationError("microsoft_mailbox_conflict")
        active = (
            current.get("connected") is True
            and current.get("connectionStatus") == ACTIVE_STATUS
            and current.get("oauthReconnectGeneration") is None
        )
        previously_reserved = (
            current.get("connected") is False
            and current.get("connectionStatus") == PENDING_STATUS
            and type(current.get("oauthReconnectGeneration")) is str
            and CREDENTIAL_GENERATION.fullmatch(current["oauthReconnectGeneration"]) is not None
        )
        if not active and not previously_reserved:
            raise MicrosoftActivationError("microsoft_mailbox_conflict")
        stored_generation = current.get("oauthCredentialGeneration")
        if type(stored_generation) is not str or not CREDENTIAL_GENERATION.fullmatch(stored_generation):
            raise MicrosoftActivationError("microsoft_mailbox_conflict")
        if stored_generation == credential_generation:
            # A reconnect must rotate to a genuinely fresh state generation.
            # Never authorize a new consent using the generation of an old
            # token, even if a random-state collision were to occur.
            raise MicrosoftActivationError("microsoft_generation_conflict")
        replacement = {
            **current,
            "connected": False,
            "connectionStatus": PENDING_STATUS,
            "connectionMessage": PENDING_MESSAGE,
            "oauthCredentialGeneration": credential_generation,
            "oauthReconnectGeneration": credential_generation,
        }
        intended = _config_mutation(
            config, auth.owner_email, replacement, replace=True
        )
        if _write_and_verify(store, auth.owner_email, "ok", config, intended) == "saved":
            return auth.email
    raise MicrosoftActivationError("microsoft_config_conflict")


def activate_reconnected_microsoft_mailbox(
    member: AuthenticatedMemberContext,
    identity: dict,
    credential_generation: str,
    token_payload: dict,
    *,
    mailbox_id: str,
    expected_email: str,
) -> dict:
    """Complete only an exact, previously reserved reconnect OAuth state."""
    auth, _title = _prepare(member, identity, credential_generation, token_payload)
    if (
        type(mailbox_id) is not str
        or mailbox_id != auth.mailbox_id
        or type(expected_email) is not str
        or expected_email != auth.email
    ):
        raise MicrosoftActivationError("microsoft_mailbox_conflict")
    store = _config_store()
    _, config = _read(store, auth.owner_email, allow_missing=False)
    current, _ = _find(config, auth)
    if (
        not isinstance(current, dict)
        or not _stable_identity_matches(current, auth, member)
        or current.get("oauthCredentialGeneration") != auth.credential_generation
        or current.get("oauthReconnectGeneration") != auth.credential_generation
        or current.get("connected") is not False
        or current.get("connectionStatus") != PENDING_STATUS
    ):
        raise MicrosoftActivationError("microsoft_mailbox_conflict")
    _ensure_token(auth, token_payload)
    return _activate(store, member, auth, reconnect=True)
