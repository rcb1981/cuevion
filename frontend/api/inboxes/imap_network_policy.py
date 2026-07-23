import imaplib
import ipaddress
import socket
import ssl
import sys


_CANONICAL_MODULE_NAME = "api.inboxes.imap_network_policy"
_LEGACY_MODULE_NAME = "imap_network_policy"
if __name__ == _CANONICAL_MODULE_NAME:
    sys.modules.setdefault(_LEGACY_MODULE_NAME, sys.modules[__name__])
elif __name__ == _LEGACY_MODULE_NAME:
    sys.modules.setdefault(_CANONICAL_MODULE_NAME, sys.modules[__name__])


DEFAULT_IMAP_TIMEOUT_SECONDS = 30
MAX_RESOLVED_IMAP_ADDRESSES = 64

_BLOCKED_HOST_SUFFIXES = ("localhost", "local", "internal")
_EXPLICITLY_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "100.64.0.0/10",
        "169.254.169.254/32",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "::ffff:0:0/96",
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "2001::/32",
        "2001:20::/28",
        "2001:db8::/32",
        "2002::/16",
        "3ffe::/16",
        "3fff::/20",
        "fec0::/10",
    )
)


class ImapNetworkPolicyError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ResolvedImapDestination:
    __slots__ = ("host", "port", "addresses", "allowed_ips")

    def __init__(
        self,
        host: str,
        port: int,
        addresses: tuple[tuple[int, int, int, tuple, str], ...],
        allowed_ips: frozenset[str],
    ):
        self.host = host
        self.port = port
        self.addresses = addresses
        self.allowed_ips = allowed_ips


class _PreconnectedIMAP4(imaplib.IMAP4):
    def __init__(
        self,
        host: str,
        port: int,
        connected_socket: socket.socket,
        *,
        timeout: float | None,
    ):
        self._preconnected_socket = connected_socket
        super().__init__(host, port, timeout=timeout)

    def _create_socket(self, timeout):
        connected_socket = self._preconnected_socket
        self._preconnected_socket = None
        return connected_socket


class _PreconnectedIMAP4SSL(imaplib.IMAP4_SSL):
    def __init__(
        self,
        host: str,
        port: int,
        connected_socket: ssl.SSLSocket,
        *,
        ssl_context: ssl.SSLContext,
        timeout: float | None,
    ):
        self._preconnected_socket = connected_socket
        super().__init__(
            host,
            port,
            ssl_context=ssl_context,
            timeout=timeout,
        )

    def _create_socket(self, timeout):
        connected_socket = self._preconnected_socket
        self._preconnected_socket = None
        return connected_socket


def normalize_imap_host(value) -> str:
    if not isinstance(value, str):
        raise ImapNetworkPolicyError("imap_host_invalid")

    if any(
        ord(character) < 32
        or 127 <= ord(character) <= 159
        or not character.isprintable()
        for character in value
    ):
        raise ImapNetworkPolicyError("imap_host_invalid")

    host = value.strip()
    if (
        not host
        or any(character.isspace() for character in host)
        or any(character in host for character in ("/", "\\", "?", "#", "@", "%"))
        or "://" in host
    ):
        raise ImapNetworkPolicyError("imap_host_invalid")

    if host.endswith("."):
        host = host[:-1]
    if not host or host.endswith("."):
        raise ImapNetworkPolicyError("imap_host_invalid")

    try:
        numeric_address = ipaddress.ip_address(host)
    except ValueError:
        numeric_address = None
    if numeric_address is not None:
        return numeric_address.compressed.lower()

    if ":" in host:
        raise ImapNetworkPolicyError("imap_host_invalid")

    try:
        normalized = host.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        raise ImapNetworkPolicyError("imap_host_invalid") from None

    if not normalized or len(normalized) > 253:
        raise ImapNetworkPolicyError("imap_host_invalid")
    labels = normalized.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or any(
            not ("a" <= character <= "z")
            and not ("0" <= character <= "9")
            and character != "-"
            for character in label
        )
        for label in labels
    ):
        raise ImapNetworkPolicyError("imap_host_invalid")

    if any(
        normalized == suffix or normalized.endswith(f".{suffix}")
        for suffix in _BLOCKED_HOST_SUFFIXES
    ):
        raise ImapNetworkPolicyError("imap_host_invalid")

    return normalized


