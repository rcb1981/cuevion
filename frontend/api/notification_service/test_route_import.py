"""Cold file-entrypoint regression; no service credentials or network required."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


FRONTEND_ROOT = Path(__file__).resolve().parents[2]


class NotificationRouteImportTests(unittest.TestCase):
    def test_only_public_route_owns_notifications_name(self):
        api = FRONTEND_ROOT / "api"
        self.assertTrue((api / "notifications.py").is_file())
        self.assertFalse((api / "notification_service.py").exists())
        for name in ("__init__.py", "http.py", "models.py", "rate_limit.py", "recipients.py", "store.py"):
            self.assertFalse((api / "notifications" / name).exists())
            self.assertTrue((api / "notification_service" / name).is_file())

    def test_known_notification_consumers_have_no_old_package_imports(self):
        api = FRONTEND_ROOT / "api"
        consumers = [api / "notifications.py"]
        consumers += [api / "collaboration" / name for name in (
            "mutations.py", "redis_store.py", "test_notifications.py", "measure_c3c_notifications.py",
        )]
        consumers += [api / "notification_service" / name for name in (
            "__init__.py", "http.py", "models.py", "rate_limit.py", "recipients.py", "store.py",
            "test_http.py", "test_store.py", "test_rate_limit.py", "test_recipients.py",
        )]
        for path in consumers:
            with self.subTest(path=path.name):
                source = path.read_text()
                self.assertNotIn("from api.notifications", source)
                self.assertNotIn("import api.notifications", source)

    def test_vercel_file_entrypoint_imports_without_credentials_or_io(self):
        # Register the file with its public module name BEFORE executing it,
        # as a loader can do. A pre-imported package or an unrelated test alias
        # hides the notifications.py / notifications/ collision.
        script = textwrap.dedent("""
            import importlib.util
            import socket
            import sys
            import urllib.request
            from pathlib import Path
            from unittest.mock import patch

            root = Path.cwd()
            assert Path(sys.path[0] or root).resolve() == root
            assert 'api.notifications' not in sys.modules
            before_path = list(sys.path)
            spec = importlib.util.spec_from_file_location(
                'api.notifications', root / 'api' / 'notifications.py',
            )
            route = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = route
            with patch('urllib.request.urlopen', side_effect=AssertionError('network on import')) as urlopen, \\
                 patch.object(socket.socket, 'connect', side_effect=AssertionError('socket on import')) as connect:
                spec.loader.exec_module(route)
            urlopen.assert_not_called()
            connect.assert_not_called()
            assert callable(route.handler)
            assert callable(route.handler.do_POST)
            assert route.notifications_response.__module__ == 'api.notification_service.http'
            assert not hasattr(route, '__path__')
            from api.notification_service import http, models, recipients, rate_limit, store
            assert route.notifications_response is http.notifications_response
            assert Path(http.__file__).resolve() == root / 'api/notification_service/http.py'
            assert sys.path == before_path
            assert 'api.notifications.http' not in sys.modules
            # Normal imports must select the very same route file as well.
            import importlib
            del sys.modules['api.notifications']
            normal_route = importlib.import_module('api.notifications')
            assert Path(normal_route.__file__).resolve() == root / 'api/notifications.py'
            assert not hasattr(normal_route, '__path__')
            assert normal_route.notifications_response is http.notifications_response
            print('PASS: canonical file loader, handler, unique implementation, no network or credentials')
        """)
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=FRONTEND_ROOT,
            env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
