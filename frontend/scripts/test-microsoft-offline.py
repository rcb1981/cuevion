#!/usr/bin/env python3
"""Run Microsoft backend tests without inherited credentials or external I/O."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import unittest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drafts-only", action="store_true", help="Run the four Drafts suites only")
    args = parser.parse_args()

    # This affects only this test process, never .env files or account settings.
    search_path = os.environ.get("PATH", "")
    os.environ.clear()
    os.environ.update({"PATH": search_path, "TZ": "Europe/Amsterdam", "PYTHONDONTWRITEBYTECODE": "1"})
    sys.dont_write_bytecode = True
    blocked_attempts = []

    def deny_external_io(event, _args):
        if event.startswith("socket.") or event in {
            "subprocess.Popen", "os.system", "os.posix_spawn", "os.fork", "os.forkpty",
        }:
            blocked_attempts.append(event)
            raise RuntimeError("Offline Microsoft test runner blocked external I/O: " + event)

    # Cannot be bypassed by accidentally restoring a mocked Python socket function.
    sys.addaudithook(deny_external_io)
    frontend = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(frontend))
    os.chdir(frontend)
    if args.drafts_only:
        suite = unittest.TestSuite(
            unittest.defaultTestLoader.loadTestsFromName("api.inboxes." + module)
            for module in (
                "test_microsoft_draft_discard", "test_microsoft_draft_editor",
                "test_microsoft_rich_draft", "test_microsoft_draft_local_http",
            )
        )
    else:
        suite = unittest.defaultTestLoader.discover(
            str(frontend / "api" / "inboxes"), pattern="test_microsoft*.py",
        )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if blocked_attempts:
        print("FAIL: external I/O was attempted: " + ", ".join(blocked_attempts), file=sys.stderr)
        return 1
    print("Offline guard: no socket, DNS or child-process attempts; inherited credentials cleared.")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
