"""RAM evidence for called code hidden behind a different overlay mapping."""

import struct

from decompnet.consoles.n64.runtime_scoped import _return_call_site, called, located, observe
from decompnet.rom.split import Segment


def words(*items):
    return struct.pack(f'>{len(items)}I', *items)


def test_return_address_only_identifies_matching_direct_call():
    ram = bytearray(0x1000)
    ram[0x100:0x104] = words(0x0c000080)  # jal 0x80000200
    assert _return_call_site(ram, 0x80000108, 0x80000200) == 0x80000100
    assert _return_call_site(ram, 0x80000108, 0x80000204) is None
    assert _return_call_site(ram, 0x80000109, 0x80000200) is None
    ram[0x100:0x104] = words(0x08000080)  # j is not a call
    assert _return_call_site(ram, 0x80000108, 0x80000200) is None


def fixture():
    rom = bytearray(0x8000)
    source = words(0x27bdffe0, 0xafbf001c, 0x0c000800, 0, 0x8fbf001c, 0x03e00008, 0x27bd0020)
    target = words(0x24820001, 0x24420002, 0x03e00008, 0)
    rom[0x3000:0x3000+len(source)] = source
    rom[0x2000:0x2000+len(target)] = target
    ram = bytearray(0x4000)
    ram[0x3000:0x3000+len(source)] = source
    ram[0x2000:0x2000+len(target)] = target
    segments = [Segment('caller',0x3000,0x3040,0x80003000,'slot'),
                Segment('other',0x5000,0x5100,0x80002000,'slot')]
    functions = [(0x3000,0x3000+len(source),0x80003000)]
    return bytes(rom), segments, functions, bytes(ram)


def test_called_overlay_candidate_requires_caller_and_body_in_two_snapshots():
    rom, segments, functions, ram = fixture()
    assert called(rom, segments, functions, [ram, ram]) == [dict(
        rom_start=0x2000, rom_end=0x2010, vram=0x80002000, scope='slot', co_resident_snapshots=2)]
    assert called(rom, segments, functions, [ram]) == []
    changed = bytearray(ram)
    changed[0x3000] ^= 1
    assert called(rom, segments, functions, [ram, bytes(changed)]) == []
    segments[1].exclusive_ram_id = 'different'
    assert called(rom, segments, functions, [ram, ram]) == []


def test_runtime_location_requires_unique_prefix_and_entire_closed_body():
    body = words(0x27bdffe0, *[0x24020000 + i for i in range(14)], 0x03e00008, 0)
    rom = bytearray(0x1000)
    rom[0x100:0x100+len(body)] = body
    ram = bytearray(0x1000)
    ram[0x200:0x200+len(body)] = body
    candidate = {'rom_offset': 0x100, 'vram': 0x80000100, 'extent_bytes': len(body)}
    assert located(bytes(rom), [candidate], [bytes(ram)]) == [dict(
        kind='runtime_location', rom_start=0x100, rom_end=0x100+len(body), vram=0x80000200, snapshots=1)]
    changed = bytearray(ram)
    changed[0x200+64] ^= 1
    assert located(bytes(rom), [candidate], [bytes(changed)]) == []
    repeated = bytearray(ram)
    repeated[0x400:0x400+len(body)] = body
    assert located(bytes(rom), [candidate], [bytes(repeated)]) == []
    rom[0x500:0x500+len(body)] = body
    assert located(bytes(rom), [candidate], [bytes(ram)]) == []


