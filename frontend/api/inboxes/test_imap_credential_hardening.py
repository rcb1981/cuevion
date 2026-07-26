import base64
import importlib.util
import io
import json
import os
import socket
import ssl
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock, patch

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
FRONTEND_DIR = API_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import authenticated_imap
import imap_connect_preview
import imap_network_policy
import mailbox_secret_store
import smtp_connection
import user_config_store


def load_route(filename, name):
    spec = importlib.util.spec_from_file_location(name, CURRENT_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


connect_route = load_route("connect-imap.py", "connect_imap_hardening_test")
action_route = load_route("message-action.py", "message_action_hardening_test")
attachment_route = load_route("download-attachment.py", "attachment_hardening_test")
send_route = load_route("send-gmail.py", "send_hardening_test")
credentials_route = load_route("credentials.py", "credentials_hardening_test")
CREDENTIAL_VERSION_A = (
    base64.urlsafe_b64encode(b"a" * 32).decode("ascii").rstrip("=")
)
CREDENTIAL_VERSION_B = (
    base64.urlsafe_b64encode(b"b" * 32).decode("ascii").rstrip("=")
)


class FakeHandler:
    def __init__(self, payload=None, headers=None):
        body = json.dumps(payload or {}).encode()
        self.headers = {"content-length": str(len(body)), **(headers or {})}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status = None
        self.path = "/"

    def send_response(self, status):
        self.status = status

    def send_header(self, _name, _value):
        pass

    def end_headers(self):
        pass

    def response(self):
        return json.loads(self.wfile.getvalue())


def invoke_connect(
    fake_handler,
    *,
    use_real_lease=False,
    smtp_result=(200, {"ok": True}),
):
    for method_name in (
        "_send_json",
        "_send_onboarding_registration_error",
        "_restore_onboarding_secret",
        "_send_secret_write_error",
        "_send_rollback_error",
        "_handle_onboarding_connection",
        "_handle_onboarding_connection_with_position_lease",
        "_handle_onboarding_connection_under_lease",
        "_handle_onboarding_capability_connection_with_position_lease",
        "_handle_onboarding_capability_connection_under_lease",
        "_handle_credential_connection",
        "_handle_credential_connection_under_lease",
        "_handle_refresh",
    ):
        setattr(
            fake_handler,
            method_name,
            getattr(connect_route.handler, method_name).__get__(
                fake_handler,
                connect_route.handler,
            ),
        )
    with patch.object(
        smtp_connection,
        "test_smtp_authentication",
        return_value=smtp_result,
    ) as smtp_test:
        if use_real_lease:
            connect_route.handler.do_POST(fake_handler)
        else:
            with patch.object(
                connect_route,
                "acquire_mailbox_mutation_lease",
                return_value={"status": "acquired", "token": "l" * 43, "error": None},
            ), patch.object(
                connect_route,
                "release_mailbox_mutation_lease",
                return_value={"status": "released", "token": "l" * 43, "error": None},
            ):
                connect_route.handler.do_POST(fake_handler)
    return smtp_test


def initial_payload(mailbox_id="demo", mode="initial"):
    return {
        "mode": mode,
        "mailboxId": mailbox_id,
        "connection": {
            "provider": "custom_imap",
            "email": "demo@example.com",
            "imap": {
                "host": "imap.example.com",
                "port": "993",
                "ssl": True,
                "username": "demo@example.com",
                "password": "one-time-imap",
            },
            "smtp": {
                "host": "smtp.example.com",
                "port": "587",
                "security": "starttls",
                "username": "",
                "password": "",
                "useSameCredentials": True,
            },
        },
        "limit": 20,
    }


def onboarding_payload(
    onboarding_inbox_id="promo",
    *,
    email="promo@example.com",
    ssl_enabled=True,
):
    return {
        "mode": "onboarding",
        "onboardingInboxId": onboarding_inbox_id,
        "connection": {
            "provider": "custom_imap",
            "email": email,
            "imap": {
                "host": "imap.example.com",
                "port": "993",
                "ssl": ssl_enabled,
                "username": "promo@example.com",
                "password": "one-time-onboarding-imap",
            },
            "smtp": {
                "host": "smtp.example.com",
                "port": "587",
                "security": "starttls",
                "username": "",
                "useSameCredentials": True,
            },
        },
    }


def onboarding_config(
    *,
    selected_inboxes=None,
    inbox_count="2",
    completed=False,
    managed_inboxes=None,
    custom_inboxes=None,
):
    selected = ["main", "promo"] if selected_inboxes is None else list(selected_inboxes)
    managed = (
        [
            {
                "id": "gmail-main",
                "email": "main@example.com",
                "provider": "google",
                "connected": True,
                "connectionMethod": "oauth",
                "connectionStatus": "connected",
                "onboardingInboxId": "main",
            }
        ]
        if managed_inboxes is None
        else list(managed_inboxes)
    )
    return {
        "v": 1,
        "email": "owner@example.com",
        "onboardingSession": {
            "schemaVersion": 1,
            "completed": completed,
            "currentStep": 2,
            "choices": {
                "inboxCount": inbox_count,
                "selectedInboxes": selected,
                "customInboxes": [] if custom_inboxes is None else list(custom_inboxes),
            },
        },
        "managedInboxes": managed,
    }


def onboarding_target(config):
    return {
        "status": "ok",
        "user": {"email": "owner@example.com"},
        "inbox": None,
        "config": config,
        "error": None,
    }


def partial_onboarding_mailbox(
    *,
    mailbox_id="imap-existing",
    credential_version=CREDENTIAL_VERSION_A,
    legacy_smtp_placeholder=False,
):
    return {
        "id": mailbox_id,
        "credentialVersion": credential_version,
        "title": "promo@example.com",
        "email": "promo@example.com",
        "provider": "custom_imap",
        "connected": True,
        "connectionMethod": "imap",
        "connectionStatus": "connected",
        "connectionMessage": None,
        "oauthAuthorizationUrl": None,
        "onboardingInboxId": "promo",
        "customImap": {
            "host": "imap.example.com",
            "port": "993",
            "ssl": True,
            "username": "promo@example.com",
        },
        "customSmtp": {"password": ""} if legacy_smtp_placeholder else {},
        "imapConnectionStatus": "connected",
        "smtpConnectionStatus": "not_configured",
        "fullyConnected": False,
    }


def partial_onboarding_target(config, **mailbox_kwargs):
    mailbox = partial_onboarding_mailbox(**mailbox_kwargs)
    return {
        "status": "ok",
        "user": {"email": "owner@example.com"},
        "inbox": mailbox,
        "config": {
            **config,
            "managedInboxes": [*config["managedInboxes"], mailbox],
        },
        "error": None,
    }


def onboarding_readback(
    config,
    mailbox_id="imap-server-owned",
    credential_version=CREDENTIAL_VERSION_B,
):
    mailbox = {
        "id": mailbox_id,
        "credentialVersion": credential_version,
        "title": "promo@example.com",
        "email": "promo@example.com",
        "provider": "custom_imap",
        "connected": True,
        "connectionMethod": "imap",
        "connectionStatus": "connected",
        "connectionMessage": None,
        "oauthAuthorizationUrl": None,
        "onboardingInboxId": "promo",
        "customImap": {
            "host": "imap.example.com",
            "port": "993",
            "ssl": True,
            "username": "promo@example.com",
        },
        "customSmtp": {
            "host": "smtp.example.com",
            "port": "587",
            "security": "starttls",
            "username": "",
            "useSameCredentials": True,
        },
        "imapConnectionStatus": "connected",
        "smtpConnectionStatus": "connected",
        "fullyConnected": True,
    }
    return {
        "status": "ok",
        "user": {"email": "owner@example.com"},
        "inbox": mailbox,
        "config": {**config, "managedInboxes": [*config["managedInboxes"], mailbox]},
        "error": None,
    }


def missing_connection_target():
    return {
        "status": "not_found",
        "user": {"email": "owner@example.com"},
        "inbox": None,
        "config": {"managedInboxes": []},
        "error": None,
    }


def existing_connection_target(
    provider="custom_imap",
    credential_version=CREDENTIAL_VERSION_A,
):
    inbox = {
        "id": "demo",
        "provider": provider,
    }
    if provider == "custom_imap" and credential_version is not None:
        inbox["credentialVersion"] = credential_version
    return {
        "status": "ok",
        "user": {"email": "owner@example.com"},
        "inbox": inbox,
        "config": {"managedInboxes": [json.loads(json.dumps(inbox))]},
        "error": None,
    }


def complete_connection_target(credential_version=CREDENTIAL_VERSION_A):
    inbox = {
        "id": "demo",
        "title": "Existing Inbox",
        "email": "old@example.com",
        "provider": "custom_imap",
        "connected": True,
        "connectionMethod": "imap",
        "connectionStatus": "connected",
        "connectionMessage": None,
        "oauthAuthorizationUrl": None,
        "credentialVersion": credential_version,
        "customImap": {
            "host": "imap.old.example.com",
            "port": "993",
            "ssl": True,
            "username": "old@example.com",
        },
        "customSmtp": {
            "host": "smtp.old.example.com",
            "port": "465",
            "security": "ssl",
            "username": "smtp-old@example.com",
            "useSameCredentials": False,
        },
    }
    return {
        "status": "ok",
        "user": {"email": "owner@example.com"},
        "inbox": inbox,
        "config": {"managedInboxes": [json.loads(json.dumps(inbox))]},
        "error": None,
    }


def resolved_mailbox():
    return {
        "status": "ok",
        "mailbox": {
            "mailboxId": "demo",
            "ownerEmail": "owner@example.com",
            "email": "demo@example.com",
            "imap": {
                "host": "imap.example.com",
                "port": 993,
                "ssl": True,
                "username": "imap-user",
                "password": "imap-secret",
            },
            "smtp": {
                "host": "smtp.example.com",
                "port": 587,
                "security": "starttls",
                "username": "smtp-user",
                "password": "smtp-secret",
                "useSameCredentials": False,
            },
        },
        "error": None,
    }


class ImapNetworkPolicyTests(unittest.TestCase):
    PUBLIC_IPV4 = "93.184.216.34"
    PUBLIC_IPV6 = "2606:2800:220:1:248:1893:25c8:1946"
    BLOCKED_IPV4 = (
        "127.0.0.1",
        "0.0.0.0",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "192.0.0.1",
        "192.0.0.9",
        "192.0.0.10",
        "192.0.0.200",
        "224.0.0.1",
        "192.0.2.1",
        "198.51.100.1",
        "203.0.113.1",
        "240.0.0.1",
    )
    BLOCKED_IPV6 = (
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "fd00::1",
        "ff02::1",
        "2001:db8::1",
        "2002:7f00:1::",
        "64:ff9b::7f00:1",
        "::ffff:8.8.8.8",
        "::ffff:127.0.0.1",
        "3ffe::1",
        "3fff::1",
        "fec0::1",
    )
    POLICY_CODES = (
        "imap_host_invalid",
        "imap_destination_not_allowed",
        "imap_dns_failed",
        "imap_peer_mismatch",
        "imap_connection_failed",
    )

    @staticmethod
    def _address_result(address, port=993):
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        socket_address = (
            (address, port, 0, 0)
            if family == socket.AF_INET6
            else (address, port)
        )
        return (
            family,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            socket_address,
        )

    @staticmethod
    def _peer_address(address, port=993):
        return (
            (address, port, 0, 0)
            if ":" in address
            else (address, port)
        )

    def _run_onboarding_network_attempt(
        self,
        *,
        dns_results=None,
        dns_error=None,
        peer_address=None,
        tls_peer_address=None,
        tls_error=None,
        settimeout_error=None,
        connect_error=None,
        raw_peer_error=None,
        raw_peer_value=None,
        raw_close_error=None,
        tls_close_error=None,
    ):
        handler = FakeHandler(onboarding_payload())
        config = onboarding_config()
        raw_socket = Mock(name="raw_socket")
        tls_socket = Mock(name="tls_socket")
        mailbox = Mock(name="mailbox")
        ssl_context = Mock(name="ssl_context")
        ssl_context.check_hostname = True
        ssl_context.verify_mode = ssl.CERT_REQUIRED

        peer_address = peer_address or self.PUBLIC_IPV4
        if raw_peer_error is not None:
            raw_socket.getpeername.side_effect = raw_peer_error
        else:
            raw_socket.getpeername.return_value = (
                raw_peer_value
                if raw_peer_value is not None
                else self._peer_address(peer_address)
            )
        tls_socket.getpeername.return_value = self._peer_address(
            tls_peer_address or peer_address
        )
        if settimeout_error is not None:
            raw_socket.settimeout.side_effect = settimeout_error
        if connect_error is not None:
            raw_socket.connect.side_effect = connect_error
        if raw_close_error is not None:
            raw_socket.close.side_effect = raw_close_error
        if tls_close_error is not None:
            tls_socket.close.side_effect = tls_close_error
        if tls_error is None:
            ssl_context.wrap_socket.return_value = tls_socket
        else:
            ssl_context.wrap_socket.side_effect = tls_error

        resolver_patcher = (
            patch.object(
                imap_network_policy.socket,
                "getaddrinfo",
                side_effect=dns_error,
            )
            if dns_error is not None
            else patch.object(
                imap_network_policy.socket,
                "getaddrinfo",
                return_value=dns_results,
            )
        )

        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            connect_route,
            "resolve_owned_onboarding_custom_imap_target",
            return_value=onboarding_target(config),
        ), patch.object(
            connect_route,
            "_prepare_server_mailbox_id",
            return_value=(
                "imap-server-owned",
                {"status": "missing", "record": None, "error": None},
                "m" * 43,
                None,
            ),
        ), patch.object(
            imap_connect_preview,
            "_build_verified_imap_ssl_context",
            return_value=ssl_context,
        ), resolver_patcher as resolver, patch.object(
            imap_network_policy.socket,
            "socket",
            return_value=raw_socket,
        ) as socket_factory, patch.object(
            imap_network_policy,
            "_PreconnectedIMAP4SSL",
            return_value=mailbox,
        ) as tls_protocol, patch.object(
            imap_network_policy,
            "_PreconnectedIMAP4",
        ) as plaintext_protocol, patch.object(
            connect_route,
            "save_mailbox_secret",
        ) as save, patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
        ) as upsert:
            invoke_connect(handler)

        return {
            "handler": handler,
            "resolver": resolver,
            "socket_factory": socket_factory,
            "raw_socket": raw_socket,
            "tls_socket": tls_socket,
            "ssl_context": ssl_context,
            "tls_protocol": tls_protocol,
            "plaintext_protocol": plaintext_protocol,
            "mailbox": mailbox,
            "save": save,
            "upsert": upsert,
        }

    def _assert_failed_onboarding_network_attempt(
        self,
        result,
        *,
        expected_status,
        expected_code,
    ):
        handler = result["handler"]
        self.assertEqual(handler.status, expected_status)
        self.assertEqual(handler.response()["error"]["code"], expected_code)
        self.assertNotIn(
            "one-time-onboarding-imap",
            json.dumps(handler.response()),
        )
        result["mailbox"].login.assert_not_called()
        result["save"].assert_not_called()
        result["upsert"].assert_not_called()

    def test_normalize_imap_host_canonicalizes_dns_idna_and_numeric_ipv6(self):
        self.assertEqual(
            imap_network_policy.normalize_imap_host(
                "  IMAP.Example.COM.  "
            ),
            "imap.example.com",
        )
        self.assertEqual(
            imap_network_policy.normalize_imap_host("BÜCHER.Example"),
            "xn--bcher-kva.example",
        )
        self.assertEqual(
            imap_network_policy.normalize_imap_host(
                "2001:4860:4860:0:0:0:0:8888"
            ),
            "2001:4860:4860::8888",
        )

    def test_numeric_ipv4_and_ipv6_hosts_use_the_same_destination_policy(self):
        for address in ("127.0.0.1", "::1"):
            with self.subTest(address=address):
                with patch.object(
                    imap_network_policy.socket,
                    "getaddrinfo",
                    return_value=[self._address_result(address)],
                ) as resolver, patch.object(
                    imap_network_policy.socket,
                    "socket",
                ) as socket_factory:
                    with self.assertRaisesRegex(
                        imap_network_policy.ImapNetworkPolicyError,
                        "^imap_destination_not_allowed$",
                    ):
                        imap_network_policy.resolve_public_imap_destination(
                            address,
                            993,
                        )

                resolver.assert_called_once_with(
                    address,
                    993,
                    socket.AF_UNSPEC,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                )
                socket_factory.assert_not_called()

    def test_onboarding_rejects_required_host_forms_before_dns_or_writes(self):
        invalid_hosts = (
            "",
            "   ",
            "https://example.com",
            "example.com/path",
            "example.com?query=1",
            "example.com#fragment",
            "user@example.com",
            "example.com\x00",
            "example.com\x1f",
            "fe80::1%eth0",
            "localhost",
            "imap.localhost",
            "printer.local",
            "mail.internal",
        )

        for host in invalid_hosts:
            with self.subTest(host=repr(host)):
                config = onboarding_config()
                before = json.dumps(config, sort_keys=True)
                payload = onboarding_payload()
                payload["connection"]["imap"]["host"] = host
                handler = FakeHandler(payload)
                with patch.object(
                    connect_route,
                    "resolve_authenticated_user",
                    return_value=({"email": "owner@example.com"}, None),
                ), patch.object(
                    connect_route,
                    "resolve_owned_onboarding_custom_imap_target",
                    return_value=onboarding_target(config),
                ), patch.object(
                    connect_route,
                    "_prepare_server_mailbox_id",
                ) as prepare, patch.object(
                    imap_connect_preview,
                    "build_secure_imap_authentication_response",
                ) as secure_connect, patch.object(
                    imap_network_policy.socket,
                    "getaddrinfo",
                ) as resolver, patch.object(
                    imap_network_policy.socket,
                    "socket",
                ) as socket_factory, patch.object(
                    connect_route,
                    "save_mailbox_secret",
                ) as save, patch.object(
                    connect_route,
                    "upsert_owned_custom_imap_mailbox",
                ) as upsert:
                    invoke_connect(handler)

                self.assertEqual(handler.status, 400)
                self.assertNotIn(
                    "one-time-onboarding-imap",
                    json.dumps(handler.response()),
                )
                prepare.assert_not_called()
                resolver.assert_not_called()
                socket_factory.assert_not_called()
                secure_connect.assert_not_called()
                save.assert_not_called()
                upsert.assert_not_called()
                self.assertEqual(json.dumps(config, sort_keys=True), before)

    def test_onboarding_allows_993_but_rejects_other_ports_before_dns_or_writes(self):
        valid_handler = FakeHandler(onboarding_payload())
        valid_config = onboarding_config()
        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            connect_route,
            "resolve_owned_onboarding_custom_imap_target",
            return_value=onboarding_target(valid_config),
        ), patch.object(
            connect_route,
            "_prepare_server_mailbox_id",
            return_value=(
                "imap-server-owned",
                {"status": "missing", "record": None, "error": None},
                "m" * 43,
                None,
            ),
        ) as valid_prepare, patch.object(
            imap_connect_preview,
            "build_secure_imap_authentication_response",
            return_value=(
                502,
                {
                    "ok": False,
                    "error": {
                        "code": "imap_dns_failed",
                        "message": "resolver detail must not escape",
                    },
                },
            ),
        ) as valid_secure_connect, patch.object(
            connect_route,
            "save_mailbox_secret",
        ) as valid_save, patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
        ) as valid_upsert:
            invoke_connect(valid_handler)

        self.assertEqual(valid_handler.status, 502)
        self.assertEqual(
            valid_handler.response()["error"]["code"],
            "imap_dns_failed",
        )
        valid_prepare.assert_called_once()
        valid_secure_connect.assert_called_once()
        self.assertEqual(
            valid_secure_connect.call_args.args[0]["port"],
            993,
        )
        valid_save.assert_not_called()
        valid_upsert.assert_not_called()

        for port in (143, 25, 465, 587, 49152):
            with self.subTest(port=port):
                config = onboarding_config()
                payload = onboarding_payload()
                payload["connection"]["imap"]["port"] = str(port)
                handler = FakeHandler(payload)
                with patch.object(
                    connect_route,
                    "resolve_authenticated_user",
                    return_value=({"email": "owner@example.com"}, None),
                ), patch.object(
                    connect_route,
                    "resolve_owned_onboarding_custom_imap_target",
                    return_value=onboarding_target(config),
                ), patch.object(
                    connect_route,
                    "_prepare_server_mailbox_id",
                ) as prepare, patch.object(
                    imap_connect_preview,
                    "build_secure_imap_authentication_response",
                ) as secure_connect, patch.object(
                    imap_network_policy.socket,
                    "getaddrinfo",
                ) as resolver, patch.object(
                    imap_network_policy.socket,
                    "socket",
                ) as socket_factory, patch.object(
                    connect_route,
                    "save_mailbox_secret",
                ) as save, patch.object(
                    connect_route,
                    "upsert_owned_custom_imap_mailbox",
                ) as upsert:
                    invoke_connect(handler)

                self.assertEqual(handler.status, 400)
                self.assertEqual(
                    handler.response()["error"]["code"],
                    "imap_port_not_allowed",
                )
                prepare.assert_not_called()
                resolver.assert_not_called()
                socket_factory.assert_not_called()
                secure_connect.assert_not_called()
                save.assert_not_called()
                upsert.assert_not_called()

    def test_required_non_public_ipv4_destinations_are_blocked_before_socket(self):
        for address in self.BLOCKED_IPV4:
            with self.subTest(address=address):
                result = self._run_onboarding_network_attempt(
                    dns_results=[self._address_result(address)]
                )
                self._assert_failed_onboarding_network_attempt(
                    result,
                    expected_status=400,
                    expected_code="imap_destination_not_allowed",
                )
                result["resolver"].assert_called_once()
                result["socket_factory"].assert_not_called()
                result["ssl_context"].wrap_socket.assert_not_called()
                result["tls_protocol"].assert_not_called()
                result["plaintext_protocol"].assert_not_called()
                self.assertNotIn(
                    address,
                    json.dumps(result["handler"].response()),
                )

    def test_required_non_public_ipv6_destinations_are_blocked_before_socket(self):
        for address in self.BLOCKED_IPV6:
            with self.subTest(address=address):
                result = self._run_onboarding_network_attempt(
                    dns_results=[self._address_result(address)]
                )
                self._assert_failed_onboarding_network_attempt(
                    result,
                    expected_status=400,
                    expected_code="imap_destination_not_allowed",
                )
                result["resolver"].assert_called_once()
                result["socket_factory"].assert_not_called()
                result["ssl_context"].wrap_socket.assert_not_called()
                result["tls_protocol"].assert_not_called()
                result["plaintext_protocol"].assert_not_called()
                self.assertNotIn(
                    address,
                    json.dumps(result["handler"].response()),
                )

    def test_mixed_public_and_private_dns_is_rejected_as_a_whole(self):
        result = self._run_onboarding_network_attempt(
            dns_results=[
                self._address_result(self.PUBLIC_IPV4),
                self._address_result("10.0.0.1"),
            ]
        )

        self._assert_failed_onboarding_network_attempt(
            result,
            expected_status=400,
            expected_code="imap_destination_not_allowed",
        )
        result["resolver"].assert_called_once()
        result["socket_factory"].assert_not_called()
        result["ssl_context"].wrap_socket.assert_not_called()
        result["tls_protocol"].assert_not_called()

    def test_peer_must_match_the_selected_address_not_only_the_dns_set(self):
        other_public_address = "1.1.1.1"
        result = self._run_onboarding_network_attempt(
            dns_results=[
                self._address_result(self.PUBLIC_IPV4),
                self._address_result(other_public_address),
            ],
            peer_address=self.PUBLIC_IPV4,
        )

        self._assert_failed_onboarding_network_attempt(
            result,
            expected_status=502,
            expected_code="imap_peer_mismatch",
        )
        result["raw_socket"].connect.assert_called_once_with(
            (other_public_address, 993)
        )
        result["raw_socket"].close.assert_called_once_with()
        result["ssl_context"].wrap_socket.assert_not_called()
        result["tls_protocol"].assert_not_called()

    def test_dns_errors_empty_and_unusable_results_fail_closed(self):
        cases = (
            (
                "resolver_error",
                None,
                socket.gaierror("resolver canary"),
            ),
            ("empty", [], None),
            (
                "non_stream",
                [
                    (
                        socket.AF_INET,
                        socket.SOCK_DGRAM,
                        socket.IPPROTO_UDP,
                        "",
                        (self.PUBLIC_IPV4, 993),
                    )
                ],
                None,
            ),
        )
        for name, dns_results, dns_error in cases:
            with self.subTest(case=name):
                result = self._run_onboarding_network_attempt(
                    dns_results=dns_results,
                    dns_error=dns_error,
                )
                self._assert_failed_onboarding_network_attempt(
                    result,
                    expected_status=502,
                    expected_code="imap_dns_failed",
                )
                result["resolver"].assert_called_once()
                result["socket_factory"].assert_not_called()
                result["ssl_context"].wrap_socket.assert_not_called()
                self.assertNotIn(
                    "resolver canary",
                    json.dumps(result["handler"].response()),
                )

    def test_every_malformed_resolver_record_fails_closed_before_socket(self):
        valid_ipv4 = self._address_result(self.PUBLIC_IPV4)
        valid_ipv6 = self._address_result(self.PUBLIC_IPV6)
        malformed_records = (
            ("not_tuple", "not-a-resolver-tuple"),
            ("too_few_fields", valid_ipv4[:-1]),
            ("too_many_fields", (*valid_ipv4, "unexpected")),
            (
                "unknown_family",
                (
                    socket.AF_UNSPEC,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (self.PUBLIC_IPV4, 993),
                ),
            ),
            (
                "boolean_family",
                (
                    True,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (self.PUBLIC_IPV4, 993),
                ),
            ),
            (
                "float_family",
                (
                    float(socket.AF_INET),
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (self.PUBLIC_IPV4, 993),
                ),
            ),
            (
                "unhashable_family",
                (
                    [],
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (self.PUBLIC_IPV4, 993),
                ),
            ),
            (
                "wrong_socket_type",
                (
                    socket.AF_INET,
                    socket.SOCK_DGRAM,
                    socket.IPPROTO_TCP,
                    "",
                    (self.PUBLIC_IPV4, 993),
                ),
            ),
            (
                "float_socket_type",
                (
                    socket.AF_INET,
                    float(socket.SOCK_STREAM),
                    socket.IPPROTO_TCP,
                    "",
                    (self.PUBLIC_IPV4, 993),
                ),
            ),
            (
                "wrong_protocol",
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_UDP,
                    "",
                    (self.PUBLIC_IPV4, 993),
                ),
            ),
            (
                "float_protocol",
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    float(socket.IPPROTO_TCP),
                    "",
                    (self.PUBLIC_IPV4, 993),
                ),
            ),
            (
                "boolean_socket_type",
                (
                    socket.AF_INET,
                    True,
                    socket.IPPROTO_TCP,
                    "",
                    (self.PUBLIC_IPV4, 993),
                ),
            ),
            (
                "boolean_protocol",
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    False,
                    "",
                    (self.PUBLIC_IPV4, 993),
                ),
            ),
            (
                "canonical_name_not_string",
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    None,
                    (self.PUBLIC_IPV4, 993),
                ),
            ),
            (
                "sockaddr_not_tuple",
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    [self.PUBLIC_IPV4, 993],
                ),
            ),
            (
                "empty_sockaddr",
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (),
                ),
            ),
            (
                "ipv4_sockaddr_wrong_arity",
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (self.PUBLIC_IPV4, 993, 0),
                ),
            ),
            (
                "ipv6_sockaddr_wrong_arity",
                (
                    socket.AF_INET6,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (self.PUBLIC_IPV6, 993, 0),
                ),
            ),
            (
                "empty_host",
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("", 993),
                ),
            ),
            (
                "host_not_string",
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (b"93.184.216.34", 993),
                ),
            ),
            (
                "port_not_integer",
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (self.PUBLIC_IPV4, "993"),
                ),
            ),
            (
                "port_is_boolean",
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (self.PUBLIC_IPV4, True),
                ),
            ),
            (
                "port_too_low",
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (self.PUBLIC_IPV4, 0),
                ),
            ),
            (
                "port_too_high",
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (self.PUBLIC_IPV4, 65536),
                ),
            ),
            (
                "unexpected_port",
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (self.PUBLIC_IPV4, 143),
                ),
            ),
            (
                "flowinfo_not_integer",
                (
                    socket.AF_INET6,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (self.PUBLIC_IPV6, 993, "0", 0),
                ),
            ),
            (
                "flowinfo_out_of_range",
                (
                    socket.AF_INET6,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (self.PUBLIC_IPV6, 993, 0x100000, 0),
                ),
            ),
            (
                "scope_id_not_integer",
                (
                    socket.AF_INET6,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (self.PUBLIC_IPV6, 993, 0, "0"),
                ),
            ),
            (
                "scope_id_out_of_range",
                (
                    socket.AF_INET6,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (self.PUBLIC_IPV6, 993, 0, 0x100000000),
                ),
            ),
            (
                "ipv4_address_with_ipv6_family",
                (
                    socket.AF_INET6,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (self.PUBLIC_IPV4, 993, 0, 0),
                ),
            ),
            (
                "ipv6_address_with_ipv4_family",
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (self.PUBLIC_IPV6, 993),
                ),
            ),
            (
                "scoped_address_text",
                (
                    socket.AF_INET6,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (f"{self.PUBLIC_IPV6}%1", 993, 0, 1),
                ),
            ),
        )

        for name, malformed_record in malformed_records:
            for records in (
                [malformed_record],
                [valid_ipv4, malformed_record],
            ):
                with self.subTest(case=name, mixed=len(records) == 2):
                    result = self._run_onboarding_network_attempt(
                        dns_results=records
                    )
                    self._assert_failed_onboarding_network_attempt(
                        result,
                        expected_status=502,
                        expected_code="imap_dns_failed",
                    )
                    result["resolver"].assert_called_once()
                    result["socket_factory"].assert_not_called()
                    result["ssl_context"].wrap_socket.assert_not_called()
                    result["tls_protocol"].assert_not_called()
                    result["plaintext_protocol"].assert_not_called()

    def test_zero_protocol_tcp_record_is_safely_normalized_to_tcp(self):
        zero_protocol_record = (
            socket.AF_INET,
            socket.SOCK_STREAM,
            0,
            "",
            (self.PUBLIC_IPV4, 993),
        )
        with patch.object(
            imap_network_policy.socket,
            "getaddrinfo",
            return_value=[zero_protocol_record],
        ), patch.object(
            imap_network_policy.socket,
            "socket",
        ) as socket_factory:
            destination = imap_network_policy.resolve_public_imap_destination(
                "imap.example.com",
                993,
            )

        self.assertEqual(
            destination.addresses,
            (
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    (self.PUBLIC_IPV4, 993),
                    self.PUBLIC_IPV4,
                ),
            ),
        )
        socket_factory.assert_not_called()

    def test_raw_resolver_record_limit_is_applied_before_record_processing(self):
        unique_records = [
            self._address_result(f"93.184.216.{index}")
            for index in range(1, 66)
        ]
        duplicate_record = self._address_result(self.PUBLIC_IPV4)
        over_limit_cases = (
            ("65_unique", unique_records),
            ("1000_duplicates", [duplicate_record] * 1000),
            ("65_malformed", ["not-a-resolver-tuple"] * 65),
            (
                "valid_duplicate_and_malformed",
                [duplicate_record] * 63
                + [self._address_result("1.1.1.1"), "malformed"],
            ),
            ("65_deduplicated_to_one", [duplicate_record] * 65),
        )

        for name, records in over_limit_cases:
            with self.subTest(case=name), patch.object(
                imap_network_policy.socket,
                "getaddrinfo",
                return_value=records,
            ) as resolver, patch.object(
                imap_network_policy,
                "_is_public_address",
            ) as classify, patch.object(
                imap_network_policy.socket,
                "socket",
            ) as socket_factory:
                with self.assertRaisesRegex(
                    imap_network_policy.ImapNetworkPolicyError,
                    "^imap_dns_failed$",
                ):
                    imap_network_policy.resolve_public_imap_destination(
                        "imap.example.com",
                        993,
                    )

            resolver.assert_called_once()
            classify.assert_not_called()
            socket_factory.assert_not_called()

        with patch.object(
            imap_network_policy.socket,
            "getaddrinfo",
            return_value=unique_records[:64],
        ), patch.object(
            imap_network_policy.socket,
            "socket",
        ) as socket_factory:
            unique_destination = (
                imap_network_policy.resolve_public_imap_destination(
                    "imap.example.com",
                    993,
                )
            )
        self.assertEqual(len(unique_destination.addresses), 64)
        self.assertEqual(len(unique_destination.allowed_ips), 64)
        socket_factory.assert_not_called()

        with patch.object(
            imap_network_policy.socket,
            "getaddrinfo",
            return_value=[duplicate_record] * 64,
        ), patch.object(
            imap_network_policy.socket,
            "socket",
        ) as socket_factory:
            duplicate_destination = (
                imap_network_policy.resolve_public_imap_destination(
                    "imap.example.com",
                    993,
                )
            )
        self.assertEqual(len(duplicate_destination.addresses), 1)
        self.assertEqual(
            duplicate_destination.allowed_ips,
            frozenset({self.PUBLIC_IPV4}),
        )
        socket_factory.assert_not_called()

    def test_custom_preview_cannot_bypass_the_public_destination_dialer(self):
        with patch.object(
            imap_connect_preview,
            "open_mailbox_connection",
            side_effect=imap_network_policy.ImapNetworkPolicyError(
                "imap_destination_not_allowed"
            ),
        ) as open_connection:
            status, response = imap_connect_preview.build_connect_preview_response(
                {
                    "mailboxId": "demo",
                    "provider": "custom_imap",
                    "email": "demo@example.com",
                    "host": "imap.example.com",
                    "port": "993",
                    "ssl": True,
                    "username": "demo@example.com",
                    "password": "test-only-secret",
                    "limit": 20,
                }
            )

        self.assertEqual(status, 400)
        self.assertEqual(
            response["error"]["code"],
            "imap_destination_not_allowed",
        )
        self.assertNotIn("test-only-secret", json.dumps(response))
        open_connection.assert_called_once_with(
            host="imap.example.com",
            port=993,
            ssl_enabled=True,
            enforce_public_destination=True,
        )

    def test_public_ipv4_and_ipv6_use_one_dns_snapshot_and_numeric_sockaddr(self):
        cases = (
            (
                self.PUBLIC_IPV4,
                socket.AF_INET,
                (self.PUBLIC_IPV4, 993),
            ),
            (
                self.PUBLIC_IPV6,
                socket.AF_INET6,
                (self.PUBLIC_IPV6, 993, 0, 0),
            ),
        )

        for address, family, expected_socket_address in cases:
            with self.subTest(address=address):
                events = []
                raw_socket = Mock(name="raw_socket")
                tls_socket = Mock(name="tls_socket")
                mailbox = Mock(name="mailbox")
                ssl_context = Mock(name="ssl_context")
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

                def resolve_once(*_args):
                    events.append("dns")
                    return [self._address_result(address)]

                def create_socket(*_args):
                    events.append("socket")
                    return raw_socket

                def connect(_socket_address):
                    events.append("connect")

                def raw_peer():
                    events.append("peer")
                    return self._peer_address(address)

                def wrap_socket(_raw_socket, *, server_hostname):
                    events.append("tls")
                    self.assertEqual(server_hostname, "imap.example.com")
                    return tls_socket

                def tls_peer():
                    events.append("tls_peer")
                    return self._peer_address(address)

                def create_protocol(*_args, **_kwargs):
                    events.append("imap")
                    return mailbox

                def login(*_args):
                    events.append("login")
                    return "OK", []

                raw_socket.connect.side_effect = connect
                raw_socket.getpeername.side_effect = raw_peer
                tls_socket.getpeername.side_effect = tls_peer
                ssl_context.wrap_socket.side_effect = wrap_socket
                mailbox.login.side_effect = login

                with patch.object(
                    imap_network_policy.socket,
                    "getaddrinfo",
                    side_effect=resolve_once,
                ) as resolver, patch.object(
                    imap_network_policy.socket,
                    "socket",
                    side_effect=create_socket,
                ) as socket_factory, patch.object(
                    imap_network_policy.ssl,
                    "create_default_context",
                    return_value=ssl_context,
                ) as default_context, patch.object(
                    imap_network_policy,
                    "_PreconnectedIMAP4SSL",
                    side_effect=create_protocol,
                ) as tls_protocol, patch.object(
                    imap_network_policy,
                    "_PreconnectedIMAP4",
                ) as plaintext_protocol:
                    status, response = (
                        imap_connect_preview.build_secure_imap_authentication_response(
                            {
                                "host": "  IMAP.Example.COM.  ",
                                "port": 993,
                                "ssl": True,
                                "username": "promo@example.com",
                                "password": "test-only-secret",
                            }
                        )
                    )

                self.assertEqual((status, response), (200, {"ok": True}))
                self.assertEqual(
                    events,
                    [
                        "dns",
                        "socket",
                        "connect",
                        "peer",
                        "tls",
                        "tls_peer",
                        "imap",
                        "login",
                    ],
                )
                resolver.assert_called_once_with(
                    "imap.example.com",
                    993,
                    socket.AF_UNSPEC,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                )
                socket_factory.assert_called_once_with(
                    family,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                )
                raw_socket.settimeout.assert_called_once_with(30)
                raw_socket.connect.assert_called_once_with(
                    expected_socket_address
                )
                default_context.assert_called_once_with()
                self.assertIs(ssl_context.check_hostname, True)
                self.assertEqual(
                    ssl_context.verify_mode,
                    ssl.CERT_REQUIRED,
                )
                ssl_context.wrap_socket.assert_called_once_with(
                    raw_socket,
                    server_hostname="imap.example.com",
                )
                tls_protocol.assert_called_once_with(
                    "imap.example.com",
                    993,
                    tls_socket,
                    ssl_context=ssl_context,
                    timeout=30,
                )
                plaintext_protocol.assert_not_called()
                mailbox.login.assert_called_once_with(
                    "promo@example.com",
                    "test-only-secret",
                )
                mailbox.logout.assert_called_once_with()

    def test_real_preconnected_imaplib_reads_banner_and_never_reconnects(self):
        public_ip = self.PUBLIC_IPV4
        events = []

        class FakeImapFile:
            def __init__(self):
                self.responses = [
                    ("banner_read", b"* OK fake IMAP4rev1 ready\r\n")
                ]
                self.close_count = 0

            def enqueue(self, *responses):
                self.responses.extend(responses)

            def readline(self, limit=-1):
                if self.close_count:
                    raise AssertionError("IMAP read after file close")
                if not self.responses:
                    raise AssertionError(
                        "unexpected IMAP read without a queued response"
                    )
                event, response = self.responses.pop(0)
                if limit >= 0 and len(response) > limit:
                    raise AssertionError("fake response exceeds readline limit")
                events.append(event)
                return response

            def read(self, size=-1):
                raise AssertionError(
                    f"unexpected IMAP literal read of {size} bytes"
                )

            def close(self):
                self.close_count += 1
                events.append("file_close")

        class FakeRawSocket:
            def __init__(self):
                self.timeouts = []
                self.connect_calls = []
                self.peer_calls = 0
                self.close_count = 0

            def settimeout(self, timeout):
                self.timeouts.append(timeout)
                events.append("raw_timeout")

            def connect(self, socket_address):
                self.connect_calls.append(socket_address)
                events.append("raw_connect")

            def getpeername(self):
                self.peer_calls += 1
                events.append("raw_peer")
                return (public_ip, 993)

            def close(self):
                self.close_count += 1
                events.append("raw_close")

        class FakeTlsSocket:
            def __init__(self):
                self.file = FakeImapFile()
                self.commands = []
                self.peer_calls = 0
                self.makefile_modes = []
                self.shutdown_calls = []
                self.close_count = 0

            def getpeername(self):
                self.peer_calls += 1
                events.append("tls_peer")
                return (public_ip, 993)

            def makefile(self, mode):
                self.makefile_modes.append(mode)
                events.append("makefile")
                return self.file

            def sendall(self, payload):
                if not isinstance(payload, bytes):
                    raise AssertionError("imaplib must send bytes")
                if not payload.endswith(b"\r\n"):
                    raise AssertionError("IMAP command lacks CRLF")

                parts = payload.rstrip(b"\r\n").split(b" ", 2)
                if len(parts) < 2:
                    raise AssertionError(
                        f"malformed IMAP command: {payload!r}"
                    )
                tag, command = parts[0], parts[1].upper()
                self.commands.append((command, payload))
                events.append(f"{command.decode('ascii').lower()}_send")

                if command == b"CAPABILITY":
                    self.file.enqueue(
                        (
                            "capability_untagged_read",
                            b"* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\n",
                        ),
                        (
                            "capability_tagged_read",
                            tag + b" OK CAPABILITY completed\r\n",
                        ),
                    )
                elif command == b"LOGIN":
                    self.file.enqueue(
                        (
                            "login_tagged_read",
                            tag + b" OK LOGIN completed\r\n",
                        )
                    )
                elif command == b"LOGOUT":
                    self.file.enqueue(
                        (
                            "logout_bye_read",
                            b"* BYE LOGOUT requested\r\n",
                        ),
                        (
                            "logout_tagged_read",
                            tag + b" OK LOGOUT completed\r\n",
                        ),
                    )
                else:
                    raise AssertionError(
                        f"unexpected IMAP command: {payload!r}"
                    )

            def shutdown(self, how):
                self.shutdown_calls.append(how)
                events.append("tls_shutdown")

            def close(self):
                self.close_count += 1
                events.append("tls_close")

        class FakeSslContext:
            check_hostname = True
            verify_mode = ssl.CERT_REQUIRED

            def __init__(self, tls_socket):
                self.tls_socket = tls_socket
                self.wrap_calls = []

            def wrap_socket(self, connected_socket, *, server_hostname):
                self.wrap_calls.append(
                    (connected_socket, server_hostname)
                )
                events.append("tls_wrap")
                return self.tls_socket

        raw_socket = FakeRawSocket()
        tls_socket = FakeTlsSocket()
        ssl_context = FakeSslContext(tls_socket)
        dns_result = [self._address_result(public_ip)]

        def resolve_once(*_args):
            events.append("dns")
            return dns_result

        def create_raw_socket(*_args):
            events.append("socket_factory")
            return raw_socket

        with patch.object(
            imap_network_policy.socket,
            "getaddrinfo",
            side_effect=resolve_once,
        ) as resolver, patch.object(
            imap_network_policy.socket,
            "socket",
            side_effect=create_raw_socket,
        ) as socket_factory, patch.object(
            imap_network_policy.socket,
            "create_connection",
            side_effect=AssertionError("hostname reconnect attempted"),
        ) as hostname_connect, patch.object(
            imap_network_policy.ssl,
            "create_default_context",
            side_effect=AssertionError("unexpected replacement context"),
        ) as default_context:
            mailbox = imap_network_policy.open_public_imap_connection(
                "  IMAP.Example.COM.  ",
                993,
                ssl_enabled=True,
                ssl_context=ssl_context,
                timeout=30,
            )

            self.assertIs(
                type(mailbox),
                imap_network_policy._PreconnectedIMAP4SSL,
            )
            self.assertIs(mailbox.sock, tls_socket)
            self.assertIs(mailbox.file, tls_socket.file)
            self.assertIsNone(mailbox._preconnected_socket)
            self.assertEqual(mailbox.host, "imap.example.com")
            self.assertEqual(mailbox.port, 993)
            self.assertEqual(mailbox.state, "NONAUTH")
            self.assertEqual(
                mailbox.capabilities,
                ("IMAP4REV1", "AUTH=PLAIN"),
            )

            self.assertEqual(
                mailbox.login(
                    "promo@example.com",
                    "test-only-secret",
                ),
                ("OK", [b"LOGIN completed"]),
            )
            self.assertEqual(mailbox.state, "AUTH")

            self.assertEqual(
                mailbox.logout(),
                ("BYE", [b"LOGOUT requested"]),
            )
            self.assertEqual(mailbox.state, "LOGOUT")

        resolver.assert_called_once_with(
            "imap.example.com",
            993,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
        )
        socket_factory.assert_called_once_with(
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
        )
        hostname_connect.assert_not_called()
        default_context.assert_not_called()

        self.assertEqual(raw_socket.timeouts, [30])
        self.assertEqual(
            raw_socket.connect_calls,
            [(public_ip, 993)],
        )
        self.assertEqual(raw_socket.peer_calls, 1)
        self.assertEqual(raw_socket.close_count, 0)
        self.assertEqual(
            ssl_context.wrap_calls,
            [(raw_socket, "imap.example.com")],
        )
        self.assertEqual(tls_socket.peer_calls, 1)
        self.assertEqual(tls_socket.makefile_modes, ["rb"])
        self.assertEqual(
            [command for command, _payload in tls_socket.commands],
            [b"CAPABILITY", b"LOGIN", b"LOGOUT"],
        )
        login_payload = tls_socket.commands[1][1]
        self.assertIn(b"promo@example.com", login_payload)
        self.assertIn(b"test-only-secret", login_payload)

        expected_flow = (
            "dns",
            "socket_factory",
            "raw_timeout",
            "raw_connect",
            "raw_peer",
            "tls_wrap",
            "tls_peer",
            "makefile",
            "banner_read",
            "capability_send",
            "capability_untagged_read",
            "capability_tagged_read",
            "login_send",
            "login_tagged_read",
            "logout_send",
            "logout_bye_read",
            "file_close",
            "tls_shutdown",
            "tls_close",
        )
        cursor = -1
        for expected_event in expected_flow:
            cursor = events.index(expected_event, cursor + 1)

        self.assertEqual(tls_socket.file.close_count, 1)
        self.assertEqual(
            tls_socket.shutdown_calls,
            [socket.SHUT_RDWR],
        )
        self.assertEqual(tls_socket.close_count, 1)

    def test_dns_rebinding_second_answer_is_never_resolved_or_connected(self):
        raw_socket = Mock(name="raw_socket")
        tls_socket = Mock(name="tls_socket")
        mailbox = Mock(name="mailbox")
        ssl_context = Mock(name="ssl_context")
        ssl_context.check_hostname = True
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        ssl_context.wrap_socket.return_value = tls_socket
        raw_socket.getpeername.return_value = self._peer_address(
            self.PUBLIC_IPV4
        )
        tls_socket.getpeername.return_value = self._peer_address(
            self.PUBLIC_IPV4
        )
        dns_answers = [
            [self._address_result(self.PUBLIC_IPV4)],
            [self._address_result("10.0.0.1")],
        ]

        with patch.object(
            imap_network_policy.socket,
            "getaddrinfo",
            side_effect=dns_answers,
        ) as resolver, patch.object(
            imap_network_policy.socket,
            "socket",
            return_value=raw_socket,
        ), patch.object(
            imap_network_policy,
            "_PreconnectedIMAP4SSL",
            return_value=mailbox,
        ), patch.object(
            imap_connect_preview,
            "_build_verified_imap_ssl_context",
            return_value=ssl_context,
        ):
            status, response = (
                imap_connect_preview.build_secure_imap_authentication_response(
                    {
                        "host": "imap.example.com",
                        "port": 993,
                        "ssl": True,
                        "username": "promo@example.com",
                        "password": "test-only-secret",
                    }
                )
            )

        self.assertEqual((status, response), (200, {"ok": True}))
        resolver.assert_called_once()
        raw_socket.connect.assert_called_once_with(
            (self.PUBLIC_IPV4, 993)
        )
        self.assertNotIn(
            "10.0.0.1",
            repr(raw_socket.connect.call_args_list),
        )

    def test_peer_mismatch_variants_close_before_tls_login_or_writes(self):
        peer_addresses = (
            "1.1.1.1",
            "10.0.0.1",
            "127.0.0.1",
            "169.254.1.1",
        )
        for peer_address in peer_addresses:
            with self.subTest(peer_address=peer_address):
                result = self._run_onboarding_network_attempt(
                    dns_results=[
                        self._address_result(self.PUBLIC_IPV4)
                    ],
                    peer_address=peer_address,
                )
                self._assert_failed_onboarding_network_attempt(
                    result,
                    expected_status=502,
                    expected_code="imap_peer_mismatch",
                )
                result["resolver"].assert_called_once()
                result["raw_socket"].connect.assert_called_once_with(
                    (self.PUBLIC_IPV4, 993)
                )
                result["raw_socket"].close.assert_called_once_with()
                result["ssl_context"].wrap_socket.assert_not_called()
                result["tls_protocol"].assert_not_called()
                result["plaintext_protocol"].assert_not_called()

    def test_every_pre_tls_operational_exception_closes_raw_socket_once(self):
        cases = (
            (
                "settimeout",
                {"settimeout_error": RuntimeError("timeout canary")},
                "imap_connection_failed",
            ),
            (
                "connect_type_error",
                {"connect_error": TypeError("type canary")},
                "imap_connection_failed",
            ),
            (
                "connect_os_error",
                {"connect_error": OSError("socket canary")},
                "imap_connection_failed",
            ),
            (
                "connect_value_error",
                {"connect_error": ValueError("value canary")},
                "imap_connection_failed",
            ),
            (
                "connect_overflow_error",
                {"connect_error": OverflowError("overflow canary")},
                "imap_connection_failed",
            ),
            (
                "connect_and_close_error",
                {
                    "connect_error": ValueError("connect canary"),
                    "raw_close_error": RuntimeError("close canary"),
                },
                "imap_connection_failed",
            ),
            (
                "getpeername",
                {"raw_peer_error": OSError("peer canary")},
                "imap_peer_mismatch",
            ),
            (
                "peer_ip_parsing",
                {"raw_peer_value": ("not-an-ip", 993)},
                "imap_peer_mismatch",
            ),
        )

        for name, attempt_kwargs, expected_code in cases:
            with self.subTest(case=name):
                result = self._run_onboarding_network_attempt(
                    dns_results=[self._address_result(self.PUBLIC_IPV4)],
                    **attempt_kwargs,
                )
                self._assert_failed_onboarding_network_attempt(
                    result,
                    expected_status=502,
                    expected_code=expected_code,
                )
                result["socket_factory"].assert_called_once_with(
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                )
                result["raw_socket"].close.assert_called_once_with()
                result["ssl_context"].wrap_socket.assert_not_called()
                result["tls_protocol"].assert_not_called()
                result["plaintext_protocol"].assert_not_called()
                self.assertNotIn(
                    "canary",
                    json.dumps(result["handler"].response()),
                )

    def test_post_tls_peer_mismatch_closes_tls_socket_before_imap_or_writes(self):
        for peer_address in ("1.1.1.1", "10.0.0.1"):
            with self.subTest(peer_address=peer_address):
                result = self._run_onboarding_network_attempt(
                    dns_results=[self._address_result(self.PUBLIC_IPV4)],
                    peer_address=self.PUBLIC_IPV4,
                    tls_peer_address=peer_address,
                )
                self._assert_failed_onboarding_network_attempt(
                    result,
                    expected_status=502,
                    expected_code="imap_peer_mismatch",
                )
                result["raw_socket"].connect.assert_called_once_with(
                    (self.PUBLIC_IPV4, 993)
                )
                result["raw_socket"].close.assert_not_called()
                result["ssl_context"].wrap_socket.assert_called_once_with(
                    result["raw_socket"],
                    server_hostname="imap.example.com",
                )
                result["tls_socket"].close.assert_called_once_with()
                result["tls_protocol"].assert_not_called()
                result["plaintext_protocol"].assert_not_called()

    def test_cleanup_failure_does_not_mask_post_tls_peer_mismatch(self):
        result = self._run_onboarding_network_attempt(
            dns_results=[self._address_result(self.PUBLIC_IPV4)],
            peer_address=self.PUBLIC_IPV4,
            tls_peer_address="1.1.1.1",
            tls_close_error=OSError("close canary"),
        )

        self._assert_failed_onboarding_network_attempt(
            result,
            expected_status=502,
            expected_code="imap_peer_mismatch",
        )
        result["raw_socket"].close.assert_not_called()
        result["tls_socket"].close.assert_called_once_with()
        result["tls_protocol"].assert_not_called()
        result["plaintext_protocol"].assert_not_called()
        self.assertNotIn(
            "close canary",
            json.dumps(result["handler"].response()),
        )

    def test_cleanup_failure_does_not_mask_imap_constructor_failure(self):
        raw_socket = Mock(name="raw_socket")
        tls_socket = Mock(name="tls_socket")
        ssl_context = Mock(name="ssl_context")
        ssl_context.check_hostname = True
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        ssl_context.wrap_socket.return_value = tls_socket
        raw_socket.getpeername.return_value = self._peer_address(
            self.PUBLIC_IPV4
        )
        tls_socket.getpeername.return_value = self._peer_address(
            self.PUBLIC_IPV4
        )
        tls_socket.close.side_effect = OSError("close canary")

        with patch.object(
            imap_network_policy.socket,
            "getaddrinfo",
            return_value=[self._address_result(self.PUBLIC_IPV4)],
        ), patch.object(
            imap_network_policy.socket,
            "socket",
            return_value=raw_socket,
        ), patch.object(
            imap_network_policy,
            "_PreconnectedIMAP4SSL",
            side_effect=imap_network_policy.imaplib.IMAP4.error(
                "banner canary"
            ),
        ) as tls_protocol:
            with self.assertRaises(
                imap_network_policy.ImapNetworkPolicyError
            ) as raised:
                imap_network_policy.open_public_imap_connection(
                    "imap.example.com",
                    993,
                    ssl_enabled=True,
                    ssl_context=ssl_context,
                    timeout=30,
                )

        self.assertEqual(raised.exception.code, "imap_connection_failed")
        tls_protocol.assert_called_once()
        raw_socket.close.assert_not_called()
        tls_socket.close.assert_called_once_with()

    def test_tls_failure_closes_socket_without_login_plaintext_or_second_dns(self):
        result = self._run_onboarding_network_attempt(
            dns_results=[self._address_result(self.PUBLIC_IPV4)],
            peer_address=self.PUBLIC_IPV4,
            tls_error=ssl.SSLCertVerificationError(
                "certificate canary"
            ),
        )

        self._assert_failed_onboarding_network_attempt(
            result,
            expected_status=502,
            expected_code="imap_connection_failed",
        )
        result["resolver"].assert_called_once()
        result["raw_socket"].connect.assert_called_once_with(
            (self.PUBLIC_IPV4, 993)
        )
        result["ssl_context"].wrap_socket.assert_called_once_with(
            result["raw_socket"],
            server_hostname="imap.example.com",
        )
        result["raw_socket"].close.assert_called_once_with()
        result["tls_protocol"].assert_not_called()
        result["plaintext_protocol"].assert_not_called()
        self.assertNotIn(
            "certificate canary",
            json.dumps(result["handler"].response()),
        )

    def _invoke_reported_policy_error(self, branch, code):
        response = (
            400
            if code
            in {"imap_host_invalid", "imap_destination_not_allowed"}
            else 502,
            {
                "ok": False,
                "error": {
                    "code": code,
                    "message": "policy detail canary",
                },
            },
        )
        user = {"email": "owner@example.com"}

        if branch == "onboarding":
            handler = FakeHandler(onboarding_payload())
            with patch.object(
                connect_route,
                "resolve_authenticated_user",
                return_value=(user, None),
            ), patch.object(
                connect_route,
                "resolve_owned_onboarding_custom_imap_target",
                return_value=onboarding_target(onboarding_config()),
            ), patch.object(
                connect_route,
                "_prepare_server_mailbox_id",
                return_value=(
                    "imap-server-owned",
                    {
                        "status": "missing",
                        "record": None,
                        "error": None,
                    },
                    "m" * 43,
                    None,
                ),
            ), patch.object(
                imap_connect_preview,
                "build_secure_imap_authentication_response",
                return_value=response,
            ) as provider, patch.object(
                connect_route,
                "snapshot_encrypted_mailbox_secret",
                return_value={
                    "status": "present",
                    "record": {"raw": "v0"},
                    "error": None,
                },
            ) as snapshot, patch.object(
                connect_route,
                "read_mailbox_secret",
                return_value={
                    "status": "present",
                    "record": {
                        "credentialVersion": CREDENTIAL_VERSION_A,
                        "imapPassword": "old",
                        "smtpPassword": "old",
                    },
                    "error": None,
                },
            ), patch.object(
                connect_route,
                "save_mailbox_secret",
            ) as save, patch.object(
                connect_route,
                "upsert_owned_custom_imap_mailbox",
            ) as upsert:
                invoke_connect(handler)
            return handler, provider, snapshot, save, upsert

        if branch in {"initial", "reconnect"}:
            handler = FakeHandler(initial_payload(mode=branch))
            target = (
                missing_connection_target()
                if branch == "initial"
                else existing_connection_target()
            )
            with patch.object(
                connect_route,
                "resolve_authenticated_user",
                return_value=(user, None),
            ), patch.object(
                connect_route,
                "resolve_owned_initial_imap_registration",
                return_value={
                    "status": "ok",
                    "user": user,
                    "inbox": None,
                    "config": onboarding_config(completed=True),
                    "error": None,
                },
            ), patch.object(
                connect_route,
                "resolve_owned_managed_inbox_record",
                return_value=target,
            ), patch.object(
                imap_connect_preview,
                "build_connect_preview_response",
                return_value=response,
            ) as provider, patch.object(
                connect_route,
                "snapshot_encrypted_mailbox_secret",
                return_value={
                    "status": "present",
                    "record": {"raw": "v0"},
                    "error": None,
                },
            ) as snapshot, patch.object(
                connect_route,
                "read_mailbox_secret",
                return_value={
                    "status": "present",
                    "record": {
                        "credentialVersion": CREDENTIAL_VERSION_A,
                        "imapPassword": "old",
                        "smtpPassword": "old",
                    },
                    "error": None,
                },
            ), patch.object(
                connect_route,
                "save_mailbox_secret",
            ) as save, patch.object(
                connect_route,
                "upsert_owned_custom_imap_mailbox",
            ) as upsert:
                invoke_connect(handler)
            return handler, provider, snapshot, save, upsert

        handler = FakeHandler(
            {"mode": "refresh", "mailboxId": "demo", "limit": 20}
        )
        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=(user, None),
        ), patch.object(
            connect_route,
            "resolve_authenticated_imap_mailbox",
            return_value=resolved_mailbox(),
        ), patch.object(
            imap_connect_preview,
            "build_connect_preview_response",
            return_value=response,
        ) as provider, patch.object(
            connect_route,
            "snapshot_encrypted_mailbox_secret",
        ) as snapshot, patch.object(
            connect_route,
            "save_mailbox_secret",
        ) as save, patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
        ) as upsert:
            invoke_connect(handler)
        return handler, provider, snapshot, save, upsert

    def test_all_custom_imap_branches_preserve_safe_policy_codes_without_writes(self):
        for branch in ("onboarding", "initial", "reconnect", "refresh"):
            for code in self.POLICY_CODES:
                with self.subTest(branch=branch, code=code):
                    handler, provider, snapshot, save, upsert = (
                        self._invoke_reported_policy_error(branch, code)
                    )
                    expected_status = (
                        400
                        if code
                        in {
                            "imap_host_invalid",
                            "imap_destination_not_allowed",
                        }
                        else 502
                    )
                    self.assertEqual(handler.status, expected_status)
                    self.assertEqual(
                        handler.response()["error"]["code"],
                        code,
                    )
                    self.assertNotIn(
                        "policy detail canary",
                        json.dumps(handler.response()),
                    )
                    self.assertNotIn(
                        "one-time-imap",
                        json.dumps(handler.response()),
                    )
                    provider.assert_called_once()
                    if branch == "reconnect":
                        snapshot.assert_called_once_with(
                            "owner@example.com",
                            "demo",
                        )
                    else:
                        snapshot.assert_not_called()
                    save.assert_not_called()
                    upsert.assert_not_called()


