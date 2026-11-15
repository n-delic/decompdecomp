import pytest

from decompnet.app import targeted_storage_repairs as repairs
from decompnet.app import trial_evidence
from decompnet.arch.mips_local_storage import read
from decomprepair import repair_localisation

STACK=[(' ','addiu $sp, $sp, -0x60','addiu $sp, $sp, -0x60'),('s','sw $t0, 0x14($sp)','sw $t0, 0x34($sp)'),('s','lw $t0, 0x14($sp)','lw $t0, 0x34($sp)')]
REG=[('r','lw $v1, 0x6c($a1)','lw $v0, 0x6c($a1)'),('r','lw $a1, 0x24($v1)','lw $a1, 0x24($v0)')]
SOURCE='''int f(int x){
 int a;
 int b;
 int value;
 a=x;
 b=a;
 value=other(b);
 return value;
}'''


def locations(source,line=6):return repair_localisation.bind(source,dict(available=True,lines=[dict(line=line)]))


def test_spill_reader_requires_one_complete_pair_and_unchanged_frame():
    assert read(STACK)==dict(kind='spill',old=52,new=20,register='t0',frame=96)
    assert read(STACK[:-1]) is None
    assert read([STACK[0],STACK[1],('s','lw $t1, 0x14($sp)','lw $t1, 0x34($sp)')]) is None
    assert read([('s','addiu $sp, $sp, -0x60','addiu $sp, $sp, -0x70'),*STACK[1:]]) is None
    assert read([STACK[0],('s','sw $ra, 0x14($sp)','sw $ra, 0x34($sp)'),('s','lw $ra, 0x14($sp)','lw $ra, 0x34($sp)')]) is None


def test_one_register_chain_requires_identical_nonregister_operands():
    assert read(REG)==dict(kind='register',target='$v1',candidate='$v0')
    assert read([REG[0],('r','addiu $v1, $zero, 2','addiu $v0, $zero, 3')]) is None
    assert read([('r','lw $sp, 4($a0)','lw $v0, 4($a0)')]) is None
    assert read([('r',None,'nop')]) is None


def test_spill_storage_is_mapped_to_one_local_and_bounded():
    variants=repairs.variants(SOURCE,'f',arch='mips',rows=STACK,locations=locations(SOURCE))
    assert variants and len(variants)<=repairs.LIMIT
    assert any('__repair_spill_slot[1]' in s and '__repair_spill_slot[0]=a' in s for _,s in variants)
    assert all(family==repairs.FAMILIES[0] for family,_ in variants)
    assert all('int value;' in s and 'a=x;' in s for _,s in variants)
    assert not repairs.variants(SOURCE,'f',arch='mips',rows=STACK,locations=locations(SOURCE+' '))
    assert not repairs.variants(SOURCE,'f',arch='mips',rows=STACK)
    assert not repairs.variants(SOURCE,'f',arch='ppc',rows=STACK,locations=locations(SOURCE))


def test_register_lifetime_keeps_rhs_evaluation_count_and_type():
    variants=repairs.variants(SOURCE,'f',arch='mips',rows=REG,locations=locations(SOURCE,7))
    assert any('__repair_chain_value = other(b);\nvalue = __repair_chain_value;' in s for _,s in variants)
    assert all(s.count('other(b)')==1 for _,s in variants)


@pytest.mark.parametrize('change',[
    lambda s:s.replace('int b;','volatile int b;'),
    lambda s:s.replace('b=a;','escape(&(b)); b=a;'),
    lambda s:s.replace('b=a;','{int b; b=a;}'),
    lambda s:s.replace('b=a;','b=__LINE__;'),
])
def test_escaped_qualified_shadowed_and_line_observing_values_decline(change):
    s=change(SOURCE)
    assert not repairs.variants(s,'f',arch='mips',rows=STACK,locations=locations(s))


def test_line_trials_reserve_exploration_but_no_longer_lead_a_register_miss():
    families=['debug_statement_boundary','source_lines']+['f'+str(i) for i in range(12)]
    ordered=trial_evidence.order(families,REG)
    assert ordered[:7]==['f'+str(i) for i in range(7)]
    assert ordered[7]=='debug_statement_boundary'
    assert set(ordered)==set(families)
    scheduling=[('o','lw $t0, ($a0)','lw $t1, ($a1)'),('o','lw $t1, ($a1)','lw $t0, ($a0)')]
    assert trial_evidence.order(families,scheduling)==families


