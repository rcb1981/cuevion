"""Phase 4 fake-Graph proofs: reload discovery and attachment-preserving editors."""
import copy
import json
import os
import unittest
from unittest.mock import patch
from api.inboxes import microsoft_draft_attachment_policy as policy
from api.inboxes import microsoft_draft_attachment_requests as requests
from api.inboxes import microsoft_draft_attachment_executor as executor
from api.inboxes import microsoft_draft_attachment_http as http
from api.inboxes import microsoft_draft_editor as plain, microsoft_rich_draft as rich
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes.test_microsoft_draft_attachment_executor import ExecutorFixture
from api.inboxes.test_microsoft_draft_attachment_foundation import AUTH, OP, row
from api.inboxes import test_microsoft_draft_attachment_http as boundary_fixture
from api.inboxes.test_microsoft_draft_editor import Response, TOKEN, TAG, TAG2, EDIT
from api.inboxes.test_microsoft_rich_draft import HTML, META
from api.inboxes.test_microsoft_http import MESSAGE_ID, _body


class ReloadDiscoveryTests(ExecutorFixture):
    def test_reload_reads_durable_uncertain_operation_without_graph_replay(self):
        self.graph.lose = True
        result = self.execute()
        self.assertTrue(result['blocked'])
        before = len(self.graph.calls)
        for _ in range(3):
            found = executor.inspect_owned_draft_attachment_requests({}, AUTH.mailbox_id, 'draft-1')
            self.assertEqual(found, [result])
            self.assertEqual(len(self.graph.writes), 1)
            self.assertEqual(len(self.graph.calls), before)
        self.assertEqual(requests.inspect_draft(AUTH, 'other-draft'), [])
        self.assertNotIn('claim', json.dumps(found))

    def test_reload_reads_confirmed_status_after_browser_lost_receipt(self):
        result = self.execute()
        self.assertEqual(executor.inspect_owned_draft_attachment_requests({}, AUTH.mailbox_id, 'draft-1'), [result])
        self.assertEqual(len(self.graph.writes), 1)

    def test_uncertain_attachment_blocks_editor_save_at_server(self):
        self.graph.lose = True
        self.execute()
        with patch.object(plain, 'save_exact_microsoft_draft') as save:
            with self.assertRaises(MicrosoftGraphError):
                owned.save_owned_microsoft_editable_draft({}, AUTH.mailbox_id, 'draft-1', etag=TAG, edit=EDIT)
            save.assert_not_called()


class DiscoveryBoundaryTests(unittest.TestCase):
    setUp = boundary_fixture.AttachmentHTTPTests.setUp
    call = boundary_fixture.AttachmentHTTPTests.call
    def test_draft_status_returns_only_validated_bound_receipts(self):
        request = {'action': 'draft_status', 'mailboxId': AUTH.mailbox_id, 'providerMessageId': 'draft-1'}
        value = {'requestId': OP, 'providerMessageId': 'draft-1', 'outcome': 'uncertain', 'count': 2, 'completed': 1, 'blocked': True}
        with patch.object(executor, 'inspect_owned_draft_attachment_requests', return_value=[value]) as inspect, patch.object(executor, 'execute_owned_attachment_request') as mutate:
            result = _body(self.call(request))
            self.assertEqual(result['requests'], [value])
            inspect.assert_called_once(); mutate.assert_not_called()
        for bad in ([{**value, 'claim': 'secret'}], [{**value, 'providerMessageId': 'other'}], [value,value], [{**value,'blocked':False}]):
            with self.subTest(bad=bad), patch.object(executor, 'inspect_owned_draft_attachment_requests', return_value=bad):
                self.assertFalse(_body(self.call(request))['ok'])


class EditorGraph:
    """In-memory Graph that retains the complete attachment collection on PATCH."""
    def __init__(self, html=False, drift=False, cid=False, bcc=False, incomplete=False):
        self.html, self.drift, self.incomplete = html, drift, incomplete
        self.calls, self.writes = [], []
        self.rows = [row('regular'), row('inline', isInline=True, contentId='logo')]
        self.body = {'contentType': 'html' if html else 'text', 'content': '<img src="cid:logo">' if cid else HTML if html else 'Old body'}
        self.msg = {'id':MESSAGE_ID, 'parentFolderId':'draft-folder', 'isDraft':True, 'hasAttachments':True,
            '@odata.etag':TAG, 'subject':'Old', 'toRecipients':[], 'ccRecipients':[],
            'bccRecipients':[{'emailAddress':{'address':'hidden@example.com'}}] if bcc else [], 'body':self.body}
    def __call__(self, req, timeout):
        self.calls.append(req)
        if req.get_method() == 'PATCH':
            self.writes.append(req)
            data = json.loads(req.data)
            assert 'attachments' not in data and 'bccRecipients' not in data
            if self.html:
                assert 'body' not in data
            self.msg.update(data); self.msg['@odata.etag'] = TAG2
            if self.drift:
                self.rows[0]['name'] = 'external-change.txt'
            return Response({'id':MESSAGE_ID})
        if '/attachments?' in req.full_url:
            return Response({} if self.incomplete else {'value':copy.deepcopy(self.rows)})
        if '/messages/' in req.full_url:
            return Response(copy.deepcopy(self.msg))
        return Response({'id':'draft-folder'})


class EditorPreservationTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(policy, '_DRAFT_ATTACHMENTS_RELEASED', True)
        p.start(); self.addCleanup(p.stop)
    def save(self, tx):
        return rich.save_rich_draft_metadata(TOKEN,MESSAGE_ID,etag=TAG,metadata=META,opener=tx) if tx.html else plain.save_exact_microsoft_draft(TOKEN,MESSAGE_ID,etag=TAG,edit=EDIT,opener=tx)
    def test_text_and_html_metadata_keep_all_existing_attachments(self):
        for html in (False, True):
            tx=EditorGraph(html)
            original=copy.deepcopy(tx.rows)
            result=self.save(tx)
            self.assertTrue(result['changed'])
            self.assertEqual(tx.rows,original)
            self.assertEqual(len(tx.writes),1)
            self.assertEqual(tx.writes[0].get_header('If-match'),TAG)
            self.assertNotIn('_attachments',result)
            if html:
                self.assertEqual(tx.msg['body']['content'],HTML)
    def test_inventory_drift_is_uncertain_and_never_retried(self):
        for html in (False,True):
            tx=EditorGraph(html,drift=True)
            with self.assertRaises(MicrosoftGraphError) as caught:
                self.save(tx)
            self.assertEqual(caught.exception.code,'microsoft_draft_edit_unconfirmed')
            self.assertEqual(len(tx.writes),1)
    def test_cid_html_bcc_and_incomplete_inventory_fail_closed(self):
        for tx in (EditorGraph(True,cid=True),EditorGraph(True,bcc=True),EditorGraph(incomplete=True),EditorGraph(True,incomplete=True)):
            with self.assertRaises(MicrosoftGraphError): self.save(tx)
            self.assertEqual(tx.writes,[])
