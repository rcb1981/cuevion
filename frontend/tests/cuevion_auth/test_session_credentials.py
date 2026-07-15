"""Security tests for the inactive Auth-B1a session-credential boundary."""

import ast
import base64
import binascii
from collections import UserDict
import contextlib
import copy
import dataclasses
import hashlib
import hmac
import importlib
import inspect
import io
import json
import os
from pathlib import Path, PurePosixPath
import pickle
import re
import subprocess
import sys
from types import MappingProxyType
import unittest
from unittest import mock

from cuevion_auth import session_credentials as credentials


_TEST_DIRECTORY = Path(__file__).resolve().parent
_FRONTEND_DIRECTORY = _TEST_DIRECTORY.parents[1]
_SOURCE_DIRECTORY = _FRONTEND_DIRECTORY / "cuevion_auth"
_SOURCE_PATH = _SOURCE_DIRECTORY / "session_credentials.py"
_DOCUMENTATION_PATH = _SOURCE_DIRECTORY / "AUTH_B1_ACTIVATION_REQUIREMENTS.md"
_COOKIE_NAME = "__Host-cuevion_session"

_LOOKUP_CURRENT_BYTES = bytes(range(32))
_BINDING_CURRENT_BYTES = bytes(range(32, 64))
_COOKIE_SECRET_BYTES = bytes(range(64, 96))
_LOOKUP_PREVIOUS_BYTES = bytes(range(96, 128))
_BINDING_PREVIOUS_BYTES = bytes(range(128, 160))


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


_LOOKUP_CURRENT_KEY = _b64(_LOOKUP_CURRENT_BYTES)
_BINDING_CURRENT_KEY = _b64(_BINDING_CURRENT_BYTES)
_LOOKUP_PREVIOUS_KEY = _b64(_LOOKUP_PREVIOUS_BYTES)
_BINDING_PREVIOUS_KEY = _b64(_BINDING_PREVIOUS_BYTES)
_COOKIE_SECRET = _b64(_COOKIE_SECRET_BYTES)

_TRACEBACK_CURRENT_KEY_BYTES = tuple(
    bytes((value,)) * 32 for value in (161, 162, 163, 164)
)
_TRACEBACK_PREVIOUS_LOOKUP_BYTES = bytes((165,)) * 32
_TRACEBACK_PREVIOUS_BINDING_BYTES = bytes((166,)) * 32
_TRACEBACK_ALL_KEY_BYTES = (
    *_TRACEBACK_CURRENT_KEY_BYTES,
    _TRACEBACK_PREVIOUS_LOOKUP_BYTES,
    _TRACEBACK_PREVIOUS_BINDING_BYTES,
)
_TRACEBACK_ALL_ENCODED_KEYS = tuple(_b64(value) for value in _TRACEBACK_ALL_KEY_BYTES)


def _configuration_values(
    *,
    lookup_epoch: str = "7",
    binding_epoch: str = "11",
    lookup_key: str = _LOOKUP_CURRENT_KEY,
    binding_key: str = _BINDING_CURRENT_KEY,
    previous_lookup: bool = False,
    previous_binding: bool = False,
) -> dict[str, str]:
    values = {
        "lookup_current_epoch": lookup_epoch,
        "lookup_current_key": lookup_key,
        "binding_current_epoch": binding_epoch,
        "binding_current_key": binding_key,
    }
    if previous_lookup:
        values.update(
            {
                "lookup_previous_epoch": "5",
                "lookup_previous_key": _LOOKUP_PREVIOUS_KEY,
            }
        )
    if previous_binding:
        values.update(
            {
                "binding_previous_epoch": "9",
                "binding_previous_key": _BINDING_PREVIOUS_KEY,
            }
        )
    return values


def _configuration(**keywords: object) -> credentials.SessionKeyConfiguration:
    return credentials.parse_session_key_configuration(
        _configuration_values(**keywords)  # type: ignore[arg-type]
    )


def _traceback_configuration_values(
    *,
    alternate_current: bool = False,
) -> dict[str, str]:
    current_offset = 2 if alternate_current else 0
    values = _configuration_values(
        lookup_epoch="17",
        binding_epoch="23",
        lookup_key=_TRACEBACK_ALL_ENCODED_KEYS[current_offset],
        binding_key=_TRACEBACK_ALL_ENCODED_KEYS[current_offset + 1],
        previous_lookup=True,
        previous_binding=True,
    )
    values["lookup_previous_epoch"] = "13"
    values["lookup_previous_key"] = _b64(_TRACEBACK_PREVIOUS_LOOKUP_BYTES)
    values["binding_previous_epoch"] = "19"
    values["binding_previous_key"] = _b64(_TRACEBACK_PREVIOUS_BINDING_BYTES)
    return values


def _envelope(
    *,
    lookup_epoch: str = "7",
    binding_epoch: str = "11",
    credential_epoch: str = "13",
    secret: str = _COOKIE_SECRET,
    version: str = "v1",
) -> str:
    return ".".join(
        (version, lookup_epoch, binding_epoch, credential_epoch, secret)
    )


def _headers(
    envelope: str | None = None,
    *,
    header_name: str = "Cookie",
    prefix: str = "",
) -> tuple[tuple[str, str], ...]:
    value = _envelope() if envelope is None else envelope
    return ((header_name, f"{prefix}{_COOKIE_NAME}={value}"),)


def _derive(
    configuration: credentials.SessionKeyConfiguration | None = None,
    envelope: str | None = None,
    *,
    prefix: str = "",
) -> credentials.DerivedSessionCredential:
    derived = credentials.derive_request_session_credential(
        _headers(envelope, prefix=prefix),
        _configuration() if configuration is None else configuration,
    )
    if derived is None:
        raise AssertionError("valid fixture credential was rejected")
    return derived


def _noncanonical_pad_bit_alias(value: str) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    index = alphabet.index(value[-1])
    return value[:-1] + alphabet[index + 1]


def _frame(fields: tuple[bytes, ...]) -> bytes:
    return b"".join(len(field).to_bytes(4, "big") + field for field in fields)


def _expected_digests(
    lookup_key: bytes,
    binding_key: bytes,
    lookup_epoch: int,
    binding_epoch: int,
    credential_epoch: int,
    secret: bytes,
) -> tuple[str, str]:
    framed = _frame(
        (
            b"v1",
            str(lookup_epoch).encode("ascii"),
            str(binding_epoch).encode("ascii"),
            str(credential_epoch).encode("ascii"),
            secret,
        )
    )
    lookup = hmac.new(
        lookup_key,
        b"cuevion/auth/session-lookup/v1\x00" + framed,
        hashlib.sha256,
    ).digest()
    binding = hmac.new(
        binding_key,
        b"cuevion/auth/session-binding/v1\x00" + framed,
        hashlib.sha256,
    ).digest()
    return _b64(lookup), _b64(binding)


