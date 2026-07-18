"""Repository-boundary tests proving the database foundation is inactive."""

import hashlib
import os
from pathlib import Path, PurePosixPath
import subprocess
import unittest


_FRONTEND = Path(__file__).resolve().parents[2]
_REPOSITORY = _FRONTEND.parent
_PROTECTED_UNTRACKED = Path("frontend/api/inboxes/oauth_google.py")
_EXISTING_SESSION_CREDENTIALS_TEST = (
    "frontend/tests/cuevion_auth/test_session_credentials.py"
)


def _changed_path_contains_forbidden_fragment(status_code, path, fragment):
    if (
        status_code != "??"
        and path == _EXISTING_SESSION_CREDENTIALS_TEST
        and fragment == "credential"
    ):
        return False
    return fragment in path.casefold()


class DatabaseFoundationInactivityTests(unittest.TestCase):
    def test_tracked_active_api_scan_never_reads_protected_untracked_file(self):
        completed = subprocess.run(
            ["git", "ls-files", "-z", "--", "frontend/api/**/*.py"],
            cwd=_REPOSITORY,
            check=False,
            capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr.decode())
        tracked = tuple(Path(value.decode()) for value in completed.stdout.split(b"\0") if value)
        self.assertNotIn(_PROTECTED_UNTRACKED, tracked)
        for relative in tracked:
            if relative.name.startswith("test_"):
                continue
            source = (_REPOSITORY / relative).read_text(encoding="utf-8")
            normalized = source.casefold()
            self.assertNotIn("cuevion_db", normalized)
            self.assertNotIn("alembic", normalized)
            self.assertNotIn("metadata.create_all", normalized)

    def test_database_modules_are_outside_vercel_api_glob(self):
        patterns = ("api/**/*.py",)
        for relative in (
            "cuevion_db/configuration.py",
            "cuevion_db/metadata.py",
            "cuevion_db/account_schema.py",
            "migrations/env.py",
            "migrations/versions/0001_account_schema_1.py",
        ):
            self.assertTrue(all(not PurePosixPath(relative).match(pattern) for pattern in patterns))

    def test_vercel_configuration_is_byte_identical_to_baseline(self):
        digest = hashlib.sha256((_FRONTEND / "vercel.json").read_bytes()).hexdigest()
        self.assertEqual(digest, "d8be3937a733e64b540edbb472c2a6d02c3d576cb3d20c360af2cdabdc223c09")

    def test_python_and_dependency_contract_is_exact(self):
        self.assertEqual((_FRONTEND / ".python-version").read_text(encoding="utf-8"), "3.12\n")
        self.assertEqual(
            (_FRONTEND / "requirements.txt").read_text(encoding="utf-8").splitlines(),
            ["cryptography~=46.0.0", "psycopg[binary]~=3.3.4", "SQLAlchemy~=2.0.51"],
        )
        self.assertEqual(
            (_FRONTEND / "requirements-migrations.txt").read_text(encoding="utf-8").splitlines(),
            ["-r requirements.txt", "alembic~=1.18.5"],
        )
        normalized = ((_FRONTEND / "requirements.txt").read_text(encoding="utf-8") + (_FRONTEND / "requirements-migrations.txt").read_text(encoding="utf-8")).casefold()
        for forbidden in ("psycopg_pool", "asyncpg", "sqlalchemy.orm"):
            self.assertNotIn(forbidden, normalized)

    def test_session_credentials_path_exception_is_exact(self):
        self.assertFalse(
            _changed_path_contains_forbidden_fragment(
                " M",
                _EXISTING_SESSION_CREDENTIALS_TEST,
                "credential",
            )
        )
        rejected = (
            ("??", _EXISTING_SESSION_CREDENTIALS_TEST),
            (" M", "frontend/cuevion_auth/session_credentials.py"),
            (" M", "frontend/tests/cuevion_db/test_session_credentials.py"),
            (
                " M",
                "archive/frontend/tests/cuevion_auth/test_session_credentials.py",
            ),
            (" M", "frontend/tests/cuevion_auth/runtime_credential.py"),
            (
                " M",
                "frontend/tests/cuevion_auth/test_session_credentials.py.backup",
            ),
        )
        for status_code, path in rejected:
            with self.subTest(status_code=status_code, path=path):
                self.assertTrue(
                    _changed_path_contains_forbidden_fragment(
                        status_code,
                        path,
                        "credential",
                    )
                )

    def test_no_forbidden_foundation_module_or_credential_file_exists(self):
        for relative in (
            "cuevion_db/runtime_connection.py",
            "cuevion_db/postgresql_account_repository.py",
            "tests/cuevion_db/test_integration.py",
        ):
            self.assertFalse((_FRONTEND / relative).exists())
        status = subprocess.run(
            ["git", "status", "--short", "--untracked-files=all"],
            cwd=_REPOSITORY,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        changed_entries = tuple((line[:2], line[3:]) for line in status.splitlines())
        for status_code, path in changed_entries:
            lowered = path.casefold()
            if lowered.endswith("database_foundation_activation_requirements.md"):
                continue
            name = Path(path).name.casefold()
            self.assertFalse(
                name == ".env"
                or (name.startswith(".env.") and not name.endswith(".example"))
            )
            for fragment in ("credential", "secret", "password", "connection_string"):
                self.assertFalse(
                    _changed_path_contains_forbidden_fragment(
                        status_code,
                        path,
                        fragment,
                    ),
                    msg=f"{fragment!r} unexpectedly found in {lowered!r}",
                )

        implementation_roots = (
            _FRONTEND / "cuevion_db",
            _FRONTEND / "migrations",
            _FRONTEND / "tests" / "cuevion_db",
        )
        candidates = list(_FRONTEND.glob(".env*"))
        for implementation_root in implementation_roots:
            for directory, _children, files in os.walk(implementation_root):
                candidates.extend(Path(directory, filename) for filename in files)
        for candidate in candidates:
            lowered = candidate.name.casefold()
            self.assertFalse(
                lowered == ".env"
                or (
                    lowered.startswith(".env.")
                    and not lowered.endswith(".example")
                )
            )
            if candidate.suffix.casefold() == ".example":
                continue
            for fragment in ("credential", "secret", "password", "connection_string"):
                self.assertNotIn(fragment, lowered)

    def test_production_foundation_has_no_runtime_activation_surface(self):
        sources = tuple((_FRONTEND / "cuevion_db").glob("*.py")) + tuple((_FRONTEND / "migrations").rglob("*.py"))
        combined = "\n".join(path.read_text(encoding="utf-8") for path in sources).casefold()
        for forbidden in (
            "create_engine",
            "metadata.create_all",
            "neon auth",
            "providerlogin",
            "set-cookie",
            "collaboration",
            "productentitlement",
        ):
            self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
