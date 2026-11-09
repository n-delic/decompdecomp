from decompnet.rom.symbols import Symbol, SymbolTable
from tests.test_claims import _game


def prepare(tmp_path, monkeypatch, header=''):
    net,pid=_game(tmp_path,[('old',4,'matched'),('caller',4,'matched'),('unrelated',4,'matched')])
    with net.store.tx() as db:
        for index,(name,source) in enumerate([('old','int old(void){return 1;}'),('caller','int caller(void){return old();}'),('unrelated','int unrelated(void){return 2;}')]):
            db.execute('UPDATE functions SET vram=?,matched_source=?,best_source=? WHERE name=?',(4096+index*16,source,source,name))
        db.execute("INSERT INTO context_versions(project_id,version,header,created_at,unit) VALUES(?,1,?,0,'')",(pid,header))
    monkeypatch.setattr(net,'symbols',lambda _:SymbolTable([Symbol('old',4096,16,'func')]))
    checked=[]
    def regression(proj,header,sources=None,only=None,**kwargs):
        checked.extend(r['name'] for r in only)
        return []
    monkeypatch.setattr(net,'regression_test',regression)
    return net,checked


def test_rename_checks_definition_and_callers_but_skips_unchanged(tmp_path,monkeypatch):
    net,checked=prepare(tmp_path,monkeypatch)
    agent,_=net.local_agent('you')
    assert net.rename_symbol(agent,'game','old','new').startswith('✅')
    assert checked == ['new','caller']
    assert net.store.one("SELECT matched_source FROM functions WHERE name='caller'")[0] == 'int caller(void){return new();}'


def test_shared_header_change_checks_every_effective_unit_body(tmp_path,monkeypatch):
    net,checked=prepare(tmp_path,monkeypatch,'int old(void);')
    agent,_=net.local_agent('you')
    net.rename_symbol(agent,'game','old','new')
    assert checked == ['new','caller','unrelated']


def test_new_spelling_capture_is_also_checked(tmp_path,monkeypatch):
    net,checked=prepare(tmp_path,monkeypatch)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source='int unrelated(void){return new();}' WHERE name='unrelated'")
    agent,_=net.local_agent('you')
    net.rename_symbol(agent,'game','old','new')
    assert 'unrelated' in checked


def test_failed_affected_match_keeps_original_name_and_sources(tmp_path,monkeypatch):
    net,_=prepare(tmp_path,monkeypatch)
    monkeypatch.setattr(net,'regression_test',lambda *args,**kwargs:[('caller','no longer matches')])
    agent,_=net.local_agent('you')
    assert net.rename_symbol(agent,'game','old','new').startswith('❌')
    assert net.store.one("SELECT name FROM functions WHERE vram=4096")[0] == 'old'
    assert 'old()' in net.store.one("SELECT matched_source FROM functions WHERE name='caller'")[0]
    assert not net._sym_override


def test_rename_does_not_decode_unaffected_function_payloads(tmp_path, monkeypatch):
    import sqlite3

    import pytest

    from decompnet import payload_storage
    net, checked = prepare(tmp_path, monkeypatch)
    unused = 'unrelated assembly and draft ' * 100
    with net.store.tx() as db:
        db.execute("UPDATE functions SET asm=?,seed=?,best_source=? WHERE name='unrelated'", (unused,unused,unused))
        db.execute("UPDATE text_payloads SET payload=X'00' WHERE hash=?", (payload_storage.digest(unused),))
    agent, _ = net.local_agent('you')
    assert net.rename_symbol(agent,'game','old','new').startswith('✅')
    assert checked == ['new','caller']
    # Unused damaged payloads were not read; selecting one explicitly still rejects corruption.
    with pytest.raises(sqlite3.OperationalError, match='user-defined function'):
        net.store.one("SELECT asm FROM functions WHERE name='unrelated'")


def test_rename_address_lookup_and_writes_use_owning_project_store(tmp_path, monkeypatch):
    from decompnet.store import Store
    net, _ = prepare(tmp_path, monkeypatch)
    agent, _ = net.local_agent('you')
    leaf = Store(tmp_path/'project.sqlite3', repair_archive=net.store.conn.repair_archive)
    net.store.conn.backup(leaf.conn)
    monkeypatch.setattr(net, 'project_store', lambda project: leaf)
    catalog_all = net.store.all
    def catalog_read(query, *args):
        assert 'SELECT id, name, size FROM functions' not in query, 'Address lookup belongs to the project'
        return catalog_all(query, *args)
    monkeypatch.setattr(net.store, 'all', catalog_read)
    try:
        assert net.rename_symbol(agent,'game','old','new').startswith('✅')
        assert leaf.one("SELECT name FROM functions WHERE vram=4096")[0] == 'new'
        assert net.store.one("SELECT name FROM functions WHERE vram=4096")[0] == 'old'
    finally:leaf.conn.close()