def _run_isolated(program: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-c", program],
        cwd=_FRONTEND_DIRECTORY,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _cold_import_program() -> str:
    source_path = str(_SOURCE_PATH)
    source_directory = str(_SOURCE_DIRECTORY)
    frontend_directory = str(_FRONTEND_DIRECTORY)
    return f"""
import atexit
import base64
import binascii
import builtins
import hashlib
import hmac
import http.client
import importlib
import importlib.util
import io
import os
import random
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request

target = 'cuevion_auth.session_credentials'
source_path = {source_path!r}
source_directory = {source_directory!r}
frontend_directory = {frontend_directory!r}
assert target not in sys.modules
assert 'cuevion_auth' not in sys.modules

class BlockedEnvironment:
    def blocked(self, *_arguments, **_keywords):
        raise AssertionError('environment access')
    __getitem__ = blocked
    __setitem__ = blocked
    __delitem__ = blocked
    __iter__ = blocked
    __len__ = blocked
    __contains__ = blocked
    get = blocked
    keys = blocked
    items = blocked
    values = blocked
    copy = blocked

side_effects = []
def blocked(*_arguments, **_keywords):
    side_effects.append('blocked operation')
    raise AssertionError('forbidden side effect')

filesystem_events = []
network_or_process_events = []
def audit(event, arguments):
    if event == 'open' or event.startswith('os.'):
        caller = sys._getframe(1).f_globals.get('__name__')
        filesystem_events.append((event, caller))
    if (
        event.startswith('socket.')
        or event.startswith('subprocess.')
        or event.startswith('http.client.')
    ):
        network_or_process_events.append(event)
sys.addaudithook(audit)

os.environ = BlockedEnvironment()
if hasattr(os, 'environb'):
    os.environb = BlockedEnvironment()
os.getenv = blocked
if hasattr(os, 'getenvb'):
    os.getenvb = blocked
socket.socket = blocked
socket.create_connection = blocked
socket.getaddrinfo = blocked
urllib.request.urlopen = blocked
urllib.request.OpenerDirector.open = blocked
http.client.HTTPConnection.connect = blocked
http.client.HTTPSConnection.connect = blocked
threading.Thread.start = blocked
subprocess.Popen = blocked
atexit.register = blocked
random.random = blocked
random.getrandbits = blocked
random.randbytes = blocked
secrets.token_bytes = blocked
secrets.token_hex = blocked
secrets.token_urlsafe = blocked
time.time = blocked
time.monotonic = blocked
time.perf_counter = blocked
base64.b64decode = blocked
base64.urlsafe_b64encode = blocked
hmac.new = blocked

allowed_imports = {{'sys', 'base64', 'binascii', 'hashlib', 'hmac'}}
production_imports = []
original_import = builtins.__import__
def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    caller = globals.get('__name__') if type(globals) is dict else None
    if caller == target:
        if level != 0 or name not in allowed_imports:
            raise AssertionError('forbidden production import')
        production_imports.append(name)
    return original_import(name, globals, locals, fromlist, level)
builtins.__import__ = guarded_import

path_before = tuple(sys.path)
modules_before = set(sys.modules)
module = importlib.import_module(target)
assert tuple(sys.path) == path_before
assert module is sys.modules[target]
assert set(sys.modules) - modules_before == {{'cuevion_auth', target}}
assert production_imports == ['sys', 'base64', 'binascii', 'hashlib', 'hmac']
assert [name for name, value in sys.modules.items() if value is module] == [target]
assert module.__name__ == target
assert module.__package__ == 'cuevion_auth'
assert module.__spec__.name == target
assert not any(
    isinstance(value, (module.SessionKeyConfiguration, module.DerivedSessionCredential))
    for value in module.__dict__.values()
)
for forbidden_surface in ('handler', 'route', 'router', 'app'):
    assert not hasattr(module, forbidden_surface)
assert side_effects == []
assert network_or_process_events == []
for event, caller in filesystem_events:
    assert caller == 'importlib._bootstrap_external', (event, caller)
"""


def _public_operation_program() -> str:
    configuration_values = _configuration_values(
        previous_lookup=True,
        previous_binding=True,
    )
    headers = _headers()
    malformed_headers = _headers(_envelope(secret="x"))
    return f"""
import atexit
import builtins
import http.client
import importlib
import io
import logging
import os
import pathlib
import random
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

target = 'cuevion_auth.session_credentials'
module = importlib.import_module(target)
module_keys_before = frozenset(module.__dict__)
for forbidden_surface in ('handler', 'route', 'router', 'app', 'service'):
    assert not hasattr(module, forbidden_surface)

class BlockedEnvironment:
    def blocked(self, *_arguments, **_keywords):
        raise AssertionError('environment access')
    __getitem__ = blocked
    __setitem__ = blocked
    __delitem__ = blocked
    __iter__ = blocked
    __len__ = blocked
    __contains__ = blocked
    get = blocked
    keys = blocked
    items = blocked
    values = blocked
    copy = blocked

side_effects = []
def blocked(*_arguments, **_keywords):
    side_effects.append('blocked operation')
    raise AssertionError('forbidden public-operation side effect')

audit_events = []
def audit(event, _arguments):
    if (
        event == 'open'
        or event.startswith('socket.')
        or event.startswith('subprocess.')
        or event.startswith('http.client.')
        or event.startswith('os.system')
        or event.startswith('os.spawn')
        or event.startswith('os.posix_spawn')
    ):
        audit_events.append(event)
        raise AssertionError('forbidden audited public-operation side effect')
sys.addaudithook(audit)

builtins.open = blocked
io.open = blocked
for method_name in (
    'open', 'read_text', 'read_bytes', 'write_text', 'write_bytes', 'touch',
    'mkdir', 'unlink', 'rename', 'replace',
):
    setattr(pathlib.Path, method_name, blocked)
os.environ = BlockedEnvironment()
if hasattr(os, 'environb'):
    os.environb = BlockedEnvironment()
os.getenv = blocked
if hasattr(os, 'getenvb'):
    os.getenvb = blocked
socket.socket = blocked
socket.create_connection = blocked
socket.getaddrinfo = blocked
urllib.request.urlopen = blocked
urllib.request.OpenerDirector.open = blocked
http.client.HTTPConnection.connect = blocked
http.client.HTTPSConnection.connect = blocked
subprocess.Popen = blocked
os.system = blocked
for process_name in (
    'fork', 'forkpty', 'posix_spawn', 'posix_spawnp', 'spawnl', 'spawnle',
    'spawnlp', 'spawnlpe', 'spawnv', 'spawnve', 'spawnvp', 'spawnvpe',
):
    if hasattr(os, process_name):
        setattr(os, process_name, blocked)
threading.Thread.start = blocked
atexit.register = blocked
signal.signal = blocked
for clock_name in (
    'time', 'time_ns', 'monotonic', 'monotonic_ns', 'perf_counter',
    'perf_counter_ns', 'process_time', 'process_time_ns',
):
    if hasattr(time, clock_name):
        setattr(time, clock_name, blocked)
for random_name in ('random', 'getrandbits', 'randbytes'):
    if hasattr(random, random_name):
        setattr(random, random_name, blocked)
for secret_name in ('token_bytes', 'token_hex', 'token_urlsafe', 'randbelow'):
    setattr(secrets, secret_name, blocked)
os.urandom = blocked
uuid.uuid1 = blocked
uuid.uuid4 = blocked
for logging_name in (
    'getLogger', 'debug', 'info', 'warning', 'error', 'exception', 'critical',
    'log', 'basicConfig',
):
    setattr(logging, logging_name, blocked)
logging.Logger._log = blocked
logging.Logger.addHandler = blocked
logging.Logger.removeHandler = blocked

captured_stdout = io.StringIO()
captured_stderr = io.StringIO()
original_stdout = sys.stdout
original_stderr = sys.stderr
try:
    sys.stdout = captured_stdout
    sys.stderr = captured_stderr
    configuration = module.parse_session_key_configuration({configuration_values!r})
    derived = module.derive_request_session_credential({headers!r}, configuration)
    malformed = module.derive_request_session_credential(
        {malformed_headers!r},
        configuration,
    )
finally:
    sys.stdout = original_stdout
    sys.stderr = original_stderr

assert type(configuration) is module.SessionKeyConfiguration
assert type(derived) is module.DerivedSessionCredential
assert derived.lookup_key_epoch == 7
assert derived.binding_key_epoch == 11
assert derived.credential_epoch == 13
assert malformed is None
assert captured_stdout.getvalue() == ''
assert captured_stderr.getvalue() == ''
assert side_effects == []
assert audit_events == []
assert frozenset(module.__dict__) == module_keys_before
for forbidden_surface in ('handler', 'route', 'router', 'app', 'service'):
    assert not hasattr(module, forbidden_surface)
"""


class ModuleIdentityAndInactivityTests(unittest.TestCase):
    def test_canonical_identity_namespace_and_exact_file_scope(self):
        self.assertEqual(credentials.__name__, "cuevion_auth.session_credentials")
        self.assertEqual(credentials.__package__, "cuevion_auth")
        self.assertEqual(credentials.__spec__.name, "cuevion_auth.session_credentials")
        self.assertIs(
            credentials,
            sys.modules["cuevion_auth.session_credentials"],
        )
        self.assertFalse((_SOURCE_DIRECTORY / "__init__.py").exists())
        self.assertFalse((_TEST_DIRECTORY / "__init__.py").exists())
        self.assertFalse((_FRONTEND_DIRECTORY / "tests" / "__init__.py").exists())
        self.assertEqual(
            {path.name for path in _SOURCE_DIRECTORY.iterdir() if path.is_file()},
            {
                "session_credentials.py",
                "AUTH_B1_ACTIVATION_REQUIREMENTS.md",
                "account_record_ids.py",
                "AUTH_RECORD_ID_ACTIVATION_REQUIREMENTS.md",
            },
        )
        self.assertEqual(
            {path.name for path in _TEST_DIRECTORY.iterdir() if path.is_file()},
            {"test_session_credentials.py", "test_account_record_ids.py"},
        )

    def test_top_level_and_alternate_dotted_imports_fail(self):
        attempts = (
            (str(_SOURCE_DIRECTORY), "session_credentials"),
            (str(_FRONTEND_DIRECTORY.parent), "frontend.cuevion_auth.session_credentials"),
        )
        values = _configuration_values()
        headers = _headers()
        for path_entry, module_name in attempts:
            with self.subTest(module_name=module_name):
                program = (
                    "import importlib,sys\n"
                    "original=importlib.import_module('cuevion_auth.session_credentials')\n"
                    "identities=(original.SessionKeyConfigurationError,original.SessionKeyConfiguration,original.DerivedSessionCredential)\n"
                    "sentinels=(original._CONFIGURATION_SENTINEL,original._DERIVED_CREDENTIAL_SENTINEL)\n"
                    f"configuration=original.parse_session_key_configuration({values!r})\n"
                    f"headers={headers!r}\n"
                    "path_before=tuple(sys.path)\n"
                    f"sys.path.insert(0,{path_entry!r})\n"
                    "try:\n"
                    f" importlib.import_module({module_name!r})\n"
                    "except ImportError:\n"
                    " pass\n"
                    "else:\n"
                    " raise SystemExit('alternate identity unexpectedly succeeded')\n"
                    "assert sys.modules['cuevion_auth.session_credentials'] is original\n"
                    "assert identities == (original.SessionKeyConfigurationError,original.SessionKeyConfiguration,original.DerivedSessionCredential)\n"
                    "assert sentinels == (original._CONFIGURATION_SENTINEL,original._DERIVED_CREDENTIAL_SENTINEL)\n"
                    "assert type(original.derive_request_session_credential(headers,configuration)) is original.DerivedSessionCredential\n"
                    "assert tuple(sys.path[1:]) == path_before\n"
                )
                completed = _run_isolated(program)
                self.assertEqual(
                    completed.returncode,
                    0,
                    msg=completed.stdout + completed.stderr,
                )

    def test_alternate_spec_duplicate_canonical_spec_and_reload_fail_early(self):
        values = _configuration_values()
        headers = _headers()
        program = (
            "import importlib,importlib.util,sys\n"
            "original=importlib.import_module('cuevion_auth.session_credentials')\n"
            "identities=(original.SessionKeyConfigurationError,original.SessionKeyConfiguration,original.DerivedSessionCredential)\n"
            "sentinels=(original._CONFIGURATION_SENTINEL,original._DERIVED_CREDENTIAL_SENTINEL)\n"
            f"configuration=original.parse_session_key_configuration({values!r})\n"
            f"headers={headers!r}\n"
            "def assert_original_usable():\n"
            " assert sys.modules['cuevion_auth.session_credentials'] is original\n"
            " assert identities == (original.SessionKeyConfigurationError,original.SessionKeyConfiguration,original.DerivedSessionCredential)\n"
            " assert sentinels == (original._CONFIGURATION_SENTINEL,original._DERIVED_CREDENTIAL_SENTINEL)\n"
            " assert type(original.derive_request_session_credential(headers,configuration)) is original.DerivedSessionCredential\n"
            f"path={str(_SOURCE_PATH)!r}\n"
            "for spec_name in ('cuevion_auth.alternate_session_credentials','cuevion_auth.session_credentials'):\n"
            " spec=importlib.util.spec_from_file_location(spec_name,path)\n"
            " duplicate=importlib.util.module_from_spec(spec)\n"
            " try:\n"
            "  spec.loader.exec_module(duplicate)\n"
            " except ImportError:\n"
            "  pass\n"
            " else:\n"
            "  raise SystemExit('duplicate spec unexpectedly succeeded')\n"
            " assert '_AUTH_B1A_SESSION_CREDENTIALS_INITIALIZED' not in duplicate.__dict__\n"
            " for name in ('SessionKeyConfigurationError','_CONFIGURATION_SENTINEL','SessionKeyConfiguration','_DERIVED_CREDENTIAL_SENTINEL','DerivedSessionCredential'):\n"
            "  assert name not in duplicate.__dict__\n"
            " assert_original_usable()\n"
            "try:\n"
            " importlib.reload(original)\n"
            "except ImportError:\n"
            " pass\n"
            "else:\n"
            " raise SystemExit('reload unexpectedly succeeded')\n"
            "assert_original_usable()\n"
        )
        completed = _run_isolated(program)
        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stdout + completed.stderr,
        )

    def test_true_cold_import_has_no_forbidden_side_effects(self):
        completed = _run_isolated(_cold_import_program())
        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stdout + completed.stderr,
        )

    def test_public_operations_have_no_forbidden_runtime_side_effects(self):
        completed = _run_isolated(_public_operation_program())
        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stdout + completed.stderr,
        )
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")

    def test_only_standard_library_imports_and_no_runtime_surface(self):
        tree = ast.parse(_SOURCE_PATH.read_text(encoding="utf-8"))
        imports: set[tuple[int, str | None]] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update((0, alias.name) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add((node.level, node.module))
        self.assertEqual(
            imports,
            {
                (0, "sys"),
                (0, "base64"),
                (0, "binascii"),
                (0, "hashlib"),
                (0, "hmac"),
            },
        )
        public = {
            name: value
            for name, value in vars(credentials).items()
            if not name.startswith("_")
        }
        self.assertEqual(set(public), set(credentials.__all__))
        self.assertEqual(
            set(credentials.__all__),
            {
                "SessionKeyConfigurationError",
                "SessionKeyConfiguration",
                "DerivedSessionCredential",
                "parse_session_key_configuration",
                "derive_request_session_credential",
            },
        )
        for forbidden in ("handler", "route", "router", "app"):
            self.assertNotIn(forbidden, vars(credentials))
        source = _SOURCE_PATH.read_text(encoding="utf-8").casefold()
        for forbidden in (
            "os.environ",
            "getenv(",
            "open(",
            "socket",
            "urlopen",
            "random.",
            "secrets.",
            "time.time",
            "datetime",
            "redis",
            "vercel",
            "compare_digest",
            "set-cookie",
        ):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("beta", source)
        self.assertEqual(source.count(_COOKIE_NAME.casefold()), 1)

    def test_public_signatures_are_exact(self):
        parser = inspect.signature(credentials.parse_session_key_configuration)
        self.assertEqual(tuple(parser.parameters), ("values",))
        self.assertEqual(parser.parameters["values"].annotation, dict[str, str])
        self.assertIs(
            parser.return_annotation,
            credentials.SessionKeyConfiguration,
        )
        derivation = inspect.signature(credentials.derive_request_session_credential)
        self.assertEqual(
            tuple(derivation.parameters),
            ("raw_headers", "configuration"),
        )
        self.assertEqual(
            derivation.parameters["raw_headers"].annotation,
            tuple[tuple[str, str], ...],
        )
        self.assertIs(
            derivation.parameters["configuration"].annotation,
            credentials.SessionKeyConfiguration,
        )
        self.assertEqual(
            derivation.return_annotation,
            credentials.DerivedSessionCredential | None,
        )

    def test_vercel_python_function_patterns_are_exact_and_exclude_auth_b1a(self):
        configuration = json.loads(
            (_FRONTEND_DIRECTORY / "vercel.json").read_text(encoding="utf-8")
        )
        functions = configuration["functions"]
        self.assertIs(type(functions), dict)
        configured_patterns = set(functions)
        self.assertEqual(configured_patterns, {"api/**/*.py"})
        self.assertEqual(
            {
                pattern
                for pattern in configured_patterns
                if pattern.casefold().endswith(".py")
            },
            {"api/**/*.py"},
        )
        for relative_path in (
            "cuevion_auth/session_credentials.py",
            "cuevion_auth/account_record_ids.py",
            "tests/cuevion_auth/test_session_credentials.py",
            "tests/cuevion_auth/test_account_record_ids.py",
        ):
            with self.subTest(relative_path=relative_path):
                self.assertTrue(
                    all(
                        not PurePosixPath(relative_path).match(pattern)
                        for pattern in configured_patterns
                    )
                )


class ConfigurationParsingTests(unittest.TestCase):
    def assert_configuration_error(self, callable_object: object) -> None:
        secret = "rejected-private-value"
        try:
            callable_object()  # type: ignore[operator]
        except credentials.SessionKeyConfigurationError as error:
            self.assertIs(type(error), credentials.SessionKeyConfigurationError)
            self.assertEqual(error.args, ())
            self.assertEqual(str(error), "session key configuration is invalid")
            self.assertEqual(repr(error), "SessionKeyConfigurationError()")
            self.assertNotIn(secret, str(error))
            self.assertNotIn(secret, repr(error))
            self.assertIsNone(error.__context__)
            self.assertIsNone(error.__cause__)
        else:
            self.fail("configuration failure was not raised")

    def assert_sanitized_configuration_error(
        self,
        callable_object: object,
        source_values: dict[str, str],
        *,
        private_markers: tuple[str, ...] = (),
    ) -> None:
        try:
            callable_object()  # type: ignore[operator]
        except credentials.SessionKeyConfigurationError as error:
            encoded_secrets = (
                *_TRACEBACK_ALL_ENCODED_KEYS,
                *private_markers,
            )
            byte_secrets = (
                *_TRACEBACK_ALL_KEY_BYTES,
                *(marker.encode("utf-8") for marker in private_markers),
            )

            self.assertIs(type(error), credentials.SessionKeyConfigurationError)
            self.assertEqual(error.args, ())
            self.assertEqual(str(error), "session key configuration is invalid")
            self.assertEqual(repr(error), "SessionKeyConfigurationError()")
            self.assertIsNone(error.__context__)
            self.assertIsNone(error.__cause__)
            for secret in encoded_secrets:
                self.assertNotIn(secret, str(error))
                self.assertNotIn(secret, repr(error))

            seen: set[int] = set()

            def inspect_safe_value(value: object) -> None:
                if value is source_values:
                    self.fail("module traceback retained the source configuration")
                if type(value) is credentials.SessionKeyConfiguration:
                    self.fail("module traceback retained a key configuration")
                if isinstance(value, BaseException):
                    if value is not error:
                        self.fail("module traceback retained a private exception")
                    return

                value_type = type(value)
                if value_type is str:
                    if any(secret in value for secret in encoded_secrets):
                        self.fail("module traceback retained an encoded key")
                    return
                if value_type is bytes:
                    if any(secret in value for secret in byte_secrets):
                        self.fail("module traceback retained decoded key bytes")
                    return
                if value_type is bytearray or value_type is memoryview:
                    raw_value = bytes(value)
                    if any(secret in raw_value for secret in byte_secrets):
                        self.fail("module traceback retained decoded key bytes")
                    return
                if value_type not in (dict, list, tuple, set, frozenset):
                    return
                identity = id(value)
                if identity in seen:
                    return
                seen.add(identity)
                if value_type is dict:
                    for nested_key, nested_value in dict.items(value):
                        inspect_safe_value(nested_key)
                        inspect_safe_value(nested_value)
                    return
                for nested_value in value:  # type: ignore[union-attr]
                    inspect_safe_value(nested_value)

            module_frames = 0
            traceback = error.__traceback__
            source_filename = os.path.realpath(_SOURCE_PATH)
            while traceback is not None:
                frame = traceback.tb_frame
                if os.path.realpath(frame.f_code.co_filename) == source_filename:
                    module_frames += 1
                    for local_name, local_value in dict.items(frame.f_locals):
                        inspect_safe_value(local_name)
                        inspect_safe_value(local_value)
                traceback = traceback.tb_next
            self.assertGreater(module_frames, 0)
        else:
            self.fail("configuration failure was not raised")

    def test_valid_current_and_optional_previous_pair_matrix(self):
        for previous_lookup, previous_binding in (
            (False, False),
            (True, False),
            (False, True),
            (True, True),
        ):
            with self.subTest(
                previous_lookup=previous_lookup,
                previous_binding=previous_binding,
            ):
                parsed = _configuration(
                    previous_lookup=previous_lookup,
                    previous_binding=previous_binding,
                )
                self.assertIs(type(parsed), credentials.SessionKeyConfiguration)

    def test_exact_dict_is_required(self):
        values = _configuration_values()

        class DictSubclass(dict):
            pass

        for rejected in (
            DictSubclass(values),
            MappingProxyType(values),
            UserDict(values),
            list(values.items()),
            tuple(values.items()),
            None,
        ):
            with self.subTest(rejected_type=type(rejected).__name__):
                self.assert_configuration_error(
                    lambda rejected=rejected: credentials.parse_session_key_configuration(
                        rejected  # type: ignore[arg-type]
                    )
                )

    def test_exact_string_keys_and_values_are_required_without_custom_behavior(self):
        class StringSubclass(str):
            def __eq__(self, _other: object) -> bool:
                raise AssertionError("custom equality invoked")

            def __hash__(self) -> int:
                return str.__hash__(self)

            def __str__(self) -> str:
                raise AssertionError("custom string conversion invoked")

            def __repr__(self) -> str:
                raise AssertionError("custom representation invoked")

        values_with_subclass_key = _configuration_values()
        original = values_with_subclass_key.pop("lookup_current_epoch")
        values_with_subclass_key[StringSubclass("lookup_current_epoch")] = original
        values_with_subclass_value = _configuration_values()
        values_with_subclass_value["lookup_current_epoch"] = StringSubclass("7")

        class HostileValue:
            def __eq__(self, _other: object) -> bool:
                raise AssertionError("hostile equality invoked")

            def __hash__(self) -> int:
                raise AssertionError("hostile hashing invoked")

            def __str__(self) -> str:
                raise AssertionError("hostile conversion invoked")

            def __repr__(self) -> str:
                raise AssertionError("hostile representation invoked")

        values_with_object = _configuration_values()
        values_with_object["lookup_current_epoch"] = HostileValue()  # type: ignore[assignment]
        for rejected in (
            values_with_subclass_key,
            values_with_subclass_value,
            values_with_object,
        ):
            self.assert_configuration_error(
                lambda rejected=rejected: credentials.parse_session_key_configuration(
                    rejected
                )
            )

    def test_non_string_key_is_rejected_before_rehash_equality_or_rendering(self):
        class HostileKey:
            armed = False

            def __hash__(self) -> int:
                if self.armed:
                    raise AssertionError("hostile rehash invoked")
                return 1

            def __eq__(self, _other: object) -> bool:
                raise AssertionError("hostile equality invoked")

            def __str__(self) -> str:
                raise AssertionError("hostile conversion invoked")

            def __repr__(self) -> str:
                raise AssertionError("hostile representation invoked")

        key = HostileKey()
        values = _configuration_values()
        values[key] = "value"  # type: ignore[index]
        key.armed = True
        self.assert_configuration_error(
            lambda: credentials.parse_session_key_configuration(values)
        )

    def test_unknown_missing_and_partial_optional_keys_are_rejected(self):
        cases: list[dict[str, str]] = []
        for required in (
            "lookup_current_epoch",
            "lookup_current_key",
            "binding_current_epoch",
            "binding_current_key",
        ):
            values = _configuration_values()
            del values[required]
            cases.append(values)
        unknown = _configuration_values()
        unknown["unexpected"] = "value"
        cases.append(unknown)
        for key in (
            "lookup_previous_epoch",
            "lookup_previous_key",
            "binding_previous_epoch",
            "binding_previous_key",
        ):
            values = _configuration_values()
            values[key] = (
                "5" if key.endswith("epoch") else _LOOKUP_PREVIOUS_KEY
            )
            cases.append(values)
        for values in cases:
            with self.subTest(keys=tuple(values)):
                self.assert_configuration_error(
                    lambda values=values: credentials.parse_session_key_configuration(
                        values
                    )
                )

    def test_present_but_invalid_previous_pairs_cannot_collapse_to_absent(self):
        cases = []
        for family in ("lookup", "binding"):
            values = _configuration_values(
                previous_lookup=family == "lookup",
                previous_binding=family == "binding",
            )
            values[f"{family}_previous_epoch"] = "0"
            cases.append(values)
            values = _configuration_values(
                previous_lookup=family == "lookup",
                previous_binding=family == "binding",
            )
            values[f"{family}_previous_key"] = "x"
            cases.append(values)
            values = _configuration_values(
                previous_lookup=family == "lookup",
                previous_binding=family == "binding",
            )
            values[f"{family}_previous_epoch"] = "0"
            values[f"{family}_previous_key"] = "x"
            cases.append(values)
        for values in cases:
            with self.subTest(keys=tuple(values)):
                self.assert_configuration_error(
                    lambda values=values: credentials.parse_session_key_configuration(
                        values
                    )
                )

    def test_epoch_boundaries_and_cross_family_equality(self):
        for boundary in ("1", "2147483647"):
            parsed = credentials.parse_session_key_configuration(
                _configuration_values(
                    lookup_epoch=boundary,
                    binding_epoch=boundary,
                )
            )
            self.assertIs(type(parsed), credentials.SessionKeyConfiguration)
        equal_cross_family = _configuration_values(
            lookup_epoch="20",
            binding_epoch="20",
            previous_lookup=True,
            previous_binding=True,
        )
        equal_cross_family["lookup_previous_epoch"] = "10"
        equal_cross_family["binding_previous_epoch"] = "10"
        self.assertIs(
            type(
                credentials.parse_session_key_configuration(
                    equal_cross_family
                )
            ),
            credentials.SessionKeyConfiguration,
        )

    def test_complete_invalid_epoch_matrix_is_rejected_in_all_four_slots(self):
        rejected = (
            "",
            "0",
            "-1",
            "+1",
            "01",
            " 1",
            "1 ",
            "1 0",
            "١",
            "２",
            "2147483648",
            "1a",
            "1\n",
            "1\x00",
        )
        for field in (
            "lookup_current_epoch",
            "lookup_previous_epoch",
            "binding_current_epoch",
            "binding_previous_epoch",
        ):
            for value in rejected:
                values = _configuration_values(
                    lookup_epoch="20",
                    binding_epoch="30",
                    previous_lookup=True,
                    previous_binding=True,
                )
                values["lookup_previous_epoch"] = "10"
                values["binding_previous_epoch"] = "15"
                values[field] = value
                with self.subTest(field=field, value=value.encode("unicode_escape")):
                    self.assert_configuration_error(
                        lambda values=values: credentials.parse_session_key_configuration(
                            values
                        )
                    )

    def test_current_epoch_must_be_greater_than_previous(self):
        for family in ("lookup", "binding"):
            for current, previous in (("5", "5"), ("4", "5")):
                values = _configuration_values(
                    lookup_epoch="7",
                    binding_epoch="11",
                    previous_lookup=family == "lookup",
                    previous_binding=family == "binding",
                )
                values[f"{family}_current_epoch"] = current
                values[f"{family}_previous_epoch"] = previous
                with self.subTest(family=family, current=current, previous=previous):
                    self.assert_configuration_error(
                        lambda values=values: credentials.parse_session_key_configuration(
                            values
                        )
                    )

    def test_canonical_key_encoding_is_strict(self):
        alias = _noncanonical_pad_bit_alias(_LOOKUP_CURRENT_KEY)
        self.assertEqual(
            base64.urlsafe_b64decode(alias + "="),
            _LOOKUP_CURRENT_BYTES,
        )
        rejected = (
            "",
            _LOOKUP_CURRENT_KEY + "=",
            _LOOKUP_CURRENT_KEY[:-1],
            _LOOKUP_CURRENT_KEY + "A",
            "+" + _LOOKUP_CURRENT_KEY[1:],
            "/" + _LOOKUP_CURRENT_KEY[1:],
            "*" + _LOOKUP_CURRENT_KEY[1:],
            "é" * 43,
            alias,
        )
        for field in (
            "lookup_current_key",
            "lookup_previous_key",
            "binding_current_key",
            "binding_previous_key",
        ):
            for value in rejected:
                values = _configuration_values(
                    previous_lookup=True,
                    previous_binding=True,
                )
                values[field] = value
                with self.subTest(field=field, length=len(value)):
                    self.assert_configuration_error(
                        lambda values=values: credentials.parse_session_key_configuration(
                            values
                        )
                    )

    def test_every_configured_raw_key_must_be_pairwise_distinct(self):
        key_fields = (
            "lookup_current_key",
            "lookup_previous_key",
            "binding_current_key",
            "binding_previous_key",
        )
        for first_index in range(len(key_fields)):
            for second_index in range(first_index + 1, len(key_fields)):
                values = _configuration_values(
                    previous_lookup=True,
                    previous_binding=True,
                )
                values[key_fields[second_index]] = values[key_fields[first_index]]
                with self.subTest(
                    first=key_fields[first_index],
                    second=key_fields[second_index],
                ):
                    self.assert_configuration_error(
                        lambda values=values: credentials.parse_session_key_configuration(
                            values
                        )
                    )

    def test_configuration_error_tracebacks_never_retain_module_owned_keys(self):
        before_decoding = _traceback_configuration_values()
        before_decoding["unexpected_configuration_key"] = "rejected"

        epoch_validation = _traceback_configuration_values(
            alternate_current=True
        )
        epoch_validation["lookup_current_epoch"] = "0"

        during_key_decoding = _traceback_configuration_values()
        during_key_decoding["lookup_current_key"] = "x"

        after_one_key_decoded = _traceback_configuration_values(
            alternate_current=True
        )
        after_one_key_decoded["lookup_previous_key"] = "x"

        after_all_keys_decoded = _traceback_configuration_values()

        pairwise_key_reuse = _traceback_configuration_values(
            alternate_current=True
        )
        pairwise_key_reuse["binding_previous_key"] = pairwise_key_reuse[
            "lookup_current_key"
        ]

        invalid_ordering = _traceback_configuration_values()
        invalid_ordering["lookup_current_epoch"] = invalid_ordering[
            "lookup_previous_epoch"
        ]

        injected_failure = _traceback_configuration_values(
            alternate_current=True
        )
        private_marker = "private-worker-exception-marker"

        scenarios = (
            ("before decoding", before_decoding, contextlib.nullcontext()),
            ("during epoch validation", epoch_validation, contextlib.nullcontext()),
            ("during key decoding", during_key_decoding, contextlib.nullcontext()),
            ("after one key decoded", after_one_key_decoded, contextlib.nullcontext()),
            (
                "after all keys decoded",
                after_all_keys_decoded,
                mock.patch.object(
                    credentials,
                    "_configuration_components_are_valid",
                    return_value=False,
                ),
            ),
            ("during pairwise key reuse", pairwise_key_reuse, contextlib.nullcontext()),
            ("during epoch ordering", invalid_ordering, contextlib.nullcontext()),
            (
                "during injected ordinary exception",
                injected_failure,
                mock.patch.object(
                    credentials,
                    "_new_configuration",
                    side_effect=RuntimeError(private_marker),
                ),
            ),
        )
        for name, values, patcher in scenarios:
            with self.subTest(stage=name), patcher:
                self.assert_sanitized_configuration_error(
                    lambda values=values: credentials.parse_session_key_configuration(
                        values
                    ),
                    values,
                    private_markers=(private_marker,),
                )

    def test_corrupt_configuration_derivation_traceback_retains_no_keys(self):
        values = _traceback_configuration_values(alternate_current=True)
        configuration = credentials.parse_session_key_configuration(values)
        object.__setattr__(
            configuration,
            "_lookup_current_epoch",
            int(values["lookup_previous_epoch"]),
        )
        self.assert_sanitized_configuration_error(
            lambda: credentials.derive_request_session_credential(
                _headers(),
                configuration,
            ),
            values,
        )

    def test_configuration_error_is_fixed_detached_and_value_free(self):
        secret = "rejected-private-value"
        error = credentials.SessionKeyConfigurationError(secret, named=secret)
        self.assertEqual(error.args, ())
        self.assertEqual(str(error), "session key configuration is invalid")
        self.assertEqual(repr(error), "SessionKeyConfigurationError()")
        error.__init__(secret, named=secret)
        self.assertEqual(error.args, ())
        partial = ValueError.__new__(
            credentials.SessionKeyConfigurationError,
            secret,
        )
        self.assertEqual(partial.args, ())
        self.assertEqual(str(partial), "session key configuration is invalid")
        self.assertEqual(repr(partial), "SessionKeyConfigurationError()")
        try:
            raise RuntimeError(secret)
        except RuntimeError:
            self.assert_configuration_error(
                lambda: credentials.parse_session_key_configuration({})
            )

    def test_ordinary_internal_configuration_failures_are_normalized(self):
        private = "private-internal-detail"
        for helper_name in (
            "_parse_epoch",
            "_decode_canonical_32_bytes",
            "_configuration_components_are_valid",
            "_new_configuration",
        ):
            with self.subTest(helper=helper_name), mock.patch.object(
                credentials,
                helper_name,
                side_effect=RuntimeError(private),
            ):
                self.assert_configuration_error(
                    lambda: credentials.parse_session_key_configuration(
                        _configuration_values()
                    )
                )

        configuration = _configuration()
        with mock.patch.object(
            credentials,
            "_configuration_components_are_valid",
            side_effect=RuntimeError(private),
        ):
            self.assert_configuration_error(
                lambda: credentials.derive_request_session_credential(
                    _headers(),
                    configuration,
                )
            )

    def test_request_decoder_failure_classification_precedes_hmac(self):
        configuration = _configuration()

        with mock.patch.object(
            credentials._base64,
            "b64decode",
            side_effect=binascii.Error("expected malformed encoding"),
        ), mock.patch.object(credentials._hmac, "new") as hmac_new:
            self.assertIsNone(
                credentials.derive_request_session_credential(
                    _headers(),
                    configuration,
                )
            )
            hmac_new.assert_not_called()

        private_marker = "private-request-decoder-runtime-error"
        unexpected = RuntimeError(private_marker)
        with mock.patch.object(
            credentials._base64,
            "b64decode",
            side_effect=unexpected,
        ), mock.patch.object(credentials._hmac, "new") as hmac_new:
            with self.assertRaises(RuntimeError) as captured:
                credentials.derive_request_session_credential(
                    _headers(),
                    configuration,
                )
            self.assertIs(captured.exception, unexpected)
            self.assertNotIsInstance(
                captured.exception,
                credentials.SessionKeyConfigurationError,
            )
            hmac_new.assert_not_called()

    def test_unexpected_configuration_decoder_failure_is_fixed_and_sanitized(self):
        values = _traceback_configuration_values(alternate_current=True)
        private_marker = "private-configuration-decoder-runtime-error"
        with mock.patch.object(
            credentials._base64,
            "b64decode",
            side_effect=RuntimeError(private_marker),
        ):
            self.assert_sanitized_configuration_error(
                lambda: credentials.parse_session_key_configuration(values),
                values,
                private_markers=(private_marker,),
            )

    def test_baseexception_is_not_swallowed_by_key_decoder_in_either_path(self):
        configuration = _configuration()
        for exception_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(path="request", exception=exception_type.__name__):
                request_failure = exception_type("request decoder stop")
                with mock.patch.object(
                    credentials._base64,
                    "b64decode",
                    side_effect=request_failure,
                ), mock.patch.object(credentials._hmac, "new") as hmac_new:
                    with self.assertRaises(exception_type) as captured:
                        credentials.derive_request_session_credential(
                            _headers(),
                            configuration,
                        )
                    self.assertIs(captured.exception, request_failure)
                    hmac_new.assert_not_called()

            with self.subTest(path="configuration", exception=exception_type.__name__):
                configuration_failure = exception_type("configuration decoder stop")
                with mock.patch.object(
                    credentials._base64,
                    "b64decode",
                    side_effect=configuration_failure,
                ):
                    with self.assertRaises(exception_type) as captured:
                        credentials.parse_session_key_configuration(
                            _configuration_values()
                        )
                    self.assertIs(captured.exception, configuration_failure)

    def test_other_baseexception_helpers_are_not_swallowed(self):
        class Stop(BaseException):
            pass

        for helper_name in (
            "_parse_epoch",
            "_decode_canonical_32_bytes",
            "_configuration_components_are_valid",
            "_new_configuration",
        ):
            with self.subTest(helper=helper_name), mock.patch.object(
                credentials,
                helper_name,
                side_effect=Stop("stop"),
            ):
                with self.assertRaises(Stop):
                    credentials.parse_session_key_configuration(
                        _configuration_values()
                    )


class OpaqueConfigurationTests(unittest.TestCase):
    def test_direct_construction_and_subclassing_fail(self):
        for arguments in ((), (object(),), ("private",), (1, 2, 3)):
            with self.subTest(arguments_length=len(arguments)):
                with self.assertRaisesRegex(
                    TypeError,
                    "session key configurations are parser-controlled",
                ):
                    credentials.SessionKeyConfiguration(*arguments)
        with self.assertRaises(TypeError):
            type(
                "ConfigurationSubclass",
                (credentials.SessionKeyConfiguration,),
                {},
            )

    def test_immutable_identity_semantics_and_no_dictionary(self):
        first = _configuration()
        second = _configuration()
        self.assertIsNot(first, second)
        self.assertNotEqual(first, second)
        self.assertEqual(hash(first), hash(first))
        with self.assertRaises(AttributeError):
            first.extra = "value"  # type: ignore[attr-defined]
        with self.assertRaises(AttributeError):
            del first._lookup_current_epoch
        self.assertFalse(hasattr(first, "__dict__"))
        self.assertFalse(dataclasses.is_dataclass(first))
        with self.assertRaises(TypeError):
            dataclasses.asdict(first)  # type: ignore[arg-type]

    def test_copy_pickle_and_rendering_are_value_free(self):
        configuration = _configuration(previous_lookup=True, previous_binding=True)
        self.assertIs(copy.copy(configuration), configuration)
        self.assertIs(copy.deepcopy(configuration), configuration)
        self.assertEqual(repr(configuration), "<SessionKeyConfiguration>")
        self.assertEqual(str(configuration), "SessionKeyConfiguration")
        for key in (
            _LOOKUP_CURRENT_KEY,
            _LOOKUP_PREVIOUS_KEY,
            _BINDING_CURRENT_KEY,
            _BINDING_PREVIOUS_KEY,
        ):
            self.assertNotIn(key, repr(configuration))
            self.assertNotIn(key, str(configuration))
        for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
            with self.subTest(protocol=protocol):
                with self.assertRaises(TypeError):
                    pickle.dumps(configuration, protocol=protocol)
        with self.assertRaises(TypeError):
            configuration.__getstate__()
        with self.assertRaises(TypeError):
            configuration.__setstate__({"key": "private"})

    def test_no_public_raw_key_or_encoded_key_accessor(self):
        configuration = _configuration(previous_lookup=True, previous_binding=True)
        self.assertEqual(
            configuration.__slots__,
            (
                "_sentinel",
                "_lookup_current_epoch",
                "_lookup_current_key",
                "_lookup_previous_epoch",
                "_lookup_previous_key",
                "_binding_current_epoch",
                "_binding_current_key",
                "_binding_previous_epoch",
                "_binding_previous_key",
            ),
        )
        public = {name for name in dir(configuration) if not name.startswith("_")}
        self.assertEqual(public, set())
        self.assertTrue(all(name.startswith("_") for name in configuration.__slots__))
        for forbidden in (
            "lookup_key",
            "binding_key",
            "raw_key",
            "encoded_key",
            "secret",
        ):
            self.assertFalse(hasattr(configuration, forbidden))

    def test_source_snapshot_is_independent_and_uses_exact_immutable_values(self):
        values = _configuration_values(previous_lookup=True, previous_binding=True)
        original_headers = _headers()
        configuration = credentials.parse_session_key_configuration(values)
        decoded_lookup = base64.urlsafe_b64decode(_LOOKUP_CURRENT_KEY + "=")
        internal_lookup = object.__getattribute__(
            configuration,
            "_lookup_current_key",
        )
        self.assertIs(type(internal_lookup), bytes)
        self.assertEqual(internal_lookup, decoded_lookup)
        self.assertIsNot(internal_lookup, decoded_lookup)
        for slot in configuration.__slots__:
            value = object.__getattribute__(configuration, slot)
            self.assertIn(type(value), (object, int, bytes, type(None)))
        values.clear()
        values["lookup_current_key"] = "changed"
        self.assertIsNotNone(
            credentials.derive_request_session_credential(
                original_headers,
                configuration,
            )
        )

    def test_forged_partial_or_corrupt_exact_configuration_is_fixed_error(self):
        candidates: list[credentials.SessionKeyConfiguration] = []
        candidates.append(object.__new__(credentials.SessionKeyConfiguration))
        partial = object.__new__(credentials.SessionKeyConfiguration)
        object.__setattr__(partial, "_sentinel", credentials._CONFIGURATION_SENTINEL)
        candidates.append(partial)
        corrupt = _configuration()
        object.__setattr__(corrupt, "_lookup_current_key", bytearray(32))
        candidates.append(corrupt)
        wrong_sentinel = _configuration()
        object.__setattr__(wrong_sentinel, "_sentinel", object())
        candidates.append(wrong_sentinel)
        bool_epoch = _configuration()
        object.__setattr__(bool_epoch, "_lookup_current_epoch", True)
        candidates.append(bool_epoch)
        reused_key = _configuration()
        object.__setattr__(
            reused_key,
            "_binding_current_key",
            object.__getattribute__(reused_key, "_lookup_current_key"),
        )
        candidates.append(reused_key)
        for candidate in candidates:
            with self.subTest(candidate=repr(candidate)):
                with self.assertRaises(credentials.SessionKeyConfigurationError) as caught:
                    credentials.derive_request_session_credential((), candidate)
                self.assertEqual(caught.exception.args, ())
                self.assertIsNone(caught.exception.__context__)
                self.assertIsNone(caught.exception.__cause__)


class RawHeaderContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.configuration = _configuration()

    def assert_rejected(self, raw_headers: object) -> None:
        self.assertIsNone(
            credentials.derive_request_session_credential(
                raw_headers,  # type: ignore[arg-type]
                self.configuration,
            )
        )

    def test_exact_top_level_tuple_pair_tuple_and_strings_are_required(self):
        class TupleSubclass(tuple):
            pass

        class StringSubclass(str):
            pass

        class Hostile:
            def __len__(self) -> int:
                raise AssertionError("custom length invoked")

            def __iter__(self) -> object:
                raise AssertionError("custom iteration invoked")

            def __repr__(self) -> str:
                raise AssertionError("custom representation invoked")

        for rejected in (
            list(_headers()),
            TupleSubclass(_headers()),
            {"Cookie": _headers()[0][1]},
            Hostile(),
            (list(_headers()[0]),),
            (TupleSubclass(_headers()[0]),),
            (("Cookie",),),
            (("Cookie", _headers()[0][1], "extra"),),
            ((StringSubclass("Cookie"), _headers()[0][1]),),
            (("Cookie", StringSubclass(_headers()[0][1])),),
            ((Hostile(), _headers()[0][1]),),
            (("Cookie", Hostile()),),
        ):
            with self.subTest(rejected_type=type(rejected).__name__):
                self.assert_rejected(rejected)

    def test_pair_count_boundaries(self):
        accepted = tuple((f"X-{index}", "v") for index in range(63)) + _headers()
        rejected = tuple((f"X-{index}", "v") for index in range(64)) + _headers()
        self.assertIsNotNone(
            credentials.derive_request_session_credential(
                accepted,
                self.configuration,
            )
        )
        self.assert_rejected(rejected)
        self.assert_rejected(())

    def test_header_name_token_and_length_contract(self):
        token_name = "!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        for name in (token_name, "A" * 128):
            raw_headers = ((name, "value"),) + _headers(header_name="cOoKiE")
            self.assertIsNotNone(
                credentials.derive_request_session_credential(
                    raw_headers,
                    self.configuration,
                )
            )
        for name in ("", "A" * 129, "bad name", "bad:name", "bad(name)", "é"):
            self.assert_rejected(((name, "value"),) + _headers())

    def test_header_value_utf8_byte_and_total_boundaries(self):
        for value in ("a" * 8192, "é" * 4096):
            self.assertIsNotNone(
                credentials.derive_request_session_credential(
                    (("X-Value", value),) + _headers(),
                    self.configuration,
                )
            )
        for value in ("a" * 8193, "é" * 4097, "\ud800"):
            self.assert_rejected((("X-Value", value),) + _headers())

        cookie_pair = _headers()[0]
        full_headers = [
            ("X-A", "a" * 8192),
            ("X-B", "b" * 8192),
            ("X-C", "c" * 8192),
        ]
        used = sum(len(name) + len(value.encode("utf-8")) for name, value in full_headers)
        used += len(cookie_pair[0]) + len(cookie_pair[1].encode("utf-8"))
        remaining_value_length = 32768 - used - len("X-D")
        self.assertGreaterEqual(remaining_value_length, 0)
        self.assertLessEqual(remaining_value_length, 8192)
        boundary = tuple(full_headers) + (("X-D", "d" * remaining_value_length), cookie_pair)
        self.assertEqual(
            sum(len(name) + len(value.encode("utf-8")) for name, value in boundary),
            32768,
        )
        self.assertIsNotNone(
            credentials.derive_request_session_credential(
                boundary,
                self.configuration,
            )
        )
        over = tuple(full_headers) + (("X-D", "d" * (remaining_value_length + 1)), cookie_pair)
        self.assert_rejected(over)

    def test_every_c0_control_and_del_is_rejected_in_any_value(self):
        for codepoint in (*range(32), 127):
            with self.subTest(codepoint=codepoint):
                self.assert_rejected(
                    (("X-Test", f"before{chr(codepoint)}after"),) + _headers()
                )

    def test_all_headers_are_validated_before_cookie_interpretation(self):
        malformed = _headers() + (("Bad Name", "value"),)
        with mock.patch.object(credentials, "_production_cookie_value") as cookie_parser:
            self.assert_rejected(malformed)
        cookie_parser.assert_not_called()

    def test_malformed_headers_cookies_and_envelopes_do_not_select_keys_or_hmac(self):
        malformed_inputs = (
            [],
            (("Bad Name", "value"),) + _headers(),
            (),
            (("Cookie", ""),),
            (("Cookie", f"{_COOKIE_NAME}=v2.7.11.13.{_COOKIE_SECRET}"),),
            (("Cookie", f"{_COOKIE_NAME}=v1.07.11.13.{_COOKIE_SECRET}"),),
        )
        with mock.patch.object(
            credentials,
            "_select_key",
            wraps=credentials._select_key,
        ) as selector, mock.patch.object(
            credentials._hmac,
            "new",
            wraps=credentials._hmac.new,
        ) as hmac_new:
            for raw_headers in malformed_inputs:
                self.assert_rejected(raw_headers)
        selector.assert_not_called()
        hmac_new.assert_not_called()


class CookieSyntaxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.configuration = _configuration()
        self.envelope = _envelope()

    def derive_cookie(self, value: str) -> credentials.DerivedSessionCredential | None:
        return credentials.derive_request_session_credential(
            (("Cookie", value),),
            self.configuration,
        )

    def test_exactly_one_case_insensitive_cookie_header_is_required(self):
        self.assertIsNone(
            credentials.derive_request_session_credential(
                (("X-Test", "value"),),
                self.configuration,
            )
        )
        self.assertIsNotNone(
            credentials.derive_request_session_credential(
                _headers(header_name="cOoKiE"),
                self.configuration,
            )
        )
        for duplicate_name in ("Cookie", "cookie", "COOKIE"):
            duplicate = _headers() + ((duplicate_name, f"{_COOKIE_NAME}={self.envelope}"),)
            self.assertIsNone(
                credentials.derive_request_session_credential(
                    duplicate,
                    self.configuration,
                )
            )

    def test_cookie_header_ascii_and_size_boundaries(self):
        tail = f"; {_COOKIE_NAME}={self.envelope}"
        padding_length = 8192 - len("x=") - len(tail)
        boundary = "x=" + ("a" * padding_length) + tail
        self.assertEqual(len(boundary.encode("ascii")), 8192)
        self.assertIsNotNone(self.derive_cookie(boundary))
        self.assertIsNone(self.derive_cookie("x=" + ("a" * (padding_length + 1)) + tail))
        self.assertIsNone(self.derive_cookie(f"x=é; {_COOKIE_NAME}={self.envelope}"))

    def test_comma_tab_quote_and_backslash_are_rejected(self):
        for rejected in (
            f"x=a,b; {_COOKIE_NAME}={self.envelope}",
            f"x=a\tb; {_COOKIE_NAME}={self.envelope}",
            f'x="value"; {_COOKIE_NAME}={self.envelope}',
            f"x=a\\b; {_COOKIE_NAME}={self.envelope}",
        ):
            with self.subTest(rejected=rejected[:12]):
                self.assertIsNone(self.derive_cookie(rejected))

    def test_empty_segments_and_noncanonical_separators_are_rejected(self):
        target = f"{_COOKIE_NAME}={self.envelope}"
        for rejected in (
            ";" + target,
            target + ";",
            "x=1;;" + target,
            "x=1; ;" + target,
            "x=1;  " + target,
            "x=1 ;" + target,
            " x=1;" + target,
            "x =1;" + target,
            "x= 1;" + target,
            "x = 1;" + target,
            "x=1; " + target + " ",
        ):
            with self.subTest(rejected=rejected[:16]):
                self.assertIsNone(self.derive_cookie(rejected))

    def test_zero_or_one_space_after_semicolon_is_accepted(self):
        target = f"{_COOKIE_NAME}={self.envelope}"
        for accepted in (
            "first=one;" + target,
            "first=one; " + target,
            "first=one; second=two;" + target,
            "first=;second=a=b; " + target,
            target + ";last=value",
        ):
            with self.subTest(accepted=accepted[:18]):
                self.assertIsNotNone(self.derive_cookie(accepted))

    def test_cookie_names_are_tokens_case_sensitive_and_unique(self):
        target = f"{_COOKIE_NAME}={self.envelope}"
        for rejected in (
            f"bad(name)=value;{target}",
            f"bad name=value;{target}",
            f"unknown=one;unknown=two;{target}",
            f"{target};{target}",
        ):
            self.assertIsNone(self.derive_cookie(rejected))
        self.assertIsNotNone(self.derive_cookie(f"Name=one;name=two;{target}"))
        wrong_case = f"__host-cuevion_session={self.envelope}"
        self.assertIsNone(self.derive_cookie(wrong_case))
        self.assertIsNotNone(self.derive_cookie(f"{wrong_case};{target}"))

    def test_exact_target_cookie_is_required_and_other_sources_are_ignored(self):
        for value in (
            f"other={self.envelope}",
            f"cuevion_session={self.envelope}",
            f"session={self.envelope}",
        ):
            self.assertIsNone(self.derive_cookie(value))
        authorization_only = (("Authorization", f"Bearer {self.envelope}"),)
        self.assertIsNone(
            credentials.derive_request_session_credential(
                authorization_only,
                self.configuration,
            )
        )


class CredentialEnvelopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.configuration = _configuration()

    def rejected(self, envelope: str) -> None:
        self.assertIsNone(
            credentials.derive_request_session_credential(
                _headers(envelope),
                self.configuration,
            )
        )

    def test_exact_valid_v1_envelope(self):
        derived = _derive(self.configuration)
        self.assertEqual(derived.lookup_key_epoch, 7)
        self.assertEqual(derived.binding_key_epoch, 11)
        self.assertEqual(derived.credential_epoch, 13)

    def test_component_count_and_version_are_exact(self):
        for rejected in (
            f"v1.7.11.13",
            f"v1.7.11.13.{_COOKIE_SECRET}.extra",
            f"v1..7.11.13.{_COOKIE_SECRET}",
            f"v2.7.11.13.{_COOKIE_SECRET}",
            f"V1.7.11.13.{_COOKIE_SECRET}",
            f".7.11.13.{_COOKIE_SECRET}",
        ):
            self.rejected(rejected)

    def test_each_epoch_rejects_noncanonical_forms_and_overflow(self):
        rejected_epochs = (
            "",
            "0",
            "00",
            "01",
            "+1",
            "-1",
            " 1",
            "1 ",
            "١",
            "２",
            "2147483648",
            "10000000000",
        )
        for field in ("lookup_epoch", "binding_epoch", "credential_epoch"):
            for value in rejected_epochs:
                arguments = {
                    "lookup_epoch": "7",
                    "binding_epoch": "11",
                    "credential_epoch": "13",
                }
                arguments[field] = value
                with self.subTest(field=field, value=value.encode("unicode_escape")):
                    self.rejected(_envelope(**arguments))

    def test_epoch_boundaries_are_accepted_in_every_position(self):
        for boundary in ("1", "2147483647"):
            configuration = _configuration(
                lookup_epoch=boundary,
                binding_epoch=boundary,
            )
            derived = _derive(
                configuration,
                _envelope(
                    lookup_epoch=boundary,
                    binding_epoch=boundary,
                    credential_epoch=boundary,
                ),
            )
            self.assertEqual(derived.lookup_key_epoch, int(boundary))
            self.assertEqual(derived.binding_key_epoch, int(boundary))
            self.assertEqual(derived.credential_epoch, int(boundary))

    def test_secret_length_alphabet_padding_unicode_and_pad_bits_are_strict(self):
        alias = _noncanonical_pad_bit_alias(_COOKIE_SECRET)
        self.assertEqual(
            base64.urlsafe_b64decode(alias + "="),
            _COOKIE_SECRET_BYTES,
        )
        for secret in (
            "",
            _COOKIE_SECRET[:-1],
            _COOKIE_SECRET + "A",
            _COOKIE_SECRET + "=",
            "+" + _COOKIE_SECRET[1:],
            "/" + _COOKIE_SECRET[1:],
            "*" + _COOKIE_SECRET[1:],
            "é" * 43,
            alias,
        ):
            with self.subTest(length=len(secret)):
                self.rejected(_envelope(secret=secret))

    def test_complete_credential_length_limit(self):
        boundary = "v1." + ("1" * 77) + f".1.1.{_COOKIE_SECRET}"
        overlong = "v1." + ("1" * 78) + f".1.1.{_COOKIE_SECRET}"
        self.assertEqual(len(boundary.encode("ascii")), 128)
        self.assertEqual(len(overlong.encode("ascii")), 129)
        with mock.patch.object(
            credentials,
            "_parse_epoch",
            return_value=1,
        ) as epoch_parser, mock.patch.object(
            credentials,
            "_decode_canonical_32_bytes",
            return_value=_COOKIE_SECRET_BYTES,
        ) as secret_decoder:
            self.assertEqual(
                credentials._credential_components(boundary),
                (1, 1, 1, _COOKIE_SECRET_BYTES),
            )
            epoch_parser.reset_mock()
            secret_decoder.reset_mock()
            self.assertIsNone(credentials._credential_components(overlong))
            epoch_parser.assert_not_called()
            secret_decoder.assert_not_called()
        self.rejected(overlong)

    def test_unknown_or_retired_key_epoch_returns_none_before_hmac(self):
        for envelope in (
            _envelope(lookup_epoch="6"),
            _envelope(binding_epoch="10"),
        ):
            with mock.patch.object(
                credentials._hmac,
                "new",
                wraps=credentials._hmac.new,
            ) as hmac_new:
                self.rejected(envelope)
            hmac_new.assert_not_called()


class DigestDerivationTests(unittest.TestCase):
    def test_fixed_vectors_and_independent_recomputation(self):
        derived = _derive()
        independently_computed = _expected_digests(
            _LOOKUP_CURRENT_BYTES,
            _BINDING_CURRENT_BYTES,
            7,
            11,
            13,
            _COOKIE_SECRET_BYTES,
        )
        self.assertEqual(
            independently_computed,
            (
                "aUwcce_qS7LrUr_XVfKxmPo6aRQUWeYm3bqtHuXKgOw",
                "GdYYGdXG4ql1ATaiC3Otvpcde1TaCxvAZGgWthZzDw0",
            ),
        )
        self.assertEqual(
            derived.credential_lookup_digest,
            independently_computed[0],
        )
        self.assertEqual(
            derived.credential_binding_digest,
            independently_computed[1],
        )
        self.assertNotEqual(
            derived.credential_lookup_digest,
            derived.credential_binding_digest,
        )

    def test_each_epoch_and_secret_are_bound_into_both_digests(self):
        baseline = _derive()
        variants = (
            _derive(
                _configuration(lookup_epoch="8"),
                _envelope(lookup_epoch="8"),
            ),
            _derive(
                _configuration(binding_epoch="12"),
                _envelope(binding_epoch="12"),
            ),
            _derive(envelope=_envelope(credential_epoch="14")),
            _derive(envelope=_envelope(secret=_b64(bytes(range(65, 97))))),
        )
        for variant in variants:
            self.assertNotEqual(
                variant.credential_lookup_digest,
                baseline.credential_lookup_digest,
            )
            self.assertNotEqual(
                variant.credential_binding_digest,
                baseline.credential_binding_digest,
            )

    def test_lookup_and_binding_key_changes_are_independent(self):
        baseline = _derive()
        alternate_lookup_key = _b64(bytes(range(1, 33)))
        lookup_changed = _derive(
            _configuration(lookup_key=alternate_lookup_key)
        )
        self.assertNotEqual(
            lookup_changed.credential_lookup_digest,
            baseline.credential_lookup_digest,
        )
        self.assertEqual(
            lookup_changed.credential_binding_digest,
            baseline.credential_binding_digest,
        )

        alternate_binding_key = _b64(bytes(range(33, 65)))
        binding_changed = _derive(
            _configuration(binding_key=alternate_binding_key)
        )
        self.assertEqual(
            binding_changed.credential_lookup_digest,
            baseline.credential_lookup_digest,
        )
        self.assertNotEqual(
            binding_changed.credential_binding_digest,
            baseline.credential_binding_digest,
        )

    def test_current_previous_key_families_select_independently(self):
        configuration = _configuration(
            previous_lookup=True,
            previous_binding=True,
        )
        lookup_keys = {7: _LOOKUP_CURRENT_BYTES, 5: _LOOKUP_PREVIOUS_BYTES}
        binding_keys = {11: _BINDING_CURRENT_BYTES, 9: _BINDING_PREVIOUS_BYTES}
        for lookup_epoch in (7, 5):
            for binding_epoch in (11, 9):
                with self.subTest(
                    lookup_epoch=lookup_epoch,
                    binding_epoch=binding_epoch,
                ):
                    derived = _derive(
                        configuration,
                        _envelope(
                            lookup_epoch=str(lookup_epoch),
                            binding_epoch=str(binding_epoch),
                        ),
                    )
                    expected = _expected_digests(
                        lookup_keys[lookup_epoch],
                        binding_keys[binding_epoch],
                        lookup_epoch,
                        binding_epoch,
                        13,
                        _COOKIE_SECRET_BYTES,
                    )
                    self.assertEqual(
                        derived.credential_lookup_digest,
                        expected[0],
                    )
                    self.assertEqual(
                        derived.credential_binding_digest,
                        expected[1],
                    )

    def test_request_supplied_digest_cookie_does_not_affect_output(self):
        baseline = _derive()
        claimed = "A" * 43
        derived = _derive(
            prefix=(
                f"credential_lookup_digest={claimed}; "
                f"credential_binding_digest={claimed}; "
            )
        )
        self.assertEqual(
            derived.credential_lookup_digest,
            baseline.credential_lookup_digest,
        )
        self.assertEqual(
            derived.credential_binding_digest,
            baseline.credential_binding_digest,
        )

    def test_exact_binary_framing_domains_two_hmacs_and_no_comparison(self):
        expected_frame = (
            b"\x00\x00\x00\x02v1"
            b"\x00\x00\x00\x017"
            b"\x00\x00\x00\x0211"
            b"\x00\x00\x00\x0213"
            b"\x00\x00\x00\x20" + _COOKIE_SECRET_BYTES
        )
        self.assertEqual(
            credentials._frame(
                (b"v1", b"7", b"11", b"13", _COOKIE_SECRET_BYTES)
            ),
            expected_frame,
        )
        calls: list[tuple[bytes, bytes, object]] = []
        original_new = credentials._hmac.new

        def recording_new(key: bytes, message: bytes, *, digestmod: object) -> object:
            calls.append((key, message, digestmod))
            return original_new(key, message, digestmod=digestmod)

        with mock.patch.object(credentials._hmac, "new", side_effect=recording_new), mock.patch.object(
            credentials._hmac,
            "compare_digest",
            wraps=credentials._hmac.compare_digest,
        ) as compare_digest:
            _derive()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], _LOOKUP_CURRENT_BYTES)
        self.assertEqual(calls[1][0], _BINDING_CURRENT_BYTES)
        self.assertEqual(
            calls[0][1],
            b"cuevion/auth/session-lookup/v1\x00" + expected_frame,
        )
        self.assertEqual(
            calls[1][1],
            b"cuevion/auth/session-binding/v1\x00" + expected_frame,
        )
        self.assertIs(calls[0][2], hashlib.sha256)
        self.assertIs(calls[1][2], hashlib.sha256)
        compare_digest.assert_not_called()


class DerivedCredentialOpacityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.derived = _derive()

    def test_exact_type_slots_and_read_only_exact_properties(self):
        self.assertIs(type(self.derived), credentials.DerivedSessionCredential)
        self.assertFalse(hasattr(self.derived, "__dict__"))
        self.assertEqual(
            self.derived.__slots__,
            (
                "_sentinel",
                "_lookup_key_epoch",
                "_binding_key_epoch",
                "_credential_epoch",
                "_credential_lookup_digest",
                "_credential_binding_digest",
            ),
        )
        expected = {
            "lookup_key_epoch": (int, 7),
            "binding_key_epoch": (int, 11),
            "credential_epoch": (int, 13),
            "credential_lookup_digest": (
                str,
                "aUwcce_qS7LrUr_XVfKxmPo6aRQUWeYm3bqtHuXKgOw",
            ),
            "credential_binding_digest": (
                str,
                "GdYYGdXG4ql1ATaiC3Otvpcde1TaCxvAZGgWthZzDw0",
            ),
        }
        public_properties = {
            name: value
            for name, value in vars(credentials.DerivedSessionCredential).items()
            if isinstance(value, property)
        }
        self.assertEqual(set(public_properties), set(expected))
        for name, (expected_type, expected_value) in expected.items():
            value = getattr(self.derived, name)
            self.assertIs(type(value), expected_type)
            self.assertEqual(value, expected_value)
            with self.assertRaises(AttributeError):
                setattr(self.derived, name, value)

    def test_direct_construction_subclassing_and_mutation_fail(self):
        for arguments in ((), (object(),), (1, 2, 3, "a", "b")):
            with self.assertRaisesRegex(
                TypeError,
                "derived session credentials are factory-controlled",
            ):
                credentials.DerivedSessionCredential(*arguments)
        with self.assertRaises(TypeError):
            type(
                "DerivedSubclass",
                (credentials.DerivedSessionCredential,),
                {},
            )
        with self.assertRaises(AttributeError):
            self.derived.extra = "value"  # type: ignore[attr-defined]
        with self.assertRaises(AttributeError):
            del self.derived._credential_lookup_digest

    def test_partial_exact_object_never_becomes_usable_authority(self):
        partial = object.__new__(credentials.DerivedSessionCredential)
        properties = (
            "lookup_key_epoch",
            "binding_key_epoch",
            "credential_epoch",
            "credential_lookup_digest",
            "credential_binding_digest",
        )

        self.assertIs(type(partial), credentials.DerivedSessionCredential)
        self.assertFalse(hasattr(partial, "__dict__"))
        self.assertEqual(repr(partial), "<DerivedSessionCredential>")
        self.assertEqual(str(partial), "DerivedSessionCredential")
        for slot in credentials.DerivedSessionCredential.__slots__:
            self.assertFalse(hasattr(partial, slot))
        for property_name in properties:
            with self.subTest(property=property_name):
                with self.assertRaises(AttributeError):
                    getattr(partial, property_name)

        copied = copy.copy(partial)
        deep_copied = copy.deepcopy(partial)
        self.assertIs(copied, partial)
        self.assertIs(deep_copied, partial)
        for candidate in (copied, deep_copied):
            for property_name in properties:
                with self.assertRaises(AttributeError):
                    getattr(candidate, property_name)

        with self.assertRaises(TypeError):
            dataclasses.asdict(partial)  # type: ignore[arg-type]
        for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
            with self.assertRaises(TypeError):
                pickle.dumps(partial, protocol=protocol)

        with self.assertRaises(credentials.SessionKeyConfigurationError):
            credentials.parse_session_key_configuration(partial)  # type: ignore[arg-type]
        with self.assertRaises(credentials.SessionKeyConfigurationError):
            credentials.derive_request_session_credential(
                _headers(),
                partial,  # type: ignore[arg-type]
            )

    def test_identity_copy_dataclass_pickle_and_value_free_rendering(self):
        second = _derive()
        self.assertIsNot(second, self.derived)
        self.assertNotEqual(second, self.derived)
        self.assertEqual(hash(self.derived), hash(self.derived))
        self.assertIs(copy.copy(self.derived), self.derived)
        self.assertIs(copy.deepcopy(self.derived), self.derived)
        self.assertFalse(dataclasses.is_dataclass(self.derived))
        with self.assertRaises(TypeError):
            dataclasses.asdict(self.derived)  # type: ignore[arg-type]
        self.assertEqual(repr(self.derived), "<DerivedSessionCredential>")
        self.assertEqual(str(self.derived), "DerivedSessionCredential")
        for sensitive in (
            _COOKIE_SECRET,
            self.derived.credential_lookup_digest,
            self.derived.credential_binding_digest,
        ):
            self.assertNotIn(sensitive, repr(self.derived))
            self.assertNotIn(sensitive, str(self.derived))
        for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
            with self.assertRaises(TypeError):
                pickle.dumps(self.derived, protocol=protocol)
        with self.assertRaises(TypeError):
            self.derived.__getstate__()
        with self.assertRaises(TypeError):
            self.derived.__setstate__({"secret": _COOKIE_SECRET})

    def test_no_secret_header_key_or_authority_surface(self):
        forbidden = (
            "secret",
            "cookie",
            "raw_header",
            "lookup_key",
            "binding_key",
            "session_id",
            "user_id",
            "email",
            "workspace",
            "role",
            "provider",
            "account",
            "access_token",
            "refresh_token",
            "id_token",
            "oauth_code",
            "otp",
            "password",
            "magic_link",
            "challenge_secret",
            "pkce_verifier",
            "mailbox",
            "encryption_key",
        )
        allowed = {
            "lookup_key_epoch",
            "binding_key_epoch",
            "credential_epoch",
            "credential_lookup_digest",
            "credential_binding_digest",
        }
        public = {name for name in dir(self.derived) if not name.startswith("_")}
        self.assertEqual(public, allowed)
        for name in public:
            if name in {"lookup_key_epoch", "binding_key_epoch"}:
                continue
            for fragment in forbidden:
                self.assertNotIn(fragment, name.casefold())


