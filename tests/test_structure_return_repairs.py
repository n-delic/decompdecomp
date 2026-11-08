"""Existing result records can use their compiler's real hidden-return ABI."""




from decompnet.app import sdk_shape_repairs as sdk

HEADER = 'typedef struct {float x,y,z;} Result;'


SOURCE = 'void f(Result *out, int input)\n{\n Result result;\n calculate(&result,input);\n *out=result;\n}\n'


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
