"""Security-contract tests for the inactive authenticated-session capability."""

import ast
import base64
import copy
import dataclasses
import inspect
import os
import pickle
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import typing
import unittest
from unittest import mock

from api.auth import models
from api.auth import session_contract as contract


_AUTH_DIRECTORY = Path(__file__).resolve().parent
_FRONTEND_DIRECTORY = _AUTH_DIRECTORY.parents[1]
_SOURCE_PATH = _AUTH_DIRECTORY / "session_contract.py"


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


def _cold_import_program(target: str) -> str:
    models_path = str(_AUTH_DIRECTORY / "models.py")
    contract_path = str(_SOURCE_PATH)
    expected_targets = (
        ("api.auth.models",)
        if target == "api.auth.models"
        else ("api.auth.models", "api.auth.session_contract")
    )
    source_paths = (
        (models_path,)
        if target == "api.auth.models"
        else (models_path, contract_path)
    )
    return f"""
import atexit
import __future__
import base64
import builtins
import dataclasses
import enum
import http.client
import importlib
import importlib.util
import io
import os
import re
import socket
import subprocess
import sys
import threading
import typing
import unicodedata
import urllib.request

target = {target!r}
expected_targets = {expected_targets!r}
source_paths = {source_paths!r}
assert all(name not in sys.modules for name in expected_targets)

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

def blocked(*_arguments, **_keywords):
    raise AssertionError('forbidden side effect')

frontend = os.getcwd()
allowed_directories = {{
    os.path.abspath(frontend),
    os.path.abspath(os.path.join(frontend, 'api')),
    os.path.abspath(os.path.join(frontend, 'api', 'auth')),
}}
allowed_files = {{os.path.abspath(path) for path in source_paths}}
allowed_files.update(
    os.path.abspath(importlib.util.cache_from_source(path))
    for path in source_paths
)

filesystem_events = []
network_or_process_events = []
loader_filesystem_calls = []
def audit(event, arguments):
    if event == 'open' or event.startswith('os.'):
        caller = sys._getframe(1).f_globals.get('__name__')
        filesystem_events.append((event, arguments, caller))
    if (
        event.startswith('socket.')
        or event.startswith('subprocess.')
        or event.startswith('http.client.')
    ):
        network_or_process_events.append((event, arguments))
sys.addaudithook(audit)

def loader_only_filesystem_call(name, operation):
    def checked(path, *arguments, **keywords):
        caller = sys._getframe(1).f_globals.get('__name__')
        if caller != 'importlib._bootstrap_external':
            raise AssertionError('non-loader filesystem access')
        normalized = os.path.abspath(os.fsdecode(path))
        if normalized not in allowed_directories and normalized not in allowed_files:
            raise AssertionError('loader accessed an unexpected path')
        loader_filesystem_calls.append((name, normalized))
        return operation(path, *arguments, **keywords)
    return checked

os.stat = loader_only_filesystem_call('stat', os.stat)
os.lstat = loader_only_filesystem_call('lstat', os.lstat)
os.access = loader_only_filesystem_call('access', os.access)
os.listdir = loader_only_filesystem_call('listdir', os.listdir)
for blocked_probe in (os.stat, os.lstat, os.access, os.listdir):
    try:
        blocked_probe(source_paths[0])
    except AssertionError:
        pass
    else:
        raise AssertionError('filesystem wrapper did not block non-loader caller')

os.environ = BlockedEnvironment()
if hasattr(os, 'environb'):
    os.environb = BlockedEnvironment()
os.getenv = blocked
if hasattr(os, 'getenvb'):
    os.getenvb = blocked
builtins.open = blocked
io.open = blocked
os.open = blocked
for filesystem_name in (
    'fstat',
    'fstatvfs',
    'readlink',
    'scandir',
    'statvfs',
    'walk',
):
    if hasattr(os, filesystem_name):
        setattr(os, filesystem_name, blocked)
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

allowed_imports = {{
    'api.auth.models': {{
        ('__future__', 0),
        ('sys', 0),
        ('base64', 0),
        ('re', 0),
        ('unicodedata', 0),
        ('dataclasses', 0),
        ('enum', 0),
    }},
    'api.auth.session_contract': {{
        ('sys', 0),
        ('enum', 0),
        ('typing', 0),
        ('', 1),
    }},
}}
production_imports = []
original_import = builtins.__import__
def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    caller = globals.get('__name__') if type(globals) is dict else None
    if caller in allowed_imports:
        request = (name, level)
        if request not in allowed_imports[caller]:
            raise AssertionError('forbidden production import')
        production_imports.append((caller, request))
    return original_import(name, globals, locals, fromlist, level)
builtins.__import__ = guarded_import

path_before = tuple(sys.path)
modules_before = set(sys.modules)
module = importlib.import_module(target)
assert tuple(sys.path) == path_before
assert module is sys.modules[target]

expected_delta = {{'api', 'api.auth', *expected_targets}}
assert set(sys.modules) - modules_before == expected_delta
for name in expected_targets:
    registered = sys.modules[name]
    aliases = [key for key, value in sys.modules.items() if value is registered]
    assert aliases == [name]
    for forbidden_surface in ('handler', 'route', 'router', 'app'):
        assert not hasattr(registered, forbidden_surface)
if target == 'api.auth.session_contract':
    assert module._models is sys.modules['api.auth.models']
    assert module._models.CuevionUser is sys.modules['api.auth.models'].CuevionUser

assert production_imports
assert network_or_process_events == []
for event, arguments, caller in filesystem_events:
    assert caller == 'importlib._bootstrap_external'
    if event == 'open':
        path = arguments[0]
        assert type(path) in (str, bytes)
        normalized = os.path.abspath(os.fsdecode(path))
        assert normalized in allowed_files
        mode = arguments[1]
        flags = arguments[2]
        assert mode == 'r'
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
    elif event == 'os.listdir':
        normalized = os.path.abspath(os.fsdecode(arguments[0]))
        assert normalized in allowed_directories
    else:
        raise AssertionError('non-loader filesystem operation')
"""


def _encoded(byte: int, length: int) -> str:
    return base64.urlsafe_b64encode(bytes([byte]) * length).decode("ascii").rstrip("=")


_USER_ID = "usr_" + _encoded(1, 16)
_OTHER_USER_ID = "usr_" + _encoded(2, 16)
_EMAIL_ID = "vem_" + _encoded(3, 16)
_OTHER_EMAIL_ID = "vem_" + _encoded(4, 16)
_IDENTITY_ID = "aid_" + _encoded(5, 16)
_OTHER_IDENTITY_ID = "aid_" + _encoded(6, 16)
_SESSION_ID = _encoded(7, 32)
_LOOKUP_DIGEST = _encoded(8, 32)
_BINDING_DIGEST = _encoded(9, 32)


class _StringSubclass(str):
    pass


