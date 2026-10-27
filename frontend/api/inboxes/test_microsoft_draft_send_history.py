"""Permanent segmented history: isolated CAS, fake Graph, no live mutations."""
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch
from api.inboxes.test_microsoft_draft_send import SendFixture, SendGraph
from api.inboxes.test_microsoft_draft_attachment_foundation import AUTH, OP
from api.inboxes.test_microsoft_graph import TOKEN
from api.inboxes import microsoft_draft_send_index as index
from api.inboxes import microsoft_draft_send_history as history
from api.inboxes import microsoft_draft_send_manifest as manifest
from api.inboxes import microsoft_draft_send_service as service
from api.inboxes import microsoft_draft_send_policy as policy
from api.inboxes import microsoft_outbound_ledger as outbound
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes.microsoft_draft_attachment_snapshot import digest
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes.test_microsoft_http import _body


def attempt(n):
    # Deliberately anti-lexical IDs: ordering must never come from request IDs.
    return 'a' + str(999999 - n).zfill(42)


def legacy_records(cas, count=32):
    root = {'v':1,'scope':history.context(AUTH)[2],'attempts':{}}
    for n in range(count):
        aid=attempt(n);draft='legacy-'+str(n)
        item={'draftId':draft,'scope':outbound._scope_hash(AUTH),'binding':digest(['legacy',n]),'reason':None}
        root['attempts'][aid]=item
        phase=['send_started','send_not_started','sent_copy_confirmed'][n%3]
        cas.records[outbound._key(AUTH,aid)]=json.dumps({'v':1,'scope':item['scope'],'digest':'c'*64,
            'attemptId':aid,'phase':phase,'draftId':draft,'updatedAt':'2026-10-24T12:00:00+00:00'})
        if n%3==2:
            key=manifest.PREFIX+digest([AUTH.owner_email,AUTH.mailbox_id,aid])
            cas.records[key]=json.dumps({'v':1,'scope':root['scope'],'attemptId':aid,'draftId':draft,'binding':item['binding'],
                'approved':{'fields':{k:'d'*64 for k in manifest.FIELDS},'attachments':[]},'result':'mismatch'})
    cas.records[history.context(AUTH)[1]]=json.dumps(root,sort_keys=True,separators=(',',':'))
    return root


