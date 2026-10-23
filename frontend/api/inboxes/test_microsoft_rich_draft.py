"""Offline proof for owner-scoped, metadata-only HTML Draft edits."""
import json
import os
import unittest
from urllib.error import URLError
from urllib.parse import quote
from unittest.mock import patch

from api.inboxes import microsoft_rich_draft as rich
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes import microsoft_draft_edit_http as http
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes.test_microsoft_draft_editor import Response, TOKEN, TAG, TAG2
from api.inboxes.test_microsoft_http import _request, MAILBOX, MESSAGE_ID

HTML = '<html><body>Original <b>formatted</b> mail.</body></html>'
META = {'subject': 'Updated', 'to': ['artist@example.com'], 'cc': []}


class RichTransport:
    def __init__(self, *, before=None, after=None, fail_at=None):
        self.calls = []
        self.before, self.after, self.fail_at = before or {}, after or {}, fail_at

    def __call__(self, req, timeout):
        self.calls.append(req)
        i = len(self.calls)
        if i == self.fail_at:
            raise URLError('simulated loss')
        if req.get_method() == 'PATCH':
            assert req.full_url.endswith('/me/mailFolders/drafts/messages/' + quote(MESSAGE_ID,safe=''))
            assert req.get_header('If-match') == TAG
            body = json.loads(req.data)
            assert 'body' not in body and 'attachments' not in body and 'bccRecipients' not in body
            assert body == {
                'subject': META['subject'],
                'toRecipients': [{'emailAddress': {'address':'artist@example.com'}}],
                'ccRecipients': [],
            }
            return Response({'id':MESSAGE_ID, 'isDraft':True})
        if '/mailFolders/drafts/messages/' in req.full_url:
            post = i >= 4
            record = {
                'id': MESSAGE_ID, 'parentFolderId': 'draft-folder', 'isDraft': True,
                'hasAttachments': False, 'bccRecipients': [],
                '@odata.etag': TAG2 if post else TAG,
                'subject': META['subject'] if post else 'Before',
                'toRecipients': ([{'emailAddress': {'address':'artist@example.com'}}] if post else []),
                'ccRecipients': [],
                'body': {'contentType':'html', 'content': HTML},
            }
            record.update(self.after if post else self.before)
            return Response(record)
        if '/mailFolders/drafts?$select=id' in req.full_url:
            return Response({'id':'draft-folder'})
        raise AssertionError(req.full_url)


