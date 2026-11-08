"""Diagnostic preparation, continuation facts and repair evidence stay separate from accepted matches."""





from decompnet.app.observed_repairs import variants


def test_observed_repair_families_are_bounded_and_preserve_neighbours():
    snippets = [
        ('int demo(int x) { if (x) return -11; x++; return -11; }', 'shared_return_tail'),
        ('int demo(int x) { x = x | 4; return x; }', 'direct_compound_store'),
        ('int demo(int n) { int i; if(n != 0) { for(i=0;i<n;i++) { use(i); } } return 0; }', 'redundant_loop_guard'),
        ('extern void use(s32, s32); int demo(int x) { use(x, 0x3DCCCCCD); return 1; }', 'float_argument_bits'),
        ('s32 __d_to_ull(f64 x) { return (s32)x; }', 'runtime_scalar_signature'),
    ]
    for source, family in snippets:
        name = '__d_to_ull' if family == 'runtime_scalar_signature' else 'demo'
        tail = '\nint other(void) { return 9; }'
        results = variants(source+tail, name)
        assert family in {f for f, _ in results}, family
        assert len(results) <= 10
        assert all(text.endswith(tail) for _, text in results)
    assert not variants('int demo(volatile int *p) { *p = *p | 4; return *p; }', 'demo')


def test_new_candidates_compile_against_independent_compiler_controls(tmp_path):
    import pytest

    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config

    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('needs pinned GCC compiler')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain':'gcc-2.7.2-kmc', 'flags':['-O2']}})
    controls = [
        ('int demo(int x) { x = x | 4; return x; }', 'int demo(int x) { x |= 4; return x; }', 'direct_compound_store'),
        ('extern void use(int,int); int demo(int x) { use(x,0x3DCCCCCD); return 1; }',
         'extern void use(int,float); int demo(int x) { use(x,0.1f); return 1; }', 'float_argument_bits'),
        ('int demo(int x) { if(x) return -11; use(); return -11; }',
         'int demo(int x) { if(x) goto done; use(); done: return -11; }', 'shared_return_tail'),
    ]
    controls += [
        ('int demo(int n) { int i; if(n != 0) { for(i=0;i<n;i++) { use(i); } } return 0; }',
         'int demo(int n) { int i; for(i=0;i<n;i++) { use(i); } return 0; }', 'redundant_loop_guard'),
        ('s32 __d_to_ull(f64 x) { return (s32)x; }',
         'u64 __d_to_ull(f64 x) { return (u64)x; }', 'runtime_scalar_signature'),
    ]
    header = 'typedef float f32; typedef double f64; typedef int s32; typedef unsigned long long u64;'
    for before, after, family in controls:
        name = '__d_to_ull' if family == 'runtime_scalar_signature' else 'demo'
        target = compile_source(cfg, header, after)
        assert target.ok, target.log
        target_code = extract_function(target.obj, name).code
        proposed = [text for kind, text in variants(before, name) if kind == family]
        assert proposed
        compiled = [compile_source(cfg, header, text) for text in proposed]
        assert any(r.ok and extract_function(r.obj, name).code == target_code for r in compiled)
