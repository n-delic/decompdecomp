"""Existing result records can use their compiler's real hidden-return ABI."""
import pytest

from decompnet.app import draft_repairs
from decompnet.app import sdk_shape_repairs as sdk
from decompnet.app import structure_return_repairs as repair

HEADER = 'typedef struct {float x,y,z;} Result;'
SOURCE = 'void f(Result *out, int input)\n{\n Result result;\n calculate(&result,input);\n *out=result;\n}\n'


def test_structure_return_uses_declared_record_and_removes_only_the_result_parameter():
    proposals = repair.variants(SOURCE, 'f', HEADER)
    assert len(proposals) == 1
    family, text = proposals[0]
    assert family == 'structure_return'
    assert 'Result f(int input)' in text and 'return result;' in text
    assert 'calculate(&result,input);' in text and '*out' not in text
    labels = {}
    assert text in draft_repairs.variants(SOURCE, 'f', 'mips', header=HEADER, labels=labels)
    assert labels[text] == 'structure_return'


@pytest.mark.parametrize('source,header', [
    (SOURCE.replace('calculate(', 'inspect(out); calculate('), HEADER),
    (SOURCE.replace('calculate(', 'if(input) return; calculate('), HEADER),
    (SOURCE, ''),
    (SOURCE, HEADER+' void f(Result*,int);'),
    (SOURCE, HEADER+'\n#define Result something_else'),
    (SOURCE.replace('Result result;', 'volatile Result result;'), HEADER),
])
def test_abi_changes_with_escaped_outputs_unknown_types_or_shared_prototypes_are_not_guessed(source, header):
    assert not repair.variants(source, 'f', header)


def test_sdk_word_setter_restores_typed_inline_locals_without_changing_other_functions():
    header = 'typedef volatile unsigned char vu8; typedef struct {void *data;int count;} Set;'
    source = 'void other(void) {}\nvoid f(Set *p,u32 n) { ((u32*)p->data)[0]=n; ((u32*)p->data)[1]=0; }'
    family, text = sdk.variants(source, 'f', header)[0]
    assert family == 'sdk_inline_word_setters'
    assert 'u32 *words;' in text and 'words=(u32*)object->data;' in text
    assert 'decomp_word_setter_0(p, n)' in text and 'decomp_word_setter_1(p, 0)' in text
    assert text.startswith('void other(void) {}\n')
    assert not sdk.variants(source, 'f', header.replace('void *data', 'volatile void *data'))
    assert not sdk.variants(source, 'f', '')
    assert not sdk.variants(source, 'f', header+'\n#define data replacement')


def test_real_period_compiler_recovers_the_structure_return_abi(tmp_path):
    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    if toolchains.installed('ido-5.3') is None:
        pytest.skip('needs already-installed ido-5.3')
    cfg=compiler_config(tmp_path,{'compiler':{'toolchain':'ido-5.3','flags':['-O3','-mips2','-G','0','-non_shared','-Xcpluscomm']}})
    header=HEADER+'\nvoid calculate(Result*,int);\n'
    target='Result f(int input)\n{\n Result result;\n calculate(&result,input);\n return result;\n}\n'
    proposal=repair.variants(SOURCE,'f',header)[0][1]
    outputs=[compile_source(cfg,header,s) for s in (target,proposal)]
    assert all(out.ok for out in outputs), [out.log for out in outputs]
    assert extract_function(outputs[0].obj,'f').fingerprint() == extract_function(outputs[1].obj,'f').fingerprint()
