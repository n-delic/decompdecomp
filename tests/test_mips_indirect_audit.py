"""Small homemade guarded switches and load/call traces, no game data."""
import struct

import pytest

from decompnet.arch.mips_gaps import extent
from decompnet.arch.mips_indirect import calls, consumers, jump_table


def fixture(order='big'):
    image=bytearray(0x1200)
    words=[0x2c620002,0x1040000b,0,0x00031080,0x3c018000,0x00220821,0x8c221100,0x00400008,0,
           0x03e00008,0,0x03e00008,0,0x03e00008,0]
    image[0x1000:0x103c]=b''.join(w.to_bytes(4,order) for w in words)
    image[0x1100:0x1108]=b''.join(w.to_bytes(4,order) for w in [0x80001024,0x8000102c])
    return image,[dict(rom_start=0x1100,rom_end=0x1108,vram=0x80001100)]


def test_guarded_switch_follows_all_cases_only_with_explicit_mapping():
    for order in ('big','little'):
        image,ranges=fixture(order)
        unverified=extent(image,0x1000,0x103c,0x80001000,order,mapping_choices=[0x80000000])
        assert not unverified['flow_closed']
        assert unverified['jump_tables'][0]['state']=='mapping_hypothesis'
        result=extent(image,0x1000,0x103c,0x80001000,order,table_ranges=ranges)
        assert result['flow_closed'] and not result['direct_flow_closed']
        assert result['return_sites']==3 and result['jump_tables'][0]['entry_count']==2
        assert not result['unresolved_transfers']


def test_bad_bounds_targets_guard_bypass_and_conflicting_mappings_stay_unresolved():
    image,ranges=fixture()
    image[0x1104:0x1108]=(0x80002000).to_bytes(4,'big')
    assert not extent(image,0x1000,0x103c,0x80001000,'big',table_ranges=ranges)['flow_closed']
    image,ranges=fixture();image[0x1000:0x1004]=(0x2c620101).to_bytes(4,'big')
    assert jump_table(image,0x1000,0x103c,0x80001000,0x101c,'big',ranges) is None
    image,ranges=fixture();image[0x1034:0x1038]=(0x08000403).to_bytes(4,'big')  # external edge into scale
    row=jump_table(image,0x1000,0x103c,0x80001000,0x101c,'big',ranges)
    assert 'bypasses' in row['reason']
    image,ranges=fixture();ranges.append(dict(rom_start=0x1120,rom_end=0x1128,vram=0x80001100))
    assert 'conflicting' in jump_table(image,0x1000,0x103c,0x80001000,0x101c,'big',ranges)['reason']


def test_callback_cell_load_call_and_indexed_hypotheses_are_distinct():
    image=bytearray(0x1200)
    # lui v0,0x8000; lw v0,0x1100(v0); jalr v0; nop
    image[0x1000:0x1010]=struct.pack('>4I',0x3c028000,0x8c421100,0x0040f809,0)
    functions=[dict(name='caller',rom=0x1000,size=16,vram=0x80001000)]
    rows=[dict(pointer_references=[dict(rom_offset=0x1100,value=0x80002000)])]
    ranges=[dict(rom_start=0x1100,rom_end=0x1104,vram=0x80001100)]
    assert not consumers(image,functions,rows,'big',ranges,[])
    assert rows[0]['callback_consumers'][0]['state']=='cell_load_call'
    consumers(image,functions,rows,'big',[],[0x80000000])
    assert rows[0]['callback_consumers'][0]['state']=='consumer_hypothesis'
    # stride 4 times an unknown index, then table load/call: reachability unproven.
    image[0x1000:0x1018]=struct.pack('>6I',0x00041880,0x3c028000,0x00431021,0x8c421100,0x0040f809,0)
    functions[0]['size']=24;consumers(image,functions,rows,'big',ranges,[])
    assert rows[0]['callback_consumers'][0]['indexed']
    assert rows[0]['callback_consumers'][0]['state']=='consumer_hypothesis'
    functions[0]['handwritten']=True
    assert calls(image,functions,'big')[0]==[]


