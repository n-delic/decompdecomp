"""Adding the functions an N64 split left as data to a project that exists (engine find_missed_functions,
consoles/n64/code.py missed_functions), without importing the game again: homemade code, no game data. A racing
game's project gained 16 such functions only by a reimport, which reopens its work."""
import hashlib
import json

import pytest
import yaml

from decompnet.app.engine import RomNetwork
from decompnet.consoles.n64.code import missed_functions
from decompnet.network import NetworkError
from decompnet.rom import split
from decompnet.rom.export import _export_segments

# before the gap: lui $a0, 0x8000; addiu $a0, $a0, 0x1010 (the leaf's address); jr $ra
BEFORE = [0x3c048000, 0x24841010, 0x03e00008, 0]
LEAF = [0x24020001, 0x24420001, 0x03e00008, 0]
AFTER = [0x03e00008, 0]


@pytest.fixture(autouse=True)
def isolated_split_check(monkeypatch):
    # These small hand-written configs exercise recovery itself; Splat's staged split has its own tests.
    from decompnet.consoles.n64 import runtime_staging
    monkeypatch.setattr(runtime_staging, 'verify', lambda *a, **kw: True)


def image():
    data = bytearray(0x2000)
    for rom, words in ((0x1000, BEFORE), (0x1010, LEAF), (0x1020, AFTER)):
        for i, w in enumerate(words):
            data[rom + 4 * i:rom + 4 * i + 4] = w.to_bytes(4, "big")
    return bytes(data)


def splat_config():
    return {"options": {"target_path": "rom.z64"}, "segments": [
        {"type": "bin", "start": 0},
        {"name": "a", "type": "code", "start": 0x1000, "vram": 0x80001000, "subsegments": [[0x1000, "c", "a"]]},
        {"type": "bin", "start": 0x1010},
        {"name": "b", "type": "code", "start": 0x1020, "vram": 0x80001020, "subsegments": [[0x1020, "c", "b"]]},
        {"type": "bin", "start": 0x1028}, [0x2000]]}


def test_the_search_writes_what_it_finds_into_splats_config(tmp_path):
    config = tmp_path / "game.yaml"
    config.write_text(yaml.safe_dump(splat_config()))
    assert missed_functions(config, image(), log=lambda *a: None) == [(0x1010, 0x1020, 0x80001010)]
    after = yaml.safe_load(config.read_text())
    assert (0x1010, 0x1020, 0x80001010) in split.code_ranges(config)
    assert not any(isinstance(s, dict) and s.get("type") == "bin" and s.get("start") == 0x1010 for s in after["segments"])
    # found again, nothing new
    assert missed_functions(config, image(), log=lambda *a: None) == []


def test_scoped_segments_keep_distinct_names_for_splat(tmp_path):
    from decompnet.consoles.n64.code import insert_segments

    cfg = splat_config()
    cfg['segments'][1]['name'] = 'code_80001010'
    config = tmp_path / 'game.yaml'
    config.write_text(yaml.safe_dump(cfg))
    insert_segments(config, cfg, [(0x1010, 0x1020, 0x80001010)], 0x2000, scopes={0x1010: 'slot'})
    result = yaml.safe_load(config.read_text())
    segment = next(s for s in result['segments'] if isinstance(s, dict) and s.get('start') == 0x1010)
    assert segment['name'] == 'code_80001010_1010'
    assert segment['exclusive_ram_id'] == 'slot'
    assert segment['symbol_name_format'] == '$VRAM_code_80001010_1010'
    assert segment['subsegments'][0][2] == segment['name']


def test_a_function_from_its_bytes_labels_its_own_branches():
    code = b"".join(w.to_bytes(4, "big") for w in (0x10800001, 0x24420001, 0x03e00008, 0))  # beqz $a0 to the jr
    fn = split.function_from_bytes("f", code, 0x1010, 0x80001010, "code_80001010")
    assert fn.labels == {0x80001018}
    assert ".L80001018" in split.function_asm(fn) and ". + 4" not in split.function_asm(fn)


def test_atomic_split_write_keeps_the_original_on_replace_failure(tmp_path, monkeypatch):
    import os

    config = tmp_path / 'game.yaml'
    config.write_text('before')
    monkeypatch.setattr(os, 'replace', lambda *a: (_ for _ in ()).throw(OSError('disk error')))
    with pytest.raises(OSError, match='disk error'):
        split.write_config_atomic(config, 'after')
    assert config.read_text() == 'before'
    assert list(tmp_path.iterdir()) == [config]


def project(tmp_path):
    net = RomNetwork(tmp_path / "missed.db", tmp_path)
    rom = image()
    (tmp_path / "rom.z64").write_bytes(rom)
    (tmp_path / "game.yaml").write_text(yaml.safe_dump(splat_config()))
    cfg = {"kind": "rom", "platform": "n64", "big_endian": True, "gp": 0, "compiler": {}, "rom": {"file": "rom.z64"},
           "segments": [vars(split.Segment("a", 0x1000, 0x1010, 0x80001000)),
                        vars(split.Segment("b", 0x1020, 0x1028, 0x80001020))]}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES('game','game','mips',1,?,?)",
                         (str(tmp_path), json.dumps(cfg))).lastrowid
        db.execute("INSERT INTO functions(project_id,name,vram,rom_offset,size,target,status,unit,relocs,asm) "
                   "VALUES(?,'a',?,?,16,?,'matched','a','[]','')", (pid, 0x80001000, 0x1000, rom[0x1000:0x1010]))
        db.execute("INSERT INTO functions(project_id,name,vram,rom_offset,size,target,status,unit,relocs,asm) "
                   "VALUES(?,'b',?,?,8,?,'open','b','[]','')", (pid, 0x80001020, 0x1020, rom[0x1020:0x1028]))
    return net, pid


def test_a_project_gains_the_missed_function_and_keeps_its_work(tmp_path):
    net, pid = project(tmp_path)
    result = net.find_missed_functions("game", log=lambda *a: None)
    assert result["added"] == [{"name": "func_80001010", "vram": 0x80001010, "size": 16}]
    store = net.project_store(pid)
    row = store.one("SELECT * FROM functions WHERE name='func_80001010'")
    assert (row["rom_offset"], row["unit"], row["status"]) == (0x1010, "code_80001010", "open")
    assert row["target"] == image()[0x1010:0x1020]
    assert store.one("SELECT status FROM functions WHERE name='a'")["status"] == "matched"
    assert store.one("SELECT 1 FROM symbols WHERE name='func_80001010' AND kind='func'")
    segments = json.loads(store.one("SELECT config FROM projects WHERE id=?", pid)["config"])["segments"]
    assert [s["name"] for s in segments] == ["a", "code_80001010", "b"]
    assert net._rom(net.project_row("game"))[1][1].rom_end == 0x1020
    # the export's split keeps it as code
    known = [{"vram": r["vram"], "size": r["size"]} for r in store.all("SELECT vram,size FROM functions")]
    _export_segments(yaml.safe_load((tmp_path / "game.yaml").read_text()), known)
    # a second look adds nothing
    assert net.find_missed_functions("game", log=lambda *a: None)["added"] == []


