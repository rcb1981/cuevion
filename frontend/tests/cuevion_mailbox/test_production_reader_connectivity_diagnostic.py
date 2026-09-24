"""Static isolation tests for the temporary Production reader diagnostic."""

from pathlib import Path
import unittest


_FRONTEND = Path(__file__).resolve().parents[2]
_ROUTE = _FRONTEND / "api" / "production-mailbox-reader-connectivity-proof.py"
_GMAIL_ROUTE = _FRONTEND / "api" / "inboxes" / "fetch-gmail.py"


class ProductionReaderConnectivityDiagnosticTests(unittest.TestCase):
    def test_route_is_isolated_reader_only_surface(self):
        source = _ROUTE.read_text(encoding="utf-8")

        self.assertIn("production_reader_diagnostic_enabled", source)
        self.assertIn("production_read_authority_enabled", source)
        self.assertIn("run_production_reader_connectivity_check", source)
        self.assertIn('"mode": "production_read"', source)
        self.assertIn('"authority_enabled": False', source)
        self.assertIn('"connected": result.status == "connected"', source)

        for forbidden in (
            "CUEVION_MAILBOX_WRITER_DATABASE_URL",
            "build_active_write_mailbox_repositories",
            "build_shadow_mailbox_repositories",
            "PostgreSQLMailboxRepository",
            "custom_imap",
            "read_gmail_folder_snapshot",
            "plan_gmail_authoritative_read",
        ):
            self.assertNotIn(forbidden, source)

    def test_normal_gmail_route_does_not_consume_diagnostic_flag(self):
        source = _GMAIL_ROUTE.read_text(encoding="utf-8")

        self.assertNotIn(
            "CUEVION_MAILBOX_PRODUCTION_READER_DIAGNOSTIC",
            source,
        )
        self.assertNotIn(
            "production_reader_diagnostic_enabled",
            source,
        )
        self.assertIn("gmail_cache_authority_enabled(os.environ)", source)


if __name__ == "__main__":
    unittest.main()