class _ExplosiveObject:
    def __getattribute__(self, _name: str) -> object:
        raise AssertionError("private behavior was invoked")

    def __str__(self) -> str:
        raise AssertionError("private string conversion was invoked")

    def __repr__(self) -> str:
        raise AssertionError("private repr conversion was invoked")

    def __eq__(self, _other: object) -> bool:
        raise AssertionError("private equality was invoked")

    def __hash__(self) -> int:
        raise AssertionError("private hashing was invoked")


class _ResolverStop(BaseException):
    pass


def _valid_records() -> tuple[
    models.CuevionUser,
    models.VerifiedEmail,
    models.AuthenticationIdentity,
    models.StoredSessionSnapshot,
]:
    user = models.CuevionUser(
        schema_version=1,
        user_id=_USER_ID,
        status=models.UserStatus.ACTIVE,
        primary_verified_email_id=_EMAIL_ID,
        display_name="Synthetic Owner",
        security_epoch=7,
        created_at=10,
        updated_at=20,
        row_version=1,
    )
    primary_email = models.VerifiedEmail(
        schema_version=1,
        email_id=_EMAIL_ID,
        user_id=_USER_ID,
        canonical_email="owner+session@example.test",
        status=models.VerifiedEmailStatus.VERIFIED,
        verification_source="test_source",
        created_at=10,
        verified_at=20,
        retired_at=None,
        row_version=1,
    )
    identity = models.AuthenticationIdentity(
        schema_version=1,
        identity_id=_IDENTITY_ID,
        user_id=_USER_ID,
        issuer="issuer_test",
        subject="subject_test",
        method=models.AuthenticationMethod.OIDC,
        status=models.AuthenticationIdentityStatus.ACTIVE,
        verified_email_id=_EMAIL_ID,
        created_at=30,
        last_used_at=120,
        row_version=1,
    )
    session = models.StoredSessionSnapshot(
        schema_version=1,
        session_id=_SESSION_ID,
        user_id=_USER_ID,
        authentication_identity_id=_IDENTITY_ID,
        credential_lookup_digest=_LOOKUP_DIGEST,
        credential_binding_digest=_BINDING_DIGEST,
        credential_epoch=3,
        security_epoch=7,
        status=models.SessionStatus.ACTIVE,
        authenticated_at=100,
        issued_at=110,
        last_used_at=120,
        idle_expires_at=200,
        absolute_expires_at=300,
        revoked_at=None,
        revocation_reason=None,
        row_version=1,
    )
    return user, primary_email, identity, session


def _forged_record_subclass(record: object) -> object:
    previous_definition_state = models._RECORD_CLASS_DEFINITION_OPEN
    models._RECORD_CLASS_DEFINITION_OPEN = True
    try:
        subclass = type(
            f"{type(record).__name__}ForgedSubclass",
            (type(record),),
            {"__slots__": ()},
        )
    finally:
        models._RECORD_CLASS_DEFINITION_OPEN = previous_definition_state
    forged = object.__new__(subclass)
    for field in dataclasses.fields(type(record)):
        object.__setattr__(forged, field.name, getattr(record, field.name))
    return forged


def _mint_valid() -> contract.AuthenticatedAccountSession:
    return contract._mint_authenticated_account_session(*_valid_records(), now=150)


