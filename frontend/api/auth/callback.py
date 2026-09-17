from http.server import BaseHTTPRequestHandler

from api.auth import account_authority, auth0_flow, http, runtime, session_store


class _Stage:
    value = "callback_entered"


class _AuthorityProxy:
    def __init__(self, inner, stage: _Stage) -> None:
        self._inner = inner
        self._stage = stage

    def resolve_current_account_by_identity(self, key):
        self._stage.value = "authority_read_started"
        result = self._inner.resolve_current_account_by_identity(key)
        self._stage.value = "authority_read_completed"
        return result

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _TeamProxy:
    def __init__(self, inner, stage: _Stage) -> None:
        self._inner = inner
        self._stage = stage

    def read_provisioning_invitation(self, *args, **kwargs):
        self._stage.value = "invite_read_started"
        result = self._inner.read_provisioning_invitation(*args, **kwargs)
        self._stage.value = "invite_read_completed"
        return result

    def accept_invitation(self, *args, **kwargs):
        self._stage.value = "invite_accept_started"
        result = self._inner.accept_invitation(*args, **kwargs)
        self._stage.value = "invite_accept_completed"
        return result

    def prove_provisioning_acceptance(self, *args, **kwargs):
        self._stage.value = "invite_proof_started"
        result = self._inner.prove_provisioning_acceptance(*args, **kwargs)
        self._stage.value = "invite_proof_completed"
        return result

    def prove_provisioned_member(self, *args, **kwargs):
        self._stage.value = "provisioned_member_proof_started"
        result = self._inner.prove_provisioned_member(*args, **kwargs)
        self._stage.value = "provisioned_member_proof_completed"
        return result

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _InviteeRepositoryProxy:
    def __init__(self, inner, stage: _Stage) -> None:
        self._inner = inner
        self._stage = stage

    def prepare(self, *args, **kwargs):
        self._stage.value = "invitee_prepare_started"
        result = self._inner.prepare(*args, **kwargs)
        self._stage.value = "invitee_prepare_completed"
        return result

    def finalize(self, *args, **kwargs):
        self._stage.value = "invitee_finalize_started"
        result = self._inner.finalize(*args, **kwargs)
        self._stage.value = "invitee_finalize_completed"
        return result

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _is_generic_auth_failure(response) -> bool:
    if getattr(response, "status", None) != 303:
        return False
    return any(
        name.casefold() == "location" and value == "/login?error=authentication_failed"
        for name, value in getattr(response, "headers", ())
    )


class handler(BaseHTTPRequestHandler):
    def _respond(self) -> None:
        try:
            raw_headers = http.snapshot_request_headers(self)
        except http.HttpBoundaryError:
            raw_headers = ()

        stage = _Stage()

        def token_transport(request):
            stage.value = "token_exchange_started"
            result = auth0_flow.urllib_transport(request)
            stage.value = "token_exchange_completed"
            return result

        def jwks_transport(request):
            stage.value = "jwks_validation_started"
            result = auth0_flow.urllib_transport(request)
            stage.value = "jwks_validation_completed"
            return result

        def session_store_factory(environment):
            stage.value = "session_store_factory_started"
            result = session_store.build_runtime_session_store(environment)
            stage.value = "session_store_factory_completed"
            return result

        def authority_factory(environment):
            stage.value = "authority_factory_started"
            result = account_authority.build_runtime_account_authority(environment)
            stage.value = "authority_factory_completed"
            return _AuthorityProxy(result, stage)

        def team_authority_factory(environment):
            from api.team.authority import build_runtime_team_authority

            stage.value = "team_factory_started"
            result = build_runtime_team_authority(environment)
            stage.value = "team_factory_completed"
            return _TeamProxy(result, stage)

        def invitee_repository_factory(environment):
            stage.value = "invitee_repository_factory_started"
            result = account_authority.build_runtime_team_invitee_repository(environment)
            stage.value = "invitee_repository_factory_completed"
            return _InviteeRepositoryProxy(result, stage)

        response = runtime.callback_response(
            self.command,
            raw_headers,
            self.path,
            token_transport=token_transport,
            jwks_transport=jwks_transport,
            session_store_factory=session_store_factory,
            authority_factory=authority_factory,
            team_authority_factory=team_authority_factory,
            invitee_repository_factory=invitee_repository_factory,
        )
        if _is_generic_auth_failure(response):
            print(f"cuevion_auth_callback_failure_stage={stage.value}", flush=True)
        http.send_public_response(self, response)

    do_GET = _respond
    do_POST = _respond
    do_PUT = _respond
    do_PATCH = _respond
    do_DELETE = _respond
    do_OPTIONS = _respond
    do_HEAD = _respond
    do_TRACE = _respond
    do_CONNECT = _respond

    def log_message(self, _format, *_args):
        return