def _validated_port(value) -> int:
    if isinstance(value, bool):
        raise ImapNetworkPolicyError("imap_connection_failed")
    try:
        port = int(str(value))
    except (TypeError, ValueError):
        raise ImapNetworkPolicyError("imap_connection_failed") from None
    if port < 1 or port > 65535:
        raise ImapNetworkPolicyError("imap_connection_failed")
    return port


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    mapped_address = getattr(address, "ipv4_mapped", None)
    if mapped_address is not None and not _is_public_address(mapped_address):
        return False
    return (
        address.is_global
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_unspecified
        and not address.is_reserved
        and not getattr(address, "is_site_local", False)
        and not any(address in network for network in _EXPLICITLY_BLOCKED_NETWORKS)
    )


def _validated_resolver_record(record, requested_port: int):
    if not isinstance(record, tuple) or len(record) != 5:
        raise ImapNetworkPolicyError("imap_dns_failed")

    family, socket_type, protocol, canonical_name, socket_address = record
    # Some platforms report protocol 0 for an otherwise TCP-constrained
    # getaddrinfo result. The stored dial target is normalized to IPPROTO_TCP.
    if (
        not isinstance(family, int)
        or isinstance(family, bool)
        or not isinstance(socket_type, int)
        or isinstance(socket_type, bool)
        or not isinstance(protocol, int)
        or isinstance(protocol, bool)
        or family not in {socket.AF_INET, socket.AF_INET6}
        or socket_type != socket.SOCK_STREAM
        or protocol not in {0, socket.IPPROTO_TCP}
        or not isinstance(canonical_name, str)
        or not isinstance(socket_address, tuple)
    ):
        raise ImapNetworkPolicyError("imap_dns_failed")

    expected_arity = 2 if family == socket.AF_INET else 4
    if len(socket_address) != expected_arity:
        raise ImapNetworkPolicyError("imap_dns_failed")

    address_value, resolved_port = socket_address[:2]
    if (
        not isinstance(address_value, str)
        or not address_value
        or "%" in address_value
        or isinstance(resolved_port, bool)
        or not isinstance(resolved_port, int)
        or resolved_port < 1
        or resolved_port > 65535
        or resolved_port != requested_port
    ):
        raise ImapNetworkPolicyError("imap_dns_failed")

    if family == socket.AF_INET:
        flow_info = 0
        scope_id = 0
    else:
        flow_info, scope_id = socket_address[2:]
        if (
            isinstance(flow_info, bool)
            or not isinstance(flow_info, int)
            or flow_info < 0
            or flow_info > 0xFFFFF
            or isinstance(scope_id, bool)
            or not isinstance(scope_id, int)
            or scope_id < 0
            or scope_id > 0xFFFFFFFF
        ):
            raise ImapNetworkPolicyError("imap_dns_failed")

    try:
        address = ipaddress.ip_address(address_value)
    except (TypeError, ValueError):
        raise ImapNetworkPolicyError("imap_dns_failed") from None
    if (
        family == socket.AF_INET
        and not isinstance(address, ipaddress.IPv4Address)
    ) or (
        family == socket.AF_INET6
        and not isinstance(address, ipaddress.IPv6Address)
    ):
        raise ImapNetworkPolicyError("imap_dns_failed")

    normalized_ip = address.compressed.lower()
    normalized_socket_address = (
        (normalized_ip, requested_port)
        if family == socket.AF_INET
        else (normalized_ip, requested_port, flow_info, scope_id)
    )
    return family, normalized_socket_address, address, normalized_ip


def resolve_public_imap_destination(host, port) -> ResolvedImapDestination:
    normalized_host = normalize_imap_host(host)
    normalized_port = _validated_port(port)
    try:
        results = socket.getaddrinfo(
            normalized_host,
            normalized_port,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
        )
    except (OSError, UnicodeError):
        raise ImapNetworkPolicyError("imap_dns_failed") from None

    if not isinstance(results, (list, tuple)):
        raise ImapNetworkPolicyError("imap_dns_failed")
    if not results or len(results) > MAX_RESOLVED_IMAP_ADDRESSES:
        raise ImapNetworkPolicyError("imap_dns_failed")

    addresses = []
    seen = set()
    allowed_ips = set()
    for record in results:
        family, normalized_socket_address, address, normalized_ip = (
            _validated_resolver_record(record, normalized_port)
        )
        if not _is_public_address(address):
            raise ImapNetworkPolicyError("imap_destination_not_allowed")

        key = (family, normalized_socket_address)
        if key in seen:
            continue
        seen.add(key)
        allowed_ips.add(normalized_ip)
        addresses.append(
            (
                family,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                normalized_socket_address,
                normalized_ip,
            )
        )

    if not addresses:
        raise ImapNetworkPolicyError("imap_dns_failed")

    addresses.sort(key=lambda item: (item[0], item[4], item[3]))
    return ResolvedImapDestination(
        normalized_host,
        normalized_port,
        tuple(addresses),
        frozenset(allowed_ips),
    )


