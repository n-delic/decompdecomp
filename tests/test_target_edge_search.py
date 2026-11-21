import pytest

from decompnet.app.diff_gated_repairs import proposals


def pair(a,b):
    a,b = a.split('|'),b.split('|')
    assert len(a)==len(b)
    return [(' ' if x==y else 'r',x,y) for x,y in zip(a,b)]


def assembly(text):
    return '\n'.join(line if line.endswith(':') else f'/* {4*i:06X} {0x80001000+4*i:08X} 00000000 */ {line}'
                     for i,line in enumerate(text.split('|')))


EDGE = 'bnel $a0, $zero, .Ltest|addiu $s0, $s0, 1|sw $s0, 0($a1)|bne $s0, $a2, .Lbody|nop'
FLOAT = ('sdc1 $f20, 16($sp)|sdc1 $f22, 24($sp)|jal first|nop|mov.s $f20, $f0|mov.s $f22, $f0|'
         'jal second|nop|bnez $v0, .Lelse|nop|mov.s $f0, $f20|mov.s $f0, $f22')
MULT = 'lhu $v0, 4($a0)|mult $v1, $v0|mflo $v0'
PACKED = '|'.join(f'{op} $t{i}, {off}({base})' for i in range(3)
                  for op,off,base in (('lwl',4*i,'$a0'),('lwr',4*i+3,'$a0'),
                                      ('swl',4*i,'$sp'),('swr',4*i+3,'$sp')))
ZERO = ('mtc1 $zero, $f20|swc1 $f20, 0($a0)|swc1 $f20, 4($a0)|bnez $a1, .Lloop|nop|'
        'swc1 $f20, 16($sp)|jal take|nop')
STORE = 'beql $a0, $zero, .Lstore|andi $v0, $a1, 0xffff|sh $v0, 4($a0)|jr $ra|nop'
KEY = ('srl $v0, $a0, 24|lbu $v1, 0($a1)|sll $v1, $v1, 8|lbu $a2, 1($a1)|'
       'sll $a2, $a2, 16|or $v0, $v0, $v1|or $v0, $v0, $a2')
INTERLEAVE = ('lwc1 $f2, 0($a0)|sh $zero, 4($a0)|sh $zero, 6($a0)|sub.s $f2, $f2, $f0|swc1 $f2, 0($a0)')
ADDRESS = ('addiu $a0, $at, %lo(base)|jal first|nop|nop|addiu $a0, $at, %lo(base)|jal second|nop')

CASES = [
    ('continue_edge_increment',
     'void f(int n,int cond,int *p) { int i; for (i=0;i<n;i++) { if (cond) { continue; } p[i]=1; } }',
     pair(EDGE,EDGE.replace('bnel $a0, $zero,','bnez $a0,').replace('addiu $s0, $s0, 1','andi $v0, $s0, 255')),
     assembly('.Lbody:|'+EDGE.replace('|bne $s0','|.Ltest:|bne $s0')), 'i++; goto repair_check_next;',
     'continue;', 'break;'),
    ('float_call_result_parameter_reuse',
     'float first(float); float second(float); float f(float a,float b) { float r,t; r=first(a); t=second(b); '
     'a=r; if (t>0) return a; return r; }',
     pair(FLOAT,FLOAT.replace('sdc1 $f22, 24($sp)','nop').replace('$f22','$f20')),
     '', 'a = first(a); r = a;', 'second(b)','second(a)'),
    ('narrow_multiply_operand',
     'struct S { unsigned short scale; }; int f(struct S *p,int low) { int v; v=p->scale; v=low*v; return v; }',
     pair(MULT,MULT.replace('mult $v1, $v0','mult $v0, $v1')),
     '', 'unsigned short repair_scale = p->scale;', 'unsigned short scale','unsigned int scale'),
    ('remove_duplicate_unsigned_double_bias',
     'double f(unsigned int u) { double v; v=(double)u; if ((int)u < 0) { v+=4294967296.0; } return v; }',
     pair('cvt.d.w $f0, $f0|bgez $a0, .Ltest|nop|nop|nop|ldc1 $f2, 0($a1)|add.d $f0, $f0, $f2',
          'cvt.d.w $f0, $f0|bgez $a0, .Ltest|ldc1 $f2, 0($a1)|add.d $f0, $f0, $f2|nop|'
          'ldc1 $f2, 0($a1)|add.d $f0, $f0, $f2'),
     '', '(double)(int)u', '4294967296.0','4294967295.0'),
    ('packed_unaligned_record_copy',
     'extern const unsigned char src[20]; void f(void) { unsigned char dst[20]; '
     '*(u32 *)(dst+1)=*(const u32 *)(src+1); *(u32 *)(dst+5)=*(const u32 *)(src+5); '
     '*(u32 *)(dst+9)=*(const u32 *)(src+9); use(dst); }',
     pair(PACKED,PACKED.replace('swl','sw').replace('swr','sh')),
     '', '__attribute__((packed))', 'src[20]','src[10]'),
    ('float_zero_loop_and_call',
     'struct S { float x,y; }; void take(int,int,int,int,float); void f(struct S *p,int n) { int i; '
     'for (i=0;i<n;i++) { p[i].x=0.0f; p[i].y=0.0f; } take(1,2,3,4,0.0f); }',
     pair(ZERO,ZERO.replace('swc1 $f20, 16($sp)','sw $zero, 16($sp)')),
     '', 'take(1,2,3,4,repair_zero)', 'int,int,int,int,float','int,int,int,int,int'),
    ('shared_store_branch_polarity',
     'void f(int x,int n,short *p) { int v; if (x == 0) { v=n&7; goto join; } v=n&3; join: *p=v; }',
     pair(STORE,STORE.replace('beql','bnel').replace('andi $v0, $a1','andi $v0, $a2')),
     assembly(STORE.replace('|sh','|.Lstore:|sh')), 'if (x != 0) { v=n&3;', 'x == 0','x < 0'),
    ('interleaved_packed_key',
     'unsigned int f(unsigned int w,const unsigned char *p) { unsigned int k; '
     'k=(w>>24)|((p[0])<<8)|((p[1])<<16); return k; }',
     pair(KEY,'srl $v0, $a0, 24|lbu $v1, 0($a1)|lbu $a2, 1($a1)|sll $v1, $v1, 8|'
          'sll $a2, $a2, 16|or $v0, $v0, $v1|or $v0, $v0, $a2'),
     '', 'repair_key_1 <<= 8;', '<<16','<<15'),
    ('interleaved_float_field_update',
     'struct S { float x; unsigned short y,z; }; void f(struct S *p,float d) { p->x-=d; p->y=1; p->z=2; }',
     pair(INTERLEAVE,'lwc1 $f2, 0($a0)|sub.s $f2, $f2, $f0|swc1 $f2, 0($a0)|sh $zero, 4($a0)|sh $zero, 6($a0)'),
     '', 'repair_field -= d;', 'p->z=2','p->x=2'),
    ('adjacent_symbol_second_call',
     'extern char neighbor; extern char base; void f(void) { first(&base); second(&base); }',
     pair(ADDRESS,ADDRESS.replace('|nop|addiu $a0, $at, %lo(base)|jal second',
                                   '|sw $s0, 16($sp)|move $a0, $s0|jal second')),
     '', '(unsigned int)&neighbor + 4U', 'extern char neighbor;', 'extern char far;'),
]