def test_search_continues_when_one_recovery_reveals_another(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import Nintendo64, code

    net, pid = project(tmp_path)
    bodies = [(0x1010, 0x1020, 0x80001010), (0x1030, 0x1040, 0x80001030)]
    known_counts = []
    def recover(self, config, rom, log, known, **kwargs):
        known_counts.append(len(known))
        for body in bodies:
            if body not in known:
                cfg = yaml.safe_load(config.read_text())
                code.insert_segments(config, cfg, [body], len(rom))
                return [body]
        return []

    monkeypatch.setattr(Nintendo64, 'missed_functions', recover)
    result = net.find_missed_functions('game', log=lambda *_: None)
    assert [item['vram'] for item in result['added']] == [bodies[0][2], bodies[1][2]]
    assert known_counts == [2, 3, 4]
    assert net.project_store(pid).one("SELECT status FROM functions WHERE project_id=? AND name='a'", pid)['status'] == 'matched'
    assert 'No further functions' in result['message']


def test_search_checks_cartridge_split_without_treating_appended_banks_as_code_parents(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import Nintendo64

    net, pid = project(tmp_path)
    store = net.project_store(pid)
    appended = bytes.fromhex('03e0000800000000')
    (tmp_path / 'rom.z64').write_bytes(image() + appended)
    with store.tx() as db:
        cfg = json.loads(db.execute('SELECT config FROM projects WHERE id=?', (pid,)).fetchone()['config'])
        cfg['donor_overlays'] = {'123': len(image())}
        cfg['segments'].append(vars(split.Segment('relocatable', len(image()), len(image()) + len(appended), 0)))
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), pid))
        db.execute("INSERT INTO functions(project_id,name,vram,rom_offset,size,target,status,unit,relocs,asm) "
                   "VALUES(?,'bank_entry',0,?,8,?,'matched','relocatable','[]','')", (pid, len(image()), appended))
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,'bank_entry',0,8,'func')", (pid,))

    def recover(self, config, rom, log, known, **kwargs):
        assert rom == image()
        assert len(known) == 2 and all(end <= len(image()) for _, end, _ in known)
        _, symbols, checked = kwargs['prepare_split'](splat_config(), [])
        assert len(checked) == 2 and all('bank_entry' not in line for line in symbols)
        return []

    monkeypatch.setattr(Nintendo64, 'missed_functions', recover)
    assert net.find_missed_functions('game', log=lambda *_: None)['added'] == []
    row = store.one("SELECT target,status FROM functions WHERE project_id=? AND name='bank_entry'", pid)
    assert row['target'] == appended and row['status'] == 'matched'


def test_analysis_bank_change_during_search_is_not_overwritten(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import Nintendo64

    net, pid = project(tmp_path)
    store = net.project_store(pid)
    def recover(self, config, rom, log, known, **kwargs):
        with store.tx() as db:
            cfg = json.loads(db.execute('SELECT config FROM projects WHERE id=?', (pid,)).fetchone()['config'])
            cfg['donor_overlays'] = {'123': len(image())}
            db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), pid))
        return []

    monkeypatch.setattr(Nintendo64, 'missed_functions', recover)
    with pytest.raises(NetworkError, match='mappings changed during the search'):
        net.find_missed_functions('game', log=lambda *_: None)
    assert json.loads(store.one('SELECT config FROM projects WHERE id=?', pid)['config'])['donor_overlays'] == {'123': len(image())}


def test_search_rejects_invalid_analysis_bank_offsets(tmp_path):
    net, pid = project(tmp_path)
    store = net.project_store(pid)
    with store.tx() as db:
        cfg = json.loads(db.execute('SELECT config FROM projects WHERE id=?', (pid,)).fetchone()['config'])
        cfg['donor_overlays'] = {'123': 'not an offset'}
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), pid))
    before = (tmp_path / 'game.yaml').read_text()
    with pytest.raises(NetworkError, match='analysis banks have invalid offsets'):
        net.find_missed_functions('game', log=lambda *_: None)
    assert (tmp_path / 'game.yaml').read_text() == before


def test_recovered_body_inside_a_code_parent_keeps_its_mapping_and_source_unit(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import code

    net, pid = project(tmp_path)
    config = splat_config()
    config['segments'] = [s for s in config['segments'] if not isinstance(s, dict) or s.get('start') != 0x1010]
    (tmp_path / 'game.yaml').write_text(yaml.safe_dump(config))
    store = net.project_store(pid)
    with store.tx() as db:
        cfg = json.loads(db.execute('SELECT config FROM projects WHERE id=?', (pid,)).fetchone()['config'])
        cfg['segments'][0]['rom_end'] = 0x1020
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), pid))
    monkeypatch.setattr(code, '_bodies_inside_code', lambda *a: [(0x1010, 0x1020, 0x80001010)])
    result = net.find_missed_functions('game', log=lambda *_: None)
    assert len(result['added']) == 1
    row = store.one("SELECT unit FROM functions WHERE name='func_80001010'")
    assert row['unit'] == 'a_001010'
    segments = json.loads(store.one('SELECT config FROM projects WHERE id=?', pid)['config'])['segments']
    assert len(segments) == 2
    assert segments[0]['rom_start'] == 0x1000 and segments[0]['rom_end'] == 0x1020


def test_many_recovered_bodies_do_not_accumulate_source_unit_suffixes():
    from decompnet.consoles.n64.code import _cut_inside

    cfg = {'segments': [{'name': 'bank', 'type': 'code', 'start': 0x1000, 'vram': 0x80001000,
                         'subsegments': [[0x1000, 'c', 'bank']]}, [0x10000]]}
    bodies = [(0x1100 + 16 * i, 0x1108 + 16 * i, 0x80001100 + 16 * i) for i in range(300)]
    assert _cut_inside(cfg, bodies)
    names = [sub[2] for sub in cfg['segments'][0]['subsegments']]
    assert len(names) == len(set(names))
    assert max(map(len, names)) < 40
    assert 'bank_001100' in names and 'bank_0023B0' in names


def test_recovered_boot_body_keeps_surrounding_handwritten_assembly():
    from decompnet.consoles.n64.code import _cut_inside

    cfg = {'segments': [{'name': 'entry', 'type': 'code', 'start': 0x1000, 'vram': 0x80000400,
                         'subsegments': [[0x1000, 'hasm']]}, [0x1050]]}
    assert _cut_inside(cfg, [(0x100c, 0x103c, 0x8000040c)])
    assert [(s[0], s[1]) for s in cfg['segments'][0]['subsegments']] == [
        (0x1000, 'hasm'), (0x100c, 'c'), (0x103c, 'hasm')]


def test_boot_body_with_the_wrong_mapping_is_not_split():
    from decompnet.consoles.n64.code import _cut_inside

    cfg = {'segments': [{'name': 'entry', 'type': 'code', 'start': 0x1000, 'vram': 0x80000400,
                         'subsegments': [[0x1000, 'hasm']]}, [0x1050]]}
    assert not _cut_inside(cfg, [(0x100c, 0x103c, 0x8001040c)])
    assert cfg['segments'][0]['subsegments'] == [[0x1000, 'hasm']]