class ResolverTests(unittest.TestCase):
    def test_owned_mailbox_metadata_and_secret_are_derived_server_side(self):
        owned = {
            "status": "ok",
            "user": {"email": "owner@example.com", "name": "Owner", "userType": "member"},
            "config": {},
            "inbox": {
                "id": "demo",
                "credentialVersion": CREDENTIAL_VERSION_A,
                "email": "demo@example.com",
                "provider": "custom_imap",
                "connected": True,
                "connectionStatus": "connected",
                "imapConnectionStatus": "connected",
                "smtpConnectionStatus": "connected",
                "fullyConnected": True,
                "customImap": {
                    "host": "imap.example.com",
                    "port": "993",
                    "ssl": True,
                    "username": "imap-user",
                },
                "customSmtp": {
                    "host": "smtp.example.com",
                    "port": "587",
                    "security": "starttls",
                    "username": "smtp-user",
                    "useSameCredentials": False,
                },
            },
            "error": None,
        }
        with patch.object(
            authenticated_imap,
            "resolve_owned_managed_inbox_record",
            return_value=owned,
        ), patch.object(
            authenticated_imap,
            "read_mailbox_secret",
            return_value={
                "status": "present",
                "record": {
                    "credentialVersion": CREDENTIAL_VERSION_A,
                    "imapPassword": "imap-secret",
                    "smtpPassword": "smtp-secret",
                },
                "error": None,
            },
        ):
            result = authenticated_imap.resolve_authenticated_imap_mailbox(
                {"untrusted": "ignored"},
                "demo",
                require_smtp=True,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["mailbox"]["imap"]["host"], "imap.example.com")
        self.assertEqual(result["mailbox"]["smtp"]["password"], "smtp-secret")

    def test_missing_secret_is_reconnect_but_outage_is_503(self):
        owned = {
            "status": "ok",
            "user": {"email": "owner@example.com"},
            "config": {},
            "inbox": {
                "id": "demo",
                "credentialVersion": CREDENTIAL_VERSION_A,
                "email": "demo@example.com",
                "provider": "custom_imap",
                "connected": True,
                "connectionStatus": "connected",
                "customImap": {"host": "imap.example.com", "port": "993", "ssl": True, "username": "u"},
                "customSmtp": {},
            },
            "error": None,
        }
        with patch.object(authenticated_imap, "resolve_owned_managed_inbox_record", return_value=owned):
            for secret_status, expected_status, expected_http in (
                ("missing", "reconnect_required", 409),
                ("unavailable", "service_unavailable", 503),
                ("malformed", "malformed", 500),
            ):
                with self.subTest(secret_status=secret_status), patch.object(
                    authenticated_imap,
                    "read_mailbox_secret",
                    return_value={"status": secret_status, "record": None, "error": None},
                ):
                    result = authenticated_imap.resolve_authenticated_imap_mailbox({}, "demo")
                self.assertEqual(result["status"], expected_status)
                self.assertEqual(result["error"]["status_code"], expected_http)

    def test_generation_mismatch_and_malformed_legacy_states_fail_before_network(self):
        base_inbox = {
            "id": "demo",
            "email": "demo@example.com",
            "provider": "custom_imap",
            "connected": True,
            "connectionStatus": "connected",
            "credentialVersion": CREDENTIAL_VERSION_A,
            "customImap": {
                "host": "imap.example.com",
                "port": "993",
                "ssl": True,
                "username": "imap-user",
            },
            "customSmtp": {},
        }
        cases = (
            (
                {**base_inbox},
                {
                    "credentialVersion": CREDENTIAL_VERSION_B,
                    "imapPassword": "wrong-generation",
                    "smtpPassword": "",
                },
            ),
            (
                {
                    key: value
                    for key, value in base_inbox.items()
                    if key != "credentialVersion"
                },
                {
                    "credentialVersion": CREDENTIAL_VERSION_A,
                    "imapPassword": "secret",
                    "smtpPassword": "",
                },
            ),
            (
                {**base_inbox},
                {"imapPassword": "legacy", "smtpPassword": ""},
            ),
            (
                {**base_inbox, "credentialVersion": "malformed"},
                {
                    "credentialVersion": CREDENTIAL_VERSION_A,
                    "imapPassword": "secret",
                    "smtpPassword": "",
                },
            ),
        )
        for inbox, secret_record in cases:
            with self.subTest(
                config_generation=inbox.get("credentialVersion"),
                secret_generation=secret_record.get("credentialVersion"),
            ):
                owned = {
                    "status": "ok",
                    "user": {"email": "owner@example.com"},
                    "config": {"managedInboxes": [inbox]},
                    "inbox": inbox,
                    "error": None,
                }
                handler = FakeHandler(
                    {"mode": "refresh", "mailboxId": "demo", "limit": 20}
                )
                with patch.object(
                    connect_route,
                    "resolve_authenticated_user",
                    return_value=({"email": "owner@example.com"}, None),
                ), patch.object(
                    authenticated_imap,
                    "resolve_owned_managed_inbox_record",
                    return_value=owned,
                ), patch.object(
                    authenticated_imap,
                    "read_mailbox_secret",
                    return_value={
                        "status": "present",
                        "record": secret_record,
                        "error": None,
                    },
                ), patch.object(
                    imap_connect_preview,
                    "build_connect_preview_response",
                ) as network_helper:
                    invoke_connect(handler)

                self.assertEqual(handler.status, 409)
                self.assertEqual(
                    handler.response()["error"]["code"],
                    "reconnect_required",
                )
                network_helper.assert_not_called()


