"""Two independent servers; isolated temporary SQLite CAS, fake Graph, no network."""
from microsoft_send_multiprocess_test import DiskCAS, FRONTEND
import json
import multiprocessing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def worker(path,n,mode,barrier):
    from api.inboxes.test_microsoft_compose_completion import SegmentedComposeTests, rid
    from api.inboxes.test_microsoft_compose_draft import AUTH
    from api.inboxes import microsoft_compose_draft_ledger as ledger
    from api.inboxes import microsoft_mailbox_access as owned
    from api.inboxes.microsoft_graph import MicrosoftGraphError
    f=SegmentedComposeTests();f.setUp();storage=DiskCAS(path)
    waited=False
    def read(config,key):
        nonlocal waited
        result=storage.read(config,key)
        if mode=='migration' and key==ledger.context(AUTH)[1] and not waited:
            waited=True;barrier.wait(timeout=15)
        return result
    try:
        with patch.object(ledger.base.store,'_read_durable_record_snapshot',side_effect=read),patch.object(
            ledger.base.store,'_write_google_durable_record_if_unchanged',side_effect=storage.write),patch.object(
            owned,'acquire_mailbox_mutation_lease',side_effect=storage.acquire),patch.object(
            owned,'release_mailbox_mutation_lease',side_effect=storage.release):
            try:
                if mode=='migration':result=ledger.reserve(AUTH,rid(100+n),'create',None,ledger.digest(n))['new']
                else:
                    barrier.wait(timeout=15);result=f.save(request=rid(100))['outcome']
            except MicrosoftGraphError:result='blocked'
            with sqlite3.connect(path) as db:db.execute('INSERT INTO results VALUES (?,?,?)',(n,json.dumps(result),len(f.graph.writes)))
    finally:f.doCleanups()


class ComposeProcessTests(unittest.TestCase):
    def pair(self,mode):
        from api.inboxes.test_microsoft_compose_completion import SegmentedComposeTests
        from api.inboxes.test_microsoft_compose_draft import AUTH
        from api.inboxes import microsoft_compose_draft_ledger as ledger
        f=SegmentedComposeTests();f.setUp()
        try:
            original=f.legacy()
            with tempfile.TemporaryDirectory(prefix='ms-compose-cas-') as temp:
                path=str(Path(temp)/'isolated.sqlite')
                with sqlite3.connect(path) as db:
                    db.execute('CREATE TABLE records(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                    db.execute('CREATE TABLE lease(token TEXT NOT NULL)')
                    db.execute('CREATE TABLE results(n INTEGER,result TEXT,writes INTEGER)')
                    db.executemany('INSERT INTO records VALUES (?,?)',f.cas.records.items())
                ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(2)
                processes=[ctx.Process(target=worker,args=(path,n,mode,barrier)) for n in (1,2)]
                for process in processes:process.start()
                try:
                    for process in processes:
                        process.join(timeout=45);self.assertFalse(process.is_alive());self.assertEqual(process.exitcode,0)
                    with sqlite3.connect(path) as db:results=db.execute('SELECT result,writes FROM results').fetchall()
                    self.assertEqual(len(results),2)
                    if mode=='save':self.assertEqual(sum(x[1] for x in results),1)
                    else:self.assertEqual(sum(json.loads(x[0]) is True for x in results),1)
                    storage=DiskCAS(path)
                    with patch.object(ledger.base.store,'_read_durable_record_snapshot',side_effect=storage.read):
                        root=ledger.read(AUTH)[0];self.assertEqual(root['count'],33)
                        self.assertEqual(ledger.segments.segment(AUTH,root['legacy'])['original'],original)
                        self.assertTrue(ledger.operation(AUTH,next(iter(original['operations'])))['blocked'])
                finally:
                    for process in processes:
                        if process.is_alive():process.terminate();process.join(timeout=5)
        finally:f.doCleanups()
    def test_two_servers_migrate_without_lost_history(self):self.pair('migration')
    def test_two_servers_replaying_save_write_at_most_once(self):self.pair('save')

if __name__=='__main__':unittest.main(verbosity=2)
