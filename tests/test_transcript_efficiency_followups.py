import json

import pytest

from decompnet import network, scoring
from decompnet.agents.api import api_tool_schemas
from decompnet.app import agent_tokens, compile_guidance, tools
from tests.test_claims import _game


def test_recovery_above_inherited_best_does_not_reset_budget(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    net._record(net._function('f','game'),a,100,'int f(){return 1;}',1)
    net.claim_row(a,'game','f')
    for score in [500,400,300,200]:
        net._record(net._function('f','game'),a,score,'int f(){return 2;}',1)
    st=net.stall_status(net._function('f','game')['id'],a.id)
    assert st['inherited_best']==100 and st['no_better']==4
    net._record(net._function('f','game'),a,90,'int f(){return 3;}',1)
    assert net.stall_status(net._function('f','game')['id'],a.id)['no_better']==0
    assert network._no_better([500,400,None,200],100)==4
    assert network._no_better([500,400,90,200,100],100)==2
    assert network._no_better([500,400])==0  # no inherited baseline: retain legacy behavior


@pytest.mark.parametrize('mode',['rom','fork'])
def test_explicit_recovery_changes_continuation_only(tmp_path,mode,monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a');fn=net._function('f','game')
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'kind':mode}),))
    net._record(fn,a,100,'int f(){return 1;}',1)
    net.claim_row(a,'game','f')
    net._record(net._function('f','game'),a,500,'int f(){return 2;}',1)
    net._record(net._function('f','game'),a,None,'int f(){broken}',1)
    from decompnet.app import agent_evidence
    def no_baseline(*args,**kwargs):raise AssertionError('Recovery must not trigger a diagnostic compile')
    monkeypatch.setattr(agent_evidence,'baseline',no_baseline)
    before=net.store.one('SELECT COUNT(*) n FROM attempts')['n']
    result=tools.call(net,a,'game','get_candidate',{'function':'f','base':'last_compiling'})
    assert 'return 2' in result and 'historical score is not reverified' in result
    result=tools.call(net,a,'game','get_candidate',{'function':'f','base':'saved_best'})
    assert 'return 1' in result
    assert agent_tokens.candidate(net,a,'game','f')[0]=='int f(){return 1;}'
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n']==before
    assert net._function('f','game')['best_score']==100
    with pytest.raises(network.NetworkError):agent_tokens.get_candidate(net,a,'game','f','unknown')


def test_feedback_does_not_encourage_a_regression():
    d=scoring.DiffResult(2353,10000,[],False)
    text=network.Network._what_changed(d,2548,883)
    assert 'keep it' not in text and '1470 points worse' in text and 'saved_best' in text


def test_explicit_move_on_requires_reason_and_preserves_notes(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    net.claim_row(a,'game','f')
    with pytest.raises(network.NetworkError,match='Explain'):net.release_function(a,'f','','game',move_on=True)
    assert net.store.one('SELECT 1 FROM claims WHERE agent_id=?',a.id)
    result=tools.call(net,a,'game','release_function',{'function':'f','notes':'No useful next hypothesis; hand off.','move_on':True})
    assert 'Released f' in result and not net.store.one('SELECT 1 FROM claims WHERE agent_id=?',a.id)


def test_compiler_guidance_points_to_local_declaration_and_recovery():
    source='int f(){\nint n;\nn=1;\nu8 *src;\nreturn n;\n}'
    text=compile_guidance.hints("COMPILE ERROR: parse error before `*'; '__repair_literal' undeclared",source)
    assert 'candidate line 4' in text and 'Candidate-local' in text and 'last_compiling' in text
    assert 'get_context' not in text


def test_default_prompts_and_both_agent_schemas_expose_new_policy():
    assert 'maximum, not a quota' in tools.WORK_PROMPT
    assert 'maximum, not a quota' in tools.FORK_PROMPT
    schemas={s['function']['name']:s['function']['parameters']['properties'] for s in api_tool_schemas()}
    assert 'base' in schemas['get_candidate'] and 'move_on' in schemas['release_function']


def test_imported_match_percent_feedback_also_distinguishes_saved_best(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    net._record(net._function('f','game'),a,100,'int f(){return 1;}',1)
    text=agent_tokens.feedback(net,a,'game','f','75.0% match (score 250; 0 = identical).',None)
    assert '150 points worse' in text and 'saved_best' in text


def test_generated_local_names_do_not_prompt_shared_header_fetches():
    for name in ('new_var', 'new_var6', 'temp_v0', 'var_a1'):
        text = compile_guidance.hints("COMPILE ERROR: `"+name+"' undeclared", 'int f(){return '+name+';}')
        assert 'Candidate-local identifier issue' in text
        assert 'Read get_context' not in text


def test_unattended_prompts_continue_after_difficult_functions():
    for prompt in (tools.WORK_PROMPT, tools.FORK_PROMPT):
        assert 'Continue with the next eligible function while session capacity remains' in prompt
        assert 'low effort setting alone is not a reason' in prompt
