"""A short draft pass proposes the record view of m2c's field accesses, however many other families apply."""
from decompnet.app import draft_repairs


def test_a_limited_draft_pass_keeps_the_record_view():
    source = ('extern s32 D_80001000;\nextern s32 D_80001004;\nvoid func_80002000(void *arg0) {\n    s32 i;\n    s32 temp;\n'
              '    for (i = 0; i < 4; i++) {\n        temp = D_80001000 + i;\n        D_80001004 = temp;\n    }\n'
              '    if (D_80001000 == 0) {\n        D_80001000 = 1;\n        D_80001004 = 2;\n    }\n'
              '    M2C_FIELD(arg0, s32 *, 0x10) = D_80001000;\n    M2C_FIELD(arg0, s32 *, 0x14) = D_80001004;\n}\n')
    children = draft_repairs.variants(source, 'func_80002000', 'mips', '', ['-O2'])
    assert any('Rec_func_80002000_arg0' in child for child in children)
