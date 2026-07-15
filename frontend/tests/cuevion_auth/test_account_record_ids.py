"""Security tests for inactive account-record ID candidate generation."""

import ast
import base64
import importlib
import inspect
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import unittest
from unittest import mock

from api.auth import models as auth_models
from cuevion_auth import account_record_ids as identifiers


_TEST_DIRECTORY = Path(__file__).resolve().parent
_FRONTEND_DIRECTORY = _TEST_DIRECTORY.parents[1]
_SOURCE_DIRECTORY = _FRONTEND_DIRECTORY / "cuevion_auth"
_SOURCE_PATH = _SOURCE_DIRECTORY / "account_record_ids.py"
_DOCUMENTATION_PATH = _SOURCE_DIRECTORY / "AUTH_RECORD_ID_ACTIVATION_REQUIREMENTS.md"

_GENERATORS = (
    (identifiers.generate_user_id_candidate, "usr_", auth_models._valid_user_id),
    (
        identifiers.generate_verified_email_id_candidate,
        "vem_",
        auth_models._valid_verified_email_id,
    ),
    (
        identifiers.generate_authentication_identity_id_candidate,
        "aid_",
        auth_models._valid_authentication_identity_id,
    ),
    (
        identifiers.generate_workspace_id_candidate,
        "wsp_",
        auth_models._valid_workspace_id,
    ),
)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


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
    return f"""
import atexit
import base64
import builtins
import http.client
import importlib
import importlib.util
import io
import logging
import os
import random
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

target = 'cuevion_auth.account_record_ids'
source_path = {source_path!r}
source_directory = {source_directory!r}
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
    raise AssertionError('forbidden import side effect')

filesystem_events = []
network_or_process_events = []
def audit(event, _arguments):
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
os.urandom = blocked
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
for name in ('random', 'getrandbits', 'randbytes'):
    if hasattr(random, name):
        setattr(random, name, blocked)
for name in ('token_bytes', 'token_hex', 'token_urlsafe', 'randbelow'):
    setattr(secrets, name, blocked)
for name in ('uuid1', 'uuid4'):
    setattr(uuid, name, blocked)
for name in ('time', 'time_ns', 'monotonic', 'perf_counter'):
    if hasattr(time, name):
        setattr(time, name, blocked)
for name in (
    'getLogger', 'debug', 'info', 'warning', 'error', 'exception', 'critical',
    'log', 'basicConfig',
):
    setattr(logging, name, blocked)
logging.Logger._log = blocked
base64.urlsafe_b64encode = blocked
base64.b64decode = blocked

allowed_imports = {{'sys', 'base64', 'secrets'}}
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
captured_stdout = io.StringIO()
captured_stderr = io.StringIO()
original_stdout = sys.stdout
original_stderr = sys.stderr
try:
    sys.stdout = captured_stdout
    sys.stderr = captured_stderr
    module = importlib.import_module(target)
finally:
    sys.stdout = original_stdout
    sys.stderr = original_stderr

assert tuple(sys.path) == path_before
assert module is sys.modules[target]
assert set(sys.modules) - modules_before == {{'cuevion_auth', target}}
assert production_imports == ['sys', 'base64', 'secrets']
assert [name for name, value in sys.modules.items() if value is module] == [target]
assert module.__name__ == target
assert module.__package__ == 'cuevion_auth'
assert module.__spec__.name == target
assert module._token_bytes is blocked
assert captured_stdout.getvalue() == ''
assert captured_stderr.getvalue() == ''
for forbidden_surface in ('handler', 'route', 'router', 'app'):
    assert not hasattr(module, forbidden_surface)
assert side_effects == []
assert network_or_process_events == []
for event, caller in filesystem_events:
    assert caller == 'importlib._bootstrap_external', (event, caller)
"""


