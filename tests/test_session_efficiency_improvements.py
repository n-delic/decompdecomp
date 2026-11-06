import hashlib
import json

import pytest

from decompnet import scoring
from decompnet.agents.claude import CLAUDE
from decompnet.app import agent_evidence, agent_tokens
from decompnet.app.compile_guidance import hints
from decompnet.network import NetworkError
from tests.test_claims import _game


def setup_candidate(tmp_path):
    net, _ = _game(tmp_path, [('f', 30, 'open')])
    agent, _ = net.local_agent('worker')
    source = 'int f(){\nreturn 1;\n}'
    with net.store.tx() as db:
        db.execute('UPDATE functions SET seed=?', (source,))
    return net,agent,net._function('f','game'),source


def evidence(net,agent,fn,source):
    key = agent_evidence.scope(net,agent,fn)+(hashlib.sha256(source.encode()).hexdigest(),)
    net._baseline_evidence = {key:{'source':source,'verified':True,'rows':[('i','add a,b,c','sub a,b,c')],'legend':scoring.LEGEND}}


def test_claim_uses_focused_baseline_and_preserves_candidate_and_instructions(tmp_path,monkeypatch):
    net,agent,fn,source = setup_candidate(tmp_path)
    evidence(net,agent,fn,source)
    monkeypatch.setattr(agent_tokens,'candidate_line_note',lambda *a:'\nC line 2: return 1;')
    full = '## CLAIMED: game/f\n### Target assembly\n```asm\n'+'\n'.join('lw $t0,4($sp) # full target instruction listing' for _ in range(100))+'\n```\n### Candidate\n```c\n'+source+'\n```\nUser instruction: preserve semantics.'
    compact = agent_tokens.brief(net,full,'game',agent)
    assert 'Focused target/compiled diff' in compact
    assert source in compact and 'preserve semantics' in compact
    assert len(compact) < len(full)//2
    assert 'nop\nnop\nnop' not in compact


def test_last_diff_reuses_only_exact_verified_baseline_without_compiling(tmp_path,monkeypatch):
    net,agent,fn,source = setup_candidate(tmp_path)
    evidence(net,agent,fn,source)
    monkeypatch.setattr(net,'_compile',lambda *a:pytest.fail('read must not compile'))
    assert 'sub a,b,c' in agent_tokens.diff_page(net,agent,'game','f')
    net._last_diffs.clear()
    next(iter(net._baseline_evidence.values()))['source'] = 'wrong source'
    with pytest.raises(NetworkError,match='No verified diff'):
        agent_tokens.diff_page(net,agent,'game','f')


def test_stale_patch_supplies_exact_current_lines_and_exhaustion_stops_suggestions(tmp_path,monkeypatch):
    net,agent,fn,source = setup_candidate(tmp_path)
    error = agent_tokens.stale(net,agent,fn,'missing',source,'current')
    assert '1: int f(){' in str(error) and 'not part of the patch anchor' in str(error)
    assert 'claim ended' in agent_tokens.next_action(net,agent,fn,source,'rev')
    net.claim_row(agent,'game','f')
    monkeypatch.setattr(net,'stall_status',lambda *a:{'compiles':8,'no_better':8,'after':8})
    assert 'budget exhausted' in agent_tokens.next_action(net,agent,fn,source,'rev')


def test_compiler_guidance_is_advisory_and_does_not_guess_types():
    text = "COMPILE ERROR: parse error before `int'; `thing' undeclared; conflicting types for x"
    result = hints(text,'int f(){for(int k=0;k<5;k++){} return thing;}')
    assert 'C89' in result and 'get_context(function=...)' in result
    assert 'invent' in result and 'shared header' in result
    assert hints('score 10','int f(){}') == ''


def test_claude_incremental_snapshots_and_final_totals_are_not_double_counted():
    state = {}
    def usage(event):
        return [json.loads(text) for kind,text in CLAUDE.parse_line(json.dumps(event),state) if kind=='usage']
    message = {'type':'assistant','message':{'id':'m1','content':[], 'usage':{'input_tokens':100,'cache_read_input_tokens':200,'output_tokens':5}}}
    first = usage(message)
    assert first[0]['coverage'] == 'partial'
    duplicate = usage(message)
    final = usage({'type':'result','num_turns':1,'usage':{'input_tokens':100,'cache_read_input_tokens':200,'output_tokens':20}})
    summed = lambda field:sum(item.get(field,0) for item in first+duplicate+final)
    assert summed('input') == 100 and summed('cache_read') == 200 and summed('output') == 20
    assert final[0]['coverage'] == 'complete'