def test_unknown_operation_breaks_trace_instead_of_preserving_a_stale_pointer():
    image=bytearray(0x1200)
    image[0x1000:0x1014]=struct.pack('>5I',0x3c028000,0x8c421100,0x24020001,0x0040f809,0)
    assert calls(image,[dict(name='f',rom=0x1000,size=20,vram=0x80001000)],'big')[0]==[]


def test_call_delay_slots_do_not_seed_post_call_register_facts():
    image=bytearray(0x1200)
    # The LUI executes BEFORE the unknown callee, which may overwrite v0.
    image[0x1000:0x1014]=struct.pack('>5I',0x0c000800,0x3c028000,0x8c421100,0x0040f809,0)
    functions=[dict(name='f',rom=0x1000,size=20,vram=0x80001000)]
    rows=[dict(pointer_references=[dict(rom_offset=0x1100,value=0x80002000)])]
    consumers(image,functions,rows,'big',[dict(rom_start=0x1100,rom_end=0x1104,vram=0x80001100)],[])
    assert not rows[0]['callback_consumers']


def test_case_path_failure_prevents_closed_flow_even_when_table_values_fit():
    image,ranges=fixture();image[0x102c:0x1030]=(0x00800008).to_bytes(4,'big')
    result=extent(image,0x1000,0x103c,0x80001000,'big',table_ranges=ranges)
    assert not result['flow_closed'] and result['unresolved_transfers']


def test_conflicting_callback_mappings_never_become_verified_consumers():
    image=bytearray(0x1200);image[0x1000:0x1010]=struct.pack('>4I',0x3c028000,0x8c421100,0x0040f809,0)
    rows=[dict(pointer_references=[dict(rom_offset=0x1100,value=0x80002000)])]
    ranges=[dict(rom_start=0x1100,rom_end=0x1104,vram=0x80001100),dict(rom_start=0x1120,rom_end=0x1124,vram=0x80001100)]
    consumers(image,[dict(name='f',rom=0x1000,size=16,vram=0x80001000)],rows,'big',ranges,[])
    result=rows[0]['callback_consumers'][0]
    assert not result['mapping_verified'] and result['state']=='consumer_hypothesis'
    assert result['reason']=='conflicting mappings'


def test_scan_budget_is_explicit_and_does_not_claim_complete_tracing(monkeypatch):
    from decompnet.arch import mips_indirect
    monkeypatch.setattr(mips_indirect,'MAX_WORDS',1)
    image=bytes(0x1100)
    assert calls(image,[dict(name='f',rom=0x1000,size=16,vram=0x80001000)],'big')==([],True)


def test_floating_branch_and_table_cases_cannot_bypass_index_guard():
    image,ranges=fixture();image[0x1034:0x1038]=(0x4501fff5).to_bytes(4,'big')
    assert 'bypasses' in jump_table(image,0x1000,0x103c,0x80001000,0x101c,'big',ranges)['reason']
    image,ranges=fixture();image[0x1100:0x1104]=(0x8000100c).to_bytes(4,'big')
    assert not extent(image,0x1000,0x103c,0x80001000,'big',table_ranges=ranges)['flow_closed']


def test_one_switch_cannot_enter_another_switch_after_its_guard():
    image,ranges=fixture();image[0x1040:0x107c]=image[0x1000:0x103c]
    image[0x1058:0x105c]=(0x8c221120).to_bytes(4,'big')
    image[0x1100:0x1104]=(0x8000104c).to_bytes(4,'big')
    image[0x1120:0x1128]=struct.pack('>2I',0x80001064,0x8000106c)
    ranges.append(dict(rom_start=0x1120,rom_end=0x1128,vram=0x80001120))
    result=extent(image,0x1000,0x107c,0x80001000,'big',table_ranges=ranges)
    assert not result['flow_closed']
    assert any(t.get('reason')=='a table edge bypasses this guard' for t in result['jump_tables'])