class HistoryTests(SendFixture):
    def add(self,n,*,uncertain=False,mismatch=False):
        self.graph=SendGraph();did='draft-'+str(n)
        self.graph.draft_id=did
        self.graph.messages={did:{**self.graph.messages['draft-1'],'id':did}}
        self.graph.rows={did:self.graph.rows['draft-1']}
        if uncertain:self.graph.visible=False
        if mismatch:self.graph.on_send=lambda g:g.bytes.update({'ordinary':b'evil'})
        current=service.drafts.read_current(TOKEN,AUTH,did,opener=self.graph)
        result=service.send_owned({},AUTH.mailbox_id,did,attempt(n),etag=current.snapshot.etag,
                                  snapshot_hash=current.snapshot.fingerprint,opener=self.graph)
        return result

    def all_pages(self):
        before=None;entries=[]
        while True:
            result=service.history_owned({},AUTH.mailbox_id,before=before)
            entries.extend(result['entries']);before=result['nextCursor']
            if before is None:return entries

    def test_120_valid_sends_exceed_both_old_limits_without_losing_any_blockers(self):
        sent=0
        for n in range(120):
            result=self.add(n,uncertain=n==0,mismatch=n==1)
            self.assertEqual(result['outcome'],'uncertain' if n==0 else 'performed_content_mismatch' if n==1 else 'confirmed_performed')
            sent+=len(self.graph.sends)
        self.assertEqual(sent,120)
        root,raw=history.read_root(AUTH)
        self.assertEqual(root['count'],120);self.assertLess(len(raw),2048)
        self.assertGreater(sum(len(v) for k,v in self.cas.records.items() if k.startswith(history.SEGMENTS)),64*1024)
        entries=self.all_pages()
        self.assertEqual([e['sequence'] for e in entries],list(range(120,0,-1)))
        self.assertEqual(len({e['receipt']['attemptId'] for e in entries}),120)
        self.assertEqual(entries[-1]['receipt']['outcome'],'uncertain')
        self.assertEqual(entries[-2]['receipt']['outcome'],'performed_content_mismatch')
        for n in (0,1,60,119):
            item=index.get_item(AUTH,attempt(n))
            self.assertEqual(index.lookup(AUTH,attempt(n),item['binding'])['attemptId'],attempt(n))
            with self.assertRaises(MicrosoftGraphError):index.reserve(AUTH,'n'*43,'draft-'+str(n),'b'*64)
        # The current draft barrier is returned independently of the first page.
        first=service.history_owned({},AUTH.mailbox_id,draft_id='draft-0')
        self.assertEqual(first['currentDraft']['receipt']['outcome'],'uncertain')
        self.assertNotIn(attempt(0),[e['receipt']['attemptId'] for e in first['entries']])
        with self.assertRaises(MicrosoftGraphError):service.inspect_owned({},AUTH.mailbox_id)

    def test_full_legacy_index_migrates_without_deleting_unknown_mismatch_or_receipts(self):
        original=legacy_records(self.cas);saved=copy.deepcopy(self.cas.records)
        self.assertEqual(self.add(100)['outcome'],'confirmed_performed')
        current,_=history.read_root(AUTH)
        self.assertEqual(current['count'],33)
        self.assertEqual(history.segment(AUTH,current['legacy'])['original'],original)
        for key,value in saved.items():
            if key!=history.context(AUTH)[1]:self.assertEqual(self.cas.records[key],value)
        self.assertEqual(index.lookup(AUTH,attempt(0),original['attempts'][attempt(0)]['binding'])['outcome'],'uncertain')
        self.assertEqual(index.lookup(AUTH,attempt(2),original['attempts'][attempt(2)]['binding'])['outcome'],'performed_content_mismatch')
        for n in (0,2):
            with self.assertRaises(MicrosoftGraphError):index.assert_mutable(AUTH,'legacy-'+str(n))
        index.assert_mutable(AUTH,'legacy-1')
        self.assertTrue(all(e['createdAt'] is None for e in self.all_pages() if e['sequence']<=32))

    def test_old_request_id_after_migration_cannot_become_new_claim(self):
        original=legacy_records(self.cas);self.add(100)
        before=copy.deepcopy(self.cas.records)
        for n in (0,1,2):
            item=original['attempts'][attempt(n)]
            self.assertFalse(index.reserve(AUTH,attempt(n),item['draftId'],item['binding']))
            with self.assertRaises(MicrosoftGraphError):index.reserve(AUTH,attempt(n),'another-draft','x'*64)
        self.assertEqual(self.cas.records,before);self.assertEqual(len(self.graph.sends),1)

    def test_same_id_send_replay_after_many_segments_has_no_graph_requests(self):
        self.send()
        for n in range(100,112):self.add(n)
        self.graph=SendGraph();before=len(self.graph.calls)
        self.assertEqual(self.send()['outcome'],'confirmed_performed')
        self.assertEqual(len(self.graph.calls),before);self.assertEqual(self.graph.sends,[])

    def test_interrupted_segment_writes_leave_original_index_readable(self):
        original=legacy_records(self.cas);original_raw=self.cas.records[history.context(AUTH)[1]]
        write=self.cas.write;count=0
        def interrupted(config,key,expected,value):
            nonlocal count
            if key.startswith(history.SEGMENTS):
                count+=1
                if count==8:return None,{'code':'interrupted'}
            return write(config,key,expected,value)
        with patch.object(outbound.store,'_write_google_durable_record_if_unchanged',side_effect=interrupted),self.assertRaises(MicrosoftGraphError):
            index.reserve(AUTH,'n'*43,'new-draft','e'*64)
        self.assertEqual(self.cas.records[history.context(AUTH)[1]],original_raw)
        self.assertEqual(history.read_root(AUTH)[0],original)
        with self.assertRaises(MicrosoftGraphError):index.assert_mutable(AUTH,'legacy-0')
        self.assertTrue(index.reserve(AUTH,'n'*43,'new-draft','e'*64))
        self.assertEqual(history.read_root(AUTH)[0]['count'],33)

    def test_failure_before_root_cas_keeps_old_history_and_all_barriers(self):
        original=legacy_records(self.cas);write=self.cas.write;key=history.context(AUTH)[1]
        def fail_root(config,k,expected,value):
            return (None,{'code':'failed'}) if k==key else write(config,k,expected,value)
        with patch.object(outbound.store,'_write_google_durable_record_if_unchanged',side_effect=fail_root),self.assertRaises(MicrosoftGraphError):
            index.reserve(AUTH,'n'*43,'new-draft','e'*64)
        self.assertEqual(history.read_root(AUTH)[0],original)
        self.assertTrue(index.reserve(AUTH,'n'*43,'new-draft','e'*64))

    def test_lost_migration_root_ack_retains_uncertain_claim_and_no_send(self):
        legacy_records(self.cas);write=self.cas.write;key=history.context(AUTH)[1]
        def lose(config,k,expected,value):
            result=write(config,k,expected,value)
            return (None,{'code':'lost'}) if k==key else result
        with patch.object(outbound.store,'_write_google_durable_record_if_unchanged',side_effect=lose),self.assertRaises(MicrosoftGraphError):
            self.send()
        self.assertEqual(self.graph.sends,[])
        self.assertEqual(self.send()['outcome'],'uncertain')
        self.assertEqual(history.read_root(AUTH)[0]['count'],33)

    def test_storage_failures_unreadable_segments_and_missing_root_fail_closed(self):
        self.add(10);saved=copy.deepcopy(self.cas.records)
        root,_=history.read_root(AUTH)
        for mode in ('root','missing','corrupt','archive'):
            self.cas.records=copy.deepcopy(saved)
            if mode=='root':del self.cas.records[history.context(AUTH)[1]]
            else:
                key=history._segment_key(root['scope'],root['attempts'])
                if mode=='missing':del self.cas.records[key]
                else:self.cas.records[key]='{"v":2}'
            with self.assertRaises(MicrosoftGraphError):index.lookup(AUTH,attempt(10),'e'*64)
        self.cas.records=saved;self.cas.bad_read=True
        with self.assertRaises(MicrosoftGraphError):index.reserve(AUTH,attempt(11),'new','e'*64)
        self.assertEqual(len(self.graph.sends),1)

    def test_legacy_archive_corruption_blocks_after_migration(self):
        legacy_records(self.cas);self.add(100);root,_=history.read_root(AUTH)
        del self.cas.records[history._segment_key(root['scope'],root['legacy'])]
        with self.assertRaises(MicrosoftGraphError):service.history_owned({},AUTH.mailbox_id)
        with self.assertRaises(MicrosoftGraphError):index.reserve(AUTH,'n'*43,'new','e'*64)

    def test_two_concurrent_migrations_publish_one_complete_root(self):
        original=legacy_records(self.cas);self.cas.race=threading.Barrier(2)
        def run(n):
            try:return index.reserve(AUTH,attempt(100+n),'new-'+str(n),'e'*64)
            except MicrosoftGraphError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,[0,1]))
        self.cas.race=None
        self.assertEqual(sum(results),1)
        root,_=history.read_root(AUTH);self.assertEqual(root['count'],33)
        self.assertEqual(history.segment(AUTH,root['legacy'])['original'],original)
        loser=results.index(False)
        self.assertTrue(index.reserve(AUTH,attempt(100+loser),'new-'+str(loser),'e'*64))
        self.assertEqual(history.read_root(AUTH)[0]['count'],34)

    def test_reconnect_disabled_flags_preserve_paged_history_and_old_draft_barrier(self):
        legacy_records(self.cas);self.add(100);changed=replace(AUTH,credential_generation='z'*43)
        with patch.object(owned,'resolve_owned_microsoft_mailbox',return_value=changed),patch.object(policy,'_DRAFT_SEND_RELEASED',False):
            first=service.history_owned({},AUTH.mailbox_id,draft_id='legacy-0')
            second=service.history_owned({},AUTH.mailbox_id,before=first['nextCursor'],draft_id='legacy-0')
            self.assertEqual(len(first['entries'])+len(second['entries']),33)
            self.assertEqual(first['currentDraft']['receipt']['outcome'],'uncertain')
            with self.assertRaises(MicrosoftGraphError):index.assert_mutable(changed,'legacy-0')
        self.assertEqual(len(self.graph.sends),1)

    def test_no_start_retry_with_new_id_preserves_every_old_request_id(self):
        for n in range(105):
            self.assertEqual(self.send(attempt(n),fingerprint='0'*64)['outcome'],'confirmed_not_started')
        self.assertEqual(history.read_root(AUTH)[0]['count'],105)
        self.assertEqual(self.send(attempt(110))['outcome'],'confirmed_performed')
        for n in (0,30,100):
            self.assertEqual(self.send(attempt(n),fingerprint='0'*64)['outcome'],'confirmed_not_started')
        self.assertEqual(len(self.graph.sends),1)

    def test_creation_sequence_remains_chronological_when_server_clock_moves_back(self):
        from datetime import datetime, timezone
        for n,year in [(1,2026),(2,2025)]:
            with patch.object(history,'datetime') as clock:
                clock.now.return_value=datetime(year,1,1,tzinfo=timezone.utc)
                clock.fromisoformat.side_effect=datetime.fromisoformat
                self.add(n)
        entries=self.all_pages()
        self.assertEqual([e['sequence'] for e in entries],[2,1])
        self.assertEqual([e['receipt']['attemptId'] for e in entries],[attempt(2),attempt(1)])

    def test_http_history_validates_cursor_identity_and_owner(self):
        legacy_records(self.cas);self.add(100)
        request={'action':'history','mailboxId':AUTH.mailbox_id,'before':None,'providerMessageId':'legacy-0'}
        result=_body(self.call_send(request));self.assertTrue(result['ok']);self.assertEqual(len(result['entries']),32)
        self.assertEqual(result['currentDraft']['receipt']['outcome'],'uncertain')
        for value in (True,0,-1,'2',1.5,9007199254740992):
            self.assertEqual(self.call_send({**request,'before':value}).status,400)
        self.assertEqual(self.call_send({**request,'before':999}).status,409)
        self.assertEqual(self.call_send({**request,'providerMessageId':'../other'}).status,400)

    def test_page_cursor_does_not_skip_old_entries_when_new_sends_arrive(self):
        legacy_records(self.cas);self.add(100)
        first=service.history_owned({},AUTH.mailbox_id)
        self.add(101)
        second=service.history_owned({},AUTH.mailbox_id,before=first['nextCursor'])
        self.assertEqual([e['sequence'] for e in first['entries']+second['entries']],list(range(33,0,-1)))

    def test_interrupted_first_root_publication_never_looks_like_empty_history(self):
        write=self.cas.write;key=history.context(AUTH)[1]
        def interrupted(config,k,expected,value):
            return (None,{'code':'interrupted'}) if k==key else write(config,k,expected,value)
        with patch.object(outbound.store,'_write_google_durable_record_if_unchanged',side_effect=interrupted),self.assertRaises(MicrosoftGraphError):
            self.send()
        self.assertIn(key+':initialized',self.cas.records)
        self.assertNotIn(key,self.cas.records)
        with self.assertRaises(MicrosoftGraphError):self.send()
        self.assertEqual(self.graph.sends,[])

    def test_legacy_mixed_attempts_for_same_draft_keep_the_blocking_anchor(self):
        legacy=legacy_records(self.cas)
        legacy['attempts'][attempt(1)]['draftId']='legacy-0'
        self.cas.records[history.context(AUTH)[1]]=json.dumps(legacy)
        record=json.loads(self.cas.records[outbound._key(AUTH,attempt(1))]);record['draftId']='legacy-0'
        self.cas.records[outbound._key(AUTH,attempt(1))]=json.dumps(record)
        self.add(100)
        with self.assertRaises(MicrosoftGraphError):index.assert_mutable(AUTH,'legacy-0')
        current=service.history_owned({},AUTH.mailbox_id,draft_id='legacy-0')['currentDraft']
        self.assertEqual(current['receipt']['outcome'],'uncertain')

    def test_mutation_and_duplicate_checks_read_paths_not_all_history(self):
        for n in range(110):index.reserve(AUTH,attempt(n),'draft-'+str(n),'b'*64)
        with patch.object(outbound.store,'_read_durable_record_snapshot',side_effect=self.cas.read) as read:
            self.assertEqual(index.lookup(AUTH,attempt(0),'b'*64)['outcome'],'uncertain')
            with self.assertRaises(MicrosoftGraphError):index.assert_mutable(AUTH,'draft-0')
        self.assertLess(read.call_count,100)

    def test_http_history_never_silently_truncates_a_page_chain(self):
        result=self.add(0)
        request={'action':'history','mailboxId':AUTH.mailbox_id,'before':None,'providerMessageId':None}
        invalid={'entries':[{'receipt':result,'sequence':50,'createdAt':None}],'nextCursor':None,'currentDraft':None}
        with patch.object(service,'history_owned',return_value=invalid):
            self.assertEqual(self.call_send(request).status,503)
