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


def test_measured_float_grid_routes_store_offset_and_product_trials():
    source = """void f(struct Input *p, float out[3][3]) {
    volatile double pad[1];
    float x = p->x;
    float y = p->y;
    float xy = x * y;
    float s = x + xy;
    float t = y + xy;
    float u = x - xy;
    float v = y - xy;
    out[0][0] = s;
    out[0][1] = t;
    out[1][0] = u;
    out[0][2] = v;
    out[1][1] = x;
    out[1][2] = y;
    out[2][0] = xy;
    out[2][1] = x;
    out[2][2] = xy;
}
"""
    rows = [('i', 'swc1 $f4, 8($a1)', 'swc1 $f4, 0xc($a1)')]
    candidates = [candidate for family, candidate in proposals(source, 'f', '', 'mips', KMC, rows, '')
                  if family == 'measured_float_grid_stores']
    assert len(candidates) == 2
    assert 'out[0][2] = (y - xy);\n    out[1][0] = (x - xy);' in candidates[0]
    assert 'float xy = y * x;' in candidates[1]


def test_kmc_shares_measured_byte_store_immediate_with_division():
    source = """void f(void *p, int divisor) {
    (*(s8 *)((char *)p + 4)) = 0xff;
    (*(s8 *)((char *)p + 5)) = 0xff;
    sink(255 / divisor);
}
"""
    rows = [
        ('i', 'addiu $s4, $zero, 0xff', 'addiu $s5, $zero, -1'),
        ('r', 'sb $s4, 4($s2)', 'sb $s5, 4($s2)'),
        ('r', 'div $zero, $s4, $v0', 'div $zero, $v0, $v1'),
        ('r', 'sb $s4, 5($s2)', 'sb $s5, 5($s2)'),
    ]
    assert ('kmc_unsigned_byte_store', source.replace('*(s8 *)', '*(u8 *)')) in proposals(
        source, 'f', '', 'mips', KMC, rows, '',
    )


def test_kmc_routes_null_iteration_and_call_delay_slot_store():
    source = """void f(int n) {
    int i;
    void *object;
    void *inner;
    i = 0;
    do {
        object = allocate(n);
        if (object != NULL) {
            inner = (*(void **)((char *)(object) + 8));
            (*(s16 *)((char *)(object) + 2)) = 0;
            (*(s16 *)((char *)(inner) + 12)) = (s16) (sample(4) + 4);
            (*(s16 *)((char *)(object) + 4)) = 8;
        }
        i += 1;
    } while (i < n);
}
"""
    rows = case_rows(
        'move $s2, $v0', 'beql $s2, $zero, 0x30', 'addiu $s3, $s3, 1', 'lw $s1, 8($s2)',
        'addiu $a0, $zero, 4', 'addiu $v0, $zero, 8', 'sh $zero, 2($s2)', 'jal sample',
        'sh $v0, 4($s2)', 'addiu $v0, $v0, 4', 'sh $v0, 0xc($s1)',
    )
    [candidate] = [text for family, text in proposals(source, 'f', '', 'mips', KMC, rows, '')
                   if family == 'measured_null_iteration_store']
    assert 'if (!object) { i += 1; continue; }' in candidate
    assert candidate.index(' + 4)) = 8;') < candidate.index('sample(4)')


def test_kmc_routes_narrowing_at_the_remainder_operand():
    source = """s32 f(s32 modulus) {
    s32 value;
    value = sample() & 0xffff;
    value = value % modulus;
    return value;
}
"""
    rows = [
        ('i', 'andi $a0, $s1, 0xff', 'andi $v0, $v0, 0xffff'),
        ('i', 'andi $v0, $v0, 0xffff', 'andi $a0, $s1, 0xff'),
    ]
    [candidate] = [text for family, text in proposals(source, 'f', '', 'mips', KMC, rows, '')
                   if family == 'measured_narrow_use']
    assert 'value = sample();\n    value = (u16)value % modulus;' in candidate


def test_kmc_routes_missing_cursor_advance_before_the_relocated_store():
    source = """extern void *volatile stream;
void f(void) {
    void *cursor;
    s32 mode;
    cursor = stream;
    stream = cursor;
    mode = modeFlag;
    cursor += 8;
    consume(mode);
}
"""
    rows = [
        ('<', 'addiu $a2, $a2, 8', ''),
        (' ', 'lui $at, %hi(stream)', 'lui $at, %hi(stream)'),
        (' ', 'sw $a2, %lo(stream)($at)', 'sw $a2, %lo(stream)($at)'),
    ]
    assert ('measured_cursor_publish', source.replace('stream = cursor;', 'stream = cursor + 8;')) in proposals(
        source, 'f', '', 'mips', KMC, rows, '',
    )


def test_kmc_routes_a_scoped_relocated_field_capture():
    source = """extern void *state;
void f(void) {
    void *local;
    int field;
    field = (*(s32 *)((char *)(state) + 8));
    consume(field);
    local = state;
    consume(local);
}
"""
    rows = [
        (' ', 'lui $a1, %hi(state)', 'lui $v1, %hi(state)'),
        (' ', 'lw $a1, %lo(state)($a1)', 'lw $v1, %lo(state)($v1)'),
        ('<', 'addiu $a0, $zero, 4', ''),
        (' ', 'lw $v1, 8($a1)', 'lw $v1, 8($v1)'),
    ]
    [candidate] = [text for family, text in proposals(source, 'f', '', 'mips', KMC, rows, '')
                   if family == 'measured_scoped_global_field']
    assert 'do {\n        local = state;\n    } while (0);' in candidate