def _runtime_inactivity_program() -> str:
    return """
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

module = importlib.import_module('cuevion_auth.account_record_ids')
module_keys_before = frozenset(module.__dict__)
entropy_calls = []
entropy_values = [bytes((value,)) * 16 for value in (1, 2, 3, 4)]
def deterministic_entropy(length):
    entropy_calls.append(length)
    return entropy_values[len(entropy_calls) - 1]
module._token_bytes = deterministic_entropy

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
    raise AssertionError('forbidden runtime side effect')

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
        raise AssertionError('forbidden audited runtime side effect')
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
os.urandom = blocked
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
uuid.uuid1 = blocked
uuid.uuid4 = blocked
for logging_name in (
    'getLogger', 'debug', 'info', 'warning', 'error', 'exception', 'critical',
    'log', 'basicConfig',
):
    setattr(logging, logging_name, blocked)
logging.Logger._log = blocked

captured_stdout = io.StringIO()
captured_stderr = io.StringIO()
original_stdout = sys.stdout
original_stderr = sys.stderr
try:
    sys.stdout = captured_stdout
    sys.stderr = captured_stderr
    generated = (
        module.generate_user_id_candidate(),
        module.generate_verified_email_id_candidate(),
        module.generate_authentication_identity_id_candidate(),
        module.generate_workspace_id_candidate(),
    )
    module._token_bytes = lambda _length: None
    try:
        module.generate_user_id_candidate()
    except module.RecordIdentifierGenerationError as error:
        assert error.args == ()
        assert error.__context__ is None
        assert error.__cause__ is None
    else:
        raise AssertionError('invalid entropy was accepted')
    def failed_entropy(_length):
        raise RuntimeError('private runtime marker')
    module._token_bytes = failed_entropy
    try:
        module.generate_workspace_id_candidate()
    except module.RecordIdentifierGenerationError as error:
        assert 'private runtime marker' not in str(error)
        assert 'private runtime marker' not in repr(error)
    else:
        raise AssertionError('entropy failure was accepted')
finally:
    sys.stdout = original_stdout
    sys.stderr = original_stderr

assert all(type(value) is str for value in generated)
assert entropy_calls == [16, 16, 16, 16]
assert captured_stdout.getvalue() == ''
assert captured_stderr.getvalue() == ''
assert side_effects == []
assert audit_events == []
assert frozenset(module.__dict__) == module_keys_before
for forbidden_surface in ('handler', 'route', 'router', 'app'):
    assert not hasattr(module, forbidden_surface)
"""


