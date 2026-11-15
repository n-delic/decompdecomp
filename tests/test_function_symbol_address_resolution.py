"""Imported func symbols must not be mistaken for data during read-only address resolution."""
import pytest

from decompnet.app import address_lookup
from decompnet.network import NetworkError
from tests.test_claims import _game


def setup(tmp_path,kind):
    net,pid=_game(tmp_path,[('canonical',2,'matched')]);agent,_=net.local_agent('a')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET vram=2147487744,rom_offset=64,size=8,unit='code'")
        db.execute('INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)',
                   (pid,'RecoveredFunction',0x80001000,8,kind))
    return net,pid,agent


@pytest.mark.parametrize('kind',['func','function'])
def test_both_function_kinds_resolve_entry_interior_and_alias_without_mutation(tmp_path,kind):
    net,_,agent=setup(tmp_path,kind)
    rows=address_lookup.resolve(net,agent,'game',['D_80001000','0x80001004','RecoveredFunction'])['results']
    assert [r['kind'] for r in rows]==['function_entry','function_interior','function_entry']
    assert rows[1]['candidates'][0]['offset']==4
    for query in ('D_80001000','0x80001004','RecoveredFunction'):
        assert address_lookup.read_function(net,agent,'game',query)=='canonical'
    assert net.store.one('SELECT COUNT(*) n FROM functions')['n']==1
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n']==0
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n']==0


@pytest.mark.parametrize('kind',['data','unknown','bss','jtbl'])
def test_real_data_and_unknown_symbols_remain_data_even_over_a_function(tmp_path,kind):
    net,_,agent=setup(tmp_path,kind)
    assert address_lookup.resolve(net,agent,'game',['D_80001000'])['results'][0]['kind']=='data'
    with pytest.raises(NetworkError,match='Cannot resolve'):
        address_lookup.read_function(net,agent,'game','RecoveredFunction')


def test_function_symbol_does_not_choose_an_overlay_or_invent_an_owner(tmp_path):
    net,pid,agent=setup(tmp_path,'func')
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name,vram,rom_offset,size,status,unit) VALUES(?,?,?,?,?,?,?)",
                   (pid,'second',0x80001000,4096,8,'matched','overlay'))
        db.execute('INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)',
                   (pid,'OrphanFunction',0x80002000,8,'func'))
    rows=address_lookup.resolve(net,agent,'game',['D_80001000','OrphanFunction'])['results']
    assert rows[0]['kind']=='ambiguous' and rows[0]['candidate_count']==2
    assert rows[1]['kind']=='unknown' and rows[1]['candidate_count']==0
    with pytest.raises(NetworkError,match='ambiguous'):
        address_lookup.read_function(net,agent,'game','D_80001000')
    scoped=address_lookup.resolve(net,agent,'game',['D_80001000'],context_function='second')['results'][0]
    assert scoped['kind']=='function_entry' and scoped['candidates'][0]['function']=='second'
    assert net.store.one('SELECT COUNT(*) n FROM functions')['n']==2


def test_function_kind_does_not_override_conflicting_data_symbol(tmp_path):
    net,pid,agent=setup(tmp_path,'func')
    with net.store.tx() as db:
        db.execute('INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)',
                   (pid,'SharedData',0x80001000,4,'data'))
    rows=address_lookup.resolve(net,agent,'game',['D_80001000','SharedData'])['results']
    assert all(r['kind']=='data' for r in rows)
    with pytest.raises(NetworkError,match='Cannot resolve'):
        address_lookup.read_function(net,agent,'game','D_80001000')
