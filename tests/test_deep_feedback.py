"""The random/local feedback loop and recovery of drafts invalidated by their current header."""



import pytest

from decompnet import scoring
from decompnet.app import register_reuse_repairs


def test_register_candidates_target_constant_copy_and_last_scaled_use_without_touching_other_functions():
    constant='int f(int x){int missing;int k=13;if(x!=missing)x++;missing=k;return x;}'
    choices=register_reuse_repairs.variants(constant,'f')
    assert any(family=='constant_register_reuse' and 'if(x!=k)' in source for family,source in choices)
    copy='int f(int x){int a;int b;a=x+3;b=a;return b+7;}'
    assert any(family=='copy_register_reuse' and 'return a+7' in source for family,source in register_reuse_repairs.variants(copy,'f'))
    shift='int f(int x){int index;index=x;return index*4;}'
    assert any(family=='last_shift_reuse' and '(index *= 4)' in source for family,source in register_reuse_repairs.variants(shift,'f'))
    for source in [shift.replace('int index;','volatile int index;'),shift.replace('return index*4;','escape(&index);return index*4;')]:
        assert not register_reuse_repairs.variants(source,'f')
    assert not any(family=='last_shift_reuse' for family,_ in register_reuse_repairs.variants(shift.replace('int index;','short index;'),'f'))
    prefix='void untouched(void){int y;y=4;use(y);}\n'
    assert all(s.startswith(prefix) for _,s in register_reuse_repairs.variants(prefix+copy,'f'))


def test_constant_register_reuse_can_recover_a_real_exact_object(tmp_path):
    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('needs pinned gcc-2.7.2-kmc')
    cfg=compiler_config(tmp_path,{'compiler':{'toolchain':'gcc-2.7.2-kmc'}})
    source='int f(int x){int missing;int k=13;if(x!=missing)x++;missing=k;return x;}'
    target=compile_source(cfg,'','int f(int x){if(x!=13)x++;return x;}')
    before=compile_source(cfg,'',source)
    assert target.ok and before.ok
    expected=extract_function(target.obj,'f')
    assert scoring.diff(expected,extract_function(before.obj,'f')).score>0
    candidates=[s for family,s in register_reuse_repairs.variants(source,'f') if family=='constant_register_reuse']
    assert candidates
    results=[compile_source(cfg,'',s) for s in candidates]
    assert all(result.ok for result in results)
    assert any(scoring.diff(expected,extract_function(result.obj,'f')).score==0 for result in results)