def test_measured_guard_reset_is_tried_with_kmc():
    source = """extern volatile s32 flag;
extern volatile s32 limit;
extern s32 x, y, a, b;
void f(void) {
    s32 index;
    do { { s32 first = a; s32 second = b; x = first; y = second; } flag = 0; index = 0; } while (0);
    if (limit > 0) { do { index++; } while (index < limit); }
}
"""
    rows = [
        ('o', '', 'lui $at, %hi(flag)'),
        ('o', '', 'sw $zero, %lo(flag)($at)'),
        (' ', 'lui $v0, %hi(limit)', 'lui $v0, %hi(limit)'),
        (' ', 'lw $v0, %lo(limit)($v0)', 'lw $v0, %lo(limit)($v0)'),
        ('o', 'lui $at, %hi(flag)', ''),
        ('o', 'sw $zero, %lo(flag)($at)', ''),
        ('i', 'blez $v0, 0x48', 'blez $v0, 0x4c'),
    ]
    candidates = dict(proposals(source, 'f', '', 'mips', KMC, rows, ''))
    assert 'if (initial_bound > 0)' in candidates['guard_reset_scheduling']
    assert 'guard_reset_scheduling' not in dict(proposals(source, 'f', '', 'mips', EGCS, rows, ''))

    slot_rows = [('o', '', 'move $s1, $zero')] + rows + [('o', 'move $s1, $zero', 'andi $v0, $s1, 0xffff')]
    slot_candidates = dict(proposals(source, 'f', '', 'mips', KMC, slot_rows, ''))
    assert 'do { index = 0; } while (0);' in slot_candidates['guard_reset_delay_slot']


def test_measured_shared_result_copy_runs_on_kmc():
    source = """void f(void) {
    s32 flag;
    s32 result;
    flag = 0;
    if (obj != NULL) {
        result = flag;
        if (M2C_FIELD(obj, void **, 16) != NULL) {
            flag = 1;
            goto done;
        }
    } else {
done:
        result = flag;
    }
    if (result == 0) fallback();
}
"""
    rows = [
        (' ', 'beqz $v0, 0x40', 'beqz $v0, 0x40'),
        ('r', 'move $v0, $a2', 'move $v1, $zero'),
        ('r', 'move $v0, $a2', 'move $v1, $a2'),
        ('r', 'bnez $v0, 0x50', 'bnez $v1, 0x50'),
    ]
    candidates = dict(proposals(source, 'f', '', 'mips', KMC, rows, ''))
    assert '}\ndone:\n    result = flag;' in candidates['shared_result_copy']
    assert 'shared_result_copy' not in dict(proposals(source, 'f', '', 'mips', EGCS, rows, ''))


def test_measured_field_mask_staging():
    source = """#define FIELD(p,T,o) (*(T *)((u8 *)(p) + (o)))
void f(void *ptr) {
    s32 word;
    word = FIELD(ptr, s32, 40) & ~252;
    output(0, word);
}
"""
    rows = [
        ('o', '', 'move $a0, $zero'),
        (' ', 'lw $v1, 0x28($s0)', 'lw $v1, 0x28($s0)'),
        ('o', 'move $a0, $zero', ''),
        (' ', 'addiu $v0, $zero, -0xfd', 'addiu $v0, $zero, -0xfd'),
        (' ', 'and $v1, $v1, $v0', 'and $v1, $v1, $v0'),
    ]
    assert 'word &= ~252;' in dict(proposals(source, 'f', '', 'mips', KMC, rows, ''))['staged_field_mask']
    assert 'staged_field_mask' not in dict(proposals(source, 'f', '', 'mips', EGCS, rows, ''))


def test_measured_search_sentinel_and_byte_guard():
    source = """void f(s32 key) {
    u32 index;
    index = 0;
    do {
        if (matches(index, key)) {
            key = index;
            goto found;
        }
        ++index;
    } while (index < 4U);
    key = index;
    index = -1U;
found:
    if (key != -1) {
        if ((u8)(*(u8 *)((u8 *)&levels + (key * 16)) << 1) == 4) hit(key);
    }
}
"""
    rows = [
        ('<', 'addiu $a0, $zero, -1', ''),
        (' ', 'move $s0, $a0', 'move $s0, $a0'),
        (' ', 'lbu $v0, 8($s0)', 'lbu $v0, 8($s0)'),
        ('i', 'addiu $v1, $zero, 2', 'addiu $v1, $zero, 4'),
        ('>', '', 'sll $v0, $v0, 1'),
        (' ', 'andi $v0, $v0, 0xff', 'andi $v0, $v0, 0xff'),
    ]
    assert 'found:\n    key = index;' in dict(proposals(source, 'f', '', 'mips', KMC, rows, ''))[
        'search_sentinel_byte_guard']
    assert 'search_sentinel_byte_guard' not in dict(proposals(source, 'f', '', 'mips', EGCS, rows, ''))


def test_measured_guarded_loop_counter_reuse():
    source = """void f(void) {
    u32 i;
    u32 j;
    i = load();
    publish(i);
    i = 0;
    if (n) {
        do { put(i); i += 1; } while (i < n);
    }
    j = 0;
    if (m) {
        offset = 0;
        do { put(j); j += 1; } while (j < m);
    }
}
"""
    rows = [
        ('r', 'move $s1, $zero', 'move $a2, $zero'),
        ('r', 'addiu $s1, $s1, 1', 'addiu $a2, $a2, 1'),
        ('r', 'sltu $v0, $s1, $v0', 'sltu $v0, $a2, $v0'),
    ]
    assert 'while (j < n);' in dict(proposals(source, 'f', '', 'mips', KMC, rows, ''))[
        'guarded_loop_counter_reuse']
    assert 'guarded_loop_counter_reuse' not in dict(proposals(source, 'f', '', 'mips', EGCS, rows, ''))


def test_shared_halfword_index_routed_for_kmc(monkeypatch):
    from decompnet.app.diff_gated_repairs import measured_narrow_use

    def candidate(source, name, rows):
        assert source == 'void f(void) {}'
        assert name == 'f'
        assert rows == [('r', 'lh $a0, ($v0)', 'lh $v1, ($v0)')]
        return [('shared_halfword_index', 'void f(void) { work(); }')]

    monkeypatch.setattr(measured_narrow_use, 'shared_halfword_index_variants', candidate)
    rows = [('r', 'lh $a0, ($v0)', 'lh $v1, ($v0)')]
    assert 'shared_halfword_index' in dict(proposals('void f(void) {}', 'f', '', 'mips', KMC, rows, ''))
    assert 'shared_halfword_index' not in dict(proposals('void f(void) {}', 'f', '', 'mips', EGCS, rows, ''))


