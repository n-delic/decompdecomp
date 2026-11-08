"""Homemade lifetimes, arithmetic execution and measured-residual search regressions."""


import hashlib
import shutil
import subprocess

import pytest

from decompnet.app import integer_repair_support, later_local_repairs, lifetime_donor_ranking, local_flow
from decompnet.app.allocator_call_guards import dominators
from decompnet.app.later_local_phases import region

SOURCE = '''int f(int a,int b) { int value; int later; int answer;
value=3; value += a+b; answer=value; later=9; observe(later); return answer; }'''


BEFORE = [(' ','addiu $sp, $sp, -64','addiu $sp, $sp, -64'),
          ('r','addu $s0, $t0, $v0','addu $s0, $v0, $t0'),
          (' ','move $a0, $s5','move $a0, $s5'),(' ','andi $s0, $v0, 65535','andi $s0, $v0, 65535')]


AFTER = [BEFORE[0],(' ',BEFORE[1][1],BEFORE[1][1]),
         ('o',BEFORE[2][1],BEFORE[3][1]),('o',BEFORE[3][1],BEFORE[2][1])]


def variants(source=SOURCE,header='',evidence=None):
    return later_local_repairs.variants(source,'f',header,'mips',evidence)


@pytest.mark.parametrize('update', ['value -= a-b;', 'value &= a|b;', 'value |= a&b;', 'value ^= a^b;',
                                  'value = a-b;', 'value = a&b;', 'value = a|b;', 'value = a^b;',
                                  'value &= 0xFFFFU;'])
def test_other_integer_operations_keep_direction_and_evaluation_count(update):
    candidates=variants(SOURCE.replace('value += a+b;',update))
    assert candidates and all(family=='later_operation_reuse' for family,_ in candidates)
    if '-=' in update:
        assert 'later = value - __repair_subtotal;' in candidates[0][1]
        assert '__repair_subtotal = a - b;' in candidates[0][1]


def test_unsigned_literal_shifts_reject_signed_dynamic_or_wide_contracts():
    unsigned=SOURCE.replace('int value; int later;','unsigned int value; unsigned int later;').replace('int a,int b','unsigned int a,int b')
    for shift in ('<<','>>'):
        assert variants(unsigned.replace('value += a+b;',f'value = a {shift} 3;'))
        for count in ('32','-1','b','3ULL'):
            assert not variants(unsigned.replace('value += a+b;',f'value = a {shift} {count};'))
    assert not variants(SOURCE.replace('value += a+b;','value = a << 3;'))
    assert not variants(SOURCE.replace('value += a+b;','value = a + 1099511627776;'))


def test_call_result_reuse_requires_explicit_same_width_return_and_keeps_one_call():
    source=SOURCE.replace('value += a+b;','value = get(a,b);')
    candidates=variants(source,'int get(int,int);')
    assert candidates and all(f=='later_call_reuse' and c.count('get(a,b)')==1 for f,c in candidates)
    for header in ('','long long get(int,int);','int *get(int,int);','int get(int,...);','int get(int,int); unsigned int get(int,int);'):
        assert not variants(source,header)
    assert not variants(source.replace('get(a,b)','get(a++,b)'),'int get(int,int);')
    assert not variants(source,'/* int get(int,int); */')


def test_dominated_branches_can_reuse_but_joins_and_live_loops_cannot():
    branch=SOURCE.replace('answer=value;','if(a){answer=value;}else{answer=value+1;}')
    candidates=variants(branch)
    assert candidates and candidates[0][0]=='branch_local_reuse'
    assert 'if(a){answer=later;}else{answer=later+1;}' in candidates[0][1]
    assert not variants(SOURCE.replace('value += a+b;','if(a){value += a+b;}'))
    assert not variants(branch.replace('later=9;','if(a)later=9;'))
    assert not variants(SOURCE.replace('answer=value;','while(a--){answer=value;}'))
    scoped=SOURCE.replace('value += a+b; answer=value;','if(a){value += a+b; answer=value;}else{answer=0;}')
    assert variants(scoped)


def test_donor_ranking_uses_unique_source_lines_and_invalidates_stale_or_ambiguous_evidence():
    source='''int f(int a,int b){int value;int first;int second;int answer;
value=3;
value += a+b;
answer=value;
first=8;
observe(first);
second=9;
observe(second);
return answer;}'''
    part=integer_repair_support.view(source,'f');flow=local_flow.Flow(part)
    index=next(i for i,n in enumerate(flow.nodes) if '+=' in part.clean[n.start:n.end])
    phase=region(flow,index,'value',dominators(flow))
    trace=dict(source_sha256=hashlib.sha256(source.encode()).hexdigest(),header_sha256=hashlib.sha256(b'').hexdigest(),values=[])
    evidence=dict(value_trace=trace,allocation=dict(values=[
        dict(source_lines=[5],calls_crossed=8,allocation_rank=0),dict(source_lines=[7],calls_crossed=0,allocation_rank=1)]))
    assert lifetime_donor_ranking.rank(flow,phase,['first','second'],evidence,source,'')==['second','first']
    ranked=variants(source,evidence=evidence)
    assert 'second = __repair_subtotal + value;' in ranked[0][1]
    trace['source_sha256']='stale'
    assert lifetime_donor_ranking.rank(flow,phase,['first','second'],evidence,source,'')==['first','second']
    trace['source_sha256']=hashlib.sha256(source.encode()).hexdigest()
    evidence['allocation']['truncated']=True
    assert lifetime_donor_ranking.rank(flow,phase,['first','second'],evidence,source,'')==['first','second']


def test_extended_candidates_preserve_homemade_execution(tmp_path):
    cc=shutil.which('cc')
    if not cc:pytest.skip('Needs host C compiler for synthetic execution.')
    sources=[SOURCE.replace('value += a+b;',update) for update in ('value -= a-b;','value ^= a^b;','value = a-b;')]
    sources += [SOURCE.replace('answer=value;','if(a&1){answer=value;}else{answer=value+1;}')]
    sources += [SOURCE.replace('value += a+b;','value = get(a,b);')]
    header='int get(int,int);'
    prefix='static int observed; void observe(int x){observed+=x;} int get(int a,int b){observed++;return a-b;}\n'
    for i,source in enumerate(sources):
        for j,(_,candidate) in enumerate(variants(source,header)):
            text=prefix+source.replace(' f(',' original(')+candidate.replace(' f(',' candidate(')
            text+='\nint main(void){int a,b,x,y,s;for(a=-5;a<6;a++)for(b=-5;b<6;b++){observed=0;x=original(a,b);s=observed;observed=0;y=candidate(a,b);if(x!=y||s!=observed)return 1;}return 0;}'
            path,binary=tmp_path/f'{i}-{j}.c',tmp_path/f'{i}-{j}'
            path.write_text(text)
            subprocess.run([cc,'-std=c89','-Werror',str(path),'-o',str(binary)],check=True,capture_output=True)
            subprocess.run([str(binary)],check=True,capture_output=True)
