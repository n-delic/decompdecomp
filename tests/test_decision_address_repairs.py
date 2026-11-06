"""Synthetic selector/decision regressions; private game captures never belong in tests."""
from decompnet.app import decision_address_repairs as repairs
from decompnet.app import draft_repairs
from decompnet.diff_summary import advice, text

HEADER='typedef signed short s16; typedef int s32; typedef unsigned char u8;'
SOURCE='''
typedef struct { u8 flags; u8 pad[39]; } Entry;
typedef struct { s32 kind; s16 height; u8 select[4]; Entry items[1]; } State;
extern State *global;
void emit(int); int enabled;
void run(void) {
    State *state=global;
    s16 px, py;
    int height;
#define POSITION (height + 2)
    height=state->height-8;
    py=height-29;
    if (enabled) {
        if (state->items[state->select[1]].flags & 1) {emit(POSITION);}
    }
    state=global;
    if (state->items[state->select[1]].flags & 1) {emit(py);}
    state=global;
    if (state->items[state->select[1]].flags & 4) {emit(py+1);}
    state=global;
    {
        int offset;
        offset=(state->kind == 3)?14:0;
        if (state->kind == 3) {emit(offset);} else {emit(0);}
    }
}
'''


def test_bundle_preserves_macros_and_reuses_existing_selector_reads():
    choices=repairs.variants(SOURCE,'run','mips',HEADER)
    assert choices and len(choices)<=repairs.MAX_VARIANTS
    family,source=choices[0]
    assert family=='decision_address_bundle'
    assert '#define POSITION (height + 2)' in source
    assert 'switch (state->kind)' in source
    assert '(unsigned short)height' in source
    assert 'goto __repair_flag_join' in source
    assert source.count('volatile State')==2
    assert source.count('->select[1]')==SOURCE.count('->select[1]')
    assert source.count('emit(')==SOURCE.count('emit(')
    assert '__repair_stride_contract' in source


def test_shared_drafts_reach_the_dedicated_macro_preserving_lane():
    labels={};choices=draft_repairs.variants(SOURCE,'run','mips',labels=labels,header=HEADER)
    assert choices and labels[choices[0]]=='decision_address_bundle'
    assert all('#define POSITION (height + 2)' in s for s in choices)


def test_switch_rejects_floats_breaks_labels_and_control_macros():
    for source,header in [
        ('void f(){float x; if(x==3){emit(1);}else{emit(0);}}',HEADER),
        ('void f(){int x; while(x){if(x==3){break;}else{emit(0);}}}',HEADER),
        ('void f(){int x;if(x==3){label:emit(1);}else{emit(0);}}',HEADER),
        ('void f(){int x;if(x==3){STOP;}else{emit(0);}}',HEADER+'\n#define STOP \\\n break;'),
    ]:
        assert not repairs.switches(source,'f',header)


def test_unknown_preprocessing_and_non_mips_are_left_alone():
    assert not repairs.variants(SOURCE,'run','arm',HEADER)
    assert not repairs.variants(SOURCE.replace('#define POSITION (height + 2)','#if SOMETHING\n#endif'),'run','mips',HEADER)


def test_shared_guard_rejects_volatile_global_pointer_reloads():
    assert repairs.shared_guard(SOURCE,'run',HEADER)
    assert repairs.shared_guard(SOURCE.replace('extern State *global;','extern State * volatile global;'),'run',HEADER) is None


def test_register_only_advice_is_compact_and_never_a_verdict():
    rows=[('r','lbu $v1, 4($a0)','lbu $v0, 4($a0)')]
    assert 'Only 1 rendered register' in advice(rows)
    assert 'compiler_experiments' in text(rows)
    assert not advice(rows+[('|','nop','addiu $v0, $zero, 1')])


def test_address_staging_requires_the_old_pointer_to_be_dead():
    source=SOURCE.replace('    state=global;\n    {\n        int offset;', '    {\n        int offset;')
    choices=repairs.addresses(source,'run',HEADER)
    assert choices
    assert all('if (state->items[state->select[1]].flags & 4)' in candidate for _,candidate in choices)
    escaped=source.replace('    State *state=global;','    State *state=global; touch(&state);')
    assert not repairs.addresses(escaped,'run',HEADER)


def test_bundle_preserves_synthetic_behavior_across_callback_updates(tmp_path):
    import shutil
    import subprocess

    import pytest
    cc=shutil.which('cc')
    if not cc:pytest.skip('Needs a host C compiler for the synthetic behavior check')
    candidate=repairs.variants(SOURCE,'run','mips',HEADER)[0][1]
    harness='''
#include <stdio.h>
State states[2]; State *global; unsigned int digest; int calls;
void emit(int value) {digest=digest*33u+(unsigned int)value; global=&states[(++calls)&1];}
int main(void) {
    int i;
    for(i=0;i<128;i++) {
        states[0].height=(short)(i*997-32768);states[1].height=(short)(32767-i*733);
        states[0].select[1]=0;states[1].select[1]=0;
        states[0].items[0].flags=i&7;states[1].items[0].flags=(i>>2)&7;
        states[0].kind=(i&1)?3:0;states[1].kind=(i&2)?3:1;
        enabled=i&1;global=&states[0];calls=0;run();
    }
    printf("%u\\n",digest);return 0;
}
'''
    outputs=[]
    for index,source in enumerate((SOURCE,candidate)):
        path=tmp_path/f'case{index}.c';binary=tmp_path/f'case{index}'
        path.write_text(HEADER+source+harness)
        subprocess.run([cc,'-std=gnu89','-O2',str(path),'-o',str(binary)],check=True,capture_output=True,timeout=20)
        outputs.append(subprocess.check_output([str(binary)],timeout=10))
    assert outputs[0]==outputs[1]