class SessionContractImportTests(unittest.TestCase):
    def test_canonical_module_identity_and_no_package_initializer(self):
        self.assertEqual(contract.__name__, "api.auth.session_contract")
        self.assertEqual(contract.__package__, "api.auth")
        self.assertEqual(contract.__spec__.name, "api.auth.session_contract")
        self.assertEqual(models.__name__, "api.auth.models")
        self.assertIs(contract._models, sys.modules["api.auth.models"])
        self.assertIs(contract._models.CuevionUser, models.CuevionUser)
        self.assertIs(
            contract._models.ModelValidationError,
            models.ModelValidationError,
        )
        self.assertFalse((_AUTH_DIRECTORY / "__init__.py").exists())
        self.assertEqual(
            {path.name for path in _AUTH_DIRECTORY.iterdir() if path.is_file()},
            {
                "AUTH_ACTIVATION_REQUIREMENTS.md",
                "models.py",
                "session_contract.py",
                "test_models.py",
                "test_session_contract.py",
            },
        )

    def test_isolated_normal_canonical_import_uses_registered_models(self):
        completed = _run_isolated(
            "import importlib,sys\n"
            "assert 'api.auth.models' not in sys.modules\n"
            "assert 'api.auth.session_contract' not in sys.modules\n"
            "module=importlib.import_module('api.auth.session_contract')\n"
            "models=sys.modules['api.auth.models']\n"
            "assert module is sys.modules['api.auth.session_contract']\n"
            "assert module._models is models\n"
            "assert module._models.CuevionUser is models.CuevionUser\n"
            "assert module.SessionResolutionReason('internal_error') is module.SessionResolutionReason.INTERNAL_ERROR\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stdout + completed.stderr,
        )

    def test_isolated_top_level_and_alternate_dotted_imports_fail_closed(self):
        attempts = (
            (str(_AUTH_DIRECTORY), "session_contract"),
            (str(_AUTH_DIRECTORY.parent), "auth.session_contract"),
            (
                str(_FRONTEND_DIRECTORY.parent),
                "frontend.api.auth.session_contract",
            ),
        )
        for path_entry, module_name in attempts:
            program = (
                "import importlib,sys\n"
                "original=importlib.import_module('api.auth.session_contract')\n"
                "models=sys.modules['api.auth.models']\n"
                "identities=(original.SessionResolutionReason,original.SessionResolutionError,original.AuthenticatedAccountSession,models.CuevionUser)\n"
                "sentinels=(original._ENUM_VALUE_MISSING,original._SESSION_RESOLUTION_ERROR_CONSTRUCTION_SENTINEL,original._CAPABILITY_FACTORY_SENTINEL)\n"
                f"sys.path.insert(0, {path_entry!r})\n"
                "try:\n"
                f" importlib.import_module({module_name!r})\n"
                "except ImportError:\n"
                " pass\n"
                "else:\n"
                " raise SystemExit('alternate import unexpectedly succeeded')\n"
                "assert sys.modules['api.auth.session_contract'] is original\n"
                "assert original._models is models\n"
                "assert identities == (original.SessionResolutionReason,original.SessionResolutionError,original.AuthenticatedAccountSession,models.CuevionUser)\n"
                "assert sentinels == (original._ENUM_VALUE_MISSING,original._SESSION_RESOLUTION_ERROR_CONSTRUCTION_SENTINEL,original._CAPABILITY_FACTORY_SENTINEL)\n"
                "assert original.SessionResolutionReason('internal_error') is original.SessionResolutionReason.INTERNAL_ERROR\n"
            )
            completed = _run_isolated(program)
            self.assertEqual(
                completed.returncode,
                0,
                msg=completed.stdout + completed.stderr,
            )

    def test_isolated_duplicate_spec_and_reload_preserve_original_identities(self):
        path = str(_SOURCE_PATH)
        program = (
            "import builtins,importlib,importlib.util,sys\n"
            "original=importlib.import_module('api.auth.session_contract')\n"
            "models=sys.modules['api.auth.models']\n"
            "identities=(original.SessionResolutionReason,original.SessionResolutionError,original.AuthenticatedAccountSession,models.CuevionUser)\n"
            "sentinels=(original._ENUM_VALUE_MISSING,original._SESSION_RESOLUTION_ERROR_CONSTRUCTION_SENTINEL,original._CAPABILITY_FACTORY_SENTINEL)\n"
            "def assert_original_usable():\n"
            " assert sys.modules['api.auth.session_contract'] is original\n"
            " assert original._models is models\n"
            " assert identities == (original.SessionResolutionReason,original.SessionResolutionError,original.AuthenticatedAccountSession,models.CuevionUser)\n"
            " assert sentinels == (original._ENUM_VALUE_MISSING,original._SESSION_RESOLUTION_ERROR_CONSTRUCTION_SENTINEL,original._CAPABILITY_FACTORY_SENTINEL)\n"
            " assert original._models.CuevionUser is models.CuevionUser\n"
            " try:\n"
            "  original.raise_session_resolution_error(original.SessionResolutionReason.INTERNAL_ERROR)\n"
            " except original.SessionResolutionError as error:\n"
            "  assert original.get_session_resolution_reason(error) is original.SessionResolutionReason.INTERNAL_ERROR\n"
            " else:\n"
            "  raise AssertionError('original error class unusable')\n"
            "spec=importlib.util.spec_from_file_location('api.auth.session_contract', "
            f"{path!r})\n"
            "duplicate=importlib.util.module_from_spec(spec)\n"
            "sibling_imports=[]\n"
            "original_import=builtins.__import__\n"
            "def monitored_import(name,globals=None,locals=None,fromlist=(),level=0):\n"
            " caller=globals.get('__name__') if type(globals) is dict else None\n"
            " if caller == 'api.auth.session_contract' and level == 1:\n"
            "  sibling_imports.append((name,fromlist))\n"
            " return original_import(name,globals,locals,fromlist,level)\n"
            "builtins.__import__=monitored_import\n"
            "try:\n"
            " try:\n"
            "  spec.loader.exec_module(duplicate)\n"
            " except ImportError:\n"
            "  pass\n"
            " else:\n"
            "  raise SystemExit('duplicate canonical spec unexpectedly succeeded')\n"
            "finally:\n"
            " builtins.__import__=original_import\n"
            "assert sibling_imports == []\n"
            "assert '_AUTH_A_SESSION_CONTRACT_INITIALIZED' not in duplicate.__dict__\n"
            "for name in ('_ENUM_VALUE_MISSING','SessionResolutionReason','SessionResolutionError','_SESSION_RESOLUTION_ERROR_CONSTRUCTION_SENTINEL','_CAPABILITY_FACTORY_SENTINEL','AuthenticatedAccountSession'):\n"
            " assert name not in duplicate.__dict__\n"
            "assert_original_usable()\n"
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

    def test_imports_are_only_standard_library_and_canonical_sibling(self):
        expected_by_path = {
            _AUTH_DIRECTORY / "models.py": {
                (0, "__future__"),
                (0, "sys"),
                (0, "base64"),
                (0, "re"),
                (0, "unicodedata"),
                (0, "dataclasses"),
                (0, "enum"),
            },
            _SOURCE_PATH: {
                (0, "sys"),
                (0, "enum"),
                (0, "typing"),
                (1, None),
            },
        }
        for path, expected in expected_by_path.items():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported: set[tuple[int, str | None]] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update((0, alias.name) for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.add((node.level, node.module))
            with self.subTest(path=path.name):
                self.assertEqual(imported, expected)

    def test_module_has_no_handler_route_or_runtime_implementation(self):
        public = {
            name: value
            for name, value in vars(contract).items()
            if not name.startswith("_")
        }
        self.assertEqual(set(public), set(contract.__all__))
        self.assertFalse(hasattr(contract, "handler"))
        self.assertNotIn("handler", contract.__dict__)
        self.assertNotIn("route", contract.__dict__)
        self.assertNotIn("sys", contract.__dict__)
        self.assertNotIn("os", contract.__dict__)
        self.assertNotIn("socket", contract.__dict__)
        self.assertNotIn("open", contract.__dict__)

    def test_true_cold_canonical_imports_have_no_forbidden_side_effects(self):
        for target in ("api.auth.models", "api.auth.session_contract"):
            with self.subTest(target=target):
                completed = _run_isolated(_cold_import_program(target))
                self.assertEqual(
                    completed.returncode,
                    0,
                    msg=completed.stdout + completed.stderr,
                )

    def test_forbidden_application_modules_are_not_imported_by_auth_a(self):
        forbidden = {
            "beta",
            "collaboration",
            "inboxes",
            "provider",
            "storage",
            "team",
            "frontend",
        }
        for path in (_AUTH_DIRECTORY / "models.py", _SOURCE_PATH):
            source_tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(source_tree):
                if isinstance(node, ast.Import):
                    names = [alias.name.casefold() for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [(node.module or "").casefold()]
                else:
                    continue
                for name in names:
                    with self.subTest(path=path.name, imported=name):
                        self.assertTrue(forbidden.isdisjoint(name.split(".")))


class SessionResolutionFailureTests(unittest.TestCase):
    def test_resolution_reason_is_closed_and_constructor_is_value_free(self):
        self.assertEqual(
            tuple(reason.value for reason in contract.SessionResolutionReason),
            (
                "authentication_required",
                "authentication_unavailable",
                "internal_error",
            ),
        )
        for reason in contract.SessionResolutionReason:
            self.assertIs(contract.SessionResolutionReason(reason), reason)
            self.assertIs(contract.SessionResolutionReason(reason.value), reason)

        rejected = (
            "private-unknown-reason",
            _StringSubclass("authentication_required"),
            True,
            None,
            _ExplosiveObject(),
        )
        for value in rejected:
            with self.subTest(type=type(value)):
                with self.assertRaises(ValueError) as raised:
                    contract.SessionResolutionReason(value)
                self.assertEqual(
                    raised.exception.args,
                    ("invalid session resolution reason",),
                )
                self.assertNotIn("private-unknown-reason", str(raised.exception))
        for arguments, keywords in (
            ((), {}),
            (("authentication_required", object()), {}),
            ((), {"private_field": "private-unknown-reason"}),
        ):
            with self.assertRaises(ValueError) as raised:
                contract.SessionResolutionReason(*arguments, **keywords)
            self.assertEqual(
                raised.exception.args,
                ("invalid session resolution reason",),
            )

    def test_public_raiser_is_exact_clean_value_free_and_exported(self):
        signature = inspect.signature(contract.raise_session_resolution_error)
        self.assertIsNone(contract.raise_session_resolution_error.__closure__)
        self.assertEqual(tuple(signature.parameters), ("reason",))
        self.assertIs(
            signature.parameters["reason"].annotation,
            contract.SessionResolutionReason,
        )
        self.assertIs(signature.return_annotation, typing.NoReturn)
        self.assertIn("raise_session_resolution_error", contract.__all__)

        marker = "private-session-resolution-marker"
        for reason in contract.SessionResolutionReason:
            with self.subTest(reason=reason):
                try:
                    raise RuntimeError(marker)
                except RuntimeError:
                    try:
                        contract.raise_session_resolution_error(reason)
                    except contract.SessionResolutionError as error:
                        captured = error
                    else:
                        self.fail("session resolution raiser unexpectedly returned")
                self.assertIs(type(captured), contract.SessionResolutionError)
                self.assertIs(captured.reason, reason)
                self.assertIs(contract.get_session_resolution_reason(captured), reason)
                self.assertEqual(captured.args, ())
                self.assertEqual(
                    str(captured),
                    "authenticated session resolution failed",
                )
                self.assertEqual(repr(captured), "SessionResolutionError()")
                self.assertIsNone(captured.__context__)
                self.assertIsNone(captured.__cause__)
                self.assertEqual(vars(captured), {})
                self.assertNotIn(marker, captured.args)
                self.assertNotIn(marker, str(captured))
                self.assertNotIn(marker, repr(captured))

    def test_public_raiser_rejects_nonexact_reasons_without_coercion(self):
        marker = "private-unknown-session-reason"
        for malformed in (
            marker,
            _StringSubclass("authentication_required"),
            RuntimeError(marker),
            True,
            None,
            _ExplosiveObject(),
        ):
            with self.subTest(type=type(malformed)):
                with self.assertRaises(TypeError) as raised:
                    contract.raise_session_resolution_error(malformed)
                self.assertEqual(
                    raised.exception.args,
                    ("session resolution reason must be exact",),
                )
                self.assertNotIn(marker, str(raised.exception))
                self.assertNotIn(marker, repr(raised.exception))

    def test_direct_error_construction_is_fixed_and_unsupported(self):
        marker = "private-direct-construction-marker"
        attempts = (
            ((), {}),
            ((contract.SessionResolutionReason.AUTHENTICATION_REQUIRED,), {}),
            ((marker,), {}),
            ((), {"private_field": marker}),
        )
        for arguments, keywords in attempts:
            with self.assertRaises(TypeError) as raised:
                contract.SessionResolutionError(*arguments, **keywords)
            self.assertEqual(
                raised.exception.args,
                (
                    "session resolution errors require the supported raising function",
                ),
            )
            self.assertNotIn(marker, str(raised.exception))
            self.assertNotIn(marker, repr(raised.exception))

    def test_safe_extractor_normalizes_partial_deleted_and_corrupt_errors(self):
        internal = contract.SessionResolutionReason.INTERNAL_ERROR
        absent = Exception.__new__(contract.SessionResolutionError)
        partial = Exception.__new__(contract.SessionResolutionError)
        Exception.__init__(partial)
        deleted = Exception.__new__(contract.SessionResolutionError)
        Exception.__init__(deleted)
        object.__setattr__(
            deleted,
            "reason",
            contract.SessionResolutionReason.AUTHENTICATION_REQUIRED,
        )
        object.__delattr__(deleted, "reason")
        malformed_reason = Exception.__new__(contract.SessionResolutionError)
        Exception.__init__(malformed_reason)
        object.__setattr__(malformed_reason, "reason", _ExplosiveObject())
        unknown_reason = Exception.__new__(contract.SessionResolutionError)
        Exception.__init__(unknown_reason)
        object.__setattr__(unknown_reason, "reason", "private-unknown-reason")

        for malformed in (
            absent,
            partial,
            deleted,
            malformed_reason,
            unknown_reason,
            object(),
            None,
        ):
            self.assertIs(contract.get_session_resolution_reason(malformed), internal)
            self.assertIs(contract._safe_session_resolution_reason(malformed), internal)

        marker = "private-corrupt-error-marker"
        corrupt_args = Exception.__new__(contract.SessionResolutionError)
        Exception.__init__(corrupt_args)
        object.__setattr__(
            corrupt_args,
            "reason",
            contract.SessionResolutionReason.AUTHENTICATION_REQUIRED,
        )
        corrupt_args.args = (marker,)
        corrupt_cause = Exception.__new__(contract.SessionResolutionError)
        Exception.__init__(corrupt_cause)
        object.__setattr__(
            corrupt_cause,
            "reason",
            contract.SessionResolutionReason.AUTHENTICATION_REQUIRED,
        )
        corrupt_cause.__cause__ = RuntimeError(marker)
        corrupt_context = Exception.__new__(contract.SessionResolutionError)
        Exception.__init__(corrupt_context)
        object.__setattr__(
            corrupt_context,
            "reason",
            contract.SessionResolutionReason.AUTHENTICATION_REQUIRED,
        )
        corrupt_context.__context__ = RuntimeError(marker)
        for corrupt in (corrupt_args, corrupt_cause, corrupt_context):
            self.assertIs(contract.get_session_resolution_reason(corrupt), internal)
            self.assertNotIn(marker, str(corrupt))
            self.assertNotIn(marker, repr(corrupt))

    def test_error_subclasses_are_not_exact_errors(self):
        class ErrorSubclass(contract.SessionResolutionError):
            pass

        with self.assertRaises(TypeError) as raised:
            ErrorSubclass(contract.SessionResolutionReason.AUTHENTICATION_REQUIRED)
        self.assertEqual(
            raised.exception.args,
            ("session resolution errors require the supported raising function",),
        )
        malformed_subclass = Exception.__new__(ErrorSubclass)
        object.__setattr__(
            malformed_subclass,
            "reason",
            contract.SessionResolutionReason.AUTHENTICATION_REQUIRED,
        )
        self.assertIs(
            contract.get_session_resolution_reason(malformed_subclass),
            contract.SessionResolutionReason.INTERNAL_ERROR,
        )


class AuthenticatedAccountSessionTests(unittest.TestCase):
    def assertAuthenticationRequired(self, *arguments: object, now: object) -> None:
        with self.assertRaises(contract.SessionResolutionError) as raised:
            contract._mint_authenticated_account_session(*arguments, now=now)
        error = raised.exception
        self.assertIs(
            contract.get_session_resolution_reason(error),
            contract.SessionResolutionReason.AUTHENTICATION_REQUIRED,
        )
        self.assertEqual(error.args, ())
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.__cause__)

    def test_successful_mint_exposes_exact_read_only_values(self):
        user, primary_email, identity, session = _valid_records()
        capability = contract._mint_authenticated_account_session(
            user, primary_email, identity, session, 150
        )
        self.assertIs(type(capability), contract.AuthenticatedAccountSession)
        self.assertEqual(capability.user_id, _USER_ID)
        self.assertEqual(capability.owner_email, primary_email.canonical_email)
        self.assertEqual(capability.display_name, user.display_name)
        self.assertEqual(capability.authentication_issuer, identity.issuer)
        self.assertEqual(capability.authentication_subject, identity.subject)
        self.assertIs(capability.authentication_method, identity.method)
        self.assertEqual(capability.session_id, _SESSION_ID)
        self.assertEqual(capability.credential_binding_digest, _BINDING_DIGEST)
        self.assertEqual(capability.authenticated_at, 100)
        self.assertEqual(capability.issued_at, 110)
        self.assertEqual(capability.expires_at, 300)
        self.assertEqual(capability.security_epoch, 7)

    def test_identity_without_email_link_does_not_gain_an_implicit_link(self):
        user, primary_email, identity, session = _valid_records()
        identity = dataclasses.replace(identity, verified_email_id=None)
        capability = contract._mint_authenticated_account_session(
            user, primary_email, identity, session, 150
        )
        self.assertEqual(capability.owner_email, primary_email.canonical_email)
        self.assertEqual(capability.authentication_subject, identity.subject)

    def test_direct_construction_and_subclassing_fail_with_fixed_behavior(self):
        marker = "private-constructor-value"
        for arguments in ((), (object(),), (_ExplosiveObject(),), (marker,)):
            with self.assertRaises(TypeError) as raised:
                contract.AuthenticatedAccountSession(*arguments)
            self.assertEqual(
                str(raised.exception),
                "authenticated session construction is unavailable",
            )
            self.assertNotIn(marker, str(raised.exception))

        with self.assertRaises(TypeError) as raised:
            class CapabilitySubclass(contract.AuthenticatedAccountSession):
                pass

        self.assertEqual(
            str(raised.exception),
            "authenticated session construction is unavailable",
        )

    def test_capability_is_slotted_immutable_and_uses_identity_semantics(self):
        first = _mint_valid()
        second = _mint_valid()
        self.assertFalse(dataclasses.is_dataclass(first))
        self.assertFalse(hasattr(first, "__dict__"))
        self.assertIsNot(first, second)
        self.assertNotEqual(first, second)
        self.assertEqual(len({first, second}), 2)
        for name in ("owner_email", "_owner_email"):
            with self.assertRaises(AttributeError):
                setattr(first, name, "private-mutated-value")
            with self.assertRaises(AttributeError):
                delattr(first, name)

    def test_capability_does_not_convert_serialize_or_duplicate(self):
        capability = _mint_valid()
        self.assertIs(copy.copy(capability), capability)
        self.assertIs(copy.deepcopy(capability), capability)
        with self.assertRaises(TypeError):
            dataclasses.asdict(capability)
        with self.assertRaises(TypeError) as raised:
            pickle.dumps(capability)
        self.assertEqual(
            str(raised.exception),
            "authenticated sessions cannot be serialized",
        )
        self.assertNotIn(_BINDING_DIGEST, str(raised.exception))

    def test_repr_and_str_are_fixed_and_value_free(self):
        capability = _mint_valid()
        self.assertEqual(repr(capability), "<AuthenticatedAccountSession>")
        self.assertEqual(str(capability), "AuthenticatedAccountSession")
        rendered = repr(capability) + str(capability)
        for private in (
            _USER_ID,
            "owner+session@example.test",
            "Synthetic Owner",
            "issuer_test",
            "subject_test",
            _SESSION_ID,
            _BINDING_DIGEST,
        ):
            self.assertNotIn(private, rendered)

    def test_capability_has_no_lookup_raw_credential_workspace_or_role_surface(self):
        capability = _mint_valid()
        expected_slots = (
            "_user_id",
            "_owner_email",
            "_display_name",
            "_authentication_issuer",
            "_authentication_subject",
            "_authentication_method",
            "_session_id",
            "_credential_binding_digest",
            "_authenticated_at",
            "_issued_at",
            "_expires_at",
            "_security_epoch",
        )
        expected_properties = {
            "user_id",
            "owner_email",
            "display_name",
            "authentication_issuer",
            "authentication_subject",
            "authentication_method",
            "session_id",
            "credential_binding_digest",
            "authenticated_at",
            "issued_at",
            "expires_at",
            "security_epoch",
        }
        self.assertEqual(contract.AuthenticatedAccountSession.__slots__, expected_slots)
        self.assertEqual(
            {
                name
                for name, value in vars(
                    contract.AuthenticatedAccountSession
                ).items()
                if isinstance(value, property)
            },
            expected_properties,
        )
        forbidden = (
            "credential_lookup_digest",
            "raw_headers",
            "raw_cookie",
            "cookie",
            "cookie_header",
            "complete_cookie_header",
            "session_cookie_value",
            "bearer_token",
            "access_token",
            "refresh_token",
            "id_token",
            "authorization_code",
            "oauth_code",
            "otp",
            "password",
            "password_hash",
            "magic_link",
            "challenge_secret",
            "pkce_verifier",
            "provider_client_secret",
            "provider_token",
            "mailbox_password",
            "mailbox_credential",
            "imap_secret",
            "smtp_secret",
            "encryption_key",
            "workspace",
            "workspace_id",
            "role",
        )
        for name in forbidden:
            self.assertFalse(hasattr(capability, name), name)
            self.assertNotIn(name, contract.AuthenticatedAccountSession.__slots__)

    def test_cross_record_identifier_confusion_fails_closed(self):
        user, primary_email, identity, session = _valid_records()
        cases = (
            (
                dataclasses.replace(
                    user, primary_verified_email_id=_OTHER_EMAIL_ID
                ),
                primary_email,
                identity,
                session,
            ),
            (
                user,
                dataclasses.replace(primary_email, user_id=_OTHER_USER_ID),
                identity,
                session,
            ),
            (
                user,
                primary_email,
                dataclasses.replace(identity, user_id=_OTHER_USER_ID),
                session,
            ),
            (
                user,
                primary_email,
                dataclasses.replace(
                    identity, verified_email_id=_OTHER_EMAIL_ID
                ),
                session,
            ),
            (
                user,
                primary_email,
                identity,
                dataclasses.replace(session, user_id=_OTHER_USER_ID),
            ),
            (
                user,
                primary_email,
                identity,
                dataclasses.replace(
                    session,
                    authentication_identity_id=_OTHER_IDENTITY_ID,
                ),
            ),
        )
        for records in cases:
            with self.subTest(records=tuple(type(record).__name__ for record in records)):
                self.assertAuthenticationRequired(*records, now=150)

    def test_inactive_user_and_unverified_primary_email_fail_closed(self):
        user, primary_email, identity, session = _valid_records()
        for status in (models.UserStatus.SUSPENDED, models.UserStatus.DISABLED):
            self.assertAuthenticationRequired(
                dataclasses.replace(user, status=status),
                primary_email,
                identity,
                session,
                now=150,
            )
        pending_email = dataclasses.replace(
            primary_email,
            status=models.VerifiedEmailStatus.PENDING,
            verified_at=None,
        )
        self.assertAuthenticationRequired(
            user, pending_email, identity, session, now=150
        )

    def test_inactive_identity_and_revoked_session_fail_closed(self):
        user, primary_email, identity, session = _valid_records()
        for status in (
            models.AuthenticationIdentityStatus.DISABLED,
            models.AuthenticationIdentityStatus.REVOKED,
        ):
            self.assertAuthenticationRequired(
                user,
                primary_email,
                dataclasses.replace(identity, status=status),
                session,
                now=150,
            )
        revoked = dataclasses.replace(
            session,
            status=models.SessionStatus.REVOKED,
            revoked_at=140,
            revocation_reason=models.SessionRevocationReason.LOGOUT,
        )
        self.assertAuthenticationRequired(
            user, primary_email, identity, revoked, now=150
        )

    def test_time_and_security_epoch_boundaries_fail_closed(self):
        user, primary_email, identity, session = _valid_records()
        boundary_capability = contract._mint_authenticated_account_session(
            user,
            primary_email,
            identity,
            session,
            now=120,
        )
        self.assertIs(
            type(boundary_capability),
            contract.AuthenticatedAccountSession,
        )
        self.assertAuthenticationRequired(
            user, primary_email, identity, session, now=119
        )
        future_last_use = dataclasses.replace(session, last_used_at=160)
        self.assertAuthenticationRequired(
            user,
            primary_email,
            identity,
            future_last_use,
            now=150,
        )
        self.assertAuthenticationRequired(
            user, primary_email, identity, session, now=200
        )
        absolute_boundary = dataclasses.replace(
            session,
            idle_expires_at=300,
            absolute_expires_at=300,
        )
        self.assertAuthenticationRequired(
            user, primary_email, identity, absolute_boundary, now=300
        )
        stale_epoch = dataclasses.replace(session, security_epoch=8)
        self.assertAuthenticationRequired(
            user, primary_email, identity, stale_epoch, now=150
        )
        maximum = models.MAX_UNIX_UTC_SECONDS
        near_maximum = dataclasses.replace(
            session,
            authenticated_at=maximum - 4,
            issued_at=maximum - 3,
            last_used_at=maximum - 2,
            idle_expires_at=maximum - 1,
            absolute_expires_at=maximum,
        )
        capability = contract._mint_authenticated_account_session(
            user,
            primary_email,
            identity,
            near_maximum,
            now=maximum - 2,
        )
        self.assertIs(type(capability), contract.AuthenticatedAccountSession)

    def test_now_boundaries_and_helper_dispatch_are_mutation_resistant(self):
        class IntegerSubclass(int):
            pass

        user, primary_email, identity, session = _valid_records()
        zero_session = dataclasses.replace(
            session,
            authenticated_at=0,
            issued_at=0,
            last_used_at=0,
            idle_expires_at=1,
            absolute_expires_at=2,
        )
        canonical_helper = models._is_timestamp
        with mock.patch.object(
            models, "_is_timestamp", wraps=canonical_helper
        ) as helper_spy:
            capability = contract._mint_authenticated_account_session(
                user,
                primary_email,
                identity,
                zero_session,
                now=0,
            )
        self.assertIs(type(capability), contract.AuthenticatedAccountSession)
        self.assertEqual(helper_spy.call_args_list[-1], mock.call(0))

        maximum = models.MAX_UNIX_UTC_SECONDS
        near_maximum = dataclasses.replace(
            session,
            authenticated_at=maximum - 4,
            issued_at=maximum - 3,
            last_used_at=maximum - 2,
            idle_expires_at=maximum - 1,
            absolute_expires_at=maximum,
        )
        for rejected_now in (maximum, maximum + 1):
            with self.subTest(rejected_now=rejected_now):
                canonical_helper = models._is_timestamp
                with mock.patch.object(
                    models, "_is_timestamp", wraps=canonical_helper
                ) as helper_spy:
                    self.assertAuthenticationRequired(
                        user,
                        primary_email,
                        identity,
                        near_maximum,
                        now=rejected_now,
                    )
                self.assertEqual(
                    helper_spy.call_args_list[-1],
                    mock.call(rejected_now),
                )

        for rejected_now in (
            False,
            True,
            IntegerSubclass(0),
        ):
            with self.subTest(rejected_type=type(rejected_now).__name__):
                canonical_helper = models._is_timestamp
                with mock.patch.object(
                    models, "_is_timestamp", wraps=canonical_helper
                ) as helper_spy:
                    self.assertAuthenticationRequired(
                        user,
                        primary_email,
                        identity,
                        session,
                        now=rejected_now,
                    )
                helper_spy.assert_not_called()

    def test_factory_revalidates_corrupted_exact_session_records(self):
        user, primary_email, identity, original = _valid_records()
        corruptions = (
            {"authenticated_at": original.issued_at + 1},
            {"issued_at": original.last_used_at + 1},
            {"last_used_at": original.idle_expires_at},
            {"last_used_at": original.idle_expires_at + 1},
            {"last_used_at": original.absolute_expires_at + 1},
            {"idle_expires_at": original.absolute_expires_at + 1},
            {"revoked_at": 150},
            {"revocation_reason": models.SessionRevocationReason.LOGOUT},
            {
                "revoked_at": 150,
                "revocation_reason": models.SessionRevocationReason.LOGOUT,
            },
        )
        for overrides in corruptions:
            records = list(_valid_records())
            session = records[3]
            for field_name, value in overrides.items():
                object.__setattr__(session, field_name, value)
            with self.subTest(fields=tuple(overrides)):
                self.assertAuthenticationRequired(*records, now=150)

    def test_exact_types_reject_subclasses_mappings_namespaces_and_duck_types(self):
        valid = _valid_records()
        for index, record in enumerate(valid):
            arguments = list(valid)
            arguments[index] = _forged_record_subclass(record)
            self.assertAuthenticationRequired(*arguments, now=150)

        for malformed in ({}, SimpleNamespace(), _ExplosiveObject()):
            for index in range(4):
                arguments = list(valid)
                arguments[index] = malformed
                self.assertAuthenticationRequired(*arguments, now=150)
        self.assertAuthenticationRequired(*valid, now=True)
        self.assertAuthenticationRequired(*valid, now=150.0)

    def test_model_validation_translation_is_private_and_baseexception_propagates(self):
        valid = _valid_records()
        private_failure = models.ModelValidationError()
        with mock.patch.object(
            models,
            "validate_user_primary_email",
            side_effect=private_failure,
        ):
            self.assertAuthenticationRequired(*valid, now=150)

        marker = "private-unexpected-validator-failure"
        with mock.patch.object(
            models,
            "validate_user_primary_email",
            side_effect=RuntimeError(marker),
        ):
            with self.assertRaises(contract.SessionResolutionError) as raised:
                contract._mint_authenticated_account_session(*valid, now=150)
        normalized = raised.exception
        self.assertIs(
            contract.get_session_resolution_reason(normalized),
            contract.SessionResolutionReason.INTERNAL_ERROR,
        )
        self.assertEqual(normalized.args, ())
        self.assertIsNone(normalized.__context__)
        self.assertIsNone(normalized.__cause__)
        self.assertNotIn(marker, str(normalized))
        self.assertNotIn(marker, repr(normalized))

        stop = _ResolverStop()
        with mock.patch.object(
            models,
            "validate_user_primary_email",
            side_effect=stop,
        ):
            with self.assertRaises(_ResolverStop) as raised:
                contract._mint_authenticated_account_session(*valid, now=150)
        self.assertIs(raised.exception, stop)

    def test_malformed_exact_records_with_deleted_slots_fail_fixed(self):
        field_names = (
            "user_id",
            "email_id",
            "verified_email_id",
            "session_id",
        )
        for index, field_name in enumerate(field_names):
            records = list(_valid_records())
            object.__delattr__(records[index], field_name)
            with self.subTest(record=index, field=field_name):
                self.assertAuthenticationRequired(*records, now=150)

    def test_factory_does_not_retain_an_exception_active_in_its_caller(self):
        marker = "private-caller-exception"
        valid = list(_valid_records())
        valid[0] = object()
        try:
            raise RuntimeError(marker)
        except RuntimeError:
            try:
                contract._mint_authenticated_account_session(*valid, now=150)
            except contract.SessionResolutionError as error:
                captured = error
            else:
                self.fail("invalid records unexpectedly minted a capability")
        self.assertIs(
            contract.get_session_resolution_reason(captured),
            contract.SessionResolutionReason.AUTHENTICATION_REQUIRED,
        )
        self.assertIsNone(captured.__context__)
        self.assertIsNone(captured.__cause__)
        self.assertNotIn(marker, str(captured))
        self.assertNotIn(marker, repr(captured))


class SessionProtocolTests(unittest.TestCase):
    def test_protocols_are_inactive_and_have_only_required_methods(self):
        expected = {
            contract.AccountRecordRepository: {
                "get_user",
                "get_verified_email",
                "get_authentication_identity",
                "get_workspace",
                "get_workspace_membership",
            },
            contract.SessionRecordRepository: {"get_session_by_lookup_digest"},
            contract.AuthenticatedSessionResolver: {
                "resolve_authenticated_session"
            },
        }
        for protocol, method_names in expected.items():
            self.assertTrue(protocol._is_protocol)
            with self.assertRaises(TypeError):
                protocol()
            actual = {
                name
                for name, value in protocol.__dict__.items()
                if inspect.isfunction(value) and not name.startswith("_")
            }
            self.assertEqual(actual, method_names)

    def test_account_repository_method_annotations_are_exact(self):
        methods = {
            "get_user": (("user_id", str), models.CuevionUser | None),
            "get_verified_email": (
                ("email_id", str),
                models.VerifiedEmail | None,
            ),
            "get_authentication_identity": (
                ("identity_id", str),
                models.AuthenticationIdentity | None,
            ),
            "get_workspace": (
                ("workspace_id", str),
                models.Workspace | None,
            ),
        }
        for method_name, (parameter, return_type) in methods.items():
            signature = inspect.signature(
                getattr(contract.AccountRecordRepository, method_name)
            )
            self.assertIs(signature.parameters[parameter[0]].annotation, parameter[1])
            self.assertEqual(signature.return_annotation, return_type)

        membership = inspect.signature(
            contract.AccountRecordRepository.get_workspace_membership
        )
        self.assertIs(membership.parameters["workspace_id"].annotation, str)
        self.assertIs(membership.parameters["user_id"].annotation, str)
        self.assertEqual(
            membership.return_annotation,
            models.WorkspaceMembership | None,
        )

    def test_session_repository_and_resolver_annotations_are_exact(self):
        repository = inspect.signature(
            contract.SessionRecordRepository.get_session_by_lookup_digest
        )
        self.assertIs(
            repository.parameters["credential_lookup_digest"].annotation,
            str,
        )
        self.assertEqual(
            repository.return_annotation,
            models.StoredSessionSnapshot | None,
        )

        resolver = inspect.signature(
            contract.AuthenticatedSessionResolver.resolve_authenticated_session
        )
        self.assertEqual(
            tuple(resolver.parameters),
            (
                "self",
                "raw_headers",
                "now",
            ),
        )
        self.assertEqual(
            resolver.parameters["raw_headers"].annotation,
            tuple[tuple[str, str], ...],
        )
        self.assertIs(resolver.parameters["now"].annotation, int)
        for parameter_name in ("raw_headers", "now"):
            parameter = resolver.parameters[parameter_name]
            self.assertIs(parameter.default, inspect.Parameter.empty)
            self.assertIs(
                parameter.kind,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        self.assertIs(
            resolver.return_annotation,
            contract.AuthenticatedAccountSession,
        )
        for forbidden_parameter in (
            "credential_lookup_digest",
            "credential_binding_digest",
            "session_id",
            "user_id",
            "email",
            "workspace",
            "account",
            "account_context",
        ):
            self.assertNotIn(forbidden_parameter, resolver.parameters)

    def test_resolver_duplicate_headers_and_trusted_derivation_are_explicit(self):
        signature = inspect.signature(
            contract.AuthenticatedSessionResolver.resolve_authenticated_session
        )
        raw_headers = signature.parameters["raw_headers"].annotation
        self.assertIs(typing.get_origin(raw_headers), tuple)
        outer_arguments = typing.get_args(raw_headers)
        self.assertEqual(len(outer_arguments), 2)
        self.assertIs(outer_arguments[1], Ellipsis)
        header_pair = outer_arguments[0]
        self.assertIs(typing.get_origin(header_pair), tuple)
        self.assertEqual(typing.get_args(header_pair), (str, str))

        documentation = inspect.getdoc(contract.AuthenticatedSessionResolver)
        self.assertIsNotNone(documentation)
        normalized = " ".join(documentation.casefold().split())
        for required in (
            "untrusted request credential input",
            "original ordered tuple of header-name/value pairs",
            "preserves header order and duplicate occurrences",
            "the tuple conveys no trust",
            "validate the exact container and element types",
            "``now`` must be an exact integer",
            "the resolver is the trusted parsing and derivation boundary",
            "reject missing, malformed, duplicate, ambiguous, oversized, or otherwise noncanonical credential or header representations",
            "must not accept a lookup digest or binding digest supplied by browser or request data",
            "server-only lookup key and dedicated lookup domain",
            "different server-only binding key and distinct binding domain",
            "only the resolver-derived canonical lookup digest may be passed",
            "authoritative stored binding digest",
            "independently derived expected value in constant time",
            "raw session cookie",
            "complete cookie header values",
            "never log or persist raw request credentials",
            "missing, malformed, ambiguous, expired, revoked, or authoritatively absent authentication maps to ``authentication_required``",
            "session or account authority outage maps to ``authentication_unavailable``",
            "persisted invariant corruption or an unexpected internal failure maps to ``internal_error``",
            "fixed failures must use ``raise_session_resolution_error``",
            "no beta-session, mailbox oauth, imap, localstorage, stateless, or workspace-selection fallback",
            "workspace membership and authorization remain separate future boundaries",
            "provides no resolver, parser, digest derivation, key access, repository, or storage implementation",
        ):
            with self.subTest(requirement=required):
                self.assertIn(required, normalized)

    def test_resolver_protocol_method_body_is_inert(self):
        tree = ast.parse(_SOURCE_PATH.read_text(encoding="utf-8"))
        resolver = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "AuthenticatedSessionResolver"
        )
        method = next(
            node
            for node in resolver.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "resolve_authenticated_session"
        )
        self.assertEqual(len(method.body), 2)
        self.assertIsInstance(method.body[0], ast.Expr)
        self.assertIsInstance(method.body[0].value, ast.Constant)
        self.assertIs(type(method.body[0].value.value), str)
        self.assertIsInstance(method.body[1], ast.Expr)
        self.assertIsInstance(method.body[1].value, ast.Constant)
        self.assertIs(method.body[1].value.value, Ellipsis)

    def test_repository_absence_outage_and_corruption_semantics_are_explicit(self):
        for repository in (
            contract.AccountRecordRepository,
            contract.SessionRecordRepository,
        ):
            documentation = inspect.getdoc(repository)
            self.assertIsNotNone(documentation)
            normalized = " ".join(documentation.casefold().split())
            for required in (
                "authoritative, successful lookup found no record",
                "storage unavailability",
                "raise_session_resolution_error",
                "authentication_unavailable",
                "persisted invariant corruption",
                "unexpected internal failure",
                "internal_error",
                "user, email, identity, workspace, or session ids",
                "credential lookup digests",
                "private storage details",
                "authentication_required",
                "invalid, revoked, or expired authentication",
                "never infrastructure outage",
            ):
                with self.subTest(
                    repository=repository.__name__,
                    requirement=required,
                ):
                    self.assertIn(required, normalized)

    def test_session_repository_receives_only_resolver_derived_lookup_digest(self):
        documentation = inspect.getdoc(contract.SessionRecordRepository)
        self.assertIsNotNone(documentation)
        normalized = " ".join(documentation.casefold().split())
        for required in (
            "only a canonical lookup digest derived by the trusted resolver",
            "server-only lookup key and dedicated lookup domain",
            "must not parse headers or cookies",
            "no raw cookie or header value may reach it",
            "no binding-digest lookup operation",
            "failures must remain fixed and value-free",
        ):
            with self.subTest(requirement=required):
                self.assertIn(required, normalized)

    def test_public_contract_has_no_raw_credential_or_fallback_surface(self):
        public_names = set(contract.__all__)
        self.assertNotIn("handler", public_names)
        forbidden_fragments = (
            "beta",
            "mailbox",
            "provider",
            "fallback",
            "cookie",
            "bearer",
            "access_token",
            "refresh_token",
            "id_token",
            "authorization_code",
            "oauth_code",
            "password",
            "otp",
            "magic_link",
            "challenge_secret",
            "pkce_verifier",
            "encryption_key",
            "workspace_selection",
        )
        for name in public_names:
            lowered = name.casefold()
            for fragment in forbidden_fragments:
                self.assertNotIn(fragment, lowered)

        approved_digest_parameters = {"credential_lookup_digest"}
        for protocol in (
            contract.AccountRecordRepository,
            contract.SessionRecordRepository,
            contract.AuthenticatedSessionResolver,
        ):
            for method_name, method in vars(protocol).items():
                if method_name.startswith("_") or not inspect.isfunction(method):
                    continue
                signature = inspect.signature(method)
                for parameter_name in signature.parameters:
                    if parameter_name == "self":
                        continue
                    if parameter_name in approved_digest_parameters:
                        self.assertIs(protocol, contract.SessionRecordRepository)
                        self.assertEqual(method_name, "get_session_by_lookup_digest")
                        continue
                    if parameter_name == "raw_headers":
                        self.assertIs(
                            protocol, contract.AuthenticatedSessionResolver
                        )
                        self.assertEqual(
                            method_name, "resolve_authenticated_session"
                        )
                        continue
                    lowered = parameter_name.casefold()
                    for fragment in forbidden_fragments + ("raw_header",):
                        self.assertNotIn(fragment, lowered)


class ActivationRequirementsDocumentationTests(unittest.TestCase):
    def test_hardening_and_process_trust_limits_are_documented(self):
        documentation = (_AUTH_DIRECTORY / "AUTH_ACTIVATION_REQUIREMENTS.md").read_text(
            encoding="utf-8"
        )
        normalized = " ".join(documentation.casefold().split())
        required = (
            "auth-a is completely inactive",
            "authenticated_at <= issued_at <= last_used_at < idle_expires_at <= absolute_expires_at",
            "last_used_at <= revoked_at <= absolute_expires_at",
            "last_used_at <= now",
            "a future `last_used_at` therefore fails closed",
            "ordinary alternate-name execution",
            "a second spec-loaded module object",
            "reload or equivalent re-execution",
            "not a security boundary against arbitrary code",
            "replaces or mutates `sys.modules`",
            "future resolvers must emit session-resolution failures only through `raise_session_resolution_error`",
            "direct construction or raising of `sessionresolutionerror` is not the supported contract",
            "returning `none` means only that an authoritative, successful lookup found no record",
            "authentication_unavailable",
            "persisted invariant corruption",
            "internal_error",
            "authentication_required",
            "not infrastructure outage",
            "no auth-a route, handler, authentication provider",
            "account or session storage",
            "cookie parsing or emission",
            "frontend integration",
            "collaboration integration",
        )
        for statement in required:
            with self.subTest(statement=statement):
                self.assertIn(statement, normalized)

    def test_resolver_trust_boundary_revision_is_documented(self):
        documentation = (_AUTH_DIRECTORY / "AUTH_ACTIVATION_REQUIREMENTS.md").read_text(
            encoding="utf-8"
        )
        normalized = " ".join(documentation.casefold().split())
        required = (
            "`authenticatedsessionresolver` receives the original duplicate-preserving tuple of raw header-name/value pairs",
            "these raw headers are untrusted request input",
            "preserves order and duplicate occurrences",
            "does not make the caller or its data trusted",
            "owns strict production-session credential parsing",
            "browser or request data may never precompute or supply a trusted credential lookup digest or credential binding digest",
            "server-only lookup key and dedicated lookup domain",
            "different server-only binding key and distinct binding domain",
            "repository lookup remains digest-only",
            "the repository must not parse headers or cookies",
            "authoritative stored binding digest",
            "future reviewed session authority",
            "missing, malformed, ambiguous, expired, revoked, or authoritatively absent authentication maps to `authentication_required`",
            "a session or account authority outage maps to `authentication_unavailable`",
            "persisted invariant corruption or an unexpected internal failure maps to `internal_error`",
            "workspace membership and authorization remain separate future boundaries",
            "auth-a implements no resolver, parser, cookie handling, digest derivation, key access, repository, or storage behavior",
            "duplicate cookie handling",
            "credential size limits",
            "canonical credential encoding",
            "separate lookup and binding keys and derivation domains",
            "constant-time binding verification",
            "key rotation",
            "revocation",
            "outage handling",
            "production/preview separation",
        )
        for statement in required:
            with self.subTest(statement=statement):
                self.assertIn(statement, normalized)


if __name__ == "__main__":
    unittest.main()
