import json

import anyio
import pytest

from decompnet import scoring
from decompnet.agents.api import FINISHING, api_tool_schemas
from decompnet.app import agent_tokens, tools
from decompnet.network import NetworkError
from tests.test_claims import _game


@pytest.mark.parametrize('mode', ['rom', 'fork'])
def test_patch_revision_uniqueness_and_normal_judge(tmp_path, monkeypatch, mode):
    net, pid = _game(tmp_path, [('f', 10, 'open')])
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?', (json.dumps({'kind': mode}),))
        db.execute('UPDATE functions SET best_source=?', ('int f(){return 1;}',))
    a, _ = net.local_agent('a')
    net.claim_row(a, 'game', 'f')
    seen = []
    def compile(agent, function, source, slug):
        seen.append(source)
        net._record(net._function('f', 'game'), agent, 10, source, 1)
        return 'score 10'
    monkeypatch.setattr(net, 'compile_and_diff', compile)
    source, revision = agent_tokens.candidate(net, a, 'game', 'f')
    text = tools.call(net, a, 'game', 'compile_patch', {'function': 'f', 'revision': revision, 'old': 'return 1', 'new': 'return 2'})
    assert seen == ['int f(){return 2;}'] and 'Candidate revision' in text
    assert agent_tokens.candidate(net, a, 'game', 'f')[0] == seen[0]
    with pytest.raises(NetworkError, match='Candidate changed'):
        agent_tokens.patch(net, a, 'game', 'f', revision, 'return 1', 'return 3')
    _, revision = agent_tokens.candidate(net, a, 'game', 'f')
    with pytest.raises(NetworkError, match='exactly once'):
        agent_tokens.patch(net, a, 'game', 'f', revision, '', 'x')
    assert len(seen) == 1
    assert 'score 10' in agent_tokens.history(net, net._function('f', 'game'))


def test_new_claim_starts_from_best_not_old_agent_failure(tmp_path):
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    a, _ = net.local_agent('a')
    fn = net._function('f', 'game')
    net._record(fn, a, 90, 'old failure', 1)
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?', ('new best',))
    net.claim_row(a, 'game', 'f')
    assert agent_tokens.candidate(net, a, 'game', 'f')[0] == 'new best'


def test_incremental_diff_does_not_confuse_equal_scores_with_identical_rows(tmp_path):
    net, _ = _game(tmp_path, [('f', 200, 'open')])
    a, _ = net.local_agent('a');fn=net._function('f','game')
    rows=[('i', f'load {i}', f'load {i+1}') for i in range(200)]
    net.keep_diff(a.id, fn['id'], rows)
    full='score 200\n'+scoring.render_rows(rows)+'\nwhen linking your code at the function\'s address:\nmissing datum'
    out=agent_tokens.feedback(net,a,'game','f',full,None)
    assert len(out)<len(full) and 'missing datum' in out
    assert 'Instruction diff unchanged' in agent_tokens.feedback(net,a,'game','f',full,rows)
    before=list(rows);before[0]=('i','different','different too')
    assert 'Instruction diff unchanged' not in agent_tokens.feedback(net,a,'game','f',full,before)
    page=agent_tokens.diff_page(net,a,'game','f',start=190,limit=10)
    assert 'load 199' in page and 'load 20 ' not in page
    assert net.store.one('SELECT COUNT(*) AS n FROM attempts')['n']==0


def test_repeat_restores_its_diff_and_compact_reply(tmp_path):
    net, _ = _game(tmp_path, [('f',10,'open')]);a,_=net.local_agent('a');b,_=net.local_agent('b');fn=net._function('f','game')
    rows=[('i','one','two')];net.keep_diff(a.id,fn['id'],rows)
    net.remember_compile('key',a,fn,'score 10\n'+'long diff '*200,10)
    repeat=net.repeat_of('key',b,fn)
    out=agent_tokens.feedback(net,b,'game','f',repeat['text'],None)
    assert len(out)<500 and net._last_diffs[b.id,fn['id']][0]==rows
    assert 'one' in agent_tokens.diff_page(net,b,'game','f')


def test_claim_brief_pages_assembly_and_preserves_candidate_and_history(tmp_path):
    net,_=_game(tmp_path,[('f',100,'open')]);a,_=net.local_agent('a');fn=net._function('f','game')
    net._record(fn,a,30,'int f(){return 1;}',1)
    text='## CLAIMED: game/f\n```asm\n'+'\n'.join(f'line{i}' for i in range(200))+'\n```\n### Best attempt\nint f(){return 1;}'
    out=agent_tokens.brief(net,text,'game')
    assert len(out)<len(text) and 'line199' not in out and 'int f(){return 1;}' in out and 'history: get_function' in out