def test_observed_execution_requires_breakpoint_capture_of_the_exact_body(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from decompnet.consoles.n64 import emulator

    body = words(0x27bdffe0, *[0x24020000 + i for i in range(14)], 0x03e00008, 0)
    rom = bytearray(0x1000)
    rom[0x100:0x100+len(body)] = body
    ram = bytearray(0x1000)
    ram[0x200:0x200+len(body)] = body
    (tmp_path / 'rom.z64').write_bytes(rom)
    candidate = {'rom_offset': 0x100, 'vram': 0x80000100, 'extent_bytes': len(body)}
    wrong = [False]
    monkeypatch.setattr(emulator, 'installed', lambda: tmp_path)

    def run(source, vis, *, dumps=None, record=None, breaks=(), **kwargs):
        if dumps:
            path = next(iter(dumps.values()))
            path.write_bytes(ram)
            return SimpleNamespace(dumps={60: path}, timed_out=False)
        captured = bytearray(ram)
        if wrong[0]:
            captured[0x200+64] ^= 1
        (record[0] / f'{breaks[0]:08X}_0.rdram').write_bytes(captured)
        return SimpleNamespace(hits=[SimpleNamespace(pc=breaks[0], vi=58)], timed_out=False, vis=1800)

    monkeypatch.setattr(emulator, 'run', run)
    result = observe(tmp_path, {'rom': {'file': 'rom.z64'}}, bytes(rom), [], [], lambda *_: None,
                     candidates=[candidate])
    assert result[0]['executed'] and result[0]['execution_retrace'] == 58
    wrong[0] = True
    result = observe(tmp_path, {'rom': {'file': 'rom.z64'}}, bytes(rom), [], [], lambda *_: None,
                     candidates=[candidate])
    assert 'executed' not in result[0]


def test_observed_execution_reuses_checkpoint_after_stop(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from decompnet.consoles.n64 import emulator

    body = words(0x27bdffe0, *[0x24020000 + i for i in range(14)], 0x03e00008, 0)
    rom = bytearray(0x1000)
    rom[0x100:0x100+len(body)] = body
    ram = bytearray(0x1000)
    ram[0x200:0x200+len(body)] = body
    (tmp_path / 'rom.z64').write_bytes(rom)
    candidate = {'rom_offset':0x100, 'vram':0x80000100, 'extent_bytes':len(body)}
    monkeypatch.setattr(emulator, 'installed', lambda: tmp_path)
    calls = []

    def run(source, vis, *, dumps=None, record=None, breaks=(), **kwargs):
        if dumps:
            path = next(iter(dumps.values()))
            path.write_bytes(ram)
            return SimpleNamespace(dumps={60:path}, timed_out=False)
        calls.append(breaks[0])
        (record[0] / f'{breaks[0]:08X}_0.rdram').write_bytes(ram)
        return SimpleNamespace(hits=[SimpleNamespace(pc=breaks[0],vi=58)], timed_out=False, vis=1800)

    saved = {}

    def checkpoint(item, save=False):
        if save:
            saved.update(executed=item['executed'], execution_retrace=item['execution_retrace'])
        else:
            return saved or None

    monkeypatch.setattr(emulator, 'run', run)
    args = (tmp_path, {'rom':{'file':'rom.z64'}}, bytes(rom), [], [], lambda *_: None)
    assert observe(*args, candidates=[candidate], checkpoint=checkpoint)[0]['executed']
    assert observe(*args, candidates=[candidate], checkpoint=checkpoint)[0]['execution_retrace'] == 58
    assert calls == [0x80000200]


def test_observed_entries_share_one_emulator_run_and_keep_individual_results(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from decompnet.consoles.n64 import emulator

    first = words(0x27bdffe0, *[0x24020000 + i for i in range(14)], 0x03e00008, 0)
    second = words(0x27bdffd0, *[0x24030000 + i for i in range(14)], 0x03e00008, 0)
    rom, ram = bytearray(0x1000), bytearray(0x1000)
    rom[0x100:0x100+len(first)], rom[0x300:0x300+len(second)] = first, second
    ram[0x500:0x500+len(first)], ram[0x700:0x700+len(second)] = first, second
    (tmp_path / 'rom.z64').write_bytes(rom)
    candidates = [{'rom_offset':0x100, 'vram':0x80000100, 'extent_bytes':len(first)},
                  {'rom_offset':0x300, 'vram':0x80000300, 'extent_bytes':len(second)}]
    calls = []
    monkeypatch.setattr(emulator, 'installed', lambda: tmp_path)

    def run(source, vis, *, dumps=None, record=None, breaks=(), **kwargs):
        if dumps:
            path = next(iter(dumps.values()))
            path.write_bytes(ram)
            return SimpleNamespace(dumps={60:path}, timed_out=False)
        calls.append(breaks)
        (record[0] / f'{breaks[0]:08X}_0.rdram').write_bytes(ram)
        return SimpleNamespace(hits=[SimpleNamespace(pc=breaks[0],vi=58)], timed_out=False, vis=1800)

    saved = {}

    def checkpoint(item, save=False):
        key = item['rom_start']
        if save:
            saved[key] = item.get('executed', False)
        return None

    monkeypatch.setattr(emulator, 'run', run)
    result = observe(tmp_path, {'rom':{'file':'rom.z64'}}, bytes(rom), [], [], lambda *_: None,
                     candidates=candidates, checkpoint=checkpoint)
    assert calls == [[0x80000500, 0x80000700]]
    assert result[0]['executed'] and 'executed' not in result[1]
    assert saved == {0x100:True, 0x300:False}


def test_a_repeated_body_or_an_existing_rom_function_is_not_proposed():
    rom, segments, functions, ram = fixture()
    repeated = bytearray(rom)
    repeated[0x6000:0x6010] = repeated[0x2000:0x2010]
    assert called(bytes(repeated), segments, functions, [ram, ram]) == []
    assert called(rom, segments, functions+[(0x1ff0,0x2010,0x80001ff0)], [ram, ram]) == []


def test_conflicting_overlay_at_end_of_body_is_not_proposed():
    rom, segments, functions, ram = fixture()
    segments.append(Segment('conflict', 0x7000, 0x7004, 0x8000200c, 'different'))
    assert called(rom, segments, functions, [ram, ram]) == []


def test_closure_follows_calls_from_new_bodies_without_repeating_runs(monkeypatch):
    from decompnet.consoles.n64 import runtime_scoped

    first = dict(rom_start=0x2000, rom_end=0x2010, vram=0x80002000, scope='slot')
    second = dict(rom_start=0x2020, rom_end=0x2030, vram=0x80002020, scope='slot')

    def calls(rom, segments, functions, dumps, *, check):
        starts = {s.rom_start for s in segments}
        return [item for item in (first, second) if item['rom_start'] not in starts
                and (item is first or 0x2000 in starts)]

    monkeypatch.setattr(runtime_scoped, 'called', calls)
    assert runtime_scoped.closure(b'', [], [], [[b'ram']]) == [first, second]
    assert runtime_scoped.closure(b'', [], [], [[b'ram']], reserved=[(0x2000, 0x2010, 0)]) == []


def test_a_static_body_inherits_only_an_unambiguous_overlay_scope():
    from decompnet.consoles.n64.runtime_scoped import inherited_scope

    body = (0x2000, 0x2010, 0x80002000)
    segments = [Segment('first', 0x3000, 0x3040, 0x80003000, 'slot'),
                Segment('other', 0x5000, 0x5100, 0x80002000, 'slot')]
    assert inherited_scope(body, segments) == 'slot'
    segments[1].exclusive_ram_id = 'other'
    assert inherited_scope(body, segments) == ''


def test_runtime_bank_scope_requires_observation_and_a_gap_past_an_overlay():
    from decompnet.consoles.n64.runtime_scoped import separate_runtime_scope
    from decompnet.rom.split import Segment

    segments = [Segment('main', 0x100, 0x200, 0x80000000),
                Segment('overlay', 0x300, 0x380, 0x80000200, 'slot')]
    config = {'segments': [{'type': 'code', 'start': 0x100, 'vram': 0x80000000, 'bss_size': 0x40},
                           {'type': 'bin', 'start': 0x200}, [0x1000]]}
    body = (0x500, 0x510, 0x80000300)
    bank = [(0x500, 0x580, 0x80000300)]
    assert separate_runtime_scope(body, segments, bank, config) == 'runtime_bank_7FFFFE00'
    assert separate_runtime_scope(body, segments, [], config) == ''
    assert separate_runtime_scope((0x500, 0x510, 0x80000210), segments,
                                  [(0x500, 0x580, 0x80000210)], config) == ''
    assert separate_runtime_scope((0x500, 0x510, 0x80000110), segments,
                                  [(0x500, 0x580, 0x80000110)], config) == ''
    assert separate_runtime_scope(body, segments[:1], bank, config) == ''


def test_missing_emulator_is_reported_without_starting_a_game(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import emulator

    rom, segments, _, _ = fixture()
    monkeypatch.setattr(emulator, 'installed', lambda:None)
    messages = []
    assert observe(tmp_path, {'rom':{'file':'rom.z64'}}, rom, segments, [], messages.append) == []
    assert any('not installed' in message for message in messages)
