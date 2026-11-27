"""Relocation trailers define independent linked files, not one long code segment."""
import struct

from decompnet.consoles.n64 import reloc_fragments


def fragment(base):
    # Three relocated calls to distinct closed framed entries.
    text = bytearray(0xb0)
    for i, entry in enumerate((0x20, 0x50, 0x90)):
        struct.pack_into('>I', text, 4*i, 0x0c000000 | ((base+entry) & 0x0fffffff) >> 2)
        struct.pack_into('>4I', text, entry, 0x27bdfff0, 0xafbf000c, 0x03e00008, 0x27bd0010)
    entries = (0x44000000, 0x44000004, 0x44000008)
    trailer = struct.pack('>5I3I', len(text), 0, 0, 0, len(entries), *entries)
    trailer += bytes(12) + struct.pack('>I', 0x30)
    return bytes(text) + trailer


def test_consecutive_relocation_sections_recover_independent_mappings():
    image = bytes(0x1000) + fragment(0x80400000) + fragment(0x80400100) + fragment(0x80400200)
    records = reloc_fragments.discover(image)
    assert [(start, text, base) for start, _, text, base in records] == [
        (0x1000, 0xb0, 0x80400000), (0x10e0, 0xb0, 0x80400100), (0x11c0, 0xb0, 0x80400200)]
    assert reloc_fragments.replace([(0x1000, len(image), 0x80401000)], records) == [
        (0x1000, 0x10b0, 0x80400000), (0x10e0, 0x1190, 0x80400100), (0x11c0, 0x1270, 0x80400200)]


def test_corrupt_relocations_do_not_establish_a_fragment_chain():
    image = bytearray(bytes(0x1000) + fragment(0x80400000) * 3)
    image[0x10b0+20] = 0xff
    assert reloc_fragments.discover(image) == []


def test_single_fragment_and_ambiguous_calls_are_not_guessed():
    image = bytes(0x1000) + fragment(0x80400000)
    assert reloc_fragments.discover(image) == []
    assert reloc_fragments.discover(image * 3, [(0x1000, 0x10e0, 0x80000000)]) == []


def test_chain_preserves_unmapped_text_and_excludes_proven_trailer_and_data():
    # This small file has no relocations from which to infer a link address.
    text = b'\x03\xe0\x00\x08' + bytes(12)
    data = b'\x01' * 16
    trailer = struct.pack('>5I', len(text), len(data), 0, 0, 0) + bytes(8) + struct.pack('>I', 0x20)
    image = bytes(0x1000) + fragment(0x80400000) + text + data + trailer + fragment(0x80400100)
    records = reloc_fragments.discover(image)
    assert len(records) == 3
    assert records[1] == (0x10e0, 0x1120, 0x10, None)
    assert reloc_fragments.replace([(0x1000, len(image), 0x80500000)], records) == [
        (0x1000, 0x10b0, 0x80400000),
        (0x10e0, 0x10f0, 0x805000e0),
        (0x1120, 0x11d0, 0x80400100),
    ]


def test_intervening_fragment_base_requires_two_agreeing_call_proven_endpoints():
    text = b'\x03\xe0\x00\x08' + bytes(12)
    trailer = struct.pack('>5I', len(text), 0, 0, 0x20, 0) + bytes(8) + struct.pack('>I', 0x20)
    middle = text + trailer
    # File size and BSS both contribute to the next file's linked address.
    image = bytes(0x1000) + fragment(0x80400000) + middle + fragment(0x80400130)
    records = reloc_fragments.discover(image)
    assert [record[3] for record in records] == [0x80400000, 0x804000e0, 0x80400130]
    wrong = bytes(0x1000) + fragment(0x80400000) + middle + fragment(0x80400140)
    assert reloc_fragments.discover(wrong)[1][3] is None


def test_chain_end_requires_distinct_closed_relocated_call_and_pointer():
    base = 0x804001c0
    text = bytearray(0x80)
    struct.pack_into('>3I', text, 0, 0x3c028040, 0x244201e0, 0x0c000000 | ((base+0x50) & 0xfffffff) >> 2)
    for entry in (0x20, 0x50):
        struct.pack_into('>5I', text, entry, 0x27bdfff0, 0xafbf000c, 0x8fbf000c, 0x03e00008, 0x27bd0010)
    relocations = (0x45000000, 0x46000004, 0x44000008)
    trailer = struct.pack('>5I3I', len(text), 0, 0, 0, len(relocations), *relocations)
    trailer += bytes(12) + struct.pack('>I', 0x30)
    image = bytes(0x1000) + fragment(0x80400000) + fragment(0x804000e0) + bytes(text) + trailer
    records = reloc_fragments.discover(image)
    assert [record[3] for record in records] == [0x80400000, 0x804000e0, base]
    # Two references to the same body do not independently support the prediction.
    struct.pack_into('>I', text, 8, 0x0c000000 | ((base+0x20) & 0xfffffff) >> 2)
    image = bytes(0x1000) + fragment(0x80400000) + fragment(0x804000e0) + bytes(text) + trailer
    assert reloc_fragments.discover(image)[-1][3] is None


def test_tiny_pointer_getter_can_confirm_a_chain_start():
    stub = struct.pack('>8I', 0x3c028040, 0xafa40000, 0x03e00008, 0x24420010,
                       0x03e00008, 0xafa40000, 0, 0)
    trailer = struct.pack('>5I2I', len(stub), 0, 0, 0, 2, 0x45000000, 0x4600000c) + struct.pack('>I', 0x20)
    image = bytes(0x1000) + stub + trailer + fragment(0x80400040) + fragment(0x80400120)
    assert [record[3] for record in reloc_fragments.discover(image)] == [0x80400000, 0x80400040, 0x80400120]
    # A pointer to a different offset does not prove where this fragment was linked.
    other = bytearray(stub)
    struct.pack_into('>I', other, 12, 0x24420014)
    image = bytes(0x1000) + bytes(other) + trailer + fragment(0x80400040) + fragment(0x80400120)
    assert reloc_fragments.discover(image)[0][3] is None
