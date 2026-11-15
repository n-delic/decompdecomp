"""Scheduled address loads keep the pairing encoded by the ELF relocation table."""
from decompnet.elfobj import section_target


def relocations(entries):
    symtab = object()
    return sorted([dict(off=off, type=kind, value=value, order=order,
                        symidx=1, symtab=symtab, rela=False)
                   for order, (off, kind, value) in enumerate(entries)], key=lambda r: r['off'])


def test_scheduled_branch_pairs_use_elf_order_even_with_the_same_register():
    # The second branch's LUI is scheduled before the first branch's load. Both use $at.
    raw = relocations([(64, 5, 0), (68, 6, 0), (44, 5, 0), (116, 6, 8)])
    assert {r['off']: section_target('mips', raw, i) for i, r in enumerate(raw)} == {
        44: 8, 64: 0, 68: 0, 116: 8}


def test_several_address_loads_in_flight_keep_their_own_offsets_and_carry():
    raw = relocations([(0, 5, 1), (16, 6, 0x8000), (4, 5, 0), (20, 6, 4),
                       (8, 5, 0), (24, 6, 8)])
    assert [section_target('mips', raw, i) for i in range(len(raw))] == [0x8000, 4, 8, 0x8000, 4, 8]


def test_old_callers_without_table_positions_and_rela_keep_their_meaning():
    raw = relocations([(0, 5, 1), (4, 6, 0xfffc)])
    for r in raw:
        del r['order']
    assert [section_target('mips', raw, i) for i in range(2)] == [0xfffc, 0xfffc]
    raw[0].update(rela=True, value=0x123456)
    assert section_target('mips', raw, 0) == 0x123456
