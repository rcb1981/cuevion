"""Decorate an already-authorized Team Invite Auth0 redirect with signed proof."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from api.auth import auth0_flow, http, runtime
from api.auth.invite_registration_proof import build_invite_registration_acr


_OAUTH_STATE_RE = re.compile(r"[A-Za-z0-9_-]{43}")


def _response_header(response: http.PublicResponse, name: str) -> str | None:
    values = [value for key, value in response.headers if key.casefold() == name.casefold()]
    return values[0] if len(values) == 1 else None


def _response_cookies(response: http.PublicResponse) -> tuple[str, ...]:
    return tuple(value for key, value in response.headers if key.casefold() == "set-cookie")


def decorate_team_invite_redirect(
    response: http.PublicResponse,
    raw_path: str,
    *,
    environment: Mapping[str, str] | None = None,
    now: int | None = None,
    team_authority_factory=None,
) -> http.PublicResponse:
    """Add one signed invite ACR only to the Auth0 invite redirect.

    ``runtime.login_response`` remains the request/authentication boundary and
    validates the raw Team bearer before this decorator runs. This function
    repeats the authoritative invitation read so the proof is minted only from
    current server-side invitation metadata. Non-Auth0 responses are preserved.
    """

    if response.status not in (302, 303):
        return response

    location = _response_header(response, "location")
    if location is None:
        return response
    parsed = urlsplit(location)
    if (
        parsed.scheme != "https"
        or parsed.netloc != auth0_flow.AUTH0_DOMAIN
        or parsed.path != "/authorize"
        or parsed.fragment
    ):
        return response

    try:
        token = runtime._parse_login_invite(raw_path)
        if token is None:
            return response

        pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=32,
            encoding="utf-8",
            errors="strict",
        )
        if any(name == "acr_values" for name, _value in pairs):
            raise ValueError("ambiguous authorization context")
        states = [value for name, value in pairs if name == "state"]
        if len(states) != 1 or _OAUTH_STATE_RE.fullmatch(states[0]) is None:
            raise ValueError("invalid authorization state")

        source = os.environ if environment is None else environment
        configuration = auth0_flow.parse_auth0_configuration(source)
        client_ids = [value for name, value in pairs if name == "client_id"]
        if client_ids != [configuration.client_id]:
            raise ValueError("invalid authorization client")
        team = runtime._team_authority(source, team_authority_factory)
        invitation = team.read_provisioning_invitation(token, allow_accepted=True)
        issued_at = int(time.time()) if now is None else now
        proof = build_invite_registration_acr(
            source,
            client_id=configuration.client_id,
            oauth_state=states[0],
            email=invitation.email,
            invitation_id=invitation.invitation_id,
            token_digest=invitation.token_digest,
            now=issued_at,
            invitation_expires_at_ms=invitation.expires_at,
        )
        pairs.append(("acr_values", proof))
        decorated = urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(pairs), "")
        )
        return http.redirect_response(
            decorated,
            status=response.status,
            set_cookies=_response_cookies(response),
        )
    except Exception:
        return runtime._authentication_unavailable_response()
