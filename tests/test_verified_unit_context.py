from types import SimpleNamespace

from decompnet.app import verified_unit_context as context


def test_helpers_are_private_candidates_and_const_values_are_actual_definitions():
    source='int helper(int); extern const int limit; int f(int x){return helper(x)+limit;}'
    donor='static const int limit=12; static int helper(int x){return x+1;} int known(int x){return helper(x)+limit;}'
    result=context.apply(source,'f',[donor])
    assert len(result)==1
    text=result[0][1]
    assert 'static const int __unit_limit=12;' in text
    assert 'static int __unit_helper(int x)' in text
    assert 'return __unit_helper(x)+__unit_limit;' in text
    assert source.startswith('int helper')


def test_conflicts_addresses_nonstatic_and_preprocessor_dependencies_are_rejected():
    source='int f(int x){return helper(x);}'
    donor='static int helper(int x){return x+1;}'
    assert not context.apply(source,'f',[donor,donor.replace('+1','+2')])
    assert not context.apply('void *f(){return &helper;}','f',[donor])
    assert not context.apply(source,'f',[donor.replace('static ','')])


def test_unknown_units_and_stale_verified_inputs_do_not_supply_context(monkeypatch):
    fn=dict(unit='',vram=1,id=1,name='f')
    net=SimpleNamespace(is_rom=lambda p:True,fork_slug=lambda s:False)
    assert context.donors(net,dict(slug='p'),fn,'')==[]
    fn['unit']='code'
    net.cfg=lambda p:{'compiler':{}}
    net.store=SimpleNamespace(all=lambda *a:[dict(vram=2)])
    monkeypatch.setattr(context,'compiler_for',lambda *a:{})
    for proof in [None,dict(changed_inputs=['header'],source='x'),dict(changed_inputs=[],regression={'bad':1},source='x')]:
        monkeypatch.setattr(context.match_bundle,'status',lambda *a,proof=proof:proof)
        assert context.donors(net,dict(slug='p',id=1),fn,'')==[]


def test_installed_gcc_context_can_recover_a_small_inlined_value():
    import pytest

    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):pytest.skip('Pinned GCC not installed')
    cfg=compiler_config('.',{'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-G0','-mips3','-mgp32','-mfp32','-O2','-g3']}})
    donor='static const int limit=12; static inline int helper(int x){return x+1;}'
    source='int helper(int); extern const int limit; int f(int x){return helper(x)+limit;}'
    candidate=context.apply(source,'f',[donor])[0][1]
    target=compile_source(cfg,'',donor+'\nint f(int x){return helper(x)+limit;}')
    before=compile_source(cfg,'',source);after=compile_source(cfg,'',candidate)
    assert target.ok and before.ok and after.ok
    goal=extract_function(target.obj,'f').fingerprint()
    assert extract_function(before.obj,'f').fingerprint()!=goal
    assert extract_function(after.obj,'f').fingerprint()==goal
