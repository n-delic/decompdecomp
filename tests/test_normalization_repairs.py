"""Synthetic C only; private retail checkpoints are replayed outside this repository."""
import shutil
import subprocess

import pytest

from decompnet.app import draft_repairs, iterative_repairs, repair_ranking
from decompnet.app import normalization_repairs as repairs


def setter_source():
    return ('void put(void *, short, int);\nint other(void){return 7;}\n'
            'int work(void){int h; short raw; void *p; raw=-9;p=0;'
            'h=(short)raw;put(p,h,190);return h;}')


def test_call_return_preserves_prototype_and_other_units():
    source = setter_source()
    result = repairs.call_returns(source, 'work')
    assert len(result) == 1
    assert 'h=__repair_call_return(p,raw)' in result[0]
    assert 'void put(void *, short, int);' in result[0]
    assert 'int other(void){return 7;}' in result[0]
    assert result[0].index('void put') < result[0].index('static inline') < result[0].index('int work')
    assert not repairs.call_returns(source.replace('put(p,h,190)', 'put(next(),h,190)'), 'work')
    assert not repairs.call_returns(source.replace('h=(short)raw;', 'if(raw) h=(short)raw;'), 'work')
    assert not repairs.call_returns(source.replace('short raw;', 'volatile short raw;'), 'work')
    assert not repairs.call_returns(source.replace('void *p;', 'void * volatile p;'), 'work')
    assert not repairs.call_returns(source.replace('void put', 'int put'), 'work')


def split_source():
    return ('int work(void){int x;int input;x=0;input=30;x=input*8;'
            'if(input){x=input;}return (short)x;}')


def test_split_initializes_the_skipped_loop_path_and_keeps_earlier_role():
    source = split_source()
    candidates = repairs.split_lifetimes(source, 'work')
    assert len(candidates) == 1
    result = candidates[0]
    assert 'x=0;__repair_saved_narrow=(short)x;' in result
    assert 'x=input*8;__repair_saved_narrow=(short)x;' in result
    assert 'if(input){__repair_saved_narrow=input;}' in result
    assert 'return (short)__repair_saved_narrow;' in result
    assert not repairs.split_lifetimes(source.replace('(short)x','x'), 'work')
    assert repairs.split_lifetimes(source.replace('x=0;', 'if(input)x=0;'), 'work')
    assert not repairs.split_lifetimes(source.replace('return (short)x;', 'use(&x);return (short)x;'), 'work')
    assert not repairs.split_lifetimes(source.replace('return (short)x;', 'return (short)x+x;'), 'work')


def frame_rows(gap=48):
    return [('s', 'addiu $sp, $sp, -0x198', f'addiu $sp, $sp, -{hex(0x198-gap)}'),
            (' ', 'sw $v0, 0xd4($sp)', 'sw $v0, 0xd4($sp)'),
            ('s', 'lw $t8, 0x164($sp)', f'lw $t8, {hex(0x164-gap)}($sp)')]


def frame_source():
    return 'int work(void){' + ''.join(f'int x{i};' for i in range(8)) + ''.join(
        f'x{i}=read_value();x{i}&=1023;' for i in range(8))+'return x0;}'


def test_partial_gap_bundles_are_bounded_mask_preserving_and_evidence_gated():
    source = frame_source()
    result = repairs.frame_variants(source, 'work', frame_rows())
    assert 0 < len(result) <= 8
    assert result[0].count('=__repair_spill_normalize(') == 6
    assert all(c.count('&=1023;') == 8 for c in result)
    assert not repairs.frame_variants(source, 'work', [])
    assert not repairs.frame_variants(source.replace('1023', '65535'), 'work', frame_rows())
    assert not repairs.frame_variants(source, 'work', frame_rows()+[('>', '', 'nop')])
    assert repairs.frame_gap(frame_rows()) == 48
    assert repair_ranking.order(['constant_local','spill_normalization'],frame_rows())[0] == 'spill_normalization'
    assert iterative_repairs.stack_distance(frame_rows(8)) < iterative_repairs.stack_distance(frame_rows(48))


def test_shared_pipeline_exposes_lifetime_repairs():
    labels = {}
    draft_repairs.variants(setter_source(), 'work', 'mips', labels=labels, limit=None)
    assert 'call_return_normalization' in labels.values()
    assert set(repairs.FAMILIES) <= set(draft_repairs.AGENT_FAMILIES)


