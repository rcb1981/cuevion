"""No-network tests for conditional Microsoft text Drafts editing."""
import json
import os
import unittest
from urllib.error import URLError, HTTPError
from unittest.mock import patch

from api.inboxes import microsoft_draft_editor as graph
from api.inboxes import microsoft_draft_edit_http as http
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes import microsoft_production_pilot as policy
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes.test_microsoft_http import _request, MAILBOX, MESSAGE_ID

TOKEN='unit-test-access-token-1234567'
TAG='W/"CQAAABYAAA=="'
TAG2='W/"CQAAABYAAQ=="'
EDIT={'subject':'Demo','to':['artist@example.com'],'cc':[],'body':'Hello draft'}

class Response:
    def __init__(self, data, status=200):
        self.data=json.dumps(data).encode(); self.status=status
    def __enter__(self):return self
    def __exit__(self,*_):return False
    def read(self, n):return self.data[:n]

class GraphTransport:
    def __init__(self, *, fail=None, after=None, before=None):
        self.calls=[];self.fail=fail;self.after=after;self.before=before
    def __call__(self, req, timeout):
        self.calls.append(req)
        index=len(self.calls)
        if index==self.fail:raise URLError('network error')
        if req.get_method()=='PATCH':
            assert req.get_header('If-match')==TAG
            assert req.full_url.endswith('/me/mailFolders/drafts/messages/'+__import__('urllib.parse',fromlist=['quote']).quote(MESSAGE_ID,safe=''))
            assert json.loads(req.data)=={
                'subject':EDIT['subject'],
                'toRecipients':[{'emailAddress':{'address':'artist@example.com'}}],
                'ccRecipients':[],
                'body':{'contentType':'Text','content':'Hello draft'},
            }
            return Response({'id':MESSAGE_ID, 'isDraft':True})
        if '/mailFolders/drafts/messages/' in req.full_url:
            current=(self.after if index>=4 else self.before) or {}
            tag=TAG2 if index>=4 else TAG
            return Response({
                'id':MESSAGE_ID, 'parentFolderId':'draft-folder', 'isDraft':True,
                'hasAttachments':False,'@odata.etag':tag,
                'subject':'Old draft' if index<4 else EDIT['subject'],
                'body':{'contentType':'text','content':'Old body' if index<4 else EDIT['body']},
                'toRecipients':[] if index<4 else [{'emailAddress':{'address':'artist@example.com'}}],
                'ccRecipients':[], **current,
            })
        if '/mailFolders/drafts?$select=id' in req.full_url:
            return Response({'id':'draft-folder'})
        raise AssertionError('bad Graph request '+req.full_url)

