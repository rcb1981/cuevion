import json
import smtplib
import socket
import ssl
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import imap_network_policy
import smtp_connection


PUBLIC_IPV4 = "93.184.216.34"
OTHER_PUBLIC_IPV4 = "8.8.8.8"
SMTP_SECRET = "smtp-secret-that-must-not-leak"


def resolver_record(address, port):
    return (
        socket.AF_INET,
        socket.SOCK_STREAM,
        socket.IPPROTO_TCP,
        "",
        (address, port),
    )


def smtp_config(
    *,
    host="SMTP.Example.COM.",
    port=587,
    security="starttls",
    username="sender@example.com",
    password=SMTP_SECRET,
):
    return {
        "host": host,
        "port": port,
        "security": security,
        "username": username,
        "password": password,
    }


def connected_socket(peer=PUBLIC_IPV4):
    result = Mock()
    result.getpeername.return_value = (peer, 44321)
    return result


def verified_context(*, wrapped_socket=None, wrap_error=None):
    context = Mock()

    def wrap_socket(raw_socket, *, server_hostname):
        if wrap_error is not None:
            raise wrap_error
        return wrapped_socket

    context.wrap_socket.side_effect = wrap_socket
    return context


class FakeSMTP:
    def __init__(
        self,
        host,
        port,
        sock,
        *,
        context=None,
        starttls_error=None,
        starttls_reply=(220, b"ready"),
        login_error=None,
        send_error=None,
        close_error=None,
    ):
        self.host = host
        self.port = port
        self.sock = sock
        self.context = context
        self.starttls_error = starttls_error
        self.starttls_reply = starttls_reply
        self.login_error = login_error
        self.send_error = send_error
        self.close_error = close_error
        self.events = []
        self.login_arguments = None
        self.sent_messages = []

    def ehlo(self):
        self.events.append("ehlo")
        return 250, b"hello"

    def starttls(self, *, context):
        self.events.append("starttls")
        if self.starttls_error is not None:
            raise self.starttls_error
        self.sock = context.wrap_socket(
            self.sock,
            server_hostname=self.host,
        )
        return self.starttls_reply

    def login(self, username, password):
        self.events.append("login")
        self.login_arguments = (username, password)
        if self.login_error is not None:
            raise self.login_error
        return 235, b"authenticated"

    def send_message(self, message, *, to_addrs):
        self.events.append("send_message")
        self.sent_messages.append((message, to_addrs))
        if self.send_error is not None:
            raise self.send_error
        return {}

    def close(self):
        self.events.append("close")
        try:
            if self.sock is not None:
                self.sock.close()
        finally:
            if self.close_error is not None:
                raise self.close_error


