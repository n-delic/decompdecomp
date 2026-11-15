"""Captured lifetime edits are bounded hypotheses; classifiers never promote cases."""
import pytest

from decompnet.app.captured_lifetimes import variants
from decompnet.repair_review import classify


def suggest(before, after, comparable):
    return classify({'evidence': {'before': {'source': before}, 'after': {'source': after}, 'comparable_settings': comparable}})


@pytest.mark.parametrize('before,family', [
    ('u32 demo(f64 x) { u32 v; v = (s32)x | (s32)0x80000000; return v; }', 'cast_bitwise_lifetime'),
    ('void demo(u8 *p, u32 y) { u32 x; x = y; *(u16 *)p = x >> 16; }', 'shift_store_lifetime'),
    ('float demo(int x, float t) { float v; v = (float)x * t; return v; }', 'float_cast_lifetime'),
])
def test_generated_candidates_preserve_neighbours_and_are_bounded(before, family):
    tail = '\nint neighbour(void) { return 9; }'
    candidates = variants(before+tail, 'demo')
    assert family in {f for f,_ in candidates}
    assert len(candidates) <= 8
    assert all(c.endswith(tail) for _,c in candidates)


def test_shift_requires_dead_unsigned_local_and_never_rewrites_volatile_or_loops():
    for source in (
        'void demo(u8 *p) { u32 x; x = 99; *(u16 *)p = x >> 16; use(x); }',
        'extern u32 x; void demo(u8 *p) { *(u16 *)p = x >> 16; }',
        'void demo(u8 *p) { s32 x; x = 99; *(u16 *)p = x >> 16; }',
        'void demo(volatile u8 *p) { u32 x; x = 99; *(u16 *)p = x >> 16; }',
        'void demo(u8 *p) { u32 x; x = 99; while(test()) { *(u16 *)p = x >> 16; } }',
    ):
        assert not any(f == 'shift_store_lifetime' for f,_ in variants(source, 'demo'))


def test_review_suggestion_separates_address_only_mixed_and_settings_changes():
    a = 'int f(){return D_80001000 + D_80002000;}'
    b = a.replace('D_80001000','D_80003000')
    assert suggest(a,b,True)['category'] == 'addresses'
    assert suggest(a,b.replace('+','-'),True)['status'] == 'reviewed'
    assert 'not established' in suggest(a,b,False)['note']
    assert suggest(a,a+'\n',True)['status'] == 'reviewed'


def test_lifetime_candidates_match_independent_compiler_controls(tmp_path):
    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config

    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('needs pinned compiler')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}})
    controls = [
        ('u32 demo(f64 x) { u32 v; v = (s32)x | (s32)0x80000000; return v; }',
         'u32 demo(f64 x) { u32 v; v = (s32)x; v = v | (s32)0x80000000; return v; }', 'cast_bitwise_lifetime'),
        ('void demo(u8 *p, u32 y) { u32 x; x = y; *(u16 *)p = x >> 16; }',
         'void demo(u8 *p, u32 y) { u32 x; x = y; x >>= 16; *(u16 *)p = x; }', 'shift_store_lifetime'),
        ('float demo(int x, float t) { float v; v = (float)x * t; return v; }',
         'float demo(int x, float t) { float v; v = (float)x; v = v * t; return v; }', 'float_cast_lifetime'),
    ]
    header = 'typedef unsigned int u32; typedef int s32; typedef double f64; typedef unsigned char u8; typedef unsigned short u16;'
    for before,after,family in controls:
        target = compile_source(cfg, header, after)
        assert target.ok, target.log
        code = extract_function(target.obj, 'demo').code
        compiled = [compile_source(cfg, header, text) for f,text in variants(before,'demo') if f == family]
        assert any(c.ok and extract_function(c.obj, 'demo').code == code for c in compiled)


def test_float_split_handles_parenthesized_loads_without_reordering_factors():
    source = 'float demo(void *p, float t) { float v; v = ((float)(*((int *)((char *)p + 4)))) * t; return v; }'
    out = [s for f,s in variants(source,'demo') if f == 'float_cast_lifetime']
    assert out and '* t;' in out[0] and 'v = v' in out[0]
    assert not any(f == 'float_cast_lifetime' for f,s in variants('float demo(float t) { float v; v = (float)read() * t; return v; }','demo'))