def test_iterative_pipeline_includes_mapped_storage_candidates():
    from decompnet.app import iterative_repairs
    proposals=iterative_repairs.targeted_pool(SOURCE,dict(rows=STACK,repair_locations=locations(SOURCE)),dict(name='f',asm=''),'mips',[])
    assert any(family==repairs.FAMILIES[0] for family,_ in proposals)


def test_verified_agent_baseline_retains_compiler_mapping_for_local_repairs(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace

    from decompnet import scoring, source_lines
    from decompnet.app import agent_evidence
    from tests.test_claims import _game
    net,pid=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('worker');fn=net._function('f','game')
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':[]}}),))
        db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,1,'','')",(pid,))
    monkeypatch.setattr(net,'_compile',lambda *args:SimpleNamespace(ok=True,obj=b'compiled',language='c',effective_settings={}))
    monkeypatch.setattr(net,'_evaluate',lambda *args:(object(),False,[]))
    monkeypatch.setattr(net,'_target_obj',lambda *args:object())
    monkeypatch.setattr(scoring,'diff',lambda *args:SimpleNamespace(score=2,rows=STACK))
    monkeypatch.setattr(source_lines,'mismatches',lambda *args,**kwargs:dict(available=True,lines=[dict(line=6,instructions=[])],unmapped=0))
    measured=agent_evidence.shared_baseline(net,agent,'game',fn,SOURCE)
    assert measured and measured['verified']
    assert repair_localisation.lines(SOURCE,measured['repair_locations'])=={6}
    assert repairs.variants(SOURCE,'f',arch='mips',rows=measured['rows'],locations=measured['repair_locations'])


def test_missing_evidence_does_not_reprioritize_line_exploration():
    families=['source_lines','other']
    assert trial_evidence.order(families,[])==families


def test_hidden_multiline_macro_reference_blocks_storage_rewrites():
    header='#define HIDDEN \\\n b=3\n'
    assert not repairs.variants(SOURCE,'f',header,arch='mips',rows=STACK,locations=locations(SOURCE))


def test_malformed_rows_do_not_change_trial_order():
    families=['source_lines','other']
    assert trial_evidence.order(families,[('i',None,'nop')])==families


def test_fresh_mapping_is_available_during_the_first_agent_proposal_pass(tmp_path,monkeypatch):
    from decompnet.app import agent_evidence, agent_tokens, local_experiments
    from tests.test_claims import _game
    net,_=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('worker');net.claim_row(agent,'game','f');fn=net._function('f','game')
    net._record(fn,agent,2,SOURCE,1)
    _,revision=agent_tokens.candidate(net,agent,'game','f')
    measured=dict(score=2,source=SOURCE,rows=STACK,repair_locations=locations(SOURCE))
    monkeypatch.setattr(agent_evidence,'preflight',lambda *args:measured)
    observed=[]
    def available(*args,**kwargs):
        observed.append(net._agent_outcomes[agent.id,fn['id']]['repair_locations'])
        return {repairs.FAMILIES[0]:[]}
    monkeypatch.setattr(local_experiments,'available',available)
    result=local_experiments.run(net,agent,'game','f',revision,[repairs.FAMILIES[0]],max_trials=1)
    assert 'No untried' in result
    assert observed==[locations(SOURCE)]


def test_stale_build_context_does_not_reuse_mapped_agent_repairs(tmp_path,monkeypatch):
    from decompnet.app import local_experiments
    from tests.test_claims import _game
    net,_=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('worker');fn=net._function('f','game')
    with net.store.tx() as db:db.execute('UPDATE projects SET config=?', ('{"compiler":{}}',))
    net._agent_outcomes={(agent.id,fn['id']):dict(source=SOURCE,rows=STACK,repair_locations=locations(SOURCE),input_scope='old-build')}
    seen=[]
    monkeypatch.setattr(repairs,'variants',lambda *args:seen.append(1) or [])
    pools=local_experiments._available(net,agent,'game',fn,SOURCE,'revision',[repairs.FAMILIES[0]])
    assert not seen and not pools[repairs.FAMILIES[0]]