class InitialAndRefreshTests(unittest.TestCase):
    def setUp(self):
        self.initial_authority_patcher = patch.object(
            connect_route,
            "resolve_owned_initial_imap_registration",
            return_value={
                "status": "ok",
                "user": {"email": "owner@example.com"},
                "inbox": None,
                "config": onboarding_config(completed=True),
                "error": None,
            },
        )
        self.initial_authority_patcher.start()
        self.addCleanup(self.initial_authority_patcher.stop)

    def test_refresh_status_gate_truth_table_stops_before_secrets_and_provider(self):
        missing = object()
        connected_values = (missing, False, True, 1)
        statuses = (
            missing,
            "not_connected",
            "oauth_required",
            "waiting_for_authentication",
            "authenticated_pending_activation",
            "connected",
            "connection_failed",
            "reconnect_required",
            "CONNECTED",
            "connected ",
        )

        for connected in connected_values:
            for connection_status in statuses:
                should_connect = connected is True and connection_status == "connected"
                with self.subTest(
                    connected=("missing" if connected is missing else repr(connected)),
                    connection_status=(
                        "missing"
                        if connection_status is missing
                        else repr(connection_status)
                    ),
                ):
                    handler = FakeHandler(
                        {
                            "mode": "refresh",
                            "mailboxId": "demo",
                            "limit": 20,
                        }
                    )
                    inbox = {
                        "id": "demo",
                        "credentialVersion": CREDENTIAL_VERSION_A,
                        "email": "demo@example.com",
                        "provider": "custom_imap",
                        "customImap": {
                            "host": "imap.example.com",
                            "port": "993",
                            "ssl": True,
                            "username": "imap-user",
                        },
                        "customSmtp": {
                            "host": "smtp.example.com",
                            "port": "587",
                            "security": "starttls",
                            "username": "smtp-user",
                            "useSameCredentials": False,
                        },
                    }
                    if connected is not missing:
                        inbox["connected"] = connected
                    if connection_status is not missing:
                        inbox["connectionStatus"] = connection_status

                    owned = {
                        "status": "ok",
                        "user": {"email": "owner@example.com"},
                        "config": {},
                        "inbox": inbox,
                        "error": None,
                    }
                    with patch.object(
                        connect_route,
                        "resolve_authenticated_user",
                        return_value=({"email": "owner@example.com"}, None),
                    ), patch.object(
                        authenticated_imap,
                        "resolve_owned_managed_inbox_record",
                        return_value=owned,
                    ), patch.object(
                        authenticated_imap,
                        "read_mailbox_secret",
                        return_value={
                            "status": "present",
                            "record": {
                                "credentialVersion": CREDENTIAL_VERSION_A,
                                "imapPassword": "imap-secret",
                                "smtpPassword": "smtp-secret",
                            },
                            "error": None,
                        },
                    ) as secret_lookup, patch.object(
                        imap_connect_preview,
                        "build_connect_preview_response",
                        return_value=(200, {"ok": True, "messages": []}),
                    ) as provider_fetch, patch.object(
                        imap_connect_preview,
                        "open_mailbox_connection",
                    ) as provider_connection:
                        invoke_connect(handler)

                    provider_connection.assert_not_called()
                    if should_connect:
                        self.assertEqual(handler.status, 200)
                        secret_lookup.assert_called_once_with(
                            "owner@example.com",
                            "demo",
                        )
                        provider_fetch.assert_called_once()
                    else:
                        self.assertEqual(handler.status, 409)
                        self.assertEqual(
                            handler.response()["error"]["code"],
                            "reconnect_required",
                        )
                        secret_lookup.assert_not_called()
                        provider_fetch.assert_not_called()

    def test_unauthenticated_initial_stops_before_imap_and_storage(self):
        handler = FakeHandler(initial_payload())
        with patch.object(connect_route, "resolve_authenticated_user", return_value=(None, {})), patch.object(
            imap_connect_preview,
            "build_connect_preview_response",
        ) as preview, patch.object(connect_route, "save_mailbox_secret") as save:
            invoke_connect(handler)
        self.assertEqual(handler.status, 401)
        preview.assert_not_called()
        save.assert_not_called()

    def test_unstable_id_and_failed_secret_save_never_persist_connected_config(self):
        user = {"email": "owner@example.com"}
        draft_handler = FakeHandler(initial_payload("draft-1"))
        with patch.object(connect_route, "resolve_authenticated_user", return_value=(user, None)):
            invoke_connect(draft_handler)
        self.assertEqual(draft_handler.status, 400)

        handler = FakeHandler(initial_payload())
        with patch.object(connect_route, "resolve_authenticated_user", return_value=(user, None)), patch.object(
            connect_route,
            "resolve_owned_managed_inbox_record",
            return_value=missing_connection_target(),
        ), patch.object(
            imap_connect_preview,
            "build_connect_preview_response",
            return_value=(200, {"ok": True, "messages": []}),
        ), patch.object(
            connect_route,
            "snapshot_encrypted_mailbox_secret",
            return_value={"status": "missing", "record": None, "error": None},
        ), patch.object(
            connect_route,
            "save_mailbox_secret",
            return_value=(None, {"code": "unavailable"}),
        ), patch.object(connect_route, "upsert_owned_custom_imap_mailbox") as upsert:
            invoke_connect(handler)
        self.assertEqual(handler.status, 503)
        self.assertNotIn("one-time-imap", json.dumps(handler.response()))
        upsert.assert_not_called()

    def test_refresh_rejects_browser_identity_before_resolving(self):
        handler = FakeHandler(
            {"mode": "refresh", "mailboxId": "demo", "host": "evil.example", "password": "x"}
        )
        with patch.object(connect_route, "resolve_authenticated_user", return_value=({"email": "owner@example.com"}, None)), patch.object(
            connect_route,
            "resolve_authenticated_imap_mailbox",
        ) as resolver:
            invoke_connect(handler)
        self.assertEqual(handler.status, 400)
        resolver.assert_not_called()

    def test_initial_rejects_any_existing_id_before_provider_or_storage(self):
        user = {"email": "owner@example.com"}
        for provider in ("custom_imap", "google"):
            with self.subTest(provider=provider):
                handler = FakeHandler(initial_payload())
                with patch.object(
                    connect_route,
                    "resolve_authenticated_user",
                    return_value=(user, None),
                ), patch.object(
                    connect_route,
                    "resolve_owned_managed_inbox_record",
                    return_value=existing_connection_target(provider),
                ), patch.object(
                    imap_connect_preview,
                    "build_connect_preview_response",
                ) as preview, patch.object(
                    connect_route,
                    "save_mailbox_secret",
                ) as save:
                    invoke_connect(handler)

                self.assertEqual(handler.status, 409)
                self.assertEqual(handler.response()["error"]["code"], "mailbox_id_conflict")
                preview.assert_not_called()
                save.assert_not_called()

    def test_reconnect_rejects_missing_and_non_custom_targets(self):
        user = {"email": "owner@example.com"}
        for target, expected_status, expected_code in (
            (missing_connection_target(), 404, "reconnect_target_not_found"),
            (existing_connection_target("google"), 409, "invalid_reconnect_target"),
        ):
            with self.subTest(expected_code=expected_code):
                handler = FakeHandler(initial_payload(mode="reconnect"))
                with patch.object(
                    connect_route,
                    "resolve_authenticated_user",
                    return_value=(user, None),
                ), patch.object(
                    connect_route,
                    "resolve_owned_managed_inbox_record",
                    return_value=target,
                ), patch.object(
                    imap_connect_preview,
                    "build_connect_preview_response",
                ) as preview, patch.object(
                    connect_route,
                    "save_mailbox_secret",
                ) as save:
                    invoke_connect(handler)

                self.assertEqual(handler.status, expected_status)
                self.assertEqual(handler.response()["error"]["code"], expected_code)
                preview.assert_not_called()
                save.assert_not_called()

    def test_reconnect_after_expired_lease_reacquisition_rechecks_generation_before_writes(self):
        user = {"email": "owner@example.com"}
        target = existing_connection_target()
        cases = (
            (
                {
                    "status": "present",
                    "record": {
                        "credentialVersion": CREDENTIAL_VERSION_B,
                        "imapPassword": "winner",
                        "smtpPassword": "winner",
                    },
                    "error": None,
                },
                409,
                "reconnect_required",
            ),
            (
                {"status": "missing", "record": None, "error": None},
                409,
                "reconnect_required",
            ),
            (
                {
                    "status": "malformed",
                    "record": None,
                    "error": {"code": "mailbox_secret_malformed"},
                },
                503,
                "mailbox_secret_store_unavailable",
            ),
        )
        for secret_result, expected_status, expected_code in cases:
            with self.subTest(secret_status=secret_result["status"]):
                handler = FakeHandler(initial_payload(mode="reconnect"))
                with patch.object(
                    connect_route,
                    "resolve_authenticated_user",
                    return_value=(user, None),
                ), patch.object(
                    connect_route,
                    "acquire_mailbox_mutation_lease",
                    return_value={
                        "status": "acquired",
                        "token": "n" * 43,
                        "error": None,
                    },
                ) as acquire_lease, patch.object(
                    connect_route,
                    "release_mailbox_mutation_lease",
                    return_value={
                        "status": "released",
                        "token": "n" * 43,
                        "error": None,
                    },
                ) as release_lease, patch.object(
                    connect_route,
                    "resolve_owned_managed_inbox_record",
                    return_value=target,
                ), patch.object(
                    connect_route,
                    "snapshot_encrypted_mailbox_secret",
                    return_value={
                        "status": "present",
                        "record": {"raw": "exact-v0"},
                        "error": None,
                    },
                ) as snapshot, patch.object(
                    connect_route,
                    "read_mailbox_secret",
                    return_value=secret_result,
                ) as secret_read, patch.object(
                    imap_connect_preview,
                    "build_connect_preview_response",
                ) as preview, patch.object(
                    connect_route,
                    "save_mailbox_secret",
                ) as save, patch.object(
                    connect_route,
                    "upsert_owned_custom_imap_mailbox",
                ) as upsert:
                    invoke_connect(handler, use_real_lease=True)

                self.assertEqual(handler.status, expected_status)
                self.assertEqual(
                    handler.response()["error"]["code"],
                    expected_code,
                )
                snapshot.assert_called_once_with("owner@example.com", "demo")
                secret_read.assert_called_once_with("owner@example.com", "demo")
                acquire_lease.assert_called_once_with(
                    "owner@example.com",
                    "demo",
                )
                release_lease.assert_called_once_with(
                    "owner@example.com",
                    "demo",
                    "n" * 43,
                )
                preview.assert_not_called()
                save.assert_not_called()
                upsert.assert_not_called()

    def test_initial_without_smtp_persists_no_smtp_secret_or_configuration(self):
        user = {"email": "owner@example.com"}
        payload = initial_payload()
        payload["connection"].pop("smtp")
        parsed, parse_error = connect_route._parse_credential_connection(payload)
        self.assertIsNone(parse_error)
        expected_inbox = connect_route._build_expected_credential_mailbox(
            parsed,
            payload,
            None,
            CREDENTIAL_VERSION_B,
        )
        saved_secret = {
            "credentialVersion": CREDENTIAL_VERSION_B,
            "imapPassword": "one-time-imap",
            "smtpPassword": "",
        }
        exact_config_readback = {
            "status": "ok",
            "user": user,
            "inbox": expected_inbox,
            "config": {"managedInboxes": [expected_inbox]},
            "error": None,
        }
        missing_snapshot = {
            "status": "missing",
            "record": None,
            "error": None,
        }
        handler = FakeHandler(payload)

        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=(user, None),
        ), patch.object(
            connect_route,
            "resolve_owned_managed_inbox_record",
            side_effect=[missing_connection_target(), exact_config_readback],
        ), patch.object(
            imap_connect_preview,
            "build_connect_preview_response",
            return_value=(200, {"ok": True, "messages": []}),
        ), patch.object(
            connect_route,
            "snapshot_mailbox_secret_namespace",
            return_value=missing_snapshot,
        ), patch.object(
            connect_route,
            "generate_mailbox_credential_version",
            return_value=CREDENTIAL_VERSION_B,
        ), patch.object(
            connect_route,
            "save_mailbox_secret",
            return_value=(saved_secret, None),
        ) as save, patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
            return_value={"status": "ok", "error": None},
        ) as upsert, patch.object(
            connect_route,
            "read_mailbox_secret",
            return_value={
                "status": "present",
                "record": saved_secret,
                "error": None,
            },
        ), patch.object(
            connect_route,
            "restore_encrypted_mailbox_secret_snapshot",
        ) as secret_rollback:
            invoke_connect(handler)

        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.response(), {"ok": True, "messages": []})
        self.assertEqual(expected_inbox["customSmtp"], {})
        save.assert_called_once_with(
            "owner@example.com",
            "demo",
            imap_password="one-time-imap",
            smtp_password=None,
            credential_version=CREDENTIAL_VERSION_B,
            expected_snapshot=missing_snapshot,
            require_namespace_missing=True,
        )
        self.assertEqual(upsert.call_args.args[3]["customSmtp"], {})
        secret_rollback.assert_not_called()

    def test_reconnect_without_passwords_reuses_server_secret_without_rotation(self):
        user = {"email": "owner@example.com"}
        payload = initial_payload(mode="reconnect")
        payload["connection"]["imap"].pop("password")
        payload["connection"].pop("smtp")
        target = complete_connection_target()
        parsed, parse_error = connect_route._parse_credential_connection(payload)
        self.assertIsNone(parse_error)
        expected_inbox = connect_route._build_expected_credential_mailbox(
            parsed,
            payload,
            target["inbox"],
            CREDENTIAL_VERSION_A,
        )
        prior_snapshot = {
            "status": "present",
            "record": {"raw": "exact-prior-secret"},
            "error": None,
        }
        prior_secret = {
            "status": "present",
            "record": {
                "credentialVersion": CREDENTIAL_VERSION_A,
                "imapPassword": "server-imap-secret",
                "smtpPassword": "server-smtp-secret",
            },
            "error": None,
        }
        exact_config_readback = {
            "status": "ok",
            "user": user,
            "inbox": expected_inbox,
            "config": {"managedInboxes": [expected_inbox]},
            "error": None,
        }
        handler = FakeHandler(payload)

        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=(user, None),
        ), patch.object(
            connect_route,
            "resolve_owned_managed_inbox_record",
            side_effect=[target, exact_config_readback],
        ), patch.object(
            connect_route,
            "snapshot_encrypted_mailbox_secret",
            return_value=prior_snapshot,
        ) as snapshot, patch.object(
            connect_route,
            "read_mailbox_secret",
            side_effect=[prior_secret, prior_secret],
        ), patch.object(
            imap_connect_preview,
            "build_connect_preview_response",
            return_value=(200, {"ok": True, "messages": []}),
        ) as preview, patch.object(
            connect_route,
            "generate_mailbox_credential_version",
        ) as generate, patch.object(
            connect_route,
            "save_mailbox_secret",
        ) as save, patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
            return_value={"status": "ok", "error": None},
        ) as upsert, patch.object(
            connect_route,
            "rollback_owned_custom_imap_mailbox_update",
        ) as config_rollback, patch.object(
            connect_route,
            "restore_encrypted_mailbox_secret_snapshot",
        ) as secret_rollback:
            invoke_connect(handler)

        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.response(), {"ok": True, "messages": []})
        self.assertEqual(
            preview.call_args.args[0]["password"],
            "server-imap-secret",
        )
        self.assertEqual(
            expected_inbox["customSmtp"],
            target["inbox"]["customSmtp"],
        )
        self.assertEqual(
            upsert.call_args.kwargs["credential_version"],
            CREDENTIAL_VERSION_A,
        )
        self.assertEqual(
            upsert.call_args.args[3]["customSmtp"],
            target["inbox"]["customSmtp"],
        )
        snapshot.assert_not_called()
        generate.assert_not_called()
        save.assert_not_called()
        config_rollback.assert_not_called()
        secret_rollback.assert_not_called()

    def test_incoming_only_reconnect_reuses_imap_secret_without_creating_smtp(self):
        user = {"email": "owner@example.com"}
        payload = initial_payload(mode="reconnect")
        payload["connection"]["imap"].pop("password")
        payload["connection"].pop("smtp")
        target = complete_connection_target()
        target["inbox"]["onboardingInboxId"] = "promo"
        target["inbox"]["customSmtp"] = {}
        target["config"]["managedInboxes"][0]["onboardingInboxId"] = "promo"
        target["config"]["managedInboxes"][0]["customSmtp"] = {}
        parsed, parse_error = connect_route._parse_credential_connection(payload)
        self.assertIsNone(parse_error)
        expected_inbox = connect_route._build_expected_credential_mailbox(
            parsed,
            payload,
            target["inbox"],
            CREDENTIAL_VERSION_A,
        )
        prior_secret = {
            "status": "present",
            "record": {
                "credentialVersion": CREDENTIAL_VERSION_A,
                "imapPassword": "server-imap-secret",
                "smtpPassword": "",
            },
            "error": None,
        }
        exact_config_readback = {
            "status": "ok",
            "user": user,
            "inbox": expected_inbox,
            "config": {"managedInboxes": [expected_inbox]},
            "error": None,
        }
        handler = FakeHandler(payload)

        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=(user, None),
        ), patch.object(
            connect_route,
            "resolve_owned_initial_imap_registration",
            return_value={"status": "ok", "error": None},
        ) as reconnect_authority, patch.object(
            connect_route,
            "resolve_owned_managed_inbox_record",
            side_effect=[target, exact_config_readback],
        ), patch.object(
            connect_route,
            "snapshot_encrypted_mailbox_secret",
        ) as snapshot, patch.object(
            connect_route,
            "read_mailbox_secret",
            side_effect=[prior_secret, prior_secret],
        ) as secret_read, patch.object(
            imap_connect_preview,
            "build_connect_preview_response",
            return_value=(200, {"ok": True, "messages": []}),
        ) as preview, patch.object(
            connect_route,
            "generate_mailbox_credential_version",
        ) as generate, patch.object(
            connect_route,
            "save_mailbox_secret",
        ) as save, patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
            return_value={"status": "ok", "error": None},
        ) as upsert, patch.object(
            connect_route,
            "rollback_owned_custom_imap_mailbox_update",
        ) as config_rollback, patch.object(
            connect_route,
            "restore_encrypted_mailbox_secret_snapshot",
        ) as secret_rollback:
            invoke_connect(handler)

        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.response(), {"ok": True, "messages": []})
        self.assertEqual(
            preview.call_args.args[0]["password"],
            "server-imap-secret",
        )
        self.assertEqual(expected_inbox["customSmtp"], {})
        self.assertEqual(upsert.call_args.args[3]["customSmtp"], {})
        self.assertEqual(
            upsert.call_args.kwargs["credential_version"],
            CREDENTIAL_VERSION_A,
        )
        reconnect_authority.assert_called_once_with(handler.headers)
        self.assertEqual(secret_read.call_count, 2)
        snapshot.assert_not_called()
        generate.assert_not_called()
        save.assert_not_called()
        config_rollback.assert_not_called()
        secret_rollback.assert_not_called()

    def test_reconnect_new_imap_password_preserves_absent_smtp_update(self):
        user = {"email": "owner@example.com"}
        payload = initial_payload(mode="reconnect")
        payload["connection"].pop("smtp")
        target = complete_connection_target()
        parsed, parse_error = connect_route._parse_credential_connection(payload)
        self.assertIsNone(parse_error)
        expected_inbox = connect_route._build_expected_credential_mailbox(
            parsed,
            payload,
            target["inbox"],
            CREDENTIAL_VERSION_B,
        )
        prior_snapshot = {
            "status": "present",
            "record": {"raw": "exact-prior-secret"},
            "error": None,
        }
        prior_secret = {
            "status": "present",
            "record": {
                "credentialVersion": CREDENTIAL_VERSION_A,
                "imapPassword": "old-imap-secret",
                "smtpPassword": "server-smtp-secret",
            },
            "error": None,
        }
        saved_secret = {
            "credentialVersion": CREDENTIAL_VERSION_B,
            "imapPassword": "one-time-imap",
            "smtpPassword": "server-smtp-secret",
        }
        exact_config_readback = {
            "status": "ok",
            "user": user,
            "inbox": expected_inbox,
            "config": {"managedInboxes": [expected_inbox]},
            "error": None,
        }
        handler = FakeHandler(payload)

        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=(user, None),
        ), patch.object(
            connect_route,
            "resolve_owned_managed_inbox_record",
            side_effect=[target, exact_config_readback],
        ), patch.object(
            connect_route,
            "snapshot_encrypted_mailbox_secret",
            return_value=prior_snapshot,
        ), patch.object(
            connect_route,
            "read_mailbox_secret",
            side_effect=[
                prior_secret,
                {
                    "status": "present",
                    "record": saved_secret,
                    "error": None,
                },
            ],
        ), patch.object(
            imap_connect_preview,
            "build_connect_preview_response",
            return_value=(200, {"ok": True, "messages": []}),
        ), patch.object(
            connect_route,
            "generate_mailbox_credential_version",
            return_value=CREDENTIAL_VERSION_B,
        ) as generate, patch.object(
            connect_route,
            "save_mailbox_secret",
            return_value=(saved_secret, None),
        ) as save, patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
            return_value={"status": "ok", "error": None},
        ) as upsert, patch.object(
            connect_route,
            "rollback_owned_custom_imap_mailbox_update",
        ) as config_rollback, patch.object(
            connect_route,
            "restore_encrypted_mailbox_secret_snapshot",
        ) as secret_rollback:
            invoke_connect(handler)

        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.response(), {"ok": True, "messages": []})
        generate.assert_called_once_with()
        save.assert_called_once_with(
            "owner@example.com",
            "demo",
            imap_password="one-time-imap",
            smtp_password=None,
            credential_version=CREDENTIAL_VERSION_B,
            expected_snapshot=prior_snapshot,
            require_namespace_missing=False,
        )
        self.assertEqual(saved_secret["smtpPassword"], "server-smtp-secret")
        self.assertEqual(
            expected_inbox["customSmtp"],
            target["inbox"]["customSmtp"],
        )
        self.assertEqual(
            upsert.call_args.args[3]["customSmtp"],
            target["inbox"]["customSmtp"],
        )
        config_rollback.assert_not_called()
        secret_rollback.assert_not_called()

    def test_reconnect_new_shared_imap_password_retests_preserved_smtp_before_rotation(self):
        user = {"email": "owner@example.com"}
        payload = initial_payload(mode="reconnect")
        payload["connection"].pop("smtp")
        target = complete_connection_target()
        target["inbox"].update(
            {
                "imapConnectionStatus": "connected",
                "smtpConnectionStatus": "connected",
                "fullyConnected": True,
                "customSmtp": {
                    "host": "smtp.old.example.com",
                    "port": "465",
                    "security": "ssl",
                    "username": "",
                    "useSameCredentials": True,
                },
            }
        )
        prior_snapshot = {
            "status": "present",
            "record": {"raw": "exact-prior-secret"},
            "error": None,
        }
        prior_secret = {
            "status": "present",
            "record": {
                "credentialVersion": CREDENTIAL_VERSION_A,
                "imapPassword": "old-imap-secret",
                "smtpPassword": "",
            },
            "error": None,
        }
        handler = FakeHandler(payload)

        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=(user, None),
        ), patch.object(
            connect_route,
            "resolve_owned_managed_inbox_record",
            return_value=target,
        ), patch.object(
            connect_route,
            "snapshot_encrypted_mailbox_secret",
            return_value=prior_snapshot,
        ), patch.object(
            connect_route,
            "read_mailbox_secret",
            return_value=prior_secret,
        ), patch.object(
            imap_connect_preview,
            "build_connect_preview_response",
            return_value=(200, {"ok": True, "messages": []}),
        ) as imap_preview, patch.object(
            connect_route,
            "generate_mailbox_credential_version",
        ) as generate, patch.object(
            connect_route,
            "save_mailbox_secret",
        ) as save, patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
        ) as upsert:
            smtp_test = invoke_connect(
                handler,
                smtp_result=(
                    401,
                    {
                        "ok": False,
                        "error": {"code": "smtp_authentication_failed"},
                    },
                ),
            )

        self.assertEqual(handler.status, 502)
        self.assertEqual(
            handler.response()["error"]["code"],
            "smtp_authentication_failed",
        )
        self.assertEqual(
            imap_preview.call_args.args[0]["password"],
            "one-time-imap",
        )
        smtp_test.assert_called_once_with(
            {
                "host": "smtp.old.example.com",
                "port": 465,
                "security": "ssl",
                "username": "demo@example.com",
                "password": "one-time-imap",
            }
        )
        generate.assert_not_called()
        save.assert_not_called()
        upsert.assert_not_called()

    def test_credential_payload_validation_stops_before_target_network_or_secrets(self):
        cases = []

        payload = initial_payload()
        payload["connection"]["smtp"] = {}
        cases.append(("empty smtp", payload, "invalid_request"))

        payload = initial_payload()
        payload["connection"]["smtp"] = {"host": "smtp.example.com"}
        cases.append(("partial smtp", payload, "invalid_request"))

        payload = initial_payload()
        payload["connection"]["smtp"] = {"password": "one-time-smtp"}
        cases.append(("smtp password without host", payload, "invalid_request"))

        payload = initial_payload()
        payload["connection"]["smtp"] = {
            "port": "587",
            "security": "starttls",
            "username": "smtp-user@example.com",
            "password": "one-time-smtp",
            "useSameCredentials": False,
        }
        cases.append(("smtp username without host", payload, "invalid_request"))

        payload = initial_payload()
        payload["connection"]["smtp"]["port"] = ""
        cases.append(("smtp missing port", payload, "invalid_request"))

        payload = initial_payload()
        payload["connection"]["smtp"].pop("security")
        cases.append(("smtp missing security", payload, "invalid_request"))

        payload = initial_payload()
        payload["connection"]["smtp"] = None
        cases.append(("null smtp", payload, "invalid_request"))

        payload = initial_payload()
        payload["connection"]["imap"]["ssl"] = False
        cases.append(("imap ssl false", payload, "tls_required"))

        payload = initial_payload()
        payload["connection"]["imap"]["password"] = "   "
        cases.append(("initial blank imap password", payload, "invalid_request"))

        for placeholder in (
            "••••••••",
            "******",
            "●●●●●●",
            "stored securely",
            "Stored securely — leave blank to reuse",
        ):
            payload = initial_payload(mode="reconnect")
            payload["connection"]["imap"]["password"] = placeholder
            cases.append(
                (f"imap placeholder {placeholder!r}", payload, "invalid_request")
            )

        for placeholder in (
            "••••••••",
            "******",
            "●●●●●●",
            "stored securely",
            "Stored securely — leave blank to reuse",
            "   ",
        ):
            payload = initial_payload()
            payload["connection"]["smtp"].update(
                {
                    "username": "smtp-user@example.com",
                    "password": placeholder,
                    "useSameCredentials": False,
                }
            )
            cases.append(
                (f"smtp placeholder {placeholder!r}", payload, "invalid_request")
            )

        for label, payload, expected_code in cases:
            with self.subTest(label=label):
                handler = FakeHandler(payload)
                with patch.object(
                    connect_route,
                    "resolve_authenticated_user",
                    return_value=({"email": "owner@example.com"}, None),
                ), patch.object(
                    connect_route,
                    "resolve_owned_managed_inbox_record",
                ) as target, patch.object(
                    imap_connect_preview,
                    "build_connect_preview_response",
                ) as preview, patch.object(
                    connect_route,
                    "snapshot_encrypted_mailbox_secret",
                ) as encrypted_snapshot, patch.object(
                    connect_route,
                    "snapshot_mailbox_secret_namespace",
                ) as namespace_snapshot, patch.object(
                    connect_route,
                    "read_mailbox_secret",
                ) as secret_read, patch.object(
                    connect_route,
                    "save_mailbox_secret",
                ) as save, patch.object(
                    connect_route,
                    "upsert_owned_custom_imap_mailbox",
                ) as upsert:
                    invoke_connect(handler)

                self.assertEqual(handler.status, 400)
                self.assertEqual(
                    handler.response()["error"]["code"],
                    expected_code,
                )
                target.assert_not_called()
                preview.assert_not_called()
                encrypted_snapshot.assert_not_called()
                namespace_snapshot.assert_not_called()
                secret_read.assert_not_called()
                save.assert_not_called()
                upsert.assert_not_called()

    def test_reconnect_smtp_config_secret_mismatch_stops_before_network(self):
        user = {"email": "owner@example.com"}
        payload = initial_payload(mode="reconnect")
        payload["connection"]["imap"].pop("password")
        payload["connection"].pop("smtp")
        target = complete_connection_target()
        prior_snapshot = {
            "status": "present",
            "record": {"raw": "exact-prior-secret"},
            "error": None,
        }
        mismatched_secret = {
            "status": "present",
            "record": {
                "credentialVersion": CREDENTIAL_VERSION_A,
                "imapPassword": "server-imap-secret",
                "smtpPassword": "",
            },
            "error": None,
        }
        handler = FakeHandler(payload)

        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=(user, None),
        ), patch.object(
            connect_route,
            "resolve_owned_managed_inbox_record",
            return_value=target,
        ), patch.object(
            connect_route,
            "snapshot_encrypted_mailbox_secret",
            return_value=prior_snapshot,
        ) as snapshot, patch.object(
            connect_route,
            "read_mailbox_secret",
            return_value=mismatched_secret,
        ) as secret_read, patch.object(
            imap_connect_preview,
            "build_connect_preview_response",
        ) as preview, patch.object(
            connect_route,
            "generate_mailbox_credential_version",
        ) as generate, patch.object(
            connect_route,
            "save_mailbox_secret",
        ) as save, patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
        ) as upsert:
            invoke_connect(handler)

        self.assertEqual(handler.status, 409)
        self.assertEqual(
            handler.response()["error"]["code"],
            "reconnect_required",
        )
        snapshot.assert_not_called()
        secret_read.assert_called_once_with("owner@example.com", "demo")
        preview.assert_not_called()
        generate.assert_not_called()
        save.assert_not_called()
        upsert.assert_not_called()

    def test_lost_config_ack_with_exact_generation_readback_is_success(self):
        user = {"email": "owner@example.com"}
        payload = initial_payload(mode="reconnect")
        target = existing_connection_target()
        parsed, parse_error = connect_route._parse_credential_connection(payload)
        self.assertIsNone(parse_error)
        expected_inbox = connect_route._build_expected_credential_mailbox(
            parsed,
            payload,
            target["inbox"],
            CREDENTIAL_VERSION_B,
        )
        exact_config_readback = {
            "status": "ok",
            "user": user,
            "inbox": expected_inbox,
            "config": {"managedInboxes": [expected_inbox]},
            "error": None,
        }
        old_secret = {
            "status": "present",
            "record": {
                "credentialVersion": CREDENTIAL_VERSION_A,
                "imapPassword": "old",
                "smtpPassword": "old",
            },
            "error": None,
        }
        exact_secret_readback = {
            "status": "present",
            "record": {
                "credentialVersion": CREDENTIAL_VERSION_B,
                "imapPassword": "one-time-imap",
                "smtpPassword": "one-time-imap",
            },
            "error": None,
        }
        handler = FakeHandler(payload)
        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=(user, None),
        ), patch.object(
            connect_route,
            "resolve_owned_managed_inbox_record",
            side_effect=[target, exact_config_readback],
        ), patch.object(
            connect_route,
            "snapshot_encrypted_mailbox_secret",
            return_value={
                "status": "present",
                "record": {"raw": "exact-v0"},
                "error": None,
            },
        ), patch.object(
            connect_route,
            "read_mailbox_secret",
            side_effect=[old_secret, exact_secret_readback],
        ), patch.object(
            imap_connect_preview,
            "build_connect_preview_response",
            return_value=(200, {"ok": True, "messages": []}),
        ), patch.object(
            connect_route,
            "generate_mailbox_credential_version",
            return_value=CREDENTIAL_VERSION_B,
        ), patch.object(
            connect_route,
            "save_mailbox_secret",
            return_value=(exact_secret_readback["record"], None),
        ), patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
            return_value={
                "status": "unavailable",
                "error": {"code": "user_config_store_unavailable"},
            },
        ), patch.object(
            connect_route,
            "rollback_owned_custom_imap_mailbox_update",
        ) as config_rollback, patch.object(
            connect_route,
            "restore_encrypted_mailbox_secret_snapshot",
        ) as secret_rollback:
            invoke_connect(handler)

        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.response(), {"ok": True, "messages": []})
        self.assertNotIn(
            CREDENTIAL_VERSION_B,
            json.dumps(handler.response()),
        )
        config_rollback.assert_not_called()
        secret_rollback.assert_not_called()

    def test_gmail_config_conflict_remerges_without_second_secret_rotation(self):
        user = {"email": "owner@example.com", "name": "Owner", "userType": "member"}
        payload = initial_payload(mode="reconnect")
        target = complete_connection_target()
        target["config"] = {
            "v": 1,
            "email": "owner@example.com",
            "smartFolders": [{"id": "before"}],
            "managedInboxes": [json.loads(json.dumps(target["inbox"]))],
        }
        gmail_inbox = {
            "id": "gmail-concurrent",
            "email": "gmail@example.com",
            "provider": "google",
            "connected": True,
            "connectionMethod": "oauth",
            "connectionStatus": "connected",
        }
        config_b = {
            **json.loads(json.dumps(target["config"])),
            "smartFolders": [{"id": "gmail-winner"}],
            "gmailConcurrentRevision": "B",
            "managedInboxes": [
                json.loads(json.dumps(target["inbox"])),
                gmail_inbox,
            ],
        }
        state = {
            "config": json.loads(json.dumps(target["config"])),
            "target_reads": 0,
            "writes": [],
        }

        def resolve_target(_headers, mailbox_id):
            self.assertEqual(mailbox_id, "demo")
            state["target_reads"] += 1
            if state["target_reads"] == 1:
                return json.loads(json.dumps(target))
            inbox = next(
                item
                for item in state["config"]["managedInboxes"]
                if item["id"] == "demo"
            )
            return {
                "status": "ok",
                "user": user,
                "inbox": json.loads(json.dumps(inbox)),
                "config": json.loads(json.dumps(state["config"])),
                "error": None,
            }

        def read_config(_store, _owner):
            return {
                "status": "ok",
                "config": json.loads(json.dumps(state["config"])),
                "error": None,
            }

        def write_config(_store, _owner, expected, replacement):
            state["writes"].append(json.loads(json.dumps(replacement)))
            if len(state["writes"]) == 1:
                self.assertEqual(expected, target["config"])
                state["config"] = json.loads(json.dumps(config_b))
                return {
                    "status": "conflict",
                    "record": None,
                    "error": {
                        "code": "user_config_write_conflict",
                        "message": "Gmail callback committed config B",
                    },
                }
            self.assertEqual(expected, config_b)
            state["config"] = json.loads(json.dumps(replacement))
            return {"status": "ok", "record": replacement, "error": None}

        previous_secret = {
            "status": "present",
            "record": {
                "credentialVersion": CREDENTIAL_VERSION_A,
                "imapPassword": "old-imap",
                "smtpPassword": "old-smtp",
            },
            "error": None,
        }
        next_secret_record = {
            "credentialVersion": CREDENTIAL_VERSION_B,
            "imapPassword": "one-time-imap",
            "smtpPassword": "one-time-imap",
        }
        handler = FakeHandler(payload)
        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=(user, None),
        ), patch.object(
            connect_route,
            "resolve_owned_managed_inbox_record",
            side_effect=resolve_target,
        ), patch.object(
            connect_route,
            "snapshot_encrypted_mailbox_secret",
            return_value={
                "status": "present",
                "record": {"raw": "exact-prior-secret"},
                "error": None,
            },
        ), patch.object(
            connect_route,
            "read_mailbox_secret",
            side_effect=[
                previous_secret,
                {
                    "status": "present",
                    "record": next_secret_record,
                    "error": None,
                },
            ],
        ), patch.object(
            connect_route,
            "generate_mailbox_credential_version",
            return_value=CREDENTIAL_VERSION_B,
        ), patch.object(
            connect_route,
            "save_mailbox_secret",
            return_value=(next_secret_record, None),
        ) as save_secret, patch.object(
            imap_connect_preview,
            "build_connect_preview_response",
            return_value=(200, {"ok": True, "messages": []}),
        ), patch.object(
            user_config_store,
            "resolve_authenticated_user",
            return_value=(user, None),
        ), patch.object(
            user_config_store,
            "resolve_user_config_store",
            return_value=({"configured": True}, None),
        ), patch.object(
            user_config_store,
            "read_user_config_record",
            side_effect=read_config,
        ), patch.object(
            user_config_store,
            "write_user_config_record_if_unchanged",
            side_effect=write_config,
        ), patch.object(
            connect_route,
            "rollback_owned_custom_imap_mailbox_update",
        ) as rollback_config, patch.object(
            connect_route,
            "restore_encrypted_mailbox_secret_snapshot",
        ) as rollback_secret:
            invoke_connect(handler)

        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.response(), {"ok": True, "messages": []})
        save_secret.assert_called_once()
        self.assertEqual(len(state["writes"]), 2)
        for replacement in state["writes"]:
            imap_mailbox = next(
                item
                for item in replacement["managedInboxes"]
                if item["id"] == "demo"
            )
            self.assertEqual(
                imap_mailbox["credentialVersion"],
                CREDENTIAL_VERSION_B,
            )
        self.assertEqual(
            state["config"]["managedInboxes"][1],
            gmail_inbox,
        )
        self.assertEqual(
            state["config"]["smartFolders"],
            [{"id": "gmail-winner"}],
        )
        self.assertEqual(state["config"]["gmailConcurrentRevision"], "B")
        rollback_config.assert_not_called()
        rollback_secret.assert_not_called()

    def test_held_mailbox_lease_rejects_before_any_target_snapshot_or_write(self):
        handler = FakeHandler(initial_payload(mode="reconnect"))
        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            connect_route,
            "acquire_mailbox_mutation_lease",
            return_value={
                "status": "held",
                "token": None,
                "error": {
                    "code": "mailbox_mutation_lease_conflict",
                    "message": "held",
                },
            },
        ), patch.object(
            connect_route,
            "release_mailbox_mutation_lease",
        ) as release, patch.object(
            connect_route,
            "resolve_owned_managed_inbox_record",
        ) as resolve_target, patch.object(
            connect_route,
            "snapshot_encrypted_mailbox_secret",
        ) as snapshot, patch.object(
            connect_route,
            "read_mailbox_secret",
        ) as read_secret, patch.object(
            imap_connect_preview,
            "build_connect_preview_response",
        ) as preview, patch.object(
            connect_route,
            "save_mailbox_secret",
        ) as save_secret, patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
        ) as upsert:
            invoke_connect(handler, use_real_lease=True)

        self.assertEqual(handler.status, 409)
        self.assertEqual(
            handler.response()["error"]["code"],
            "mailbox_connection_in_progress",
        )
        resolve_target.assert_not_called()
        snapshot.assert_not_called()
        read_secret.assert_not_called()
        preview.assert_not_called()
        save_secret.assert_not_called()
        upsert.assert_not_called()
        release.assert_not_called()

    def test_mailbox_lease_is_finally_held_through_readback_and_compensation(self):
        events = []
        user = {"email": "owner@example.com"}
        target = complete_connection_target()
        target_reads = {"count": 0}

        def resolve_target(_headers, _mailbox_id):
            target_reads["count"] += 1
            events.append(
                "target_snapshot"
                if target_reads["count"] == 1
                else "config_readback"
            )
            if target_reads["count"] == 1:
                return target
            return {
                "status": "unavailable",
                "inbox": None,
                "config": None,
                "error": None,
            }

        secret_reads = {"count": 0}

        def read_secret(_owner, _mailbox_id):
            secret_reads["count"] += 1
            events.append(
                "secret_snapshot"
                if secret_reads["count"] == 1
                else "secret_readback"
            )
            if secret_reads["count"] == 1:
                return {
                    "status": "present",
                    "record": {
                        "credentialVersion": CREDENTIAL_VERSION_A,
                        "imapPassword": "old-imap",
                        "smtpPassword": "old-smtp",
                    },
                    "error": None,
                }
            return {"status": "unavailable", "record": None, "error": None}

        def release(*_args):
            events.append("release")
            return {"status": "released", "token": "l" * 43, "error": None}

        handler = FakeHandler(initial_payload(mode="reconnect"))
        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=(user, None),
        ), patch.object(
            connect_route,
            "acquire_mailbox_mutation_lease",
            side_effect=lambda *_args: (
                events.append("acquire")
                or {"status": "acquired", "token": "l" * 43, "error": None}
            ),
        ), patch.object(
            connect_route,
            "release_mailbox_mutation_lease",
            side_effect=release,
        ), patch.object(
            connect_route,
            "resolve_owned_managed_inbox_record",
            side_effect=resolve_target,
        ), patch.object(
            connect_route,
            "snapshot_encrypted_mailbox_secret",
            side_effect=lambda *_args: (
                events.append("encrypted_snapshot")
                or {
                    "status": "present",
                    "record": {"raw": "exact-prior-secret"},
                    "error": None,
                }
            ),
        ), patch.object(
            connect_route,
            "read_mailbox_secret",
            side_effect=read_secret,
        ), patch.object(
            imap_connect_preview,
            "build_connect_preview_response",
            side_effect=lambda *_args: (
                events.append("preview")
                or (200, {"ok": True, "messages": []})
            ),
        ), patch.object(
            connect_route,
            "generate_mailbox_credential_version",
            return_value=CREDENTIAL_VERSION_B,
        ), patch.object(
            connect_route,
            "save_mailbox_secret",
            side_effect=lambda *_args, **_kwargs: (
                events.append("secret_write")
                or (
                    {
                        "credentialVersion": CREDENTIAL_VERSION_B,
                        "imapPassword": "one-time-imap",
                        "smtpPassword": "one-time-imap",
                    },
                    None,
                )
            ),
        ), patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
            side_effect=lambda *_args, **_kwargs: (
                events.append("config_write")
                or {"status": "unavailable", "error": {"code": "offline"}}
            ),
        ), patch.object(
            connect_route,
            "rollback_owned_custom_imap_mailbox_update",
            side_effect=lambda *_args: events.append("config_rollback"),
        ), patch.object(
            connect_route,
            "restore_encrypted_mailbox_secret_snapshot",
            side_effect=lambda *_args, **_kwargs: events.append("secret_rollback"),
        ):
            invoke_connect(handler, use_real_lease=True)

        self.assertEqual(handler.status, 503)
        self.assertEqual(
            events,
            [
                "acquire",
                "target_snapshot",
                "encrypted_snapshot",
                "secret_snapshot",
                "preview",
                "secret_write",
                "config_write",
                "config_readback",
                "secret_readback",
                "config_rollback",
                "secret_rollback",
                "release",
            ],
        )

    def test_onboarding_mailbox_reconnect_is_blocked_until_completion(self):
        target = existing_connection_target()
        target["inbox"]["onboardingInboxId"] = "promo"
        target["config"]["managedInboxes"][0]["onboardingInboxId"] = "promo"
        handler = FakeHandler(initial_payload(mode="reconnect"))
        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            connect_route,
            "resolve_owned_managed_inbox_record",
            return_value=target,
        ), patch.object(
            connect_route,
            "resolve_owned_initial_imap_registration",
            return_value={
                "status": "conflict",
                "user": {"email": "owner@example.com"},
                "inbox": None,
                "config": onboarding_config(completed=False),
                "error": {"code": "onboarding_incomplete", "message": "incomplete"},
            },
        ), patch.object(
            imap_connect_preview,
            "build_connect_preview_response",
        ) as preview, patch.object(
            connect_route,
            "snapshot_encrypted_mailbox_secret",
        ) as snapshot, patch.object(
            connect_route,
            "save_mailbox_secret",
        ) as save, patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
        ) as upsert:
            invoke_connect(handler)

        self.assertEqual(handler.status, 409)
        self.assertEqual(
            handler.response()["error"]["code"],
            "onboarding_reconnect_unavailable",
        )
        preview.assert_not_called()
        snapshot.assert_not_called()
        save.assert_not_called()
        upsert.assert_not_called()

    def _run_config_failure(self, mode, snapshot, rollback_error=None):
        handler = FakeHandler(initial_payload(mode=mode))
        user = {"email": "owner@example.com"}
        target = (
            missing_connection_target()
            if mode == "initial"
            else existing_connection_target()
        )
        prior_secret_result = (
            {
                "status": "missing",
                "record": None,
                "error": None,
            }
            if mode == "initial"
            else {
                "status": "present",
                "record": {
                    "credentialVersion": CREDENTIAL_VERSION_A,
                    "imapPassword": "old",
                    "smtpPassword": "old",
                },
                "error": None,
            }
        )
        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=(user, None),
        ), patch.object(
            connect_route,
            "resolve_owned_managed_inbox_record",
            return_value=target,
        ), patch.object(
            imap_connect_preview,
            "build_connect_preview_response",
            return_value=(200, {"ok": True, "messages": []}),
        ), patch.object(
            connect_route,
            "snapshot_encrypted_mailbox_secret",
            return_value=snapshot,
        ), patch.object(
            connect_route,
            "snapshot_mailbox_secret_namespace",
            return_value={"status": "missing", "record": None, "error": None},
        ), patch.object(
            connect_route,
            "read_mailbox_secret",
            return_value=prior_secret_result,
        ), patch.object(
            connect_route,
            "generate_mailbox_credential_version",
            return_value=CREDENTIAL_VERSION_B,
        ), patch.object(
            connect_route,
            "save_mailbox_secret",
            return_value=(
                {
                    "credentialVersion": CREDENTIAL_VERSION_B,
                    "imapPassword": "new-secret",
                },
                None,
            ),
        ), patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
            return_value={"status": "unavailable", "error": {"code": "offline"}},
        ), patch.object(
            connect_route,
            "rollback_owned_custom_imap_mailbox_update",
            return_value=None,
        ), patch.object(
            connect_route,
            "restore_encrypted_mailbox_secret_snapshot",
            return_value=rollback_error,
        ) as restore:
            invoke_connect(handler)
        return handler, restore

    def test_config_failure_restores_previous_reconnect_secret(self):
        snapshot = {
            "status": "present",
            "record": {"ciphertext": "exact-prior-v2-record"},
            "error": None,
        }
        handler, restore = self._run_config_failure("reconnect", snapshot)

        self.assertEqual(handler.status, 503)
        self.assertFalse(handler.response()["ok"])
        restore.assert_called_once_with(
            "owner@example.com",
            "demo",
            snapshot,
            expected_credential_version=CREDENTIAL_VERSION_B,
        )

    def test_config_failure_removes_new_initial_secret(self):
        snapshot = {"status": "missing", "record": None, "error": None}
        handler, restore = self._run_config_failure("initial", snapshot)

        self.assertEqual(handler.status, 503)
        self.assertFalse(handler.response()["ok"])
        restore.assert_called_once_with(
            "owner@example.com",
            "demo",
            snapshot,
            expected_credential_version=CREDENTIAL_VERSION_B,
        )

    def test_rollback_failure_fails_closed(self):
        snapshot = {"status": "missing", "record": None, "error": None}
        handler, _ = self._run_config_failure(
            "initial",
            snapshot,
            {"code": "mailbox_secret_store_unavailable", "message": "offline"},
        )

        self.assertEqual(handler.status, 503)
        self.assertEqual(
            handler.response()["error"]["code"],
            "mailbox_connection_rollback_failed",
        )


