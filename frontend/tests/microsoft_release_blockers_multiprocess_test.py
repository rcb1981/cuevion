"""Independent workers: expired acceptance and moved Compose barriers, offline."""
from microsoft_send_multiprocess_test import DiskCAS, network_attempts
import json
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch


def worker(path, mode, number, identifier, barrier=None):
    from api.inboxes.test_microsoft_release_blockers import (
        AcceptanceFixture, acceptance, ledger, owned, executor, OP,
        MovedInboxGraph, inbox_action,
    )
    from api.inboxes.microsoft_graph import MicrosoftGraphError
    f=AcceptanceFixture();f.setUp();disk=DiskCAS(path)
    clock=[time.time()];inside=[False];delayed=[False]
    real_read=ledger.read;real_checkpoint=acceptance.Permit.checkpoint
    def checkpoint(permit,**kw):
        inside[0]=True
        try:return real_checkpoint(permit,**kw)
        finally:inside[0]=False
    def expire():
        control=disk.read({},f.control_key())[0]
        clock[0]=control['expiresAt'];delayed[0]=True
    def started(root):
        item=ledger.entry(f.active,OP,root)
        return item is not None and item['item']['phase']=='started'
    def read(authority):
        result=real_read(authority)
        if mode=='ledger-expiry' and inside[0] and not delayed[0] and started(result[0]):expire()
        return result
    def check_lease(authority,token,deadline):
        with sqlite3.connect(path) as db:row=db.execute('SELECT token FROM lease').fetchone()
        if row!=(token,) or time.monotonic()+45>=deadline:ledger.fail('store_unavailable')
        if mode=='lease-expiry' and started(real_read(authority)[0]):expire()
    graph=MovedInboxGraph(identifier,unread=mode=='unread')
    try:
        with patch.object(ledger.base.store,'_read_durable_record_snapshot',side_effect=disk.read),patch.object(
            ledger.base.store,'_write_google_durable_record_if_unchanged',side_effect=disk.write),patch.object(
            owned,'acquire_mailbox_mutation_lease',side_effect=disk.acquire),patch.object(
            owned,'release_mailbox_mutation_lease',side_effect=disk.release),patch.object(
            executor,'_assert_lease',side_effect=check_lease),patch.object(acceptance.Permit,'checkpoint',checkpoint),patch.object(
            ledger,'read',read),patch.object(time,'time',side_effect=lambda:clock[0]):
            if barrier:barrier.wait(timeout=15)
            try:
                result=f.save_trial()['outcome'] if mode.endswith('expiry') else inbox_action(mode,graph)
            except (MicrosoftGraphError,owned.MicrosoftMailboxAccessError):result='blocked'
            with sqlite3.connect(path) as db:
                db.execute('INSERT INTO results VALUES (?,?,?,?)',(number,json.dumps(result),len(f.graph.writes)+len(graph.writes),int(delayed[0])))
    finally:
        f.doCleanups()
        if network_attempts:raise AssertionError(network_attempts)


class ReleaseBlockerProcessTests(unittest.TestCase):
    def pair_and_restart(self,mode):
        from api.inboxes.test_microsoft_release_blockers import AcceptanceFixture, compose, ledger, HEADERS, AUTH, EDIT, OP
        f=AcceptanceFixture();f.setUp()
        try:
            identifier='normal-message'
            if not mode.endswith('expiry'):
                identifier=f.save_trial()['providerMessageId']
                current=compose.load_owned(HEADERS,AUTH.mailbox_id,identifier,opener=f.graph)
                f.graph.lose=True
                self.assertTrue(f.save_trial('p'*43,{**EDIT,'body':'lost ACK'},identifier,current['etag'])['blocked'])
                f.stop()
            records=dict(f.cas.records);authority=f.active
        finally:f.doCleanups()
        with tempfile.TemporaryDirectory(prefix='ms-release-blockers-') as temp:
            path=str(Path(temp)/'isolated.sqlite')
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE records(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                db.execute('CREATE TABLE lease(token TEXT NOT NULL)')
                db.execute('CREATE TABLE results(n INTEGER,result TEXT,writes INTEGER,delayed INTEGER)')
                db.executemany('INSERT INTO records VALUES (?,?)',records.items())
            ctx=multiprocessing.get_context('spawn');gate=ctx.Barrier(2);processes=[]
            def run(numbers,barrier=None):
                group=[ctx.Process(target=worker,args=(path,mode,n,identifier,barrier)) for n in numbers]
                processes.extend(group)
                for p in group:p.start()
                for p in group:
                    p.join(timeout=30);self.assertFalse(p.is_alive());self.assertEqual(p.exitcode,0)
            try:
                run([1,2],gate);run([3])  # New process, same durable history.
                with sqlite3.connect(path) as db:
                    rows=db.execute('SELECT result,writes,delayed FROM results').fetchall()
                    stored=dict(db.execute('SELECT key,value FROM records').fetchall())
                self.assertEqual(len(rows),3);self.assertEqual(sum(r[1] for r in rows),0)
                self.assertTrue(all(json.loads(r[0]) in ('blocked','uncertain') for r in rows))
                if mode.endswith('expiry'):self.assertGreater(sum(r[2] for r in rows),0)
                # Check the durable claim after every worker has stopped.
                f=AcceptanceFixture();f.setUp()
                try:
                    f.cas.records=stored
                    self.assertTrue(ledger.operation(authority,OP if mode.endswith('expiry') else 'p'*43)['blocked'])
                finally:f.doCleanups()
            finally:
                for p in processes:
                    if p.is_alive():p.terminate();p.join(timeout=5)
    def test_two_servers_ledger_expiry_and_restart(self):self.pair_and_restart('ledger-expiry')
    def test_two_servers_lease_expiry_and_restart(self):self.pair_and_restart('lease-expiry')
    def test_two_servers_moved_compose_read_and_restart(self):self.pair_and_restart('read')
    def test_two_servers_moved_compose_unread_and_restart(self):self.pair_and_restart('unread')
    def test_two_servers_moved_compose_archive_and_restart(self):self.pair_and_restart('archive')
    def test_two_servers_moved_compose_delete_and_restart(self):self.pair_and_restart('delete')

if __name__=='__main__':unittest.main(verbosity=2)
