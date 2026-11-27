import hashlib
import json
import struct

import crunch64
import pytest

from decompnet.app import donor_reanalysis
from decompnet.app.engine import RomNetwork
from decompnet.consoles.n64 import uso
from decompnet.network import NetworkError
from decompnet.rom.export_entry import object_for
from decompnet.rom.split import Segment, SplitResult, function_from_bytes

from .test_app_engine import CLAMP
from .test_rom import ROM, built  # noqa: F401


def _module(data, *, compressed=True, relocations=b'', text_base=False):
    packed = crunch64.yay0.compress(data) if compressed else data
    symbols = b''.join(struct.pack('>III', tag, value, 0) for tag, value in (
        (0x00520000, 4), (0x00540000 if text_base else 0x00520000, 0), (0x00520000, 5), (0x00520000, len(data)), (0x00530000, 8)))
    record = lambda kind, body, flags: struct.pack('>III', kind, len(body), flags) + body
    return (struct.pack('>III', 0x12345678, 1, 1) + b'code.uso\0'
            + struct.pack('>IIII', 0x12345678, 0, 0, 0)
            + record(1, symbols, 0x10000000)
            + record(2, relocations, 0x10000000)
            + record(5, packed, 0x10010000 if compressed else 0x10000000)
            + record(11, b'', 0x10000000))


def test_uso_requires_complete_directory_and_keeps_module_relative_bytes():
    code = bytes(range(32))
    cartridge = b'Yay0' + bytes(60) + _module(code)
    region, = uso.read(cartridge)
    assert region.data == code and region.name == 'code.uso' and region.compressed
    assert region.entries == (4,)
    assert uso.read(cartridge[:-2]) == []
    assert uso.read(cartridge[:64] + b'Yay0' + bytes(100)) == []


def test_donor_location_rejects_ambiguous_overlay_bytes():
    code = bytes(range(32))
    mapped = Segment('main', 4, 40, 0x80000000)
    assert donor_reanalysis._location(code, bytes(4) + code + bytes(4), [], [mapped], {})['vram'] == 0x80000000
    regions = [uso.Text('ambiguous.uso', 53, code + code, True), uso.Text('unique.uso', 123, code, True)]
    assert donor_reanalysis._location(code, bytes(40), regions, [], {}) is None
    native = uso.Text('native.uso', 4, code, False, relocations=())
    located = donor_reanalysis._location(code, bytes(4) + code + bytes(4), [native], [mapped], {})
    assert located['native_region'] == '4' and located['native_offset'] == 0
    duplicate = uso.Text('other.uso', 4, code, False, relocations=())
    assert donor_reanalysis._location(code, bytes(4) + code + bytes(4), [native, duplicate], [mapped], {})['native_ambiguous']


def test_donor_reads_a_short_function_from_an_object(tmp_path):
    from .test_fastelf import elf32

    code = struct.pack('>II', 0x03e00008, 0)
    template = elf32(False, [(0, 0, 0, 0, 0, 0)])
    path = tmp_path / 'short.o'
    path.write_bytes(object_for(template, 'short', code))
    assert list(donor_reanalysis._bodies(path, {'short'})) == [('short', code)]


def test_donor_rejects_a_closed_epilogue_without_a_stack_frame():
    from decompnet.consoles import get

    # A closed return and an ADDIU stack pop can still be just another function's tail.
    code = struct.pack('>III', 0x27bd0020, 0x03e00008, 0)
    assert get('n64').donor_candidate_review(code, 0x80001000)
    assert get('n64').donor_candidate_review(bytes(0x10004), 0x80001000)


