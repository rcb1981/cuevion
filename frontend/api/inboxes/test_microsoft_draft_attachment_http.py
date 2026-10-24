"""No-network disabled-route, origin, strict payload and output regressions."""
import base64
from io import BytesIO
import json
import os
from pathlib import Path
from unittest.mock import patch
import unittest
from api.inboxes import microsoft_draft_attachment_http as http
from api.inboxes import microsoft_draft_attachment_policy as policy
from api.inboxes import microsoft_draft_attachment_executor as executor
from api.inboxes import microsoft_draft_attachment_ledger as ledger
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes.test_microsoft_draft_attachment_foundation import AUTH, OP, snapshot
from api.inboxes.test_microsoft_http import _request, _body

HOST = 'cuevion-offline-test.vercel.app'
ORIGIN = (('Origin', 'https://' + HOST),)
ENVIRONMENT = {
 'VERCEL_ENV': 'preview', 'VERCEL_URL': HOST,
 'CUEVION_MICROSOFT_PREVIEW_ORIGIN': 'https://' + HOST,
 'CUEVION_APP_URL': 'https://' + HOST,
 'MICROSOFT_OAUTH_REDIRECT_URI': 'https://' + HOST + '/api/inboxes/oauth-callback',
}


def payload(action='add', **kw):
    base = {'action': action, 'mailboxId': AUTH.mailbox_id, 'providerMessageId': 'draft-1'}
    if action != 'load':
        base['requestId'] = OP
    if action in ('add', 'remove'):
        base['snapshotHash'] = 'a'*64
    if action == 'add':
        base['files'] = [{'name': 'new.txt', 'contentType': 'text/plain', 'base64': 'bmV3IQ=='}]
    if action == 'remove':
        base['attachmentId'] = 'ordinary'
    return {**base, **kw}


class AttachmentHTTPTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, ENVIRONMENT, clear=True)
        env.start(); self.addCleanup(env.stop)
        self.flag = patch.object(policy, '_DRAFT_ATTACHMENTS_RELEASED', True)
        self.flag.start(); self.addCleanup(self.flag.stop)
    def call(self, obj=None, **kw):
        req = _request(obj or payload(), header_pairs=ORIGIN, **kw)
        req.headers.replace_header('Host', HOST)
        return http.microsoft_draft_attachment_response(req)
    def test_disabled_source_gate_reads_no_body_and_calls_no_owner_graph_or_store(self):
        with patch.object(policy, '_DRAFT_ATTACHMENTS_RELEASED', False), patch.object(owned, 'resolve_owned_microsoft_mailbox') as owner, patch.object(executor, 'execute_owned_attachment_request') as execute:
            req = _request(payload(), header_pairs=ORIGIN)
            req.headers.replace_header('Host', HOST)
            req.rfile = None
            self.assertEqual(http.microsoft_draft_attachment_response(req).status, 403)
            owner.assert_not_called(); execute.assert_not_called()
    def test_production_denied_independently_of_mock_release(self):
        with patch.dict(os.environ, {'VERCEL_ENV': 'production'}), patch.object(executor, 'execute_owned_attachment_request') as execute:
            self.assertEqual(self.call().status, 403)
            execute.assert_not_called()
    def test_origin_host_duplicates_and_methods_fail_before_execution(self):
        with patch.object(executor, 'execute_owned_attachment_request') as execute:
            for headers in ((), (('Origin', 'https://evil.example'),), ORIGIN+(('Host','evil.example'),)):
                result = http.microsoft_draft_attachment_response(_request(payload(), header_pairs=headers))
                self.assertGreaterEqual(result.status, 400)
            for method in ('GET', 'HEAD', 'DELETE', 'OPTIONS', 'PATCH'):
                self.assertEqual(self.call(command=method).status, 405)
            execute.assert_not_called()
    def test_valid_upload_decodes_bytes_once_and_returns_bound_receipt_only(self):
        expected = {'requestId': OP, 'providerMessageId': 'draft-1', 'outcome': 'confirmed_performed', 'count': 1, 'completed': 1, 'blocked': False}
        with patch.object(executor, 'execute_owned_attachment_request', return_value=expected) as execute:
            result = self.call()
            self.assertEqual(result.status, 200)
            self.assertEqual(_body(result), {'ok': True, 'mailboxId': AUTH.mailbox_id, **expected})
            self.assertEqual(execute.call_args.kwargs['files'], [{'name': 'new.txt', 'content_type': 'text/plain', 'content': b'new!'}])
    def test_valid_remove_and_explicit_status_have_no_implicit_retry(self):
        expected = {'requestId': OP, 'providerMessageId': 'draft-1', 'outcome': 'uncertain', 'count': 1, 'completed': 0, 'blocked': True}
        with patch.object(executor, 'execute_owned_attachment_request', return_value=expected) as execute, patch.object(executor, 'inspect_owned_attachment_request', return_value=expected) as status:
            self.assertEqual(self.call(payload('remove')).status, 200)
            self.assertEqual(execute.call_args.kwargs['attachment_id'], 'ordinary')
            self.assertTrue(_body(self.call(payload('status')))['blocked'])
            self.assertEqual(execute.call_count, 1); status.assert_called_once()
    def test_load_exposes_only_metadata_and_snapshot_hash_never_html_tokens_or_claims(self):
        with patch.object(http.snapshots, 'read_owned_snapshot', return_value=snapshot()):
            result = _body(self.call(payload('load')))
            self.assertTrue(result['complete'])
            self.assertTrue(result['attachments'][0]['removable'])
            self.assertEqual(set(result), {'ok', 'mailboxId', 'providerMessageId', 'snapshotHash', 'attachments', 'complete'})
            self.assertNotIn('body', json.dumps(result)); self.assertNotIn('claim', result)
    def test_strict_schema_bad_ids_base64_duplicate_keys_and_untrusted_filenames_rejected(self):
        cases = [payload(url='https://graph.microsoft.com'), payload(snapshotHash='invalid'), payload(requestId='short'),
                 payload(mailboxId='../other'), payload(providerMessageId='../draft'), payload('remove', attachmentId='../bad'),
                 payload(files=[{'name':'../secret','contentType':'text/plain','base64':'eA=='}]),
                 payload(files=[{'name':'bad\r\n.txt','contentType':'text/plain','base64':'eA=='}]),
                 payload(files=[{'name':'x','contentType':'text/plain','base64':'eA='}]),
                 payload(files=[{'name':'x','contentType':'text/plain','base64':'eB=='}]),
                 payload(files=[{'name':'x','contentType':'text/plain','base64':'eA==','contentId':'logo'}])]
        with patch.object(executor, 'execute_owned_attachment_request') as execute:
            for obj in cases:
                with self.subTest(obj=str(obj)[:120]):
                    self.assertEqual(self.call(obj).status, 400)
            self.assertEqual(self.call(raw=b'{"action":"load","action":"remove"}').status, 400)
            execute.assert_not_called()
    def test_limits_validated_before_any_owner_or_graph_work(self):
        two = {'name':'x','contentType':'application/octet-stream','base64':base64.b64encode(b'x'*(2*1024*1024)).decode()}
        cases = [[two]*4+[{'name':'x','contentType':'text/plain','base64':'eA=='}], [two]*11,
                 [{**two,'base64':base64.b64encode(b'x'*(2*1024*1024+1)).decode()}]]
        with patch.object(executor, 'execute_owned_attachment_request') as execute:
            for files in cases:
                self.assertIn(self.call(payload(files=files)).status, (400,413))
            execute.assert_not_called()
    def test_content_length_cap_and_transfer_encoding_reject_before_read(self):
        req = _request(payload(), header_pairs=ORIGIN)
        req.headers.replace_header('Host', HOST)
        del req.headers['Content-Length']; req.headers['Content-Length'] = str(http.MAX_REQUEST_BYTES+1)
        req.rfile = None
        self.assertEqual(http.microsoft_draft_attachment_response(req).status, 413)
        req = _request(payload(), header_pairs=ORIGIN+(('Transfer-Encoding','chunked'),))
        req.headers.replace_header('Host', HOST)
        req.rfile = None
        self.assertEqual(http.microsoft_draft_attachment_response(req).status, 400)
    def test_private_errors_are_sanitized_and_leave_uncertain_feedback(self):
        for error in (RuntimeError('private-token secret'), ledger.AttachmentLedgerError('store_unavailable'), owned.MicrosoftMailboxAccessError('microsoft_reconnect_required')):
            with patch.object(executor, 'execute_owned_attachment_request', side_effect=error):
                result = _body(self.call())
                self.assertFalse(result['ok']); self.assertTrue(result['blocked'])
                self.assertEqual(result['outcome'], 'uncertain')
                self.assertNotIn('private-token', json.dumps(result))
    def test_route_adapter_uses_only_disabled_microsoft_boundary(self):
        source = Path(http.__file__).with_name('mutate-microsoft-draft-attachments.py').read_text()
        self.assertIn('microsoft_draft_attachment_response(self)', source)
        self.assertNotIn('urlopen', source)

    def test_unexpected_output_keys_or_cross_draft_receipt_are_never_exposed(self):
        expected = {'requestId': OP, 'providerMessageId': 'draft-1', 'outcome': 'confirmed_performed', 'count': 1, 'completed': 1, 'blocked': False}
        for value in ({**expected, 'claim': 'private-claim'}, {**expected, 'providerMessageId': 'other'},
                      {**expected, 'blocked': True}, {**expected, 'completed': 0}):
            with patch.object(executor, 'execute_owned_attachment_request', return_value=value):
                result = _body(self.call())
                self.assertFalse(result['ok']); self.assertTrue(result['blocked'])
                self.assertNotIn('private-claim', json.dumps(result))