def test_byte_backedge_routed_for_kmc(monkeypatch):
    from decompnet.app.diff_gated_repairs import measured_narrow_use

    def candidate(source, name, rows):
        assert (source, name) == ('void f(void) {}', 'f')
        assert rows == [('r', 'lbu $a0, ($t0)', 'lbu $v0, ($t0)')]
        return [('byte_backedge_carrier', 'void f(void) { work(); }')]

    monkeypatch.setattr(measured_narrow_use, 'byte_backedge_variants', candidate)
    rows = [('r', 'lbu $a0, ($t0)', 'lbu $v0, ($t0)')]
    assert 'byte_backedge_carrier' in dict(proposals('void f(void) {}', 'f', '', 'mips', KMC, rows, ''))
    assert 'byte_backedge_carrier' not in dict(proposals('void f(void) {}', 'f', '', 'mips', EGCS, rows, ''))


def test_repeated_float_products_routed_for_kmc(monkeypatch):
    from decompnet.app.diff_gated_repairs import float_intermediate_repairs

    def candidate(source, name, rows):
        assert (source, name) == ('void f(void) {}', 'f')
        assert rows == [('r', 'mul.s $f0, $f6, $f2', 'mul.s $f2, $f6, $f2')]
        return [('repeated_float_product_staging', 'void f(void) { work(); }')]

    monkeypatch.setattr(float_intermediate_repairs, 'repeated_products', candidate)
    rows = [('r', 'mul.s $f0, $f6, $f2', 'mul.s $f2, $f6, $f2')]
    assert 'repeated_float_product_staging' in dict(proposals('void f(void) {}', 'f', '', 'mips', KMC, rows, ''))
    assert 'repeated_float_product_staging' not in dict(proposals('void f(void) {}', 'f', '', 'mips', EGCS, rows, ''))


def test_kmc_proposes_an_alias_for_a_separately_relocated_byte_test_and_clear():
    source = '''
extern M2C_UNK state;
void f(void) {
    if (M2C_FIELD(&state, u8 *, 0x24) == 1) {
        M2C_FIELD(&state, u8 *, 0x24) = 0;
        use();
    }
}
'''
    asm = '''
/* 00 80001000 3C038000 */ lui $v1, %hi(state + 0x24)
/* 04 80001004 90632024 */ lbu $v1, %lo(state + 0x24)($v1)
/* 08 80001008 24020001 */ addiu $v0, $zero, 1
/* 0C 8000100C 14620003 */ bne $v1, $v0, .Ldone
/* 10 80001010 00000000 */ nop
/* 14 80001014 3C018000 */ lui $at, %hi(state + 0x24)
/* 18 80001018 A0202024 */ sb $zero, %lo(state + 0x24)($at)
'''
    resolve = {'state': 0x80002000, 'D_80002024': 0x80002024}.get
    rows = case_rows('lbu $v1, %lo(D_80002024)($v1)', 'sb $zero, %lo(D_80002024)($at)')
    candidates = dict(proposals(source, 'f', '', 'mips', KMC, rows, asm, resolve=resolve))
    assert 'if (D_80002024 == 1)' in candidates['relocated_branch_byte']
    assert 'D_80002024 = 0;' in candidates['relocated_branch_byte']


def test_measured_boolean_byte_carrier():
    source = """void f(void) {
    s32 flag;
    s32 value;
    flag = 0;
    if (ready) {
        value = state != 7;
        flag = (u8)value;
    }
    if (flag & 0xff) work();
}
"""
    rows = [
        (' ', 'sltu $v0, $zero, $v0', 'sltu $v0, $zero, $v0'),
        ('|', 'move $s0, $v0', 'andi $s0, $v0, 0xff'),
    ]
    assert 'u8 flag;' in dict(proposals(source, 'f', '', 'mips', KMC, rows, ''))['boolean_byte_carrier']
    assert 'boolean_byte_carrier' not in dict(proposals(source, 'f', '', 'mips', EGCS, rows, ''))


def test_measured_relocated_signed_halfword_load():
    source = 'void f(void) { s32 value; value = *(volatile s32 *)(fields + index); use(value); }'
    rows = [('|', 'lh $v0, %lo(fields)($v0)', 'lw $v0, %lo(fields)($v0)')]
    candidates = [text for name, text in proposals(source, 'f', '', 'mips', KMC, rows, '')
                  if name == 'relocated_signed_halfword_load']
    assert any('do { value = *(s16 *)(fields + index); } while (0);' in text for text in candidates)
    assert 'relocated_signed_halfword_load' not in dict(proposals(source, 'f', '', 'mips', EGCS, rows, ''))


def test_measured_leaf_call_byte_offset():
    source = 'void f(s32 n, u16 *packet) { work(n & 0xffff, packet + 4, *packet); }\n'
    rows = [
        (' ', 'lhu $a2, ($a1)', 'lhu $a2, ($a1)'),
        (' ', 'andi $a0, $a0, 0xffff', 'andi $a0, $a0, 0xffff'),
        (' ', 'jal work', 'jal work'),
        ('i', 'addiu $a1, $a1, 4', 'addiu $a1, $a1, 8'),
    ]
    assert dict(proposals(source, 'f', '', 'mips', KMC, rows, ''))['measured_call_byte_offset'] == source.replace(
        'packet + 4', '(u8 *)packet + 4')


def test_forwarded_stack_output_resolves_the_callee_in_raw_assembly():
    source = ('f32 distance(u16 *); /* extern */\n'
              'void f(s32 item) {\n    u16 marker;\n    save(distance(&marker), item);\n}')
    setup = 'addiu $a1, $sp, 0x10'
    rows = [(' ', 'jal distance', 'jal distance'), ('r', setup, setup.replace('$a1', '$a0'))]
    asm = assembly('addiu $sp, $sp, -0x18', 'sw $ra, 0x14($sp)', 'jal func_80002000', setup,
                   'lw $ra, 0x14($sp)', 'jr $ra', 'addiu $sp, $sp, 0x18')
    result = dict(proposals(source, 'f', '', 'mips', KMC, rows, asm,
                            resolve={'distance': 0x80002000}.get))
    assert 'distance(item, &marker)' in result['passthrough_argument']
    assert 'passthrough_argument' not in dict(proposals(source, 'f', '', 'mips', KMC, rows, asm))