def test_overlay_switch_requires_its_own_entry_and_valid_loader_table():
    from dataclasses import replace

    from decompnet.consoles import get

    from .test_mips_indirect_audit import fixture

    image, _ = fixture()
    code = bytearray(image[0x1000:0x103c])
    # Use an argument as the index and a module-relative, relocated table.
    for at, word in ((0, 0x2c820002), (8, 0x308500f0), (12, 0x00041080),
                     (16, 0x3c010000), (24, 0x8c220020)):
        code[at:at + 4] = word.to_bytes(4, 'big')
    code = bytes(code)
    rodata = bytes(0x20) + struct.pack('>II', 40, 48)
    region = uso.Text('table.uso', 64, bytes(4) + code, False, entries=(4,),
                      relocations=((20, 5, 1), (28, 6, 1)), rodata=rodata, rodata_bases=(1,),
                      text_bases=(2,), rodata_relocations=((0x20, 2, 2), (0x24, 2, 2)))
    console = get('n64')
    assert console.donor_candidate_review(code, 4)  # the indirect transfer alone is not enough
    assert console.donor_candidate_review(code, 4, region=region, offset=4) is None
    assert console.donor_candidate_review(code, 4, region=replace(region, entries=()), offset=4)
    assert console.donor_candidate_review(code, 4, region=replace(region, entries=(4, 40)), offset=4)
    assert console.donor_candidate_review(code, 4, region=replace(region, relocations=None), offset=4)
    assert console.donor_candidate_review(code, 4, region=replace(region, rodata_relocations=()), offset=4)
    corrupt = replace(region, rodata=bytes(0x20) + struct.pack('>II', 40, 0x1000))
    assert console.donor_candidate_review(code, 4, region=corrupt, offset=4)
    fn = function_from_bytes('switch', code, 0, 4, 'module')
    metadata = console.prepare_donor_function(fn, region, 4)['uso']
    assert metadata['jump_tables'] == [[24, 1, [36, 44]]]
    assert fn.uso == metadata
    native = function_from_bytes('native_switch', code, region.offset + 4, 0x80001000, 'module')
    native_info = console.prepare_donor_function(native, region, 4, native=True)['uso']
    assert native_info['native'] is True
    row = {'rom_offset': native.rom, 'size': native.size, 'meta': json.dumps({'uso': native_info})}
    assert console.overlay_cartridge_placement(row, {}) == native_info


def test_preview_requires_cpu_entry_evidence_before_offering_donor_bytes(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from decompnet.forks.project import ForkProject

    from .test_missed_functions import image, project

    net, pid = project(tmp_path)
    rom = bytearray(image())
    # This closed sequence is an epilogue of earlier code, not a function entry.
    epilogue = struct.pack('>III', 0x8fb3002c, 0x03e00008, 0x27bd0068)
    rom[0x1040:0x1040 + len(epilogue)] = epilogue
    valid = struct.pack('>8I', 0x24820001, 0x24420001, 0x24420002, 0x24420003,
                        0x24420004, 0x24420005, 0x03e00008, 0)
    rom[0x1060:0x1060 + len(valid)] = valid
    (tmp_path / 'rom.z64').write_bytes(rom)
    with net.store.tx() as db:
        cfg = json.loads(db.execute('SELECT config FROM projects WHERE id=?', (pid,)).fetchone()['config'])
        cfg['rom']['sha1'] = hashlib.sha1(rom).hexdigest()
        cfg['segments'] = [vars(Segment('main', 0x1000, 0x1100, 0x80001000))]
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), pid))
        donor = db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                           ('donor', 'donor', str(tmp_path), '{"kind":"fork","platform":"n64"}')).lastrowid
        for name in ('valid', 'leaf', 'leaf_alias', 'epilogue', 'unlocated', 'missing'):
            db.execute("INSERT INTO functions(project_id,name,unit,status) VALUES(?,?,?,'matched')", (donor, name, 'unit'))
        db.execute("INSERT INTO functions(project_id,name,unit,status) VALUES(?,?,?,'matched')", (donor, 'missing_object', 'unavailable'))
    obj = tmp_path / 'donor.o'
    obj.write_bytes(b'placeholder')
    monkeypatch.setattr(ForkProject, 'original_image', lambda *args: tmp_path / 'rom.z64')
    monkeypatch.setattr(ForkProject, 'units', lambda self: [SimpleNamespace(name='unit', target=obj),
                        SimpleNamespace(name='unavailable', target=None), SimpleNamespace(name='unindexed', target=obj)])
    leaf = bytes(rom[0x1010:0x1020])
    monkeypatch.setattr(donor_reanalysis, '_bodies', lambda path, names: iter([
        ('valid', valid), ('leaf', leaf), ('leaf_alias', leaf), ('epilogue', epilogue), ('unlocated', b'\x12' * 32)]))
    locate = donor_reanalysis._location
    short_scans = []
    def count_short(body, *args):
        if body == leaf:short_scans.append(body)
        return locate(body, *args)
    monkeypatch.setattr(donor_reanalysis, '_location', count_short)
    report = donor_reanalysis.preview(net, 'game', 'donor', lambda *_: None)
    assert len(short_scans) == 1  # repeated stubs do not rescan the whole ROM
    assert report['counts'] == {'raw': 1, 'overlay': 0, 'review': 2}
    candidates = {row['name']: row for row in report['functions']}
    assert candidates['valid']['state'] == 'new'
    assert candidates['leaf']['state'] == 'review' and 'short body' in candidates['leaf']['review_reason']
    assert candidates['epilogue']['state'] == 'review'
    assert candidates['epilogue']['review_reason']
    assert report['coverage']['uncompared_functions'] == 2
    assert report['coverage']['unlocated_functions'] == 1
    assert report['coverage']['unavailable_units'] == 1
    assert report['coverage']['units_without_indexed_functions'] == 1
    # A newly indexed but unextractable function changes the review token too.
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name,unit,status) VALUES(?,?,?,'matched')", (donor, 'also_missing', 'unit'))
    updated = donor_reanalysis.preview(net, 'game', 'donor', lambda *_: None)
    assert updated['counts'] == report['counts']
    assert updated['coverage']['uncompared_functions'] == 3
    assert updated['token'] != report['token']
    from decompnet.consoles.n64 import Nintendo64

    review = Nintendo64.donor_candidate_review
    def fail_epilogue(self, body, vram, **kwargs):
        if body == epilogue:raise ValueError('bad evidence')
        return review(self, body, vram, **kwargs)
    monkeypatch.setattr(Nintendo64, 'donor_candidate_review', fail_epilogue)
    failed = donor_reanalysis.preview(net, 'game', 'donor', lambda *_: None)
    assert failed['coverage']['unavailable_units'] == 2
    assert failed['coverage']['uncompared_functions'] == 5