def route(source,diff,asm,*,compiler='gcc-2.7.2-kmc',resolve=None,flags=()):
    if resolve is None:
        resolve = {'base':0x80001004,'neighbor':0x80001000,'far':0x80002000}.get
    return proposals(source,'f','','mips',compiler,diff,asm,big_endian=True,resolve=resolve,flags=flags)


@pytest.mark.parametrize('case',CASES,ids=[case[0] for case in CASES])
def test_measured_gate_and_source(case):
    family,source,diff,asm,expected,old,new = case
    found = [result for kind,result in route(source,diff,asm) if kind==family]
    assert len(found)==1 and expected in found[0]
    assert family not in {kind for kind,_ in route(source.replace(old,new),diff,asm)}
    assert family not in {kind for kind,_ in route(source,[(' ',a,a) for _,a,_ in diff],asm)}
    assert family not in {kind for kind,_ in route(source,diff,asm,compiler='ido-5.3')}


def test_packed_copy_respects_strict_aliasing():
    family,source,diff,asm,*_ = CASES[4]
    assert family not in {kind for kind,_ in route(source,diff,asm,flags=('-fstrict-aliasing',))}


def test_neighbor_must_resolve_to_an_equal_address_with_the_offset():
    family,source,diff,asm,*_ = CASES[-1]
    assert family not in {kind for kind,_ in route(source,diff,asm,resolve={'base':0x80001004}.get)}


def test_cast_repairs_reach_measured_kmc_proposals():
    source = '''
extern f32 as_float;
extern s32 as_word;
void f(u8 *base, int select) {
    u8 *p;
    p = base;
    if (select) {
        as_float = (f32)*p;
    } else {
        as_word = (s32)*p;
    }
}
'''
    rows = [('|', 'lwc1 $f0, ($v1)', 'lbu $v0, ($v1)'), ('>', '', 'mtc1 $v0, $f0'),
            ('>', '', 'cvt.s.w $f0, $f0'), ('|', 'lw $v0, ($v1)', 'lbu $v0, ($v1)')]
    candidates = [candidate for family,candidate in route(source,rows,'') if family=='kmc_typed_load']
    assert len(candidates)==3
    assert '*(f32 *)p' in candidates[0] and '*(s32 *)p' in candidates[0]
    assert 'kmc_typed_load' not in {family for family,_ in route(source,rows,'',compiler='ido-5.3')}
    assert 'kmc_typed_load' not in {family for family,_ in route(source,rows,'',flags=('-fstrict-aliasing',))}

    source = 'void f(s32 *p) { *p = (*p | (u16)0x20000000); }'
    rows = [(' ', 'lui $s2, 0x2000', 'lui $s2, 0x2000'), (' ', 'lw $v0, ($a0)', 'lw $v0, ($a0)'),
            ('<', 'or $v0, $v0, $s2', ''), (' ', 'sw $v0, ($a0)', 'sw $v0, ($a0)')]
    assert ('kmc_truncated_literal',source.replace('(u16)','')) in route(source,rows,'')


@pytest.mark.parametrize(('family','source','before','after','following','expected'), [
    ('measured_call_literal','int f(void) { return pick(3); }',
     'addiu $a0, $zero, 3','addiu $a0, $zero, 7','jal pick','pick(7)'),
    ('measured_branch_mask','int f(int x) { if (!(x & 0x10)) return 0; return x; }',
     'andi $v0, $v1, 0x10','andi $v0, $v1, 4','bnez $v0, next','(x & 0x4)'),
    ('measured_byte_offset','void f(char *p) { M2C_FIELD(p,s8 *,1) = 0; }',
     'sb $zero, 1($v0)','sb $zero, ($v0)','jr $ra','M2C_FIELD(p,s8 *,0)'),
])
def test_measured_literals_reach_kmc_proposals(family,source,before,after,following,expected):
    rows = [('i',after,before),(' ',following,following)]
    assert any(kind == family and expected in candidate for kind,candidate in route(source,rows,''))
    assert family not in {kind for kind,_ in route(source,rows,'',compiler='ido-5.3')}


def test_measured_address_binding_uses_the_project_data_symbol_table():
    from decomprepair.symbols import Symbol, SymbolTable

    source = 'extern char D_80001000[]; void f(void) { send(D_80001000); }'
    rows = [('i','lui $a0, %hi(D_80022000)','lui $a0, %hi(D_80001000)'),
            ('i','addiu $a0, $a0, %lo(D_80022000)','addiu $a0, $a0, %lo(D_80001000)')]
    symbols = SymbolTable([Symbol('D_80022000',0x80022000,kind='bss')])
    family='measured_relocation_binding'
    assert (family,source.replace('D_80001000','D_80022000')) in proposals(
        source,'f','','mips','gcc-2.7.2-kmc',rows,'',symbols=symbols)
    assert family not in {kind for kind,_ in proposals(source,'f','','mips','gcc-2.7.2-kmc',rows,'')}
    assert family not in {kind for kind,_ in proposals(source,'f','','mips','ido-5.3',rows,'',symbols=symbols)}


def test_measured_address_add_reaches_the_deep_proposal_path():
    source = 'extern s32 D_800BFF04; u16 *f(int a) { s32 i; i = a; return (u16 *)(D_800BFF04+i*8); }'
    rows = [('r','addu $a1, $v0, $v1','addu $a1, $v1, $v0')]
    assert ('measured_address_add',source.replace('D_800BFF04+i*8','i * 8 + D_800BFF04')) in route(source,rows,'')