def test_switch_forwarding_uses_original_rom_table_entries():
    source = ('s32 effect(u8); /* extern */\n'
              'void f(void *object) { switch (kind) { case 1: effect(mode); break; } }')
    ops = ['sltiu $v0, $v1, 2', 'beqz $v0, .Lend', 'sll $v0, $v1, 2',
           'lui $at, %hi(jtbl_80003000)', 'addu $at, $at, $v0',
           'lw $v0, %lo(jtbl_80003000)($at)', 'jr $v0', 'nop',
           'jal effect', 'lbu $a1, 8($s0)', 'sb $zero, 8($s0)', '.Lend:', 'jr $ra', 'nop']
    rows = [('r', op, op.replace('$a1', '$a0')) if i == 9 else (' ', op, op)
            for i, op in enumerate(ops) if not op.endswith(':')]
    table = b''.join(address.to_bytes(4, 'big') for address in (0x80001020, 0x80001030))
    def read_data(name, width):
        return table if (name, width) == ('jtbl_80003000', len(table)) else None

    result = dict(proposals(source, 'f', '', 'mips', KMC, rows, assembly(*ops),
                            read_data=read_data, big_endian=True))
    assert 'effect(object, mode)' in result['passthrough_argument']
    assert 'passthrough_argument' not in dict(proposals(source, 'f', '', 'mips', KMC, rows, assembly(*ops)))


def test_kmc_clamp_reschedules_stack_pointer_after_value_arithmetic():
    source = '''
void f(void) {
    s32 value;
    u8 buffer;
    value = read();
    write(&buffer, &fmt, value & ((s32) ~value >> 0x1F) & 0xFFFF);
}
'''
    stack = 'addiu $a0, $sp, 0x20'
    rows = [(' ', op, op) for op in ('jal read', 'nop')]
    rows += [('o', '', stack)]
    rows += [(' ', op, op) for op in ('nor $v1, $zero, $v0', 'sra $v1, $v1, 0x1f', 'and $v0, $v0, $v1')]
    rows += [('o', stack, '')]
    rows += [(' ', op, op) for op in ('lui $a1, %hi(fmt)', 'addiu $a1, $a1, %lo(fmt)', 'jal write',
                                      'andi $a2, $v0, 0xffff')]
    result = dict(proposals(source, 'f', '', 'mips', KMC, rows, ''))
    assert 'value &= (s32) ~value >> 31;' in result['staged_call_clamp']
    assert 'staged_call_clamp' not in dict(proposals(source, 'f', '', 'mips', EGCS, rows, ''))


def test_kmc_indexed_context_uses_byte_pointer_and_stages_the_offset():
    source = '''
extern s32 state;
s32 f(s32 index) {
    return M2C_FIELD((state + ((index & 0xFF) * 24)), s32 *, 96) == 1;
}
'''
    rows = [('o', '', 'andi $a0, $a0, 255')]
    rows.extend((' ', op, op) for op in (
        'lui $v1, %hi(state)', 'lw $v1, %lo(state)($v1)', 'andi $a0, $a0, 255',
        'sll $v0, $a0, 1', 'addu $v0, $v0, $a0', 'sll $v0, $v0, 3',
        'addu $v1, $v1, $v0', 'lw $v0, 96($v1)', 'xori $v0, $v0, 1',
        'jr $ra', 'sltiu $v0, $v0, 1',
    ))
    fixed = dict(proposals(source, 'f', '', 'mips', KMC, rows, ''))['staged_indexed_context']
    assert 'extern s8 *state;' in fixed
    assert '__repair_address_offset = (index & 0xFF) * 24;' in fixed
    assert 'staged_indexed_context' not in dict(proposals(source, 'f', '', 'mips', EGCS, rows, ''))


def test_kmc_narrow_call_result_preserves_the_measured_return_value():
    source = '''
s32 reserve();
void f(void *owner) {
    s32 ticket;
    ticket = reserve(owner) & 0xffff;
    M2C_FIELD(owner, s32 *, 4) = ticket;
}
'''
    ops = (
        'jal reserve', 'move $a0, $s0', 'lw $v1, 8($s0)', 'andi $v0, $v0, 0xffff',
        'sll $a0, $v0, 2', 'addu $v1, $v1, $a0', 'sw $v0, ($v1)',
        'lw $v1, 16($s0)', 'andi $a1, $v0, 31', 'or $a1, $a1, $a0',
        'sw $a1, ($v1)', 'lw $ra, 20($sp)', 'lw $s1, 16($sp)', 'lw $s0, 12($sp)',
        'jr $ra', 'addiu $sp, $sp, 24',
    )
    rows = [(' ', op, op) for op in ops]
    rows[3] = ('r', ops[3], 'andi $a0, $v0, 0xffff')
    family = 'measured_narrow_call_return'
    fixed = dict(proposals(source, 'f', '', 'mips', KMC, rows, ''))[family]
    assert 'ticket = (u16)reserve(owner);' in fixed
    assert 'return ticket;' in fixed
    assert family not in dict(proposals(source, 'f', '', 'mips', EGCS, rows, ''))


def test_kmc_measured_stack_record_binds_the_payload_to_the_call():
    source = '''
void enqueue(s32 queue, s16 *record, s32 delay);
void f(s32 queue, s16 value) {
    s16 tag;
    s16 payload;
    payload = value;
    tag = 2;
    enqueue(queue + 64, &tag, 0);
}
'''
    rows = [
        ('s', 'addiu $sp, $sp, -40', 'addiu $sp, $sp, -32'),
        (' ', 'addiu $v0, $zero, 2', 'addiu $v0, $zero, 2'),
        (' ', 'addiu $a0, $a0, 64', 'addiu $a0, $a0, 64'),
        ('<', 'sh $a1, 20($sp)', ''),
        (' ', 'addiu $a1, $sp, 16', 'addiu $a1, $sp, 16'),
        (' ', 'move $a2, $zero', 'move $a2, $zero'),
        ('s', 'sw $ra, 32($sp)', 'sw $ra, 24($sp)'),
        (' ', 'jal enqueue', 'jal enqueue'),
        (' ', 'sh $v0, 16($sp)', 'sh $v0, 16($sp)'),
        ('s', 'lw $ra, 32($sp)', 'lw $ra, 24($sp)'),
        (' ', 'jr $ra', 'jr $ra'),
        ('s', 'addiu $sp, $sp, 40', 'addiu $sp, $sp, 32'),
    ]
    family = 'measured_tagged_stack_record'
    fixed = dict(proposals(source, 'f', '', 'mips', KMC, rows, ''))[family]
    assert '__repair_stack_record.__repair_stack_record_data = value;' in fixed
    assert family not in dict(proposals(source, 'f', '', 'mips', EGCS, rows, ''))