def test_exact_same_rom_preview_is_reviewed_then_adds_missing_raw_function(built, tmp_path):  # noqa: F811
    net = RomNetwork(tmp_path / 'decomp.db', tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent('autopilot')
    net.split_function(agent, slug, 'func_800007C4', '0x800007D0')
    proj = net._project(slug)
    fn = net._function('func_800007D0', slug)
    compiled = net._compile(proj, net._context(proj['id'], fn['unit'])['header'], CLAMP, fn)
    assert compiled.ok
    root = tmp_path / 'donor'
    root.mkdir()
    (root / 'baserom.z64').write_bytes(built)
    (root / 'reference.o').write_bytes(object_for(compiled.obj, 'donor_clamp', fn['target']))
    (root / 'objdiff.json').write_text(json.dumps({'units':[
        {'name':'source', 'target_path':'reference.o', 'metadata':{'source_path':'source.c'}}
    ]}))
    with net.store.tx() as db:
        donor = db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                           ('donor', 'Donor project', str(root), '{"kind":"fork","platform":"n64"}')).lastrowid
        db.execute("INSERT INTO functions(project_id,name,unit,status) VALUES(?,?,?,'matched')",
                   (donor, 'donor_clamp', 'source'))
        db.execute('DELETE FROM functions WHERE id=?', (fn['id'],))
    assert donor_reanalysis.donors(net, slug) == [{'slug':'donor', 'name':'Donor project'}]
    report = donor_reanalysis.preview(net, slug, 'donor')
    assert report['counts'] == {'raw':1, 'overlay':0, 'review':0}
    assert not net.store.one('SELECT 1 FROM functions WHERE project_id=? AND rom_offset=?', proj['id'], fn['rom_offset'])
    with pytest.raises(NetworkError, match='Preview'):
        donor_reanalysis.apply(net, slug, 'donor', 'stale')
    assert donor_reanalysis.apply(net, slug, 'donor', report['token']) == {'added':1}
    row = net.store.one('SELECT * FROM functions WHERE project_id=? AND rom_offset=?', proj['id'], fn['rom_offset'])
    assert row['status'] == 'open' and row['target'] == fn['target']
    assert donor_reanalysis.preview(net, slug, 'donor')['counts']['raw'] == 0


def test_donor_entry_classifies_known_sdk_before_generic_assembly(tmp_path, monkeypatch):
    from decompnet.app.source_assembly import verified
    from decompnet.consoles.n64 import Nintendo64

    from .test_missed_functions import image, project

    net, pid = project(tmp_path)
    with net.store.tx() as db:
        cfg = json.loads(db.execute('SELECT config FROM projects WHERE id=?', (pid,)).fetchone()['config'])
        cfg['segments'].append(vars(Segment('gap', 0x1010, 0x1020, 0x80001010)))
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), pid))
    proj = net.project_row('game')
    code = image()[0x1010:0x1020]
    proof = {'name': 'sdk_leaf', 'names': ['sdk_leaf'], 'corpus': 'test SDK',
             'target_sha256': hashlib.sha256(code).hexdigest()}
    monkeypatch.setattr(Nintendo64, 'sdk_assembly_identity', lambda self, body: proof if body == code else None)
    monkeypatch.setattr(Nintendo64, 'asm_reason', lambda self, body: 'generic assembly')
    report = {'token': 'reviewed', 'functions': [{'region': 'raw', 'position': 0x1010, 'offset': 0x1010,
              'size': len(code), 'vram': 0x80001010, 'name': 'donor_leaf', 'state': 'new'}]}
    monkeypatch.setattr(donor_reanalysis, '_preview', lambda *args: (proj, cfg, image(), [], report))
    assert donor_reanalysis.apply(net, 'game', 'donor', 'reviewed') == {'added': 1}
    row = net.store.one('SELECT * FROM functions WHERE project_id=? AND rom_offset=?', pid, 0x1010)
    meta = json.loads(row['meta'])
    assert row['status'] == 'asm' and meta['verified_assembly']['kind'] == 'sdk'
    assert meta['donor_boundary']['symbol'] == 'donor_leaf'
    assert meta['asm_code'] != 'generic assembly'
    assert verified(net.project_row('game'), row)


