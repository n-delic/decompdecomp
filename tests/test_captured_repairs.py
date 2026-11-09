import pytest

from decompnet.app import agent_tokens, captured_repairs, repair_ranking, tools


def test_coordinated_arity_changes_call_and_prototype():
    source='void send(int,int,int,char); void f(Obj *p){send(0,0,p->x,p->y);}'
    out=captured_repairs.variants(source,'f')
    assert any('void send(int, int);' in text and 'send(0, 0)' in text for family,text in out if family=='call_arity')
    assert not any(f=='call_arity' for f,_ in captured_repairs.variants(source.replace('p->y','read()'),'f'))
    assert not any(f=='call_arity' for f,_ in captured_repairs.variants(source.replace('send(0,0,p->x,p->y);','send(0,0,p->x,p->y);send(0,0,p->x,p->y);'),'f'))


def test_struct_byte_offsets_are_coordinated_and_bounded():
    source='typedef struct {int a,b;} S; extern S *global; void f(){S *local; use(local+12);use(global+20);}'
    out=[s for f,s in captured_repairs.variants(source,'f') if f=='struct_byte_offset']
    assert len(out)==2
    assert '(unsigned char *)local + 12' in out[0] and '(unsigned char *)global + 20' in out[0]
    assert not captured_repairs.variants(source.replace('local+12','local+i').replace('global+20','global++'),'f')


def test_switch_call_distribution_rejects_side_effects_and_fallthrough():
    source='void f(int n){int x;switch(n){case 1:x=10;break;default:x=20;break;}result=send(x,4);finish();}'
    out=[s for f,s in captured_repairs.variants(source,'f') if f=='switch_call']
    assert len(out)==1 and 'send(10, 4)' in out[0] and 'send(20, 4)' in out[0]
    assert out[0].count('result=send')==2
    assert not any(f=='switch_call' for f,_ in captured_repairs.variants(source.replace('x=10','x=read()'),'f'))
    assert not any(f=='switch_call' for f,_ in captured_repairs.variants(source.replace('x=10;break;','x=10;'),'f'))
    assert not any(f=='switch_call' for f,_ in captured_repairs.variants(source.replace('finish();','finish(x);'),'f'))


def test_ineffective_families_are_demoted_not_removed():
    class Net:pass
    net=Net();base={'score':1,'rows':[('r','a','b')]}
    repair_ranking.remember(net,'scope','slow',base,base)
    repair_ranking.remember(net,'scope','slow',base,base)
    assert repair_ranking.demote(net,'scope',['slow','new'])==['new','slow']
    assert repair_ranking.demote(net,'other',['slow','new'])==['slow','new']
    repair_ranking.remember(net,'scope','slow',base,{'score':1,'rows':[('r','a','c')]})
    assert repair_ranking.demote(net,'scope',['slow','new'])==['slow','new']


def test_compiler_trial_schema_excludes_family_payloads():
    from pydantic import ValidationError
    assert tools.CompilerTrial(compiler='current').compiler=='current'
    with pytest.raises(ValidationError):tools.CompilerTrial(family='signedness')


def test_matched_alias_and_full_alias(tmp_path):
    from tests.test_claims import _game
    net,_=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('a')
    assert 'No verified' in agent_tokens.function_view(net,agent,'game','f','matched')


def test_redraft_prose_is_actionable_without_calling_backend():
    from decompnet.network import NetworkError
    with pytest.raises(NetworkError,match='C declarations'):
        tools._redraft(None,'game','f','Load the arguments after the prologue')
    with pytest.raises(NetworkError,match='C declarations'):
        tools._redraft(None,'game','f',None)


@pytest.mark.parametrize('imported',[False,True])
@pytest.mark.parametrize('family,source',[
    ('call_arity','void send(int,int,int,int);void f1(){send(0,0,x,y);}'),
    ('struct_byte_offset','typedef struct {int x,y;} S; void f1(){S *p;use(p+20);}'),
    ('switch_call','void f1(){int x;switch(n){case 1:x=2;break;default:x=3;break;}result=send(x,4);}'),
])
def test_new_families_use_normal_mode_judge(imported,family,source):
    from decompnet.app.permute import State
    from tests.test_draft_repairs import setup_repair_net
    from tests.test_permute import AGENT, ProjectStub, ScriptedPermuter
    net=setup_repair_net()
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?',(source,))
    if imported:
        net.fork_slug=lambda slug:'fork'
        class Fork(ProjectStub):
            def permuter_header(self,slug,function):return ''
        net.forks=Fork()
    expected=next(s for f,s in captured_repairs.variants(source,'f1') if f==family)
    perm=ScriptedPermuter(net,{expected:0});state=State()
    perm._repair_round(AGENT,net.project_row('game'),state)
    assert state.matched==1