@pytest.mark.parametrize('raw', [[0x1020, 'bin', 'raw'], [0x1020, 'bin']])
@pytest.mark.parametrize('start', [0x1020, 0x1030])
def test_raw_child_recovery_keeps_the_parent_and_surrounding_data(tmp_path, monkeypatch, raw, start):
    from decompnet.consoles.n64 import code, emulator

    cfg = {'options': {'target_path': 'rom.z64'}, 'segments': [
        {'name': 'bank', 'type': 'code', 'start': 0x1000, 'vram': 0x80001000,
         'subsegments': [[0x1000, 'c', 'bank'], raw, [0x1080, 'c', 'later']]},
        [0x1100]]}
    config = tmp_path / 'game.yaml'
    config.write_text(yaml.safe_dump(cfg))
    body = (start, start + 0x10, 0x80001000 + start - 0x1000)
    monkeypatch.setattr(code, 'complete_gaps', lambda *a, **kw: [body])
    monkeypatch.setattr(emulator, 'installed', lambda: None)
    assert missed_functions(config, bytes(0x1100), log=lambda *_: None) == [body]
    result = yaml.safe_load(config.read_text())
    assert len(result['segments']) == 2
    subs = result['segments'][0]['subsegments']
    expected = [(0x1000, 'c')]
    if start != 0x1020:
        expected.append((0x1020, 'bin'))
    expected.extend([(start, 'c'), (start + 0x10, 'bin'), (0x1080, 'c')])
    assert [(s[0], s[1]) for s in subs] == expected
    assert split.code_ranges(config) == [(0x1000, 0x1020, 0x80001000), body,
                                         (0x1080, 0x1100, 0x80001080)]


def test_raw_child_with_a_different_mapping_is_deferred(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import code, emulator

    cfg = {'segments': [{'name': 'bank', 'type': 'code', 'start': 0x1000, 'vram': 0x80001000,
                         'subsegments': [[0x1000, 'c', 'bank'], [0x1020, 'bin', 'raw']]}, [0x1100]]}
    config = tmp_path / 'game.yaml'
    config.write_text(yaml.safe_dump(cfg))
    before = config.read_text()
    monkeypatch.setattr(code, 'complete_gaps', lambda *a, **kw: [(0x1020, 0x1030, 0x80101020)])
    monkeypatch.setattr(emulator, 'installed', lambda: None)
    log = []
    assert missed_functions(config, bytes(0x1100), log=log.append) == []
    assert config.read_text() == before
    assert any('parent mapping' in line for line in log)


def test_a_raw_child_ending_at_the_parent_boundary_needs_no_extra_marker():
    from decompnet.consoles.n64.code import _cut_inside

    cfg = {'segments': [{'name': 'bank', 'type': 'code', 'start': 0x1000, 'vram': 0x80001000,
                         'subsegments': [[0x1000, 'c', 'bank'], [0x1020, 'bin']]}, [0x1030]]}
    assert _cut_inside(cfg, [(0x1020, 0x1030, 0x80001020)])
    assert [(s[0], s[1]) for s in cfg['segments'][0]['subsegments']] == [(0x1000, 'c'), (0x1020, 'c')]


def test_recovered_cut_keeps_startless_bss_in_virtual_address_order():
    from decompnet.consoles.n64.code import _cut_inside

    bss = {'type': 'bss', 'vram': 0x80001100}
    cfg = {'segments': [{'name': 'bank', 'type': 'code', 'start': 0x1000, 'vram': 0x80001000,
                         'subsegments': [[0x1000, 'c', 'bank'], [0x1080, 'data', 'data'], bss]}, [0x1100]]}
    assert _cut_inside(cfg, [(0x1020, 0x1030, 0x80001020)])
    subs = cfg['segments'][0]['subsegments']
    assert [s[0] for s in subs[:-1]] == [0x1000, 0x1020, 0x1030, 0x1080]
    assert subs[-1] == bss


def test_search_anchors_existing_entries_if_a_new_cut_would_hide_them(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import code, emulator, runtime_staging

    cfg = {'segments': [{'name': 'bank', 'type': 'code', 'start': 0x1000, 'vram': 0x80001000,
                         'subsegments': [[0x1000, 'c', 'bank']]}, [0x1100]]}
    config = tmp_path / 'game.yaml'
    config.write_text(yaml.safe_dump(cfg))
    body = (0x1030, 0x1040, 0x80001030)
    old = {'name': 'old', 'rom_offset': 0x1060, 'size': 0x10, 'vram': 0x80001060}
    known = [(0x1000, 0x1010, 0x80001000), (0x1060, 0x1070, 0x80001060)]
    monkeypatch.setattr(code, 'complete_gaps', lambda *a, **kw: [])
    monkeypatch.setattr(code, '_bodies_inside_code', lambda *a: [body])
    monkeypatch.setattr(emulator, 'installed', lambda: None)
    accepted = []

    def verify(config, rom, proposed, candidates, log, **kwargs):
        if not any(s[0] == old['rom_offset'] for s in proposed['segments'][0]['subsegments']):
            kwargs['on_conflict']([old])
            kwargs['on_error']('the staged split changed or removed an existing function')
            return False
        accepted.append(yaml.safe_load(yaml.safe_dump(proposed)))
        return True

    monkeypatch.setattr(runtime_staging, 'verify', verify)
    assert missed_functions(config, bytes(0x1100), log=lambda *_: None, known_functions=known) == [body]
    assert yaml.safe_load(config.read_text()) == accepted[-1]


@pytest.mark.parametrize(('caller', 'accepted'), [('same', True), ('absent', False), ('other', False)])
def test_called_body_in_rodata_is_staged_without_replacing_surrounding_data(tmp_path, monkeypatch, caller, accepted):
    from decompnet.consoles.n64 import code, emulator

    cfg = {'segments': [{'name': 'bank', 'type': 'code', 'start': 0x1000, 'vram': 0x80001000,
                         'subsegments': [[0x1000, 'c', 'bank'], [0x1020, 'rodata', 'constants'],
                                         [0x1080, 'c', 'later']]}, [0x1100]]}
    config = tmp_path / 'game.yaml'
    config.write_text(yaml.safe_dump(cfg))
    body = (0x1030, 0x1040, 0x80001030)
    known = [(0x1000, 0x1010, 0x80001000), (0x1080, 0x1090, 0x80001080)]
    if caller == 'other':
        known[0] = (0x1000, 0x1010, 0x80011000)
    rom = bytearray(0x1100)
    if caller != 'absent':
        rom[0x1000:0x1004] = (0x0c00040c).to_bytes(4, 'big')  # jal 0x80001030
    monkeypatch.setattr(code, 'complete_gaps', lambda *a, **kw: [body])
    monkeypatch.setattr(code, '_bodies_inside_code', lambda *a: [])
    monkeypatch.setattr(emulator, 'installed', lambda: None)
    result = missed_functions(config, bytes(rom), log=lambda *_: None, known_functions=known)
    assert result == ([body] if accepted else [])
    subs = yaml.safe_load(config.read_text())['segments'][0]['subsegments']
    if accepted:
        assert [(s[0], s[1]) for s in subs] == [(0x1000, 'c'), (0x1020, 'rodata'),
                                                 (0x1030, 'c'), (0x1040, 'rodata'), (0x1080, 'c')]
    else:
        assert subs == cfg['segments'][0]['subsegments']


def test_import_segment_in_a_raw_child_stays_inside_its_existing_parent(tmp_path):
    from decompnet.consoles.n64.code import insert_segments
    from decompnet.rom import RomError

    cfg = {'segments': [{'name': 'bank', 'type': 'code', 'start': 0x1000, 'vram': 0x80001000,
                         'subsegments': [[0x1000, 'c', 'bank'], [0x1020, 'bin'], [0x1080, 'c', 'later']]},
                        [0x1100]]}
    path = tmp_path / 'game.yaml'
    path.write_text(yaml.safe_dump(cfg))
    insert_segments(path, cfg, [(0x1030, 0x1040, 0x80001030)], 0x1100)
    result = yaml.safe_load(path.read_text())
    assert len(result['segments']) == 2
    assert [(s[0], s[1]) for s in result['segments'][0]['subsegments']] == [
        (0x1000, 'c'), (0x1020, 'bin'), (0x1030, 'c'), (0x1040, 'bin'), (0x1080, 'c')]
    before = path.read_text()
    with pytest.raises(RomError, match='different mapping'):
        insert_segments(path, result, [(0x1050, 0x1060, 0x80101050)], 0x1100)
    assert path.read_text() == before


def test_recovered_overlay_retains_scope_and_existing_symbol_names(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import Nintendo64

    net, pid = project(tmp_path)
    store = net.project_store(pid)
    with store.tx() as db:
        cfg = json.loads(db.execute('SELECT config FROM projects WHERE id=?', (pid,)).fetchone()['config'])
        cfg['segments'][1].update(vram=0x80001010, exclusive_ram_id='slot')
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), pid))
        db.execute("UPDATE functions SET vram=? WHERE name='b'", (0x80001010,))
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,'b',?,8,'func')",
                   (pid, 0x80001010))
    symbols = net.symbols(pid)

    def recover(self, config, image, log, known, **kwargs):
        cfg = splat_config()
        cfg['segments'][2] = {'name': 'recovered', 'type': 'code', 'start': 0x1010,
                              'vram': 0x80001010, 'exclusive_ram_id': 'slot',
                              'subsegments': [[0x1010, 'c', 'recovered']]}
        cfg['segments'][3].update(vram=0x80001010, exclusive_ram_id='slot')
        config.write_text(yaml.safe_dump(cfg))
        return [(0x1010, 0x1020, 0x80001010)]

    monkeypatch.setattr(Nintendo64, 'missed_functions', recover)
    result = net.find_missed_functions('game', log=lambda *_: None)
    assert len(result['added']) == 1
    assert 'b' in symbols.by_name
    assert {'b', 'func_80001010'} <= net.symbols(pid).by_name.keys()
    segments = json.loads(store.one('SELECT config FROM projects WHERE id=?', pid)['config'])['segments']
    recovered = next(segment for segment in segments if segment['rom_start'] == 0x1010)
    assert recovered['exclusive_ram_id'] == 'slot'
    assert store.one("SELECT status FROM functions WHERE name='a'")['status'] == 'matched'


