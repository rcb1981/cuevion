import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlencode, urlsplit

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from user_config_store import (  # noqa: E402
    acquire_mailbox_mutation_lease,
    read_user_config_record,
    release_mailbox_mutation_lease,
    resolve_authenticated_member_authority,
    resolve_owned_managed_inbox_record,
    resolve_user_config_store,
    write_user_config_record_if_unchanged,
)
from api.auth.email_address import normalize_auth_email  # noqa: E402
from api.user.config import _classify_stored_onboarding_session  # noqa: E402

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_CALLBACK_PATH = "/api/inboxes/oauth-callback"
PUBLIC_APP_ORIGIN_ENV = "CUEVION_APP_URL"
PRODUCTION_APP_ORIGIN = "https://app.cuevion.com"
LOCAL_APP_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
MICROSOFT_AUTHORIZATION_ENDPOINT_TEMPLATE = (
    "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
)
DEFAULT_GOOGLE_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]
DEFAULT_MICROSOFT_SCOPES = [
    "openid",
    "email",
    "profile",
    "offline_access",
    "https://graph.microsoft.com/Mail.Read",
]
MAX_REQUEST_BODY_BYTES = 16 * 1024
OAUTH_STATE_VERSION = 3
OAUTH_STATE_TTL_SECONDS = 15 * 60
STATE_SIGNATURE_DOMAIN = "cuevion-oauth-state-signature:v3"
OWNER_BINDING_DOMAIN = "cuevion-oauth-owner-binding:v3"
PKCE_DERIVATION_DOMAIN = "cuevion-oauth-pkce:v3"
OAUTH_CREDENTIAL_GENERATION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
OAUTH_MAILBOX_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
MAX_RECONNECT_CONFIG_WRITE_ATTEMPTS = 3
ONBOARDING_CONFLICT_ERROR = {
    "ok": False,
    "error": {
        "code": "onboarding_state_conflict",
        "message": "The selected onboarding inbox is no longer available. Reload and try again.",
    },
}
PUBLIC_APP_ORIGIN_ERROR = {
    "ok": False,
    "error": {
        "code": "oauth_public_origin_invalid",
        "message": "OAuth public application origin is not configured safely.",
    },
}


def _deployment_environment() -> str:
    return os.getenv("VERCEL_ENV", "").strip().lower()


def _is_production_environment() -> bool:
    return _deployment_environment() == "production"


