"""Manual storage maintenance visits each owner, including separate transcripts."""
from pathlib import Path

from decompnet.app import database_storage, header_cleanup
from decompnet.app.storage_files import all_files
from decompnet.app.transcripts import service
from tests import test_project_storage as base
from tests.test_header_storage import Log

scoped = base.scoped


def test_header_pruning_and_source_retention_preserve_owner_references(scoped):
    net, stores = scoped
    for pid in (1,2):
        with stores[pid].tx() as db:
            for version in (2,3):
                db.execute('INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,?,?,?)',
                           (pid,version,f'header {pid}/{version}',''))
            for n in range(3):
                db.execute('INSERT INTO attempts(function_id,agent_id,source,score,compiled,created_at,context_version,score_improved) VALUES(?,?,?,?,?,?,1,0)',
                           (pid*10,1,f'candidate {pid}/{n}',100,1,1))
    assert header_cleanup.summary(net)[0]['unused_versions'] == 2
    result = header_cleanup.prune(net,Log())
    assert result['removed_versions'] == 2
    for pid in (1,2):
        assert {r['version'] for r in stores[pid].all('SELECT version FROM context_history')} == {1,3}
        assert not stores[pid].one('PRAGMA foreign_key_check')
    pruned = database_storage.prune(net,{'days':1},Log())
    assert pruned['removed_sources'] == 4
    for pid in (1,2):
        history = database_storage.history(net,'first' if pid==1 else 'second','same_name')['attempts']
        assert len(history) == 3 and history[0]['source_available']
        assert history[0]['agent'] == 'worker'
    assert not net.store.one('SELECT 1 FROM attempts')


def test_packing_stats_and_compaction_include_transcript_file(scoped):
    net, stores = scoped
    transcripts = service(net).for_project('first')
    with stores[1].tx() as db:
        db.execute("INSERT INTO helper_sessions(id,helper_id,project,kind,model,agent,started_at) VALUES(1,1,'first','codex','test','worker',1)")
    transcripts.append(1,'text','full conversation '*1000)
    assert transcripts.store in all_files(net)
    assert database_storage.stats(net)['logical_bytes'] >= len('full conversation '*1000)
    database_storage.pack(net,Log())
    assert transcripts.read('first',1)['events'][0]['text'] == 'full conversation '*1000
    for store in (stores[1],stores[2],transcripts.store):
        with store.tx() as db:
            db.execute('CREATE TABLE discarded(value BLOB)')
            db.execute('INSERT INTO discarded VALUES(zeroblob(2000000))')
        store.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        with store.tx() as db:db.execute('DELETE FROM discarded')
        store.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    result = header_cleanup.compact(net,Log())
    assert result['reclaimed_bytes'] > 5000000
    for store in all_files(net):
        assert Path(store.path).is_file()
        assert store.one('PRAGMA integrity_check')[0] == 'ok'
    assert transcripts.read('first',1)['events'][0]['text'] == 'full conversation '*1000


def test_export_symbols_and_focused_status_use_project_file(scoped):
    import threading
    import time
    from types import SimpleNamespace

    from decompnet.app import focused_agents
    from decompnet.network import Agent
    from decompnet.rom.export import _symbol_lines
    net, stores = scoped
    for pid in (1,2):
        with stores[pid].tx() as db:db.execute('UPDATE functions SET name=?,vram=?,size=4',(f'function_{pid}',pid*4096))
    net.symbols = lambda pid:SimpleNamespace(by_name={})
    assert _symbol_lines(net,net._project('first')) == ['function_1 = 0x00001000; // type:func size:0x4']
    assert _symbol_lines(net,net._project('second')) == ['function_2 = 0x00002000; // type:func size:0x4']
    until = time.time()+60
    agent = Agent(1,'worker',None)
    net._agent_focus = {1:{'id':20,'slug':'second','until':until,'stop':threading.Event()}}
    assert focused_agents.target(net,agent,'second') == 'function_2'
    session = SimpleNamespace(slug='second',options={'focus_id':20,'focus_until':until})
    manager = SimpleNamespace(net=net)
    assert focused_agents.completion(manager,session) is None
    with stores[2].tx() as db:db.execute("UPDATE functions SET status='matched'")
    assert 'matched' in focused_agents.completion(manager,session)
    assert stores[1].one('SELECT status FROM functions')['status'] == 'open'