def test_measured_callback_prototype_reaches_kmc_proposals():
    source = '''
void f(void *owner) {
    void *record;
    void (*callback)(void *);
    cleanup(owner);
    record = M2C_FIELD(owner, void **, 0x10);
    if (record != NULL) {
        callback = M2C_FIELD(record, void (**)(void *), 0xC);
        if (callback != NULL) callback(record);
    }
}
'''
    rows = pair('jal cleanup|nop|lw $v0, ($s0)|lw $v1, 0x10($v0)|beqz $v1, out|nop|lw $v0, 0xc($v1)|'
                'beqz $v0, out|nop|jalr $v0|nop',
                'jal cleanup|nop|lw $v0, ($s0)|lw $a0, 0x10($v0)|beqz $a0, out|nop|lw $v0, 0xc($a0)|'
                'beqz $v0, out|nop|jalr $v0|nop')
    family = 'measured_callback_without_record'
    expected = source.replace('(void *)','(void)').replace('callback(record)','callback()')
    assert (family,expected) in route(source,rows,'')
    assert family not in {kind for kind,_ in route(source,rows,'',compiler='ido-5.3')}


def test_measured_stack_reservation_ignores_volatile_in_neighboring_source():
    source = "extern volatile int status; int f(void) { int result = 1; return result; }"
    prior = 'addiu $sp, $sp, -0x20|sw $ra, 0x18($sp)|jal work|nop|lw $ra, 0x18($sp)|jr $ra|addiu $sp, $sp, 0x20'
    target = 'addiu $sp, $sp, -0x28|sw $ra, 0x20($sp)|jal work|nop|lw $ra, 0x20($sp)|jr $ra|addiu $sp, $sp, 0x28'
    rows = [(' ' if a==b else 's',a,b) for a,b in zip(target.split('|'),prior.split('|'))]
    choices = dict(route(source,rows,''))
    assert '__repair_stack_padding[8]' in choices['inferred_frame_reservation']
    assert 'inferred_frame_reservation' not in dict(route(source.replace('return result;','return *(volatile int *)&result;'),rows,''))

    shrinking = [(mark,b,a) for mark,a,b in rows]
    excess = source.replace('int result','char frame[8]; int result')
    assert dict(route(excess,shrinking,''))['measured_unused_frame_array'] == excess.replace('char frame[8];','')


def test_measured_direct_return_reaches_kmc_proposals():
    source = 's32 f(void *p) { s32 result = 0; if (p) result = *(s32 *)((char *)p + 0x10); return result; }'
    rows = [('r','lw $v0, 0x10($v1)','lw $v1, 0x10($v0)'),
            (' ','jr $ra','jr $ra'),('|','nop','move $v0, $v1')]
    family = 'measured_direct_load_return'
    expected = source.replace('result = *(s32','return *(s32').replace('return result;','return 0;')
    assert (family,expected) in route(source,rows,'')
    assert family not in dict(route(source,rows,'',compiler='ido-5.3'))


def test_measured_sign_test_reaches_kmc_proposals():
    source = 'int f(void) { s32 status = *(volatile s32 *)0x8000; return (status & 0x80000000U) != 0; }'
    rows = [(' ','jr $ra','jr $ra'),('|','slti $v0, $v0, 0','srl $v0, $v0, 0x1f')]
    family = 'measured_sign_test'
    assert (family,source.replace('& 0x80000000U','>> 31')) in route(source,rows,'')
    assert family not in dict(route(source,rows,'',compiler='ido-5.3'))


def test_measured_memory_order_reaches_kmc_proposals():
    source = 'void f(void *p) { f32 x,y; (x=M2C_FIELD(p,f32 *,4), y=M2C_FIELD(p,f32 *,8), use(x,y)); }'
    rows = [('r', 'lwc1 $f2, 8($s0)', 'lwc1 $f0, 4($s0)'),
            ('r', 'lwc1 $f0, 4($s0)', 'lwc1 $f2, 8($s0)')]
    family = 'measured_comma_load_order'
    assert family in dict(route(source, rows, ''))
    assert family not in dict(route(source, rows, '', compiler='ido-5.3'))


def test_measured_shared_exit_flag_reaches_kmc_proposals():
    source = '''
void f(void *p) {
    int flag;
    int work = 0;
    if (p) { flag = 0; goto out; }
    flag = work;
out:
    if (flag == 0) finish(p);
}
'''
    rows = [(' ', 'bne $v1, $v0, out', 'bne $v1, $v0, out'),
            ('r', 'move $v0, $a1', 'move $v0, $zero')]
    family = 'measured_shared_exit_flag'
    assert (family, source.replace('int flag;', 'int flag = 0;')) in route(source, rows, '')
    assert family not in dict(route(source, rows, '', compiler='ido-5.3'))


def test_measured_float_table_scale_reaches_kmc_deep_search():
    source = 'extern M2C_UNK table;\nf32 f(s32 arg) {\n    return *(&table + (((arg + 1) & 0xFF) * 4));\n}'
    rows = [('i', 'sll $a0, $a0, 2', 'sll $a0, $a0, 4'),
            ('o', 'lwc1 $f0, %lo(table)($at)', 'cvt.s.w $f0, $f0')]
    assert 'measured_float_table_scale' in dict(route(source, rows, ''))
    assert 'measured_float_table_scale' not in dict(route(source, rows, '', compiler='ido-5.3'))


def test_uncached_read_repair_is_routed_for_measured_kmc_mismatches(monkeypatch):
    from decomprepair import uncached_read_repairs

    calls = []

    def rewrite(source, name, asm, resolve):
        calls.append((source, name, asm))
        return 'repaired candidate'

    monkeypatch.setattr(uncached_read_repairs, 'rewrite', rewrite)
    rows = [('r', 'lw $v1, ($v0)', 'lw $v0, ($v1)')]
    assert ('measured_uncached_word_read', 'repaired candidate') in route('void f(void) {}', rows, 'asm')
    assert ('measured_uncached_word_read', 'repaired candidate') not in route(
        'void f(void) {}', rows, 'asm', compiler='ido-5.3')
    assert calls == [('void f(void) {}', 'f', 'asm')]


def test_callback_loop_repair_uses_measured_kmc_proposal_path(monkeypatch):
    from decomprepair import measured_callback_loop

    calls = []

    def rewrite(source, name, asm, header, resolve):
        calls.append((source, name, asm, header))
        return 'repaired callback loop'

    monkeypatch.setattr(measured_callback_loop, 'rewrite', rewrite)
    rows = [('r', 'lw $v1, ($v0)', 'lw $v0, ($v1)')]
    assert ('measured_callback_loop', 'repaired callback loop') in route('void f(void) {}', rows, 'asm')
    assert ('measured_callback_loop', 'repaired callback loop') not in route(
        'void f(void) {}', rows, 'asm', compiler='ido-5.3')
    assert calls == [('void f(void) {}', 'f', 'asm', '')]


