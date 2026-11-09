"""Cross-project naming must not inflate every target's C and disassembly."""
import json
from types import SimpleNamespace

import pytest

from decompnet import payload_storage
from decompnet.app.twins import Print, Twins
from decompnet.store import Store


@pytest.mark.parametrize('fork', [False, True])
@pytest.mark.parametrize('accepted', [False, True])
def test_name_reuse_reads_metadata_and_preserves_rename_checks(tmp_path, monkeypatch, fork, accepted):
    store = Store(tmp_path/'db')
    large = '/* synthetic saved code and disassembly */\n'*100
    with store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,root,config) VALUES(1,'target','','{}'),(2,'donor','','{}')")
        # Only the first generated name is eligible. Claimed functions, established names,
        # explicit renames, duplicate target fingerprints and occupied names are protected.
        names = ['func_1000','func_2000','established','func_4000','func_5000','func_6000',
                 'func_7000','occupied']
        for fid,name in enumerate(names, 1):
            meta = json.dumps({'naming':{'original_name':'old'}}) if fid == 4 else '{}'
            db.execute('INSERT INTO functions(id,project_id,name,meta,asm,seed,best_source,matched_source) '
                       'VALUES(?,1,?,?,?,?,?,?)', (fid,name,meta,large,large,large,large))
        db.execute('INSERT INTO claims(function_id,agent_id) VALUES(2,1)')
        for fid,name in enumerate(['inferred','claimed','other_name','renamed','ambiguous','occupied'], 11):
            db.execute("INSERT INTO functions(id,project_id,name,status,meta) VALUES(?,2,?,'matched','{}')",
                       (fid,name))
    projects = {r['slug']:r for r in store.all('SELECT * FROM projects')}
    mine = {fid:Print(key, [], 64) for fid,key in enumerate(['a','b','c','d','e','e','f','g'],1)}
    donor = {fid:Print(key, [], 64) for fid,key in enumerate(['a','b','c','d','e','f'],11)}
    calls = []
    def rename(*args):
        calls.append(args)
        return '✅ Renamed' if accepted else 'Existing match would fail'
    net = SimpleNamespace(store=store, links={}, cfg=lambda _: {'platform':'n64'},
                          project_row=projects.__getitem__, fork_slug=lambda _: fork,
                          local_agent=lambda _: (1,None), rename_symbols=rename, rename_symbol=rename)
    net.project_store = lambda _: net.store
    twins = Twins(net)
    monkeypatch.setattr(twins, 'prints', lambda p: mine if p['id']==1 else donor)
    reads = []
    def read(digest, blob):
        reads.append(digest)
        return payload_storage.unpack(digest, blob)
    for conn in (store.conn,store._reader()):
        conn.create_function('payload_read',2,read,deterministic=True)
    assert twins.reuse_names('target') == int(accepted)
    assert reads == []
    assert len(calls) == 1
    if fork:
        assert calls[0][2:4] == ('func_1000','inferred')
    else:
        assert calls[0][2] == [('func_1000','inferred')]
    naming = json.loads(store.one('SELECT meta FROM functions WHERE id=1')['meta']).get('naming')
    if accepted:
        assert naming['method'] == 'exact_cross_game'
        assert naming['original_name'] == 'func_1000'
        assert naming['source_project'] == 'donor'
        assert naming['source_function_id'] == 11
    else:
        assert naming is None
    store.conn.close()