def test_kmc_measured_byte_table_checks_both_relocations():
    source = '''
extern M2C_UNK table;
u8 f(s32 index) {
    return *(&table + (index & 7));
}
'''
    ops = ('andi $v1, $a0, 7', *('nop',) * 6,
           'lui $v0, %hi(table)', 'addu $v0, $v0, $v1', 'jr $ra', 'lbu $v0, %lo(table)($v0)')
    rows = [(' ', op, op) for op in ops]
    rows.insert(7, ('>', '', 'sll $v1, $v1, 2'))
    rows[8] = ('i', 'lui $v0, %hi(table)', 'lui $v0, %hi(last_byte)')
    rows[-1] = ('i', 'lbu $v0, %lo(table)($v0)', 'lbu $v0, %lo(last_byte)($v0)')
    resolve = {'table': 0x80001200, 'last_byte': 0x80001203}.get
    family = 'measured_byte_table_index'
    result = dict(proposals(source, 'f', '', 'mips', KMC, rows, '', resolve=resolve))
    assert '((u8 *)&table)[(index & 7)]' in result[family]
    assert family not in dict(proposals(source, 'f', '', 'mips', EGCS, rows, '', resolve=resolve))


def test_kmc_measured_halfword_limit_follows_coordinate_stores():
    source = '''
void f(void *out, void *in) {
    M2C_FIELD(out, s16 *, 4) = 511;
    M2C_FIELD(out, s16 *, 0) = (s16) (M2C_FIELD(in, s16 *, 8) * 2);
    M2C_FIELD(out, s16 *, 2) = (s16) (M2C_FIELD(in, s16 *, 10) * 2);
    M2C_FIELD(out, s16 *, 6) = 0;
}
'''
    moved = ('addiu $a2, $zero, 511', 'sh $a2, 4($a0)')
    middle = ('lh $v0, 8($a1)', 'sll $v0, $v0, 1', 'sh $v0, ($a0)', 'lh $v0, 10($a1)')
    tail = ('sh $zero, 6($a0)', 'sll $v0, $v0, 1', 'sh $v0, 2($a0)', *('nop',) * 13)
    rows = [('o', '', op) for op in moved] + [(' ', op, op) for op in middle]
    rows += [('o', op, '') for op in moved] + [(' ', op, op) for op in tail]
    family = 'measured_late_halfword_limit'
    fixed = dict(proposals(source, 'f', '', 'mips', KMC, rows, ''))[family]
    assert fixed.index('M2C_FIELD(out, s16 *, 2)') < fixed.index('= 511;')
    assert family not in dict(proposals(source, 'f', '', 'mips', EGCS, rows, ''))


def test_kmc_record_repairs_receive_the_project_resolver(monkeypatch):
    from decompnet.app.diff_gated_repairs import measured_halfword_stores, measured_kmc_record_reads

    source = 'void f(void) {}'
    rows = [('r', 'move $v0, $a0', 'move $v0, $a1')]
    resolve = {'state': 0x80001200}.get

    def halfwords(text, name, diff, resolver):
        assert (text, name, diff) == (source, 'f', rows)
        assert resolver is resolve
        return [('halfword_fixture', 'halfwords')]

    def records(text, name, header, diff, resolver):
        assert (text, name, header, diff) == (source, 'f', '', rows)
        assert resolver is resolve
        return [('record_fixture', 'records')]

    monkeypatch.setattr(measured_halfword_stores, 'variants', halfwords)
    monkeypatch.setattr(measured_kmc_record_reads, 'variants', records)
    kmc = dict(proposals(source, 'f', '', 'mips', KMC, rows, '', resolve=resolve))
    assert kmc['halfword_fixture'] == 'halfwords'
    assert kmc['record_fixture'] == 'records'
    egcs = dict(proposals(source, 'f', '', 'mips', EGCS, rows, '', resolve=resolve))
    assert 'halfword_fixture' not in egcs and 'record_fixture' not in egcs


def test_kmc_short_integer_sqrt_requires_fast_math():
    source = '''
s32 f(s32 x) {
    s32 result;
    result = 0;
    if (x >= 0) { result = (s32) sqrtf((f32) x); }
    return result;
}
'''
    ops = ('bltz $a0, 32', 'move $v0, $zero', 'mtc1 $a0, $f0', 'nop', 'cvt.s.w $f0, $f0',
           'sqrt.s $f0, $f0', 'trunc.w.s $f2, $f0', 'mfc1 $v0, $f2', 'jr $ra', 'nop')
    rows = [(' ', op, op) for op in ops]
    rows[1] = ('r', rows[1][1], 'move $v1, $zero')
    rows.insert(8, ('o', '', 'nop'))
    family = 'measured_early_integer_sqrt'
    fixed = dict(proposals(source, 'f', '', 'mips', KMC, rows, '', flags=('-ffast-math',)))[family]
    assert '__builtin_sqrtf' in fixed
    assert family not in dict(proposals(source, 'f', '', 'mips', KMC, rows, ''))


