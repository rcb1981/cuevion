from __future__ import annotations

import unittest

from api.auth.http import HttpBoundaryError

from .http import MAX_REQUEST_BODY_BYTES, read_workflow_json_request
from .test_http import _Handler


class PriorityWorkflowHttpBoundaryTests(unittest.TestCase):
    def test_valid_exact_json_uses_strict_authenticated_post_boundary(self):
        headers, payload = read_workflow_json_request(
            _Handler(
                b'{"operation":"read","mailboxId":"mailbox-1","identities":[]}'
            )
        )
        self.assertIsInstance(headers, tuple)
        self.assertEqual(payload["operation"], "read")

    def test_oversized_body_is_rejected_before_read(self):
        handler = _Handler(b"{}", content_length=str(MAX_REQUEST_BODY_BYTES + 1))
        with self.assertRaises(HttpBoundaryError) as captured:
            read_workflow_json_request(handler)
        self.assertEqual(captured.exception.status, 413)
        self.assertEqual(captured.exception.code, "invalid_request")
        self.assertEqual(handler.rfile.tell(), 0)


if __name__ == "__main__":
    unittest.main()