def test_new_tools_and_paging_are_exposed_to_mcp_and_api(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    mcp=tools.build_mcp(net,lambda ctx:(a,'game'))
    definitions=anyio.run(mcp.list_tools)
    for name in ('get_candidate','compile_patch','compile_variants','compile_family','family_targets','get_assembly','last_diff'):
        assert name in {t.name for t in definitions}
        assert name in {t['function']['name'] for t in api_tool_schemas()}
        assert name in tools.tool_names(False) and name in tools.tool_names(True)
    patch=next(t['function'] for t in api_tool_schemas() if t['function']['name']=='compile_patch')
    assert patch['parameters']['required']==['function','revision','old','new']
    assert 'compile_patch' in FINISHING and 'compile_variants' in FINISHING
    batch=next(t['function'] for t in api_tool_schemas() if t['function']['name']=='compile_variants')
    assert batch['parameters']['required']==['function','revision','patches']
    assert batch['parameters']['properties']['patches']['maxItems']==6
    assert 'occurrence' in batch['parameters']['properties']['patches']['items']['properties']
    family=next(t['function'] for t in api_tool_schemas() if t['function']['name']=='compile_family')
    assert family['parameters']['required']==['function','revision','family']
    choices=family['parameters']['properties']['selector']['oneOf']
    assert {c['properties']['mode']['const'] for c in choices}=={'next','anchor','declaration'}
    assert all(c['additionalProperties'] is False for c in choices)


def test_fork_reads_disk_candidate_and_all_target_assembly(tmp_path, monkeypatch):
    from types import SimpleNamespace
    net,_=_game(tmp_path,[('f',600,'open')]);a,_=net.local_agent('a')
    fp=SimpleNamespace(root=tmp_path,unit=lambda name: SimpleNamespace(source=None), diff=lambda *args: SimpleNamespace(target_asm=lambda:'\n'.join(f'op{i}' for i in range(600))))
    monkeypatch.setattr(net,'fork_slug',lambda slug: slug)
    net.forks.get=lambda slug:(None,fp,{})
    net.forks._current_source=lambda fp,fn:'int f(){return 7;}'
    net.forks._meta=lambda fn:{}
    from decompnet.app import agent_evidence
    monkeypatch.setattr(agent_evidence,'baseline',lambda *args:None)
    assert 'return 7' in agent_tokens.get_candidate(net,a,'game','f')
    assert 'op599' in agent_tokens.assembly(net,'game','f',start=590,limit=20)


def test_cached_compile_error_stays_actionable(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a');fn=net._function('f','game')
    net.remember_compile('error',a,fn,'COMPILE ERROR\nunknown type Widget',None)
    text=net.repeat_of('error',a,fn)['text']
    out=agent_tokens.feedback(net,a,'game','f',text,None)
    assert 'unknown type Widget' in out and 'Use last_diff' not in out


def test_compact_project_facts_never_trim_user_instructions(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(tools, '_project_info', lambda *args: 'facts '*1000)
    wish = 'Mandatory user instructions ' * 200
    net=SimpleNamespace(instructions=lambda slug:wish, overview=lambda slug:{}, fork_slug=lambda slug:None,
                        knowledge=SimpleNamespace(for_prompt=lambda slug:''))
    compact=tools.project_info(net,'game')
    assert wish in compact and len(compact)<len(tools.project_info(net,'game',detail=True))


def test_short_tool_descriptions_keep_extended_help(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    mcp=tools.build_mcp(net,lambda ctx:(a,'game'))
    definition=next(t for t in anyio.run(mcp.list_tools) if t.name=='compile_and_diff')
    extended=tools.guide_text('tool:compile_and_diff')
    assert len(definition.description)<len(extended) and 'Forks:' in extended


def test_claim_defers_supporting_sections_but_full_read_keeps_them(tmp_path, monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    text='## CLAIMED: game/f\n### Notes\nlong previous investigation\n### Current source\n```c\nint f(){return 7;}\n```\n### Compiler\nkeep flags\n'
    out=agent_tokens.brief(net,text,'game')
    assert 'long previous investigation' not in out
    assert 'return 7' in out and 'keep flags' in out and 'get_function' in out
    net._record(net._function('f','game'),a,4,'int f(){return 7;}',1)
    monkeypatch.setattr(net, 'get_function', lambda *args: text)
    assert 'long previous investigation' in tools.call(net,a,'game','get_function',{'function':'f'})
    assert 'Recent history' in tools.call(net,a,'game','get_function',{'function':'f'})


def test_patch_prompt_reuses_revisions_and_prefers_small_edits():
    for prompt in (tools.WORK_PROMPT, tools.FORK_PROMPT):
        assert 'Prefer compile_patch for small edits' in prompt
        assert 'Do not fetch get_candidate after a successful patch' in prompt


def test_codex_invalid_effort_is_fatal_but_transient_errors_are_not():
    from decompnet.agents.codex import CODEX
    assert CODEX.fatal('{"code":"unsupported_value","param":"reasoning.effort"}')
    assert CODEX.fatal("Unsupported value: minimal is not supported with this model. Supported reasoning effort values are low and high")
    assert not CODEX.fatal('connection reset; retry later')
    assert not CODEX.fatal('rate limit exceeded')


@pytest.mark.parametrize('mode', ['rom', 'fork'])
def test_independent_variants_use_normal_judge_and_select_best_candidate(tmp_path, monkeypatch, mode):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?', (json.dumps({'kind':mode}),))
        db.execute('UPDATE functions SET best_source=?', ('int f(){return 1;}',))
    net.claim_row(a,'game','f')
    seen=[]
    def compile(agent, function, source, slug):
        seen.append(source);net._record(net._function('f','game'),agent,10,source,1)
        return 'score 10'
    monkeypatch.setattr(net,'compile_and_diff',compile)
    monkeypatch.setattr('decompnet.app.agent_evidence.preflight',lambda *args:None)
    _,rev=agent_tokens.candidate(net,a,'game','f')
    result=tools.call(net,a,'game','compile_variants',{'function':'f','revision':rev,'patches':[
        {'old':'return 1','new':'return 2'}, {'old':'return 1','new':'return 3'}]})
    assert seen==['int f(){return 2;}','int f(){return 3;}']
    assert 'last submitted variant 2' in result
    assert 'Continuation state:' in result and 'applied_patch_ids=1' in result
    assert agent_tokens.candidate(net,a,'game','f')[0]==seen[0]
    with pytest.raises(NetworkError,match='Candidate changed'):
        agent_tokens.variants(net,a,'game','f',rev,[{'old':'return 1','new':'return 5'}])
    _,rev=agent_tokens.candidate(net,a,'game','f')
    with pytest.raises(NetworkError,match='exactly once'):
        agent_tokens.variants(net,a,'game','f',rev,[{'old':'return 2','new':'return 5'}, {'old':'absent','new':'x'}])
    assert len(seen)==2  # validate the whole batch before compiling anything


def test_variants_stop_on_match_and_normal_limit_error(tmp_path, monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?',('int f(){return 1;}',))
    net.claim_row(a,'game','f');_,rev=agent_tokens.candidate(net,a,'game','f')
    calls=[]
    def match(*args):
        calls.append(args)
        with net.store.tx() as db:db.execute("UPDATE functions SET status='matched'")
        return 'MATCH!'
    monkeypatch.setattr(tools,'_compile',match)
    result=agent_tokens.variants(net,a,'game','f',rev,[{'old':'1','new':'2'},{'old':'1','new':'3'}])
    assert len(calls)==1 and 'last submitted variant 1' in result
    with net.store.tx() as db:db.execute("UPDATE functions SET status='open'")
    def limit(*args):raise NetworkError('Max tries reached')
    monkeypatch.setattr(tools,'_compile',limit)
    assert 'stopped: Max tries reached' in agent_tokens.variants(net,a,'game','f',rev,[{'old':'1','new':'2'}])


def test_claim_revision_and_selective_reads(tmp_path, monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    src='int f(){return 1;}'
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?',(src,))
    net.claim_row(a,'game','f');_,rev=agent_tokens.candidate(net,a,'game','f')
    text='## CLAIMED: game/f\n### Current source\n```c\n'+src+'\n```\n### Notes\nuseful note\n### Compiler\ncompiler flags'
    assert rev in agent_tokens.brief(net,text,'game',a)
    monkeypatch.setattr(net,'get_function',lambda *args:text)
    out=tools.call(net,a,'game','get_function',{'function':'f','section':'notes'})
    assert 'useful note' in out and src not in out and 'compiler flags' not in out
    assert 'compiler flags' in agent_tokens.function_view(net,a,'game','f','all')
    assert src in agent_tokens.function_view(net,a,'game','f','candidate')
    with pytest.raises(NetworkError,match='Choose section'):
        agent_tokens.function_view(net,a,'game','f','invalid')


def test_unchanged_instruction_guidance_resets_on_different_diff(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a');fn=net._function('f','game')
    rows=[('i','one','two')];net.keep_diff(a.id,fn['id'],rows)
    full='score 10\n'+scoring.render_rows(rows)
    for _ in range(3):out=agent_tokens.feedback(net,a,'game','f',full,rows)
    assert 'Multiple edits produced identical instructions' in out
    agent_tokens.feedback(net,a,'game','f',full,[('i','other','diff')])
    assert 'Multiple edits' not in agent_tokens.feedback(net,a,'game','f',full,rows)



def test_variant_time_budget_stops_before_next_compile(tmp_path, monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?',('int f(){return 1;}',))
    net.claim_row(a,'game','f');_,rev=agent_tokens.candidate(net,a,'game','f')
    seen=[];monkeypatch.setattr(agent_tokens.time,'monotonic',lambda:61 if seen else 0)
    monkeypatch.setattr(tools,'_compile',lambda *args:seen.append(args) or 'score 10')
    result=agent_tokens.variants(net,a,'game','f',rev,[{'old':'1','new':'2'},{'old':'1','new':'3'}])
    assert len(seen)==1 and '60-second batch budget reached' in result


def test_grouped_batch_compares_start_and_keeps_failures_separate(tmp_path, monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    source='int f(){return 1;}'
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?',(source,))
    net.claim_row(a,'game','f');fn=net._function('f','game')
    before=[('i','base','target')];after=[('i','new','target')]
    net.keep_diff(a.id,fn['id'],before)
    agent_tokens.outcome(net,a,fn,source,'score 20')
    seen=[]
    def compile(agent, function, src, slug):
        seen.append(src)
        if 'return 4' in src:
            net._record(fn,agent,None,src,1)
            return 'COMPILE ERROR\ninvalid candidate score 0'
        net._record(fn,agent,10,src,1);net.keep_diff(a.id,fn['id'],after)
        return 'score 10'
    monkeypatch.setattr(net,'compile_and_diff',compile)
    monkeypatch.setattr('decompnet.app.agent_evidence.preflight',lambda *args:None)
    _,rev=agent_tokens.candidate(net,a,'game','f')
    result=agent_tokens.variants(net,a,'game','f',rev,[{'old':'return 1','new':f'return {i}'} for i in (2,3,4)])
    assert len(seen)==3 and 'Variants 1,2: score 10; improved vs starting score 20' in result
    assert '1 displayed diff rows changed' in result
    assert 'Variants 3: score unavailable' in result and 'invalid candidate' in result
    summary=agent_tokens.tried_summary(net,fn)
    assert 'explicit patches: 3 trials' in summary and 'not exhausted' in summary
    assert 'Already tried' in agent_tokens.brief(net,'## CLAIMED: game/f\n','game',a)


def test_equal_score_different_diffs_do_not_group(tmp_path, monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?',('int f(){return 1;}',))
    net.claim_row(a,'game','f');fn=net._function('f','game')
    def compile(agent,function,src,slug):
        net._record(fn,agent,10,src,1);net.keep_diff(a.id,fn['id'],[('i',src,'target')]);return 'score 10'
    monkeypatch.setattr(net,'compile_and_diff',compile)
    monkeypatch.setattr('decompnet.app.agent_evidence.preflight',lambda *args:None)
    _,rev=agent_tokens.candidate(net,a,'game','f')
    result=agent_tokens.variants(net,a,'game','f',rev,[{'old':'1','new':'2'},{'old':'1','new':'3'}])
    assert 'Variants 1:' in result and 'Variants 2:' in result and 'Variants 1,2:' not in result
    assert 'baseline unavailable' in result


def test_generated_families_are_bounded_and_explicit():
    casts=agent_tokens.family_patches('casts','value')
    assert len(casts)==6 and casts[0]=={'old':'value','new':'(int)(value)'}
    assert agent_tokens.family_patches('signedness','unsigned int value;')[0]['new']=='int value;'
    assert agent_tokens.family_patches('signedness','s32 value;')[0]['new']=='u32 value;'
    assert agent_tokens.family_patches('declaration_order','int a;\nshort b;')[0]['new']=='short b;\nint a;'
    for family,old in [('invalid','x'),('signedness','float x;'),('declaration_order','int a=side_effect();\nint b;')]:
        with pytest.raises(NetworkError):agent_tokens.family_patches(family,old)


@pytest.mark.parametrize('mode',['rom','fork'])
def test_local_family_uses_same_batch_judge_and_records_history(tmp_path, monkeypatch, mode):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'kind':mode}),))
        db.execute('UPDATE functions SET best_source=?',('int f(){int x;return x;}',))
    net.claim_row(a,'game','f');fn=net._function('f','game');seen=[]
    def compile(agent,function,source,slug):
        seen.append(source);net._record(fn,agent,10,source,1);return 'score 10'
    monkeypatch.setattr(net,'compile_and_diff',compile)
    monkeypatch.setattr('decompnet.app.agent_evidence.preflight',lambda *args:None)
    _,rev=agent_tokens.candidate(net,a,'game','f')
    result=tools.call(net,a,'game','compile_family',{'function':'f','revision':rev,'family':'signedness','old':'int x;'})
    assert seen==['int f(){unsigned int x;return x;}'] and 'Generated changes' in result
    assert 'signedness: 1 trials' in agent_tokens.tried_summary(net,fn)


def test_occurrence_selection_and_actionable_errors():
    from decompnet.app import agent_targets
    source='int f(){return value + value;}'
    assert agent_targets.replace_anchor(source,'value','other',2)=='int f(){return value + other;}'
    with pytest.raises(NetworkError,match='Found 2 occurrences'):
        agent_targets.replace_anchor(source,'value','other')
    for occurrence in (-1,3,True,'1'):
        with pytest.raises(NetworkError):agent_targets.replace_anchor(source,'value','other',occurrence)


@pytest.mark.parametrize('mode',['rom','fork'])
def test_named_and_occurrence_family_targets_use_normal_judge(tmp_path, monkeypatch,mode):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    source='int f(){\n  int a;\n  short b;\n  return a + a;\n}'
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'kind':mode}),))
        db.execute('UPDATE functions SET best_source=?',(source,))
    net.claim_row(a,'game','f');fn=net._function('f','game');seen=[]
    def compile(agent,function,source,slug):
        seen.append(source);net._record(fn,agent,10,source,1);return 'score 10'
    monkeypatch.setattr(net,'compile_and_diff',compile)
    monkeypatch.setattr('decompnet.app.agent_evidence.preflight',lambda *args:None)
    _,rev=agent_tokens.candidate(net,a,'game','f')
    out=tools.call(net,a,'game','compile_family',{'function':'f','revision':rev,'family':'declaration_order','declaration':'a'})
    assert 'score 10' in out and '  short b;\n  int a;' in seen[0]
    _,rev=agent_tokens.candidate(net,a,'game','f')
    out=tools.call(net,a,'game','compile_family',{'function':'f','revision':rev,'family':'casts','old':'a','occurrence':3})
    assert len(seen)==7 and 'return a + (int)(a)' in seen[1]
    _,rev=agent_tokens.candidate(net,a,'game','f')
    out=tools.call(net,a,'game','compile_family',{'function':'f','revision':rev,'family':'signedness'})
    assert 'Selected declaration' in out and len(seen)==8
    _,rev=agent_tokens.candidate(net,a,'game','f')
    out=tools.call(net,a,'game','compile_patch',{'function':'f','revision':rev,'old':'a','new':'other','occurrence':1})
    assert 'int other;' in seen[-1]  # occurrence counts exact nonoverlapping text, including declarations


def test_declaration_choices_ignore_comments_and_require_supported_pairs():
    from decompnet.app import agent_targets
    source='/*\nint fake;\n*/\nint a;\nshort b;\nint initialized=call();\n'
    assert [d[0] for d in agent_targets.declarations(source)]==['a','b']
    assert 'declaration_order(declaration=a)' in agent_targets.suggestions(source)
    assert agent_targets.named_anchor(source,'a','declaration_order')=='int a;\nshort b;'
    assert 'fake' not in agent_targets.choices(source)
    assert 'declaration_order' not in agent_targets.suggestions('int a;\n\nshort b;')
    with pytest.raises(NetworkError,match='exactly one'):
        agent_targets.named_anchor('int a;\nint a;','a','signedness')


def test_claim_compacts_shared_headers_preserving_candidate_and_user_text(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    source='int f(){\n int a;\n short b;\n return a;\n}'
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?',(source,))
    net.claim_row(a,'game','f')
    declarations='\n'.join(f'extern int data{i};' for i in range(30))
    text='## CLAIMED: game/f\n### Declarations it uses\n```c\n'+declarations+'\n```\n### Best attempt\n```c\n'+source+'\n```\nMandatory user instructions: retain this.\n'
    out=agent_tokens.brief(net,text,'game',a)
    assert source in out and 'Mandatory user instructions: retain this.' in out
    assert 'data29' not in out and 'section="declarations"' in out
    assert 'Local families available' in out and 'declaration=a' in out
    assert len(out)<len(text)+400  # suggestions remain bounded



def test_claim_deduplicates_short_shared_header_and_generated_next_step(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')])
    next_step='Next: read context.h with get_context, write C for this function, and iterate with compile_and_diff until score is 0. Submit only the function body (plus any helper declarations); context.h is included automatically.'
    block='### Declarations it uses\n```c\nextern int shared;\n```\n'
    text='## CLAIMED: game/f\n'+block+'### Candidate\n```c\nint f(){return 1;}\n```\n'+next_step+'\n'+block+'### Candidate\n```c\nint g(){return 2;}\n```\n'+next_step+'\n'
    out=agent_tokens.brief(net,text,'game')
    assert out.count('extern int shared;')==1 and out.count(next_step)==1
    assert 'int f(){return 1;}' in out and 'int g(){return 2;}' in out


def test_family_choices_only_offer_usable_pairs():
    from decompnet.app import agent_targets
    source='int a;\nshort b;\ncall();\nint c;\n'
    assert [t['name'] for t in agent_targets.valid_targets(source,'declaration_order')]==['a']
    out=agent_targets.choices(source,'declaration_order')
    assert out=='Valid declaration_order targets: a -> b (line 1)'
    with pytest.raises(NetworkError,match='Valid declaration_order targets: a -> b'):
        agent_targets.named_anchor(source,'c','declaration_order')
    assert not agent_targets.valid_targets('int a;\nint a;\n','signedness')


@pytest.mark.parametrize('mode',['rom','fork'])
def test_current_target_listing_and_auto_selection_are_revision_scoped(tmp_path, monkeypatch,mode):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    source='int f(){\n int a;\n short b;\n char c;\n return a;\n}'
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'kind':mode}),))
        db.execute('UPDATE functions SET best_source=?',(source,))
    net.claim_row(a,'game','f')
    net._record(net._function('f','game'),a,10,source,1)
    _,rev=agent_tokens.candidate(net,a,'game','f')
    seen=[]
    # Cached-style trials retain the candidate: makes repeat selection under the same snapshot observable.
    def compile(*args):
        seen.append(args[4])
        agent_tokens.outcome(net,a,net._function('f','game'),args[4],'score 10')
        return 'score 10'
    monkeypatch.setattr(tools,'_compile',compile)
    monkeypatch.setattr('decompnet.app.agent_evidence.preflight',lambda *args:None)
    listed=tools.call(net,a,'game','family_targets',{'function':'f','expression':' a'})
    assert rev in listed and 'a -> b' in listed and 'b -> c' in listed and '2 literal occurrences' in listed
    assert seen==[]
    first=tools.call(net,a,'game','compile_family',{'function':'f','revision':rev,'family':'declaration_order','target':'next'})
    second=tools.call(net,a,'game','compile_family',{'function':'f','revision':rev,'family':'declaration_order','target':'next'})
    third=tools.call(net,a,'game','compile_family',{'function':'f','revision':rev,'family':'declaration_order','target':'next'})
    assert 'Selected declaration a' in first and 'Selected declaration b' in second
    assert 'No untried valid' in third and len(seen)==2
    assert 'tried' in agent_tokens.family_targets(net,a,'game','f')
    with net.store.tx() as db:db.execute('UPDATE projects SET config=?',(json.dumps({'kind':mode,'gp':4096}),))
    assert 'untried' in agent_tokens.family_targets(net,a,'game','f')
    fn=net._function('f','game');previous_scope=agent_tokens.target_scope(net,fn,rev)
    with net.store.tx() as db:
        db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,9999,'extern int changed;','')",(fn['project_id'],))
    assert previous_scope != agent_tokens.target_scope(net,fn,rev)
    with pytest.raises(NetworkError,match='Candidate changed'):
        agent_tokens.local_family(net,a,'game','f','stale','declaration_order',target='next')
    with pytest.raises(NetworkError,match='do not combine'):
        agent_tokens.local_family(net,a,'game','f',rev,'declaration_order',declaration='a',target='next')


