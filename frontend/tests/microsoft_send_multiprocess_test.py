"""Real independent Python server processes; fake Graph and temporary SQLite CAS.

Run separately from the backend runner (which deliberately forbids child
processes). No credentials, network, Redis, Graph or application database used.
"""
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

FRONTEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FRONTEND))
os.environ.clear()
os.environ.update({'PATH': '/usr/bin:/bin', 'TZ': 'Europe/Amsterdam', 'PYTHONDONTWRITEBYTECODE': '1'})
sys.dont_write_bytecode = True

network_attempts = []

def deny_network(event, args):
    if event.startswith('socket.'):
        network_attempts.append(event)
        raise AssertionError('NETWORK_FORBIDDEN')
sys.addaudithook(deny_network)


class DiskCAS:
    def __init__(self, path, barrier=None):
        self.path, self.barrier, self.waited = path, barrier, False
    def read(self, config, key):
        with sqlite3.connect(self.path, timeout=10) as db:
            row=db.execute('SELECT value FROM records WHERE key=?',(key,)).fetchone()
        if self.barrier is not None and not self.waited and key.startswith('cuevion:microsoft:existing-draft-send:v1:'):
            self.waited=True;self.barrier.wait(timeout=15)
        return (json.loads(row[0]),row[0],None) if row else (None,None,None)
    def write(self, config, key, expected, value):
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT value FROM records WHERE key=?',(key,)).fetchone()
            if (row[0] if row else None)!=expected:return None,{'code':'conflict'}
            db.execute('INSERT OR REPLACE INTO records VALUES (?,?)',(key,json.dumps(value,sort_keys=True,separators=(',',':'))))
        return value,None
    def acquire(self,*args):
        token=str(os.getpid())
        with sqlite3.connect(self.path,timeout=10) as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT token FROM lease').fetchone():return {'status':'held'}
            db.execute('INSERT INTO lease VALUES (?)',(token,))
        return {'status':'acquired','token':token}
    def release(self,owner,mailbox,token):
        with sqlite3.connect(self.path,timeout=10) as db:
            db.execute('DELETE FROM lease WHERE token=?',(token,))
        return {'status':'released'}


def worker(path, number, mode, barrier):
    from api.inboxes.test_microsoft_draft_send import SendFixture
    from api.inboxes.test_microsoft_draft_attachment_foundation import AUTH
    from api.inboxes import microsoft_draft_send_index as index
    from api.inboxes import microsoft_outbound_ledger as outbound
    from api.inboxes import microsoft_mailbox_access as owned
    from api.inboxes.microsoft_graph import MicrosoftGraphError
    fixture=SendFixture();fixture.setUp()
    storage=DiskCAS(path,barrier if mode=='migration' else None)
    patches=[patch.object(outbound.store,'_read_durable_record_snapshot',side_effect=storage.read),
             patch.object(outbound.store,'_write_google_durable_record_if_unchanged',side_effect=storage.write),
             patch.object(owned,'acquire_mailbox_mutation_lease',side_effect=storage.acquire),
             patch.object(owned,'release_mailbox_mutation_lease',side_effect=storage.release)]
    for p in patches:p.start()
    try:
        if mode=='send':barrier.wait(timeout=15)
        try:
            if mode=='migration':result=index.reserve(AUTH,str(number)*43,'new-'+str(number),'b'*64)
            else:result=fixture.send(str(number)*43)['outcome']
        except (MicrosoftGraphError,outbound.OutboundLedgerError):result='blocked'
        with sqlite3.connect(path,timeout=10) as db:
            db.execute('INSERT INTO results VALUES (?,?,?,?)',(os.getpid(),number,json.dumps(result),len(fixture.graph.sends)))
    finally:
        for p in reversed(patches):p.stop()
        fixture.doCleanups()
        if network_attempts:raise AssertionError(network_attempts)


class ProcessTests(unittest.TestCase):
    def run_pair(self,mode):
        from api.inboxes.test_microsoft_draft_send_history import legacy_records
        from api.inboxes.test_microsoft_draft_attachment_foundation import CAS,AUTH
        from api.inboxes import microsoft_draft_send_history as history
        from api.inboxes import microsoft_outbound_ledger as outbound
        with tempfile.TemporaryDirectory(prefix='cuevion-ms-process-') as temp:
            path=str(Path(temp)/'isolated.sqlite')
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE records(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                db.execute('CREATE TABLE lease(token TEXT NOT NULL)')
                db.execute('CREATE TABLE results(pid INTEGER,n INTEGER,result TEXT,sends INTEGER)')
                if mode=='migration':
                    memory=CAS()
                    with patch.object(outbound.store,'_resolve_durable_store_config',return_value={'isolated':True}):original=legacy_records(memory)
                    db.executemany('INSERT INTO records VALUES (?,?)',memory.records.items())
            ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(2)
            processes=[ctx.Process(target=worker,args=(path,n,mode,barrier)) for n in (1,2)]
            for process in processes:process.start()
            try:
                for process in processes:
                    process.join(timeout=45)
                    self.assertFalse(process.is_alive(),'worker stalled')
                    self.assertEqual(process.exitcode,0)
                with sqlite3.connect(path) as db:results=db.execute('SELECT pid,result,sends FROM results').fetchall()
                self.assertEqual(len({r[0] for r in results}),2)
                if mode=='send':
                    self.assertEqual(sum(r[2] for r in results),1)
                    self.assertEqual(sorted(json.loads(r[1]) for r in results),['blocked','confirmed_performed'])
                else:
                    self.assertEqual(sum(json.loads(r[1]) is True for r in results),1)
                    storage=DiskCAS(path)
                    with patch.object(outbound.store,'_resolve_durable_store_config',return_value={'isolated':True}),patch.object(
                        outbound.store,'_read_durable_record_snapshot',side_effect=storage.read):
                        root,_=history.read_root(AUTH)
                        self.assertEqual(root['count'],33)
                        self.assertEqual(history.segment(AUTH,root['legacy'])['original'],original)
                        for identifier,item in original['attempts'].items():
                            self.assertEqual(history.entry(AUTH,identifier)['item'],item)
            finally:
                for process in processes:
                    if process.is_alive():process.terminate();process.join(timeout=5)

    def test_two_independent_servers_send_exactly_once(self):self.run_pair('send')
    def test_two_independent_servers_migrate_one_complete_root(self):self.run_pair('migration')

if __name__=='__main__':unittest.main(verbosity=2)