def _close_socket_quietly(socket_to_close) -> None:
    try:
        socket_to_close.close()
    except Exception:
        pass


def _validated_peer_ip(
    connected_socket: socket.socket,
    destination: ResolvedImapDestination,
    *,
    expected_ip: str,
) -> str:
    try:
        peer = connected_socket.getpeername()
        peer_value = peer[0] if isinstance(peer, tuple) and peer else None
        peer_address = ipaddress.ip_address(peer_value)
    except (OSError, TypeError, ValueError):
        raise ImapNetworkPolicyError("imap_peer_mismatch") from None

    normalized_peer = peer_address.compressed.lower()
    if (
        not _is_public_address(peer_address)
        or normalized_peer not in destination.allowed_ips
        or normalized_peer != expected_ip
    ):
        raise ImapNetworkPolicyError("imap_peer_mismatch")
    return normalized_peer


def _connect_to_resolved_destination(
    destination: ResolvedImapDestination,
    timeout: float | None,
) -> socket.socket:
    connected_socket = None
    ownership_transferred = False
    try:
        if timeout is not None and timeout <= 0:
            raise ImapNetworkPolicyError("imap_connection_failed")
        family, socket_type, protocol, socket_address, selected_ip = (
            destination.addresses[0]
        )
        connected_socket = socket.socket(family, socket_type, protocol)
        if timeout is not None:
            connected_socket.settimeout(timeout)
        connected_socket.connect(socket_address)
        _validated_peer_ip(
            connected_socket,
            destination,
            expected_ip=selected_ip,
        )
        ownership_transferred = True
        return connected_socket
    except ImapNetworkPolicyError:
        raise
    except Exception:
        raise ImapNetworkPolicyError("imap_connection_failed") from None
    finally:
        if connected_socket is not None and not ownership_transferred:
            _close_socket_quietly(connected_socket)


def open_public_imap_connection(
    host,
    port,
    *,
    ssl_enabled: bool,
    ssl_context: ssl.SSLContext | None = None,
    timeout: float | None = DEFAULT_IMAP_TIMEOUT_SECONDS,
):
    destination = resolve_public_imap_destination(host, port)
    connected_socket = _connect_to_resolved_destination(destination, timeout)
    protocol_socket = connected_socket

    try:
        if ssl_enabled:
            if ssl_context is None:
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = True
                ssl_context.verify_mode = ssl.CERT_REQUIRED
            if (
                ssl_context.check_hostname is not True
                or ssl_context.verify_mode != ssl.CERT_REQUIRED
            ):
                raise ImapNetworkPolicyError("imap_connection_failed")
            protocol_socket = ssl_context.wrap_socket(
                connected_socket,
                server_hostname=destination.host,
            )
            _validated_peer_ip(
                protocol_socket,
                destination,
                expected_ip=destination.addresses[0][4],
            )
            connected_socket = None
            return _PreconnectedIMAP4SSL(
                destination.host,
                destination.port,
                protocol_socket,
                ssl_context=ssl_context,
                timeout=timeout,
            )

        connected_socket = None
        return _PreconnectedIMAP4(
            destination.host,
            destination.port,
            protocol_socket,
            timeout=timeout,
        )
    except ImapNetworkPolicyError:
        _close_socket_quietly(protocol_socket)
        raise
    except (OSError, ssl.SSLError, imaplib.IMAP4.error):
        _close_socket_quietly(protocol_socket)
        raise ImapNetworkPolicyError("imap_connection_failed") from None
    except Exception:
        _close_socket_quietly(protocol_socket)
        raise ImapNetworkPolicyError("imap_connection_failed") from None