class UniformRejectionAndSurfaceTests(unittest.TestCase):
    def test_malformed_and_ambiguous_requests_return_exactly_none_without_output(self):
        configuration = _configuration()
        private_value = "request-private-value"
        malformed: tuple[object, ...] = (
            None,
            [],
            (),
            (("Cookie", ""),),
            (("Cookie", private_value),),
            (("Cookie", f"{_COOKIE_NAME}={private_value}"),),
            (("Cookie", f"{_COOKIE_NAME}={_envelope()}; {_COOKIE_NAME}={_envelope()}"),),
            (("Cookie", f"unknown=1;unknown=2;{_COOKIE_NAME}={_envelope()}"),),
            (("Cookie", f"{_COOKIE_NAME}=v2.7.11.13.{_COOKIE_SECRET}"),),
            (("Cookie", f"{_COOKIE_NAME}=v1.99.11.13.{_COOKIE_SECRET}"),),
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            for raw_headers in malformed:
                with self.subTest(raw_headers_type=type(raw_headers).__name__):
                    result = credentials.derive_request_session_credential(
                        raw_headers,  # type: ignore[arg-type]
                        configuration,
                    )
                    self.assertIs(result, None)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn(private_value, stdout.getvalue() + stderr.getvalue())

    def test_invalid_configuration_is_never_request_none(self):
        for invalid in (None, object(), {}, _configuration_values()):
            with self.subTest(invalid_type=type(invalid).__name__):
                with self.assertRaises(credentials.SessionKeyConfigurationError) as caught:
                    credentials.derive_request_session_credential(
                        (),
                        invalid,  # type: ignore[arg-type]
                    )
                self.assertEqual(caught.exception.args, ())
                self.assertIsNone(caught.exception.__context__)
                self.assertIsNone(caught.exception.__cause__)

    def test_public_sensitive_data_surface_is_narrow(self):
        forbidden = (
            "raw_cookie",
            "cookie_secret",
            "raw_cookie_value",
            "bearer",
            "access_token",
            "refresh_token",
            "id_token",
            "oauth_code",
            "otp",
            "password",
            "magic_link",
            "challenge_secret",
            "pkce_verifier",
            "mailbox_credential",
            "encryption_key",
        )
        public_functions = (
            credentials.parse_session_key_configuration,
            credentials.derive_request_session_credential,
        )
        for function in public_functions:
            signature = inspect.signature(function)
            names = (function.__name__, *signature.parameters)
            for name in names:
                for fragment in forbidden:
                    self.assertNotIn(fragment, name.casefold())
        self.assertIn(
            "raw_headers",
            inspect.signature(
                credentials.derive_request_session_credential
            ).parameters,
        )


class ActivationDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.documentation = _DOCUMENTATION_PATH.read_text(encoding="utf-8")
        cls.normalized = " ".join(cls.documentation.casefold().split())

    def test_inactivity_identity_and_cookie_requirements_are_documented(self):
        required = (
            "auth-b1a is a pure, provider-independent session-credential boundary",
            "this slice is completely inactive",
            "defines no resolver, account repository, session repository, storage adapter, database, redis or kv access",
            "environment-variable loader",
            "session creation, rotation, revocation, idle touch",
            "http route, handler, app, router, cookie emitter, frontend integration",
            "beta integration, mailbox integration, team integration, collaboration integration, or feature activation",
            "only successful production-module identity is `cuevion_auth.session_credentials`",
            "implicit namespace package with no `__init__.py`",
            "duplicate execution through a second canonical spec",
            "reload or equivalent re-execution",
            "not a security boundary against arbitrary code",
            "deliberately replaces or mutates `sys.modules`",
            "exactly one case-insensitive `cookie` header",
            "`__host-cuevion_session`",
            "parses this cookie but never emits it",
            "`secure`, `httponly`, `path=/`, and `samesite=lax`",
            "must omit `domain`",
            "there is no beta, bearer, provider, mailbox, or stateless fallback",
        )
        for statement in required:
            with self.subTest(statement=statement):
                self.assertIn(statement, self.normalized)

    def test_header_cookie_envelope_and_configuration_contracts_are_documented(self):
        required = (
            "exact built-in `tuple` of exact two-entry built-in tuples containing exact built-in strings",
            "at most 64 header pairs",
            "1 through 128 ascii characters and uses rfc token characters only",
            "each value is at most 8192 utf-8 bytes",
            "sum of encoded name and value bytes is at most 32768",
            "cr, lf, nul, every c0 control, and del",
            "complete header structure is validated before cookie interpretation, key selection, or hmac",
            "pairs use exactly `;` followed by either no space or one ascii space",
            "every repeated cookie name is rejected",
            "exact envelope grammar is `v1.<lookup-key-epoch>.<binding-key-epoch>.<credential-epoch>.<secret>`",
            "at most 128 bytes",
            "43 unpadded base64url characters",
            "noncanonical trailing pad-bit aliases are rejected",
            "`lookup_current_epoch`",
            "`lookup_previous_key`",
            "`binding_current_epoch`",
            "`binding_previous_key`",
            "all configured raw keys must be pairwise different",
            "one fixed `sessionkeyconfigurationerror`",
            "invalid trusted configuration never becomes a request-level `none`",
        )
        for statement in required:
            with self.subTest(statement=statement):
                self.assertIn(statement, self.normalized)

    def test_traceback_and_decoder_failure_boundaries_are_documented(self):
        required = (
            "its args, repr, and str are value-free, and its `__cause__` and `__context__` are `none`",
            "module-owned configuration-error traceback frames are designed not to retain the supplied configuration dictionary, supplied encoded keys, decoded key bytes, or a private underlying exception",
            "this guarantee does not extend to arbitrary caller frames outside the module",
            "configuration callers and telemetry must never capture or log caller locals containing configuration secrets",
            "expected malformed request-controlled base64url encoding also returns exactly `none`",
            "an unexpected request decoder failure is an internal implementation failure",
            "must not be classified as missing authentication",
            "future resolver must map such an unexpected request-boundary failure to fixed `internal_error`",
            "unexpected decoder failure while parsing trusted configuration is sanitized to the fixed value-free `sessionkeyconfigurationerror`",
        )
        for statement in required:
            with self.subTest(statement=statement):
                self.assertIn(statement, self.normalized)

    def test_derivation_opacity_rotation_and_future_review_are_documented(self):
        required = (
            "unsigned 32-bit big-endian integer",
            "cuevion/auth/session-lookup/v1",
            "cuevion/auth/session-binding/v1",
            "lookup and binding current/previous selection occurs independently",
            "request never supplies a trusted digest",
            "retains no raw secret, cookie value, cookie header, raw headers, or key material",
            "this slice performs no digest comparison",
            "compare the authoritative stored binding digest with the independently derived expected binding digest in constant time",
            "one current and at most one previous key per lookup or binding domain",
            "later issuance must use both current epochs",
            "recommended schedule of every 90 days",
            "maximum absolute session lifetime plus deployment overlap",
            "maximum absolute session lifetime shorter than the rotation interval",
            "deploy the new current plus old previous configuration everywhere before later issuing credentials",
            "remove a previous key only after no affected credential can remain valid",
            "reject the affected epoch and require fresh login and session invalidation",
            "production and preview keys, epochs, and namespaces fully separate",
            "auth-b1b requires a separate review",
            "auth-b2 requires separately reviewed repositories and production storage",
            "explicit activation decision",
        )
        for statement in required:
            with self.subTest(statement=statement):
                self.assertIn(statement, self.normalized)

    def test_documentation_contains_no_deployable_key_or_credential_example(self):
        credential_pattern = re.compile(
            r"v1\.[1-9][0-9]*\.[1-9][0-9]*\.[1-9][0-9]*\.[A-Za-z0-9_-]{43}"
        )
        self.assertIsNone(credential_pattern.search(self.documentation))
        assignments = re.findall(
            r"(?:lookup|binding)_(?:current|previous)_key\s*=\s*([A-Za-z0-9_-]{43})",
            self.documentation,
        )
        self.assertEqual(assignments, [])
        standalone_material = re.findall(
            r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])",
            self.documentation,
        )
        self.assertEqual(standalone_material, [])


if __name__ == "__main__":
    unittest.main()
