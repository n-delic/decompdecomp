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


def _module(data, *, compressed=True, relocations=b''):
    packed = crunch64.yay0.compress(data) if compressed else data
    symbols = b''.join(struct.pack('>III', tag, value, 0) for tag, value in (
        (0x00520000, 4), (0x00520000, 0), (0x00520000, 5), (0x00520000, len(data)), (0x00530000, 8)))
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