def _normalize_public_app_origin(
    value: str,
    *,
    production: bool,
) -> str | None:
    candidate = value.strip()
    if (
        not candidate
        or "?" in candidate
        or "#" in candidate
        or "\\" in candidate
        or any(character.isspace() for character in candidate)
    ):
        return None

    if candidate.endswith("/"):
        candidate = candidate[:-1]

    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return None

    hostname = parsed.hostname.lower() if isinstance(parsed.hostname, str) else ""
    if (
        not hostname
        or "*" in hostname
        or not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None

    scheme = parsed.scheme.lower()
    is_local = hostname in LOCAL_APP_HOSTS
    if production:
        if scheme != "https" or hostname != "app.cuevion.com" or port is not None:
            return None
    elif scheme != "https" and not (scheme == "http" and is_local):
        return None

    normalized_host = f"[{hostname}]" if ":" in hostname else hostname
    normalized_netloc = (
        f"{normalized_host}:{port}" if port is not None else normalized_host
    )
    if parsed.netloc.lower() != normalized_netloc.lower():
        return None

    normalized_origin = f"{scheme}://{normalized_netloc}"
    if production and normalized_origin != PRODUCTION_APP_ORIGIN:
        return None
    return normalized_origin


def resolve_public_app_origin(headers=None) -> str | None:
    deployment_environment = _deployment_environment()
    production = _is_production_environment()
    configured_origin = os.getenv(PUBLIC_APP_ORIGIN_ENV, "")
    if configured_origin.strip():
        normalized_origin = _normalize_public_app_origin(
            configured_origin,
            production=production,
        )
        if not normalized_origin:
            return None
        if normalized_origin == PRODUCTION_APP_ORIGIN:
            return normalized_origin

        parsed_origin = urlsplit(normalized_origin)
        if (
            deployment_environment == "preview"
            and parsed_origin.scheme == "https"
            and parsed_origin.hostname not in LOCAL_APP_HOSTS
        ):
            return normalized_origin
        if (
            deployment_environment in {"", "development"}
            and parsed_origin.hostname in LOCAL_APP_HOSTS
        ):
            return normalized_origin
        return None

    if deployment_environment not in {"", "development"} or headers is None:
        return None

    request_host = headers.get("host")
    if not isinstance(request_host, str) or not request_host.strip():
        return None

    forwarded_protocol = headers.get("x-forwarded-proto")
    protocol = (
        forwarded_protocol.strip().lower()
        if isinstance(forwarded_protocol, str) and forwarded_protocol.strip()
        else "http"
    )
    local_origin = _normalize_public_app_origin(
        f"{protocol}://{request_host.strip()}",
        production=False,
    )
    if not local_origin:
        return None
    parsed_local_origin = urlsplit(local_origin)
    if parsed_local_origin.hostname not in LOCAL_APP_HOSTS:
        return None
    return local_origin


def resolve_google_redirect_uri(headers=None) -> str | None:
    public_origin = resolve_public_app_origin(headers)
    if not public_origin:
        return None
    return f"{public_origin}{OAUTH_CALLBACK_PATH}"


def base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def build_owner_binding(
    *,
    owner_email: str,
    member_user_id: str,
    member_workspace_id: str,
    provider: str,
    email_hint: str,
    nonce: str,
    issued_at: int,
    expires_at: int,
    signing_secret: str,
    inbox_position: str | None = None,
    mode: str = "initial",
    mailbox_id: str | None = None,
    expected_email: str | None = None,
    credential_generation: str | None = None,
) -> str:
    normalized_owner = normalize_auth_email(owner_email)
    normalized_user_id = (
        member_user_id.strip() if isinstance(member_user_id, str) else ""
    )
    normalized_workspace_id = (
        member_workspace_id.strip()
        if isinstance(member_workspace_id, str)
        else ""
    )
    if not normalized_user_id or not normalized_workspace_id:
        raise ValueError("Authenticated member context is required.")
    binding_fields = [
        OWNER_BINDING_DOMAIN,
        str(OAUTH_STATE_VERSION),
        normalized_owner,
        normalized_user_id,
        normalized_workspace_id,
        provider,
        email_hint,
        mode,
        credential_generation or "",
    ]
    if mode == "reconnect":
        binding_fields.extend((mailbox_id or "", expected_email or ""))
    if inbox_position is not None:
        binding_fields.append(inbox_position)
    binding_fields.extend((nonce, str(issued_at), str(expires_at)))
    binding_message = json.dumps(
        binding_fields,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return base64url_encode(
        hmac.new(
            signing_secret.encode("utf-8"),
            binding_message.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    )


def build_signed_state(
    provider: str,
    email_hint: str,
    owner_email: str,
    signing_secret: str,
    inbox_position: str | None = None,
    *,
    member_user_id: str,
    member_workspace_id: str,
    mode: str = "initial",
    mailbox_id: str | None = None,
    expected_email: str | None = None,
    credential_generation: str | None = None,
) -> tuple[str, str]:
    generation = credential_generation or secrets.token_urlsafe(32)
    if not OAUTH_CREDENTIAL_GENERATION_PATTERN.fullmatch(generation):
        raise ValueError("OAuth credential generation is invalid.")
    if mode not in {"initial", "reconnect"}:
        raise ValueError("OAuth connection mode is invalid.")
    if mode == "reconnect":
        if (
            provider != "google"
            or not isinstance(mailbox_id, str)
            or OAUTH_MAILBOX_ID_PATTERN.fullmatch(mailbox_id) is None
            or not isinstance(expected_email, str)
            or expected_email != expected_email.strip().lower()
            or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", expected_email)
            or inbox_position is not None
        ):
            raise ValueError("OAuth reconnect target is invalid.")
    elif mailbox_id is not None or expected_email is not None:
        raise ValueError("Initial OAuth state cannot contain a reconnect target.")

    issued_at = int(time.time())
    expires_at = issued_at + OAUTH_STATE_TTL_SECONDS
    nonce = secrets.token_urlsafe(16)
    state_payload = {
        "v": OAUTH_STATE_VERSION,
        "provider": provider,
        "email_hint": email_hint,
        "mode": mode,
        "credential_generation": generation,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "nonce": nonce,
        "owner_binding": build_owner_binding(
            owner_email=owner_email,
            member_user_id=member_user_id,
            member_workspace_id=member_workspace_id,
            provider=provider,
            email_hint=email_hint,
            nonce=nonce,
            issued_at=issued_at,
            expires_at=expires_at,
            signing_secret=signing_secret,
            inbox_position=inbox_position,
            mode=mode,
            mailbox_id=mailbox_id,
            expected_email=expected_email,
            credential_generation=generation,
        ),
    }
    if mode == "reconnect":
        state_payload["mailboxId"] = mailbox_id
        state_payload["expected_email"] = expected_email
    if inbox_position is not None:
        state_payload["inboxPosition"] = inbox_position
    encoded_payload = base64url_encode(
        json.dumps(state_payload, separators=(",", ":"), sort_keys=True).encode("utf-8"),
    )
    signature = base64url_encode(
        hmac.new(
            signing_secret.encode("utf-8"),
            f"{STATE_SIGNATURE_DOMAIN}:{encoded_payload}".encode("utf-8"),
            hashlib.sha256,
        ).digest(),
    )
    code_verifier = base64url_encode(
        hmac.new(
            signing_secret.encode("utf-8"),
            f"{PKCE_DERIVATION_DOMAIN}:{encoded_payload}".encode("utf-8"),
            hashlib.sha256,
        ).digest(),
    )
    return f"{encoded_payload}.{signature}", code_verifier


def build_code_challenge(code_verifier: str) -> str:
    return base64url_encode(
        hashlib.sha256(code_verifier.encode("utf-8")).digest(),
    )


def resolve_google_scopes() -> list[str]:
    configured_scopes = os.getenv("GOOGLE_OAUTH_SCOPES", "").strip()
    if not configured_scopes:
        return DEFAULT_GOOGLE_SCOPES

    return [scope for scope in configured_scopes.split() if scope]


def resolve_microsoft_scopes() -> list[str]:
    configured_scopes = os.getenv("MICROSOFT_OAUTH_SCOPES", "").strip()
    if not configured_scopes:
        return DEFAULT_MICROSOFT_SCOPES

    return [scope for scope in configured_scopes.split() if scope]


def resolve_authoritative_onboarding_position(
    owner_email: str,
    inbox_position: str,
) -> tuple[str | None, int | None, dict | None]:
    store, store_error = resolve_user_config_store()
    if store_error or not store:
        return None, 503, {
            "ok": False,
            "error": {
                "code": "user_config_store_unavailable",
                "message": "User config storage is temporarily unavailable.",
            },
        }

    read_result = read_user_config_record(store, owner_email)
    if read_result["status"] == "unavailable":
        return None, 503, {
            "ok": False,
            "error": {
                "code": "user_config_store_unavailable",
                "message": "User config storage is temporarily unavailable.",
            },
        }
    if read_result["status"] != "ok" or not isinstance(read_result["config"], dict):
        return None, 409, ONBOARDING_CONFLICT_ERROR

    config = read_result["config"]
    stored_owner = config.get("email")
    if stored_owner is not None and (
        not isinstance(stored_owner, str)
        or normalize_auth_email(stored_owner)
        != normalize_auth_email(owner_email)
    ):
        return None, 409, ONBOARDING_CONFLICT_ERROR

    session_state, normalized_session = _classify_stored_onboarding_session(
        config.get("onboardingSession")
    )
    if (
        getattr(session_state, "value", None) != "valid"
        or not isinstance(normalized_session, dict)
        or normalized_session.get("schemaVersion") != 1
        or normalized_session.get("completed") is not False
    ):
        return None, 409, ONBOARDING_CONFLICT_ERROR

    choices = normalized_session.get("choices")
    selected_inboxes = choices.get("selectedInboxes") if isinstance(choices, dict) else None
    if not isinstance(selected_inboxes, list) or inbox_position not in selected_inboxes:
        return None, 409, ONBOARDING_CONFLICT_ERROR

    return inbox_position, None, None


def _reconnect_error(status_code: int, code: str, message: str):
    return None, status_code, {
        "ok": False,
        "error": {"code": code, "message": message},
    }


def _resolve_reconnect_target_index(config: dict, mailbox_id: str) -> int | None:
    managed_inboxes = config.get("managedInboxes")
    if not isinstance(managed_inboxes, list):
        return None
    exact_matches = [
        index
        for index, inbox in enumerate(managed_inboxes)
        if isinstance(inbox, dict) and inbox.get("id") == mailbox_id
    ]
    casefold_matches = [
        inbox
        for inbox in managed_inboxes
        if isinstance(inbox, dict)
        and isinstance(inbox.get("id"), str)
        and inbox["id"].casefold() == mailbox_id.casefold()
    ]
    if len(exact_matches) != 1 or len(casefold_matches) != 1:
        return None
    return exact_matches[0]


def reserve_authoritative_google_reconnect(
    headers,
    session_member,
    mailbox_id: str,
    asserted_email: str,
    credential_generation: str,
) -> tuple[str | None, int | None, dict | None]:
    """CAS-reserve one exact owned Gmail target without changing its live status."""
    store, store_error = resolve_user_config_store()
    if store_error or not store:
        return _reconnect_error(
            503,
            "user_config_store_unavailable",
            "Mailbox configuration is temporarily unavailable.",
        )

    for _attempt in range(MAX_RECONNECT_CONFIG_WRITE_ATTEMPTS):
        owned_result = resolve_owned_managed_inbox_record(
            headers,
            mailbox_id,
            include_member_authority=True,
        )
        owned_status = owned_result.get("status")
        if owned_status == "unauthorized":
            return _reconnect_error(
                401,
                "unauthorized",
                "A valid member session is required.",
            )
        if owned_status == "unavailable":
            return _reconnect_error(
                503,
                "user_config_store_unavailable",
                "Mailbox configuration is temporarily unavailable.",
            )
        if owned_status == "not_found":
            return _reconnect_error(
                404,
                "managed_inbox_not_found",
                "The requested mailbox was not found.",
            )
        if owned_status != "ok":
            return _reconnect_error(
                409,
                "oauth_reconnect_target_invalid",
                "The selected Google mailbox cannot be reconnected safely.",
            )

        member_authority = owned_result.get("memberAuthority")
        inbox = owned_result.get("inbox")
        config = owned_result.get("config")
        if (
            member_authority is None
            or member_authority.user_id != session_member.user_id
            or member_authority.workspace_id != session_member.workspace_id
            or not isinstance(inbox, dict)
            or not isinstance(config, dict)
            or inbox.get("provider") != "google"
        ):
            return _reconnect_error(
                409,
                "oauth_reconnect_target_invalid",
                "The selected Google mailbox cannot be reconnected safely.",
            )

        raw_expected_email = inbox.get("email")
        expected_email = (
            normalize_auth_email(raw_expected_email)
            if isinstance(raw_expected_email, str)
            else ""
        )
        stored_owner = inbox.get("oauthOwnerEmail")
        if (
            not expected_email
            or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", expected_email)
            or (
                stored_owner is not None
                and (
                    not isinstance(stored_owner, str)
                    or normalize_auth_email(stored_owner)
                    != normalize_auth_email(session_member.email)
                )
            )
        ):
            return _reconnect_error(
                409,
                "oauth_reconnect_target_invalid",
                "The selected Google mailbox cannot be reconnected safely.",
            )
        if asserted_email and normalize_auth_email(asserted_email) != expected_email:
            return _reconnect_error(
                409,
                "oauth_reconnect_email_mismatch",
                f"Reconnect this mailbox using the Google account for {expected_email}.",
            )

        target_index = _resolve_reconnect_target_index(config, mailbox_id)
        if target_index is None:
            return _reconnect_error(
                409,
                "oauth_reconnect_target_invalid",
                "The selected Google mailbox cannot be reconnected safely.",
            )
        managed_inboxes = config.get("managedInboxes")
        matching_email_mailboxes = (
            [
                candidate
                for candidate in managed_inboxes
                if isinstance(candidate, dict)
                and isinstance(candidate.get("email"), str)
                and normalize_auth_email(candidate["email"]) == expected_email
            ]
            if isinstance(managed_inboxes, list)
            else []
        )
        if len(matching_email_mailboxes) != 1:
            return _reconnect_error(
                409,
                "oauth_reconnect_target_invalid",
                "The selected Google mailbox cannot be reconnected safely.",
            )

        replacement = deepcopy(config)
        replacement_target = deepcopy(replacement["managedInboxes"][target_index])
        replacement_target["oauthReconnectGeneration"] = credential_generation
        replacement["managedInboxes"][target_index] = replacement_target
        replacement["updatedAt"] = (
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )
        write_result = write_user_config_record_if_unchanged(
            store,
            session_member.email,
            config,
            replacement,
        )
        if not isinstance(write_result, dict):
            return _reconnect_error(
                503,
                "user_config_store_unavailable",
                "Mailbox configuration is temporarily unavailable.",
            )
        if write_result.get("status") == "ok":
            return expected_email, None, None
        if write_result.get("status") == "conflict":
            continue
        return _reconnect_error(
            503,
            "user_config_store_unavailable",
            "Mailbox configuration is temporarily unavailable.",
        )

    return _reconnect_error(
        409,
        "oauth_reconnect_stale",
        "The mailbox changed before reconnect could start. Reload and try again.",
    )


class handler(BaseHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        if code == HTTPStatus.NOT_IMPLEMENTED:
            self.close_connection = True
            self._send_json(
                405,
                {"ok": False, "error": {"code": "method_not_allowed", "message": "Use POST to start inbox authentication"}},
                write_body=getattr(self, "command", "") != "HEAD",
            )
            return
        super().send_error(code, message, explain)

    def _send_json(self, status_code: int, payload: dict, *, write_body: bool = True):
        response_body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        if write_body:
            self.wfile.write(response_body)

    def do_POST(self):
        try:
            session_member, auth_error = resolve_authenticated_member_authority(
                self.headers
            )
            if not session_member:
                if auth_error and auth_error.get("code") == "session_auth_unavailable":
                    self._send_json(
                        503,
                        {"ok": False, "error": {"code": "session_auth_unavailable", "message": "Authentication is temporarily unavailable."}},
                    )
                else:
                    self._send_json(
                        401,
                        {"ok": False, "error": {"code": "unauthorized", "message": "A valid member session is required."}},
                    )
                return

            try:
                content_length = int(self.headers.get("content-length", "0"))
            except (TypeError, ValueError):
                self._send_json(400, {"ok": False, "error": {"code": "invalid_request", "message": "Content-Length must be valid."}})
                return
            if content_length < 0 or content_length > MAX_REQUEST_BODY_BYTES:
                self._send_json(400, {"ok": False, "error": {"code": "invalid_request", "message": "Request body size is invalid."}})
                return
            raw_body = (
                self.rfile.read(content_length)
                if content_length > 0
                else b""
            )

            try:
                payload = json.loads(raw_body.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(
                    400,
                    {
                        "ok": False,
                        "error": {
                            "code": "invalid_request",
                            "message": "Request body must be valid JSON",
                        },
                    },
                )
                return
            if not isinstance(payload, dict) or set(payload) - {
                "provider",
                "email",
                "inboxPosition",
                "mode",
                "mailboxId",
            }:
                self._send_json(400, {"ok": False, "error": {"code": "invalid_request", "message": "Request body contains unsupported fields."}})
                return

            provider = payload.get("provider")
            raw_email = payload.get("email", "")
            email = raw_email.strip().lower() if isinstance(raw_email, str) else ""
            inbox_position = payload.get("inboxPosition")
            has_inbox_position = "inboxPosition" in payload
            mode = payload.get("mode", "initial")
            mailbox_id = payload.get("mailboxId")
            email_pattern = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

            if not isinstance(provider, str) or provider not in ("google", "microsoft"):
                self._send_json(
                    400,
                    {
                        "ok": False,
                        "error": {
                            "code": "unsupported_provider",
                            "message": "OAuth is not configured for this provider.",
                        },
                    },
                )
                return

            if mode not in {"initial", "reconnect"}:
                self._send_json(
                    400,
                    {
                        "ok": False,
                        "error": {
                            "code": "invalid_request",
                            "message": "OAuth connection mode is invalid.",
                        },
                    },
                )
                return

            if mode == "reconnect":
                if (
                    provider != "google"
                    or not isinstance(mailbox_id, str)
                    or OAUTH_MAILBOX_ID_PATTERN.fullmatch(mailbox_id) is None
                    or has_inbox_position
                ):
                    self._send_json(
                        400,
                        {
                            "ok": False,
                            "error": {
                                "code": "invalid_request",
                                "message": "Google reconnect requires one exact mailbox target.",
                            },
                        },
                    )
                    return
            elif "mailboxId" in payload:
                self._send_json(
                    400,
                    {
                        "ok": False,
                        "error": {
                            "code": "invalid_request",
                            "message": "Initial OAuth setup cannot target an existing mailbox.",
                        },
                    },
                )
                return

            if "email" in payload and not isinstance(raw_email, str):
                self._send_json(
                    400,
                    {
                        "ok": False,
                        "error": {
                            "code": "invalid_request",
                            "message": "Email hint must be a string.",
                        },
                    },
                )
                return

            if email and not email_pattern.match(email):
                email_message = (
                    "Email hint must be a valid Gmail or Google Workspace address."
                    if provider == "google"
                    else "Email hint must be a valid Microsoft 365 or Outlook address."
                )
                self._send_json(
                    400,
                    {
                        "ok": False,
                        "error": {
                            "code": "invalid_request",
                            "message": email_message,
                        },
                    },
                )
                return

            if has_inbox_position:
                if provider != "google" or not isinstance(inbox_position, str):
                    self._send_json(
                        400,
                        {
                            "ok": False,
                            "error": {
                                "code": "invalid_request",
                                "message": "Onboarding inbox position is invalid.",
                            },
                        },
                    )
                    return
                (
                    inbox_position,
                    onboarding_error_status,
                    onboarding_error_payload,
                ) = resolve_authoritative_onboarding_position(
                    session_member.email,
                    inbox_position,
                )
                if onboarding_error_status is not None and onboarding_error_payload:
                    self._send_json(
                        onboarding_error_status,
                        onboarding_error_payload,
                    )
                    return

            if provider == "google":
                client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
                client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
                redirect_uri = resolve_google_redirect_uri(self.headers)
                oauth_state_secret = (
                    os.getenv("CUEVION_OAUTH_STATE_SECRET", "").strip()
                    or client_secret
                )
            else:
                client_id = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
                client_secret = os.getenv("MICROSOFT_CLIENT_SECRET", "").strip()
                redirect_uri = os.getenv("MICROSOFT_OAUTH_REDIRECT_URI", "").strip()
                oauth_state_secret = (
                    os.getenv("CUEVION_OAUTH_STATE_SECRET", "").strip()
                    or client_secret
                )

            if (
                not client_id
                or not client_secret
                or (provider == "microsoft" and not redirect_uri)
            ):
                config_message = (
                    "Google OAuth is not configured safely."
                    if provider == "google"
                    else "Microsoft OAuth is not configured. Set MICROSOFT_CLIENT_ID, "
                    "MICROSOFT_CLIENT_SECRET, and MICROSOFT_OAUTH_REDIRECT_URI."
                )
                self._send_json(
                    503,
                    {
                        "ok": False,
                        "error": {
                            "code": "oauth_not_configured",
                            "message": config_message,
                        },
                    },
                )
                return

            if provider == "google" and not redirect_uri:
                self._send_json(503, PUBLIC_APP_ORIGIN_ERROR)
                return

            if provider == "microsoft" and (
                not redirect_uri
                or not redirect_uri.startswith(("https://", "http://"))
            ):
                self._send_json(
                    503,
                    {
                        "ok": False,
                        "error": {
                            "code": "oauth_invalid_redirect_uri",
                            "message": "MICROSOFT_OAUTH_REDIRECT_URI must be an absolute URL.",
                        },
                    },
                )
                return

            credential_generation = secrets.token_urlsafe(32)
            if not OAUTH_CREDENTIAL_GENERATION_PATTERN.fullmatch(
                credential_generation
            ):
                self._send_json(
                    503,
                    {
                        "ok": False,
                        "error": {
                            "code": "oauth_not_configured",
                            "message": "OAuth reconnect state could not be prepared safely.",
                        },
                    },
                )
                return

            expected_email = None
            if mode == "reconnect":
                lease_result = acquire_mailbox_mutation_lease(
                    session_member.email,
                    mailbox_id,
                )
                if lease_result.get("status") != "acquired" or not isinstance(
                    lease_result.get("token"), str
                ):
                    lease_status = lease_result.get("status")
                    self._send_json(
                        409 if lease_status == "held" else 503,
                        {
                            "ok": False,
                            "error": {
                                "code": (
                                    "oauth_reconnect_in_progress"
                                    if lease_status == "held"
                                    else "user_config_store_unavailable"
                                ),
                                "message": (
                                    "Another reconnect is already in progress for this mailbox."
                                    if lease_status == "held"
                                    else "Mailbox configuration is temporarily unavailable."
                                ),
                            },
                        },
                    )
                    return

                lease_token = lease_result["token"]
                try:
                    (
                        expected_email,
                        reconnect_error_status,
                        reconnect_error_payload,
                    ) = reserve_authoritative_google_reconnect(
                        self.headers,
                        session_member,
                        mailbox_id,
                        email,
                        credential_generation,
                    )
                finally:
                    release_mailbox_mutation_lease(
                        session_member.email,
                        mailbox_id,
                        lease_token,
                    )
                if reconnect_error_status is not None and reconnect_error_payload:
                    self._send_json(
                        reconnect_error_status,
                        reconnect_error_payload,
                    )
                    return
                if not isinstance(expected_email, str):
                    self._send_json(
                        503,
                        {
                            "ok": False,
                            "error": {
                                "code": "user_config_store_unavailable",
                                "message": "Mailbox configuration is temporarily unavailable.",
                            },
                        },
                    )
                    return
                email = expected_email

            authorization_state, code_verifier = build_signed_state(
                provider,
                email,
                session_member.email,
                oauth_state_secret,
                inbox_position,
                member_user_id=session_member.user_id,
                member_workspace_id=session_member.workspace_id,
                mode=mode,
                mailbox_id=mailbox_id if mode == "reconnect" else None,
                expected_email=expected_email,
                credential_generation=credential_generation,
            )
            if provider == "google":
                authorization_params = {
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "scope": " ".join(resolve_google_scopes()),
                    "access_type": "offline",
                    "include_granted_scopes": "true",
                    "prompt": "consent",
                    "state": authorization_state,
                    "code_challenge": build_code_challenge(code_verifier),
                    "code_challenge_method": "S256",
                }
                if email:
                    authorization_params["login_hint"] = email
                authorization_url = (
                    f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{urlencode(authorization_params)}"
                )
                message = "Continue with Google to finish authentication."
            else:
                microsoft_tenant = os.getenv("MICROSOFT_OAUTH_TENANT", "").strip() or "common"
                authorization_params = {
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "response_mode": "query",
                    "scope": " ".join(resolve_microsoft_scopes()),
                    "prompt": "select_account",
                    "state": authorization_state,
                    "code_challenge": build_code_challenge(code_verifier),
                    "code_challenge_method": "S256",
                }
                if email:
                    authorization_params["login_hint"] = email
                authorization_url = (
                    f"{MICROSOFT_AUTHORIZATION_ENDPOINT_TEMPLATE.format(tenant=microsoft_tenant)}"
                    f"?{urlencode(authorization_params)}"
                )
                message = "Continue with Microsoft to finish authentication."

            self._send_json(
                200,
                {
                    "ok": True,
                    "connectionMethod": "oauth",
                    "connectionStatus": "waiting_for_authentication",
                    "authorizationUrl": authorization_url,
                    "message": message,
                    "mode": mode,
                    **(
                        {"mailboxId": mailbox_id}
                        if mode == "reconnect"
                        else {}
                    ),
                },
            )
        except Exception:
            self._send_json(
                500,
                {
                    "ok": False,
                    "error": {
                        "code": "server_error",
                        "message": "OAuth could not be started.",
                    },
                },
            )

    def do_GET(self):
        self._send_json(
            405,
            {
                "ok": False,
                "error": {
                    "code": "method_not_allowed",
                    "message": "Use POST to start inbox authentication",
                },
            },
        )

    def do_OPTIONS(self):
        self._send_json(200, {"ok": True})

    def do_PUT(self):
        self.do_GET()

    def do_PATCH(self):
        self.do_GET()

    def do_DELETE(self):
        self.do_GET()

    def do_HEAD(self):
        self._send_json(
            405,
            {"ok": False, "error": {"code": "method_not_allowed", "message": "Use POST to start inbox authentication"}},
            write_body=False,
        )

    def log_message(self, format, *args):
        return