def test_mmio_polling_repair_uses_measured_kmc_proposal_path(monkeypatch):
    from decomprepair import measured_mmio_polling

    calls = []

    def rewrite(source, name, asm, header):
        calls.append((source, name, asm, header))
        return 'repaired polling'

    monkeypatch.setattr(measured_mmio_polling, 'rewrite', rewrite)
    rows = [('r', 'lw $v1, ($v0)', 'lw $v0, ($v1)')]
    assert ('measured_mmio_polling', 'repaired polling') in route('void f(void) {}', rows, 'asm')
    assert ('measured_mmio_polling', 'repaired polling') not in route(
        'void f(void) {}', rows, 'asm', compiler='ido-5.3')
    assert calls == [('void f(void) {}', 'f', 'asm', '')]


def test_shared_cursor_branch_uses_measured_kmc_proposal_path(monkeypatch):
    from decomprepair import likely_branch_cursor

    monkeypatch.setattr(likely_branch_cursor, 'rewrite', lambda source, name, asm, header: 'shared advance')
    rows = [('r', 'beql $v0, $zero, .Ljoin', 'bnez $v0, .Lactive')]
    assert ('likely_branch_cursor', 'shared advance') in route('void f(void) {}', rows, 'asm')
    assert 'likely_branch_cursor' not in dict(route('void f(void) {}', rows, 'asm', compiler='ido-5.3'))


def test_relocated_byte_table_is_proposed_only_for_kmc(monkeypatch):
    from decomprepair import relocated_byte_table

    calls = []

    def rewrite(source, name, asm, header, resolve):
        calls.append((source, name, asm, header, resolve))
        return 'byte table candidate'

    monkeypatch.setattr(relocated_byte_table, 'rewrite', rewrite)
    rows = [('i', 'lbu $v0, ($v0)', 'lbu $v0, 3($v0)')]
    resolve = {'slot': 0x80001000}.get
    assert ('relocated_byte_table', 'byte table candidate') in route('void f(void) {}', rows, 'asm', resolve=resolve)
    assert 'relocated_byte_table' not in dict(route('void f(void) {}', rows, 'asm', compiler='ido-5.3'))
    assert calls == [('void f(void) {}', 'f', 'asm', '', resolve)]


def test_frame_reservation_prefers_a_new_scope_when_callee_keeps_local_slot():
    source = 'void f(void) { short value; value = 7; send(0, &value); }'
    target = ('addiu $sp, $sp, -40|addiu $a1, $sp, 16|sw $ra, 32($sp)|jal send|'
              'sh $v0, 16($sp)|lw $ra, 32($sp)|jr $ra|addiu $sp, $sp, 40')
    old = target.replace('-40', '-32').replace('32($sp)', '24($sp)').replace('$sp, 40', '$sp, 32')
    rows = [(' ' if a == b else 's', a, b) for a, b in zip(target.split('|'), old.split('|'))]
    options = [text for family, text in route(source, rows, '') if family == 'inferred_frame_reservation']

    assert len(options) == 2
    assert options[0].index('send(0, &value);') < options[0].index('char __repair_stack_padding[8];')
    assert options[1].index('char __repair_stack_padding[8];') < options[1].index('send(0, &value);')
    assert 'inferred_frame_reservation' not in dict(route(source, rows, '', compiler='ido-5.3'))


def test_aligned_stack_read_is_proposed_only_for_kmc(monkeypatch):
    from decomprepair import aligned_stack_read

    calls = []

    def rewrite(source, name, asm):
        calls.append((source, name, asm))
        return 'aligned stack candidate'

    monkeypatch.setattr(aligned_stack_read, 'rewrite', rewrite)
    rows = [('s', 'addiu $s0, $sp, 39', 'addiu $s0, $sp, 24')]
    assert ('aligned_stack_read', 'aligned stack candidate') in route('void f(void) {}', rows, 'asm')
    assert 'aligned_stack_read' not in dict(route('void f(void) {}', rows, 'asm', compiler='ido-5.3'))
    assert calls == [('void f(void) {}', 'f', 'asm')]


def test_measured_wrapper_and_copy_loop_rules_reach_kmc_proposals(monkeypatch):
    from decomprepair import loop_storage_shapes, passthrough_argument_repairs

    monkeypatch.setattr(passthrough_argument_repairs, 'missing_entry_argument', lambda *args: 'wrapper')
    monkeypatch.setattr(loop_storage_shapes, 'measured_copy_setup', lambda *args: 'copy loop')
    rows = [('r', 'lw $a1, ($v0)', 'lw $a0, ($v0)')]
    kmc = route('void f(void) {}', rows, 'asm')
    assert ('entry_wrapper_argument', 'wrapper') in kmc
    assert ('measured_copy_loop_setup', 'copy loop') in kmc
    ido = dict(route('void f(void) {}', rows, 'asm', compiler='ido-5.3'))
    assert 'entry_wrapper_argument' not in ido
    assert 'measured_copy_loop_setup' not in ido


def test_measured_relocated_global_update_reaches_kmc_proposals(monkeypatch):
    from decomprepair import relocated_global_update

    monkeypatch.setattr(relocated_global_update, 'rewrite', lambda *args: 'direct global')
    rows = [('r', 'lui $v0, %hi(state)', 'lui $a0, %hi(state)')]
    assert ('relocated_global_update', 'direct global') in route('void f(void) {}', rows, 'asm')
    assert 'relocated_global_update' not in dict(route('void f(void) {}', rows, 'asm', compiler='ido-5.3'))


def test_measured_packed_matrix_conversion_reaches_kmc_proposals(monkeypatch):
    from decomprepair import packed_matrix_conversion

    monkeypatch.setattr(packed_matrix_conversion, 'rewrite', lambda *args: 'matrix candidate')
    rows = [('r', 'sw $a0, ($t1)', 'sw $a0, ($t3)')]
    assert ('packed_matrix_conversion', 'matrix candidate') in route('void f(void) {}', rows, 'asm')
    assert 'packed_matrix_conversion' not in dict(route('void f(void) {}', rows, 'asm', compiler='ido-5.3'))


