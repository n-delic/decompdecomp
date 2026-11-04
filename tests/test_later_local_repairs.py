"""Homemade scalar phases and optional private, ROM-backed allocation replay."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from decompnet.app import (
    allocation_search,
    draft_repairs,
    integer_abi,
    later_local_repairs,
    repair_ranking,
    search_frontier,
)

HEADER = 'typedef long s32; typedef unsigned long u32; typedef short s16;'
SOURCE = '''int f(int a,int b) { int value; int later; int unused;
value=3; value += a+b; observe(value); later=9; observe(later); return value; }'''
# The phase must end before the later value starts; return the observed sum through a different local.
SOURCE = SOURCE.replace('int unused;','int answer;').replace('observe(value);','answer=value;').replace('return value;','return answer;')
ROWS = [('r','addu $s0, $t0, $v0','addu $s0, $v0, $t0')]


def proposals(source=SOURCE, header=''):
    return later_local_repairs.variants(source,'f',header,'mips')


def test_later_reuse_is_bounded_preserves_calls_and_separate_value():
    candidates = proposals()
    assert candidates and len(candidates)<=later_local_repairs.LIMIT
    candidate = candidates[0][1]
    assert 'later = __repair_subtotal + value;' in candidate
    assert 'answer=later;' in candidate and 'later=9; observe(later);' in candidate
    assert candidate.count('observe(')==SOURCE.count('observe(')
    assert 'later_local_reuse' in draft_repairs.AGENT_FAMILIES
    labels = {}
    assert candidate in draft_repairs.variants(SOURCE,'f','mips',labels=labels,limit=None)
    assert labels[candidate]=='later_local_reuse'
    lane = allocation_search.proposals(SOURCE,'f','mips',ROWS,[],{},header='')
    assert lane and lane[0][0]=='later_local_reuse'
    assert repair_ranking.order(['constant_local','later_local_reuse'],ROWS)[0]=='later_local_reuse'


@pytest.mark.parametrize('source',[
    SOURCE.replace('later=9;','if(a) later=9;'), # original later value is live on the other arm
    SOURCE.replace('value += a+b;','escape(&later); value += a+b;'),
    SOURCE.replace('int later;','volatile int later;'),
    SOURCE.replace('int later;','short later;'),
    SOURCE.replace('value=3;','if(a) value=3;'),
    SOURCE.replace('answer=value;','if(a) answer=value; else observe(value);'),
    SOURCE.replace('later=9;','if(a) goto skip; later=9; skip:;'),
    SOURCE.replace('value=3;', 'again: observe(later); value=3;').replace('later=9;','if(a) goto again; later=9;'),
    SOURCE.replace('value += a+b;', 'value += update()+b;'),
    SOURCE.replace('value += a+b;', 'if(a) value += a+b;'),
    SOURCE.replace('value += a+b;', 'if(a){value += a+b;}'),
    SOURCE.replace('int later;', 'int later; {int later; observe(later);}'),
])
def test_unsafe_or_unknown_phases_are_not_reused(source):
    assert not proposals(source)


def test_unknown_macros_qualifiers_alias_widths_and_scope_are_rejected():
    assert not proposals(SOURCE,'#define observe(x) do { later++; } while(0)')
    assert not proposals(SOURCE,'#define hidden() observe(later)\n#define observe(x) hidden()')
    source = SOURCE.replace('int value; int later;','s32 value; s32 later;')
    assert proposals(source,HEADER)
    assert not proposals(source,HEADER.replace('long s32','short s32'))
    assert not proposals(source,HEADER+' typedef int *s32;')
    assert not later_local_repairs.variants(SOURCE,'f','','ppc64')
    assert proposals('volatile unsigned long long other();\n'+SOURCE)
    assert not proposals(SOURCE.replace('int later;','int *later;'))


def test_abi_resolution_never_uses_host_long_width():
    for arch in integer_abi.ARCHES:
        assert integer_abi.scalar('s32',HEADER,arch)==(32,True)
        assert integer_abi.scalar('u32',HEADER,arch)==(32,False)
        assert integer_abi.scalar('alias',HEADER+' typedef s32 alias;',arch)==(32,True)
    for context in [HEADER+'typedef short s32;', '#define s32 int', 'typedef volatile int s32;', 'typedef other s32;']:
        assert integer_abi.scalar('s32',context,'mips') is None
    assert integer_abi.scalar('long',HEADER,'x86_64') is None


def test_fixed_target_position_survives_worse_scheduling_score_and_frontier_pressure():
    baseline = ROWS+[(' ','move $a0, $s5','move $a0, $s5'),(' ','andi $s0, $v0, 0xffff','andi $s0, $v0, 0xffff')]
    alternative = [(' ',ROWS[0][1],ROWS[0][1]),('o',baseline[1][1],baseline[2][1]),('o',baseline[2][1],baseline[1][1])]
    frontier = search_frontier.Frontier()
    frontier.offer('numeric',115,baseline)
    frontier.offer('fixed-addition',170,alternative,arch='mips',baseline=baseline,baseline_score=115)
    for i in range(20):
        frontier.offer(str(i),116+i,ROWS+[('r','move $t1, $a0',f'move $s{i%8}, $a0')],arch='mips',baseline=baseline,baseline_score=115)
    assert len(frontier.nodes)<=search_frontier.WIDTH
    assert {'numeric','fixed-addition'}<={n['source'] for n in frontier.nodes}


def test_candidates_preserve_synthetic_execution(tmp_path):
    cc = shutil.which('cc')
    if not cc:pytest.skip('Needs a host C compiler for synthetic behavior checks.')
    prefix = 'static int seen; void observe(int x){seen+=x;}\n'
    for i,source in enumerate([SOURCE]+[c for _,c in proposals()]):
        c,binary = tmp_path/f'{i}.c',tmp_path/f'{i}'
        c.write_text(prefix+source+'\nint main(void){int r=f(5,7);return !(r==15 && seen==9);}')
        subprocess.run([cc,'-std=c89','-Werror',str(c),'-o',str(binary)],check=True,capture_output=True)
        subprocess.run([str(binary)],check=True,capture_output=True)


def test_private_frozen_allocation_replay():
    """Opt-in only: keep the game's frozen source, context and ROM outside this repository."""
    case_path = os.environ.get('DECOMPNET_LIFETIME_REPLAY')
    if not case_path:pytest.skip('Set DECOMPNET_LIFETIME_REPLAY to a private frozen-case JSON file.')
    import hashlib
    import time

    from decompnet.app import iterative_repairs
    from decompnet.app.linked_repair_benchmark import evaluator
    case = json.loads(Path(case_path).read_text())
    source,header = Path(case['before']).read_text(),Path(case['header']).read_text()
    assert hashlib.sha256(source.encode()).hexdigest()==case['source_sha256']
    assert hashlib.sha256(header.encode()).hexdigest()==case['header_sha256']
    evaluate,scope,arch,asm,_ = evaluator(Path(case['project']),case['function'],header)
    assert scope==case['scope']
    baseline = evaluate(source)
    assert baseline.get('score') is not None and not baseline['matched']
    started = time.monotonic()
    pool = iterative_repairs.targeted_variants(source,baseline,{'name':case['function'],'asm':asm},
        arch,scope['compiler']['flags'],header,tagged=True,limit=20)
    assert len(pool)<=20 and pool[0][0]=='later_local_reuse'
    for _,candidate in pool:
        assert time.monotonic()-started<120
        result = evaluate(candidate)
        if result.get('matched'):
            assert result['score']==0
            break
    else:
        pytest.fail('Autopilot did not recover the frozen exact linked output within 20 candidates.')