def test_loaded_index_can_form_a_record_stride_but_never_proves_reachability():
    image=bytearray(0x1200)
    image[0x1000:0x1024]=struct.pack('>9I',0x8c840000,0x000418c0,0x00641823,0x00031880,0x3c028000,0x00431021,0x8c421100,0x0040f809,0)
    rows=[dict(pointer_references=[dict(rom_offset=0x1138,value=0x80002000)])]
    consumers(image,[dict(name='f',rom=0x1000,size=36,vram=0x80001000)],rows,'big',
              [dict(rom_start=0x1138,rom_end=0x113c,vram=0x80001138)],[])
    found=rows[0]['callback_consumers'][0]
    assert found['stride']==28 and found['possible_index']==2
    assert found['mapping_verified'] and found['state']=='consumer_hypothesis'


def test_a_switch_whose_scale_sits_in_the_guards_delay_slot_is_followed():
    """GCC scheduled the index's scale into the guard branch's delay slot in an N64 racing game (sltiu; beqz; sll in
    its slot; lui; addu; lw; jr), one word shorter than the idiom above: its table is read and its cases followed."""
    image=bytearray(0x1200)
    words=[0x2c620002,0x1040000a,0x00031080,0x3c018000,0x00220821,0x8c221100,0x00400008,0,
           0x03e00008,0,0x03e00008,0,0x03e00008,0]
    image[0x1000:0x1038]=b''.join(w.to_bytes(4,'big') for w in words)
    image[0x1100:0x1108]=b''.join(w.to_bytes(4,'big') for w in [0x80001020,0x80001028])
    ranges=[dict(rom_start=0x1100,rom_end=0x1108,vram=0x80001100)]
    assert jump_table(image,0x1000,0x1038,0x80001000,0x1018,'big',ranges)['state']=='validated'
    result=extent(image,0x1000,0x1038,0x80001000,'big',table_ranges=ranges)
    assert result['flow_closed'] and result['return_sites']==3 and not result['unresolved_transfers']


def test_ido_switch_reads_unique_overlay_table_with_compact_addresses():
    # sltiu, beqz, LUI in the branch slot, ADDIU, index scale, ADDU, raw LW, JR.
    vram = 0x80200000
    words = [0x2c620002,0x10400009,0x3c020020,0x24420050,0x00031880,
             0x00621821,0x8c620000,0x00400008,0,0x03e00008,0,0x03e00008,0]
    image = b''.join(word.to_bytes(4,'big') for word in words)
    image += bytes(0x50-len(image))+bytes.fromhex('002000240020002c')
    alias = dict(rom_start=0x50,rom_end=0x58,vram=0x200050,target_prefix=0x80000000,code_delta=vram)
    result = jump_table(image,0,len(words)*4,vram,28,'big',[alias])
    assert result['state']=='validated' and result['targets']==[vram+36,vram+44]
    wrong = dict(alias,code_delta=vram+4)
    assert jump_table(image,0,len(words)*4,vram,28,'big',[wrong])['state']=='unresolved'
    corrupt = image[:-4]+bytes.fromhex('00200010')
    assert jump_table(corrupt,0,len(words)*4,vram,28,'big',[alias])['state']=='unresolved'


@pytest.mark.parametrize('prefix', [0, 0x80000000])
def test_fp_delay_slot_bound_and_boot_mapped_cases(prefix):
    words = [0x45000003, 0x2c620002, 0x44800800, 0x24c60001,
             0x1040000a, 0x00031080, 0x3c018000, 0x00220821, 0x8c221100, 0x00400008, 0,
             0x03e00008, 0, 0x03e00008, 0, 0x03e00008, 0]
    image = bytearray(0x1200)
    image[0x1000:0x1044] = struct.pack('>17I', *words)
    image[0x1100:0x1108] = struct.pack('>2I', prefix|0x102c, prefix|0x1034)
    alias = dict(rom_start=0x1100, rom_end=0x1108, vram=0x80001100, target_prefix=0x80000000, boot_copy=True)
    def audit():
        return extent(image, 0x1000, 0x1044, 0x80001000, 'big', table_ranges=[alias])
    assert audit()['flow_closed']
    if prefix:
        swapped = bytearray(image)
        swapped[0x1044:0x1088] = image[0x1000:0x1044]
        result = extent(swapped, 0x1044, 0x1088, 0x80001000, 'big', table_ranges=[alias])
        assert not result['flow_closed']
    # A branch-likely annuls the bound on one path.
    image[0x1000:0x1004] = struct.pack('>I', 0x45020003)
    assert not audit()['flow_closed']