@pytest.mark.parametrize('accepted', [True, False])
def test_runtime_scoped_candidates_require_a_verified_staged_split(tmp_path, monkeypatch, accepted):
    from decompnet.consoles.n64 import code, emulator, loaded_code, runtime_scoped, runtime_staging

    net, pid = project(tmp_path)
    config = splat_config()
    config['segments'][1]['exclusive_ram_id'] = 'slot'
    config['segments'][3].update(exclusive_ram_id='slot', vram=0x80001010)
    (tmp_path / 'game.yaml').write_text(yaml.safe_dump(config))
    monkeypatch.setattr(code, 'complete_gaps', lambda *a, **kw: [])
    monkeypatch.setattr(code, '_bodies_inside_code', lambda *a, **kw: [])
    monkeypatch.setattr(emulator, 'installed', lambda: tmp_path)

    def sample(*args, inspect_dumps, **kwargs):
        inspect_dumps([b'one', b'two'])
        return {'segments': []}

    candidate = {'rom_start': 0x1010, 'rom_end': 0x1020, 'vram': 0x80001010,
                 'scope': 'slot', 'co_resident_snapshots': 2}
    monkeypatch.setattr(loaded_code, 'find', sample)
    monkeypatch.setattr(loaded_code, 'moved_banks', lambda *a, **kw: [])
    monkeypatch.setattr(runtime_scoped, 'closure', lambda rom, segments, functions, *a, **kw:
                        [] if (0x1010, 0x1020, 0x80001010) in functions else [candidate])
    checked = []

    def verify(config, rom, proposed, candidates, log, **kwargs):
        checked.append(proposed)
        return accepted

    monkeypatch.setattr(runtime_staging, 'verify', verify)
    result = net.find_missed_functions('game', log=lambda *_: None)
    assert len(checked) == 1
    assert len(result['added']) == int(accepted)
    persisted = yaml.safe_load((tmp_path / 'game.yaml').read_text())
    actual = next(s for s in persisted['segments'] if isinstance(s, dict) and s.get('start') == 0x1010)
    if accepted:
        assert actual['exclusive_ram_id'] == 'slot'
        assert net.project_store(pid).one("SELECT status FROM functions WHERE name='a'")['status'] == 'matched'
    else:
        assert actual['type'] == 'bin'


@pytest.mark.parametrize('accepted', [True, False])
def test_observed_overlapping_bank_is_staged_in_its_own_scope(tmp_path, monkeypatch, accepted):
    from decompnet.consoles.n64 import code, emulator, loaded_code, loader_code, runtime_staging

    config = tmp_path / 'game.yaml'
    original = yaml.safe_dump(splat_config())
    config.write_text(original)
    rom = image()
    (tmp_path / 'rom.z64').write_bytes(rom)
    monkeypatch.setattr(code, 'complete_gaps', lambda *a, **kw: [])
    monkeypatch.setattr(code, '_bodies_inside_code', lambda *a, **kw: [])
    monkeypatch.setattr(emulator, 'installed', lambda: tmp_path)
    monkeypatch.setattr(loaded_code, 'moved_banks', lambda *a, **kw: [])
    bank = (0x1010, 0x1020, 0x80001000)
    monkeypatch.setattr(loaded_code, 'find', lambda *a, **kw: {'segments': [], 'overlap_segments': [bank]})
    monkeypatch.setattr(loader_code, 'complete', lambda rom, established, banks, *a, **kw: established + banks)
    monkeypatch.setattr(runtime_staging, 'verify', lambda *a, **kw: accepted)
    known = [(0x1000, 0x1010, 0x80001000), (0x1020, 0x1028, 0x80001020)]
    result = missed_functions(config, rom, log=lambda *_: None, known_functions=known)
    if accepted:
        assert result == [bank]
        segments = yaml.safe_load(config.read_text())['segments']
        recovered = next(s for s in segments if isinstance(s, dict) and s.get('start') == bank[0])
        assert recovered['exclusive_ram_id'] == 'runtime_overlap_00001010'
        existing = next(s for s in segments if isinstance(s, dict) and s.get('start') == 0x1000)
        assert existing['exclusive_ram_id'] == recovered['exclusive_ram_id']
    else:
        assert result == []
        assert config.read_text() == original


