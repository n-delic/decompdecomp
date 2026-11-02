"""Allocator proposals remain bounded and preserve observable execution."""
import shutil
import subprocess

import pytest

from decompnet.app import allocation_evidence, draft_repairs, loop_weight_repairs, repair_ranking


def test_loop_scopes_keep_update_pairs_and_exclude_control_transfers():
    source = 'int f(void){int x; int y; x=1;y=2;++y;x+=y;return x+y;}'
    proposals = loop_weight_repairs.variants(source,'f')
    assert 0 < len(proposals) <= loop_weight_repairs.LIMIT
    assert any('do {do {do {' in text for _,text in proposals)
    assert any('++y;x+=y;' in text for _,text in proposals)
    assert all('return x+y;' in text for _,text in proposals)
    assert not loop_weight_repairs.variants(source.replace('int x;', 'volatile int x;'),'f')
    assert not loop_weight_repairs.variants(source,'f','#define x hidden')
    assert all(not loop_weight_repairs.variants(text,'f') for _,text in proposals)


def test_equal_copy_lifetime_does_not_cross_calls_or_narrowing():
    source = 'int f(void){int x;int y;y=3;x=y;++y;return x+y;}'
    assert any(family=='equal_value_increment' for family,_ in loop_weight_repairs.variants(source,'f'))
    assert not any(family=='equal_value_increment' for family,_ in loop_weight_repairs.variants(source.replace('int x;', 'short x;'),'f'))
    assert not any(family=='equal_value_increment' for family,_ in loop_weight_repairs.variants(source.replace('x=y;', 'x=y;work();'),'f'))


def test_register_only_guidance_and_family_integration():
    rows = [('r','addu $s1, $s2, $s3','addu $s2, $s1, $s3'),('r','move $s1, $a0','move $s3, $a0')]
    assert allocation_evidence.diagnose(rows)['register_only']
    assert not allocation_evidence.diagnose([('r','addiu $s1, $s2, 1','addiu $s1, $s2, 2')])['register_only']
    assert '2 register-only rows' in allocation_evidence.text(rows)
    assert repair_ranking.order(['declaration_order',*loop_weight_repairs.FAMILIES],rows)[0]=='equal_value_increment'
    assert set(loop_weight_repairs.FAMILIES) <= set(draft_repairs.AGENT_FAMILIES)


def test_synthetic_candidates_preserve_execution(tmp_path):
    compiler = shutil.which('cc')
    if not compiler:
        pytest.skip('Host C compiler unavailable')
    source = 'int f(void){int x;int y;y=3;x=y;++y;x+=y;return x+y;}\nint main(void){return f()!=11;}'
    for index,text in enumerate([source]+[text for _,text in loop_weight_repairs.variants(source,'f')]):
        path = tmp_path/f'case{index}.c'
        path.write_text(text)
        binary = tmp_path/f'case{index}'
        subprocess.run([compiler,'-O2',str(path),'-o',str(binary)],check=True,timeout=20,capture_output=True)
        subprocess.run([str(binary)],check=True,timeout=5)
