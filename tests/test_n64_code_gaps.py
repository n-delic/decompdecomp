"""The importer's last look at the gaps between an N64 game's code (consoles/n64/code.py, complete_gaps): homemade
code, no game data. A racing game's split left 16 functions (31 KB) as data: object handlers reached only through
pointers its code builds, leaves with no frame, switches, several functions to a gap."""
import pytest

from decompnet.consoles.n64.code import aligned_gaps, complete_gaps, mapped_data, mapped_gaps, runtime_entries

LEAF = [0x24020001, 0x24420001, 0x03e00008, 0]


@pytest.mark.parametrize('outcome', ['partial', 'lost_existing', 'no_progress', 'resplit_error'])
def test_import_keeps_safe_recovered_entries_and_rolls_back_unsafe_resplits(tmp_path, monkeypatch, outcome):
    from types import SimpleNamespace

    from decompnet.consoles.n64 import code
    from decompnet.rom import split

    config = tmp_path / 'game.yaml'
    config.write_text('original')
    (tmp_path / 'rom.z64').write_bytes(bytes(64))
    old = SimpleNamespace(rom=0, size=8, vram=0x80001000)
    found = SimpleNamespace(rom=16, size=8, vram=0x80001010)
    base = SimpleNamespace(config_path=config, image='rom.z64', functions=[old])
    updated = SimpleNamespace(config_path=config, image='rom.z64', functions=(
        [old, found] if outcome == 'partial' else [found] if outcome == 'lost_existing' else [old]))
    calls = []

    def recover(path, _image, _log, _known):
        if calls:
            return []
        calls.append('scan')
        path.write_text('updated')
        return [(16, 24, 0x80001010), (32, 40, 0x80001020)]

    monkeypatch.setattr(code, 'missed_functions', recover)
    def resplit(*_args, **_kwargs):
        if config.read_text() == 'original':
            return base
        if outcome == 'resplit_error':
            raise ValueError('staged split failed')
        return updated

    monkeypatch.setattr(split, 'resplit_prepared', resplit)
    result = code.finish_import(base, tmp_path, 'rom.z64', object(), log=lambda *_: None)
    assert result is (updated if outcome == 'partial' else base)
    assert config.read_text() == ('updated' if outcome == 'partial' else 'original')