def test_blank_redraft_is_actionable_and_does_not_invoke_drafter(tmp_path, monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a');seen=[]
    monkeypatch.setattr(net,'redraft',lambda *args:seen.append(args) or 'draft')
    result=tools.call(net,a,'game','redraft',{'function':'f','context':'  '})
    assert 'No redraft was run' in result and 'section="candidate"' in result and seen==[]
    assert tools.call(net,a,'game','redraft',{'function':'f','context':'extern int count;'})=='draft'
    assert len(seen)==1
    schemas={t['function']['name']:t['function'] for t in api_tool_schemas()}
    assert schemas['redraft']['parameters']['properties']['context']['minLength']==1
    assert 'NOT function-specific notes' in schemas['share_knowledge']['description']


@pytest.mark.parametrize('mode',['rom','fork'])
@pytest.mark.parametrize('family,source,expected',[
    ('typed_temporary','void f(u16 *d,u32 w){*(u16*)d=(u16)(w>>16);}','__repair_narrow'),
    ('call_result_inline','int f(u8*a,u8*b){u8 x,y;x=read(*a);y=read(*b);if(x>y)return 1;return 0;}','(u8)read(*a)'),
])
def test_pattern_family_uses_normal_judge_and_captures_provenance(tmp_path, monkeypatch,mode,family,source,expected):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'kind':mode,'compiler':{'toolchain':'example','flags':[]}}),))
        db.execute('UPDATE functions SET best_source=?',(source,))
    net.claim_row(a,'game','f');fn=net._function('f','game');seen=[]
    def compile(agent,function,source,slug):
        seen.append(source);net._record(fn,agent,0,source,1,matched=True);return 'MATCH!'
    monkeypatch.setattr(net,'compile_and_diff',compile)
    monkeypatch.setattr('decompnet.app.agent_evidence.preflight',lambda *args:None)
    _,rev=agent_tokens.candidate(net,a,'game','f')
    out=tools.call(net,a,'game','compile_family',{'function':'f','revision':rev,'family':family})
    assert len(seen)==1 and expected in seen[0] and 'MATCH!' in out
    case=net.repair_feedback.listing('game')['examples'][0]['id']
    assert net.repair_feedback.detail('game',case)['evidence']['provenance']['family']==family