@pytest.mark.parametrize('module_name', ['measured_kmc_cursor_switch', 'measured_kmc_carriers', 'measured_kmc_call_setup'])
def test_measured_kmc_repairs_route_is_compiler_specific(monkeypatch, module_name):
    from decompnet.app import diff_gated_repairs

    source = 'void f(void) {}'
    rows = [('r', 'move $v0, $a0', 'move $v0, $a1')]

    def repair(text, name, header, diff):
        assert (text, name, header, diff) == (source, 'f', '', rows)
        return [('cursor_fixture', 'repaired')]

    monkeypatch.setattr(getattr(diff_gated_repairs, module_name), 'variants', repair)
    assert dict(proposals(source, 'f', '', 'mips', KMC, rows, ''))['cursor_fixture'] == 'repaired'
    assert 'cursor_fixture' not in dict(proposals(source, 'f', '', 'mips', EGCS, rows, ''))


def test_byte_offset_scale_reaches_the_deep_permuter_diff_dispatch():
    source = """void f(void) {
    s32 off;
    s32 reload;
    off = key * 4;
    use(*(s32 *)((s8 *)&first + off));
    reload = key * 4;
    use(*(s32 *)((s8 *)&second + reload));
}
"""
    rows = [('i', 'sll $v1, $v0, 4', 'sll $v1, $v0, 2'),
            ('i', 'sll $v0, $v0, 4', 'sll $v0, $v0, 2')]
    expected = ('measured_byte_offset_scale', source.replace('key * 4', 'key * 16'))
    assert expected in proposals(source, 'f', '', 'mips', KMC, rows, '')
    assert expected not in proposals(source, 'f', '', 'mips', EGCS, rows, '')


def test_next_record_field_reaches_kmc_diff_dispatch():
    source = """extern u16 *cursor;
typedef struct { unsigned short field_0; unsigned char pad_2[10]; unsigned short field_C; } __repair_record_cursor;
int f(void) {
    cursor += 6;
    if (((__repair_record_cursor *)cursor)->field_C != 0) {
        return 1;
    }
    return 0;
}
"""
    step = 'addiu $v0, $a1, 0xc'
    rows = [('i', 'lhu $v1, 0xc($a1)', 'lhu $v1, 0x18($a1)'), (' ', step, step)]
    expected = ('next_record_field_after_advance', source.replace('->field_C', '->field_0'))
    assert expected in proposals(source, 'f', '', 'mips', KMC, rows, '')
    assert expected not in proposals(source, 'f', '', 'mips', EGCS, rows, '')


def test_commuted_global_stride_reaches_kmc_diff_dispatch():
    source = """extern M2C_UNK table;
void f(void *input) {
    void *record;
    record = (M2C_FIELD(input, s8 *, 12) * 0xe8) + &table;
    use(M2C_FIELD(record, u8 *, 4));
}
"""
    ops = ['sll $v0, $v0, 3', 'lui $v1, %hi(table)', 'addiu $v1, $v1, %lo(table)',
           'addu $s0, $v0, $v1', 'lbu $a1, 4($s0)']
    rows = [('i', ops[0], 'sll $v0, $v0, 5'), *[(' ', op, op) for op in ops[1:]]]
    expected = ('measured_commuted_global_stride', source.replace('+ &table', '+ (s8 *)&table'))
    assert expected in proposals(source, 'f', '', 'mips', KMC, rows, '')
    assert expected not in proposals(source, 'f', '', 'mips', EGCS, rows, '')


def test_measured_kmc_input_receives_the_linked_symbol_resolver(monkeypatch):
    from decompnet.app import diff_gated_repairs

    def resolve(name):
        return {'table': 32}.get(name)

    source = 'void f(void) {}'
    rows = [('r', 'move $v0, $a0', 'move $v0, $a1')]

    def repair(text, name, header, diff, resolve):
        assert (text, name, header, diff) == (source, 'f', '', rows)
        assert resolve('table') == 32
        return [('input_fixture', 'repaired')]

    monkeypatch.setattr(diff_gated_repairs.measured_kmc_input, 'variants', repair)
    assert dict(proposals(source, 'f', '', 'mips', KMC, rows, '', resolve=resolve))['input_fixture'] == 'repaired'
    assert 'input_fixture' not in dict(proposals(source, 'f', '', 'mips', EGCS, rows, '', resolve=resolve))


def test_measured_halfword_switch_is_routed_for_kmc(monkeypatch):
    from decompnet.app import diff_gated_repairs

    source = 'void f(void) {}'
    rows = [('r', 'move $v0, $a0', 'move $v0, $a1')]
    monkeypatch.setattr(diff_gated_repairs.measured_halfword_switch, 'variants',
                        lambda text, name, header, diff: [('halfword_switch_fixture', text)])
    assert 'halfword_switch_fixture' in dict(proposals(source, 'f', '', 'mips', KMC, rows, ''))
    assert 'halfword_switch_fixture' not in dict(proposals(source, 'f', '', 'mips', EGCS, rows, ''))


def test_identical_branch_assignments_reach_kmc_diff_dispatch():
    source = """s32 f(s32 input) {
    f32 score;
    s32 ok;
    score = input;
    if (score) {
        do { ok = 1; } while (0);
    } else {
        ok = 1;
    }
    return ok;
}
"""
    rows = [('>', '', 'move $t1, $zero')]
    generated = proposals(source, 'f', '', 'mips', KMC, rows, '')
    assert any(label == 'measured_identical_branch_assignments' and 'if (score)' not in candidate
               for label, candidate in generated)


def test_inline_interpolated_product_reaches_kmc_diff_dispatch():
    source = """extern f32 table[];
f32 f(int i) {
    f32 gain, mix, tmp, out;
    gain = 2.0f;
    mix = 0.25f;
    tmp = ((1.0f-mix)*table[i]+mix*table[i+1]);
    out = gain * tmp;
    return out;
}
"""
    branch = 'bne $a3, $t2, 0x80'
    rows = [
        ('r', 'add.s $f2, $f2, $f0', 'add.s $f0, $f2, $f0'),
        (' ', branch, branch),
        ('r', 'mul.s $f6, $f6, $f2', 'mul.s $f6, $f6, $f0'),
    ]
    generated = proposals(source, 'f', '', 'mips', KMC, rows, '')
    assert any(label == 'measured_inline_interpolated_product' and 'out = gain*' in candidate
               for label, candidate in generated)


