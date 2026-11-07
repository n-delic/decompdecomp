"""Remaining queue recovery, byte-peer lookups and reuse notifications select owners."""
from types import SimpleNamespace

from decompnet.app import project_functions
from decompnet.app.source_queue import Queue
from decompnet.app.support import Jobs
from tests import test_project_storage as base

scoped = base.scoped


def setup(net):
    net.project_row = net._project
    net.console = lambda proj:SimpleNamespace(cpu_variant='')
    net.links = {}


def test_byte_peers_use_metadata_shortlists_across_independent_files(scoped):
    net, stores = scoped
    setup(net)
    exact = project_functions.listing(net,'first','same_name')
    assert exact['exact'] and exact['rows'][0]['slug'] == 'second'
    with stores[2].tx() as db:db.execute("UPDATE functions SET target=?",(bytes.fromhex('01000224'),))
    similar = project_functions.listing(net,'first','same_name')
    assert not similar['exact']
    assert not net.store.one('SELECT 1 FROM functions')


def test_source_queue_restart_cleans_only_its_own_project_claim(scoped):
    net, stores = scoped
    setup(net)
    with net.store.tx() as db:
        db.execute("INSERT INTO agents(id,name,token_hash) VALUES(2,'source-queue-1','source-token')")
    for pid,actor in ((1,2),(2,1)):
        with stores[pid].tx() as db:db.execute('INSERT INTO claims VALUES(?,?,1,9999999999)',(pid*10,actor))
    queue = Queue(net,Jobs())
    queue._save(1,{'state':'paused','agent_id':2,'items':[{'function':'same_name','state':'running'}]})
    queue._save(2,{'state':'paused','agent_id':1,'items':[{'function':'same_name','state':'running'}]})
    queue.resume()
    assert not stores[1].one('SELECT 1 FROM claims')
    assert stores[2].one('SELECT agent_id FROM claims')['agent_id'] == 1
    assert queue.status('first')['items'][0]['state'] == 'pending'
    assert not net.store.one("SELECT 1 FROM settings WHERE key LIKE 'source-recovery-queue:%'")


def test_reuse_cascade_checks_donor_and_destination_owners(scoped, monkeypatch):
    from decompnet.app import reuse_cascade
    net, stores = scoped
    setup(net)
    with stores[1].tx() as db:db.execute("UPDATE functions SET status='matched',best_source='int donor(void){return 0;}',name='donor',size=4")
    with stores[2].tx() as db:db.execute('UPDATE functions SET size=4')
    monkeypatch.setattr('decompnet.app.shared_reuse.compatible',lambda *args, **kw:[net._project('second')])
    launched = []
    monkeypatch.setattr(reuse_cascade.workers,'pool',lambda:SimpleNamespace(submit=launched.append))
    cascade = reuse_cascade.Cascade(net)
    cascade.enabled = True
    cascade.notify(net._project('first'),10)
    assert cascade.pending == {'second'} and len(launched) == 1
