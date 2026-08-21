from __future__ import annotations

import io
import unittest

from api.auth.http import HttpBoundaryError

from .http import MAX_REQUEST_BODY_BYTES, read_semantic_json_request


class _Headers:
    def __init__(self, values: list[tuple[str, str]]) -> None:
        self._values = values

    def raw_items(self):
        return list(self._values)


class _Handler:
    def __init__(
        self,
        body: bytes,
        *,
        method: str = "POST",
        host: str = "app.cuevion.com",
        origin: str = "https://app.cuevion.com",
        content_length: str | None = None,
        extra_headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.command = method
        headers = [
            ("Host", host),
            ("Origin", origin),
            ("Content-Type", "application/json"),
            ("Content-Length", content_length or str(len(body))),
            *extra_headers,
        ]
        self.headers = _Headers(headers)
        self.rfile = io.BytesIO(body)


class SemanticHttpBoundaryTests(unittest.TestCase):
    def assert_boundary(
        self,
        handler: _Handler,
        *,
        code: str,
        status: int,
    ) -> None:
        with self.assertRaises(HttpBoundaryError) as captured:
            read_semantic_json_request(handler)
        self.assertEqual(captured.exception.code, code)
        self.assertEqual(captured.exception.status, status)

    def test_valid_exact_json_object_is_returned_with_header_snapshot(self):
        headers, payload = read_semantic_json_request(
            _Handler(b'{"mailboxId":"mailbox-1"}')
        )
        self.assertEqual(payload, {"mailboxId": "mailbox-1"})
        self.assertIsInstance(headers, tuple)

    def test_wrong_method_host_and_origin_fail_before_body_read(self):
        cases = (
            (_Handler(b"{}", method="GET"), "method_not_allowed", 405),
            (_Handler(b"{}", host="attacker.invalid"), "forbidden_host", 403),
            (_Handler(b"{}", origin="https://attacker.invalid"), "forbidden_origin", 403),
        )
        for handler, code, status in cases:
            with self.subTest(code=code):
                self.assert_boundary(handler, code=code, status=status)
                self.assertEqual(handler.rfile.tell(), 0)

    def test_malformed_duplicate_key_and_non_object_json_are_rejected(self):
        for body in (
            b'{"mailboxId":',
            b'{"mailboxId":"one","mailboxId":"two"}',
            b"[]",
        ):
            with self.subTest(body=body):
                self.assert_boundary(
                    _Handler(body),
                    code="invalid_request",
                    status=400,
                )

    def test_oversized_or_inexact_body_is_rejected_by_bounded_reader(self):
        self.assert_boundary(
            _Handler(b"{}", content_length=str(MAX_REQUEST_BODY_BYTES + 1)),
            code="invalid_request",
            status=413,
        )
        short = _Handler(b"{}", content_length="3")
        self.assert_boundary(short, code="invalid_request", status=400)

    def test_duplicate_security_header_is_rejected(self):
        self.assert_boundary(
            _Handler(b"{}", extra_headers=(("Content-Length", "2"),)),
            code="ambiguous_headers",
            status=400,
        )


if __name__ == "__main__":
    unittest.main()