def test_identity_family_accepts_abi_equivalent_long_aliases():
    from decompnet.app import identity_lifetime_repairs
    from tests.test_register_cycle_search import SOURCE as IDENTITY_SOURCE
    assert identity_lifetime_repairs.variants(IDENTITY_SOURCE,'f',HEADER,'mips')
    assert not identity_lifetime_repairs.variants(IDENTITY_SOURCE,'f',HEADER.replace('long u32','long long u32'),'mips')


def test_iterative_beam_retains_repaired_position_without_spending_extra_budget():
    from decompnet.app import iterative_repairs
    baseline = ROWS+[(' ','move $a0, $s5','move $a0, $s5')]
    fixed = [(' ',ROWS[0][1],ROWS[0][1]),('o',baseline[1][1],'move $a1, $s5')]
    tested = []
    def evaluate(source):
        tested.append(source)
        return {'score':170 if source=='fixed' else 115,'matched':False,
                'rows':fixed if source=='fixed' else baseline}
    def generate(source,report):
        return [('later_local_reuse','fixed')] if source=='start' else [('followup','finish')]
    state = dict(pending=[dict(source='start',depth=0,ceiling=115)],next=[],seen=['start'])
    assert not iterative_repairs.advance(state,evaluate,generate,lambda:len(tested)>=3,arch='mips')
    assert tested==['start','fixed','finish']


def test_plain_sum_reuse_and_arithmetic_contracts():
    plain = SOURCE.replace('value += a+b;','value = a+b;')
    assert proposals(plain) and '__repair_subtotal' not in proposals(plain)[0][1]
    assert not proposals(SOURCE.replace('int a,int b','long long a,int b'))
    assert not proposals(SOURCE.replace('int a,int b','int *a,int b'))
    unsigned = SOURCE.replace('int a,int b','unsigned int a,int b')
    assert 'unsigned int __repair_subtotal;' in proposals(unsigned)[0][1]
    shadowed = SOURCE.replace('answer=value;','{Alias value;value=7;answer=value;}')
    assert not proposals(shadowed)


def test_unknown_display_rows_cannot_claim_a_repaired_target():
    for rows in (None,[],['placeholder'],[(None,'target','candidate')]):
        assert search_frontier.mismatch_positions(rows)==set()


def test_shared_draft_generator_reaches_each_supported_architecture():
    for arch in integer_abi.ARCHES:
        labels = {}
        candidates = draft_repairs.variants(SOURCE,'f',arch,labels=labels,limit=None)
        assert any(labels[c]=='later_local_reuse' for c in candidates)
