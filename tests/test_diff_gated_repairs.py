import pytest

from decompnet.app.diff_gated_repairs import proposals

KMC = 'gcc-2.7.2-kmc'
EGCS = 'gcc-egcs-2.91.66'


def case_rows(*ops):
    return [(' ', '', '')] + [('<', op, '') for op in ops]


def assembly(*ops):
    return '\n'.join(op if op.endswith(':') else
                     f'/* {4*i:06X} {0x80001000+4*i:08X} 00000000 */ {op}'
                     for i,op in enumerate(ops))


def test_jump_labels_use_linked_case_permutation_without_an_instruction_diff():
    source = 'int f(int k) { switch (k) { case 2: return 11; case 7: return 22; default: return 0; } }'
    rows = [{'case':2,'table':100,'target':202,'candidate':201},
            {'case':7,'table':100,'target':201,'candidate':202}]
    assert proposals(source,'f','','mips',KMC,[],'',rows) == [
        ('linked_switch_labels', source.replace('case 2','case 7',1).replace('case 7: return 22','case 2: return 22'))]
    assert not proposals(source,'f','','mips',KMC,[],'')
    assert not proposals(source,'f','','arm',KMC,[],'',rows)
    assert not proposals(source,'f','','mips',KMC,[],'',[rows[0],dict(rows[1],table=200)])
    assert not proposals(source,'f','','mips',KMC,[],'',[rows[0],dict(rows[1],target=203)])
    assert not proposals(source.replace('case 7','default',1),'f','','mips',KMC,[],'',rows)


SENTINEL_ASM = assembly('ori $t1, $zero, 0xffff','lhu $t0, 0($a0)','beq $t0, $t1, .Lend','nop',
                         'ori $t2, $zero, 0xffff','.Lloop:','addiu $a0, $a0, 2','lhu $t0, 0($a0)',
                         'bne $t0, $t2, .Lloop','nop','.Lend:','jr $ra','nop')

CASES = [
    ('staged_field_reload', KMC,
     'struct V { float val; float copy; int flag; }; void f(struct V *p) { p->val = 2; p->flag = 1; p->copy = p->val; }',
     case_rows('swc1 $f0, 0($a0)', 'lwc1 $f1, 0($a0)', 'sw $v0, 8($a0)', 'swc1 $f1, 4($a0)'),
     '', 'repair_reload = *(volatile float *)&(p->val);'),
    ('adjacent_stack_redistribution', KMC,
     'int f(void) { float pad[3]; float values[2]; fill(values); return values[0]; }',
     [(' ', 'addiu $sp, $sp, -0x30', 'addiu $sp, $sp, -0x30'),
      ('s','lwc1 $f0, 0x20($sp)','lwc1 $f0, 0x18($sp)')],
     '', 'float pad[2]; float values[3];'),
    ('staged_table_entry_read', KMC,
     'void f(void) { int **p; int **q; p = A; q = B; if (*p != 0) use(q); }',
     case_rows('lw $v0, 0($t0)', 'lui $t1, 0x8000', 'beqz $v0, 0x18'),
     '', 'p = A; repair_first_entry = *p; q = B; if (repair_first_entry != 0)'),
    ('sentinel_test_loop', KMC,
     'void f(void) { u16 v; u16 *p; p = A; v = *p; if (v != 0xffff) { do { use(v); p++; v = *p; } while (v != 0xffff); } }',
     case_rows('beq $t0, $t1, 0x18', 'bne $t0, $t2, -0x8'),
     SENTINEL_ASM, 'while ((v = *p) != 0xffff) { use(v); p++; '),
    ('guard_delay_slot_initializer', KMC,
     'void f(int n) { int i; if (n) { i = 0; while (i < n) { use(i); i++; } } }',
     case_rows('beqz $a0, 0x20', 'move $v0, $zero'),
     '', 'i = 0; if (n)'),
    ('staged_squared_terms', EGCS,
     'float f(void) { float x; float y; x = a-b; y = c-d; return x*x + y*y; }',
     [('r','mul.s $f0, $f2, $f2','mul.s $f4, $f2, $f2')],
     '', 'x = x * x; return x + y*y;'),
    ('double_unsigned_conversion', KMC,
     'u32 f(double x) { u32 y; if (x >= 2147483648.0) { y = ((s32)(x - 2147483648.0) | 0x80000000U); } else { y = (s32)x; } return y; }',
     case_rows('c.le.d $f0, $f12', 'trunc.w.d $f2, $f12', 'lui $v0, 0x8000', 'or $v0, $t0, $v0'),
     '', 'y = (u32)x;'),
    ('separated_unsigned_divisions', KMC,
     'u32 f(void) { u32 n; u32 q; n = input(); q = n / 192000; return q; }',
     case_rows('addiu $t1, $zero, 1000', 'divu $t0, $t1', 'mflo $t0',
               'ori $t1, $zero, 192', 'divu $t0, $t1'),
     '', 'unsigned repair_quotient = n / 1000U; q = repair_quotient / 192U;'),
    ('in_place_minmax', KMC,
     'void f(void) { int a; int b; int x; int y; x = get(); y = get(); a = x - u; b = y - v; out = (a < b) ? b : a; }',
     [('r','subu $v0, $a0, $a1','subu $v1, $a0, $a1')],
     '', 'x -= u; y -= v; if (x < y) { out = y; } else { out = x; }'),
]


