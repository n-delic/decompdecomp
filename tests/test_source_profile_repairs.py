"""Repairs stay bounded and source-scoped; profile matches are still made by the normal judge."""
import pytest

from decompnet.app import deep_permuter, draft_repairs, operand_order_repairs, source_diagnostics
from decompnet.network import NetworkError
from tests.test_deep_permuter import Log, setup
from tests.test_permute import AGENT


def test_comparison_order_changes_only_the_requested_body_and_reaches_draft_pipeline():
    source = 'int other(void){return a != b;}\nint f(void) {\n if(p->id[i] != temp[i]) return 2;\n return 0;\n}\n'
    got = operand_order_repairs.variants(source, 'f')
    assert len(got) == 1 and got[0][0] == 'comparison_operand_order'
    assert 'if(temp[i] != p->id[i])' in got[0][1]
    assert got[0][1].startswith('int other(void){return a != b;}')
    labels = {}
    assert got[0][1] in draft_repairs.variants(source,'f','mips',labels=labels,limit=None)
    assert labels[got[0][1]] == 'comparison_operand_order'


@pytest.mark.parametrize('expression', ['get() != b', 'a++ != b', 'a != b++', 'a != b()', 'a + c != b'])
def test_no_partial_side_effecting_comparison_rewrites(expression):
    assert not operand_order_repairs.variants('int f(void){return '+expression+';}', 'f')


def test_volatile_macros_and_floating_products_are_not_reordered():
    assert not operand_order_repairs.variants('int f(void){return a != b;}', 'f', 'extern volatile int a;')
    assert not operand_order_repairs.variants('#define a read()\nint f(void){return a != b;}', 'f')
    assert not operand_order_repairs.variants('float f(float a,float b,float c){return a*b*c;}', 'f')
    got = operand_order_repairs.variants('u32 f(u32 a,u32 b,u32 c){return a*b*c;}', 'f')
    assert len(got) == 2 and all(family == 'integer_product_order' for family,_ in got)


def test_advisory_type_and_retail_diagnostics_do_not_modify_source():
    s='int f(void*p){if(M2C_FIELD(p,int*,4))return a*b*c;return 0;}'
    hints=source_diagnostics.hints(s,'f')
    assert len(hints)==3
    assert any('argument types' in h for h in hints)
    assert any('retail' in h for h in hints)
    assert source_diagnostics.hints('int g(void){return 1;}','f')==[]


def test_improved_source_profile_check_releases_claim_and_reacquires(monkeypatch):
    net=setup(monkeypatch);net._comparison_digest=lambda *a:'digest'
    search=deep_permuter.Search(net,1,10,Log(),net.function())
    fn=net.function();proj=net.project_row('game')
    assert search._hold(AGENT,fn,count_turn=False)
    def preflight(*args):
        assert not net.store.one('SELECT 1 FROM claims WHERE function_id=?',fn['id'])
        with net.store.tx() as db:db.execute('UPDATE functions SET best_score=20')
        return {'tested':2}
    monkeypatch.setattr(search,'_compiler_preflight',preflight)
    assert search._repair_preflight(AGENT,proj,fn,40,None)==20
    assert net.store.one('SELECT agent_id FROM claims WHERE function_id=?',fn['id'])['agent_id']==AGENT.id
    search._let_go(AGENT,fn)


def test_profile_repair_does_not_steal_a_concurrent_claim(monkeypatch):
    net=setup(monkeypatch);net._comparison_digest=lambda *a:'digest'
    search=deep_permuter.Search(net,1,10,Log(),net.function());fn=net.function()
    def preflight(*args):
        with net.store.tx() as db:db.execute('INSERT INTO claims VALUES(?,?,?,?)',(fn['id'],AGENT.id,0,9999999999))
    monkeypatch.setattr(search,'_compiler_preflight',preflight)
    with pytest.raises(NetworkError,match='Another worker'):
        search._repair_preflight(AGENT,net.project_row('game'),fn,40,None)
    assert net.store.one('SELECT 1 FROM claims WHERE function_id=?',fn['id'])


def test_newly_matched_profile_is_not_reclaimed(monkeypatch):
    net=setup(monkeypatch);net._comparison_digest=lambda *a:'digest'
    search=deep_permuter.Search(net,1,10,Log(),net.function());fn=net.function()
    def preflight(*args):
        with net.store.tx() as db:db.execute("UPDATE functions SET status='matched',best_score=0")
    monkeypatch.setattr(search,'_compiler_preflight',preflight)
    assert search._repair_preflight(AGENT,net.project_row('game'),fn,40,None)==0
    assert not net.store.one('SELECT 1 FROM claims')


def test_local_winner_checks_flags_before_generating_another_round(monkeypatch):
    net=setup(monkeypatch);search=deep_permuter.Search(net,1,20,Log(),net.function())
    rounds=[];profiles=[]
    def candidates(source,*args,**kwargs):
        rounds.append(source)
        return ['improved']
    monkeypatch.setattr(draft_repairs,'variants',candidates)
    def score(agent,proj,fn,source,best,fork):
        with net.store.tx() as db:
            db.execute('UPDATE functions SET best_source=?,best_score=20',(source,))
        return 20
    monkeypatch.setattr(search,'_score',score)
    def profile(agent,proj,fn,best,fork):
        profiles.append(fn['best_source'])
        if fn['best_source']=='improved':
            with net.store.tx() as db:
                db.execute("UPDATE functions SET status='matched',best_score=0")
            return 0
        return best
    monkeypatch.setattr(search,'_repair_preflight',profile)
    best,_=search.local_repairs(AGENT,net.project_row('game'),net.function(),40,None)
    assert best==0 and profiles[-1]=='improved' and len(rounds)==1


def test_deep_search_tries_only_the_selected_template_before_flags(monkeypatch):
    from decompnet.app import source_preparation
    net=setup(monkeypatch);net._comparison_digest=lambda *a:'digest';order=[]
    def prepare(search,proj,state,log,**kwargs):
        assert kwargs['only']=={'f'} and kwargs['budget']==1
        order.append('typed source')
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status='matched',best_score=0")
        return {'tested':1,'improved':1,'matched':1,'errors':[]}
    def profiles(self,proj,fn,state,deadline):
        order.append('flags')
        assert fn['status']=='matched'
        return {'tested':0}
    monkeypatch.setattr(source_preparation,'run',prepare)
    monkeypatch.setattr(deep_permuter.Search,'_compiler_preflight',profiles)
    result=deep_permuter.run(net,'game','f',1,10,Log())
    assert order==['typed source','flags'] and result['matched']
    assert result['tested']==1 and result['improvements']==1