class OnboardingImapRegistrationTests(unittest.TestCase):
    def _invoke_with_config(self, config, payload=None):
        handler = FakeHandler(payload or onboarding_payload())
        user = {"email": "owner@example.com"}
        read_result = {
            "status": "ok",
            "config": config,
            "error": None,
        }
        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=(user, None),
        ), patch.object(
            user_config_store,
            "read_user_config_for_authenticated_user",
            return_value=(user, read_result),
        ), patch.object(
            imap_connect_preview,
            "build_secure_imap_authentication_response",
        ) as secure_connect, patch.object(
            connect_route,
            "snapshot_mailbox_secret_namespace",
        ) as snapshot, patch.object(
            connect_route,
            "save_mailbox_secret",
        ) as save, patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
        ) as upsert:
            invoke_connect(handler)
        return handler, secure_connect, snapshot, save, upsert

    def test_same_onboarding_position_uses_one_stable_lease_before_authority_snapshot(self):
        handler = FakeHandler(onboarding_payload("promo"))
        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            connect_route,
            "acquire_mailbox_mutation_lease",
            return_value={
                "status": "held",
                "token": None,
                "error": {
                    "code": "mailbox_mutation_lease_conflict",
                    "message": "request A holds this onboarding position",
                },
            },
        ) as acquire, patch.object(
            connect_route,
            "release_mailbox_mutation_lease",
        ) as release, patch.object(
            connect_route,
            "resolve_owned_onboarding_custom_imap_target",
        ) as resolve_target, patch.object(
            connect_route,
            "_prepare_server_mailbox_id",
        ) as prepare_id, patch.object(
            imap_connect_preview,
            "build_secure_imap_authentication_response",
        ) as preview, patch.object(
            connect_route,
            "snapshot_mailbox_secret_namespace",
        ) as snapshot, patch.object(
            connect_route,
            "save_mailbox_secret",
        ) as save_secret, patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
        ) as upsert:
            invoke_connect(handler, use_real_lease=True)

        self.assertEqual(handler.status, 409)
        self.assertEqual(
            handler.response()["error"]["code"],
            "mailbox_connection_in_progress",
        )
        acquire.assert_called_once_with(
            "owner@example.com",
            "onboarding:promo",
        )
        resolve_target.assert_not_called()
        prepare_id.assert_not_called()
        preview.assert_not_called()
        snapshot.assert_not_called()
        save_secret.assert_not_called()
        upsert.assert_not_called()
        release.assert_not_called()

    def test_onboarding_holds_server_id_lease_after_config_commit_until_readback(self):
        config = onboarding_config()
        generated_uuid = Mock(hex="d" * 32)
        mailbox_id = f"imap-{generated_uuid.hex}"
        user = {"email": "owner@example.com"}
        held = {}
        acquired_keys = []
        released_keys = []
        reconnect_attempts = []

        def acquire(owner_email, lease_mailbox_id):
            self.assertEqual(owner_email, "owner@example.com")
            if lease_mailbox_id in held:
                return {
                    "status": "held",
                    "token": None,
                    "error": {
                        "code": "mailbox_mutation_lease_conflict",
                        "message": "held",
                    },
                }
            token = ("p" if lease_mailbox_id.startswith("onboarding:") else "m") * 43
            held[lease_mailbox_id] = token
            acquired_keys.append(lease_mailbox_id)
            return {"status": "acquired", "token": token, "error": None}

        def release(owner_email, lease_mailbox_id, token):
            self.assertEqual(owner_email, "owner@example.com")
            self.assertEqual(held.get(lease_mailbox_id), token)
            del held[lease_mailbox_id]
            released_keys.append(lease_mailbox_id)
            return {"status": "released", "token": token, "error": None}

        def commit_config_then_probe_reconnect(*_args, **_kwargs):
            reconnect_attempts.append(acquire("owner@example.com", mailbox_id))
            return {"status": "ok", "error": None}

        handler = FakeHandler(onboarding_payload("promo"))
        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=(user, None),
        ), patch.object(
            connect_route,
            "acquire_mailbox_mutation_lease",
            side_effect=acquire,
        ), patch.object(
            connect_route,
            "release_mailbox_mutation_lease",
            side_effect=release,
        ), patch.object(
            connect_route,
            "resolve_owned_onboarding_custom_imap_target",
            return_value=onboarding_target(config),
        ), patch.object(
            connect_route.uuid,
            "uuid4",
            return_value=generated_uuid,
        ), patch.object(
            connect_route,
            "snapshot_mailbox_secret_namespace",
            return_value={"status": "missing", "record": None, "error": None},
        ), patch.object(
            imap_connect_preview,
            "build_secure_imap_authentication_response",
            return_value=(200, {"ok": True}),
        ), patch.object(
            connect_route,
            "generate_mailbox_credential_version",
            return_value=CREDENTIAL_VERSION_B,
        ), patch.object(
            connect_route,
            "save_mailbox_secret",
            return_value=(
                {
                    "credentialVersion": CREDENTIAL_VERSION_B,
                    "imapPassword": "one-time-onboarding-imap",
                    "smtpPassword": "",
                },
                None,
            ),
        ), patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
            side_effect=commit_config_then_probe_reconnect,
        ), patch.object(
            connect_route,
            "resolve_owned_managed_inbox_record",
            return_value=onboarding_readback(config, mailbox_id),
        ), patch.object(
            connect_route,
            "read_mailbox_secret",
            return_value={
                "status": "present",
                "record": {
                    "credentialVersion": CREDENTIAL_VERSION_B,
                    "imapPassword": "one-time-onboarding-imap",
                    "smtpPassword": "",
                },
                "error": None,
            },
        ):
            invoke_connect(handler, use_real_lease=True)

        self.assertEqual(handler.status, 200)
        self.assertEqual(
            acquired_keys,
            ["onboarding:promo", mailbox_id],
        )
        self.assertEqual(len(reconnect_attempts), 1)
        self.assertEqual(reconnect_attempts[0]["status"], "held")
        self.assertEqual(
            reconnect_attempts[0]["error"]["code"],
            "mailbox_mutation_lease_conflict",
        )
        self.assertEqual(
            released_keys,
            [mailbox_id, "onboarding:promo"],
        )
        self.assertEqual(held, {})

    def test_valid_selected_position_uses_server_id_imaps_and_imap_only_secret(self):
        config = onboarding_config()
        onboarding_before = json.dumps(config["onboardingSession"], sort_keys=True)
        handler = FakeHandler(onboarding_payload())
        user = {"email": "owner@example.com"}
        generated_uuid = Mock(hex="a" * 32)
        expected_id = f"imap-{generated_uuid.hex}"
        readback = onboarding_readback(config, expected_id)

        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=(user, None),
        ) as session_resolver, patch.object(
            connect_route,
            "resolve_owned_onboarding_custom_imap_target",
            return_value=onboarding_target(config),
        ) as target_resolver, patch.object(
            connect_route.uuid,
            "uuid4",
            return_value=generated_uuid,
        ), patch.object(
            connect_route,
            "generate_mailbox_credential_version",
            return_value=CREDENTIAL_VERSION_B,
        ), patch.object(
            imap_connect_preview,
            "build_secure_imap_authentication_response",
            return_value=(200, {"ok": True}),
        ) as secure_connect, patch.object(
            connect_route,
            "snapshot_mailbox_secret_namespace",
            return_value={"status": "missing", "record": None, "error": None},
        ) as snapshot, patch.object(
            connect_route,
            "save_mailbox_secret",
            return_value=({"imapPassword": "stored", "smtpPassword": ""}, None),
        ) as save, patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
            return_value={"status": "ok", "error": None},
        ) as upsert, patch.object(
            connect_route,
            "resolve_owned_managed_inbox_record",
            return_value=readback,
        ) as resolve_readback, patch.object(
            connect_route,
            "read_mailbox_secret",
            return_value={
                "status": "present",
                "record": {
                    "credentialVersion": CREDENTIAL_VERSION_B,
                    "imapPassword": "one-time-onboarding-imap",
                    "smtpPassword": "",
                },
                "error": None,
            },
        ), patch.object(
            connect_route,
            "restore_encrypted_mailbox_secret_snapshot",
        ) as restore_secret, patch.object(
            connect_route,
            "rollback_owned_custom_imap_mailbox_update",
        ) as restore_config:
            invoke_connect(handler)

        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.response(), {"ok": True})
        self.assertNotIn("one-time-onboarding-imap", json.dumps(handler.response()))
        self.assertNotIn(expected_id, json.dumps(handler.response()))
        self.assertNotIn(CREDENTIAL_VERSION_B, json.dumps(handler.response()))
        session_resolver.assert_called_once_with(handler.headers)
        target_resolver.assert_called_once_with(
            handler.headers,
            "promo",
            "promo@example.com",
            None,
        )
        secure_connect.assert_called_once_with(
            {
                "host": "imap.example.com",
                "port": 993,
                "ssl": True,
                "username": "promo@example.com",
                "password": "one-time-onboarding-imap",
            }
        )
        snapshot.assert_called_once_with("owner@example.com", expected_id)
        save.assert_called_once_with(
            "owner@example.com",
            expected_id,
            imap_password="one-time-onboarding-imap",
            smtp_password="",
            credential_version=CREDENTIAL_VERSION_B,
            expected_snapshot={"status": "missing", "record": None, "error": None},
            require_namespace_missing=True,
        )
        upsert.assert_called_once_with(
            handler.headers,
            expected_id,
            "initial",
            {
                "email": "promo@example.com",
                "onboardingInboxId": "promo",
                "customImap": {
                    "host": "imap.example.com",
                    "port": "993",
                    "ssl": True,
                    "username": "promo@example.com",
                },
                "customSmtp": {
                    "host": "smtp.example.com",
                    "port": "587",
                    "security": "starttls",
                    "username": "",
                    "useSameCredentials": True,
                },
                "imapConnectionStatus": "connected",
                "smtpConnectionStatus": "connected",
                "fullyConnected": True,
            },
            credential_version=CREDENTIAL_VERSION_B,
            expected_inbox=None,
            onboarding_inbox_id="promo",
            expected_onboarding_session=config["onboardingSession"],
        )
        resolve_readback.assert_called_once_with(handler.headers, expected_id)
        restore_secret.assert_not_called()
        restore_config.assert_not_called()
        self.assertEqual(
            json.dumps(config["onboardingSession"], sort_keys=True),
            onboarding_before,
        )

    def test_readback_mailbox_fingerprint_is_json_type_exact(self):
        config = onboarding_config()
        parsed, error = connect_route._parse_onboarding_connection(
            onboarding_payload()
        )
        self.assertIsNone(error)
        readback = onboarding_readback(config)
        readback["inbox"]["connected"] = 1

        self.assertFalse(
            connect_route._onboarding_readback_is_exact(
                readback,
                parsed,
                "imap-server-owned",
                CREDENTIAL_VERSION_B,
                config,
            )
        )
        self.assertIs(config["onboardingSession"]["completed"], False)

    def test_current_main_only_state_rejects_promo_before_network_or_writes(self):
        config = onboarding_config(selected_inboxes=["main"], inbox_count="1")
        before = json.dumps(config, sort_keys=True)
        handler, secure_connect, snapshot, save, upsert = self._invoke_with_config(config)

        self.assertEqual(handler.status, 409)
        self.assertEqual(handler.response()["error"]["code"], "inbox_position_not_selected")
        secure_connect.assert_not_called()
        snapshot.assert_not_called()
        save.assert_not_called()
        upsert.assert_not_called()
        self.assertEqual(json.dumps(config, sort_keys=True), before)
        self.assertEqual(config["onboardingSession"]["choices"]["selectedInboxes"], ["main"])
        self.assertEqual(config["onboardingSession"]["choices"]["inboxCount"], "1")
        self.assertEqual(config["onboardingSession"]["choices"]["customInboxes"], [])

    def test_client_chosen_mailbox_and_account_authority_fields_are_rejected(self):
        forbidden_fields = (
            "id",
            "mailboxId",
            "managedInboxId",
            "credentialId",
            "userId",
            "workspaceId",
            "ownerId",
            "ownerEmail",
            "oauthOwnerEmail",
        )
        for field in forbidden_fields:
            with self.subTest(field=field):
                payload = onboarding_payload()
                payload[field] = "attacker-controlled"
                handler = FakeHandler(payload)
                with patch.object(
                    connect_route,
                    "resolve_authenticated_user",
                    return_value=({"email": "owner@example.com"}, None),
                ), patch.object(
                    connect_route,
                    "resolve_owned_onboarding_custom_imap_target",
                ) as target, patch.object(
                    imap_connect_preview,
                    "build_secure_imap_authentication_response",
                ) as secure_connect, patch.object(
                    connect_route,
                    "save_mailbox_secret",
                ) as save, patch.object(
                    connect_route,
                    "upsert_owned_custom_imap_mailbox",
                ) as upsert:
                    invoke_connect(handler)

                self.assertEqual(handler.status, 400)
                self.assertEqual(
                    handler.response()["error"]["code"],
                    "forbidden_client_authority",
                )
                target.assert_not_called()
                secure_connect.assert_not_called()
                save.assert_not_called()
                upsert.assert_not_called()

    def test_client_generation_aliases_are_rejected_before_any_connection_action(self):
        for mode, payload_factory in (
            ("onboarding", onboarding_payload),
            ("initial", initial_payload),
            ("reconnect", lambda: initial_payload(mode="reconnect")),
        ):
            for field in (
                "credentialVersion",
                "secretVersion",
                "credentialGeneration",
                "secret_generation",
                "credential-revision",
            ):
                with self.subTest(mode=mode, field=field):
                    payload = payload_factory()
                    payload["connection"][field] = "attacker-controlled"
                    handler = FakeHandler(payload)
                    with patch.object(
                        connect_route,
                        "resolve_authenticated_user",
                        return_value=({"email": "owner@example.com"}, None),
                    ), patch.object(
                        connect_route,
                        "resolve_owned_managed_inbox_record",
                    ) as target, patch.object(
                        connect_route,
                        "resolve_owned_onboarding_custom_imap_target",
                    ) as onboarding_target_resolver, patch.object(
                        imap_connect_preview,
                        "build_connect_preview_response",
                    ) as preview, patch.object(
                        imap_connect_preview,
                        "build_secure_imap_authentication_response",
                    ) as onboarding_preview, patch.object(
                        connect_route,
                        "save_mailbox_secret",
                    ) as save, patch.object(
                        connect_route,
                        "upsert_owned_custom_imap_mailbox",
                    ) as upsert:
                        invoke_connect(handler)

                    self.assertEqual(handler.status, 400)
                    self.assertEqual(
                        handler.response()["error"]["code"],
                        "forbidden_client_authority",
                    )
                    target.assert_not_called()
                    onboarding_target_resolver.assert_not_called()
                    preview.assert_not_called()
                    onboarding_preview.assert_not_called()
                    save.assert_not_called()
                    upsert.assert_not_called()

    def test_semantic_generation_fields_are_forbidden_for_authenticated_actions(self):
        found = authenticated_imap.find_forbidden_custom_request_fields(
            {
                "nested": {
                    "Credential_Generation": "attacker",
                    "secret-revision": "attacker",
                }
            }
        )
        self.assertEqual(
            found,
            ["Credential_Generation", "secret-revision"],
        )

    def test_unknown_and_completed_onboarding_positions_fail_before_network(self):
        unknown_handler, unknown_connect, _, unknown_save, unknown_upsert = (
            self._invoke_with_config(
                onboarding_config(),
                onboarding_payload("unknown-position"),
            )
        )
        self.assertEqual(unknown_handler.status, 400)
        self.assertEqual(unknown_handler.response()["error"]["code"], "unknown_inbox_position")
        unknown_connect.assert_not_called()
        unknown_save.assert_not_called()
        unknown_upsert.assert_not_called()

        completed_handler, completed_connect, _, completed_save, completed_upsert = (
            self._invoke_with_config(onboarding_config(completed=True))
        )
        self.assertEqual(completed_handler.status, 409)
        self.assertEqual(completed_handler.response()["error"]["code"], "onboarding_completed")
        completed_connect.assert_not_called()
        completed_save.assert_not_called()
        completed_upsert.assert_not_called()

    def test_position_and_normalized_email_conflicts_fail_before_network(self):
        position_record = {
            "id": "imap-existing",
            "email": "other@example.com",
            "provider": "custom_imap",
            "connected": True,
            "connectionMethod": "imap",
            "connectionStatus": "connected",
            "onboardingInboxId": "promo",
        }
        position_config = onboarding_config(
            managed_inboxes=[*onboarding_config()["managedInboxes"], position_record]
        )
        handler, secure_connect, _, save, upsert = self._invoke_with_config(position_config)
        self.assertEqual(handler.status, 409)
        self.assertEqual(handler.response()["error"]["code"], "inbox_position_conflict")
        secure_connect.assert_not_called()
        save.assert_not_called()
        upsert.assert_not_called()

        duplicate_config = onboarding_config()
        duplicate_handler, duplicate_connect, _, duplicate_save, duplicate_upsert = (
            self._invoke_with_config(
                duplicate_config,
                onboarding_payload(email="  MAIN@EXAMPLE.COM  "),
            )
        )
        self.assertEqual(duplicate_handler.status, 409)
        self.assertEqual(
            duplicate_handler.response()["error"]["code"],
            "mailbox_already_registered",
        )
        duplicate_connect.assert_not_called()
        duplicate_save.assert_not_called()
        duplicate_upsert.assert_not_called()

    def test_plaintext_and_smtp_fields_are_rejected_before_any_action(self):
        payloads = [onboarding_payload(ssl_enabled=False)]
        smtp_payload = onboarding_payload()
        smtp_payload["connection"]["smtp"] = {
            "host": "smtp.example.com",
            "password": "must-not-store",
        }
        payloads.append(smtp_payload)

        for payload in payloads:
            with self.subTest(payload=payload):
                handler = FakeHandler(payload)
                with patch.object(
                    connect_route,
                    "resolve_authenticated_user",
                    return_value=({"email": "owner@example.com"}, None),
                ), patch.object(
                    connect_route,
                    "resolve_owned_onboarding_custom_imap_target",
                ) as target, patch.object(
                    imap_connect_preview.imaplib,
                    "IMAP4",
                ) as plaintext, patch.object(
                    imap_connect_preview.imaplib,
                    "IMAP4_SSL",
                ) as tls, patch.object(
                    connect_route,
                    "save_mailbox_secret",
                ) as save, patch.object(
                    connect_route,
                    "upsert_owned_custom_imap_mailbox",
                ) as upsert:
                    invoke_connect(handler)

                self.assertEqual(handler.status, 400)
                target.assert_not_called()
                plaintext.assert_not_called()
                tls.assert_not_called()
                save.assert_not_called()
                upsert.assert_not_called()

    def test_secure_authentication_helper_passes_verified_context_without_fallback(self):
        mailbox = Mock()
        with patch.object(
            imap_connect_preview,
            "open_public_imap_connection",
            return_value=mailbox,
        ) as secure_open, patch.object(
            imap_connect_preview.imaplib,
            "IMAP4_SSL",
        ) as raw_tls, patch.object(
            imap_connect_preview.imaplib,
            "IMAP4",
        ) as plaintext:
            status, response = imap_connect_preview.build_secure_imap_authentication_response(
                {
                    "host": "imap.example.com",
                    "port": 993,
                    "ssl": True,
                    "username": "promo@example.com",
                    "password": "test-only-secret",
                }
            )

        self.assertEqual((status, response), (200, {"ok": True}))
        plaintext.assert_not_called()
        raw_tls.assert_not_called()
        secure_open.assert_called_once()
        args, kwargs = secure_open.call_args
        self.assertEqual(args, ("imap.example.com", 993))
        self.assertIs(kwargs["ssl_enabled"], True)
        context = kwargs["ssl_context"]
        self.assertIs(context.check_hostname, True)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(kwargs["timeout"], 30)
        mailbox.login.assert_called_once_with("promo@example.com", "test-only-secret")
        mailbox.logout.assert_called_once()

    def test_certificate_failure_has_safe_error_and_no_plaintext_fallback(self):
        with patch.object(
            imap_connect_preview,
            "open_public_imap_connection",
            side_effect=ssl.SSLCertVerificationError(
                "certificate canary"
            ),
        ) as secure_open, patch.object(
            imap_connect_preview.imaplib,
            "IMAP4_SSL",
        ) as raw_tls, patch.object(
            imap_connect_preview.imaplib,
            "IMAP4",
        ) as plaintext:
            status, response = imap_connect_preview.build_secure_imap_authentication_response(
                {
                    "host": "imap.example.com",
                    "port": 993,
                    "ssl": True,
                    "username": "promo@example.com",
                    "password": "test-only-secret",
                }
            )

        self.assertEqual(status, 502)
        self.assertEqual(response["error"]["code"], "imap_connection_failed")
        self.assertNotIn("certificate canary", json.dumps(response))
        self.assertNotIn("test-only-secret", json.dumps(response))
        secure_open.assert_called_once()
        raw_tls.assert_not_called()
        plaintext.assert_not_called()

    def test_server_writer_rechecks_target_and_preserves_onboarding_exactly(self):
        config = onboarding_config()
        onboarding_before = json.loads(json.dumps(config["onboardingSession"]))
        written = {}

        def capture_write(_store, _owner_email, expected, record):
            written["expected"] = expected
            written["record"] = record
            return {"status": "ok", "record": {"result": "OK"}, "error": None}

        with patch.object(
            user_config_store,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            user_config_store,
            "resolve_user_config_store",
            return_value=({"rest_url": "https://store.invalid", "rest_token": "token"}, None),
        ), patch.object(
            user_config_store,
            "read_user_config_record",
            return_value={"status": "ok", "config": config, "error": None},
        ), patch.object(
            user_config_store,
            "write_user_config_record_if_unchanged",
            side_effect=capture_write,
        ) as write:
            result = user_config_store.upsert_owned_custom_imap_mailbox(
                {},
                "imap-server-owned",
                "initial",
                {
                    "email": " PROMO@EXAMPLE.COM ",
                    "onboardingInboxId": "promo",
                    "customImap": {
                        "host": "imap.example.com",
                        "port": "993",
                        "ssl": True,
                        "username": "promo@example.com",
                        "password": "must-be-stripped",
                    },
                    "customSmtp": {},
                },
                credential_version=CREDENTIAL_VERSION_B,
                expected_inbox=None,
                onboarding_inbox_id="promo",
            )

        self.assertEqual(result["status"], "ok")
        write.assert_called_once()
        self.assertEqual(written["expected"], config)
        stored = written["record"]
        self.assertEqual(stored["onboardingSession"], onboarding_before)
        self.assertIs(stored["onboardingSession"]["completed"], False)
        self.assertEqual(
            stored["onboardingSession"]["choices"],
            config["onboardingSession"]["choices"],
        )
        new_mailbox = stored["managedInboxes"][-1]
        self.assertEqual(new_mailbox["id"], "imap-server-owned")
        self.assertEqual(new_mailbox["email"], "promo@example.com")
        self.assertEqual(new_mailbox["onboardingInboxId"], "promo")
        self.assertEqual(new_mailbox["customSmtp"], {})
        self.assertNotIn("password", json.dumps(new_mailbox).lower())

    def test_server_writer_recheck_blocks_late_position_conflict_without_write(self):
        conflict = {
            "id": "imap-race-winner",
            "email": "winner@example.com",
            "provider": "custom_imap",
            "connected": True,
            "connectionMethod": "imap",
            "connectionStatus": "connected",
            "onboardingInboxId": "promo",
        }
        config = onboarding_config(
            managed_inboxes=[*onboarding_config()["managedInboxes"], conflict]
        )
        with patch.object(
            user_config_store,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            user_config_store,
            "resolve_user_config_store",
            return_value=({"rest_url": "https://store.invalid", "rest_token": "token"}, None),
        ), patch.object(
            user_config_store,
            "read_user_config_record",
            return_value={"status": "ok", "config": config, "error": None},
        ), patch.object(
            user_config_store,
            "write_user_config_record_if_unchanged",
        ) as write:
            result = user_config_store.upsert_owned_custom_imap_mailbox(
                {},
                "imap-server-owned",
                "initial",
                {
                    "email": "promo@example.com",
                    "onboardingInboxId": "promo",
                    "customImap": {
                        "host": "imap.example.com",
                        "port": "993",
                        "ssl": True,
                        "username": "promo@example.com",
                    },
                    "customSmtp": {},
                },
                credential_version=CREDENTIAL_VERSION_B,
                expected_inbox=None,
                onboarding_inbox_id="promo",
            )

        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["error"]["code"], "inbox_position_conflict")
        write.assert_not_called()

    def test_reconnect_of_existing_config_uses_atomic_compare_and_set(self):
        existing_mailbox = onboarding_readback(onboarding_config())["inbox"]
        config = onboarding_config(
            managed_inboxes=[*onboarding_config()["managedInboxes"], existing_mailbox]
        )
        with patch.object(
            user_config_store,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            user_config_store,
            "resolve_user_config_store",
            return_value=({"rest_url": "https://store.invalid", "rest_token": "token"}, None),
        ), patch.object(
            user_config_store,
            "read_user_config_record",
            return_value={"status": "ok", "config": config, "error": None},
        ), patch.object(
            user_config_store,
            "write_user_config_record_if_unchanged",
            return_value={"status": "ok", "record": {"result": "saved"}, "error": None},
        ) as conditional_write, patch.object(
            user_config_store,
            "write_user_config_record",
        ) as blind_write:
            result = user_config_store.upsert_owned_custom_imap_mailbox(
                {},
                "imap-server-owned",
                "reconnect",
                {
                    "email": "promo@example.com",
                    "customImap": {
                        "host": "imap-new.example.com",
                        "port": "993",
                        "ssl": True,
                        "username": "promo@example.com",
                    },
                    "customSmtp": {},
                },
                credential_version=CREDENTIAL_VERSION_B,
                expected_inbox=existing_mailbox,
            )

        self.assertEqual(result["status"], "ok")
        conditional_write.assert_called_once()
        self.assertEqual(conditional_write.call_args.args[2], config)
        self.assertEqual(
            conditional_write.call_args.args[3]["managedInboxes"][-1]["customImap"]["host"],
            "imap-new.example.com",
        )
        blind_write.assert_not_called()

    def test_server_writer_fails_closed_on_cas_race_and_late_incomplete_onboarding(self):
        config = onboarding_config()
        metadata = {
            "email": "promo@example.com",
            "onboardingInboxId": "promo",
            "customImap": {
                "host": "imap.example.com",
                "port": "993",
                "ssl": True,
                "username": "promo@example.com",
            },
            "customSmtp": {},
        }
        store = {"rest_url": "https://store.invalid", "rest_token": "token"}
        with patch.object(
            user_config_store,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            user_config_store,
            "resolve_user_config_store",
            return_value=(store, None),
        ), patch.object(
            user_config_store,
            "read_user_config_record",
            return_value={"status": "ok", "config": config, "error": None},
        ), patch.object(
            user_config_store,
            "write_user_config_record_if_unchanged",
            return_value={
                "status": "conflict",
                "record": None,
                "error": {
                    "code": "user_config_write_conflict",
                    "message": "stale",
                },
            },
        ) as conditional_write, patch.object(
            user_config_store,
            "write_user_config_record",
        ) as blind_write:
            stale = user_config_store.upsert_owned_custom_imap_mailbox(
                {},
                "imap-server-owned",
                "initial",
                metadata,
                credential_version=CREDENTIAL_VERSION_B,
                expected_inbox=None,
                onboarding_inbox_id="promo",
            )

        self.assertEqual(stale["status"], "conflict")
        self.assertEqual(stale["error"]["code"], "user_config_write_conflict")
        self.assertEqual(
            conditional_write.call_count,
            user_config_store.MAX_CUSTOM_IMAP_CONFIG_WRITE_ATTEMPTS,
        )
        blind_write.assert_not_called()

        incomplete = onboarding_config(selected_inboxes=["main"], inbox_count="1")
        with patch.object(
            user_config_store,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            user_config_store,
            "resolve_user_config_store",
            return_value=(store, None),
        ), patch.object(
            user_config_store,
            "read_user_config_record",
            return_value={"status": "ok", "config": incomplete, "error": None},
        ), patch.object(
            user_config_store,
            "write_user_config_record_if_unchanged",
        ) as conditional_write:
            blocked = user_config_store.upsert_owned_custom_imap_mailbox(
                {},
                "settings-client-id",
                "initial",
                {
                    "email": "settings@example.com",
                    "customImap": {},
                    "customSmtp": {},
                },
                credential_version=CREDENTIAL_VERSION_B,
                expected_inbox=None,
                require_completed_onboarding=True,
            )

        self.assertEqual(blocked["status"], "conflict")
        self.assertEqual(blocked["error"]["code"], "onboarding_incomplete")
        conditional_write.assert_not_called()

    def test_authentication_and_tls_failures_leave_no_secret_or_config(self):
        for error_code, expected_code in (
            ("authentication_failed", "authentication_failed"),
            ("tls_connection_failed", "tls_connection_failed"),
        ):
            with self.subTest(error_code=error_code):
                config = onboarding_config()
                handler = FakeHandler(onboarding_payload())
                with patch.object(
                    connect_route,
                    "resolve_authenticated_user",
                    return_value=({"email": "owner@example.com"}, None),
                ), patch.object(
                    connect_route,
                    "resolve_owned_onboarding_custom_imap_target",
                    return_value=onboarding_target(config),
                ), patch.object(
                    imap_connect_preview,
                    "build_secure_imap_authentication_response",
                    return_value=(
                        502,
                        {"ok": False, "error": {"code": error_code, "message": "internal"}},
                    ),
                ), patch.object(
                    connect_route,
                    "snapshot_mailbox_secret_namespace",
                    return_value={"status": "missing", "record": None, "error": None},
                ) as snapshot, patch.object(
                    connect_route,
                    "save_mailbox_secret",
                ) as save, patch.object(
                    connect_route,
                    "upsert_owned_custom_imap_mailbox",
                ) as upsert:
                    invoke_connect(handler)

                self.assertEqual(handler.status, 502)
                self.assertEqual(handler.response()["error"]["code"], expected_code)
                self.assertNotIn("internal", json.dumps(handler.response()))
                self.assertNotIn("one-time-onboarding-imap", json.dumps(handler.response()))
                snapshot.assert_called_once()
                save.assert_not_called()
                upsert.assert_not_called()

    def test_config_failure_removes_new_secret_and_never_stores_smtp(self):
        config = onboarding_config()
        snapshot = {"status": "missing", "record": None, "error": None}
        handler = FakeHandler(onboarding_payload())
        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            connect_route,
            "resolve_owned_onboarding_custom_imap_target",
            return_value=onboarding_target(config),
        ), patch.object(
            connect_route,
            "_prepare_server_mailbox_id",
            return_value=("imap-server-owned", snapshot, "m" * 43, None),
        ), patch.object(
            imap_connect_preview,
            "build_secure_imap_authentication_response",
            return_value=(200, {"ok": True}),
        ), patch.object(
            connect_route,
            "generate_mailbox_credential_version",
            return_value=CREDENTIAL_VERSION_B,
        ), patch.object(
            connect_route,
            "snapshot_encrypted_mailbox_secret",
            return_value=snapshot,
        ), patch.object(
            connect_route,
            "save_mailbox_secret",
            return_value=({"imapPassword": "stored", "smtpPassword": ""}, None),
        ) as save, patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
            return_value={"status": "unavailable", "error": {"code": "offline"}},
        ), patch.object(
            connect_route,
            "read_mailbox_secret",
            return_value={"status": "missing", "record": None, "error": None},
        ), patch.object(
            connect_route,
            "rollback_owned_custom_imap_mailbox_update",
            return_value=None,
        ) as restore_config, patch.object(
            connect_route,
            "restore_encrypted_mailbox_secret_snapshot",
            return_value=None,
        ) as restore:
            invoke_connect(handler)

        self.assertEqual(handler.status, 503)
        save.assert_called_once_with(
            "owner@example.com",
            "imap-server-owned",
            imap_password="one-time-onboarding-imap",
            smtp_password="",
            credential_version=CREDENTIAL_VERSION_B,
            expected_snapshot=snapshot,
            require_namespace_missing=True,
        )
        restore.assert_called_once_with(
            "owner@example.com",
            "imap-server-owned",
            snapshot,
            expected_credential_version=CREDENTIAL_VERSION_B,
        )
        restore_config.assert_called_once_with(
            handler.headers,
            "imap-server-owned",
            onboarding_readback(
                config,
                credential_version=CREDENTIAL_VERSION_B,
            )["inbox"],
            None,
        )
        self.assertNotIn("one-time-onboarding-imap", json.dumps(handler.response()))

    def test_config_rollback_conflict_preserves_a_newer_secret(self):
        config = onboarding_config()
        snapshot = {"status": "missing", "record": None, "error": None}
        handler = FakeHandler(onboarding_payload())
        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            connect_route,
            "resolve_owned_onboarding_custom_imap_target",
            return_value=onboarding_target(config),
        ), patch.object(
            connect_route,
            "_prepare_server_mailbox_id",
            return_value=("imap-server-owned", snapshot, "m" * 43, None),
        ), patch.object(
            imap_connect_preview,
            "build_secure_imap_authentication_response",
            return_value=(200, {"ok": True}),
        ), patch.object(
            connect_route,
            "generate_mailbox_credential_version",
            return_value=CREDENTIAL_VERSION_B,
        ), patch.object(
            connect_route,
            "save_mailbox_secret",
            return_value=({"imapPassword": "stored", "smtpPassword": ""}, None),
        ), patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
            return_value={"status": "unavailable", "error": {"code": "offline"}},
        ), patch.object(
            connect_route,
            "read_mailbox_secret",
            return_value={"status": "missing", "record": None, "error": None},
        ), patch.object(
            connect_route,
            "rollback_owned_custom_imap_mailbox_update",
            return_value={
                "code": "user_config_write_conflict",
                "message": "newer mailbox fingerprint",
            },
        ) as restore_config, patch.object(
            connect_route,
            "restore_encrypted_mailbox_secret_snapshot",
        ) as restore_secret:
            invoke_connect(handler)

        self.assertEqual(handler.status, 409)
        self.assertEqual(
            handler.response()["error"]["code"],
            "mailbox_connection_conflict",
        )
        restore_config.assert_called_once()
        restore_secret.assert_not_called()

    def test_failed_exact_readback_restores_config_and_secret_snapshots(self):
        config = onboarding_config()
        snapshot = {"status": "missing", "record": None, "error": None}
        handler = FakeHandler(onboarding_payload())
        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            connect_route,
            "resolve_owned_onboarding_custom_imap_target",
            return_value=onboarding_target(config),
        ), patch.object(
            connect_route,
            "_prepare_server_mailbox_id",
            return_value=("imap-server-owned", snapshot, "m" * 43, None),
        ), patch.object(
            imap_connect_preview,
            "build_secure_imap_authentication_response",
            return_value=(200, {"ok": True}),
        ), patch.object(
            connect_route,
            "generate_mailbox_credential_version",
            return_value=CREDENTIAL_VERSION_B,
        ), patch.object(
            connect_route,
            "snapshot_encrypted_mailbox_secret",
            return_value=snapshot,
        ), patch.object(
            connect_route,
            "save_mailbox_secret",
            return_value=({"imapPassword": "stored", "smtpPassword": ""}, None),
        ), patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
            return_value={"status": "ok", "error": None},
        ), patch.object(
            connect_route,
            "resolve_owned_managed_inbox_record",
            return_value={"status": "not_found", "inbox": None, "config": config},
        ), patch.object(
            connect_route,
            "read_mailbox_secret",
            return_value={"status": "missing", "record": None, "error": None},
        ), patch.object(
            connect_route,
            "rollback_owned_custom_imap_mailbox_update",
            return_value=None,
        ) as restore_config, patch.object(
            connect_route,
            "restore_encrypted_mailbox_secret_snapshot",
            return_value=None,
        ) as restore_secret:
            invoke_connect(handler)

        self.assertEqual(handler.status, 503)
        self.assertEqual(
            handler.response()["error"]["code"],
            "configuration_persistence_failed",
        )
        restore_config.assert_called_once_with(
            handler.headers,
            "imap-server-owned",
            onboarding_readback(
                config,
                credential_version=CREDENTIAL_VERSION_B,
            )["inbox"],
            None,
        )
        restore_secret.assert_called_once_with(
            "owner@example.com",
            "imap-server-owned",
            snapshot,
            expected_credential_version=CREDENTIAL_VERSION_B,
        )

    def test_incomplete_onboarding_blocks_legacy_initial_mode_before_any_action(self):
        handler = FakeHandler(initial_payload("browser-chosen-id"))
        events = []

        def acquire_lease(owner_email, mailbox_id):
            self.assertEqual(
                (owner_email, mailbox_id),
                ("owner@example.com", "browser-chosen-id"),
            )
            events.append("lease_acquired")
            return {"status": "acquired", "token": "l" * 43, "error": None}

        def resolve_initial_authority(_headers):
            events.append("authority_read")
            return {
                "status": "conflict",
                "user": {"email": "owner@example.com"},
                "inbox": None,
                "config": onboarding_config(
                    selected_inboxes=["main"],
                    inbox_count="1",
                ),
                "error": {
                    "code": "onboarding_incomplete",
                    "message": "authoritative onboarding required",
                },
            }

        def release_lease(owner_email, mailbox_id, token):
            self.assertEqual(
                (owner_email, mailbox_id, token),
                ("owner@example.com", "browser-chosen-id", "l" * 43),
            )
            events.append("lease_released")
            return {"status": "released", "token": token, "error": None}

        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            connect_route,
            "resolve_owned_initial_imap_registration",
            side_effect=resolve_initial_authority,
        ), patch.object(
            connect_route,
            "acquire_mailbox_mutation_lease",
            side_effect=acquire_lease,
        ), patch.object(
            connect_route,
            "release_mailbox_mutation_lease",
            side_effect=release_lease,
        ), patch.object(
            connect_route,
            "resolve_owned_managed_inbox_record",
        ) as target, patch.object(
            imap_connect_preview,
            "build_connect_preview_response",
        ) as preview, patch.object(
            connect_route,
            "snapshot_encrypted_mailbox_secret",
        ) as snapshot, patch.object(
            connect_route,
            "save_mailbox_secret",
        ) as save, patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
        ) as upsert:
            invoke_connect(handler, use_real_lease=True)

        self.assertEqual(handler.status, 400)
        self.assertEqual(handler.response()["error"]["code"], "forbidden_client_authority")
        self.assertEqual(events, ["lease_acquired", "authority_read", "lease_released"])
        target.assert_not_called()
        preview.assert_not_called()
        snapshot.assert_not_called()
        save.assert_not_called()
        upsert.assert_not_called()

    def test_general_initial_authority_helper_allows_only_completed_onboarding(self):
        user = {"email": "owner@example.com"}
        for completed, expected_status, expected_code in (
            (False, "conflict", "onboarding_incomplete"),
            (True, "ok", None),
        ):
            with self.subTest(completed=completed), patch.object(
                user_config_store,
                "read_user_config_for_authenticated_user",
                return_value=(
                    user,
                    {
                        "status": "ok",
                        "config": onboarding_config(completed=completed),
                        "error": None,
                    },
                ),
            ):
                result = user_config_store.resolve_owned_initial_imap_registration({})

            self.assertEqual(result["status"], expected_status)
            self.assertEqual(
                (result.get("error") or {}).get("code"),
                expected_code,
            )

    def test_canonical_onboarding_classifier_rejects_malformed_known_choices(self):
        for field, value in (
            ("inboxCount", {"connected": True}),
            ("focusPreferences", {"password": "must-not-authorize"}),
            ("primaryRole", "unknown-role"),
        ):
            with self.subTest(field=field):
                config = onboarding_config()
                config["onboardingSession"]["choices"][field] = value
                handler, secure_connect, snapshot, save, upsert = self._invoke_with_config(
                    config
                )
                self.assertEqual(handler.status, 500)
                self.assertEqual(
                    handler.response()["error"]["code"],
                    "mailbox_configuration_malformed",
                )
                self.assertNotIn("must-not-authorize", json.dumps(handler.response()))
                secure_connect.assert_not_called()
                snapshot.assert_not_called()
                save.assert_not_called()
                upsert.assert_not_called()

    def test_generated_id_rejects_existing_credential_namespace_before_imap(self):
        config = onboarding_config()
        generated = [Mock(hex=f"{index:032x}") for index in range(16)]
        handler = FakeHandler(onboarding_payload())
        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            connect_route,
            "resolve_owned_onboarding_custom_imap_target",
            return_value=onboarding_target(config),
        ), patch.object(
            connect_route.uuid,
            "uuid4",
            side_effect=generated,
        ), patch.object(
            connect_route,
            "snapshot_mailbox_secret_namespace",
            return_value={
                "status": "present",
                "record": None,
                "error": None,
            },
        ) as snapshot, patch.object(
            imap_connect_preview,
            "build_secure_imap_authentication_response",
        ) as secure_connect, patch.object(
            connect_route,
            "save_mailbox_secret",
        ) as save, patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
        ) as upsert:
            invoke_connect(handler)

        self.assertEqual(handler.status, 503)
        self.assertEqual(handler.response()["error"]["code"], "mailbox_id_generation_failed")
        self.assertEqual(snapshot.call_count, 16)
        secure_connect.assert_not_called()
        save.assert_not_called()
        upsert.assert_not_called()

    def test_onboarding_write_uses_atomic_compare_and_set(self):
        store = {"rest_url": "https://store.invalid", "rest_token": "token"}
        expected = onboarding_config()
        replacement = {
            **expected,
            "managedInboxes": [
                *expected["managedInboxes"],
                {"id": "imap-server-owned", "email": "promo@example.com"},
            ],
        }
        with patch.object(
            user_config_store,
            "_perform_rest_request",
            return_value=({"result": "saved"}, None),
        ) as request:
            result = user_config_store.write_user_config_record_if_unchanged(
                store,
                "owner@example.com",
                expected,
                replacement,
            )

        self.assertEqual(result["status"], "ok")
        args, kwargs = request.call_args
        self.assertEqual(args[:3], (store, "POST", ""))
        command = json.loads(kwargs["body"])
        self.assertEqual(command[0], "EVAL")
        self.assertEqual(command[2], 1)
        self.assertEqual(
            command[3],
            user_config_store.build_user_config_key("owner@example.com"),
        )
        self.assertEqual(json.loads(command[4]), expected)
        self.assertEqual(json.loads(command[5]), replacement)

        with patch.object(
            user_config_store,
            "_perform_rest_request",
            return_value=({"result": "stale"}, None),
        ):
            stale = user_config_store.write_user_config_record_if_unchanged(
                store,
                "owner@example.com",
                expected,
                replacement,
            )
        self.assertEqual(stale["status"], "conflict")
        self.assertEqual(stale["error"]["code"], "user_config_write_conflict")

        with patch.object(
            user_config_store,
            "_perform_rest_request",
            return_value=({"result": "saved"}, None),
        ) as create_request:
            created = user_config_store.write_user_config_record_if_missing(
                store,
                "owner@example.com",
                replacement,
            )
        self.assertEqual(created["status"], "ok")
        create_command = json.loads(create_request.call_args.kwargs["body"])
        self.assertEqual(create_command[0], "EVAL")
        self.assertEqual(
            create_command[3],
            user_config_store.build_user_config_key("owner@example.com"),
        )
        self.assertEqual(json.loads(create_command[4]), replacement)

        with patch.object(
            user_config_store,
            "_perform_rest_request",
            return_value=({"result": "exists"}, None),
        ):
            raced = user_config_store.write_user_config_record_if_missing(
                store,
                "owner@example.com",
                replacement,
            )
        self.assertEqual(raced["status"], "conflict")
        self.assertEqual(raced["error"]["code"], "user_config_write_conflict")

    def test_atomic_rollback_removes_only_its_mailbox_and_preserves_concurrent_changes(self):
        baseline = onboarding_config()
        target_mailbox = onboarding_readback(baseline)["inbox"]
        concurrent_mailbox = {
            "id": "imap-concurrent",
            "email": "legal@example.com",
            "provider": "custom_imap",
            "connected": True,
            "connectionMethod": "imap",
            "connectionStatus": "connected",
            "onboardingInboxId": "legal",
        }
        current = json.loads(json.dumps(baseline))
        current["onboardingSession"]["choices"]["inboxCount"] = "3"
        current["onboardingSession"]["choices"]["selectedInboxes"].append("legal")
        current["managedInboxes"].extend([target_mailbox, concurrent_mailbox])
        captured = {}

        def capture_cas(_store, _owner, expected, replacement):
            captured["expected"] = expected
            captured["replacement"] = replacement
            return {"status": "ok", "record": replacement, "error": None}

        with patch.object(
            user_config_store,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            user_config_store,
            "resolve_user_config_store",
            return_value=({"rest_url": "https://store.invalid", "rest_token": "token"}, None),
        ), patch.object(
            user_config_store,
            "read_user_config_record",
            return_value={"status": "ok", "config": current, "error": None},
        ), patch.object(
            user_config_store,
            "write_user_config_record_if_unchanged",
            side_effect=capture_cas,
        ) as conditional_write, patch.object(
            user_config_store,
            "write_user_config_record",
        ) as blind_write:
            error = user_config_store.rollback_owned_onboarding_imap_registration(
                {},
                "imap-server-owned",
                target_mailbox,
                baseline,
            )

        self.assertIsNone(error)
        conditional_write.assert_called_once()
        blind_write.assert_not_called()
        self.assertEqual(captured["expected"], current)
        replacement = captured["replacement"]
        self.assertEqual(
            replacement["onboardingSession"]["choices"]["selectedInboxes"],
            ["main", "promo", "legal"],
        )
        self.assertEqual(replacement["onboardingSession"]["choices"]["inboxCount"], "3")
        self.assertEqual(
            [mailbox["id"] for mailbox in replacement["managedInboxes"]],
            ["gmail-main", "imap-concurrent"],
        )

    def test_atomic_rollback_refuses_a_mutated_same_id_mailbox(self):
        baseline = onboarding_config()
        expected_mailbox = onboarding_readback(baseline)["inbox"]
        mutated_mailbox = json.loads(json.dumps(expected_mailbox))
        mutated_mailbox["connected"] = 1
        current = json.loads(json.dumps(baseline))
        current["managedInboxes"].append(mutated_mailbox)

        with patch.object(
            user_config_store,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            user_config_store,
            "resolve_user_config_store",
            return_value=({"rest_url": "https://store.invalid", "rest_token": "token"}, None),
        ), patch.object(
            user_config_store,
            "read_user_config_record",
            return_value={"status": "ok", "config": current, "error": None},
        ), patch.object(
            user_config_store,
            "write_user_config_record_if_unchanged",
        ) as conditional_write:
            error = user_config_store.rollback_owned_onboarding_imap_registration(
                {},
                "imap-server-owned",
                expected_mailbox,
                baseline,
            )

        self.assertEqual(error["code"], "user_config_write_conflict")
        conditional_write.assert_not_called()
        self.assertEqual(current["managedInboxes"][-1], mutated_mailbox)


