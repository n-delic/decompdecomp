"""Another field of one table and a literal for a donor's global (app/twin_fields.py), and a donor's own jumps in
rom_renames. Synthetic code: no game, no toolchain."""
import struct
from types import SimpleNamespace

from decompnet.app import twin_fields
from decompnet.app.twins import Print, apply, rom_renames

DONOR = '''extern u8 D_80001000[];
extern s32 D_80009000;
void use(s32, s32);

void func_80002000(void)
{
    use(*(s16 *)(D_80001000 + 0x210C), D_80009000);
    use(*(s16 *)(D_80001000 + 0x2108), 2.0f);
}
'''


def words(*ws):
    return b''.join(w.to_bytes(4, 'big') for w in ws)


def symbols():
    own = SimpleNamespace(name='func_80002000', address=0x80002000)
    table = SimpleNamespace(name='D_80001000', address=0x80001000)
    by_addr = {s.address: s for s in (own, table)}
    return SimpleNamespace(by_addr=by_addr, by_name={s.name: s for s in by_addr.values()}, starts=sorted(by_addr))


def test_a_jump_inside_the_donor_is_no_name():
    a = Print('k', [], 0x200, [0x80002100])
    b = Print('k', [], 0x200, [0x80003100])
    assert rom_renames(symbols(), DONOR, a, b, 'func_80002000', 'func_80003000') == {'func_80002000': 'func_80003000'}


def test_a_moved_offset_keeps_the_base_name():
    pairs = [(0x8000310C, 0x8000310A), (0x80003108, 0x80003108)]
    source, names = twin_fields.moved_offsets(DONOR, {'D_80001000': 'D_80000FFE'}, pairs, symbols())
    assert 'D_80001000 + 0x210A' in source and 'D_80001000 + 0x2108' in source and names == {}
    # with nothing else read through the base, renaming it says the same
    assert twin_fields.moved_offsets(DONOR, {'D_80001000': 'D_80000FFE'}, pairs[:1], symbols()) is None


def test_the_combined_child_moves_the_offset_and_writes_the_constant():
    reloc = lambda *addresses: '[' + ','.join(f'[{4 * i},4,{a}]' for i, a in enumerate(addresses)) + ']'  # noqa: E731
    fifty = struct.unpack('>I', struct.pack('>f', 50.0))[0]
    donor = {'relocs': reloc(0x8000310C, 0x80003108, 0x80009000), 'target': words(0x3c060000)}
    target = {'relocs': reloc(0x8000310A, 0x80003108), 'target': words(0x3c060000 | fifty >> 16)}
    pairs = [(0x8000310C, 0x8000310A), (0x80003108, 0x80003108)]
    first = twin_fields.variants(DONOR, {'func_80002000': 'func_80003000', 'D_80001000': 'D_80000FFE'}, pairs, donor,
                                 target, symbols(), apply)[0]
    assert 'use(*(s16 *)(D_80001000 + 0x210A), 50.0f);' in first
    assert 'D_80009000' not in first and 'void use(s32, f32);' in first and 'func_80003000' in first