def test_measured_byte_cursor_postincrements_reach_kmc_proposals(monkeypatch):
    from decomprepair import byte_cursor_postincrements

    monkeypatch.setattr(byte_cursor_postincrements, 'rewrite', lambda *args: 'byte cursor candidate')
    rows = [('s', 'addiu $v0, $a1, 1', 'addiu $v0, $a1, 4')]
    assert ('byte_cursor_postincrements', 'byte cursor candidate') in route('void f(void) {}', rows, 'asm')
    assert 'byte_cursor_postincrements' not in dict(route('void f(void) {}', rows, '', compiler='ido-5.3'))


def test_measured_allocator_and_halfword_updates_reach_kmc(monkeypatch):
    from decomprepair import aligned_allocator_request, ordered_halfword_updates

    monkeypatch.setattr(aligned_allocator_request, 'rewrite', lambda *args: 'allocator')
    monkeypatch.setattr(ordered_halfword_updates, 'rewrite', lambda *args: 'halfword')
    rows = [('r', 'lw $v1, 8($a2)', 'lw $a0, 8($a2)')]
    kmc = dict(route('void f(void) {}', rows, 'asm'))
    assert kmc['aligned_allocator_request'] == 'allocator'
    assert kmc['ordered_halfword_updates'] == 'halfword'
    ido = dict(route('void f(void) {}', rows, 'asm', compiler='ido-5.3'))
    assert 'aligned_allocator_request' not in ido
    assert 'ordered_halfword_updates' not in ido


def test_measured_indexed_field_cursor_reaches_kmc_proposals(monkeypatch):
    from decomprepair import indexed_field_cursor

    monkeypatch.setattr(indexed_field_cursor, 'rewrite', lambda *args: 'indexed fields')
    rows = [('i', 'sw $zero, 0x58($s2)', 'sw $zero, 0x40($s2)')]
    assert ('indexed_field_cursor', 'indexed fields') in route('void f(void) {}', rows, 'asm')
    assert 'indexed_field_cursor' not in dict(route('void f(void) {}', rows, 'asm', compiler='ido-5.3'))


def test_measured_split_dispatch_zero_case_reaches_kmc_proposals(monkeypatch):
    from decomprepair import split_dispatch_zero_case

    monkeypatch.setattr(split_dispatch_zero_case, 'rewrite', lambda *args: 'flat dispatch')
    rows = [('i', 'beq $v1, $v0, .Lprobe', 'beq $v1, $v0, .Lother')]
    assert ('split_dispatch_zero_case', 'flat dispatch') in route('void f(void) {}', rows, 'asm')
    assert 'split_dispatch_zero_case' not in dict(route('void f(void) {}', rows, 'asm', compiler='ido-5.3'))


def test_measured_relocated_countdown_reaches_kmc_proposals(monkeypatch):
    from decomprepair import relocated_global_update

    monkeypatch.setattr(relocated_global_update, 'rewrite_countdown', lambda *args: 'direct countdown')
    rows = [('i', 'sw $v0, %lo(counter)($at)', 'sw $v0, ($a0)')]
    assert ('relocated_countdown', 'direct countdown') in route('void f(void) {}', rows, 'asm')
    assert 'relocated_countdown' not in dict(route('void f(void) {}', rows, 'asm', compiler='ido-5.3'))


def test_measured_stack_read_retry_reaches_kmc_proposals(monkeypatch):
    from decomprepair import stack_read_retry

    monkeypatch.setattr(stack_read_retry, 'rewrite', lambda *args: 'stack read retry')
    rows = [('s', 'addiu $sp, $sp, -56', 'addiu $sp, $sp, -32')]
    assert ('stack_read_retry', 'stack read retry') in route('void f(void) {}', rows, 'asm')
    assert 'stack_read_retry' not in dict(route('void f(void) {}', rows, 'asm', compiler='ido-5.3'))


def test_measured_stack_read_inline_compare_reaches_kmc_proposals(monkeypatch):
    from decomprepair import stack_read_retry

    monkeypatch.setattr(stack_read_retry, 'rewrite_inline', lambda *args: 'inline stack compare')
    rows = [('s', 'addiu $sp, $sp, -56', 'addiu $sp, $sp, -32')]
    assert ('stack_read_inline_compare', 'inline stack compare') in route('void f(void) {}', rows, 'asm')
    assert 'stack_read_inline_compare' not in dict(route('void f(void) {}', rows, 'asm', compiler='ido-5.3'))


def test_float_matrix_identity_reaches_kmc_proposals(monkeypatch):
    from decomprepair import float_matrix_identity

    monkeypatch.setattr(float_matrix_identity, 'rewrite', lambda *args: 'floating-point diagonal')
    rows = [('i', 'swc1 $f0, ($a3)', 'sw $t0, ($a3)')]
    assert ('float_matrix_identity', 'floating-point diagonal') in route('void f(void) {}', rows, 'asm')
    assert 'float_matrix_identity' not in dict(route('void f(void) {}', rows, 'asm', compiler='ido-5.3'))


def test_field_snapshot_copies_reach_kmc_proposals(monkeypatch):
    from decomprepair import field_snapshot_copies

    monkeypatch.setattr(field_snapshot_copies, 'rewrite', lambda *args: 'staged halfwords')
    rows = [('s', 'lhu $v1, 0x4($v0)', 'lhu $v1, 0x6($v0)')]
    assert ('field_snapshot_copies', 'staged halfwords') in route('void f(void) {}', rows, 'asm')
    assert 'field_snapshot_copies' not in dict(route('void f(void) {}', rows, 'asm', compiler='ido-5.3'))


def test_short_leaf_repairs_reach_kmc_proposals(monkeypatch):
    from decomprepair import global_pointer_local_repairs, shared_return_repairs

    monkeypatch.setattr(global_pointer_local_repairs, 'rewrite_linked_insert', lambda *args: 'snapshot and link')
    monkeypatch.setattr(shared_return_repairs, 'rewrite_global_guard', lambda *args: 'early zero')
    rows = [('r', 'lw $v0, 0x10($v1)', 'lw $v1, 0x10($v0)')]
    kmc = dict(route('void f(void) {}', rows, 'asm'))
    assert kmc['linked_insert'] == 'snapshot and link'
    assert kmc['guarded_global_return'] == 'early zero'
    ido = dict(route('void f(void) {}', rows, 'asm', compiler='ido-5.3'))
    assert 'linked_insert' not in ido
    assert 'guarded_global_return' not in ido