class SmtpConnectionTests(unittest.TestCase):
    def invoke_with_network(
        self,
        config,
        *,
        raw_socket=None,
        tls_socket=None,
        context=None,
        client_factory=None,
        resolver_results=None,
        operation=None,
    ):
        raw_socket = raw_socket or connected_socket()
        tls_socket = tls_socket or connected_socket()
        context = context or verified_context(wrapped_socket=tls_socket)
        resolver_results = resolver_results or [
            resolver_record(PUBLIC_IPV4, config["port"])
        ]
        socket_factory = Mock(return_value=raw_socket)
        if client_factory is None:
            clients = []

            def client_factory(host, port, sock, *, timeout):
                client = FakeSMTP(host, port, sock)
                clients.append(client)
                return client
        else:
            clients = None

        with patch.object(
            imap_network_policy.socket,
            "getaddrinfo",
            return_value=resolver_results,
        ) as getaddrinfo, patch.object(
            imap_network_policy.socket,
            "socket",
            socket_factory,
        ), patch.object(
            smtp_connection.ssl,
            "create_default_context",
            return_value=context,
        ) as create_default_context, patch.object(
            smtp_connection,
            "_PreconnectedSMTP",
            side_effect=client_factory,
        ) as smtp_factory:
            result = (
                operation(config)
                if operation is not None
                else smtp_connection.test_smtp_authentication(config)
            )

        return {
            "result": result,
            "raw_socket": raw_socket,
            "tls_socket": tls_socket,
            "context": context,
            "clients": clients,
            "getaddrinfo": getaddrinfo,
            "socket_factory": socket_factory,
            "create_default_context": create_default_context,
            "smtp_factory": smtp_factory,
        }

    def assert_safe_error(self, result, status, code):
        self.assertEqual(
            result,
            (
                status,
                {
                    "ok": False,
                    "error": {"code": code},
                },
            ),
        )
        self.assertNotIn(SMTP_SECRET, json.dumps(result))

    def test_starttls_authenticates_in_required_order_with_one_dns_snapshot(self):
        config = smtp_config()
        raw_socket = connected_socket()
        tls_socket = connected_socket()
        context = verified_context(wrapped_socket=tls_socket)
        clients = []

        def client_factory(host, port, sock, *, timeout):
            client = FakeSMTP(host, port, sock)
            clients.append(client)
            return client

        outcome = self.invoke_with_network(
            config,
            raw_socket=raw_socket,
            tls_socket=tls_socket,
            context=context,
            client_factory=client_factory,
        )

        self.assertEqual(outcome["result"], (200, {"ok": True}))
        self.assertEqual(clients[0].events, [
            "ehlo",
            "starttls",
            "ehlo",
            "login",
            "close",
        ])
        self.assertEqual(
            clients[0].login_arguments,
            ("sender@example.com", SMTP_SECRET),
        )
        outcome["getaddrinfo"].assert_called_once_with(
            "smtp.example.com",
            587,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
        )
        outcome["socket_factory"].assert_called_once_with(
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
        )
        raw_socket.connect.assert_called_once_with((PUBLIC_IPV4, 587))
        raw_socket.settimeout.assert_called_once_with(30)
        raw_socket.getpeername.assert_called_once_with()
        context.wrap_socket.assert_called_once_with(
            raw_socket,
            server_hostname="smtp.example.com",
        )
        self.assertIs(context.check_hostname, True)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        tls_socket.getpeername.assert_called_once_with()
        tls_socket.close.assert_called_once_with()

    def test_single_send_reuses_pinned_authenticated_client_and_one_dns_snapshot(self):
        config = smtp_config()
        raw_socket = connected_socket()
        tls_socket = connected_socket()
        context = verified_context(wrapped_socket=tls_socket)
        message = object()
        recipients = ["to@example.com", "cc@example.com"]
        clients = []

        def client_factory(host, port, sock, *, timeout):
            client = FakeSMTP(host, port, sock)
            clients.append(client)
            return client

        def send_once(current_config):
            return smtp_connection.send_public_smtp_message(
                current_config["host"],
                current_config["port"],
                current_config["security"],
                current_config["username"],
                current_config["password"],
                message,
                recipients,
            )

        outcome = self.invoke_with_network(
            config,
            raw_socket=raw_socket,
            tls_socket=tls_socket,
            context=context,
            client_factory=client_factory,
            operation=send_once,
        )

        self.assertIsNone(outcome["result"])
        self.assertEqual(
            clients[0].events,
            [
                "ehlo",
                "starttls",
                "ehlo",
                "login",
                "send_message",
                "close",
            ],
        )
        self.assertEqual(clients[0].sent_messages, [(message, recipients)])
        outcome["getaddrinfo"].assert_called_once_with(
            "smtp.example.com",
            587,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
        )
        raw_socket.connect.assert_called_once_with((PUBLIC_IPV4, 587))
        raw_socket.getpeername.assert_called_once_with()
        context.wrap_socket.assert_called_once_with(
            raw_socket,
            server_hostname="smtp.example.com",
        )
        tls_socket.getpeername.assert_called_once_with()
        tls_socket.close.assert_called_once_with()

    def test_send_failure_is_safe_and_cleanup_errors_do_not_mask_it(self):
        config = smtp_config(port=465, security="ssl")
        tls_socket = connected_socket()
        context = verified_context(wrapped_socket=tls_socket)
        clients = []

        def client_factory(host, port, sock, *, timeout):
            client = FakeSMTP(
                host,
                port,
                sock,
                send_error=smtplib.SMTPDataError(554, b"private send detail"),
                close_error=RuntimeError("private cleanup detail"),
            )
            clients.append(client)
            return client

        def send_and_capture(current_config):
            try:
                smtp_connection.send_public_smtp_message(
                    current_config["host"],
                    current_config["port"],
                    current_config["security"],
                    current_config["username"],
                    current_config["password"],
                    object(),
                    ["to@example.com"],
                )
            except smtp_connection.SmtpConnectionError as error:
                return error.code
            self.fail("send failure must return a safe SMTP error")

        outcome = self.invoke_with_network(
            config,
            tls_socket=tls_socket,
            context=context,
            client_factory=client_factory,
            operation=send_and_capture,
        )

        self.assertEqual(outcome["result"], "smtp_send_failed")
        self.assertNotIn(SMTP_SECRET, outcome["result"])
        self.assertEqual(
            clients[0].events,
            ["ehlo", "login", "send_message", "close"],
        )
        self.assertEqual(len(clients[0].sent_messages), 1)
        tls_socket.close.assert_called_once_with()

    def test_implicit_tls_authenticates_without_starttls_or_mail(self):
        config = smtp_config(port=465, security="ssl")
        raw_socket = connected_socket()
        tls_socket = connected_socket()
        context = verified_context(wrapped_socket=tls_socket)
        clients = []

        def client_factory(host, port, sock, *, timeout):
            client = FakeSMTP(host, port, sock)
            clients.append(client)
            return client

        outcome = self.invoke_with_network(
            config,
            raw_socket=raw_socket,
            tls_socket=tls_socket,
            context=context,
            client_factory=client_factory,
        )

        self.assertEqual(outcome["result"], (200, {"ok": True}))
        self.assertEqual(clients[0].events, ["ehlo", "login", "close"])
        self.assertEqual(
            clients[0].login_arguments,
            ("sender@example.com", SMTP_SECRET),
        )
        outcome["getaddrinfo"].assert_called_once()
        raw_socket.connect.assert_called_once_with((PUBLIC_IPV4, 465))
        raw_socket.getpeername.assert_called_once_with()
        context.wrap_socket.assert_called_once_with(
            raw_socket,
            server_hostname="smtp.example.com",
        )
        outcome["smtp_factory"].assert_called_once_with(
            "smtp.example.com",
            465,
            tls_socket,
            timeout=30,
        )
        tls_socket.getpeername.assert_called_once_with()
        tls_socket.close.assert_called_once_with()

    def test_only_exact_supported_security_port_pairs_are_allowed(self):
        invalid_transports = [
            (465, "starttls"),
            (587, "ssl"),
            (25, "starttls"),
            (465, "SSL"),
            (True, "ssl"),
        ]
        for port, security in invalid_transports:
            with self.subTest(port=port, security=security), patch.object(
                imap_network_policy.socket,
                "getaddrinfo",
            ) as getaddrinfo, patch.object(
                imap_network_policy.socket,
                "socket",
            ) as socket_factory:
                result = smtp_connection.test_smtp_authentication(
                    smtp_config(port=port, security=security)
                )
                self.assert_safe_error(
                    result,
                    400,
                    "smtp_transport_not_allowed",
                )
                getaddrinfo.assert_not_called()
                socket_factory.assert_not_called()

    def test_configuration_shape_and_credentials_fail_before_network(self):
        invalid_configs = [
            None,
            {**smtp_config(), "unexpected": "field"},
            {key: value for key, value in smtp_config().items() if key != "host"},
        ]
        credential_configs = [
            smtp_config(username=" "),
            smtp_config(password=""),
        ]
        with patch.object(
            imap_network_policy.socket,
            "getaddrinfo",
        ) as getaddrinfo, patch.object(
            imap_network_policy.socket,
            "socket",
        ) as socket_factory:
            for config in invalid_configs:
                with self.subTest(config=config):
                    self.assert_safe_error(
                        smtp_connection.test_smtp_authentication(config),
                        400,
                        "smtp_configuration_invalid",
                    )
            for config in credential_configs:
                with self.subTest(config=config):
                    self.assert_safe_error(
                        smtp_connection.test_smtp_authentication(config),
                        400,
                        "smtp_credentials_invalid",
                    )
            getaddrinfo.assert_not_called()
            socket_factory.assert_not_called()

    def test_private_loopback_and_mixed_dns_are_blocked_before_socket_creation(self):
        scenarios = [
            [resolver_record("127.0.0.1", 587)],
            [resolver_record("10.0.0.5", 587)],
            [
                resolver_record(PUBLIC_IPV4, 587),
                resolver_record("169.254.169.254", 587),
            ],
        ]
        for resolver_results in scenarios:
            with self.subTest(resolver_results=resolver_results), patch.object(
                imap_network_policy.socket,
                "getaddrinfo",
                return_value=resolver_results,
            ) as getaddrinfo, patch.object(
                imap_network_policy.socket,
                "socket",
            ) as socket_factory:
                result = smtp_connection.test_smtp_authentication(smtp_config())
                self.assert_safe_error(
                    result,
                    502,
                    "smtp_destination_not_allowed",
                )
                getaddrinfo.assert_called_once()
                socket_factory.assert_not_called()

    def test_pre_tls_peer_mismatch_stops_before_tls_and_closes_raw_socket(self):
        raw_socket = connected_socket(peer=OTHER_PUBLIC_IPV4)
        outcome = self.invoke_with_network(
            smtp_config(),
            raw_socket=raw_socket,
        )

        self.assert_safe_error(
            outcome["result"],
            502,
            "smtp_peer_mismatch",
        )
        raw_socket.close.assert_called_once_with()
        outcome["create_default_context"].assert_not_called()
        outcome["smtp_factory"].assert_not_called()

    def test_certificate_failure_is_safe_and_closes_the_raw_socket(self):
        raw_socket = connected_socket()
        raw_socket.close.side_effect = RuntimeError("cleanup detail")
        context = verified_context(
            wrap_error=ssl.SSLCertVerificationError("certificate detail")
        )
        outcome = self.invoke_with_network(
            smtp_config(port=465, security="ssl"),
            raw_socket=raw_socket,
            context=context,
        )

        self.assert_safe_error(
            outcome["result"],
            502,
            "smtp_tls_failed",
        )
        raw_socket.close.assert_called_once_with()
        outcome["smtp_factory"].assert_not_called()

    def test_starttls_failure_is_safe_and_closes_the_client(self):
        raw_socket = connected_socket()
        clients = []

        def client_factory(host, port, sock, *, timeout):
            client = FakeSMTP(
                host,
                port,
                sock,
                starttls_error=ssl.SSLError("private TLS detail"),
            )
            clients.append(client)
            return client

        outcome = self.invoke_with_network(
            smtp_config(),
            raw_socket=raw_socket,
            client_factory=client_factory,
        )

        self.assert_safe_error(
            outcome["result"],
            502,
            "smtp_tls_failed",
        )
        self.assertEqual(clients[0].events, ["ehlo", "starttls", "close"])
        raw_socket.close.assert_called_once_with()

    def test_bad_starttls_reply_is_rejected_before_second_ehlo_or_login(self):
        raw_socket = connected_socket()
        tls_socket = connected_socket()
        context = verified_context(wrapped_socket=tls_socket)
        clients = []

        def client_factory(host, port, sock, *, timeout):
            client = FakeSMTP(
                host,
                port,
                sock,
                starttls_reply=(454, b"TLS unavailable"),
            )
            clients.append(client)
            return client

        outcome = self.invoke_with_network(
            smtp_config(),
            raw_socket=raw_socket,
            tls_socket=tls_socket,
            context=context,
            client_factory=client_factory,
        )

        self.assert_safe_error(outcome["result"], 502, "smtp_tls_failed")
        self.assertEqual(clients[0].events, ["ehlo", "starttls", "close"])
        tls_socket.close.assert_called_once_with()

    def test_authentication_failure_is_safe_and_closes_the_client(self):
        clients = []

        def client_factory(host, port, sock, *, timeout):
            client = FakeSMTP(
                host,
                port,
                sock,
                login_error=smtplib.SMTPAuthenticationError(
                    535,
                    b"private auth detail",
                ),
            )
            clients.append(client)
            return client

        outcome = self.invoke_with_network(
            smtp_config(port=465, security="ssl"),
            client_factory=client_factory,
        )

        self.assert_safe_error(
            outcome["result"],
            401,
            "smtp_authentication_failed",
        )
        self.assertEqual(clients[0].events, ["ehlo", "login", "close"])
        outcome["tls_socket"].close.assert_called_once_with()

    def test_post_tls_peer_mismatch_is_safe_and_closes_tls_socket(self):
        raw_socket = connected_socket()
        tls_socket = connected_socket(peer=OTHER_PUBLIC_IPV4)
        context = verified_context(wrapped_socket=tls_socket)
        outcome = self.invoke_with_network(
            smtp_config(port=465, security="ssl"),
            raw_socket=raw_socket,
            tls_socket=tls_socket,
            context=context,
        )

        self.assert_safe_error(
            outcome["result"],
            502,
            "smtp_peer_mismatch",
        )
        outcome["smtp_factory"].assert_not_called()
        tls_socket.close.assert_called_once_with()

    def test_client_constructor_failure_still_closes_transport(self):
        raw_socket = connected_socket()

        def client_factory(_host, _port, _sock, *, timeout):
            raise smtplib.SMTPConnectError(421, "private banner detail")

        outcome = self.invoke_with_network(
            smtp_config(),
            raw_socket=raw_socket,
            client_factory=client_factory,
        )

        self.assert_safe_error(
            outcome["result"],
            502,
            "smtp_connection_failed",
        )
        raw_socket.close.assert_called_once_with()

    def test_preconnected_smtp_consumes_supplied_socket_without_resolution(self):
        supplied_socket = object()
        client = object.__new__(smtp_connection._PreconnectedSMTP)
        client._preconnected_socket = supplied_socket

        with patch.object(socket, "create_connection") as create_connection:
            selected = client._get_socket("ignored.example", 587, 30)

        self.assertIs(selected, supplied_socket)
        self.assertIsNone(client._preconnected_socket)
        create_connection.assert_not_called()
        with self.assertRaises(OSError):
            client._get_socket("ignored.example", 587, 30)


if __name__ == "__main__":
    unittest.main()