def test_compiled_helper_keeps_negative_and_positive_values(tmp_path):
    cc = shutil.which('cc')
    if not cc:
        pytest.skip('Host compiler unavailable')
    source = setter_source().replace('raw=-9;', 'raw=input;').replace('int work(void)', 'int work(int input)')
    candidates = repairs.call_returns(source, 'work')
    assert candidates
    support = ('int seen;void put(void *p,short h,int c){seen=h;}\n'
               'int main(void){int i;for(i=-32768;i<=32767;i+=127){'
               'if(work(i)!=i||seen!=i)return 1;}return 0;}')
    path = tmp_path/'check.c'
    path.write_text(candidates[0]+support)
    binary = tmp_path/'check'
    subprocess.run([cc,'-std=c99','-O2',str(path),'-o',str(binary)],check=True,capture_output=True)
    subprocess.run([str(binary)],check=True,timeout=10)


def test_header_prototypes_and_mixed_register_address_advice():
    source = setter_source().replace('void put(void *, short, int);\n','')
    assert repairs.call_returns(source,'work','void put(void *, short, int);')
    rows = [('r','sll $s0, $s3, 16','sll $v0, $s3, 16'),
            ('i','lui $v0, %hi(external)','lui $v0, %hi(other)')]
    assert repair_ranking.order(['local_width','call_return_normalization'],rows)[0] == 'call_return_normalization'
    assert not repairs.frame_gap(frame_rows()+[('s','lw $t9, 0x16c($sp)','lw $t9, 0x15c($sp)')])


def test_deep_frontier_prefers_closer_offsets_on_a_numeric_plateau():
    from decompnet.app.search_frontier import Frontier
    frontier = Frontier()
    frontier.offer('far',141,frame_rows(48))
    frontier.offer('closer',141,frame_rows(8))
    assert frontier.choose(0,'fallback') == 'closer'


def test_split_keeps_the_value_when_the_later_phase_is_skipped(tmp_path):
    cc = shutil.which('cc')
    if not cc:
        pytest.skip('Host compiler unavailable')
    source = ('int work(int input){int x;int value;x=0;value=input;'
              'x=value*8;if(input>0){x=value;}return (short)x;}')
    candidate = repairs.split_lifetimes(source,'work')[0]
    path = tmp_path/'split.c'
    path.write_text(candidate+'\nint main(void){int i;for(i=-100;i<100;i++){'
                    'if(work(i)!=(short)(i>0?i:i*8))return 1;}return 0;}')
    binary = tmp_path/'split'
    subprocess.run([cc,'-std=c99','-O2',str(path),'-o',str(binary)],check=True,capture_output=True)
    subprocess.run([str(binary)],check=True,timeout=10)


def test_frame_helper_preserves_masked_negative_and_large_values(tmp_path):
    cc = shutil.which('cc')
    if not cc:
        pytest.skip('Host compiler unavailable')
    source = 'int read_value(void);\n'+frame_source()
    candidate = next(c for c in repairs.frame_variants(source,'work',frame_rows())
                     if 'x0=__repair_spill_normalize(x0);' in c)
    support = ('int value;int read_value(void){return value;}\n'
               'int main(void){int a[]={-2147483647,-65537,-32769,-1,0,32768,65537,2147483647};'
               'int i;for(i=0;i<8;i++){value=a[i];if(work()!=(value&1023))return 1;}return 0;}')
    path = tmp_path/'mask.c'
    path.write_text(candidate+support)
    binary = tmp_path/'mask'
    subprocess.run([cc,'-std=c99','-O2',str(path),'-o',str(binary)],check=True,capture_output=True)
    subprocess.run([str(binary)],check=True,timeout=10)


def test_targeted_phase_prioritizes_bundles_and_excludes_other_architectures():
    source = frame_source()
    candidates = iterative_repairs.targeted_variants(source,{'rows':frame_rows()},
                                                     {'name':'work','asm':''},'mips',[])
    assert '__repair_spill_normalize' in candidates[0]
    choices, labels = [], {}
    repairs.extend_frame(choices,labels,source,'work','ppc',frame_rows())
    assert choices == []


def test_local_function_pointer_shadow_is_not_extracted_to_global_helper():
    source = setter_source().replace('int h;', 'void (*put)(void *,short,int);int h;')
    assert not repairs.call_returns(source,'work')
