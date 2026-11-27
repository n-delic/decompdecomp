"""A reimport must not lose safe existing functions that a new split missed."""

from types import SimpleNamespace

import yaml

from decompnet.consoles.n64 import reimport_preserve, runtime_staging
from decompnet.rom import split


def fixture(tmp_path):
    cfg = {'options': {'target_path':'game.z64'}, 'segments': [
        {'name':'bank', 'type':'code', 'start':0x100, 'vram':0x80001000,
         'subsegments':[[0x100,'c','bank']]}, [0x180]]}
    path = tmp_path/'game.yaml'
    path.write_text(yaml.safe_dump(cfg))
    image = bytes.fromhex('24020001 03e00008 00000000') * 32
    image = bytes(0x100) + image[:0x80]
    first = split.function_from_bytes('current', image[0x100:0x108], 0x100, 0x80001000, 'bank')
    result = SimpleNamespace(config_path=path, functions=[first], segments=split.segments_from_config(path))
    row = {'name':'old', 'rom_offset':0x110, 'vram':0x80001010, 'size':8,
           'target':image[0x110:0x118], 'unit':'bank'}
    return result, image, row


def test_preserve_keeps_only_disjoint_original_bytes_after_staging(tmp_path, monkeypatch):
    result, image, row = fixture(tmp_path)
    captured = []
    def verify(config, data, proposed, candidates, log, **kwargs):
        captured.extend(candidates)
        return True
    monkeypatch.setattr(runtime_staging, 'verify', verify)
    overlapping = {**row, 'name':'overlap', 'rom_offset':0x104, 'vram':0x80001004,
                   'target':image[0x104:0x10c]}
    changed = {**row, 'name':'changed', 'rom_offset':0x120, 'vram':0x80001020, 'target':b'bad bytes'}
    assert reimport_preserve.preserve(None, None, result, image, [row, overlapping, changed], lambda *_:None) == 1
    assert captured == [dict(rom_start=0x110, rom_end=0x118, vram=0x80001010)]
    assert [(f.name, f.rom, f.size) for f in result.functions] == [('current',0x100,8), ('old',0x110,8)]
    assert [s[0] for s in yaml.safe_load(result.config_path.read_text())['segments'][0]['subsegments']] == [0x100,0x110,0x118]


def test_preserve_leaves_split_alone_for_competing_or_unverified_entries(tmp_path, monkeypatch):
    result, image, row = fixture(tmp_path)
    before = result.config_path.read_text()
    assert reimport_preserve.preserve(None, None, result, image, [row, {**row,'name':'other'}], lambda *_:None) == 0
    monkeypatch.setattr(runtime_staging, 'verify', lambda *args, **kwargs:False)
    assert reimport_preserve.preserve(None, None, result, image, [row], lambda *_:None) == 0
    assert result.config_path.read_text() == before
    assert [f.name for f in result.functions] == ['current']