class GraphRichDraftTests(unittest.TestCase):
    def test_load_returns_no_raw_html_and_performs_only_gets(self):
        tx = RichTransport()
        result = rich.read_rich_draft_metadata(TOKEN, MESSAGE_ID, opener=tx)
        self.assertEqual(result['metadata']['subject'], 'Before')
        self.assertEqual(result['bodyPreview'], 'Original formatted mail.')
        self.assertEqual(result['etag'], TAG)
        self.assertNotIn('_bodyHash', result)
        self.assertNotIn(HTML, json.dumps(result))
        self.assertEqual([req.get_method() for req in tx.calls], ['GET','GET'])

    def test_one_patch_preserves_original_html_and_rechecks_etag(self):
        tx = RichTransport(); preflight=[]
        result = rich.save_rich_draft_metadata(
            TOKEN, MESSAGE_ID, etag=TAG, metadata=META, opener=tx,
            before_write=lambda: preflight.append(len(tx.calls)),
        )
        self.assertEqual(result['metadata'], META)
        self.assertTrue(result['changed'])
        self.assertEqual(result['etag'], TAG2)
        self.assertEqual(preflight, [2])
        self.assertEqual([req.get_method() for req in tx.calls], ['GET','GET','PATCH','GET','GET'])

    def test_changed_html_is_never_confirmed(self):
        tx = RichTransport(after={'body':{'contentType':'html','content':'<b>lost</b>'}})
        with self.assertRaises(MicrosoftGraphError) as err:
            rich.save_rich_draft_metadata(TOKEN, MESSAGE_ID, etag=TAG, metadata=META, opener=tx)
        self.assertEqual(err.exception.code, 'microsoft_draft_edit_unconfirmed')
        self.assertEqual(sum(req.get_method()=='PATCH' for req in tx.calls), 1)

    def test_failed_or_ambiguous_patch_never_retries(self):
        for failed_at in (3,4,5):
            tx = RichTransport(fail_at=failed_at)
            with self.subTest(failed_at=failed_at),self.assertRaises(MicrosoftGraphError):
                rich.save_rich_draft_metadata(TOKEN, MESSAGE_ID, etag=TAG, metadata=META, opener=tx)
            self.assertEqual(sum(req.get_method()=='PATCH' for req in tx.calls), 1)

    def test_uncertain_record_and_attachments_never_patch(self):
        abnormal = (
            {'hasAttachments': True},
            {'bccRecipients':[{'emailAddress':{'address':'hidden@example.com'}}]},
            {'body':{'contentType':'text','content':'not rich'}},
            {'body':{'contentType':'html','content':'<img src="cid:inline-image" />'}},
            {'id':'other-message'},
            {'isDraft':False},
            {'parentFolderId':'inbox'},
            {'@odata.etag':None},
        )
        for value in abnormal:
            tx = RichTransport(before=value)
            with self.subTest(value=value),self.assertRaises(MicrosoftGraphError):
                rich.save_rich_draft_metadata(TOKEN, MESSAGE_ID, etag=TAG, metadata=META, opener=tx)
            self.assertFalse(any(req.get_method()=='PATCH' for req in tx.calls))

    def test_stale_etag_and_body_field_never_make_graph_request(self):
        for value in ({**META,'body':'injection'}, {**META,'cc':None}, {**META,'to':['bad']}, {**META,'subject':'injected\r\nheader'}):
            tx = RichTransport()
            with self.subTest(value=value),self.assertRaises(MicrosoftGraphError):
                rich.save_rich_draft_metadata(TOKEN, MESSAGE_ID, etag=TAG, metadata=value, opener=tx)
            self.assertFalse(tx.calls)
        tx = RichTransport()
        with self.assertRaises(MicrosoftGraphError):
            rich.save_rich_draft_metadata(TOKEN, MESSAGE_ID, etag='W/"wrong"', metadata=META, opener=tx)
        self.assertFalse(any(req.get_method()=='PATCH' for req in tx.calls))

    def test_unchanged_draft_requires_no_patch(self):
        tx = RichTransport()
        result = rich.save_rich_draft_metadata(TOKEN, MESSAGE_ID, etag=TAG,
            metadata={'subject':'Before','to':[],'cc':[]}, opener=tx)
        self.assertFalse(result['changed'])
        self.assertEqual(len(tx.calls),2)


class RichDraftBoundaryTests(unittest.TestCase):
    def test_production_gate_precedes_owner_and_graph(self):
        with patch.dict(os.environ,{'VERCEL_ENV':'production'}), \
             patch.object(owned, 'read_owned_microsoft_rich_draft_metadata') as read, \
             patch.object(owned, 'save_owned_microsoft_editable_draft') as save:
            for action in ('load_rich','save_rich'):
                r = http.microsoft_draft_edit_response(_request({'action':action,
                    'mailboxId':MAILBOX,'providerMessageId':MESSAGE_ID}))
                self.assertEqual(r.status,403)
            read.assert_not_called();save.assert_not_called()

    def test_owner_scoped_load_and_save_and_no_body_input(self):
        record={'providerMessageId':MESSAGE_ID,'etag':TAG,'metadata':META,'bodyPreview':'Original'}
        with patch.dict(os.environ,{'VERCEL_ENV':'preview'}), \
             patch.object(http,'require_microsoft_request_origin'), \
             patch.object(owned,'read_owned_microsoft_rich_draft_metadata',return_value=record) as reader, \
             patch.object(owned,'save_owned_microsoft_editable_draft',return_value={**record,'changed':True}) as saver:
            r=http.microsoft_draft_edit_response(_request({'action':'load_rich',
                'mailboxId':MAILBOX,'providerMessageId':MESSAGE_ID}))
            self.assertEqual(r.status,200)
            reader.assert_called_once()
            r=http.microsoft_draft_edit_response(_request({'action':'save_rich',
                'mailboxId':MAILBOX,'providerMessageId':MESSAGE_ID,'etag':TAG,'edit':META}))
            self.assertEqual(r.status,200)
            self.assertTrue(saver.call_args.kwargs['metadata_only'])
            saver.assert_called_once()
            bad=http.microsoft_draft_edit_response(_request({'action':'save_rich',
                'mailboxId':MAILBOX,'providerMessageId':MESSAGE_ID,'etag':TAG,
                'edit':{**META,'body':'do not overwrite'}}))
            self.assertEqual(bad.status,400)
            saver.assert_called_once()

if __name__ == '__main__':
    unittest.main()
