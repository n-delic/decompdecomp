"""Target-block ranking, bounded composition and compact advisory evidence."""
from decompnet.app import iterative_repairs, region_repairs, repair_ablation
from tests.test_region_repairs import rows


def test_first_block_focus_preserves_target_position_and_ignores_later_noise():
    report = rows({17,49})
    focused = region_repairs.focus(report)
    assert len(focused)==48
    assert sum(r[0]!=' ' for r in focused)==1
    text = region_repairs.summary(report,'mips')
    assert 'block: 2 (instruction 18' in text
    assert 'Advisory only' in text
    assert region_repairs.focus([('r',1,'bad')])==[]
    assert region_repairs.summary([None],'mips')==''
    assert region_repairs.focus([('i',None,None)])==[]
    assert region_repairs.summary(rows(set()),'mips')==''


def test_region_and_global_families_interleave_without_dropping_any(monkeypatch):
    from decompnet.app import repair_ranking
    monkeypatch.setattr(repair_ranking,'order',lambda families,r:list(reversed(families)) if len(r)<len(rows({17,49})) else families)
    assert region_repairs.order(['a','b','c','d'],rows({1,40}))==['d','a','c','b']


def test_regional_followups_are_ranked_then_bounded_and_spend_existing_budget():
    state = dict(pending=[],next=[],seen=[])
    base = region_repairs.snapshot(rows({0,1,2}))
    for source,score,r in [('small',21,rows({0,1})),('recovered',24,rows({20}))]:
        regions = region_repairs.snapshot(r)
        state['next'].append(dict(source=source,score=score,ceiling=20,depth=1,report={'rows':r},
                                  regions=regions,region_gain=region_repairs.gain(base,regions),
                                  region_damage=region_repairs.damage(base,regions)))
    iterative_repairs.expand_wave(state,lambda s,r:[('a',s+'1'),('b',s+'2')],None)
    assert state['pending'][0]['source']=='recovered1'
    assert state['regional_bursts']==1
    # No allowance evades the caller's stop; pending children remain resumable.
    assert not iterative_repairs.advance(state,lambda s: (_ for _ in ()).throw(AssertionError()),lambda s,r:[],lambda:True)
    assert len(state['pending'])>0


def test_forward_pair_finds_canceling_edits_greedy_deletion_cannot_remove():
    before='int a=1; /* gap */ int b=1; /* gap */ int c=1; /* gap */ int d=1;'
    after=before.replace('=1','=2')
    def same_output(source):
        return ('a=2' in source and 'b=2' in source and
                (('c=2' in source)==('d=2' in source)))
    result=repair_ablation.minimize(before,after,same_output,trials=12)
    assert result['sufficient_recipes']==[[0,1]]
    assert result['reverted']==2
    assert 'c=1' in result['source'] and 'd=1' in result['source']
    assert len(result['trials'])<=12


def test_targeted_candidate_cap_does_not_let_one_family_starve_others(monkeypatch):
    from decompnet.app import draft_repairs, stack_padding_repairs
    def generate(source,name,arch,asm,flags,labels,**kwargs):
        candidates=[f'a{i}' for i in range(30)]+['b']
        labels.update({c:'first' if c!='b' else 'second' for c in candidates})
        return candidates
    monkeypatch.setattr(draft_repairs,'variants',generate)
    monkeypatch.setattr(stack_padding_repairs,'grow',lambda *args:[])
    for arch in ('ppc','x86','arm'):
        result=iterative_repairs.targeted_variants('source',{},dict(name='f',asm=''),arch,[],tagged=True)
        assert len(result)==20 and result[1]==('second','b')