class ModuleIdentityAndInactivityTests(unittest.TestCase):
    def test_canonical_identity_namespace_and_no_aliases(self):
        self.assertEqual(
            identifiers.__name__,
            "cuevion_auth.account_record_ids",
        )
        self.assertEqual(identifiers.__package__, "cuevion_auth")
        self.assertEqual(
            identifiers.__spec__.name,
            "cuevion_auth.account_record_ids",
        )
        self.assertIs(
            identifiers,
            sys.modules["cuevion_auth.account_record_ids"],
        )
        self.assertEqual(
            [name for name, value in sys.modules.items() if value is identifiers],
            ["cuevion_auth.account_record_ids"],
        )
        self.assertFalse((_SOURCE_DIRECTORY / "__init__.py").exists())
        self.assertFalse((_TEST_DIRECTORY / "__init__.py").exists())
        self.assertFalse((_FRONTEND_DIRECTORY / "tests" / "__init__.py").exists())

    def test_top_level_and_alternate_dotted_imports_fail(self):
        attempts = (
            (str(_SOURCE_DIRECTORY), "account_record_ids"),
            (
                str(_FRONTEND_DIRECTORY.parent),
                "frontend.cuevion_auth.account_record_ids",
            ),
        )
        for path_entry, module_name in attempts:
            with self.subTest(module_name=module_name):
                program = (
                    "import importlib,sys\n"
                    "original=importlib.import_module('cuevion_auth.account_record_ids')\n"
                    "identities=(original.RecordIdentifierGenerationError,original.generate_user_id_candidate,original.generate_verified_email_id_candidate,original.generate_authentication_identity_id_candidate,original.generate_workspace_id_candidate)\n"
                    "sentinels=(original._GENERATION_FAILURE,original._ERROR_CONSTRUCTION_SENTINEL)\n"
                    "original._token_bytes=lambda length: bytes(length)\n"
                    "path_before=tuple(sys.path)\n"
                    f"sys.path.insert(0,{path_entry!r})\n"
                    "try:\n"
                    f" importlib.import_module({module_name!r})\n"
                    "except ImportError:\n"
                    " pass\n"
                    "else:\n"
                    " raise SystemExit('alternate identity unexpectedly succeeded')\n"
                    "assert sys.modules['cuevion_auth.account_record_ids'] is original\n"
                    "assert identities == (original.RecordIdentifierGenerationError,original.generate_user_id_candidate,original.generate_verified_email_id_candidate,original.generate_authentication_identity_id_candidate,original.generate_workspace_id_candidate)\n"
                    "assert sentinels == (original._GENERATION_FAILURE,original._ERROR_CONSTRUCTION_SENTINEL)\n"
                    "assert original.generate_user_id_candidate().startswith('usr_')\n"
                    "assert tuple(sys.path[1:]) == path_before\n"
                )
                completed = _run_isolated(program)
                self.assertEqual(
                    completed.returncode,
                    0,
                    msg=completed.stdout + completed.stderr,
                )

    def test_duplicate_canonical_spec_and_reload_fail_before_redefinition(self):
        program = (
            "import importlib,importlib.util,sys\n"
            "original=importlib.import_module('cuevion_auth.account_record_ids')\n"
            "identities=(original.RecordIdentifierGenerationError,original.generate_user_id_candidate,original.generate_verified_email_id_candidate,original.generate_authentication_identity_id_candidate,original.generate_workspace_id_candidate)\n"
            "sentinels=(original._GENERATION_FAILURE,original._ERROR_CONSTRUCTION_SENTINEL)\n"
            "original._token_bytes=lambda length: bytes(length)\n"
            "def assert_original_usable():\n"
            " assert sys.modules['cuevion_auth.account_record_ids'] is original\n"
            " assert identities == (original.RecordIdentifierGenerationError,original.generate_user_id_candidate,original.generate_verified_email_id_candidate,original.generate_authentication_identity_id_candidate,original.generate_workspace_id_candidate)\n"
            " assert sentinels == (original._GENERATION_FAILURE,original._ERROR_CONSTRUCTION_SENTINEL)\n"
            " assert original.generate_workspace_id_candidate().startswith('wsp_')\n"
            f"path={str(_SOURCE_PATH)!r}\n"
            "for spec_name in ('cuevion_auth.alternate_account_record_ids','cuevion_auth.account_record_ids'):\n"
            " spec=importlib.util.spec_from_file_location(spec_name,path)\n"
            " duplicate=importlib.util.module_from_spec(spec)\n"
            " try:\n"
            "  spec.loader.exec_module(duplicate)\n"
            " except ImportError:\n"
            "  pass\n"
            " else:\n"
            "  raise SystemExit('duplicate spec unexpectedly succeeded')\n"
            " assert '_AUTH_B1B_ACCOUNT_RECORD_IDS_INITIALIZED' not in duplicate.__dict__\n"
            " for name in ('RecordIdentifierGenerationError','_GENERATION_FAILURE','_ERROR_CONSTRUCTION_SENTINEL','generate_user_id_candidate','generate_verified_email_id_candidate','generate_authentication_identity_id_candidate','generate_workspace_id_candidate'):\n"
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

    def test_genuine_cold_import_has_no_forbidden_side_effects(self):
        completed = _run_isolated(_cold_import_program())
        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stdout + completed.stderr,
        )
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")

    def test_public_operations_have_no_forbidden_runtime_side_effects(self):
        completed = _run_isolated(_runtime_inactivity_program())
        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stdout + completed.stderr,
        )
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")