class OnboardingCapabilityUpgradeTests(unittest.TestCase):
    def _run_partial_upgrade(
        self,
        *,
        use_same_credentials,
        legacy_smtp_placeholder=False,
        smtp_result=(200, {"ok": True}),
    ):
        base_config = onboarding_config()
        target = partial_onboarding_target(
            base_config,
            legacy_smtp_placeholder=legacy_smtp_placeholder,
        )
        existing = target["inbox"]
        payload = onboarding_payload()
        payload["serverMailboxId"] = existing["id"]
        payload["connection"].pop("imap")
        if use_same_credentials:
            smtp_username = ""
            explicit_smtp_password = None
            stored_smtp_password = ""
        else:
            smtp_username = "outgoing@example.com"
            explicit_smtp_password = "one-time-outgoing-secret"
            stored_smtp_password = explicit_smtp_password
            payload["connection"]["smtp"].update(
                {
                    "username": smtp_username,
                    "password": explicit_smtp_password,
                    "useSameCredentials": False,
                }
            )

        full_mailbox = {
            **existing,
            "credentialVersion": CREDENTIAL_VERSION_B,
            "customSmtp": {
                "host": "smtp.example.com",
                "port": "587",
                "security": "starttls",
                "username": smtp_username,
                "useSameCredentials": use_same_credentials,
            },
            "imapConnectionStatus": "connected",
            "smtpConnectionStatus": "connected",
            "fullyConnected": True,
        }
        full_config = {
            **target["config"],
            "managedInboxes": [
                full_mailbox if inbox.get("id") == existing["id"] else inbox
                for inbox in target["config"]["managedInboxes"]
            ],
        }
        encrypted_snapshot = {
            "status": "present",
            "record": {"ciphertext": "opaque-existing-secret"},
            "error": None,
        }
        existing_secret = {
            "status": "present",
            "record": {
                "credentialVersion": CREDENTIAL_VERSION_A,
                "imapPassword": "stored-imap-secret",
                "smtpPassword": "",
            },
            "error": None,
        }
        updated_secret = {
            "status": "present",
            "record": {
                "credentialVersion": CREDENTIAL_VERSION_B,
                "imapPassword": "stored-imap-secret",
                "smtpPassword": stored_smtp_password,
            },
            "error": None,
        }
        handler = FakeHandler(payload)
        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            connect_route,
            "resolve_owned_onboarding_custom_imap_target",
            side_effect=[target, target],
        ) as resolve_target, patch.object(
            connect_route,
            "snapshot_encrypted_mailbox_secret",
            return_value=encrypted_snapshot,
        ) as encrypted_read, patch.object(
            connect_route,
            "read_mailbox_secret",
            side_effect=[existing_secret, updated_secret],
        ) as secret_read, patch.object(
            connect_route,
            "generate_mailbox_credential_version",
            return_value=CREDENTIAL_VERSION_B,
        ), patch.object(
            imap_connect_preview,
            "build_secure_imap_authentication_response",
        ) as imap_auth, patch.object(
            connect_route,
            "save_mailbox_secret",
            return_value=(updated_secret["record"], None),
        ) as save_secret, patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
            return_value={"status": "ok", "error": None},
        ) as upsert, patch.object(
            connect_route,
            "resolve_owned_managed_inbox_record",
            return_value={
                "status": "ok",
                "user": {"email": "owner@example.com"},
                "inbox": full_mailbox,
                "config": full_config,
                "error": None,
            },
        ) as config_readback, patch.object(
            connect_route,
            "rollback_owned_custom_imap_mailbox_update",
        ) as config_rollback, patch.object(
            connect_route,
            "restore_encrypted_mailbox_secret_snapshot",
        ) as secret_rollback:
            smtp_test = invoke_connect(handler, smtp_result=smtp_result)

        return {
            "handler": handler,
            "payload": payload,
            "target": target,
            "existing": existing,
            "full": full_mailbox,
            "encrypted_snapshot": encrypted_snapshot,
            "stored_smtp_password": stored_smtp_password,
            "resolve_target": resolve_target,
            "encrypted_read": encrypted_read,
            "secret_read": secret_read,
            "imap_auth": imap_auth,
            "smtp_test": smtp_test,
            "save_secret": save_secret,
            "upsert": upsert,
            "config_readback": config_readback,
            "config_rollback": config_rollback,
            "secret_rollback": secret_rollback,
            "explicit_smtp_password": explicit_smtp_password,
        }

    def test_existing_legacy_partial_reuses_imap_secret_and_same_mailbox_id(self):
        result = self._run_partial_upgrade(
            use_same_credentials=True,
            legacy_smtp_placeholder=True,
        )

        self.assertEqual(result["handler"].status, 200)
        self.assertEqual(result["handler"].response(), {"ok": True})
        self.assertNotIn(
            "password",
            json.dumps(result["handler"].response()).casefold(),
        )
        self.assertNotIn("imap", result["payload"]["connection"])
        self.assertEqual(result["full"]["id"], result["existing"]["id"])
        self.assertEqual(
            [
                inbox["id"]
                for inbox in result["target"]["config"]["managedInboxes"]
                if inbox["id"] == result["existing"]["id"]
            ],
            [result["existing"]["id"]],
        )
        result["resolve_target"].assert_has_calls(
            [
                unittest.mock.call(
                    result["handler"].headers,
                    "promo",
                    "promo@example.com",
                    result["existing"]["id"],
                ),
                unittest.mock.call(
                    result["handler"].headers,
                    "promo",
                    "promo@example.com",
                    result["existing"]["id"],
                ),
            ]
        )
        result["imap_auth"].assert_not_called()
        result["smtp_test"].assert_called_once_with(
            {
                "host": "smtp.example.com",
                "port": 587,
                "security": "starttls",
                "username": "promo@example.com",
                "password": "stored-imap-secret",
            }
        )
        result["save_secret"].assert_called_once_with(
            "owner@example.com",
            result["existing"]["id"],
            imap_password="stored-imap-secret",
            smtp_password="",
            credential_version=CREDENTIAL_VERSION_B,
            expected_snapshot=result["encrypted_snapshot"],
            require_namespace_missing=False,
        )
        result["upsert"].assert_called_once()
        self.assertEqual(result["upsert"].call_args.args[1], result["existing"]["id"])
        self.assertEqual(result["upsert"].call_args.args[2], "reconnect")
        self.assertEqual(
            result["upsert"].call_args.kwargs["expected_inbox"],
            result["existing"],
        )
        result["config_rollback"].assert_not_called()
        result["secret_rollback"].assert_not_called()

    def test_new_onboarding_mailbox_requires_smtp_in_the_same_request(self):
        payload = onboarding_payload()
        payload["connection"].pop("smtp")
        handler = FakeHandler(payload)
        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            connect_route,
            "resolve_owned_onboarding_custom_imap_target",
        ) as resolve_target, patch.object(
            imap_connect_preview,
            "build_secure_imap_authentication_response",
        ) as imap_auth, patch.object(
            connect_route,
            "save_mailbox_secret",
        ) as save_secret:
            smtp_test = invoke_connect(handler)

        self.assertEqual(handler.status, 400)
        self.assertEqual(handler.response()["error"]["code"], "invalid_request")
        resolve_target.assert_not_called()
        imap_auth.assert_not_called()
        smtp_test.assert_not_called()
        save_secret.assert_not_called()

    def test_existing_partial_supports_explicit_separate_smtp_credentials(self):
        result = self._run_partial_upgrade(use_same_credentials=False)

        self.assertEqual(result["handler"].status, 200)
        result["imap_auth"].assert_not_called()
        result["smtp_test"].assert_called_once_with(
            {
                "host": "smtp.example.com",
                "port": 587,
                "security": "starttls",
                "username": "outgoing@example.com",
                "password": "one-time-outgoing-secret",
            }
        )
        result["save_secret"].assert_called_once_with(
            "owner@example.com",
            result["existing"]["id"],
            imap_password="stored-imap-secret",
            smtp_password="one-time-outgoing-secret",
            credential_version=CREDENTIAL_VERSION_B,
            expected_snapshot=result["encrypted_snapshot"],
            require_namespace_missing=False,
        )

    def test_partial_smtp_auth_failure_preserves_incoming_state_without_writes(self):
        result = self._run_partial_upgrade(
            use_same_credentials=True,
            smtp_result=(
                401,
                {
                    "ok": False,
                    "error": {"code": "smtp_authentication_failed"},
                },
            ),
        )

        self.assertEqual(result["handler"].status, 502)
        self.assertEqual(
            result["handler"].response()["error"]["code"],
            "smtp_authentication_failed",
        )
        self.assertIs(result["existing"]["connected"], True)
        self.assertEqual(
            result["existing"]["imapConnectionStatus"],
            "connected",
        )
        self.assertIs(result["existing"]["fullyConnected"], False)
        result["save_secret"].assert_not_called()
        result["upsert"].assert_not_called()
        result["config_readback"].assert_not_called()
        result["config_rollback"].assert_not_called()
        result["secret_rollback"].assert_not_called()

    def test_selector_mismatch_stops_before_imap_smtp_or_secret_access(self):
        payload = onboarding_payload()
        payload["serverMailboxId"] = "imap-client-selector"
        payload["connection"].pop("imap")
        handler = FakeHandler(payload)
        target = {
            "status": "conflict",
            "user": {"email": "owner@example.com"},
            "inbox": None,
            "config": onboarding_config(),
            "error": {
                "code": "mailbox_id_conflict",
                "message": "selector mismatch",
            },
        }
        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            connect_route,
            "resolve_owned_onboarding_custom_imap_target",
            return_value=target,
        ) as resolve_target, patch.object(
            imap_connect_preview,
            "build_secure_imap_authentication_response",
        ) as imap_auth, patch.object(
            connect_route,
            "snapshot_encrypted_mailbox_secret",
        ) as secret_snapshot, patch.object(
            connect_route,
            "save_mailbox_secret",
        ) as save_secret, patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
        ) as upsert:
            smtp_test = invoke_connect(handler)

        self.assertEqual(handler.status, 409)
        self.assertEqual(
            handler.response()["error"]["code"],
            "mailbox_registration_conflict",
        )
        resolve_target.assert_called_once_with(
            handler.headers,
            "promo",
            "promo@example.com",
            "imap-client-selector",
        )
        imap_auth.assert_not_called()
        smtp_test.assert_not_called()
        secret_snapshot.assert_not_called()
        save_secret.assert_not_called()
        upsert.assert_not_called()

    def test_new_mailbox_smtp_failure_creates_no_record_or_secret(self):
        config = onboarding_config()
        handler = FakeHandler(onboarding_payload())
        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            connect_route,
            "resolve_owned_onboarding_custom_imap_target",
            return_value=onboarding_target(config),
        ), patch.object(
            connect_route,
            "_prepare_server_mailbox_id",
            return_value=(
                "imap-server-owned",
                {"status": "missing", "record": None, "error": None},
                "m" * 43,
                None,
            ),
        ), patch.object(
            imap_connect_preview,
            "build_secure_imap_authentication_response",
            return_value=(200, {"ok": True}),
        ) as imap_auth, patch.object(
            connect_route,
            "save_mailbox_secret",
        ) as save_secret, patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
        ) as upsert:
            smtp_test = invoke_connect(
                handler,
                smtp_result=(
                    502,
                    {"ok": False, "error": {"code": "smtp_tls_failed"}},
                ),
            )

        self.assertEqual(handler.status, 502)
        self.assertEqual(
            handler.response()["error"]["code"],
            "smtp_tls_failed",
        )
        imap_auth.assert_called_once()
        smtp_test.assert_called_once()
        save_secret.assert_not_called()
        upsert.assert_not_called()
        self.assertEqual(config["managedInboxes"], onboarding_config()["managedInboxes"])