def test_donor_overlay_keeps_loader_placement_for_judging_and_export(tmp_path, monkeypatch):
    from decompnet.consoles.n64.uso_match import Target

    from .test_missed_functions import image, project

    net, pid = project(tmp_path)
    proj = net.project_row('game')
    cfg = json.loads(proj['config'])
    code = image()[0x1010:0x1020]
    region = uso.Text('module.uso', 64, code, True, entries=(0,), relocations=())
    report = {'token': 'reviewed', 'functions': [{'region': '64', 'position': None, 'offset': 0,
              'size': len(code), 'vram': 0, 'name': 'donor_leaf', 'state': 'new'}]}
    monkeypatch.setattr(donor_reanalysis, '_preview', lambda *args: (proj, cfg, image(), [region], report))
    assert donor_reanalysis.apply(net, 'game', 'donor', 'reviewed', include_overlays=True) == {'added': 1}
    row = net.store.one("SELECT * FROM functions WHERE project_id=? AND json_extract(meta,'$.donor_boundary') IS NOT NULL", pid)
    target = net._rom_target(row)
    assert isinstance(target, Target)
    assert target.code == code and target.uso == {'section': 64, 'start': 0, 'size': len(code), 'relocs': []}
    assert net.console(net.project_row('game')).overlay_cartridge_placement(row, net.cfg(net.project_row('game'))) == target.uso


def test_donor_overlay_without_loader_records_cannot_be_published(tmp_path, monkeypatch):
    from .test_missed_functions import image, project

    net, pid = project(tmp_path)
    proj = net.project_row('game')
    cfg = json.loads(proj['config'])
    code = image()[0x1010:0x1020]
    region = uso.Text('module.uso', 64, code, True, entries=(0,), relocations=None)
    report = {'token': 'reviewed', 'functions': [{'region': '64', 'position': None, 'offset': 0,
              'size': len(code), 'vram': 0, 'name': 'donor_leaf', 'state': 'new'}]}
    monkeypatch.setattr(donor_reanalysis, '_preview', lambda *args: (proj, cfg, image(), [region], report))
    with pytest.raises(NetworkError, match='relocation records'):
        donor_reanalysis.apply(net, 'game', 'donor', 'reviewed', include_overlays=True)
    assert net.store.one("SELECT COUNT(*) n FROM functions WHERE project_id=? AND json_extract(meta,'$.donor_boundary') IS NOT NULL", pid)['n'] == 0
    assert net.project_row('game')['config'] == proj['config']
    assert not list(tmp_path.glob('donor-analysis-*'))


@pytest.mark.parametrize('compressed', [False, True])
def test_native_uso_import_preserves_cartridge_and_skips_known_raw_body(tmp_path, compressed):
    body = struct.pack('>III', 0x24820001, 0x03e00008, 0x00441021)
    code = bytes(4) + body + bytes(16)
    cartridge = bytes(64) + _module(code, compressed=compressed)
    path = tmp_path / 'rom.z64'
    path.write_bytes(cartridge)
    region, = uso.read(cartridge)
    result = SplitResult(functions=[], segments=[], data=[], named={}, config_path=path, compiler="", log="", image=path.name)
    uso.add_to_split(result, tmp_path, cartridge, lambda *_: None)
    fn, = result.functions
    segment, = result.segments
    assert result.analysis_base == path.name
    assert result.analysis_banks == {str(region.offset): segment.rom_start}
    assert fn.vram == 4 and fn.rom == segment.rom_start + 4 and fn.size == len(body)
    analysis = (tmp_path / result.image).read_bytes()
    assert analysis[:len(cartridge)] == path.read_bytes() == cartridge
    assert analysis[fn.rom:fn.rom + fn.size] == body
    if not compressed:
        known = function_from_bytes('known', body, region.offset + 4, 0x80000000, 'main')
        result = SplitResult(functions=[known], segments=[], data=[], named={}, config_path=path, compiler="", log="", image=path.name)
        uso.add_to_split(result, tmp_path, cartridge, lambda *_: None)
        assert result.functions == [known] and result.image == path.name
