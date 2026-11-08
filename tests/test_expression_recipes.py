from decompnet.app.value_range_repairs import bounds, variants


def test_range_proof_propagates_bounds_and_rejects_overflow():
    assert bounds('(x & 255) >> 1',{'x':(0,65535)})==(0,127)
    assert bounds('x + 1',{'x':(2147483647,2147483647)}) is None
    assert bounds('x >> 1',{'x':(-1,10)}) is None
    assert bounds('unknown()',{}) is None
    assert bounds('0u - 1',{}) is None


def test_range_repair_handles_multiple_assignments():
    proposals=variants('int f(){int n; n=1000; n=n>>1; return n;}','f')
    assert len(proposals)==1 and 'short n;' in proposals[0][1]
    assert not variants('int f(){int n; n=1000; n=99999; return n;}','f')


def test_range_repair_rejects_escapes_branches_unknown_values_and_macros():
    for body in ('n=other; return n;', 'n=1; use(&n); return n;', 'if(x) n=1; return n;', 'n=1; return sizeof(n);'):
        assert not variants('int f(){int n;'+body+'}','f')
    assert not variants('int f(){int n; n=1;return n;}','f','#define n other')


def test_range_analysis_uses_parameter_types_and_masked_values():
    proposals=variants('int f(int input){int n; n=(input & 65535)>>1; return n;}','f')
    assert len(proposals)==1
    assert not variants('int f(int input){int n; n=input >> 1; return n;}','f')


def test_pinned_compiler_preserves_output_for_proven_mask_narrowing(tmp_path):
    import io

    import pytest
    from elftools.elf.elffile import ELFFile

    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config
    if toolchains.installed('gcc-2.7.2-kmc') is None:
        pytest.skip('Needs pinned KMC compiler')
    config=compiler_config(tmp_path,{'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':[
        '-G0','-mips3','-mgp32','-mfp32','-O2','-fno-strength-reduce','-msoft-float']}})
    source='int f(int input){int n; n=(input & 65535)>>1; return n;}'
    outputs=[]
    for candidate in (source,variants(source,'f')[0][1]):
        built=compile_source(config,'',candidate)
        assert built.ok,built.log
        outputs.append(ELFFile(io.BytesIO(built.obj)).get_section_by_name('.text').data())
    assert outputs[0]==outputs[1]