def test_kmc_proposes_an_unsigned_array_for_a_single_byte_load_mismatch():
    source = 'extern char data[];\ns32 f(s32 i) { return *(data + i); }'
    rows = [('|', 'lbu $v0, %lo(data)($at)', 'lb $v0, %lo(data)($at)')]
    assert dict(route(source, rows, 'asm'))['kmc_unsigned_byte_array'] == source.replace('char', 'unsigned char')
    assert 'kmc_unsigned_byte_array' not in dict(route(source, rows, 'asm', compiler='ido-5.3'))


def test_kmc_proposes_a_byte_offset_from_the_target_relocations():
    source = 'extern M2C_UNK state;\nvoid f(void) { g(&state + 24); }'
    rows = [('i', 'lui $a0, %hi(state+0x18)', 'lui $a0, %hi(state+0x60)')]
    asm = 'lui $a0, %hi(state + 0x18)\naddiu $a0, $a0, %lo(state + 0x18)'
    expected = source.replace('&state', '(char *)&state')
    assert dict(route(source, rows, asm))['byte_scaled_global_offset'] == expected
    assert 'byte_scaled_global_offset' not in dict(route(source, rows, asm, compiler='ido-5.3'))


def test_kmc_preserves_byte_stride_when_loading_an_unsigned_halfword():
    source = 'extern char data[];\ns32 f(s32 i) { return *(data + i); }'
    rows = [('|', 'lhu $v0, %lo(data)($at)', 'lb $v0, %lo(data)($at)')]
    expected = source.replace('*(data', '*(u16 *)(data')
    assert dict(route(source, rows, 'asm'))['kmc_unsigned_halfword_array_load'] == expected
    assert 'kmc_unsigned_halfword_array_load' not in dict(route(source, rows, 'asm', compiler='ido-5.3'))


def test_kmc_proposes_staging_a_shift_and_mask_after_a_call():
    source = ('u32 status();\ns32 f(void) { s32 result; result = 0; '
              'if (enabled) { result = (status() >> 2) & 1; } return result & 0xFF; }')
    rows = [
        ('r', 'srl $v0, $v0, 2', 'srl $v1, $v0, 2'),
        ('r', 'andi $v1, $v0, 1', 'andi $v1, $v1, 1'),
    ]
    assert 'measured_staged_shift_mask' in dict(route(source, rows, 'asm'))
    assert 'measured_staged_shift_mask' not in dict(route(source, rows, 'asm', compiler='ido-5.3'))


def test_kmc_proposes_stack_buffer_fills_and_direct_global_reloads(monkeypatch):
    from decomprepair import relocated_global_update, stack_fill_drafts, stack_header_buffer

    monkeypatch.setattr(stack_fill_drafts, 'rewrite', lambda *args: 'indexed buffer')
    monkeypatch.setattr(stack_fill_drafts, 'rewrite_ascending', lambda *args: 'ascending buffer')
    monkeypatch.setattr(stack_header_buffer, 'rewrite', lambda *args: 'serialized header')
    monkeypatch.setattr(relocated_global_update, 'rewrite_reload_wrapper', lambda *args: 'direct reload')
    rows = [('s', 'addiu $sp, $sp, -48', 'addiu $sp, $sp, -32')]
    kmc = dict(route('void f(void) {}', rows, 'asm'))
    assert kmc['descending_stack_fill'] == 'indexed buffer'
    assert kmc['ascending_stack_fill'] == 'ascending buffer'
    assert kmc['stack_header_buffer'] == 'serialized header'
    assert kmc['direct_global_reload'] == 'direct reload'
    ido = dict(route('void f(void) {}', rows, 'asm', compiler='ido-5.3'))
    assert 'descending_stack_fill' not in ido
    assert 'ascending_stack_fill' not in ido
    assert 'stack_header_buffer' not in ido
    assert 'direct_global_reload' not in ido


def test_kmc_proposes_address_range_returns_and_linked_list_transfers(monkeypatch):
    from decomprepair import direct_mapped_address, global_pointer_local_repairs

    monkeypatch.setattr(direct_mapped_address, 'rewrite', lambda *args: 'separate returns')
    monkeypatch.setattr(global_pointer_local_repairs, 'rewrite_list_transfer', lambda *args: 'retained node')
    rows = [('r', 'and $v0, $a0, $v1', 'and $a1, $a0, $v1')]
    kmc = dict(route('void f(void) {}', rows, 'asm'))
    assert kmc['direct_mapped_address'] == 'separate returns'
    assert kmc['list_node_transfer'] == 'retained node'
    ido = dict(route('void f(void) {}', rows, 'asm', compiler='ido-5.3'))
    assert 'direct_mapped_address' not in ido
    assert 'list_node_transfer' not in ido

def test_kmc_proposes_unsigned_literal_stores_from_matching_byte_destinations():
    source = 'extern s8 flag;\nvoid f(void) {\n    flag = 0xFF;\n}'
    instructions = ('lui $at, %hi(flag)', 'sb $v0, %lo(flag)($at)')
    rows = [('i', 'addiu $v0, $zero, 0xff', 'addiu $v0, $zero, -1'),
            *((' ', instruction, instruction) for instruction in instructions)]
    assert dict(route(source, rows, 'asm'))['kmc_unsigned_byte_store'] == source.replace('flag =', '*(u8 *)&flag =')
    assert 'kmc_unsigned_byte_store' not in dict(route(source, rows, 'asm', compiler='ido-5.3'))


def test_kmc_proposes_byte_flag_orientation_and_float_table_types(monkeypatch):
    from decomprepair import guard_inversion_repairs, kmc_cast_repairs

    monkeypatch.setattr(guard_inversion_repairs, 'rewrite_byte_toggle', lambda *args: 'swap byte flag arms')
    monkeypatch.setattr(kmc_cast_repairs, 'rewrite_float_table', lambda *args: 'typed table')
    rows = [('s', 'sll $a0, $a0, 2', 'sll $a0, $a0, 4')]
    kmc = dict(route('void f(void) {}', rows, 'asm'))
    assert kmc['byte_flag_branch_orientation'] == 'swap byte flag arms'
    assert kmc['direct_float_table_load'] == 'typed table'
    ido = dict(route('void f(void) {}', rows, 'asm', compiler='ido-5.3'))
    assert 'byte_flag_branch_orientation' not in ido
    assert 'direct_float_table_load' not in ido


