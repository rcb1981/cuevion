import smtplib
import ssl
import sys
from contextlib import contextmanager


_CANONICAL_MODULE_NAME = "api.inboxes.smtp_connection"
_LEGACY_MODULE_NAME = "smtp_connection"
if __name__ == _CANONICAL_MODULE_NAME:
    sys.modules.setdefault(_LEGACY_MODULE_NAME, sys.modules[__name__])
elif __name__ == _LEGACY_MODULE_NAME:
    sys.modules.setdefault(_CANONICAL_MODULE_NAME, sys.modules[__name__])


try:
    from .imap_network_policy import (
        ImapNetworkPolicyError,
        close_socket_quietly,
        connect_to_resolved_destination,
        resolve_public_destination,
        validate_resolved_destination_peer,
    )
except ImportError:
    from imap_network_policy import (
        ImapNetworkPolicyError,
        close_socket_quietly,
        connect_to_resolved_destination,
        resolve_public_destination,
        validate_resolved_destination_peer,
    )


DEFAULT_SMTP_TIMEOUT_SECONDS = 30
_SUPPORTED_SMTP_TRANSPORTS = {
    ("ssl", 465),
    ("starttls", 587),
}
_NETWORK_POLICY_ERROR_CODES = {
    "imap_host_invalid": "smtp_host_invalid",
    "imap_destination_not_allowed": "smtp_destination_not_allowed",
    "imap_dns_failed": "smtp_dns_failed",
    "imap_peer_mismatch": "smtp_peer_mismatch",
    "imap_connection_failed": "smtp_connection_failed",
}
_SMTP_ERROR_HTTP_STATUS = {
    "smtp_transport_not_allowed": 400,
    "smtp_credentials_invalid": 400,
    "smtp_host_invalid": 400,
    "smtp_authentication_failed": 401,
    "smtp_destination_not_allowed": 502,
    "smtp_dns_failed": 502,
    "smtp_peer_mismatch": 502,
    "smtp_tls_failed": 502,
    "smtp_connection_failed": 502,
    "smtp_send_failed": 502,
}
_SMTP_CONFIG_FIELDS = {
    "host",
    "port",
    "security",
    "username",
    "password",
}


class SmtpConnectionError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _PreconnectedSMTP(smtplib.SMTP):
    def __init__(
        self,
        host: str,
        port: int,
        connected_socket,
        *,
        timeout: float | None,
    ):
        self._preconnected_socket = connected_socket
        super().__init__(host=host, port=port, timeout=timeout)

    def _get_socket(self, host, port, timeout):
        connected_socket = self._preconnected_socket
        self._preconnected_socket = None
        if connected_socket is None:
            raise OSError("preconnected SMTP socket unavailable")
        return connected_socket


def _validated_transport(port, security) -> tuple[int, str]:
    if isinstance(port, bool) or not isinstance(security, str):
        raise SmtpConnectionError("smtp_transport_not_allowed")
    try:
        normalized_port = int(str(port))
    except (TypeError, ValueError):
        raise SmtpConnectionError("smtp_transport_not_allowed") from None
    normalized_security = security
    if (normalized_security, normalized_port) not in _SUPPORTED_SMTP_TRANSPORTS:
        raise SmtpConnectionError("smtp_transport_not_allowed")
    return normalized_port, normalized_security


def _validated_credentials(username, password) -> tuple[str, str]:
    if (
        not isinstance(username, str)
        or not username.strip()
        or not isinstance(password, str)
        or not password.strip()
    ):
        raise SmtpConnectionError("smtp_credentials_invalid")
    return username.strip(), password


def _verified_ssl_context() -> ssl.SSLContext:
    try:
        context = ssl.create_default_context()
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
    except Exception:
        raise SmtpConnectionError("smtp_tls_failed") from None
    if (
        context.check_hostname is not True
        or context.verify_mode != ssl.CERT_REQUIRED
    ):
        raise SmtpConnectionError("smtp_tls_failed")
    return context


def _require_ehlo_success(reply) -> None:
    code = reply[0] if isinstance(reply, tuple) and reply else None
    if (
        isinstance(code, bool)
        or not isinstance(code, int)
        or code < 200
        or code >= 300
    ):
        raise SmtpConnectionError("smtp_connection_failed")


def _require_starttls_success(reply) -> None:
    code = reply[0] if isinstance(reply, tuple) and reply else None
    if isinstance(code, bool) or code != 220:
        raise SmtpConnectionError("smtp_tls_failed")


def _map_network_policy_error(error: ImapNetworkPolicyError) -> SmtpConnectionError:
    return SmtpConnectionError(
        _NETWORK_POLICY_ERROR_CODES.get(error.code, "smtp_connection_failed")
    )