class ExistingMailboxOperationTests(unittest.TestCase):
    def test_action_and_attachment_reject_browser_connection_fields(self):
        for route, payload in (
            (
                action_route,
                {"mailboxId": "demo", "folder": "INBOX", "uid": "1", "action": "mark_read", "host": "evil"},
            ),
            (
                attachment_route,
                {"mailboxId": "demo", "folder": "INBOX", "uid": "1", "attachmentId": "part-2", "password": "evil"},
            ),
        ):
            with self.subTest(route=route.__name__):
                handler = FakeHandler(payload)
                with patch.object(route, "resolve_authenticated_imap_mailbox") as resolver:
                    route.handler.do_POST(handler)
                self.assertEqual(handler.status, 400)
                self.assertEqual(handler.response()["error"]["code"], "forbidden_connection_fields")
                resolver.assert_not_called()

    def test_imap_action_uses_owned_credentials_and_uid_store(self):
        mailbox = Mock()
        mailbox.select.return_value = ("OK", [])
        mailbox.response.return_value = ("UIDVALIDITY", [b"456"])
        mailbox.uid.return_value = ("OK", [])
        handler = FakeHandler(
            {
                "mailboxId": "demo",
                "folder": "INBOX",
                "uid": "123",
                "uidValidity": "456",
                "action": "mark_read",
            }
        )
        with patch.object(action_route, "resolve_authenticated_imap_mailbox", return_value=resolved_mailbox()), patch.object(
            action_route,
            "resolve_owned_mailbox",
            return_value={"status": "ok", "inbox": {"provider": "custom_imap"}},
        ), patch.object(
            action_route,
            "connect_mailbox_with_settings",
            return_value=mailbox,
        ) as connect:
            action_route.handler.do_POST(handler)

        self.assertEqual(handler.status, 200)
        connect.assert_called_once_with(
            host="imap.example.com",
            port=993,
            username="imap-user",
            password="imap-secret",
            ssl_enabled=True,
        )
        mailbox.uid.assert_called_once_with("store", "123", "+FLAGS.SILENT", "(\\Seen)")

    def test_gmail_action_dispatch_remains_on_existing_branch(self):
        handler = FakeHandler(
            {
                "mailboxId": "gmail-1",
                "messageId": "message-1",
                "action": "star",
            }
        )
        with patch.object(action_route, "_perform_gmail_action") as gmail_action, patch.object(
            action_route,
            "resolve_owned_mailbox",
            return_value={"status": "ok", "inbox": {"provider": "google"}},
        ), patch.object(
            action_route,
            "resolve_gmail_context",
            return_value={"status": "ok", "context": {"access_token": "mock", "refresh_attempted": False}},
        ), patch.object(
            action_route,
            "resolve_authenticated_imap_mailbox",
        ) as imap_resolver:
            action_route.handler.do_POST(handler)
        gmail_action.assert_called_once_with(handler, ANY, "star", ANY)
        imap_resolver.assert_not_called()

    def test_custom_smtp_uses_server_derived_from_transport_and_password(self):
        handler = FakeHandler(
            {
                "mailboxId": "demo",
                "to": "recipient@example.com",
                "subject": "Subject",
                "bodyHtml": "<p>Body</p>",
                "bodyText": "Body",
                "attachments": [],
            }
        )
        with patch.object(send_route, "resolve_authenticated_imap_mailbox", return_value=resolved_mailbox()), patch.object(
            send_route,
            "resolve_owned_mailbox",
            return_value={"status": "ok", "inbox": {"provider": "custom_imap"}},
        ), patch.object(
            send_route,
            "send_public_smtp_message",
        ) as safe_smtp_send:
            send_route.handler.do_POST(handler)

        self.assertEqual(handler.status, 200)
        safe_smtp_send.assert_called_once()
        send_arguments = safe_smtp_send.call_args
        self.assertEqual(
            send_arguments.args[:5],
            (
                "smtp.example.com",
                587,
                "starttls",
                "smtp-user",
                "smtp-secret",
            ),
        )
        self.assertEqual(send_arguments.args[6], ["recipient@example.com"])
        self.assertEqual(send_arguments.kwargs, {"timeout": 30})
        sent_message = send_arguments.args[5]
        self.assertEqual(sent_message["From"], "demo@example.com")

    def test_gmail_send_dispatch_does_not_use_custom_smtp_transport(self):
        handler = FakeHandler(
            {
                "mailboxId": "gmail-1",
                "to": "recipient@example.com",
                "subject": "Subject",
                "bodyHtml": "<p>Body</p>",
                "bodyText": "Body",
                "attachments": [],
            }
        )
        gmail_context = {
            "mailbox_email": "owner@example.com",
            "access_token": "google-token",
        }
        with patch.object(
            send_route,
            "resolve_owned_mailbox",
            return_value={"status": "ok", "inbox": {"provider": "google"}},
        ), patch.object(
            send_route,
            "resolve_gmail_context",
            return_value={"status": "ok", "context": gmail_context},
        ), patch.object(
            send_route,
            "_send_with_gmail_oauth",
            return_value=(True, None, None),
        ) as gmail_send, patch.object(
            send_route,
            "send_public_smtp_message",
        ) as custom_smtp_send:
            send_route.handler.do_POST(handler)

        self.assertEqual(handler.status, 200)
        gmail_send.assert_called_once()
        self.assertIs(gmail_send.call_args.args[0], gmail_context)
        self.assertEqual(
            gmail_send.call_args.args[1]["From"],
            "owner@example.com",
        )
        custom_smtp_send.assert_not_called()

    def test_custom_smtp_rejects_from_and_password_overrides(self):
        for forbidden in ({"from": "attacker@example.com"}, {"password": "evil"}):
            handler = FakeHandler(
                {
                    "mailboxId": "demo",
                    "to": "recipient@example.com",
                    "subject": "Subject",
                    "bodyHtml": "",
                    "bodyText": "Body",
                    **forbidden,
                }
            )
            with patch.object(send_route, "resolve_authenticated_imap_mailbox") as resolver:
                send_route.handler.do_POST(handler)
            self.assertEqual(handler.status, 400)
            resolver.assert_not_called()