def test_kmc_word_index_scales_only_once_at_a_measured_table_load():
    source = 'extern M2C_UNK table;\ns32 f(s32 i) { return *(&table + (i * 4)); }'
    instructions = ('lui $at, %hi(table)', 'addu $at, $at, $a0', 'lw $v0, %lo(table)($at)')
    rows = [('i', 'sll $a0, $a0, 2', 'sll $a0, $a0, 4'),
            *((' ', instruction, instruction) for instruction in instructions)]
    candidate = dict(route(source, rows, 'asm'))['measured_duplicate_word_scale']
    assert 'extern M2C_UNK table[];' in candidate
    assert 'return table[i];' in candidate
    assert 'measured_duplicate_word_scale' not in dict(route(source, rows, 'asm', compiler='ido-5.3'))


def test_kmc_prioritizes_a_live_argument_when_the_other_call_registers_shift():
    source = 'int call();\nvoid f(int input, void *obj) { call(M2C_FIELD(obj, s32 *, 8)); }'
    rows = [(' ', 'jal call', 'jal call'), ('r', 'lw $a1, 8($a1)', 'lw $a0, 8($a1)')]
    asm = 'jal call\nlw $a1, 8($a1)'
    assert 'call(input, M2C_FIELD' in dict(route(source, rows, asm))['passthrough_argument']
    assert 'passthrough_argument' not in dict(route(source, rows, 'move $a0, $zero\n' + asm))


def test_kmc_prioritizes_existing_buffer_sizes_for_stack_only_growth():
    asm = 'addiu $sp, $sp, -48\nsw $ra, 40($sp)'
    source = 'void f(void) {\n    M2C_UNK sp18;\n    write_bytes(&sp18);\n}'
    rows = [('s', 'addiu $sp, $sp, -48', 'addiu $sp, $sp, -32'),
            ('s', 'sw $ra, 40($sp)', 'sw $ra, 24($sp)'),
            ('s', 'lw $ra, 40($sp)', 'lw $ra, 24($sp)'),
            ('s', 'addiu $sp, $sp, 48', 'addiu $sp, $sp, 32')]
    proposals = dict(route(source, rows, asm))
    assert 'u8 sp18[0x10];' in proposals['stack_object_size']
    assert list(proposals).index('stack_object_size') < list(proposals).index('inferred_frame_reservation')
    assert 'stack_object_size' not in dict(route(source, rows + [('r', 'move $a0, $a1', 'move $a0, $a2')], asm))
    assert 'stack_object_size' not in dict(route(source, [(m, b, a) for m, a, b in rows], asm))


def test_kmc_preserves_byte_stride_when_normalization_clears_the_high_relocation_difference():
    source = ('extern M2C_UNK D_80001230;\n'
              'u8 f(s32 i) { return *(&D_80001230 + ((i & 65535) * 100)); }')
    rows = [
        ('i', 'sll $a0, $a0, 2', 'sll $a0, $a0, 4'),
        ('i', 'lui $at, %hi(D_80001230)', 'lui $at, %hi(D_80001233)'),
        (' ', 'addu $at, $at, $a0', 'addu $at, $at, $a0'),
        ('i', 'lbu $v0, %lo(D_80001230)($at)', 'lbu $v0, %lo(D_80001233)($at)'),
    ]
    family = 'measured_relocated_array_scale'
    actual = dict(route(source, rows, 'asm'))[family]
    assert 'extern u8 D_80001230[];' in actual
    assert 'D_80001230[(i & 65535) * 100]' in actual
    assert family not in dict(route(source, rows, 'asm', compiler='ido-5.3'))


def test_kmc_repairs_repeated_word_table_scaling_with_other_differences():
    source = ('extern M2C_UNK table;\n'
              's32 f(s32 i, s32 j) { return *(&table + (i * 4)) + *(&table + (j * 4)); }')
    instructions = ('lui $at, %hi(table)', 'addu $at, $at, $a0', 'lw $v0, %lo(table)($at)')
    access = [('i', 'sll $a0, $a0, 2', 'sll $a0, $a0, 4'),
              *((' ', instruction, instruction) for instruction in instructions)]
    rows = [*access, ('r', 'move $a0, $s2', 'move $a0, $s3'), *access]
    family = 'measured_repeated_word_table'
    candidate = dict(route(source, rows, 'asm'))[family]
    assert 'return table[i] + table[j];' in candidate
    assert family not in dict(route(source, rows, 'asm', compiler='ido-5.3'))


def test_kmc_uses_relocated_string_only_when_the_rom_confirms_the_literal():
    source = 'void f(void) { format(out, "%s %s", a, b); }'
    rows = [
        ('i', 'lui $a1, %hi(D_80001000)', 'lui $a1, %hi(?local)'),
        ('i', 'addiu $a1, $a1, %lo(D_80001000)', 'addiu $a1, $a1, %lo(?local)'),
    ]
    family = 'verified_rom_string_symbol'

    def check(data):
        return dict(proposals(source, 'f', '', 'mips', 'gcc-2.7.2-kmc', rows, '',
                              read_data=lambda symbol, width: data[:width] if symbol == 'D_80001000' else None))

    assert 'format(out, D_80001000,' in check(b'%s %s\0')[family]
    assert family not in check(b'%d %s\0')


def test_measured_earlier_load_uses_the_project_resolver_for_field_aliases():
    source = """void f(void *arg) {
    void *p;
    p = lookup(arg);
    M2C_FIELD(&base, void **, 4) = p;
    M2C_FIELD(p, s32 *, 16) = (s32) (M2C_FIELD(p, s32 *, 16) | 4);
}"""
    load = 'lw $v1, 16($v0)'
    rows = [
        (' ', 'jal lookup', 'jal lookup'),
        (' ', 'nop', 'nop'),
        ('o', load, ''),
        (' ', 'lui $at, %hi(field)', 'lui $at, %hi(field)'),
        (' ', 'sw $v0, %lo(field)($at)', 'sw $v0, %lo(field)($at)'),
        ('o', '', load),
        (' ', 'ori $v1, $v1, 4', 'ori $v1, $v1, 4'),
        (' ', 'sw $v1, 16($v0)', 'sw $v1, 16($v0)'),
    ]
    family = 'measured_earlier_word_load'
    actual = dict(route(source, rows, 'asm', resolve={'base': 0x1000, 'field': 0x1004}.get))[family]
    assert actual.index('__repair_before_publish = ') < actual.index('M2C_FIELD(&base')
    assert family not in dict(route(source, rows, 'asm', resolve={'base': 0x1000, 'field': 0x1008}.get))