def test_prepared_resplit_removes_stale_assembly_without_recreating_the_config(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from decompnet.rom import split

    asm = tmp_path / 'asm'
    asm.mkdir()
    (asm / 'stale.s').write_text('old output')
    config = tmp_path / 'game.yaml'
    config.write_text('prepared')
    previous = SimpleNamespace(config_path=config, image='image.z64', compiler='KMC', gp=1, log='before')
    calls = []

    def finish(*args):
        assert not asm.exists()
        calls.append(args)
        return 'new split'

    monkeypatch.setattr(split, '_finish_split', finish)
    assert split.resplit_prepared(tmp_path, 'rom.z64', object(), previous) == 'new split'
    assert calls[0][2] == config and calls[0][3] == 'image.z64'
    assert config.read_text() == 'prepared'


def test_import_repeats_recovery_until_no_more_bodies(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from decompnet.consoles.n64 import code
    from decompnet.rom import split

    config = tmp_path / 'game.yaml'
    config.write_text('original')
    (tmp_path / 'rom.z64').write_bytes(bytes(64))
    functions = [SimpleNamespace(rom=n, size=8, vram=0x80001000 + n) for n in (0, 16, 32)]
    results = [SimpleNamespace(config_path=config, image='rom.z64', functions=functions[:n]) for n in (1, 2, 3)]
    scans = []

    def recover(path, _image, _log, known):
        scans.append(known)
        if len(scans) == 3:
            return []
        path.write_text(str(len(scans)))
        fn = functions[len(scans)]
        return [(fn.rom, fn.rom + fn.size, fn.vram)]

    monkeypatch.setattr(code, 'missed_functions', recover)
    monkeypatch.setattr(split, 'resplit_prepared', lambda *_args, **_kwargs: results[int(config.read_text())])
    assert code.finish_import(results[0], tmp_path, 'rom.z64', object(), log=lambda *_: None) is results[2]
    assert [len(known) for known in scans] == [1, 2, 3]


def test_exact_sdk_body_before_a_function_uses_its_true_entry(monkeypatch):
    from decompnet.consoles.n64 import sdk_assembly
    from decompnet.consoles.n64.code import _sdk_between_functions

    body = b'known SDK body' + bytes(50)
    rom = bytes(0x30) + body + bytes(0x20)
    spans = [(0x10, 0x20, 0x80001010), (0x70, 0x80, 0x80001070)]
    banks = [(0x10, 0x80, 0x80001010)]
    monkeypatch.setattr(sdk_assembly, 'SIZES', (64,))
    monkeypatch.setattr(sdk_assembly, 'identify', lambda data: {'name': 'sdk'} if data == body else None)
    assert _sdk_between_functions(rom, spans, banks) == [(0x30, 0x70, 0x80001030)]
    assert not _sdk_between_functions(rom, [spans[0], (0x70, 0x80, 0x80101070)], banks)
    assert not _sdk_between_functions(rom, spans, [(0x10, 0x60, 0x80001010)])
    assert not _sdk_between_functions(rom[:0x30] + bytes(64) + rom[0x70:], spans, banks)


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


def test_rodata_ahead_of_an_overlay_is_mapped_on_the_overlay_after_it():
    """A table of jump tables can sit between one overlay's code and the next overlay's code, loaded with the next:
    a switch in that overlay found its table nowhere (the data was mapped only on the code before it), so the
    function stayed raw bytes. The bytes are now mapped on both neighbours' mappings."""
    spans = [(0x1000, 0x1100, 0x80001000), (0x2000, 0x2100, 0x80300000)]
    ranges = {(r["rom_start"], r["rom_end"], r["vram"]) for r in mapped_data(spans, (), 0x3000)}
    assert (0x1100, 0x2000, 0x80001100) in ranges  # after the code before it
    assert (0x1100, 0x2000, 0x802FF100) in ranges  # ahead of the code after it
    same = [(0x1000, 0x1100, 0x80001000), (0x2000, 0x2100, 0x80002000)]
    assert len([r for r in mapped_data(same, (), 0x3000) if r["rom_start"] == 0x1100]) == 1


def test_an_overlay_tail_placed_past_its_alignment_boundary_becomes_code():
    """The bytes after an overlay's last listed function can sit a few bytes further on than its mapping says (the
    next function starts on a 16-byte boundary), with another overlay after them. Two calls from known code into
    frames of the gap, at one offset, place it."""
    fn = [0x27bdffe8, 0xafbf0010, 0x24020001, 0x8fbf0010, 0x03e00008, 0x27bd0018]
    before = [0x0c00040c, 0x0c000412, 0x03e00008, 0]  # jal 0x80001030; jal 0x80001048
    image = image_with([(0x1000, before), (0x1020, fn), (0x1038, fn), (0x1050, [0x03e00008, 0])])
    found = [(0x1000, 0x1010, 0x80001000), (0x1050, 0x1058, 0x80205050)]
    assert aligned_gaps(image, found) == [(0x1010, 0x1050, 0x80001020)]
    recovered = complete_gaps(image, [], found, log=lambda *a: None)
    assert (0x1020, 0x1038, 0x80001030) in recovered and (0x1038, 0x1050, 0x80001048) in recovered
    assert aligned_gaps(image, [(0x1000, 0x1010, 0x80001000), (0x1050, 0x1058, 0x80001050)]) == []  # one mapping


def test_alignment_calls_to_a_known_bank_do_not_prove_a_gap_mapping():
    fn = [0x27bdffe8, 0xafbf0010, 0x24020001, 0x8fbf0010, 0x03e00008, 0x27bd0018]
    before = [0x0c00040c, 0x0c000412, 0x03e00008, 0]
    image = image_with([(0x1000, before), (0x1020, fn), (0x1038, fn), (0x1050, [0x03e00008, 0]),
                        (0x1100, fn), (0x1118, fn)])
    spans = [(0x1000, 0x1010, 0x80001000), (0x1050, 0x1058, 0x80205050),
             (0x1100, 0x1130, 0x80001030)]
    deferred = []
    assert not any(lo == 0x1010 for lo, _, _ in aligned_gaps(
        image, spans, on_deferred=lambda lo, hi: deferred.append((lo, hi))))
    assert (0x1010, 0x1050) in deferred


def test_alignment_tie_is_left_for_review():
    fn = [0x27bdffe8, 0xafbf0010, 0x24020001, 0x8fbf0010, 0x03e00008, 0x27bd0018]
    before = [0x0c000420, 0x0c000426, 0x03e00008, 0]  # calls 0x80001080 and 0x80001098
    image = image_with([(0x1000, before), (0x1030, fn), (0x1048, fn), (0x1070, fn), (0x1088, fn),
                        (0x10f0, [0x03e00008, 0])])
    spans = [(0x1000, 0x1010, 0x80001000), (0x10f0, 0x10f8, 0x80205000)]
    assert aligned_gaps(image, spans) == []
    messages = []
    complete_gaps(image, [], spans, log=messages.append)
    assert any('ambiguous alignment' in message for message in messages)


def test_alignment_does_not_switch_to_a_weaker_offset_after_rejecting_known_calls():
    fn = [0x27bdffe8, 0xafbf0010, 0x24020001, 0x8fbf0010, 0x03e00008, 0x27bd0018]
    before = [0x0c000420, 0x0c000426, 0x0c00042c, 0x0c000450, 0x0c000456, 0x03e00008, 0]
    image = image_with([(0x1000, before), (0x1060, fn), (0x1078, fn), (0x1090, fn),
                        (0x10e0, fn), (0x10f8, fn), (0x1200, [0x03e00008, 0]),
                        (0x1300, fn), (0x1318, fn)])
    spans = [(0x1000, 0x101c, 0x80001000), (0x1200, 0x1208, 0x80205000),
             (0x1300, 0x1330, 0x80001080)]
    assert not any(lo == 0x101c for lo, _, _ in aligned_gaps(image, spans))


def test_aligned_gap_search_checks_cancellation_before_scanning_code():
    def stop():
        raise RuntimeError('stopped')

    with pytest.raises(RuntimeError, match='stopped'):
        aligned_gaps(bytes(0x200), [(0, 4, 0x80000000), (0x100, 0x104, 0x80200000)], check=stop)
