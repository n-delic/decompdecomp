from types import SimpleNamespace

from decompnet.app import allocation_search, callee_frame_evidence, candidate_diagnostics, switch_tables
from decompnet.rom.link import DataMismatch, LinkResult


def assembly(*lines):
    return '\n'.join(f'/* {i*4:06X} {0x80001000+i*4:08X} 00000000 */ {line}' for i,line in enumerate(lines))


def test_jump_table_diff_has_case_numbers_and_keeps_fallback_for_ambiguous_tables():
    asm = assembly('addiu $v1, $a0, -2','sltiu $v0, $v1, 3','beqz $v0, .Ldefault',
                   'sll $v0, $v1, 2','lui $at, %hi(jtbl_80002000)','addu $at, $at, $v0',
                   'lw $v0, %lo(jtbl_80002000)($at)','jr $v0','nop')
    addr, one, two = 0x80002000, 0x80001028, 0x80001030
    pack = lambda words: b''.join(w.to_bytes(4,'big') for w in words)
    target, candidate = pack([one,two,two]), pack([two,one,two])
    net = SimpleNamespace(is_rom=lambda _:True, reader=lambda *_:lambda a,n:target,
                          symbols=lambda _:SimpleNamespace(resolve=lambda symbol:addr))
    proj = {'id':1,'arch':'mips','big_endian':True}
    fn = {'asm':asm,'vram':0x80001000,'size':0x80}
    result = LinkResult(None, False, instructions_exact=True, data_mismatches=[
        DataMismatch(addr,target,candidate,frozenset({0,4,8}))])
    rows = switch_tables.differences(net,proj,fn,result)
    assert rows == [{'table':addr,'case':2,'target':one,'candidate':two},
                    {'table':addr,'case':3,'target':two,'candidate':one}]
    assert '2 | 0x80001028 | 0x80001030' in candidate_diagnostics.text({'jump_tables':rows})
    assert not switch_tables.differences(net,proj,dict(fn,asm=asm.replace('jr $v0','jr $v1')),result)
    result.data_mismatches = [DataMismatch(addr,target,candidate,frozenset({0,8}))]
    assert not switch_tables.differences(net,proj,fn,result)


def test_callee_observation_reports_widths_and_stops_at_pointer_alias():
    code = assembly('swc1 $f0, 0($a0)','swc1 $f2, 4($a0)','jr $ra','swc1 $f4, 8($a0)')
    assert callee_frame_evidence.writes(code,'a0') == [(0,4),(4,4),(8,4)]
    assert not callee_frame_evidence.writes(code.replace('swc1 $f2, 4($a0)','move $v0, $a0'),'a0')
    fn = {'asm':assembly('addiu $sp, $sp, -0x30','jal fill','addiu $a0, $sp, 0x18')}
    net = SimpleNamespace(is_rom=lambda _:True, _asm_text=lambda row,proj:row['asm'],
                          project_store=lambda _:SimpleNamespace(all=lambda *args:[{'asm':code}]))
    proj = {'id':1,'arch':'mips'}
    diff = [('s','addiu $sp, $sp, -0x30','addiu $sp, $sp, -0x28')]
    [message] = callee_frame_evidence.messages(net,proj,fn,diff)
    assert 'fill receives sp+0x18 in $a0' in message
    assert '+0x8 (4 bytes)' in message


def test_floating_register_lane_prioritizes_only_independent_float_load_orders():
    source = 'float f(Vec *p) { float x = p->x; float y = p->y; return x-y; }'
    rows = [('r','lwc1 $f2, 0($a0)','lwc1 $f4, 0($a0)'),
            ('r','lwc1 $f4, 4($a0)','lwc1 $f2, 4($a0)')]
    proposals = allocation_search.proposals(source,'f','mips',rows,[],{})
    assert proposals[0][0] == 'floating_initializer_order'
    assert 'float y = p->y; float x = p->x;' in proposals[0][1]
    assert not allocation_search.floating_only(rows+[('i','sll $a0, $a0, 1','sll $a0, $a0, 2')],'mips')
    assert not allocation_search.floating_only(rows,'arm')


def test_floating_register_lane_tries_the_two_arm_float_rewrites_first():
    # A function whose both arms set one float field, six $f0/$f2 operands from exact (KMC GCC 2.7.2).
    source = ('''void f(void *o, s32 k, f32 limit, f32 y) {
    f32 old;
    f32 r;
    f32 s;

    if (k == 2) {
        r = (limit - y) * 30.0f;
        old = M2C_FIELD(o, f32 *, 0xCC);
        r = (f32)((f64)(r - old) * K + (f64)old);
    } else {
        s = M2C_FIELD(o, f32 *, 0x90);
        r = s * 15.0f;
    }
    M2C_FIELD(o, f32 *, 0xCC) = r;
}
''')
    rows = [('r','mul.s $f2, $f2, $f0','mul.s $f0, $f2, $f0'),('r','lwc1 $f0, 0xcc($s2)','lwc1 $f2, 0xcc($s2)')]
    labels = {}
    proposals = allocation_search.proposals(source,'f','mips',rows,[],labels)
    assert proposals[0][0] == 'float_arm_roles'
    assert 'M2C_FIELD(o, f32 *, 0xCC) += ' in proposals[0][1]
    assert allocation_search.floating_proposals(source.replace('M2C_FIELD(o, f32 *, 0xCC) = r;','M2C_FIELD(o, f32 *, 0xCC) = r;\n#include "x.h"'),'f','',rows,'mips') == []
    assert not allocation_search.floating_proposals(source,'f','',[('r','addu $a0, $a1, $a2','addu $a0, $a1, $a3')],'mips')


def test_proven_indexed_dispatch_checks_past_first_word():
    from decompnet.rom.match import R_MIPS_32, RomTarget, _Linker

    def pack(words):
        return b''.join(word.to_bytes(4,'big') for word in words)

    addr = 0x80002000
    code = pack([0x2483FFFE,0x2C620003,0x10400006,0x00031080,
                 0x3C018000,0x00220821,0x8C222000,0x00400008,0,
                 0x03E00008,0,0x03E00008,0])
    first, second, third = 0x80001024,0x80001028,0x8000102C
    expected = pack([first,second,third])
    candidate = pack([first,third,second])
    linker = _Linker.__new__(_Linker)
    linker.target = RomTarget('f',0x80001000,code,[])
    linker.read = lambda address,size: expected[:size] if address == addr else None
    # The linker only asks this section for its type and its bytes.
    class Section(dict):
        name = '.rodata'
        def data(self):
            return candidate
    linker.elf = SimpleNamespace(get_section=lambda _:Section(sh_type='SHT_PROGBITS'))
    linker.extent = lambda *_:12
    linker.widths = {(1,0):4}
    linker.significant = lambda _data,_addr,width:width
    linker._relocs_in = lambda _: [(i,R_MIPS_32,None) for i in (0,4,8)]
    linker._pointee = lambda _,word:(word,None)
    linker.symbols = SimpleNamespace(label=lambda _:'jtbl')
    linker.problems, linker.data_mismatches = [], []
    linker.verify_data(1,0,addr,instructions_exact=True)
    assert linker.problems and len(linker.data_mismatches[0].target) == 12
    # A table of pointers with no matching dispatch stays on the scalar comparison.
    linker.target.code = b'\0'*len(code)
    linker.problems, linker.data_mismatches = [], []
    linker.verify_data(1,0,addr,instructions_exact=True)
    assert not linker.problems
