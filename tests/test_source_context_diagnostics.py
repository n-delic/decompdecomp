from decompnet.app.typed_draft_repairs import variants

HEADER='''typedef struct { int mode; short status; unsigned char pad[2]; float alpha; } App;
#define W(p,o) (*(int *) ((u8 *) p + o))
#define H(p,o) (*(short *) ((u8 *) p + o))
#define F(p,o) (*(float *) ((u8 *) p + o))
'''


def test_typed_fields_use_actual_layout_and_macro_body():
    source='void f(App *p) { if (W(p,0)) { H(p,4)=32; F(p,8)=0; } }'
    pairs=variants(source,'f',HEADER)
    assert len(pairs)==1
    assert 'p->mode' in pairs[0][1] and 'p->status=32' in pairs[0][1] and 'p->alpha=0' in pairs[0][1]
    assert pairs[0][2]==HEADER
    assert not variants(source,'f',HEADER.replace('int mode','Unknown mode'))
    assert not variants(source,'f',HEADER.replace('((u8 *) p + o)','((u8 *) p - o)'))
    assert not variants(source,'f','#pragma pack(1)\n'+HEADER)
    assert not variants(source,'f',HEADER.replace('int mode','volatile int mode'))


def test_fields_reject_wrong_width_and_untyped_pointer():
    assert not variants('void f(App *p) { H(p,0)=1; }','f',HEADER)
    assert not variants('void f(void *p) { W(p,0)=1; }','f',HEADER)


def test_shadowed_pointer_is_not_assumed_to_have_parameter_layout():
    assert not variants('void f(App *p) { { void *p; W(p,0)=1; } }','f',HEADER)
