"""Homemade disjoint phases; private game inputs are supplied only by an opt-in replay."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from decompnet.app import draft_repairs, iterative_repairs, repair_ranking
from decompnet.app import scratch_role_repairs as repairs
from decomprepair import integer_abi

HEADER='typedef unsigned char u8; typedef unsigned short u16; typedef int s32;'
SOURCE='''int f(u8 *object, int key) {
 int answer;
 u8 *link;
 if ((*((u16 *)(object + 6))) == 3) {
   link = *((u8 **)(object + 12));
   key = read_key(key);
   answer = *(int *)(link + 8);
   if (key != 1234) { return 1; }
   answer = answer ^ 1234;
   goto done;
 }
 answer = key ^ 1234;
 done:
 return answer != 0;
}'''
ROWS=[(' ','lhu $v1, 6($a0)','lhu $v1, 6($a0)'),
      ('r','lw $v1, 12($a0)','lw $v0, 12($a0)'),
      ('r','lw $a1, 8($v1)','lw $a1, 8($v0)'),
      ('r','addiu $v1, $zero, 1234','addiu $v0, $zero, 1234')]


def variants(source=SOURCE,header=HEADER,arch='mips'):
    return repairs.variants(source,'f',header,arch)


def test_combined_candidate_stages_actual_reads_and_constant_only_after_pointer_dies():
    candidates=variants()
    assert len(candidates)<=repairs.LIMIT
    combined=candidates[0][1]
    assert 'repair_scratch = *((u16 *)(object + 6));' in combined
    assert 'repair_scratch = (int)(*((u8 **)(object + 12)));' in combined
    assert '((u8 *)repair_scratch) + 8' in combined
    assert 'repair_scratch = 1234;\n  if (key != repair_scratch)' in combined
    assert 'answer = answer ^ repair_scratch;' in combined
    assert 'answer = key ^ 1234;' in combined  # other arm is not dominated
    assert combined.count('*((u16 *)')==SOURCE.count('*((u16 *)')
    assert combined.count('read_key(key)')==1
    assert 'goto done;' in combined and 'return answer != 0;' in combined
    assert not any(x in combined for x in ('asm','register ','long long'))


def test_partial_improvement_can_resume_sentinel_phase_without_restarting_selector():
    partial=repairs.pointer_phases(SOURCE,'f',HEADER,'mips')[0][1]
    assert 'if (key != 1234)' in partial
    continuation=repairs.sentinel_phases(partial,'f',HEADER,'mips')
    assert continuation and continuation[0] in variants()
    assert continuation[0][1].count('repair_scratch = *((u16 *)')==1


@pytest.mark.parametrize('arch',sorted(integer_abi.ARCHES))
def test_shared_draft_path_reaches_known_32_bit_targets(arch):
    labels={}
    result=draft_repairs.variants(SOURCE,'f',arch,header=HEADER,labels=labels,limit=None)
    assert any(labels[c]==repairs.FAMILY for c in result)
    assert repairs.FAMILY in draft_repairs.AGENT_FAMILIES


@pytest.mark.parametrize('change',[
    ('link = *((u8 **)(object + 12));','use(link); link = *((u8 **)(object + 12));'),
    ('key = read_key(key);','use(&link); key = read_key(key);'),
    ('key = read_key(key);','{ int link; link = 1; use(link); } key = read_key(key);'),
    ('key = read_key(key);','++link; key = read_key(key);'),
    ('key = read_key(key);','++(link); key = read_key(key);'),
    ('key = read_key(key);','use(link = object); key = read_key(key);'),
    ('key = read_key(key);','link += 1; key = read_key(key);'),
    ('if ((*((u16 *)(object + 6))) == 3) {','if (key) if ((*((u16 *)(object + 6))) == 3) {'),
    ('*((u16 *)(object + 6))','*((u16 *)(get_object() + 6))'),
    ('*((u16 *)(object + 6))','*((u32 *)(object + 6))'),
    ('*((u16 *)(object + 6))','*((volatile u16 *)(object + 6))'),
    ('u8 *link;','u8 *link = object;'),
])
def test_unsafe_pointer_or_selector_shapes_are_declined(change):
    assert not repairs.pointer_phases(SOURCE.replace(*change),'f',HEADER,'mips')


def test_escaping_macros_observers_unknown_abi_and_alias_widths_are_declined():
    assert not variants(arch='unknown')
    assert not variants(header=HEADER.replace('unsigned char u8','unsigned long long u8'))
    assert not variants(header=HEADER+'\n#define link other')
    assert not variants(SOURCE.replace('key = read_key(key);','READ(link);'),HEADER+'\n#define READ(v) use(v)')
    assert not variants(SOURCE.replace('int answer;','int answer; int line = __LINE__;'))


def test_live_or_redefined_scratch_cannot_be_overwritten_by_sentinel():
    partial=repairs.pointer_phases(SOURCE,'f',HEADER,'mips')[0][1]
    assert not repairs.sentinel_phases(partial.replace('return answer != 0;','return answer != repair_scratch;'),'f',HEADER,'mips')
    assert not repairs.sentinel_phases(partial.replace('answer = answer ^ 1234;','repair_scratch = 5; answer = answer ^ 1234;'),'f',HEADER,'mips')
    assert not repairs.sentinel_phases(partial.replace('1234','0xFFFFFFFFU'),'f',HEADER,'mips')


def test_backedge_reaching_old_pointer_phase_prevents_sentinel_reuse():
    partial=repairs.pointer_phases(SOURCE,'f',HEADER,'mips')[0][1]
    partial=partial.replace('repair_scratch = (int)','again: repair_scratch = (int)').replace('answer = answer ^ 1234;','if (key) goto again; answer = answer ^ 1234;')
    assert not repairs.sentinel_phases(partial,'f',HEADER,'mips')


def test_register_only_priority_and_agent_plan_have_a_concrete_next_step():
    ordered=repair_ranking.order(['declaration_order',repairs.FAMILY,'pointer_capture_reuse'],ROWS)
    assert ordered[0]==repairs.FAMILY
    brief=repairs.brief(SOURCE,'f',HEADER,'mips',ROWS)
    assert 'earlier matching rows' in brief and 'partial improvement' in brief
    assert not repairs.brief(SOURCE,'f',HEADER,'mips',[('i','addiu $v1, $zero, 12','addiu $v1, $zero, 13')])
    report=dict(score=20,rows=ROWS)
    pool=iterative_repairs.targeted_variants(SOURCE,report,dict(name='f',asm=''),'mips',[],HEADER,tagged=True,limit=12)
    assert pool[0][0]==repairs.FAMILY


def test_existing_beam_keeps_partial_step_and_finishes_with_same_family():
    partial=repairs.pointer_phases(SOURCE,'f',HEADER,'mips')[0][1]
    exact=repairs.sentinel_phases(partial,'f',HEADER,'mips')[0][1]
    calls=[]
    def evaluate(source):
        calls.append(source)
        return dict(score=0 if source==exact else 10 if source==partial else 20,matched=source==exact,rows=ROWS)
    def generate(source,report):
        return [(repairs.FAMILY,partial)] if source==SOURCE else repairs.sentinel_phases(source,'f',HEADER,'mips')
    state=dict(pending=[dict(source=SOURCE,depth=0,ceiling=20)],next=[],seen=[SOURCE])
    result=iterative_repairs.advance(state,evaluate,generate,lambda:len(calls)>=6,arch='mips')
    assert result is True and state['reason']=='matched'
    assert state['winning_path']==[repairs.FAMILY,repairs.FAMILY]
    assert calls==[SOURCE,partial,exact]


def test_private_original_checkpoint_replay_when_supplied():
    location=os.environ.get('DECOMPNET_SCRATCH_ROLE_REPLAY')
    if not location:pytest.skip('Supply a private frozen case; no game data in CI.')
    from decompnet.app.linked_repair_benchmark import evaluator
    case=json.loads(Path(location).read_text())
    source=Path(case['before']).read_text();header=Path(case['header']).read_text()
    assert hashlib.sha256(source.encode()).hexdigest()==case['source_sha256']
    evaluate,scope,arch,asm,_=evaluator(Path(case['project']),case['function'],header)
    assert scope==case['scope']
    baseline=evaluate(source)
    assert baseline['score']==case['baseline_score'] and not baseline['matched']
    # Only generated proposals; the exact source is never read or supplied.
    pool=iterative_repairs.targeted_variants(source,baseline,dict(name=case['function'],asm=asm),
        arch,scope['compiler']['flags'],header,tagged=True,limit=12)
    assert pool and pool[0][0]==repairs.FAMILY
    for trial,(_,candidate) in enumerate(pool,1):
        result=evaluate(candidate)
        if result['matched']:
            assert result['score']==0 and trial==1
            break
    else:pytest.fail('No generated scratch-role candidate recovered the exact linked bytes.')


def test_agent_evidence_includes_applicable_plan_and_build_scoped_failed_trials():
    from decompnet.app import agent_tokens, search_evidence
    from tests.test_permute import AGENT, FakeNet
    net=FakeNet()
    net._context=lambda *_:dict(header=HEADER,version=1)
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=? WHERE id=1',(SOURCE,))
    fn=net.function();proj=net.project_row('game')
    net._last_diffs={(AGENT.id,fn['id']):(ROWS,'legend')}
    search_evidence.remember(net,AGENT,proj,fn,SOURCE,20)
    scope=agent_tokens.target_scope(net,fn,'')
    with net.store.tx() as db:
        db.execute('INSERT INTO agent_family_targets VALUES(?,?,?,?)',(fn['id'],scope,'compiled-error','hash'))
    summary=search_evidence.brief(net,proj,fn)
    assert 'Scratch-role plan' in summary and 'Try scratch_role_reuse.' in summary
    assert '1 compiled-error' in summary and 'not proof that a whole family' in summary
    net._context=lambda *_:dict(header=HEADER+'\nint other;',version=2)
    assert search_evidence.brief(net,proj,fn)==''


@pytest.mark.parametrize('imported',[False,True])
def test_deep_search_uses_generated_bundle_and_stops_at_exact(monkeypatch,imported):
    from decompnet.app import deep_permuter
    from tests.test_deep_permuter import Log, setup
    from tests.test_permute import AGENT
    generate=draft_repairs.variants
    net=setup(monkeypatch,imported)
    monkeypatch.setattr(draft_repairs,'variants',generate)
    net._context=lambda *_:dict(header=HEADER,version=1)
    net.is_rom=lambda *_:False  # no optional compiler-phase captures in this fixture
    if imported:net.forks.permuter_header=lambda *_:HEADER
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?,best_score=20 WHERE id=1',(SOURCE,))
    fn=net.function();proj=net.project_row('game');seen=[]
    exact=variants()[0][1]
    search=deep_permuter.Search(net,4,20,Log(),fn);search.winner_diff=(ROWS,'legend')
    monkeypatch.setattr(search,'_repair_preflight',lambda *_:20)
    def judge(agent,proj,fn,source,best,fork):
        seen.append(source)
        score=0 if source==exact else 20
        if score<best:
            with net.store.tx() as db:
                db.execute('UPDATE functions SET best_source=?,best_score=?,status=? WHERE id=?',(source,score,'matched',fn['id']))
        return score
    monkeypatch.setattr(search,'_score',judge)
    assert search.local_repairs(AGENT,proj,fn,20,'fork' if imported else None)[0]==0, (seen,search.local_status)
    assert seen==[exact]