def test_inventory_reuses_source_tokens_but_invalidates_changed_and_deleted_sources(tmp_path,monkeypatch):
    from decompnet.app.rename_scope import inventory
    net,_=prepare(tmp_path,monkeypatch)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source=matched_source || ?",(' /*'+' large '*200+'*/',))
    proj=net.project_row('game');store=net.project_store(proj['id']);reads=[];original=store.one
    def read(sql,*args):
        if sql.startswith('SELECT matched_source FROM functions'):reads.append(args[0])
        return original(sql,*args)
    monkeypatch.setattr(store,'one',read)
    first=inventory(net,proj);assert len(reads)==3
    assert inventory(net,proj)==first and len(reads)==3
    caller=original("SELECT matched_source FROM functions WHERE name='caller'")[0]
    with store.tx() as db:db.execute("UPDATE functions SET matched_source=? WHERE name='caller'",(caller+" newToken",))
    changed=inventory(net,proj);assert len(reads)==4
    assert any('newToken' in words for _,words in changed)
    with store.tx() as db:db.execute("UPDATE functions SET status='open' WHERE name='caller'")
    assert len(inventory(net,proj))==2


def test_rename_batches_complete_affected_rows_without_dropping_judge_inputs(tmp_path, monkeypatch):
    net, _ = prepare(tmp_path, monkeypatch)
    store = net.store
    expected = [dict(r) for r in store.all("SELECT * FROM functions WHERE name IN ('old','caller') ORDER BY id")]
    expected[0]['name'] = 'new'
    seen = []
    def regression(proj, header, sources=None, only=None, **kwargs):
        assert only == expected
        seen.extend(r['name'] for r in only)
        return []
    monkeypatch.setattr(net, 'regression_test', regression)
    original = store.all
    def read(sql, *args):
        assert not sql.startswith('SELECT * FROM functions WHERE project_id=? AND id IN'), 'one native row per affected body'
        return original(sql, *args)
    monkeypatch.setattr(store, 'all', read)
    agent, _ = net.local_agent('you')
    assert net.rename_symbol(agent,'game','old','new').startswith('✅')
    assert seen == ['new','caller']


def test_builtin_rename_retries_and_persists_only_verified_callers(tmp_path,monkeypatch):
    import json

    from decompnet.project import compiler_for
    net,_=prepare(tmp_path,monkeypatch)
    with net.store.tx() as db:
        cfg={'kind':'rom','compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}}
        db.execute('UPDATE projects SET config=?',(json.dumps(cfg),))
    trials=[]
    def regression(proj,header,sources=None,only=None,**kwargs):
        trials.append([r['name'] for r in only])
        for row in only:
            flags=compiler_for(json.loads(proj['config']),row['vram'],row)['flags']
            if row['name']=='caller' and '-fno-builtin' not in flags:
                return [('caller','no longer matches')]
        return []
    monkeypatch.setattr(net,'regression_test',regression)
    assert net.rename_symbol(net.local_agent('you')[0],'game','old','bzero').startswith('✅')
    assert trials==[['bzero','caller'],['caller'],['caller']]
    cfg=json.loads(net.project_row('game')['config'])
    assert cfg['compiler']['flags']==['-O2']
    assert len(cfg['function_compilers'])==1
    caller=net._function('caller','game');other=net._function('unrelated','game')
    assert compiler_for(cfg,caller['vram'],caller)['flags']==['-O2','-fno-builtin']
    assert compiler_for(cfg,other['vram'],other)['flags']==['-O2']


def test_failed_builtin_fallback_rolls_back_flags_names_and_sources(tmp_path,monkeypatch):
    import json
    net,_=prepare(tmp_path,monkeypatch)
    cfg={'kind':'rom','compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}}
    with net.store.tx() as db:db.execute('UPDATE projects SET config=?',(json.dumps(cfg),))
    monkeypatch.setattr(net,'regression_test',lambda *args,**kwargs:[('caller','no longer matches')])
    result=net.rename_symbol(net.local_agent('you')[0],'game','old','memcpy')
    assert result.startswith('❌')
    assert json.loads(net.project_row('game')['config'])==cfg
    assert 'old()' in net._function('caller','game')['matched_source']
    assert net._function('old','game') and not net._sym_override



def test_targeted_builtin_guard_preserves_flag_order_and_existing_profile(tmp_path,monkeypatch):
    import json

    from decompnet.project import compiler_for
    net,_=prepare(tmp_path,monkeypatch)
    original=['-O2','-O1','-O2']
    cfg={'kind':'rom','compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O0']},
         'function_compilers':{'caller-own':{'function':'caller','vram':4112,'toolchain':'gcc-2.7.2-kmc','flags':original,'evidence':'retained'}}}
    with net.store.tx() as db:db.execute('UPDATE projects SET config=?',(json.dumps(cfg),))
    def regression(proj,header,sources=None,only=None,**kwargs):
        for row in only:
            flags=compiler_for(json.loads(proj['config']),row['vram'],row)['flags']
            if row['name']=='caller' and '-fno-builtin-bzero' not in flags:return [('caller','changed')]
        return []
    monkeypatch.setattr(net,'regression_test',regression)
    assert net.rename_symbol(net.local_agent('you')[0],'game','old','bzero').startswith('✅')
    cfg=net.cfg(net.project_row('game'))
    fn=net._function('caller','game')
    assert compiler_for(cfg,fn['vram'],fn)['flags']==original+['-fno-builtin-bzero']