class DraftEditorGraphTests(unittest.TestCase):
    def test_load_is_exact_and_no_network_mutation(self):
        t=GraphTransport()
        x=graph.read_editable_microsoft_draft(TOKEN,MESSAGE_ID,opener=t)
        self.assertEqual(x['etag'],TAG)
        self.assertEqual(x['edit']['subject'],'Old draft')
        self.assertEqual([r.get_method() for r in t.calls],['GET','GET'])
        self.assertTrue(all(r.get_header('Prefer')=='IdType="ImmutableId"' for r in t.calls))

    def test_successful_save_uses_one_conditional_patch_and_confirmed_readback(self):
        t=GraphTransport();proof=[]
        result=graph.save_exact_microsoft_draft(TOKEN,MESSAGE_ID,etag=TAG,edit=EDIT,opener=t,
            before_write=lambda:proof.append(len(t.calls)))
        self.assertEqual(result['edit'],EDIT)
        self.assertEqual(result['etag'],TAG2)
        self.assertTrue(result['changed'])
        self.assertEqual(proof,[2])
        self.assertEqual([r.get_method() for r in t.calls],['GET','GET','PATCH','GET','GET'])

    def test_stale_or_missing_etag_never_patches(self):
        for etag in ('W/"OTHER"','missing','',None):
            t=GraphTransport()
            with self.subTest(etag=etag),self.assertRaises(MicrosoftGraphError):
                graph.save_exact_microsoft_draft(TOKEN,MESSAGE_ID,etag=etag,edit=EDIT,opener=t)
            self.assertFalse(any(req.get_method()=='PATCH' for req in t.calls))

    def test_invalid_recipient_or_html_is_rejected_without_patch(self):
        for edit in (
            {**EDIT,'to':['friend@example.com'],'cc':['friend@example.com']},
            {**EDIT,'to':None},
            {**EDIT,'cc':None},
            {**EDIT,'to':['bad']},
            {**EDIT,'subject':'bad\r\nheader'},
            {**EDIT,'body':'\x00'},
        ):
            t=GraphTransport()
            with self.subTest(edit=edit),self.assertRaises(MicrosoftGraphError):
                graph.save_exact_microsoft_draft(TOKEN,MESSAGE_ID,etag=TAG,edit=edit,opener=t)
            self.assertFalse(t.calls)
        for abnormal in ({'hasAttachments':True},
                         {'body':{'contentType':'html','content':'<b>Hi</b>'}},
                         {'isDraft':False}, {'parentFolderId':'inbox-folder'},
                         {'@odata.etag':None}):
            t=GraphTransport(before=abnormal)
            with self.subTest(abnormal=abnormal),self.assertRaises(MicrosoftGraphError):
                graph.save_exact_microsoft_draft(TOKEN,MESSAGE_ID,etag=TAG,edit=EDIT,opener=t)
            self.assertFalse(any(req.get_method()=='PATCH' for req in t.calls))

    def test_timeout_or_uncertain_confirmation_never_retries(self):
        for failed_step in (3,4,5):
            t=GraphTransport(fail=failed_step)
            with self.subTest(failed_step=failed_step),self.assertRaises(MicrosoftGraphError):
                graph.save_exact_microsoft_draft(TOKEN,MESSAGE_ID,etag=TAG,edit=EDIT,opener=t)
            self.assertEqual(sum(r.get_method()=='PATCH' for r in t.calls),1)
        t=GraphTransport(after={'body':{'contentType':'text','content':'unexpected'}})
        with self.assertRaises(MicrosoftGraphError):
            graph.save_exact_microsoft_draft(TOKEN,MESSAGE_ID,etag=TAG,edit=EDIT,opener=t)
        self.assertEqual(sum(r.get_method()=='PATCH' for r in t.calls),1)

    def test_unchanged_request_does_not_write(self):
        t=GraphTransport()
        old={'subject':'Old draft','to':[],'cc':[],'body':'Old body'}
        response=graph.save_exact_microsoft_draft(TOKEN,MESSAGE_ID,etag=TAG,edit=old,opener=t)
        self.assertFalse(response['changed'])
        self.assertEqual(len(t.calls),2)

class DraftEditorHttpTests(unittest.TestCase):
    def test_production_gate_precedes_parsing_auth_and_graph(self):
        with patch.dict(os.environ,{'VERCEL_ENV':'production'}),patch.object(owned,'read_owned_microsoft_editable_draft') as read,patch.object(owned,'save_owned_microsoft_editable_draft') as save:
            for action in ('load','save'):
                r=http.microsoft_draft_edit_response(_request({'action':action,'mailboxId':MAILBOX,'providerMessageId':MESSAGE_ID}))
                self.assertEqual(r.status,403)
            read.assert_not_called(); save.assert_not_called()
    def test_load_and_save_are_owner_authorized_after_origin_proof(self):
        with patch.dict(os.environ,{'VERCEL_ENV':'preview'}),patch.object(http,'require_microsoft_request_origin'),patch.object(owned,'read_owned_microsoft_editable_draft',return_value={'providerMessageId':MESSAGE_ID,'etag':TAG,'edit':EDIT}) as reader,patch.object(owned,'save_owned_microsoft_editable_draft',return_value={'providerMessageId':MESSAGE_ID,'etag':TAG2,'edit':EDIT,'changed':True}) as saver:
            r=http.microsoft_draft_edit_response(_request({'action':'load','mailboxId':MAILBOX,'providerMessageId':MESSAGE_ID}))
            self.assertEqual(r.status,200)
            self.assertEqual(reader.call_count,1)
            req=_request({'action':'save','mailboxId':MAILBOX,'providerMessageId':MESSAGE_ID,'etag':TAG,'edit':EDIT})
            r=http.microsoft_draft_edit_response(req)
            self.assertEqual(r.status,200)
            self.assertEqual(saver.call_count,1)
            self.assertEqual(saver.call_args.kwargs['etag'],TAG)

    def test_invalid_payload_never_calls_owner(self):
        with patch.dict(os.environ,{'VERCEL_ENV':'preview'}),patch.object(http,'require_microsoft_request_origin'),patch.object(owned,'save_owned_microsoft_editable_draft') as saver:
            for bad in ({'action':'save','mailboxId':MAILBOX,'providerMessageId':MESSAGE_ID,'etag':TAG,'edit':{**EDIT,'to':None}},
                        {'action':'load','mailboxId':MAILBOX,'providerMessageId':'../other'},
                        {'action':'save','mailboxId':MAILBOX,'providerMessageId':MESSAGE_ID,'etag':TAG,'edit':EDIT,'externalUrl':'https://malicious.com'}):
                with self.subTest(bad=bad):
                    r=http.microsoft_draft_edit_response(_request(bad))
                    self.assertEqual(r.status,400)
            saver.assert_not_called()

