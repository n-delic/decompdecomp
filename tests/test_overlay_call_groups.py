from types import SimpleNamespace

from decompnet.rom.automatic_boundaries import shared_slot_calls
from decompnet.rom.split import AsmLine, Segment


def test_adjacent_units_share_overlay_call_evidence():
    segments = [Segment('first', 0x100, 0x200, 0x80001000),
                Segment('second', 0x200, 0x300, 0x80001100)]
    targets = [0x80001020+4*i for i in range(4)] + [0x80001120+4*i for i in range(4)]
    lines = [AsmLine(0x10+4*i, 0x80000010+4*i, 0x0c000000 | ((target & 0xfffffff) >> 2), 'jal', '')
             for i, target in enumerate(targets)]
    caller = SimpleNamespace(lines=lines, segment='main')
    ignored, slots = shared_slot_calls([caller], bytes(0x400), SimpleNamespace(big_endian=True), segments)
    assert {item[0] for item in ignored} == set(targets)
    assert slots == segments
    # A sparse group does not establish that calls belong to sibling overlays.
    caller.lines = lines[:-1]
    assert shared_slot_calls([caller], bytes(0x400), SimpleNamespace(big_endian=True), segments) == ([], [])

