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