@contextmanager
def open_authenticated_public_smtp_connection(
    host,
    port,
    security,
    username,
    password,
    *,
    timeout: float | None = DEFAULT_SMTP_TIMEOUT_SECONDS,
):
    """Yield one authenticated client over a pinned, verified SMTP transport."""
    normalized_port, normalized_security = _validated_transport(port, security)
    normalized_username, validated_password = _validated_credentials(
        username,
        password,
    )
    if timeout is not None and (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or timeout <= 0
    ):
        raise SmtpConnectionError("smtp_connection_failed")

    raw_socket = None
    tls_socket = None
    client = None
    try:
        destination = resolve_public_destination(host, normalized_port)
        raw_socket = connect_to_resolved_destination(destination, timeout)
        context = _verified_ssl_context()

        if normalized_security == "ssl":
            tls_socket = context.wrap_socket(
                raw_socket,
                server_hostname=destination.host,
            )
            validate_resolved_destination_peer(
                tls_socket,
                destination,
                expected_ip=destination.addresses[0][4],
            )
            raw_socket = None
            client = _PreconnectedSMTP(
                destination.host,
                destination.port,
                tls_socket,
                timeout=timeout,
            )
            tls_socket = None
            _require_ehlo_success(client.ehlo())
        else:
            client = _PreconnectedSMTP(
                destination.host,
                destination.port,
                raw_socket,
                timeout=timeout,
            )
            raw_socket = None
            _require_ehlo_success(client.ehlo())
            _require_starttls_success(client.starttls(context=context))
            validate_resolved_destination_peer(
                client.sock,
                destination,
                expected_ip=destination.addresses[0][4],
            )
            _require_ehlo_success(client.ehlo())

        client.login(normalized_username, validated_password)
        yield client
    except SmtpConnectionError:
        raise
    except ImapNetworkPolicyError as error:
        raise _map_network_policy_error(error) from None
    except smtplib.SMTPAuthenticationError:
        raise SmtpConnectionError("smtp_authentication_failed") from None
    except (ssl.SSLCertVerificationError, ssl.SSLError):
        raise SmtpConnectionError("smtp_tls_failed") from None
    except (OSError, smtplib.SMTPException):
        raise SmtpConnectionError("smtp_connection_failed") from None
    except Exception:
        raise SmtpConnectionError("smtp_connection_failed") from None
    finally:
        if client is not None:
            close_socket_quietly(client)
        elif tls_socket is not None:
            close_socket_quietly(tls_socket)
        elif raw_socket is not None:
            close_socket_quietly(raw_socket)


def authenticate_public_smtp_connection(
    host,
    port,
    security,
    username,
    password,
    *,
    timeout: float | None = DEFAULT_SMTP_TIMEOUT_SECONDS,
) -> None:
    """Authenticate over a verified public SMTP transport without sending mail."""
    with open_authenticated_public_smtp_connection(
        host,
        port,
        security,
        username,
        password,
        timeout=timeout,
    ):
        pass


def send_public_smtp_message(
    host,
    port,
    security,
    username,
    password,
    message,
    recipients,
    *,
    timeout: float | None = DEFAULT_SMTP_TIMEOUT_SECONDS,
) -> None:
    """Send exactly one already-built message over the safe SMTP transport."""
    if (
        not isinstance(recipients, (list, tuple))
        or not recipients
        or any(
            not isinstance(recipient, str) or not recipient.strip()
            for recipient in recipients
        )
    ):
        raise SmtpConnectionError("smtp_send_failed")

    with open_authenticated_public_smtp_connection(
        host,
        port,
        security,
        username,
        password,
        timeout=timeout,
    ) as client:
        try:
            client.send_message(message, to_addrs=recipients)
        except SmtpConnectionError:
            raise
        except Exception:
            raise SmtpConnectionError("smtp_send_failed") from None


def test_smtp_authentication(
    config,
    *,
    timeout: float | None = DEFAULT_SMTP_TIMEOUT_SECONDS,
) -> tuple[int, dict]:
    """Return a safe HTTP-ready result after SMTP connect, TLS, and AUTH only."""
    if not isinstance(config, dict) or set(config) != _SMTP_CONFIG_FIELDS:
        return 400, {
            "ok": False,
            "error": {"code": "smtp_configuration_invalid"},
        }

    try:
        authenticate_public_smtp_connection(
            config["host"],
            config["port"],
            config["security"],
            config["username"],
            config["password"],
            timeout=timeout,
        )
    except SmtpConnectionError as error:
        return _SMTP_ERROR_HTTP_STATUS.get(error.code, 502), {
            "ok": False,
            "error": {"code": error.code},
        }
    return 200, {"ok": True}