class ChangedScopeGuardTests(unittest.TestCase):
    def test_gmail_thread_hydration_sources_are_unchanged_from_head(self):
        result = subprocess.run(
            [
                "git",
                "diff",
                "--quiet",
                "HEAD",
                "--",
                "frontend/api/inboxes/gmail_thread_parser.py",
                "frontend/src/lib/inboxConnectionApi.test.ts",
            ],
            cwd=FRONTEND_DIR.parent,
            check=False,
        )
        self.assertEqual(result.returncode, 0)


class CredentialGenerationRaceTests(unittest.TestCase):
    def test_stale_initial_registration_loser_never_writes_config_or_cleans_winner(self):
        secret_records = {}
        create_attempts = []

        def clone(value):
            return json.loads(json.dumps(value))

        def read_secret(_config, key):
            return clone(secret_records[key]) if key in secret_records else None, None

        def create_secret(_config, encrypted_key, legacy_key, replacement):
            decrypted, decrypt_error = mailbox_secret_store._decrypt_secret_record(
                b"k" * 32,
                "owner@example.com",
                "demo",
                replacement,
            )
            self.assertIsNone(decrypt_error)
            create_attempts.append(decrypted["credentialVersion"])
            if encrypted_key in secret_records or legacy_key in secret_records:
                return 0, None
            secret_records[encrypted_key] = clone(replacement)
            return 1, None

        encryption_key = (
            base64.urlsafe_b64encode(b"k" * 32).decode("ascii").rstrip("=")
        )
        user = {"email": "owner@example.com"}
        with patch.dict(
            os.environ,
            {
                mailbox_secret_store.MAILBOX_SECRET_ENCRYPTION_KEY_ENV: encryption_key
            },
            clear=False,
        ), patch.object(
            mailbox_secret_store,
            "_resolve_durable_store_config",
            return_value={"configured": True},
        ), patch.object(
            mailbox_secret_store,
            "_read_durable_record",
            side_effect=read_secret,
        ), patch.object(
            mailbox_secret_store,
            "_perform_create_secret_namespace_if_missing",
            side_effect=create_secret,
        ):
            winner = mailbox_secret_store.create_mailbox_secret_if_missing(
                "owner@example.com",
                "demo",
                CREDENTIAL_VERSION_A,
                imap_password="winner",
                smtp_password="winner",
            )
            self.assertEqual(winner["status"], "applied")

            handler = FakeHandler(initial_payload())
            with patch.object(
                connect_route,
                "resolve_authenticated_user",
                return_value=(user, None),
            ), patch.object(
                connect_route,
                "resolve_owned_initial_imap_registration",
                return_value={"status": "ok", "error": None},
            ), patch.object(
                connect_route,
                "resolve_owned_managed_inbox_record",
                return_value=missing_connection_target(),
            ), patch.object(
                imap_connect_preview,
                "build_connect_preview_response",
                return_value=(200, {"ok": True, "messages": []}),
            ), patch.object(
                connect_route,
                "snapshot_mailbox_secret_namespace",
                return_value={"status": "missing", "record": None, "error": None},
            ), patch.object(
                connect_route,
                "generate_mailbox_credential_version",
                return_value=CREDENTIAL_VERSION_B,
            ), patch.object(
                connect_route,
                "upsert_owned_custom_imap_mailbox",
            ) as upsert, patch.object(
                connect_route,
                "rollback_owned_custom_imap_mailbox_update",
            ) as config_rollback, patch.object(
                connect_route,
                "restore_encrypted_mailbox_secret_snapshot",
            ) as secret_rollback:
                invoke_connect(handler)

            self.assertEqual(handler.status, 409)
            self.assertEqual(
                handler.response()["error"]["code"],
                "mailbox_connection_conflict",
            )
            upsert.assert_not_called()
            config_rollback.assert_not_called()
            secret_rollback.assert_not_called()
            final_secret = mailbox_secret_store.read_mailbox_secret(
                "owner@example.com",
                "demo",
            )
            self.assertEqual(final_secret["status"], "present")
            self.assertEqual(
                final_secret["record"]["credentialVersion"],
                CREDENTIAL_VERSION_A,
            )
            self.assertEqual(final_secret["record"]["imapPassword"], "winner")
            self.assertEqual(
                create_attempts,
                [CREDENTIAL_VERSION_A, CREDENTIAL_VERSION_B],
            )

    def test_late_losing_reconnect_preserves_the_newer_config_and_secret(self):
        version_v0 = (
            base64.urlsafe_b64encode(b"0" * 32).decode("ascii").rstrip("=")
        )
        secret_records = {}
        events = []

        def clone(value):
            return json.loads(json.dumps(value))

        def read_secret(_config, key):
            return clone(secret_records[key]) if key in secret_records else None, None

        def create_secret(_config, encrypted_key, legacy_key, replacement):
            if encrypted_key in secret_records or legacy_key in secret_records:
                return 0, None
            decrypted, decrypt_error = mailbox_secret_store._decrypt_secret_record(
                b"k" * 32,
                "owner@example.com",
                "demo",
                replacement,
            )
            self.assertIsNone(decrypt_error)
            events.append(("secret_create", decrypted["credentialVersion"]))
            secret_records[encrypted_key] = clone(replacement)
            return 1, None

        def replace_secret(_config, key, expected_snapshot, replacement):
            current = secret_records.get(key)
            matches = (
                current is None
                if expected_snapshot["status"] == "missing"
                else current == expected_snapshot.get("record")
            )
            if not matches:
                return 0, None
            decrypted, decrypt_error = mailbox_secret_store._decrypt_secret_record(
                b"k" * 32,
                "owner@example.com",
                "demo",
                replacement,
            )
            self.assertIsNone(decrypt_error)
            events.append(("secret_replace", decrypted["credentialVersion"]))
            secret_records[key] = clone(replacement)
            return 1, None

        def delete_secret(_config, key, expected_record):
            if secret_records.get(key) != expected_record:
                return 0, None
            del secret_records[key]
            return 1, None

        initial_inbox = {
            "id": "demo",
            "title": "Demo",
            "email": "demo@example.com",
            "provider": "custom_imap",
            "connected": True,
            "connectionMethod": "imap",
            "connectionStatus": "connected",
            "connectionMessage": None,
            "oauthAuthorizationUrl": None,
            "credentialVersion": version_v0,
            "customImap": {
                "host": "imap.old.example.com",
                "port": "993",
                "ssl": True,
                "username": "demo@example.com",
            },
            "customSmtp": {
                "host": "smtp.old.example.com",
                "port": "587",
                "security": "starttls",
                "username": "demo@example.com",
                "useSameCredentials": True,
            },
        }
        config_state = {
            "value": {
                "v": 1,
                "email": "owner@example.com",
                "managedInboxes": [clone(initial_inbox)],
            }
        }

        def read_config(_store, _owner):
            return {
                "status": "ok",
                "config": clone(config_state["value"]),
                "error": None,
            }

        def write_config_if_unchanged(_store, _owner, expected, replacement):
            if config_state["value"] != expected:
                return {
                    "status": "conflict",
                    "record": None,
                    "error": {
                        "code": "user_config_write_conflict",
                        "message": "stale",
                    },
                }
            generation = replacement["managedInboxes"][0].get(
                "credentialVersion"
            )
            events.append(("config_write", generation))
            config_state["value"] = clone(replacement)
            return {"status": "ok", "record": clone(replacement), "error": None}

        def resolve_target(_headers, mailbox_id):
            matches = [
                inbox
                for inbox in config_state["value"]["managedInboxes"]
                if inbox.get("id") == mailbox_id
            ]
            if len(matches) != 1:
                return {
                    "status": "not_found",
                    "user": {"email": "owner@example.com"},
                    "inbox": None,
                    "config": clone(config_state["value"]),
                    "error": None,
                }
            return {
                "status": "ok",
                "user": {"email": "owner@example.com"},
                "inbox": clone(matches[0]),
                "config": clone(config_state["value"]),
                "error": None,
            }

        encoded_encryption_key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
        user = {
            "email": "owner@example.com",
            "name": "Owner",
            "userType": "member",
        }
        original_upsert = connect_route.upsert_owned_custom_imap_mailbox

        with patch.dict(
            os.environ,
            {
                mailbox_secret_store.MAILBOX_SECRET_ENCRYPTION_KEY_ENV: encoded_encryption_key
            },
            clear=False,
        ), patch.object(
            mailbox_secret_store,
            "_resolve_durable_store_config",
            return_value={"configured": True},
        ), patch.object(
            mailbox_secret_store,
            "_read_durable_record",
            side_effect=read_secret,
        ), patch.object(
            mailbox_secret_store,
            "_perform_create_secret_namespace_if_missing",
            side_effect=create_secret,
        ), patch.object(
            mailbox_secret_store,
            "_perform_compare_and_set_secret",
            side_effect=replace_secret,
        ), patch.object(
            mailbox_secret_store,
            "_perform_compare_and_delete_secret",
            side_effect=delete_secret,
        ), patch.object(
            user_config_store,
            "resolve_authenticated_user",
            return_value=(user, None),
        ), patch.object(
            user_config_store,
            "resolve_user_config_store",
            return_value=({"configured": True}, None),
        ), patch.object(
            user_config_store,
            "read_user_config_record",
            side_effect=read_config,
        ), patch.object(
            user_config_store,
            "write_user_config_record_if_unchanged",
            side_effect=write_config_if_unchanged,
        ), patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=(user, None),
        ), patch.object(
            connect_route,
            "resolve_owned_managed_inbox_record",
            side_effect=resolve_target,
        ), patch.object(
            connect_route,
            "generate_mailbox_credential_version",
            return_value=CREDENTIAL_VERSION_A,
        ), patch.object(
            imap_connect_preview,
            "build_connect_preview_response",
            return_value=(200, {"ok": True, "messages": []}),
        ):
            initial_secret = mailbox_secret_store.create_mailbox_secret_if_missing(
                "owner@example.com",
                "demo",
                version_v0,
                imap_password="old-secret",
                smtp_password="old-secret",
            )
            self.assertEqual(initial_secret["status"], "applied")
            events.clear()

            def commit_b_then_reject_a(*args, **kwargs):
                snapshot_a = mailbox_secret_store.snapshot_encrypted_mailbox_secret(
                    "owner@example.com",
                    "demo",
                )
                saved_b, error_b = mailbox_secret_store.save_mailbox_secret(
                    "owner@example.com",
                    "demo",
                    imap_password="winner-b",
                    smtp_password="winner-b",
                    credential_version=CREDENTIAL_VERSION_B,
                    expected_snapshot=snapshot_a,
                )
                self.assertIsNone(error_b)
                self.assertEqual(
                    saved_b["credentialVersion"],
                    CREDENTIAL_VERSION_B,
                )
                committed_b = original_upsert(
                    args[0],
                    args[1],
                    "reconnect",
                    args[3],
                    args[4],
                    credential_version=CREDENTIAL_VERSION_B,
                    expected_inbox=initial_inbox,
                )
                self.assertEqual(committed_b["status"], "ok")
                rejected_a = original_upsert(*args, **kwargs)
                self.assertEqual(rejected_a["status"], "conflict")
                self.assertEqual(
                    rejected_a["error"]["code"],
                    "user_config_write_conflict",
                )
                return rejected_a

            handler = FakeHandler(initial_payload(mode="reconnect"))
            with patch.object(
                connect_route,
                "upsert_owned_custom_imap_mailbox",
                side_effect=commit_b_then_reject_a,
            ), patch.object(
                connect_route,
                "restore_encrypted_mailbox_secret_snapshot",
                wraps=mailbox_secret_store.restore_encrypted_mailbox_secret_snapshot,
            ) as stale_secret_rollback:
                invoke_connect(handler)

            self.assertEqual(handler.status, 409)
            self.assertEqual(
                handler.response()["error"]["code"],
                "mailbox_connection_conflict",
            )
            stale_secret_rollback.assert_called_once_with(
                "owner@example.com",
                "demo",
                ANY,
                expected_credential_version=CREDENTIAL_VERSION_A,
            )
            final_inbox = config_state["value"]["managedInboxes"][0]
            self.assertEqual(
                final_inbox["credentialVersion"],
                CREDENTIAL_VERSION_B,
            )
            final_secret = mailbox_secret_store.read_mailbox_secret(
                "owner@example.com",
                "demo",
            )
            self.assertEqual(final_secret["status"], "present")
            self.assertEqual(
                final_secret["record"]["credentialVersion"],
                CREDENTIAL_VERSION_B,
            )
            self.assertEqual(
                final_secret["record"]["imapPassword"],
                "winner-b",
            )
            self.assertEqual(
                events,
                [
                    ("secret_replace", CREDENTIAL_VERSION_A),
                    ("secret_replace", CREDENTIAL_VERSION_B),
                    ("config_write", CREDENTIAL_VERSION_B),
                ],
            )