@pytest.mark.parametrize('setting', [{'bss_size': 0x20}, {'symbol_name_format': 'custom_$VRAM'}])
def test_runtime_overlap_keeps_existing_layout_and_custom_names(tmp_path, monkeypatch, setting):
    from decompnet.consoles.n64 import code, emulator, loaded_code, loader_code

    cfg = splat_config()
    cfg['segments'][1].update(setting)
    config = tmp_path / 'game.yaml'
    original = yaml.safe_dump(cfg)
    config.write_text(original)
    rom = image()
    (tmp_path / 'rom.z64').write_bytes(rom)
    monkeypatch.setattr(code, 'complete_gaps', lambda *a, **kw: [])
    monkeypatch.setattr(code, '_bodies_inside_code', lambda *a, **kw: [])
    monkeypatch.setattr(emulator, 'installed', lambda: tmp_path)
    monkeypatch.setattr(loaded_code, 'moved_banks', lambda *a, **kw: [])
    bank = (0x1010, 0x1020, 0x80001000)
    monkeypatch.setattr(loaded_code, 'find', lambda *a, **kw: {'segments': [], 'overlap_segments': [bank]})
    monkeypatch.setattr(loader_code, 'complete', lambda rom, established, banks, *a, **kw: established + banks)
    known = [(0x1000, 0x1010, 0x80001000), (0x1020, 0x1028, 0x80001020)]
    assert missed_functions(config, rom, log=lambda *_: None, known_functions=known) == []
    assert config.read_text() == original


def test_connected_observed_banks_share_one_scope_and_conflicting_scopes_are_deferred():
    from decompnet.consoles.n64.code import _observed_overlap_scopes

    banks = [(0x2000, 0x2040, 0x80001000), (0x3000, 0x3040, 0x80001030)]
    known = [split.Segment('old', 0x1000, 0x1040, 0x80001060)]
    cfg = {'segments': [{'name': 'old', 'start': 0x1000, 'type': 'code'}]}
    scopes, existing = _observed_overlap_scopes(banks, [banks[0]], known, cfg)
    assert scopes[banks[0]] == scopes[banks[1]] == existing[0x1000]
    known.append(split.Segment('other', 0x4000, 0x4040, 0x80001020, 'other_scope'))
    known[0].exclusive_ram_id = 'old_scope'
    scopes, existing = _observed_overlap_scopes(banks, [banks[0]], known, cfg)
    assert not scopes and not existing


