"""Local permit across separate processes/restarts; fake Graph + temporary CAS.

Run independently: the backend offline runner correctly forbids child processes.
The imported process harness removes inherited credentials and denies sockets.
"""
import json
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from microsoft_send_multiprocess_test import DiskCAS, network_attempts


def worker(path, cfg, number, barrier=None):
    from api.inboxes.test_microsoft_local_test_policy import LocalFixture
    from api.inboxes import microsoft_outbound_ledger as outbound
    from api.inboxes import microsoft_mailbox_access as owned
    from api.inboxes.microsoft_graph import MicrosoftGraphError
    fixture=LocalFixture();fixture.setUp();fixture.cfg=cfg;fixture.configure()
    disk=DiskCAS(path)
    try:
        with patch.object(outbound.store,'_read_durable_record_snapshot',side_effect=disk.read),patch.object(
                outbound.store,'_write_google_durable_record_if_unchanged',side_effect=disk.write),patch.object(
                owned,'acquire_mailbox_mutation_lease',side_effect=disk.acquire),patch.object(
                owned,'release_mailbox_mutation_lease',side_effect=disk.release):
            if barrier:barrier.wait(timeout=15)
            try:outcome=fixture.send(attempt=str(number)*43)['outcome']
            except MicrosoftGraphError:outcome='blocked'
            with sqlite3.connect(path,timeout=10) as db:
                db.execute('INSERT INTO results VALUES (?,?,?)',(number,outcome,len(fixture.graph.sends)))
    finally:
        fixture.doCleanups()
        if network_attempts:raise AssertionError(network_attempts)


class LocalProcessTests(unittest.TestCase):
    def test_two_servers_then_restart_preserve_single_consumed_authorization(self):
        from api.inboxes.test_microsoft_local_test_policy import LocalFixture
        from api.inboxes import microsoft_local_test_policy as local
        fixture=LocalFixture();fixture.setUp()
        cfg=fixture.cfg;records=dict(fixture.cas.records);fixture.doCleanups()
        with tempfile.TemporaryDirectory(prefix='cuevion-ms-local-policy-') as temp:
            path=str(Path(temp)/'isolated.sqlite')
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE records(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                db.execute('CREATE TABLE lease(token TEXT NOT NULL)')
                db.execute('CREATE TABLE results(n INTEGER,result TEXT,sends INTEGER)')
                db.executemany('INSERT INTO records VALUES (?,?)',records.items())
            ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(2)
            processes=[]
            def run(numbers, gate=None):
                group=[ctx.Process(target=worker,args=(path,cfg,n,gate)) for n in numbers]
                processes.extend(group)
                for process in group:process.start()
                for process in group:
                    process.join(timeout=45)
                    self.assertFalse(process.is_alive(),'worker stalled')
                    self.assertEqual(process.exitcode,0)
            try:
                run([1,2],barrier)
                with sqlite3.connect(path) as db:rows=db.execute('SELECT n,result,sends FROM results').fetchall()
                self.assertEqual(sum(r[2] for r in rows),1)
                self.assertEqual(sorted(r[1] for r in rows),['blocked','confirmed_performed'])
                winner=next(r[0] for r in rows if r[2]==1)
                # New interpreter + same durable store: replay, then new attempt.
                run([winner]);run([3])
                with sqlite3.connect(path) as db:
                    rows=db.execute('SELECT n,result,sends FROM results ORDER BY rowid').fetchall()
                    fuse=json.loads(db.execute('SELECT value FROM records WHERE key=?',(local.FUSE_KEY,)).fetchone()[0])
                self.assertEqual(sum(r[2] for r in rows),1)
                self.assertEqual(rows[-2],(winner,'confirmed_performed',0))
                self.assertEqual(rows[-1],(3,'blocked',0))
                self.assertEqual(fuse['attemptId'],str(winner)*43)
            finally:
                for process in processes:
                    if process.is_alive():process.terminate();process.join(timeout=5)

if __name__=='__main__':unittest.main(verbosity=2)
