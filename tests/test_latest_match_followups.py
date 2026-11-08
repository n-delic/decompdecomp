"""Small source hypotheses and reference diagnostics never change the accepted-match rules."""


import pytest

from decompnet.app.match_followup_repairs import variants


def test_zero_test_can_use_an_unused_same_width_signed_local():
    source = 'void demo(void) { u32 n; u32 t; s32 r; n=get(); t=n; n--; if(t != 0) { use(n); } }'
    out = [s for f,s in variants(source,'demo') if f == 'word_zero_test_reuse']
    assert out and 'r=n; n--; if(r != 0)' in out[0]
    assert not variants(source.replace('s32 r;','s16 r;'),'demo')
    assert not variants(source.replace('s32 r;','volatile s32 r;'),'demo')


def test_pointer_call_staging_needs_real_prototype_and_unused_pointer():
    source = 'void *get(void); void demo(void *p) { void *m; use(p, get()); }'
    out = [s for f,s in variants(source,'demo') if f == 'pointer_call_staging']
    assert out and 'm = get();' in out[0] and 'use(p, m);' in out[0]
    assert not variants(source.replace('void *get(void);',''),'demo')
    assert not variants(source.replace('use(p, get())','use(next(), get())'),'demo')
    assert not variants(source.replace('void *m;','void *m; consume(m);'),'demo')


def test_generated_lifetimes_match_synthetic_compiler_controls(tmp_path):
    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('needs pinned compiler')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}})
    controls = [
        ('int demo(void) { u32 n; u32 t; s32 r; n=get(); t=n; n--; if(t != 0) { use(n); } return 1; }',
         'int demo(void) { u32 n; u32 t; s32 r; n=get(); r=n; n--; if(r != 0) { use(n); } return 1; }', 'word_zero_test_reuse'),
        ('void *get(void); void use(void*,void*); void demo(void *p) { void *m; use(p,get()); }',
         'void *get(void); void use(void*,void*); void demo(void *p) { void *m; m=get(); use(p,m); }', 'pointer_call_staging'),
    ]
    for before, after, family in controls:
        target = compile_source(cfg, 'typedef unsigned int u32; typedef int s32;', after)
        assert target.ok, target.log
        candidates = [compile_source(cfg, 'typedef unsigned int u32; typedef int s32;', text)
                      for f,text in variants(before,'demo') if f == family]
        assert candidates
        assert any(c.ok and extract_function(c.obj,'demo').code == extract_function(target.obj,'demo').code for c in candidates)


def test_duplicate_identical_pointer_prototype_in_header_is_not_a_conflict():
    source = 'void *get(void); void demo(void *p) { void *m; use(p, get()); }'
    assert any(f == 'pointer_call_staging' for f,_ in variants(source,'demo','void *get(void);'))