class DraftEditorOwnedLeaseTests(unittest.TestCase):
    def test_invalid_metadata_only_types_reject_before_authority_lease_token_or_graph(self):
        class Truthy:
            def __bool__(self):
                raise AssertionError("Never coerce internal metadata_only input")

        with patch.object(owned, "resolve_owned_microsoft_mailbox") as authority, patch.object(
            owned, "acquire_mailbox_mutation_lease",
        ) as lease, patch.object(owned, "_owned_token") as token, patch.object(
            graph, "save_exact_microsoft_draft",
        ) as plain_save, patch(
            "api.inboxes.microsoft_rich_draft.save_rich_draft_metadata",
        ) as rich_save:
            for invalid in (None, 0, 1, 0.0, "false", "true", [], {}, (), Truthy()):
                with self.subTest(kind=type(invalid).__name__), self.assertRaises(MicrosoftGraphError) as caught:
                    owned.save_owned_microsoft_editable_draft(
                        (), MAILBOX, MESSAGE_ID, etag=TAG, edit=EDIT, metadata_only=invalid,
                    )
                self.assertEqual(caught.exception.code, "microsoft_draft_edit_invalid")
            for operation in (authority, lease, token, plain_save, rich_save):
                operation.assert_not_called()

    def test_edit_is_leased_scoped_and_releases_after_confirmed_save(self):
        from types import SimpleNamespace
        owner=SimpleNamespace(owner_email='owner@example.com',mailbox_id=MAILBOX)
        with patch.object(owned,'resolve_owned_microsoft_mailbox',return_value=owner), \
             patch.object(owned,'acquire_mailbox_mutation_lease',return_value={'status':'acquired','token':'lease-token'}) as acquire, \
             patch.object(owned,'release_mailbox_mutation_lease',return_value={'status':'released'}) as release, \
             patch.object(owned,'_owned_token',return_value=(TOKEN,owner)) as token, \
             patch.object(owned,'_verify_unchanged_owned_mailbox') as verify, \
             patch.object(graph,'save_exact_microsoft_draft',return_value={'providerMessageId':MESSAGE_ID,'etag':TAG2,'edit':EDIT,'changed':True}) as action:
            value=owned.save_owned_microsoft_editable_draft(object(),MAILBOX,MESSAGE_ID,etag=TAG,edit=EDIT)
            self.assertTrue(value['changed'])
            acquire.assert_called_once_with('owner@example.com',MAILBOX)
            release.assert_called_once_with('owner@example.com',MAILBOX,'lease-token')
            self.assertEqual(token.call_args.kwargs['require_read_write'],True)
            self.assertEqual(action.call_args.kwargs['etag'],TAG)
            self.assertTrue(callable(action.call_args.kwargs['before_write']))
            self.assertEqual(verify.call_count,1)

    def test_mutation_error_releases_lock_without_retry(self):
        from types import SimpleNamespace
        owner=SimpleNamespace(owner_email='owner@example.com',mailbox_id=MAILBOX)
        with patch.object(owned,'resolve_owned_microsoft_mailbox',return_value=owner), \
             patch.object(owned,'acquire_mailbox_mutation_lease',return_value={'status':'acquired','token':'lease-token'}), \
             patch.object(owned,'release_mailbox_mutation_lease',return_value={'status':'released'}) as release, \
             patch.object(owned,'_owned_token',return_value=(TOKEN,owner)), \
             patch.object(graph,'save_exact_microsoft_draft',side_effect=MicrosoftGraphError('microsoft_draft_edit_unconfirmed')) as write:
            with self.assertRaises(MicrosoftGraphError):
                owned.save_owned_microsoft_editable_draft(object(),MAILBOX,MESSAGE_ID,etag=TAG,edit=EDIT)
            self.assertEqual(write.call_count,1)
            self.assertEqual(release.call_count,1)

    def test_held_lease_blocks_without_token_access(self):
        from types import SimpleNamespace
        owner=SimpleNamespace(owner_email='owner@example.com',mailbox_id=MAILBOX)
        with patch.object(owned,'resolve_owned_microsoft_mailbox',return_value=owner), \
             patch.object(owned,'acquire_mailbox_mutation_lease',return_value={'status':'held'}), \
             patch.object(owned,'_owned_token') as token:
            with self.assertRaises(owned.MicrosoftMailboxAccessError) as ctx:
                owned.save_owned_microsoft_editable_draft(object(),MAILBOX,MESSAGE_ID,etag=TAG,edit=EDIT)
            self.assertEqual(ctx.exception.code,'microsoft_mutation_in_progress')
            token.assert_not_called()