@pytest.mark.parametrize('mode',['rom','fork'])
@pytest.mark.parametrize('scores,expected', [([20,30],1),([5,30],2),([None,None],1)])
def test_batch_continuation_keeps_baseline_or_best_without_extra_attempt(tmp_path,monkeypatch,mode,scores,expected):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a');fn=net._function('f','game')
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'kind':mode}),))
    net.claim_row(a,'game','f');net._record(fn,a,10,'int f(){return 1;}',1)
    seen=[]
    def compile(agent,function,source,slug):
        score=scores[len(seen)];seen.append(source);net._record(fn,agent,score,source,1)
        net.keep_diff(agent.id,fn['id'],[('i',str(score),'target')])
        return 'COMPILE ERROR' if score is None else 'score '+str(score)
    monkeypatch.setattr(net,'compile_and_diff',compile)
    monkeypatch.setattr('decompnet.app.agent_evidence.preflight',lambda *args:None)
    _,rev=agent_tokens.candidate(net,a,'game','f')
    out=agent_tokens.variants(net,a,'game','f',rev,[{'old':'return 1','new':'return 2'},{'old':'return 1','new':'return 3'}])
    selected,newrev=agent_tokens.candidate(net,a,'game','f')
    assert f'return {expected}' in selected
    assert net.store.one('SELECT COUNT(*) AS n FROM attempts')['n']==3
    assert 'Continuation:' in out and newrev in out
    assert out.startswith('Current candidate revision '+newrev)
    assert 'Discard the starting revision' in out
    assert net._function('f','game')['status']=='open'
    # A stale request rejects without spending another compile and supplies the current state/delta.
    if newrev!=rev:
        with pytest.raises(NetworkError,match='Current candidate revision') as error:
            agent_tokens.patch(net,a,'game','f',rev,'return 1','return 7')
        assert 'Current changes' in str(error.value)
        assert len(seen)==2
    # The next edit starts from the selected C; selection is invalidated by the new attempt.
    def next_compile(agent,function,source,slug):
        net._record(fn,agent,4,source,1)
        return 'score 4'
    monkeypatch.setattr(net,'compile_and_diff',next_compile)
    agent_tokens.patch(net,a,'game','f',newrev,f'return {expected}','return 7')
    assert 'return 7' in agent_tokens.candidate(net,a,'game','f')[0]