@pytest.mark.parametrize('family,toolchain,source,rows,asm,expected', CASES)
def test_source_repairs_require_the_measured_diff(family, toolchain, source, rows, asm, expected):
    generated = proposals(source,'f','', 'mips',toolchain,rows,asm)
    assert any(label == family and expected in candidate for label,candidate in generated), generated
    assert len([candidate for label,candidate in generated if label == family]) <= 4
    assert not proposals(source, 'f','', 'mips',toolchain,[(' ', a,a) for _,a,_ in rows],asm)
    assert not proposals(source, 'f','', 'arm',toolchain,rows,asm)
    assert not proposals(source, 'f','', 'mips','ido-5.3',rows,asm)


def test_relocated_record_base_uses_project_symbols():
    source = """extern s8 low;
extern s32 high;
s32 f(void) {
    s8 *base;
    s32 *record;
    s32 ok;
    base = &low;
    ok = 1;
    (*base) = 0;
    record = (s32 *)(base + 0x24);
    *record = 0;
    return ok;
}
"""
    rows = case_rows('lui $v1, %hi(high)', 'addiu $v1, $v1, %lo(high)', 'lbu $v0, -0x24($v1)')
    results = proposals(source, 'f', '', 'mips', KMC, rows, '', resolve={'low': 0x100, 'high': 0x124}.get)
    assert ('measured_global_rebase', source.replace('(s32 *)(base + 0x24)',
                                                     '(s32 *)((((u32)&high) + ok) - ok)')) in results


def test_cursor_loop_tail_routes_the_measured_branch_likely_edge():
    source = """void f(Node *node) {
    Node *cursor;
    Node *next;
    cursor = head;
    if (cursor != NULL) {
        for (;;) {
            next = cursor->right;
            if (next == NULL) {
                cursor->right = node;
                break;
            }
            if (((cursor < node) & (node < next)) != 1) {
                cursor = next;
                continue;
            }
            cursor->right = node;
            next->left = node;
            break;
        }
    }
}
"""
    rows = case_rows('and $v0, $v0, $v1', 'beql $v0, $zero, 0xc4', 'move $a0, $a1')
    [repaired] = [candidate for family, candidate in proposals(source, 'f', '', 'mips', KMC, rows, '')
                  if family == 'measured_cursor_loop_tail']
    assert '} while (cursor != NULL);' in repaired
    assert 'continue;' not in repaired