def test_kmc_repairs_a_byte_cursor_using_the_measured_halfword_loop():
    source = """int f(void *record) {
    char *cursor;
    cursor = (*(u16 **)((char *)record + 4));
    do {
        use(*(u16 *)cursor);
        cursor += 3;
    } while (again());
}"""
    rows = [
        (' ', 'move $s4, $a0', 'move $s4, $a0'),
        (' ', 'lhu $a0, ($s4)', 'lhu $a0, ($s4)'),
        ('|', 'bnez $v0, 4 [bytes 1440fffe]', 'bnez $v0, 4 [bytes 1440fffd]'),
        ('i', 'addiu $s4, $s4, 6', 'addiu $s4, $s4, 3'),
    ]
    family = 'measured_halfword_byte_cursor'
    assert 'cursor += 6;' in dict(route(source, rows, 'asm'))[family]
    assert family not in dict(route(source, rows, 'asm', compiler='ido-5.3'))


def test_short_halfword_leaf_repair_reaches_kmc_deep_search():
    source = 'void f(void) { M2C_FIELD(&state, s16 *, 4) = 0x8001; }'
    asm_rows = [
        ('|', 'ori $v0, $zero, 0x8001', 'addiu $v0, $zero, -32767'),
        (' ', 'lui $at, %hi(state + 4)', 'lui $at, %hi(state + 4)'),
        (' ', 'jr $ra', 'jr $ra'),
        (' ', 'sh $v0, %lo(state + 4)($at)', 'sh $v0, %lo(state + 4)($at)'),
    ]
    asm = '\n'.join(target for _, target, _ in asm_rows)
    key = 'measured_unsigned_halfword_store'
    assert dict(route(source, asm_rows, asm))[key] == source.replace('s16', 'u16')
    assert key not in dict(route(source, asm_rows, asm, compiler='ido-5.3'))


def test_kmc_recovers_a_missing_formal_argument_from_shifted_delay_slot_setup():
    source = 'M2C_UNK lookup(void *); /* extern */\nvoid f(void) { lookup(&object); }'
    rows = [(' ', 'jal lookup', 'jal lookup'), ('r', 'move $a1, $s0', 'move $a0, $s0')]
    asm = 'jal lookup\nmove $a1, $s0'
    fixed = dict(route(source, rows, asm))['passthrough_argument']
    assert 'f(s32 __repair_incoming)' in fixed
    assert 'lookup(__repair_incoming, &object);' in fixed
    assert 'passthrough_argument' not in dict(route(source, rows, 'move $a0, $zero\n' + asm))


def test_measured_record_extent_runs_only_for_frame_only_growth():
    source = '''void f(void) {
    s16 sp10;
    use(&sp10);
    sp10 = 3;
}'''
    rows = [
        ('s', 'addiu $sp, $sp, -40', 'addiu $sp, $sp, -32'),
        ('s', 'sw $ra, 36($sp)', 'sw $ra, 28($sp)'),
        (' ', 'jal use', 'jal use'),
        (' ', 'addiu $a0, $sp, 16', 'addiu $a0, $sp, 16'),
        (' ', 'sh $v0, 16($sp)', 'sh $v0, 16($sp)'),
        ('s', 'lw $ra, 36($sp)', 'lw $ra, 28($sp)'),
        (' ', 'jr $ra', 'jr $ra'),
        ('s', 'addiu $sp, $sp, 40', 'addiu $sp, $sp, 32'),
    ]
    asm = '\n'.join(target for _, target, _ in rows)
    candidate = dict(route(source, rows, asm))['stack_record_extent']
    assert 'u8 __repair_record_bytes[18]' in candidate
    changed = rows[:4] + [('s', 'sh $v0, 16($sp)', 'sh $v0, 18($sp)')] + rows[5:]
    assert 'stack_record_extent' not in dict(route(source, changed, asm))


def test_ido_71_routes_the_measured_short_call():
    source = '''int send(int, int, int); /* extern */
void f(int base, int mode) {
    send(mode, base + 8, base);
}
'''
    rows = [
        ('r', 'move $a3, $a0', 'move $a2, $a0'),
        (' ', 'sw $ra, 0x14($sp)', 'sw $ra, 0x14($sp)'),
        (' ', 'move $a0, $a1', 'move $a0, $a1'),
        (' ', 'jal send', 'jal send'),
        ('r', 'addiu $a1, $a3, 8', 'addiu $a1, $a2, 8'),
    ]
    fixed = dict(route(source, rows, '', compiler='ido-7.1'))
    assert 'send(mode, base + 8);' in fixed['drop_duplicate_call_argument']
    assert 'drop_duplicate_call_argument' not in dict(route(source, rows, '', compiler='ido-5.3'))


def test_combined_frame_and_call_repair_reaches_measured_search():
    source = '''int get();
void f(int context, int value) {
    get(value);
}
'''
    rows = [
        ('s', 'addiu $sp, $sp, -32', 'addiu $sp, $sp, -24'),
        ('s', 'sw $ra, 24($sp)', 'sw $ra, 16($sp)'),
        (' ', 'jal get', 'jal get'),
        ('r', 'move $a1, $a1', 'move $a0, $a1'),
        ('s', 'lw $ra, 24($sp)', 'lw $ra, 16($sp)'),
        (' ', 'jr $ra', 'jr $ra'),
        ('s', 'addiu $sp, $sp, 32', 'addiu $sp, $sp, 24'),
    ]
    asm = '\n'.join(row[1] for row in rows)
    fixed = dict(route(source, rows, asm))['frame_reservation_with_forwarded_argument']
    assert 'get(context, value);' in fixed and 'volatile u64 scratch;' in fixed


def test_ido_routes_a_measured_global_byte_decrement():
    source = 'extern u8 timer;\nvoid f(void) { timer -= 8; }\n'
    target = ['lui $v1, %hi(timer)', 'addiu $v1, $v1, %lo(timer)', 'lbu $v0, 0($v1)',
              'addiu $v0, $v0, -8', 'jr $ra', 'sb $v0, 0($v1)']
    asm = '\n'.join(f'/* {i*4:06X} {0x80000000+i*4:08X} 00000000 */ {line}'
                    for i, line in enumerate(target))
    rows = [(' ', line, line) if line == 'jr $ra' else ('r', line, 'other') for line in target]
    fixed = dict(route(source, rows, asm, compiler='ido-5.3'))
    assert '__repair_byte_value -= 8;' in fixed['staged_global_byte_decrement']
    assert 'staged_global_byte_decrement' not in dict(route(source, rows, asm, compiler='ido-7.1'))