def test_no_effect_streak_suggests_applicable_untried_family(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a');fn=net._function('f','game')
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?',('int f(){\nint x;\nreturn x;}',))
    net.claim_row(a,'game','f');rows=[('i','one','two')];net.keep_diff(a.id,fn['id'],rows)
    for _ in range(3):
        text=agent_tokens.feedback(net,a,'game','f','score 10\nmarkers: i=operand',rows)
    assert 'Next valid local experiment' in text and 'family="signedness"' in text and 'target="next"' in text


@pytest.mark.parametrize('mode', ['rom','fork'])
def test_generated_compile_errors_are_cached_under_exact_build_inputs(tmp_path, monkeypatch, mode):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    source = 'int f(){return 1;}'
    bad = 'int f(){return missing;}'
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'kind':mode}),))
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?',(source,))
    net.claim_row(a,'game','f');_,rev=agent_tokens.candidate(net,a,'game','f')
    monkeypatch.setattr('decompnet.app.agent_evidence.preflight',lambda *args:None)
    monkeypatch.setattr(tools,'_compile',lambda *args:'❌ COMPILE ERROR\nunknown identifier missing')
    agent_tokens.variants(net,a,'game','f',rev,[{'old':source,'new':bad}], 'local repairs')
    fn=net._function('f','game')
    assert agent_tokens.compiled_before(net,fn,bad)
    assert not agent_tokens.compiled_before(net,fn,bad+' ')
    with net.store.tx() as db:
        db.execute('UPDATE functions SET target=?',(b'new target',))
    assert not agent_tokens.compiled_before(net,net._function('f','game'),bad)