def test_cursor_advance_order_reaches_kmc_diff_dispatch():
    source = """void f(void) {
    s32 *left;
    u8 *middle;
    s32 *right;
    left = (s32 *) (((u8 *) left) + 0x20);
    middle += 32;
    right = (s32 *) (((u8 *) right) + 32);
}
"""
    a, b, c = (f'addiu ${reg}, ${reg}, 0x20' for reg in ('s3', 's5', 's4'))
    generated = proposals(source, 'f', '', 'mips', KMC, [('r', a, c), (' ', b, b), ('r', c, a)], '')
    assert any(label == 'measured_cursor_advance_order' and candidate.index('    right =') < candidate.index('    left =')
               for label, candidate in generated)


def test_label_loop_cursor_reaches_kmc_diff_dispatch():
    source = """void f(void *base) {
    void *cursor;
    cursor = base;
    first_loop:
    write_halfword(cursor);
    cursor += 2;
    if (more()) goto first_loop;

    cursor = base;
    next_loop:
    write_word(cursor);
    cursor += 4;
    if (more()) goto next_loop;
}
"""
    rows = [
        ('r', 'move $v1, $s5', 'move $s1, $s5'),
        ('r', 'sh $zero, 0x1bc($v1)', 'sh $zero, 0x1bc($s1)'),
        ('r', 'sh $v0, 0x1bc($v1)', 'sh $v0, 0x1bc($s1)'),
        ('r', 'addiu $v1, $v1, 2', 'addiu $s1, $s1, 2'),
    ]
    generated = proposals(source, 'f', '', 'mips', KMC, rows, '')
    assert any(label == 'measured_label_loop_cursor' and 'write_halfword(__repair_first_loop_cursor)' in candidate
               for label, candidate in generated)


def test_byte_subtraction_carrier_reaches_kmc_diff_dispatch():
    source = """void f(void *obj, void *child) {
    short result;
    void *pointer;
    result = *((u8 *) (((char *) child) + 38));
    pointer = (void *) (*((u8 *) (((char *) obj) + 130))); result = (u32) pointer - result;
    if (result < 0) use(result);
}
"""
    rows = [
        ('r', 'lbu $a0, 0x26($s1)', 'lbu $a1, 0x26($s1)'),
        ('r', 'lbu $a1, 0x82($s0)', 'lbu $a2, 0x82($s0)'),
        ('r', 'subu $a0, $a1, $a0', 'subu $v1, $a2, $a1'),
        ('r', 'bgez $a0, 0x18c', 'bgez $v1, 0x18c'),
    ]
    generated = proposals(source, 'f', '', 'mips', KMC, rows, '')
    assert any(label == 'measured_byte_subtraction_carrier' and 'result -=' in candidate
               for label, candidate in generated)


def test_float_scale_carrier_reaches_kmc_diff_dispatch():
    source = """extern f32 table[];
f32 f(int key, int plain, f32 factor) {
    f32 scale;
    if (plain) {
        scale = table[key] * factor;
    } else {
        f32 coefficient;
        coefficient = 0.5f;
        coefficient = table[key] * coefficient;
        scale = coefficient * factor;
    }
    return scale;
}
"""
    load = ('r', 'lwc1 $f2, %lo(table)($at)', 'lwc1 $f0, %lo(table)($at)')
    multiply = ('r', 'mul.s $f2, $f2, $f4', 'mul.s $f2, $f0, $f4')
    literal = ('r', 'mtc1 $at, $f0', 'mtc1 $at, $f2')
    rows = [load, multiply, literal, load]
    generated = proposals(source, 'f', '', 'mips', KMC, rows, '')
    assert any(label == 'measured_float_scale_carrier' and 'scale *= coefficient;' in candidate
               for label, candidate in generated)


def test_early_float_load_reaches_kmc_diff_dispatch():
    source = """void f(void *parent) {
    void *child;
    child = M2C_FIELD(parent, void **, 108);
    M2C_FIELD(child, s16 *, 38) = 1;
    M2C_FIELD(child, f32 *, 28) = (f32) (M2C_FIELD(parent, f32 *, 40) - 1.0f);
}
"""
    read, sub = 'lwc1 $f0, 0x28($s0)', 'sub.s $f0, $f0, $f2'
    bind, field, save = 'lw $v0, 0x6c($s0)', 'sh $v1, 0x26($v0)', 'swc1 $f0, 0x1c($v0)'
    rows = [(' ', bind, bind), ('o', read, ''), ('o', sub, ''), (' ', field, field),
            ('o', '', read), ('o', '', sub), (' ', save, save)]
    generated = proposals(source, 'f', '', 'mips', KMC, rows, '')
    assert any(label == 'measured_early_float_field_load' and
               '__repair_parent_float = M2C_FIELD(parent, f32 *, 40);' in candidate
               for label, candidate in generated)


def test_measured_kmc_byte_stride_routes_to_kmc(monkeypatch):
    from decompnet.app import diff_gated_repairs

    rows = [('r', 'move $v0, $a0', 'move $v0, $a1')]
    monkeypatch.setattr(diff_gated_repairs.measured_kmc_byte_stride, 'variants',
                        lambda text, name, header, diff: [('byte_stride_fixture', text)])
    assert 'byte_stride_fixture' in dict(proposals('void f(void) {}', 'f', '', 'mips', KMC, rows, ''))
    assert 'byte_stride_fixture' not in dict(proposals('void f(void) {}', 'f', '', 'mips', EGCS, rows, ''))


def test_measured_command_word_is_routed_for_kmc(monkeypatch):
    from decompnet.app import diff_gated_repairs

    rows = [('r', 'move $v0, $a0', 'move $v0, $a1')]
    monkeypatch.setattr(diff_gated_repairs.measured_kmc_command, 'variants',
                        lambda text, name, header, diff: [('command_fixture', text)])
    assert 'command_fixture' in dict(proposals('void f(void) {}', 'f', '', 'mips', KMC, rows, ''))
    assert 'command_fixture' not in dict(proposals('void f(void) {}', 'f', '', 'mips', EGCS, rows, ''))