def test_claude_partial_stream_usage_survives_without_result_and_is_deduplicated():
    state = {}
    events = [
        {'type':'stream_event','event':{'type':'message_start','message':{'id':'m1','usage':{'input_tokens':100,'output_tokens':0}}}},
        {'type':'stream_event','event':{'type':'message_delta','usage':{'output_tokens':9}}},
        {'type':'assistant','message':{'id':'m1','content':[], 'usage':{'input_tokens':100,'output_tokens':9}}},
    ]
    parts = [json.loads(text) for event in events for kind,text in CLAUDE.parse_line(json.dumps(event),state) if kind=='usage']
    assert sum(p.get('input',0) for p in parts)==100
    assert sum(p.get('output',0) for p in parts)==9
    assert all(p['coverage']=='partial' for p in parts)


def test_partial_usage_survives_session_completion_and_is_excluded_from_token_averages(tmp_path):
    import time

    from decompnet.app.agents import Manager
    from decompnet.app.session_efficiency import summarize
    net,agent,fn,source = setup_candidate(tmp_path)
    manager = Manager(net,tmp_path,'http://127.0.0.1/mcp')
    session = manager._record('game','claude','worker','Worker',{})
    sid = manager._session_begin(session)
    manager.account(session,{'input':100,'output':20,'coverage':'partial'})
    session.usage['tool_calls'] = 1
    manager._session_end(session,sid,agent,time.time()-1,'rotated')
    row = dict(net.store.one('SELECT * FROM helper_sessions WHERE id=?',sid))
    assert row['usage_coverage']=='partial' and row['tokens']==120
    row['matches']=1
    summary = summarize([row],{sid:{'cohort':'test'}})[0]
    assert summary['missing_usage_sessions']==1 and summary['tokens_per_match'] is None
    assert session.snapshot()['current_usage_coverage']=='partial'


def test_stale_preview_tracks_requested_anchor_far_from_first_change(tmp_path):
    net,agent,fn,source = setup_candidate(tmp_path)
    old = '\n'.join('int variable'+str(i)+';' for i in range(60))
    current = old.replace('variable0','changed0').replace('variable50','changed50')
    net._agent_snapshots={(agent.id,fn['id']):{'old':old}}
    message = str(agent_tokens.stale(net,agent,fn,'old',current,'new','int variable50;'))
    assert '51: int changed50;' in message


def test_released_claim_notice_survives_compact_feedback(tmp_path,monkeypatch):
    from decompnet.app import tools
    net,agent,fn,source = setup_candidate(tmp_path)
    net.claim_row(agent,'game','f')
    rows = [('i','add a,b,c','sub a,b,c')]
    net.keep_diff(agent.id,fn['id'],rows,scoring.LEGEND)
    monkeypatch.setattr(net,'compile_and_diff',lambda *a:'score 10\nmarkers: i=operand\nlisting')
    def release(*args):
        with net.store.tx() as db:
            db.execute('DELETE FROM claims WHERE function_id=?',(fn['id'],))
        return 'Unchanged-output limit reached. Saved work and released claim.'
    monkeypatch.setattr(agent_evidence,'track',release)
    text = tools._compile(net,agent,'game','f',source)
    assert 'Unchanged-output limit reached' in text and 'Do not release this function again' in text
    assert 'Budget:' not in text


def test_imported_project_build_errors_get_context_guidance():
    result = hints("BUILD ERROR: use of undeclared identifier 'widget'",'int f(){return widget;}')
    assert 'widget' in result and 'get_context(function=...)' in result


def test_claude_inconsistent_final_usage_stays_partial():
    state = {}
    CLAUDE.parse_line(json.dumps({'type':'assistant','message':{'id':'m1','content':[], 'usage':{'input_tokens':100}}}),state)
    result = CLAUDE.parse_line(json.dumps({'type':'result','usage':{'input_tokens':50}}),state)
    usage = [json.loads(text) for kind,text in result if kind=='usage']
    assert usage[0]['coverage']=='partial' and usage[0]['input']==0
