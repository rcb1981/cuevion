from __future__ import annotations

import importlib.util
import io
import json
import os
import unittest
from pathlib import Path


def _load_route():
    route_path = Path(__file__).with_name("out-of-office-worker.py")
    spec = importlib.util.spec_from_file_location("api_out_of_office_worker_route", route_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("route could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


route = _load_route()


class FakeHandler:
    def __init__(self, authorization=None):
        self.headers = {}
        if authorization is not None:
            self.headers["authorization"] = authorization
        self.wfile = io.BytesIO()
        self.status_code = None
        self.response_headers = {}

    def send_response(self, status_code):
        self.status_code = status_code

    def send_header(self, name, value):
        self.response_headers[name.lower()] = value

    def end_headers(self):
        return None

    def payload(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


class OutOfOfficeCronHttpTests(unittest.TestCase):
    def setUp(self):
        self.original_env = dict(os.environ)
        self.original_store_builder = route.build_out_of_office_store
        self.original_worker = route.run_out_of_office_worker
        self.original_mailbox_loader = route.load_out_of_office_mailbox
        self.original_adapter_resolver = route.resolve_out_of_office_adapter

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_env)
        route.build_out_of_office_store = self.original_store_builder
        route.run_out_of_office_worker = self.original_worker
        route.load_out_of_office_mailbox = self.original_mailbox_loader
        route.resolve_out_of_office_adapter = self.original_adapter_resolver

    def _enable_production(self):
        os.environ["VERCEL_ENV"] = "production"
        os.environ["CRON_SECRET"] = "c" * 48

    def test_preview_cannot_run_worker_even_with_matching_header(self):
        os.environ["VERCEL_ENV"] = "preview"
        os.environ["CRON_SECRET"] = "c" * 48
        route.build_out_of_office_store = lambda: self.fail("store must not open")
        handler = FakeHandler("Bearer " + "c" * 48)
        route.handler.do_GET(handler)
        self.assertEqual(handler.status_code, 404)

    def test_missing_or_wrong_secret_is_not_found(self):
        self._enable_production()
        route.build_out_of_office_store = lambda: self.fail("store must not open")
        for authorization in (None, "Bearer wrong"):
            with self.subTest(authorization=authorization):
                handler = FakeHandler(authorization)
                route.handler.do_GET(handler)
                self.assertEqual(handler.status_code, 404)
                self.assertEqual(handler.payload()["error"]["code"], "not_found")

    def test_authorized_cron_returns_aggregate_only(self):
        self._enable_production()
        fake_store = object()
        route.build_out_of_office_store = lambda: fake_store

        def run_worker(*, store, load_mailbox, resolve_adapter):
            self.assertIs(store, fake_store)
            self.assertIs(load_mailbox, route.load_out_of_office_mailbox)
            self.assertIs(resolve_adapter, route.resolve_out_of_office_adapter)
            return [
                {
                    "ownerEmail": "private@example.com",
                    "mailboxId": "secret-mailbox-id",
                    "status": "processed",
                    "sent": 1,
                    "sentCopyFailures": 1,
                    "suppressed": 2,
                    "skipped": 3,
                    "error": None,
                },
                {
                    "ownerEmail": "private2@example.com",
                    "mailboxId": "secret-mailbox-id-2",
                    "status": "inactive",
                    "sent": 0,
                    "suppressed": 0,
                    "skipped": 0,
                    "error": None,
                },
            ]

        route.run_out_of_office_worker = run_worker
        handler = FakeHandler("Bearer " + "c" * 48)
        route.handler.do_GET(handler)
        payload = handler.payload()
        self.assertEqual(handler.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["processedTargets"], 2)
        self.assertEqual(payload["sent"], 1)
        self.assertEqual(payload["sentCopyFailures"], 1)
        self.assertEqual(payload["suppressed"], 2)
        self.assertEqual(payload["skipped"], 3)
        self.assertEqual(payload["statuses"], {"inactive": 1, "processed": 1})
        serialized = json.dumps(payload)
        self.assertNotIn("private@example.com", serialized)
        self.assertNotIn("secret-mailbox-id", serialized)

    def test_post_is_never_a_worker_trigger(self):
        self._enable_production()
        handler = FakeHandler("Bearer " + "c" * 48)
        route.handler.do_POST(handler)
        self.assertEqual(handler.status_code, 405)


if __name__ == "__main__":
    unittest.main()