def test_static_discovery_in_a_scoped_bank_keeps_its_scope(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import code, emulator, runtime_staging

    net, pid = project(tmp_path)
    cfg = splat_config()
    cfg['segments'][1]['exclusive_ram_id'] = 'slot'
    cfg['segments'][3].update(exclusive_ram_id='slot', vram=0x80001010)
    (tmp_path / 'game.yaml').write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(code, 'complete_gaps', lambda *a, **kw: [(0x1010, 0x1020, 0x80001010)])
    monkeypatch.setattr(code, '_bodies_inside_code', lambda *a, **kw: [])
    monkeypatch.setattr(emulator, 'installed', lambda: None)
    monkeypatch.setattr(runtime_staging, 'verify', lambda *a, **kw: True)
    result = net.find_missed_functions('game', log=lambda *_: None)
    assert len(result['added']) == 1
    stored = json.loads(net.project_store(pid).one('SELECT config FROM projects WHERE id=?', pid)['config'])
    segment = next(s for s in stored['segments'] if s['rom_start'] == 0x1010)
    assert segment['exclusive_ram_id'] == 'slot'


def test_a_failed_combined_split_does_not_publish_other_gap_cuts(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import code, emulator, loaded_code, runtime_scoped, runtime_staging

    net, pid = project(tmp_path)
    cfg = splat_config()
    cfg['segments'][1]['exclusive_ram_id'] = 'slot'
    (tmp_path / 'game.yaml').write_text(yaml.safe_dump(cfg))
    before = (tmp_path / 'game.yaml').read_text()
    monkeypatch.setattr(code, 'complete_gaps', lambda *a, **kw: [])
    monkeypatch.setattr(code, '_bodies_inside_code', lambda *a, **kw: [(0x1004, 0x1008, 0x80001004)])
    monkeypatch.setattr(emulator, 'installed', lambda: tmp_path)
    monkeypatch.setattr(loaded_code, 'moved_banks', lambda *a, **kw: [])

    def sample(*args, inspect_dumps, **kwargs):
        inspect_dumps([b'one', b'two'])
        return {'segments': []}

    monkeypatch.setattr(loaded_code, 'find', sample)
    candidate = {'rom_start': 0x1010, 'rom_end': 0x1020, 'vram': 0x80001010, 'scope': 'slot'}
    monkeypatch.setattr(runtime_scoped, 'closure', lambda *a, **kw: [candidate])
    monkeypatch.setattr(runtime_staging, 'verify', lambda *a, **kw: False)
    assert net.find_missed_functions('game', log=lambda *_: None)['added'] == []
    assert (tmp_path / 'game.yaml').read_text() == before
    assert not net.project_store(pid).one("SELECT 1 FROM functions WHERE name='func_80001010'")


def test_unscoped_gap_recovery_also_requires_a_valid_split(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import emulator, runtime_staging

    net, pid = project(tmp_path)
    config = tmp_path / 'game.yaml'
    before = config.read_text()
    monkeypatch.setattr(emulator, 'installed', lambda: None)
    checked = []
    monkeypatch.setattr(runtime_staging, 'verify', lambda *args, **kwargs: checked.append(args[3]) or False)
    assert net.find_missed_functions('game', log=lambda *_: None)['added'] == []
    assert len(checked) == 1 and checked[0][0]['rom_start'] == 0x1010
    assert config.read_text() == before
    assert not net.project_store(pid).one("SELECT 1 FROM functions WHERE name='func_80001010'")


def test_one_invalid_candidate_does_not_block_other_recovered_bodies(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import code, emulator, runtime_staging

    net, pid = project(tmp_path)
    monkeypatch.setattr(emulator, 'installed', lambda: None)
    monkeypatch.setattr(code, '_bodies_inside_code', lambda *a, **kw: [])
    monkeypatch.setattr(code, 'complete_gaps', lambda *a, **kw: [
        (0x1010, 0x1020, 0x80001010), (0x1100, 0x1110, 0x80001100)])
    monkeypatch.setattr(runtime_staging, 'verify',
                        lambda config, image, proposed, candidates, log, **kw:
                        all(item['rom_start'] != 0x1010 for item in candidates))
    messages = []
    result = net.find_missed_functions('game', log=messages.append)
    assert [item['vram'] for item in result['added']] == [0x80001100]
    assert net.project_store(pid).one("SELECT 1 FROM functions WHERE name='func_80001100'")
    assert any('1 candidate body could not be added safely' in line for line in messages)


def test_a_failed_baseline_does_not_retry_each_candidate(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import code, emulator, runtime_staging

    net, _ = project(tmp_path)
    monkeypatch.setattr(emulator, 'installed', lambda: None)
    monkeypatch.setattr(code, '_bodies_inside_code', lambda *a, **kw: [])
    monkeypatch.setattr(code, 'complete_gaps', lambda *a, **kw: [
        (0x1010, 0x1020, 0x80001010), (0x1100, 0x1110, 0x80001100)])
    calls = []

    def unavailable(*args, on_failure, **kwargs):
        calls.append(1)
        on_failure(True)
        return False

    monkeypatch.setattr(runtime_staging, 'verify', unavailable)
    assert net.find_missed_functions('game', log=lambda *_: None)['added'] == []
    assert len(calls) == 1


def test_stopped_search_resumes_previously_checked_bodies(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import code, emulator, runtime_staging

    net, pid = project(tmp_path)
    monkeypatch.setattr(emulator, 'installed', lambda: None)
    monkeypatch.setattr(code, '_bodies_inside_code', lambda *a, **kw: [])
    monkeypatch.setattr(code, 'complete_gaps', lambda *a, **kw: [
        (0x1010, 0x1020, 0x80001010), (0x1100, 0x1110, 0x80001100)])
    monkeypatch.setattr(runtime_staging, 'verify',
                        lambda config, image, proposed, candidates, log, **kw:
                        all(item['rom_start'] != 0x1010 for item in candidates))
    store = net.project_store(pid)
    key = f'missed-functions-checkpoint:{pid}'
    before = (tmp_path / 'game.yaml').read_text()

    class StopAfterCheckpoint:
        def __call__(self, *_):
            pass

        def check_cancel(self):
            if store.one('SELECT 1 FROM settings WHERE key=?', key):
                raise RuntimeError('stopped')

    with pytest.raises(RuntimeError, match='stopped'):
        net.find_missed_functions('game', log=StopAfterCheckpoint())
    assert (tmp_path / 'game.yaml').read_text() == before
    assert store.one('SELECT 1 FROM settings WHERE key=?', key)
    assert not store.one("SELECT 1 FROM functions WHERE name='func_80001100'")
    messages = []
    result = net.find_missed_functions('game', log=messages.append)
    assert [item['vram'] for item in result['added']] == [0x80001100]
    assert any('Resuming with 1 previously checked' in line for line in messages)
    assert not store.one('SELECT 1 FROM settings WHERE key=?', key)


def test_changed_split_does_not_reuse_an_old_search_checkpoint(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import code, emulator, runtime_staging

    net, pid = project(tmp_path)
    monkeypatch.setattr(emulator, 'installed', lambda: None)
    monkeypatch.setattr(code, '_bodies_inside_code', lambda *a, **kw: [])
    monkeypatch.setattr(code, 'complete_gaps', lambda *a, **kw: [
        (0x1010, 0x1020, 0x80001010), (0x1100, 0x1110, 0x80001100)])
    monkeypatch.setattr(runtime_staging, 'verify',
                        lambda config, image, proposed, candidates, log, **kw:
                        all(item['rom_start'] != 0x1010 for item in candidates))
    store = net.project_store(pid)
    key = f'missed-functions-checkpoint:{pid}'

    class StopAfterCheckpoint:
        def __call__(self, *_):
            pass

        def check_cancel(self):
            if store.one('SELECT 1 FROM settings WHERE key=?', key):
                raise RuntimeError('stopped')

    with pytest.raises(RuntimeError, match='stopped'):
        net.find_missed_functions('game', log=StopAfterCheckpoint())
    config = tmp_path / 'game.yaml'
    config.write_text(config.read_text() + '\n# changed between searches\n')
    messages = []
    result = net.find_missed_functions('game', log=messages.append)
    assert [item['vram'] for item in result['added']] == [0x80001100]
    assert not any('Resuming with' in line for line in messages)


def test_a_concurrent_config_edit_is_not_overwritten(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import emulator, runtime_staging

    net, pid = project(tmp_path)
    monkeypatch.setattr(emulator, 'installed', lambda: None)
    config = tmp_path / 'game.yaml'
    external = config.read_text() + '\n# changed by another editor\n'

    def verify(*args, **kwargs):
        config.write_text(external)
        return True

    monkeypatch.setattr(runtime_staging, 'verify', verify)
    with pytest.raises(ValueError, match='configuration changed during the search'):
        net.find_missed_functions('game', log=lambda *_: None)
    assert config.read_text() == external
    assert not net.project_store(pid).one("SELECT 1 FROM functions WHERE name='func_80001010'")


def test_a_concurrent_function_change_rolls_back_only_our_config(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import emulator, runtime_staging

    net, pid = project(tmp_path)
    monkeypatch.setattr(emulator, 'installed', lambda: None)
    config = tmp_path / 'game.yaml'
    before = config.read_text()

    def verify(*args, **kwargs):
        with net.project_store(pid).tx() as db:
            db.execute("UPDATE functions SET name='renamed' WHERE name='a'")
        return True

    monkeypatch.setattr(runtime_staging, 'verify', verify)
    with pytest.raises(NetworkError, match='code or mappings changed'):
        net.find_missed_functions('game', log=lambda *_: None)
    assert config.read_text() == before
    assert net.project_store(pid).one("SELECT 1 FROM functions WHERE name='renamed'")
    assert not net.project_store(pid).one("SELECT 1 FROM functions WHERE name='func_80001010'")


def test_concurrent_status_and_source_updates_survive_the_search(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import emulator, runtime_staging

    net, pid = project(tmp_path)
    monkeypatch.setattr(emulator, 'installed', lambda: None)

    def verify(*args, **kwargs):
        with net.project_store(pid).tx() as db:
            db.execute("UPDATE functions SET status='asm' WHERE name='a'")
            db.execute("UPDATE functions SET status='matched', matched_source='new work' WHERE name='b'")
        return True

    monkeypatch.setattr(runtime_staging, 'verify', verify)
    result = net.find_missed_functions('game', log=lambda *_: None)
    assert len(result['added']) == 1
    store = net.project_store(pid)
    assert store.one("SELECT status FROM functions WHERE name='a'")['status'] == 'asm'
    row = store.one("SELECT status,matched_source FROM functions WHERE name='b'")
    assert (row['status'], row['matched_source']) == ('matched', 'new work')


def test_a_concurrent_symbol_change_is_not_overwritten(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import emulator, runtime_staging

    net, pid = project(tmp_path)
    monkeypatch.setattr(emulator, 'installed', lambda: None)
    config = tmp_path / 'game.yaml'
    before = config.read_text()

    def verify(*args, **kwargs):
        with net.project_store(pid).tx() as db:
            db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,'new_data',?,4,'data')",
                       (pid, 0x80001800))
        return True

    monkeypatch.setattr(runtime_staging, 'verify', verify)
    with pytest.raises(NetworkError, match='code or mappings changed'):
        net.find_missed_functions('game', log=lambda *_: None)
    assert config.read_text() == before
    assert net.project_store(pid).one("SELECT 1 FROM symbols WHERE name='new_data'")


def test_split_check_rejects_an_existing_target_that_differs_from_the_image(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import emulator, runtime_staging

    net, pid = project(tmp_path)
    with net.project_store(pid).tx() as db:
        db.execute("UPDATE functions SET target=? WHERE name='a'", (b'wrong',))
    monkeypatch.setattr(emulator, 'installed', lambda: None)

    def verify(config, image, proposed, candidates, log, *, prepare, **kwargs):
        with pytest.raises(ValueError, match='does not match its original image bytes'):
            prepare(proposed, candidates)
        return False

    monkeypatch.setattr(runtime_staging, 'verify', verify)
    assert net.find_missed_functions('game', log=lambda *_: None)['added'] == []


@pytest.mark.parametrize('scope', ['slot', ''])
def test_static_discovery_overlapping_another_mapping_is_deferred(tmp_path, monkeypatch, scope):
    from decompnet.consoles.n64 import code, emulator

    net, pid = project(tmp_path)
    cfg = splat_config()
    cfg['segments'][3].update(exclusive_ram_id=scope, vram=0x80001010)
    config = tmp_path / 'game.yaml'
    config.write_text(yaml.safe_dump(cfg))
    before = config.read_text()
    monkeypatch.setattr(code, 'complete_gaps', lambda *a, **kw: [(0x1010, 0x1020, 0x80001010)])
    monkeypatch.setattr(code, '_bodies_inside_code', lambda *a, **kw: [])
    monkeypatch.setattr(emulator, 'installed', lambda: None)
    messages = []
    assert net.find_missed_functions('game', log=messages.append)['added'] == []
    assert any('scope is ambiguous' in message for message in messages)
    assert config.read_text() == before
    assert not net.project_store(pid).one("SELECT 1 FROM functions WHERE name='func_80001010'")


def test_cancelling_while_saving_rolls_back_the_split_and_symbols(tmp_path):
    net, pid = project(tmp_path)
    config = tmp_path / 'game.yaml'
    before = config.read_text()

    class Log:
        checks = 0

        def __call__(self, *_):
            pass

        def check_cancel(self):
            self.checks += 1
            if self.checks == 2:
                raise RuntimeError('cancelled')

    # The normal scan has its own cancellation points. Isolate the commit to cancel after its first insert.
    from decompnet.consoles.n64 import Nintendo64
    original = Nintendo64.missed_functions

    def scan_without_check(self, config, image, log, known, **kwargs):
        return original(self, config, image, lambda *_: None, known, **kwargs)

    from unittest.mock import patch
    with patch.object(Nintendo64, 'missed_functions', scan_without_check):
        with pytest.raises(RuntimeError, match='cancelled'):
            net.find_missed_functions('game', log=Log())
    assert config.read_text() == before
    assert not net.project_store(pid).one("SELECT 1 FROM functions WHERE name='func_80001010'")
    assert 'func_80001010' not in net.symbols(pid).by_name


def test_a_console_without_the_search_says_so(tmp_path, monkeypatch):
    net, _ = project(tmp_path)
    from decompnet.consoles import Console
    from decompnet.consoles.n64 import Nintendo64
    monkeypatch.setattr(Nintendo64, "missed_functions", Console.missed_functions)
    before = (tmp_path / "game.yaml").read_text()
    with pytest.raises(NetworkError, match="isn't supported"):
        net.find_missed_functions("game", log=lambda *a: None)
    assert (tmp_path / "game.yaml").read_text() == before


def test_recovered_sdk_is_identified_before_the_assembly_fallback(tmp_path, monkeypatch):
    from decompnet.app.source_assembly import verified
    from decompnet.consoles.n64 import Nintendo64

    code = image()[0x1010:0x1020]
    identity = {"name": "sdk_leaf", "names": ["sdk_leaf"], "corpus": "test SDK",
                "target_sha256": hashlib.sha256(code).hexdigest()}
    monkeypatch.setattr(Nintendo64, "sdk_assembly_identity", lambda self, body: identity if body == code else None)
    monkeypatch.setattr(Nintendo64, "asm_reason", lambda self, body: "generic assembly")
    net, pid = project(tmp_path)
    net.find_missed_functions("game", log=lambda *a: None)
    row = net.project_store(pid).one("SELECT * FROM functions WHERE name='func_80001010'")
    meta = json.loads(row["meta"])
    assert row["status"] == "asm"
    assert meta["verified_assembly"]["kind"] == "sdk"
    assert meta["sdk_display"]["name"] == "sdk_leaf"
    assert meta["asm_code"] != "generic assembly"
    assert verified(net.project_row("game"), row)


def test_recovered_unidentified_assembly_is_set_aside(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import Nintendo64

    monkeypatch.setattr(Nintendo64, "asm_reason", lambda self, body: "generic assembly")
    net, pid = project(tmp_path)
    net.find_missed_functions("game", log=lambda *a: None)
    row = net.project_store(pid).one("SELECT status,meta FROM functions WHERE name='func_80001010'")
    assert row["status"] == "asm"
    assert json.loads(row["meta"])["asm_code"] == "generic assembly"


def test_existing_project_gains_every_entry_of_a_runtime_verified_trap_table(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import emulator, loaded_code

    net, pid = project(tmp_path)
    table = b''.join((8).to_bytes(4, 'big') + (0x24080000 | i).to_bytes(4, 'big') for i in range(12))
    rom = bytearray(image())
    rom[0x1200:0x1200 + len(table)] = table
    (tmp_path / 'rom.z64').write_bytes(rom)
    monkeypatch.setattr(emulator, 'installed', lambda: tmp_path)
    monkeypatch.setattr(loaded_code, 'find', lambda *a, **kw: {
        'segments': [(0x1200, 0x1200 + len(table), 0x80201000)]})
    result = net.find_missed_functions('game', log=lambda *_: None)
    recovered = [row for row in result['added'] if 0x80201000 <= row['vram'] < 0x80201000 + len(table)]
    assert len(recovered) == 12
    store = net.project_store(pid)
    rows = store.all('SELECT status,target FROM functions WHERE vram>=? AND vram<? ORDER BY vram',
                     0x80201000, 0x80201000 + len(table))
    assert len(rows) == 12 and all(row['status'] == 'asm' for row in rows)
    assert b''.join(row['target'] for row in rows) == table
    assert store.one("SELECT status FROM functions WHERE name='a'")['status'] == 'matched'
    assert len([r for r in split.code_ranges(tmp_path / 'game.yaml') if 0x1200 <= r[0] < 0x1200 + len(table)]) == 12


def test_existing_project_recovers_called_code_from_a_runtime_observed_range(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import emulator, loaded_code

    net, pid = project(tmp_path)
    rom = bytearray(image())
    address = 0x80201000
    caller = [0x0c000000 | ((address >> 2) & 0x03ffffff), 0, 0x03e00008, 0]
    body = [0x27bdffe8, 0xafbf0014, 0x24020007, 0x8fbf0014, 0x03e00008, 0x27bd0018]
    for index, word in enumerate(caller):
        rom[0x1000+index*4:0x1004+index*4] = word.to_bytes(4, 'big')
    for index, word in enumerate(body):
        rom[0x1200+index*4:0x1204+index*4] = word.to_bytes(4, 'big')
    (tmp_path/'rom.z64').write_bytes(rom)
    with net.project_store(pid).tx() as db:
        db.execute("UPDATE functions SET target=? WHERE project_id=? AND name='a'", (bytes(rom[0x1000:0x1010]), pid))
    monkeypatch.setattr(emulator, 'installed', lambda: tmp_path)
    monkeypatch.setattr(loaded_code, 'find', lambda *a, **kw: {'segments': [(0x1200, 0x1218, address)]})
    result = net.find_missed_functions('game', log=lambda *_: None)
    assert any(row['vram'] == address and row['size'] == 24 for row in result['added'])
    row = net.project_store(pid).one('SELECT target FROM functions WHERE project_id=? AND vram=?', pid, address)
    assert row['target'] == bytes(rom[0x1200:0x1218])
    assert net.project_store(pid).one("SELECT status FROM functions WHERE name='a'")['status'] == 'matched'


def test_existing_project_does_not_run_a_different_image(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import emulator, loaded_code

    config = tmp_path / 'game.yaml'
    config.write_text(yaml.safe_dump(splat_config()))
    (tmp_path / 'rom.z64').write_bytes(b'not the analysed image')
    monkeypatch.setattr(emulator, 'installed', lambda: tmp_path)
    monkeypatch.setattr(loaded_code, 'find', lambda *a, **kw: pytest.fail('ran the wrong image'))
    assert missed_functions(config, image(), log=lambda *_: None) == [(0x1010, 0x1020, 0x80001010)]


def test_stopping_the_runtime_scan_leaves_the_split_unchanged(tmp_path, monkeypatch):
    import threading

    from decompnet.consoles.n64 import emulator, loaded_code

    config = tmp_path / 'game.yaml'
    config.write_text(yaml.safe_dump(splat_config()))
    before = config.read_text()
    (tmp_path / 'rom.z64').write_bytes(image())

    class Log:
        cancel_event = threading.Event()

        def __call__(self, *_):
            pass

        def check_cancel(self):
            if self.cancel_event.is_set():
                raise RuntimeError('Stopped')

    log = Log()
    monkeypatch.setattr(emulator, 'installed', lambda: tmp_path)

    def stop_during_run(*args, stop, **kwargs):
        log.cancel_event.set()
        assert stop()
        return {'segments': []}

    monkeypatch.setattr(loaded_code, 'find', stop_during_run)
    with pytest.raises(RuntimeError, match='Stopped'):
        missed_functions(config, image(), log=log)
    assert config.read_text() == before


def test_runtime_evidence_defers_a_mapped_bank_without_rewriting_existing_addresses(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import code, emulator, loaded_code

    rom = bytearray(0x2000)
    rom[0x1040:0x1060] = bytes(range(32))
    rom[0x1060:0x1080] = bytes(range(32, 64))
    (tmp_path / 'rom.z64').write_bytes(rom)
    config = tmp_path / 'game.yaml'
    config.write_text(yaml.safe_dump({'options': {'target_path': 'rom.z64'}, 'segments': [
        {'type': 'bin', 'start': 0},
        {'name': 'bank', 'type': 'code', 'start': 0x1000, 'vram': 0x80001000,
         'subsegments': [[0x1000, 'c', 'bank']]},
        {'type': 'bin', 'start': 0x1100}, [len(rom)]]}))
    before = config.read_text()
    bodies = [(0x1040, 0x1060, 0x80001040), (0x1060, 0x1080, 0x80001060)]
    ram = bytearray(0x4000)
    ram[0x2040:0x2080] = rom[0x1040:0x1080]
    monkeypatch.setattr(code, 'complete_gaps', lambda *a, **kw: [])
    monkeypatch.setattr(code, '_bodies_inside_code', lambda *a: bodies)
    monkeypatch.setattr(emulator, 'installed', lambda: tmp_path)

    def observed(*args, inspect_dumps, **kwargs):
        inspect_dumps([bytes(ram), bytes(ram)])
        return {'segments': []}

    monkeypatch.setattr(loaded_code, 'find', observed)
    log = []
    assert missed_functions(config, bytes(rom), log=log.append, known_functions=[(0x1000, 0x1010, 0x80001000)]) == []
    assert config.read_text() == before
    assert any('Deferred code' in line and 'Review the bank' in line for line in log)


def test_high_memory_candidates_are_also_reviewed_with_expansion_memory(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import code, emulator, loaded_code

    rom = bytearray(0x2000)
    rom[0x1040:0x1060] = bytes(range(32))
    rom[0x1060:0x1080] = bytes(range(32, 64))
    (tmp_path / 'rom.z64').write_bytes(rom)
    config = tmp_path / 'game.yaml'
    config.write_text(yaml.safe_dump({'options': {'target_path': 'rom.z64'}, 'segments': [
        {'type': 'bin', 'start': 0},
        {'name': 'bank', 'type': 'code', 'start': 0x1000, 'vram': 0x80701000,
         'subsegments': [[0x1000, 'c', 'bank']]},
        {'type': 'bin', 'start': 0x1100}, [len(rom)]]}))
    before = config.read_text()
    bodies = [(0x1040, 0x1060, 0x80701040), (0x1060, 0x1080, 0x80701060)]
    ram = bytearray(0x4000)
    ram[0x2040:0x2080] = rom[0x1040:0x1080]
    monkeypatch.setattr(code, 'complete_gaps', lambda *a, **kw: [])
    monkeypatch.setattr(code, '_bodies_inside_code', lambda *a: bodies)
    monkeypatch.setattr(emulator, 'installed', lambda: tmp_path)
    runs = []

    def observed(*args, inspect_dumps, expansion_pak=False, **kwargs):
        runs.append(expansion_pak)
        if expansion_pak:
            assert kwargs['vis'] == 1800 and kwargs['dumps_at'][-2:] == (1500, 1790)
        inspect_dumps([bytes(ram), bytes(ram)] if expansion_pak else [bytes(0x4000)] * 2)
        return {'segments': []}

    monkeypatch.setattr(loaded_code, 'find', observed)
    log = []
    assert missed_functions(config, bytes(rom), log=log.append, known_functions=[(0x1000, 0x1010, 0x80701000)]) == []
    assert runs == [False, True]
    assert config.read_text() == before
    assert any('Deferred code' in line for line in log)