def test_branch_halfword_pointer_routes_to_kmc(monkeypatch):
    from decompnet.app import diff_gated_repairs

    rows = [('r', 'move $v0, $a0', 'move $v0, $a1')]
    monkeypatch.setattr(diff_gated_repairs.measured_kmc_branch_pointer, 'variants',
                        lambda text, name, header, diff: [('branch_pointer_fixture', text)])
    assert 'branch_pointer_fixture' in dict(proposals('void f(void) {}', 'f', '', 'mips', KMC, rows, ''))
    assert 'branch_pointer_fixture' not in dict(proposals('void f(void) {}', 'f', '', 'mips', EGCS, rows, ''))


def test_direct_flag_update_routes_to_kmc(monkeypatch):
    from decompnet.app import diff_gated_repairs

    rows = [('r', 'move $v0, $a0', 'move $v0, $a1')]
    monkeypatch.setattr(diff_gated_repairs.measured_kmc_flag_update, 'variants',
                        lambda text, name, header, diff: [('flag_update_fixture', text)])
    assert 'flag_update_fixture' in dict(proposals('void f(void) {}', 'f', '', 'mips', KMC, rows, ''))
    assert 'flag_update_fixture' not in dict(proposals('void f(void) {}', 'f', '', 'mips', EGCS, rows, ''))


def test_thread_addresses_receive_symbol_resolver(monkeypatch):
    from decompnet.app import diff_gated_repairs

    rows = [('r', 'move $v0, $a0', 'move $v0, $a1')]
    resolve = {'table': 32}.get

    def repair(text, name, header, diff, resolver):
        assert resolver('table') == 32
        return [('thread_address_fixture', text)]

    monkeypatch.setattr(diff_gated_repairs.measured_thread_addresses, 'variants', repair)
    assert 'thread_address_fixture' in dict(proposals('void f(void) {}', 'f', '', 'mips', KMC, rows, '', resolve=resolve))
    assert 'thread_address_fixture' not in dict(proposals('void f(void) {}', 'f', '', 'mips', EGCS, rows, '', resolve=resolve))


def test_repeated_reads_route_to_kmc_with_symbol_resolution(monkeypatch):
    from decompnet.app import diff_gated_repairs

    rows = [('r', 'move $v0, $a0', 'move $v0, $a1')]
    resolve = {'word': 32}.get

    def repair(text, name, header, diff, resolver):
        assert resolver('word') == 32
        return [('repeated_read_fixture', text)]

    monkeypatch.setattr(diff_gated_repairs.measured_kmc_repeated_reads, 'variants', repair)
    assert 'repeated_read_fixture' in dict(proposals('void f(void) {}', 'f', '', 'mips', KMC, rows, '', resolve=resolve))
    assert 'repeated_read_fixture' not in dict(proposals('void f(void) {}', 'f', '', 'mips', EGCS, rows, '', resolve=resolve))


def test_stack_and_call_repairs_receive_the_resolver(monkeypatch):
    from decompnet.app import diff_gated_repairs

    rows = [('r', 'move $v0, $a0', 'move $v0, $a1')]
    resolve = {'word': 32}.get

    def repair(text, name, header, diff, resolver):
        assert resolver('word') == 32
        return [('stack_call_fixture', text)]

    monkeypatch.setattr(diff_gated_repairs.measured_kmc_stack_and_call, 'variants', repair)
    assert 'stack_call_fixture' in dict(proposals('void f(void) {}', 'f', '', 'mips', KMC, rows, '', resolve=resolve))
    assert 'stack_call_fixture' not in dict(proposals('void f(void) {}', 'f', '', 'mips', EGCS, rows, '', resolve=resolve))


def test_vector_square_repairs_route_to_kmc(monkeypatch):
    from decompnet.app import diff_gated_repairs

    rows = [('r', 'move $v0, $a0', 'move $v0, $a1')]
    monkeypatch.setattr(diff_gated_repairs.measured_kmc_vector_square, 'variants',
                        lambda text, name, header, diff: [('vector_square_fixture', text)])
    assert 'vector_square_fixture' in dict(proposals('void f(void) {}', 'f', '', 'mips', KMC, rows, ''))
    assert 'vector_square_fixture' not in dict(proposals('void f(void) {}', 'f', '', 'mips', EGCS, rows, ''))


def test_byte_and_message_repairs_route_to_kmc(monkeypatch):
    from decompnet.app import diff_gated_repairs

    rows = [('r', 'move $v0, $a0', 'move $v0, $a1')]
    monkeypatch.setattr(diff_gated_repairs.measured_kmc_byte_and_message, 'variants',
                        lambda text, name, header, diff: [('byte_message_fixture', text)])
    assert 'byte_message_fixture' in dict(proposals('void f(void) {}', 'f', '', 'mips', KMC, rows, ''))
    assert 'byte_message_fixture' not in dict(proposals('void f(void) {}', 'f', '', 'mips', EGCS, rows, ''))


def test_packed_bits_repairs_route_to_kmc(monkeypatch):
    from decompnet.app import diff_gated_repairs

    rows = [('r', 'move $v0, $a0', 'move $v0, $a1')]
    monkeypatch.setattr(diff_gated_repairs.measured_kmc_packed_bits, 'variants',
                        lambda text, name, header, diff: [('packed_bits_fixture', text)])
    assert 'packed_bits_fixture' in dict(proposals('void f(void) {}', 'f', '', 'mips', KMC, rows, ''))
    assert 'packed_bits_fixture' not in dict(proposals('void f(void) {}', 'f', '', 'mips', EGCS, rows, ''))


def test_field_pointer_repairs_route_to_kmc(monkeypatch):
    from decompnet.app import diff_gated_repairs

    rows = [('r', 'move $v0, $a0', 'move $v0, $a1')]
    monkeypatch.setattr(diff_gated_repairs.measured_kmc_field_pointer, 'variants',
                        lambda text, name, header, diff: [('field_pointer_fixture', text)])
    assert 'field_pointer_fixture' in dict(proposals('void f(void) {}', 'f', '', 'mips', KMC, rows, ''))
    assert 'field_pointer_fixture' not in dict(proposals('void f(void) {}', 'f', '', 'mips', EGCS, rows, ''))