class PublicSurfaceAndFormatTests(unittest.TestCase):
    def test_exact_standard_library_imports_and_public_surface(self):
        tree = ast.parse(_SOURCE_PATH.read_text(encoding="utf-8"))
        imports: set[tuple[int, str | None]] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update((0, alias.name) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add((node.level, node.module))
        self.assertEqual(
            imports,
            {(0, "sys"), (0, "base64"), (0, "secrets")},
        )

        expected_public = {
            "RecordIdentifierGenerationError",
            "generate_user_id_candidate",
            "generate_verified_email_id_candidate",
            "generate_authentication_identity_id_candidate",
            "generate_workspace_id_candidate",
        }
        public = {
            name: value
            for name, value in vars(identifiers).items()
            if not name.startswith("_")
        }
        self.assertEqual(set(public), expected_public)
        self.assertEqual(set(identifiers.__all__), expected_public)
        self.assertEqual(
            identifiers.__all__,
            (
                "RecordIdentifierGenerationError",
                "generate_user_id_candidate",
                "generate_verified_email_id_candidate",
                "generate_authentication_identity_id_candidate",
                "generate_workspace_id_candidate",
            ),
        )
        for forbidden in ("handler", "route", "router", "app"):
            self.assertNotIn(forbidden, vars(identifiers))

    def test_exact_no_argument_signatures_and_no_sensitive_generator_surface(self):
        generator_functions = tuple(item[0] for item in _GENERATORS)
        for generator in generator_functions:
            with self.subTest(generator=generator.__name__):
                signature = inspect.signature(generator)
                self.assertEqual(tuple(signature.parameters), ())
                self.assertIs(signature.return_annotation, str)
                self.assertFalse(
                    any(
                        parameter.default is not inspect.Parameter.empty
                        or parameter.kind
                        in (
                            inspect.Parameter.VAR_POSITIONAL,
                            inspect.Parameter.VAR_KEYWORD,
                        )
                        for parameter in signature.parameters.values()
                    )
                )

        public_names = {name.casefold() for name in identifiers.__all__}
        for forbidden in (
            "session",
            "cookie",
            "secret",
            "credential",
            "token",
            "digest",
            "epoch",
            "batch",
            "retry",
            "generic",
        ):
            self.assertTrue(
                all(forbidden not in name for name in public_names),
                msg=forbidden,
            )

    def test_deterministic_vectors_are_canonical_and_auth_a_compatible(self):
        entropy_values = (
            bytes(16),
            b"\xff" * 16,
            bytes((0, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 17, 29, 47)),
        )
        for generator, prefix, validator in _GENERATORS:
            for entropy in entropy_values:
                with self.subTest(generator=generator.__name__, entropy=entropy.hex()):
                    with mock.patch.object(
                        identifiers,
                        "_token_bytes",
                        return_value=entropy,
                    ) as entropy_source:
                        candidate = generator()
                    entropy_source.assert_called_once_with(16)
                    expected_suffix = _b64(entropy)
                    self.assertIs(type(candidate), str)
                    self.assertEqual(candidate, prefix + expected_suffix)
                    self.assertEqual(len(candidate), 26)
                    suffix = candidate[len(prefix) :]
                    self.assertEqual(len(suffix), 22)
                    self.assertTrue(suffix.isascii())
                    self.assertIsNone(re.search(r"[^A-Za-z0-9_-]", suffix))
                    self.assertNotIn("=", suffix)
                    decoded = base64.b64decode(
                        suffix.encode("ascii") + b"==",
                        altchars=b"-_",
                        validate=True,
                    )
                    self.assertEqual(decoded, entropy)
                    self.assertEqual(_b64(decoded), suffix)
                    self.assertTrue(validator(candidate))


class EntropyAndFailureTests(unittest.TestCase):
    def assert_generation_error(
        self,
        callable_object: object,
        *,
        private_markers: tuple[str, ...] = (),
    ) -> identifiers.RecordIdentifierGenerationError:
        try:
            callable_object()  # type: ignore[operator]
        except identifiers.RecordIdentifierGenerationError as error:
            self.assertIs(type(error), identifiers.RecordIdentifierGenerationError)
            self.assertEqual(error.args, ())
            self.assertEqual(
                str(error),
                "account record identifier generation failed",
            )
            self.assertEqual(repr(error), "RecordIdentifierGenerationError()")
            self.assertIsNone(error.__context__)
            self.assertIsNone(error.__cause__)
            for marker in private_markers:
                self.assertNotIn(marker, str(error))
                self.assertNotIn(marker, repr(error))
            return error
        self.fail("generation failure was not raised")

    def test_direct_error_construction_and_subclassing_are_unsupported(self):
        for arguments in ((), ("private",), (object(),)):
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(
                    TypeError,
                    "record identifier generation errors require the supported raising function",
                ):
                    identifiers.RecordIdentifierGenerationError(*arguments)
        with self.assertRaises(TypeError):
            type(
                "ErrorSubclass",
                (identifiers.RecordIdentifierGenerationError,),
                {},
            )

    def test_each_generator_requests_exactly_one_fresh_sixteen_byte_draw(self):
        for generator, _prefix, _validator in _GENERATORS:
            with self.subTest(generator=generator.__name__):
                with mock.patch.object(
                    identifiers,
                    "_token_bytes",
                    return_value=bytes(range(16)),
                ) as entropy_source:
                    generator()
                entropy_source.assert_called_once_with(16)

        entropy_values = [bytes((index,)) * 16 for index in range(1, 5)]
        with mock.patch.object(
            identifiers,
            "_token_bytes",
            side_effect=entropy_values,
        ) as entropy_source:
            results = tuple(generator() for generator, _prefix, _validator in _GENERATORS)
        self.assertEqual(entropy_source.call_args_list, [mock.call(16)] * 4)
        for result, entropy, (_generator, prefix, _validator) in zip(
            results,
            entropy_values,
            _GENERATORS,
        ):
            self.assertEqual(result, prefix + _b64(entropy))
        self.assertEqual(len(set(results)), 4)

    def test_invalid_entropy_types_and_lengths_fail_without_coercion(self):
        class BytesSubclass(bytes):
            pass

        class HostileValue:
            def __bytes__(self) -> bytes:
                raise AssertionError("custom bytes conversion invoked")

            def __str__(self) -> str:
                raise AssertionError("custom string conversion invoked")

            def __repr__(self) -> str:
                raise AssertionError("custom representation invoked")

            def __iter__(self) -> object:
                raise AssertionError("custom iteration invoked")

            def __len__(self) -> int:
                raise AssertionError("custom length invoked")

            def __eq__(self, other: object) -> bool:
                raise AssertionError("custom equality invoked")

            def __hash__(self) -> int:
                raise AssertionError("custom hashing invoked")

        invalid_values = (
            None,
            "x" * 16,
            bytearray(16),
            memoryview(bytes(16)),
            HostileValue(),
            BytesSubclass(bytes(16)),
            b"",
            bytes(15),
            bytes(17),
            bytes(32),
        )
        for generator, _prefix, _validator in _GENERATORS:
            for invalid in invalid_values:
                with self.subTest(
                    generator=generator.__name__,
                    invalid_type=type(invalid).__name__,
                    invalid_length=(len(invalid) if type(invalid) is bytes else None),
                ):
                    with mock.patch.object(
                        identifiers,
                        "_token_bytes",
                        return_value=invalid,
                    ) as entropy_source:
                        self.assert_generation_error(generator)
                    entropy_source.assert_called_once_with(16)

    def test_ordinary_entropy_failures_are_fixed_and_never_retried(self):
        class PrivateFailure(Exception):
            pass

        for exception in (
            RuntimeError("private-runtime-marker"),
            ValueError("private-value-marker"),
            PrivateFailure("private-custom-marker"),
        ):
            for generator, _prefix, _validator in _GENERATORS:
                with self.subTest(
                    generator=generator.__name__,
                    exception=type(exception).__name__,
                ):
                    with mock.patch.object(
                        identifiers,
                        "_token_bytes",
                        side_effect=exception,
                    ) as entropy_source:
                        self.assert_generation_error(
                            generator,
                            private_markers=(str(exception),),
                        )
                    entropy_source.assert_called_once_with(16)

    def test_baseexception_propagates_unchanged_without_retry(self):
        for exception_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            failure = exception_type("private-stop-marker")
            for generator, _prefix, _validator in _GENERATORS:
                with self.subTest(
                    generator=generator.__name__,
                    exception=exception_type.__name__,
                ):
                    with mock.patch.object(
                        identifiers,
                        "_token_bytes",
                        side_effect=failure,
                    ) as entropy_source:
                        with self.assertRaises(exception_type) as captured:
                            generator()
                    self.assertIs(captured.exception, failure)
                    entropy_source.assert_called_once_with(16)


class TracebackSafetyTests(unittest.TestCase):
    def assert_module_traceback_is_safe(
        self,
        callable_object: object,
        *,
        byte_markers: tuple[bytes, ...],
        text_markers: tuple[str, ...],
        private_objects: tuple[object, ...],
    ) -> None:
        try:
            callable_object()  # type: ignore[operator]
        except identifiers.RecordIdentifierGenerationError as error:
            self.assertIs(type(error), identifiers.RecordIdentifierGenerationError)
            self.assertEqual(error.args, ())
            self.assertEqual(
                str(error),
                "account record identifier generation failed",
            )
            self.assertEqual(repr(error), "RecordIdentifierGenerationError()")
            self.assertIsNone(error.__context__)
            self.assertIsNone(error.__cause__)

            seen: set[int] = set()

            def inspect_safe_value(value: object) -> None:
                if isinstance(value, BaseException):
                    if value is error:
                        return
                    self.fail("module traceback retained an exception object")
                if any(value is private for private in private_objects):
                    self.fail("module traceback retained a private object")
                value_type = type(value)
                if value_type is str:
                    if any(marker in value for marker in text_markers):
                        self.fail("module traceback retained private text")
                    return
                if value_type is bytes:
                    if any(marker and marker in value for marker in byte_markers):
                        self.fail("module traceback retained private bytes")
                    return
                if (
                    value_type is not dict
                    and value_type is not list
                    and value_type is not tuple
                    and value_type is not set
                    and value_type is not frozenset
                ):
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
            source_filename = os.path.realpath(_SOURCE_PATH)
            traceback = error.__traceback__
            while traceback is not None:
                frame = traceback.tb_frame
                if os.path.realpath(frame.f_code.co_filename) == source_filename:
                    module_frames += 1
                    if frame.f_code.co_name == "_generate_candidate_worker":
                        self.fail("module traceback retained the private worker frame")
                    for local_name, local_value in dict.items(frame.f_locals):
                        inspect_safe_value(local_name)
                        inspect_safe_value(local_value)
                traceback = traceback.tb_next
            self.assertGreater(module_frames, 0)
            return
        self.fail("generation failure was not raised")

    def test_all_fixed_failure_stages_drop_sensitive_worker_frames(self):
        raw_entropy = bytes((193,)) * 16
        short_entropy = raw_entropy[:-1]
        suffix = _b64(raw_entropy)
        suffix_bytes = suffix.encode("ascii")
        candidate = "usr_" + suffix
        candidate_bytes = candidate.encode("ascii")
        source_marker = "private-entropy-source-exception-marker"
        encoding_marker = "private-encoding-exception-marker"
        canonicality_marker = "private-canonicality-exception-marker"
        entropy_source_exception = RuntimeError(source_marker)
        encoding_exception = RuntimeError(encoding_marker)
        canonicality_exception = RuntimeError(canonicality_marker)

        class InvalidEntropy:
            pass

        invalid_entropy = InvalidEntropy()
        private_objects = (
            entropy_source_exception,
            encoding_exception,
            canonicality_exception,
            invalid_entropy,
            raw_entropy,
            short_entropy,
            suffix,
            suffix_bytes,
            candidate,
            candidate_bytes,
        )
        byte_markers = (
            raw_entropy,
            short_entropy,
            suffix_bytes,
            candidate_bytes,
            source_marker.encode("ascii"),
            encoding_marker.encode("ascii"),
            canonicality_marker.encode("ascii"),
        )
        text_markers = (
            source_marker,
            encoding_marker,
            canonicality_marker,
            suffix,
            candidate,
            "usr_",
        )

        with self.subTest(stage="entropy exception"):
            with mock.patch.object(
                identifiers,
                "_token_bytes",
                side_effect=entropy_source_exception,
            ):
                self.assert_module_traceback_is_safe(
                    identifiers.generate_user_id_candidate,
                    byte_markers=byte_markers,
                    text_markers=text_markers,
                    private_objects=private_objects,
                )

        with self.subTest(stage="invalid entropy type"):
            with mock.patch.object(
                identifiers,
                "_token_bytes",
                return_value=invalid_entropy,
            ):
                self.assert_module_traceback_is_safe(
                    identifiers.generate_user_id_candidate,
                    byte_markers=byte_markers,
                    text_markers=text_markers,
                    private_objects=private_objects,
                )

        with self.subTest(stage="invalid entropy length"):
            with mock.patch.object(
                identifiers,
                "_token_bytes",
                return_value=short_entropy,
            ):
                self.assert_module_traceback_is_safe(
                    identifiers.generate_user_id_candidate,
                    byte_markers=byte_markers,
                    text_markers=text_markers,
                    private_objects=private_objects,
                )

        with self.subTest(stage="encoding exception"):
            with mock.patch.object(
                identifiers,
                "_token_bytes",
                return_value=raw_entropy,
            ), mock.patch.object(
                identifiers,
                "_encode_base64url",
                side_effect=encoding_exception,
            ):
                self.assert_module_traceback_is_safe(
                    identifiers.generate_user_id_candidate,
                    byte_markers=byte_markers,
                    text_markers=text_markers,
                    private_objects=private_objects,
                )

        with self.subTest(stage="canonicality exception"):
            with mock.patch.object(
                identifiers,
                "_token_bytes",
                return_value=raw_entropy,
            ), mock.patch.object(
                identifiers,
                "_is_canonical_candidate",
                side_effect=canonicality_exception,
            ):
                self.assert_module_traceback_is_safe(
                    identifiers.generate_user_id_candidate,
                    byte_markers=byte_markers,
                    text_markers=text_markers,
                    private_objects=private_objects,
                )


class IndependenceRouteAndDocumentationTests(unittest.TestCase):
    def test_one_changed_entropy_value_affects_only_its_own_call(self):
        baseline_values = [bytes((index,)) * 16 for index in range(4)]
        changed_values = list(baseline_values)
        changed_values[2] = bytes((99,)) * 16

        with mock.patch.object(
            identifiers,
            "_token_bytes",
            side_effect=baseline_values,
        ):
            baseline = tuple(generator() for generator, _prefix, _validator in _GENERATORS)
        with mock.patch.object(
            identifiers,
            "_token_bytes",
            side_effect=changed_values,
        ):
            changed = tuple(generator() for generator, _prefix, _validator in _GENERATORS)

        self.assertEqual(baseline[:2], changed[:2])
        self.assertNotEqual(baseline[2], changed[2])
        self.assertEqual(baseline[3], changed[3])
        for result, (_generator, prefix, _validator) in zip(changed, _GENERATORS):
            self.assertTrue(result.startswith(prefix))

    def test_no_collision_storage_clock_logging_or_fallback_surface(self):
        tree = ast.parse(_SOURCE_PATH.read_text(encoding="utf-8"))
        imported_roots = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertEqual(imported_roots, {"sys", "base64", "secrets"})
        source = _SOURCE_PATH.read_text(encoding="utf-8").casefold()
        for forbidden in (
            "os.environ",
            "getenv(",
            "time.time",
            "datetime",
            "uuid",
            "random.",
            "socket",
            "urlopen",
            "redis",
            "database",
            "set-cookie",
            "session_id",
            "cookie_secret",
            "credential_epoch",
        ):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("existing_ids", source)
        self.assertNotIn("retry_count", source)
        self.assertEqual(source.count("_token_bytes(_entropy_byte_length)"), 1)

    def test_vercel_python_function_pattern_is_exact_and_excludes_new_paths(self):
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
            "cuevion_auth/account_record_ids.py",
            "tests/cuevion_auth/test_account_record_ids.py",
        ):
            with self.subTest(relative_path=relative_path):
                self.assertTrue(
                    all(
                        not PurePosixPath(relative_path).match(pattern)
                        for pattern in configured_patterns
                    )
                )

    def test_activation_document_covers_candidate_and_deferral_contract(self):
        documentation = _DOCUMENTATION_PATH.read_text(encoding="utf-8")
        normalized = " ".join(documentation.casefold().split())
        required = (
            "this slice is completely inactive",
            "unpersisted account-record id candidates",
            "only a candidate until a future transactional repository persists it",
            "generation alone as persisted authority",
            "an abandoned candidate is harmless",
            "exactly four candidate types are generated",
            "canonical unpadded base64url encoding of exactly 16 fresh random bytes",
            "exactly 22 ascii characters",
            "`secrets.token_bytes(16)`",
            "every call receives a fresh independent draw",
            "accept no arguments",
            "deterministic tests may patch only the private module entropy primitive",
            "generator performs no retry",
            "authoritative final collision guard",
            "definitive uniqueness conflict",
            "ambiguous storage outcome must never trigger blind candidate generation",
            "generates no session id, cookie secret, session credential, credential envelope",
            "fixed, value-free `recordidentifiergenerationerror`",
            "module-owned traceback frames for the fixed generation error retain no raw entropy",
            "does not extend to arbitrary caller frames",
            "not a security boundary against arbitrary code",
            "production and preview execute identical code",
            "future transactional account creation remains blocked",
            "no database or authentication vendor is selected",
        )
        for statement in required:
            with self.subTest(statement=statement):
                self.assertIn(statement, normalized)

        candidate_pattern = re.compile(
            r"(?:usr|vem|aid|wsp)_[A-Za-z0-9_-]{22}"
        )
        credential_pattern = re.compile(
            r"v1\.[1-9][0-9]*\.[1-9][0-9]*\.[1-9][0-9]*\."
            r"[A-Za-z0-9_-]{43}"
        )
        self.assertIsNone(candidate_pattern.search(documentation))
        self.assertIsNone(credential_pattern.search(documentation))


if __name__ == "__main__":
    unittest.main()