class CredentialsRouteTests(unittest.TestCase):
    def test_secret_writes_are_disabled(self):
        handler = FakeHandler({"mailboxId": "demo", "imapPassword": "secret"})
        credentials_route.handler.do_POST(handler)
        self.assertEqual(handler.status, 405)
        self.assertEqual(handler.response()["error"]["code"], "method_not_allowed")

    def _invoke_status(self, inbox, secret_result):
        handler = FakeHandler()
        handler.path = "/api/inboxes/credentials?mailboxIds=demo"
        with patch.object(
            credentials_route,
            "resolve_authenticated_user",
            return_value=(
                {
                    "email": "owner@example.com",
                    "name": "Owner",
                    "userType": "member",
                },
                None,
            ),
        ), patch.object(
            credentials_route,
            "resolve_owned_managed_inbox",
            return_value={
                "status": "ok",
                "inbox": inbox,
                "error": None,
            },
        ), patch.object(
            credentials_route,
            "read_mailbox_secret",
            return_value=secret_result,
        ) as secret_lookup:
            credentials_route.handler.do_GET(handler)
        return handler, secret_lookup

    def test_status_pre_gates_config_before_secret_lookup(self):
        valid_inbox = {
            "id": "demo",
            "provider": "custom_imap",
            "connected": True,
            "connectionStatus": "connected",
            "credentialVersion": CREDENTIAL_VERSION_A,
        }
        invalid_inboxes = (
            ("wrong provider", {**valid_inbox, "provider": "google"}),
            ("not connected", {**valid_inbox, "connected": False}),
            (
                "wrong connection status",
                {**valid_inbox, "connectionStatus": "reconnect_required"},
            ),
            (
                "missing generation",
                {
                    key: value
                    for key, value in valid_inbox.items()
                    if key != "credentialVersion"
                },
            ),
            (
                "malformed generation",
                {**valid_inbox, "credentialVersion": "malformed"},
            ),
        )

        for label, inbox in invalid_inboxes:
            with self.subTest(label=label):
                handler, secret_lookup = self._invoke_status(
                    inbox,
                    {
                        "status": "present",
                        "record": {
                            "credentialVersion": CREDENTIAL_VERSION_A,
                            "imapPassword": "must-not-read",
                            "smtpPassword": "must-not-read",
                        },
                        "error": None,
                    },
                )

                self.assertEqual(handler.status, 200)
                self.assertEqual(
                    handler.response()["credentials"]["demo"],
                    {"imapPasswordSet": False, "smtpPasswordSet": False},
                )
                secret_lookup.assert_not_called()

    def test_status_accepts_only_clean_missing_or_consistent_present_shapes(self):
        inbox = {
            "id": "demo",
            "provider": "custom_imap",
            "connected": True,
            "connectionStatus": "connected",
            "credentialVersion": CREDENTIAL_VERSION_A,
        }
        missing_handler, missing_lookup = self._invoke_status(
            inbox,
            {"status": "missing", "record": None, "error": None},
        )
        self.assertEqual(missing_handler.status, 200)
        self.assertEqual(
            missing_handler.response()["credentials"]["demo"],
            {"imapPasswordSet": False, "smtpPasswordSet": False},
        )
        missing_lookup.assert_called_once_with("owner@example.com", "demo")

        matching_record = {
            "credentialVersion": CREDENTIAL_VERSION_A,
            "imapPassword": "must-not-leak",
            "smtpPassword": "must-not-leak",
        }
        inconsistent_results = (
            None,
            [],
            {},
            {
                "status": "missing",
                "record": matching_record,
                "error": None,
            },
            {
                "status": "missing",
                "record": None,
                "error": {
                    "code": "private_error",
                    "message": CREDENTIAL_VERSION_B,
                },
            },
            {"status": "present", "record": None, "error": None},
            {"status": "present", "record": [], "error": None},
            {
                "status": "present",
                "record": matching_record,
                "error": {
                    "code": "private_error",
                    "message": CREDENTIAL_VERSION_B,
                },
            },
            {
                "status": "unavailable",
                "record": matching_record,
                "error": {
                    "code": "private_error",
                    "message": CREDENTIAL_VERSION_B,
                },
            },
            {"status": "future_status", "record": None, "error": None},
        )
        for secret_result in inconsistent_results:
            with self.subTest(secret_result=secret_result):
                handler, secret_lookup = self._invoke_status(inbox, secret_result)

                self.assertEqual(handler.status, 503)
                self.assertEqual(
                    handler.response(),
                    {
                        "ok": False,
                        "error": {
                            "code": "mailbox_secret_store_unavailable",
                            "message": (
                                "Mailbox credential status is temporarily unavailable."
                            ),
                        },
                    },
                )
                response_text = json.dumps(handler.response())
                self.assertNotIn(CREDENTIAL_VERSION_A, response_text)
                self.assertNotIn(CREDENTIAL_VERSION_B, response_text)
                self.assertNotIn("must-not-leak", response_text)
                secret_lookup.assert_called_once_with("owner@example.com", "demo")

        handler = FakeHandler()
        handler.path = "/api/inboxes/credentials?mailboxIds=demo"
        with patch.object(
            credentials_route,
            "resolve_authenticated_user",
            return_value=({"email": "owner@example.com"}, None),
        ), patch.object(
            credentials_route,
            "resolve_owned_managed_inbox",
            return_value={"status": "ok", "inbox": inbox, "error": None},
        ), patch.object(
            credentials_route,
            "read_mailbox_secret",
            side_effect=RuntimeError(CREDENTIAL_VERSION_B),
        ):
            credentials_route.handler.do_GET(handler)
        self.assertEqual(handler.status, 503)
        self.assertNotIn(
            CREDENTIAL_VERSION_B,
            json.dumps(handler.response()),
        )

    def test_status_is_authenticated_owned_and_never_returns_secret(self):
        handler = FakeHandler()
        handler.path = "/api/inboxes/credentials?mailboxIds=demo"
        with patch.object(
            credentials_route,
            "resolve_authenticated_user",
            return_value=(
                {
                    "email": "owner@example.com",
                    "name": "Owner",
                    "userType": "member",
                    "authSource": "auth0",
                },
                None,
            ),
        ), patch.object(
            credentials_route,
            "resolve_owned_managed_inbox",
            return_value={
                "status": "ok",
                "inbox": {
                    "id": "demo",
                    "provider": "custom_imap",
                    "connected": True,
                    "connectionStatus": "connected",
                    "imapConnectionStatus": "connected",
                    "smtpConnectionStatus": "connected",
                    "fullyConnected": True,
                    "credentialVersion": CREDENTIAL_VERSION_A,
                    "customSmtp": {
                        "host": "smtp.example.com",
                        "port": "587",
                        "security": "starttls",
                        "username": "demo@example.com",
                        "useSameCredentials": True,
                    },
                },
                "error": None,
            },
        ), patch.object(
            credentials_route,
            "read_mailbox_secret",
            return_value={
                "status": "present",
                "record": {
                    "credentialVersion": CREDENTIAL_VERSION_A,
                    "imapPassword": "secret",
                    "smtpPassword": "",
                },
                "error": None,
            },
        ):
            credentials_route.handler.do_GET(handler)
        self.assertEqual(handler.status, 200)
        response = handler.response()
        self.assertEqual(
            response["credentials"]["demo"],
            {"imapPasswordSet": True, "smtpPasswordSet": True},
        )
        self.assertNotIn("secret", json.dumps(response))

    def test_status_reports_smtp_false_when_smtp_is_not_configured(self):
        inbox = {
            "id": "demo",
            "provider": "custom_imap",
            "connected": True,
            "connectionStatus": "connected",
            "credentialVersion": CREDENTIAL_VERSION_A,
            "customSmtp": {},
        }
        handler, secret_lookup = self._invoke_status(
            inbox,
            {
                "status": "present",
                "record": {
                    "credentialVersion": CREDENTIAL_VERSION_A,
                    "imapPassword": "imap-secret",
                    "smtpPassword": "orphaned-smtp-secret",
                },
                "error": None,
            },
        )

        self.assertEqual(handler.status, 200)
        self.assertEqual(
            handler.response()["credentials"]["demo"],
            {"imapPasswordSet": True, "smtpPasswordSet": False},
        )
        self.assertNotIn("orphaned-smtp-secret", json.dumps(handler.response()))
        secret_lookup.assert_called_once_with("owner@example.com", "demo")

    def test_status_rejects_a_stored_smtp_placeholder_as_a_credential(self):
        inbox = {
            "id": "demo",
            "provider": "custom_imap",
            "connected": True,
            "connectionStatus": "connected",
            "credentialVersion": CREDENTIAL_VERSION_A,
            "customSmtp": {
                "host": "smtp.example.com",
                "port": "587",
                "security": "starttls",
                "username": "smtp-user@example.com",
                "useSameCredentials": False,
            },
        }
        handler, _secret_lookup = self._invoke_status(
            inbox,
            {
                "status": "present",
                "record": {
                    "credentialVersion": CREDENTIAL_VERSION_A,
                    "imapPassword": "imap-secret",
                    "smtpPassword": "Stored securely",
                },
                "error": None,
            },
        )

        self.assertEqual(handler.status, 200)
        self.assertEqual(
            handler.response()["credentials"]["demo"],
            {"imapPasswordSet": True, "smtpPasswordSet": False},
        )
        self.assertNotIn("Stored securely", json.dumps(handler.response()))

    def test_status_never_reports_passwords_for_a_generation_mismatch(self):
        handler = FakeHandler()
        handler.path = "/api/inboxes/credentials?mailboxIds=demo"
        with patch.object(
            credentials_route,
            "resolve_authenticated_user",
            return_value=(
                {
                    "email": "owner@example.com",
                    "name": "Owner",
                    "userType": "member",
                },
                None,
            ),
        ), patch.object(
            credentials_route,
            "resolve_owned_managed_inbox",
            return_value={
                "status": "ok",
                "inbox": {
                    "id": "demo",
                    "provider": "custom_imap",
                    "connected": True,
                    "connectionStatus": "connected",
                    "credentialVersion": CREDENTIAL_VERSION_A,
                },
                "error": None,
            },
        ), patch.object(
            credentials_route,
            "read_mailbox_secret",
            return_value={
                "status": "present",
                "record": {
                    "credentialVersion": CREDENTIAL_VERSION_B,
                    "imapPassword": "must-not-count",
                    "smtpPassword": "must-not-count",
                },
                "error": None,
            },
        ):
            credentials_route.handler.do_GET(handler)

        self.assertEqual(handler.status, 200)
        self.assertEqual(
            handler.response()["credentials"]["demo"],
            {"imapPasswordSet": False, "smtpPasswordSet": False},
        )
        self.assertNotIn("must-not-count", json.dumps(handler.response()))


if __name__ == "__main__":
    unittest.main()