def test_switch_reuses_the_high_half_register_for_the_scaled_index():
    vram = 0x80200000
    words = [0x2c620002,0x10400009,0x3c088020,0x25040050,0x00034080,
             0x01044021,0x8d090000,0x01200008,0,0x03e00008,0,0x03e00008,0]
    image = struct.pack('>13I',*words)
    image += bytes(0x50-len(image))+struct.pack('>2I',vram+36,vram+44)
    ranges = [dict(rom_start=0x50,rom_end=0x58,vram=vram+0x50)]
    result = extent(image,0,len(words)*4,vram,'big',table_ranges=ranges)
    assert result['flow_closed'] and result['return_sites'] == 2
    assert result['jump_tables'][0]['targets'] == [vram+36,vram+44]
    assert not extent(image,0,len(words)*4,vram,'big')['flow_closed']


def test_deep_jump_table_evidence_reads_rom_without_assembly_labels():
    from types import SimpleNamespace

    from decompnet.app import switch_tables

    for order in ('big', 'little'):
        image, _ = fixture(order)
        net = SimpleNamespace(is_rom=lambda _: True,
                              reader=lambda *_, data=image: lambda address, size: data[address-0x80000000:address-0x80000000+size])
        proj = {'arch': 'mips', 'big_endian': order == 'big'}
        fn = {'vram': 0x80001000, 'size': 60, 'asm': '', 'target': image[0x1000:0x103c]}
        assert switch_tables.read(net, proj, fn) == [(0, [0x80001024, 0x8000102c])]
        image[0x1104:0x1108] = (0x80002000).to_bytes(4, order)
        assert switch_tables.read(net, proj, fn) == []


@pytest.mark.parametrize('order', ['big', 'little'])
def test_saved_switch_base_can_follow_an_early_return(order):
    vram = 0x80001000
    words = [
        0x0c001000, 0,  # jal to an external helper
        0x1080000c, 0,  # beqz a0 to the return tail
        0x3c178000, 0x26f72000,  # table base in s7
        0x2c620002, 0x10400007,  # bound and default branch
        0x00031080, 0x00571021, 0x8c420000, 0x00400008, 0,
        0x1000fff8, 0,  # a case loops to the guard
        0x03e00008, 0,
    ]
    destinations = [vram + 13 * 4, vram + 15 * 4]

    def recognize(words):
        code = b''.join(word.to_bytes(4, order) for word in words)
        data = b''.join(address.to_bytes(4, order) for address in destinations)
        return jump_table(code, 0, len(code), vram, 11 * 4, order,
                          reader=lambda at, size: data if (at, size) == (0x80002000, 8) else None,
                          allow_saved_base=True)

    assert recognize(words)['targets'] == destinations
    bypass = words.copy()
    bypass[2] = 0x10800003  # can jump to the guard without establishing s7
    assert recognize(bypass) is None
    delay_slot_base = words.copy()
    delay_slot_base[3] = 0x1080000b
    assert recognize(delay_slot_base) is None
    clobber = words.copy()
    clobber[14] = 0x0000b821  # a backedge delay slot overwrites s7
    assert recognize(clobber) is None


def test_guard_can_schedule_stack_spills_and_an_unrelated_move():
    image, ranges = fixture()
    # Insert two stack spills between the bound and branch, and fill its delay slot.
    code = image[0x1000:0x103c]
    scheduled = code[:4] + struct.pack('>II', 0xafbf001c, 0xafa40020) + code[4:8]
    scheduled += struct.pack('>I', 0x00808025) + code[12:]
    end = 0x1000 + len(scheduled)
    image[0x1000:end] = scheduled
    image[0x1100:0x1108] = struct.pack('>II', 0x8000102c, 0x80001034)
    result = jump_table(image, 0x1000, end, 0x80001000, 0x1024, 'big', ranges)
    assert result['state'] == 'validated' and result['entry_count'] == 2

    image[0x1008:0x100c] = struct.pack('>I', 0x00801825)  # Clobbers the checked index.
    assert jump_table(image, 0x1000, end, 0x80001000, 0x1024, 'big', ranges) is None