def test_infrastructure_errors_are_not_cached_as_bad_source(tmp_path, monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    source = 'int f(){return 1;}'
    bad = 'int f(){return 2;}'
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?',(source,))
    net.claim_row(a,'game','f');_,rev=agent_tokens.candidate(net,a,'game','f')
    def broken(*args):
        raise NetworkError('Toolchain infrastructure failure')
    monkeypatch.setattr(tools,'_compile',broken)
    agent_tokens.variants(net,a,'game','f',rev,[{'old':source,'new':bad}], 'local repairs')
    assert not agent_tokens.compiled_before(net,net._function('f','game'),bad)


@pytest.mark.parametrize('imported', [False, True])
def test_assembly_metadata_setting_applies_to_pages_briefs_and_full_reads(tmp_path, monkeypatch, imported):
    from types import SimpleNamespace
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    agent, _ = net.local_agent('reader')
    raw = ('glabel f\n/* 3B18C 8003A58C 03E00008 */ jr $ra\n'
           '8003A590  nop\n.L8003A598:\n    beq $a0, $zero, .L8003A598\n'
           '    lui $a0, %hi(D_80040000) # retain comment')
    with net.store.tx() as db:
        db.execute('UPDATE functions SET asm=? WHERE name=?', (raw, 'f'))
    text = '### Target assembly\n```asm\n' + raw + '\n```'
    monkeypatch.setattr(net, 'get_function', lambda *args: text)
    if imported:
        monkeypatch.setattr(net, 'fork_slug', lambda slug: slug)
        net.forks.get = lambda slug: (None, SimpleNamespace(
            unit=lambda _: None, diff=lambda *args: SimpleNamespace(target_asm=lambda: raw)), {})
        net.forks._meta = lambda fn: {}
    else:
        monkeypatch.setattr(net, '_asm_text', lambda *args: raw)
    assert '03E00008' in agent_tokens.assembly(net, 'game', 'f', focus='page', agent=agent)
    net.set_selection('game', agent.name, {'assembly_metadata': False})
    outputs = [agent_tokens.assembly(net, 'game', 'f', focus='page', agent=agent),
               agent_tokens.brief(net, text, 'game', agent),
               agent_tokens.function_view(net, agent, 'game', 'f', 'all')]
    for output in outputs:
        assert '03E00008' not in output and '8003A590' not in output
        assert 'jr $ra' in output and 'nop' in output
        assert '.L8003A598:' in output and '%hi(D_80040000)' in output
        assert '# retain comment' in output
    net.set_selection('game', agent.name, {'assembly_metadata': True})
    assert raw in agent_tokens.assembly(net, 'game', 'f', focus='page', agent=agent)
    assert net._function('f', 'game')['asm'] == raw  # no stored target edits


def test_candidate_and_startup_brief_include_exact_baseline_line_evidence(tmp_path, monkeypatch):
    from decompnet.app import agent_evidence
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    agent, _ = net.local_agent('a')
    source = 'int f(){return 1;}'
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?,best_score=10', (source,))
    note = '\nCompiled C lines associated with differences (advisory, not causes):\n  C line 1: return 1; (1 differing instructions)'
    monkeypatch.setattr(agent_evidence,'baseline',lambda *args:{'source':source,'line_note':note})
    assert note in agent_tokens.get_candidate(net,agent,'game','f')
    text = agent_tokens.brief(net, '## CLAIMED: game/f\n```c\n'+source+'\n```', 'game',agent)
    assert note in text and text.index(note) < text.index('## CLAIMED:')
    monkeypatch.setattr(agent_evidence,'baseline',lambda *args:{'source':'different C','line_note':note})
    assert note not in agent_tokens.get_candidate(net,agent,'game','f')
    assert 'no line locations inferred' in agent_tokens.get_candidate(net,agent,'game','f')


def test_compact_compile_feedback_preserves_line_mapping(tmp_path):
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    agent, _ = net.local_agent('a')
    fn = net._function('f','game')
    rows = [('i','add a,b,c','add a,b,d')]
    net._last_diffs[agent.id,fn['id']] = (rows,scoring.LEGEND)
    note = '\nCompiled C lines associated with differences (advisory, not causes):\n  C line 2: return x; (1 differing instructions)\n    i target: add | compiled: sub'
    text = 'score 10\nmarkers: i=operand\nlisting'+note+'\ncompiler output:\na warning'
    for previous in (None,rows):
        out = agent_tokens.feedback(net,agent,'game','f',text,previous)
        if previous is None:
            assert note in out and out.count('C line 2:') == 1
        else:
            assert note not in out and 'repeated C-line table omitted' in out
        assert 'a warning' in out
