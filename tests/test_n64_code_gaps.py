"""The importer's last look at the gaps between an N64 game's code (consoles/n64/code.py, complete_gaps): homemade
code, no game data. A racing game's split left 16 functions (31 KB) as data: object handlers reached only through
pointers its code builds, leaves with no frame, switches, several functions to a gap."""
from decompnet.consoles.n64.code import complete_gaps, mapped_gaps, runtime_entries

LEAF = [0x24020001, 0x24420001, 0x03e00008, 0]


def image_with(spans_words):
    image = bytearray(0x3000)
    for rom, words in spans_words:
        for i, w in enumerate(words):
            image[rom + 4 * i:rom + 4 * i + 4] = w.to_bytes(4, "big")
    return bytes(image)


def test_a_leaf_reached_only_through_a_pointer_the_code_builds_becomes_code():
    # before the gap: lui $a0, 0x8000; addiu $a0, $a0, 0x1010 (the leaf's address); jr $ra
    before = [0x3c048000, 0x24841010, 0x03e00008, 0]
    after = [0x03e00008, 0]
    image = image_with([(0x1000, before), (0x1010, LEAF), (0x1020, after)])
    found = [(0x1000, 0x1010, 0x80001000), (0x1020, 0x1028, 0x80001020)]
    assert mapped_gaps(found) == [(0x1010, 0x1020, 0x80001010)]
    assert (0x1010, 0x1020, 0x80001010) in complete_gaps(image, [], found, log=lambda *a: None)


def test_unreferenced_bytes_between_code_stay_data():
    image = image_with([(0x1000, [0x03e00008, 0]), (0x1010, LEAF), (0x1020, [0x03e00008, 0])])
    found = [(0x1000, 0x1008, 0x80001000), (0x1020, 0x1028, 0x80001020)]
    assert complete_gaps(image, [], found, log=lambda *a: None) == found


def test_an_address_the_game_ran_opens_a_gap_between_two_overlays(tmp_path):
    """Code on two mappings either side (two overlays side by side): no address both agree on, so the gap stays
    data, unless the game was seen running an address the code before it places there."""
    image = image_with([(0x1000, [0x03e00008, 0]), (0x1008, LEAF), (0x1020, [0x03e00008, 0])])
    found = [(0x1000, 0x1008, 0x80001000), (0x1020, 0x1028, 0x80205000)]
    assert complete_gaps(image, [], found, log=lambda *a: None) == found
    (tmp_path / "runtime-entries.txt").write_text("80001008\nnot an address\n")
    entries = runtime_entries(tmp_path)
    assert entries == {0x80001008}
    assert (0x1008, 0x1018, 0x80001008) in complete_gaps(image, [], found, entries=entries, log=lambda *a: None)
