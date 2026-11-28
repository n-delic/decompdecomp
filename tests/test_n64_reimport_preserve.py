"""A reimport must not lose safe existing functions that a new split missed."""

import json
from types import SimpleNamespace

import pytest
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


def test_preserve_does_not_restore_a_proven_data_range_as_code(tmp_path, monkeypatch):
    result, image, row = fixture(tmp_path)
    result.data = [split.DataSymbol('old', row['vram'], row['rom_offset'], row['size'], 'data',
                                    boundary_proven=True)]
    monkeypatch.setattr(runtime_staging, 'verify', lambda *args, **kwargs: True)
    assert reimport_preserve.preserve(None, None, result, image, [row], lambda *_: None) == 0
    assert [f.name for f in result.functions] == ['current']
    # An ordinary data label alone is weaker evidence: keep the old entry.
    result.data[0].boundary_proven = False
    assert reimport_preserve.preserve(None, None, result, image, [row], lambda *_: None) == 1


@pytest.mark.parametrize('case',['valid','outside_call','outside_inner','old_padding','entered_padding','pointer',
                                  'inner_pointer','changed','not_joined','live_tail'])
def test_reimport_keeps_only_a_still_proven_join(case):
    base = 0x80001000
    raw = bytes.fromhex('10000001 00000000 03e00008 00000000')
    image = bytearray(bytes(0x100)+raw+bytes(0x40))
    head = split.function_from_bytes('head',raw[:8],0x100,base,'code')
    tail = split.function_from_bytes('tail',raw[8:],0x108,base+8,'code')
    functions = [head,tail]
    if case in ('outside_call','outside_inner','old_padding','entered_padding'):
        word = 0x0c000000 | (((base+8+(4 if case=='outside_inner' else 0))>>2)&0x3ffffff)
        image[0x120:0x128] = word.to_bytes(4,'big')+bytes(4)
        functions.append(split.function_from_bytes('caller',bytes(image[0x120:0x128]),0x120,base+0x20,'code'))
    if case in ('pointer','inner_pointer'):image[0x140:0x144] = (base+8+(4 if case=='inner_pointer' else 0)).to_bytes(4,'big')
    old = [dict(name='head',rom_offset=0x100,vram=base,size=16,target=raw,
                meta=json.dumps({'boundary_decision':{'action':'join_previous' if case!='not_joined' else 'keep'}})),
           dict(name='tail',rom_offset=0x108,vram=base+8,size=8,target=raw[8:] if case!='changed' else bytes(8),
                gone_mark=None if case=='live_tail' else 1,meta=json.dumps({'boundary_absorbed_by':'head'}))]
    if case in ('old_padding','entered_padding'):
        old.append(dict(name='caller',rom_offset=0x120,vram=base+0x20,size=8,target=bytes(image[0x120:0x128]),
                        gone_mark=1,meta=json.dumps({'boundary_classification':'padding',
                                                     'boundary_decision':{'action':'trim_start'}})))
    if case=='entered_padding':image[0x140:0x144]=(base+0x20).to_bytes(4,'big')
    result = SimpleNamespace(functions=functions,data=[])
    joined = case in ('valid','old_padding')
    assert reimport_preserve._joined_bodies(result,bytes(image),old) == joined
    assert any(fn.name=='tail' for fn in result.functions) != joined
    assert result.retained_join_archives == ({'tail'} if joined else set())


@pytest.mark.parametrize('case', ['partial', 'other_mapping', 'unproven'])
def test_proven_data_overlap_blocks_only_its_own_mapping(tmp_path, monkeypatch, case):
    result, image, row = fixture(tmp_path)
    result.data = [split.DataSymbol('part', row['vram']+4+(0x1000 if case=='other_mapping' else 0),
                                    row['rom_offset']+4, 4, 'jtbl', boundary_proven=case!='unproven')]
    monkeypatch.setattr(runtime_staging, 'verify', lambda *args, **kwargs: True)
    assert reimport_preserve.preserve(None, None, result, image, [row], lambda *_: None) == (case!='partial')


@pytest.mark.parametrize('obstacle', ['none', 'referenced_fragment', 'old_fragment', 'proven_data', 'staging'])
def test_preserve_restores_contiguous_called_code_misclassified_as_data(tmp_path, monkeypatch, obstacle):
    from decompnet.arch import mips_gaps, mips_references

    cfg = {'options':{'target_path':'game.z64'}, 'segments':[
        {'name':'bank','type':'code','start':0x100,'vram':0x80001000,
         'subsegments':[[0x100,'rodata','wrong'],[0x120,'c','tail']]}, [0x180]]}
    path = tmp_path/'game.yaml';path.write_text(yaml.safe_dump(cfg))
    body = bytes.fromhex('27bdfff0 03e00008 27bd0010')
    image = bytearray(0x180);image[0x100:0x124] = body*3
    fragment = split.function_from_bytes('fragment',bytes(image[0x120:0x124]),0x120,0x80001020,'tail')
    caller = split.function_from_bytes('caller',bytes.fromhex('03e00008 00000000'),0x140,0x80001040,'tail')
    result = SimpleNamespace(config_path=path,functions=[fragment,caller],segments=split.segments_from_config(path),data=[])
    rows = [dict(name=f'old{i}',rom_offset=0x100+i*12,vram=0x80001000+i*12,size=12,target=body,unit='bank')
            for i in range(3)]
    targets = [r['vram'] for r in rows]
    if obstacle == 'referenced_fragment':
        targets.append(fragment.vram)
    if obstacle == 'old_fragment':
        rows.append(dict(name='other_old_name',rom_offset=fragment.rom,vram=fragment.vram,size=fragment.size,
                         target=bytes(image[0x120:0x124]),unit='tail'))
    if obstacle == 'proven_data':
        result.data.append(SimpleNamespace(rom=0x108,vram=0x80001008,size=4,boundary_proven=True))
    monkeypatch.setattr(mips_references,'references',lambda *_:[(target,0x140,'call') for target in targets])
    monkeypatch.setattr(mips_gaps,'extent',lambda image,lo,hi,*args,**kwargs:
                        {'direct_flow_closed':True,'step_limit':False,'extent_bytes':hi-lo})
    monkeypatch.setattr(runtime_staging,'verify',lambda *args,**kwargs: obstacle != 'staging')
    count = reimport_preserve.preserve(None,None,result,bytes(image),rows,lambda *_:None)
    if obstacle == 'none':
        assert count == 3
        assert {f.name for f in result.functions} == {'caller','old0','old1','old2'}
        assert yaml.safe_load(path.read_text())['segments'][0]['subsegments'][0][1] == 'c'
    else:
        assert count == 0
        assert {f.name for f in result.functions} == {'caller','fragment'}
        assert yaml.safe_load(path.read_text()) == cfg


@pytest.mark.parametrize('obstacle', ['none', 'no_call', 'other_mapping', 'changed_bytes', 'proven_data',
                                      'competing', 'owner_known', 'staging'])
def test_reimport_keeps_an_independently_called_old_entry_inside_a_new_body(tmp_path, monkeypatch, obstacle):
    result, original, row = fixture(tmp_path)
    image = bytearray(original)
    # An actual direct call from the same loaded bank to the old entry.
    call = 0x0c000000 | ((row['vram'] & 0x0fffffff) >> 2)
    image[0x140:0x148] = call.to_bytes(4, 'big') + bytes(4)
    owner = split.function_from_bytes('new_prefix', bytes(image[0x108:0x118]), 0x108, 0x80001008, 'bank')
    caller = split.function_from_bytes('caller', bytes(image[0x140:0x148]), 0x140,
                                       0x80002040 if obstacle == 'other_mapping' else 0x80001040, 'bank')
    result.functions = [owner, caller]
    result.data = []
    if obstacle == 'no_call':
        image[0x140:0x144] = bytes(4)
    if obstacle == 'changed_bytes':
        row['target'] = bytes(8)
    if obstacle == 'proven_data':
        result.data = [split.DataSymbol('data', row['vram'], row['rom_offset'], row['size'], 'data',
                                        boundary_proven=True)]
    rows = [row]
    if obstacle == 'competing':
        rows.append({**row, 'name': 'another_old'})
    if obstacle == 'owner_known':
        rows.append(dict(name=owner.name, rom_offset=owner.rom, vram=owner.vram, size=owner.size,
                         target=bytes(image[owner.rom:owner.rom + owner.size]), unit='bank'))
    monkeypatch.setattr(runtime_staging, 'verify', lambda *args, **kwargs: obstacle != 'staging')
    count = reimport_preserve.preserve(None, None, result, bytes(image), rows, lambda *_: None)
    if obstacle == 'none':
        assert count == 1
        functions = {fn.name: (fn.rom, fn.size) for fn in result.functions}
        assert functions['new_prefix'] == (0x108, 8)
        assert functions['old'] == (0x110, 8)
    else:
        assert count == 0
        assert {fn.name: fn.size for fn in result.functions} == {'new_prefix': 16, 'caller': 8}


def test_large_proven_join_is_retained_without_large_pointer_patterns():
    base = 0x80001000
    head_bytes = bytes.fromhex('10000001 00000000')
    tail_bytes = bytes(1100) + bytes.fromhex('03e00008 00000000')
    raw = head_bytes + tail_bytes
    image = bytes(0x100) + raw
    head = split.function_from_bytes('head', head_bytes, 0x100, base, 'code')
    tail = split.function_from_bytes('tail', tail_bytes, 0x108, base+8, 'code')
    old = [dict(name='head', rom_offset=0x100, vram=base, size=len(raw), target=raw,
                meta=json.dumps({'boundary_decision': {'action': 'join_previous'}})),
           dict(name='tail', rom_offset=0x108, vram=base+8, size=len(tail_bytes), target=tail_bytes,
                gone_mark='1', meta=json.dumps({'boundary_absorbed_by': 'head'}))]
    result = SimpleNamespace(functions=[head, tail], data=[])
    assert reimport_preserve._joined_bodies(result, image, old) == 1
    assert result.functions[0].size == len(raw)
    assert result.retained_join_archives == {'tail'}


@pytest.mark.parametrize('case', ['compressed', 'decoded_pointer', 'invalid_block', 'raw_pointer'])
def test_literal_pointer_inside_verified_compression_is_not_a_direct_pointer(monkeypatch, case):
    from decompassets import rnc

    low = 0x80001000
    image = bytearray(256)
    image[0x40:0x44] = b'RNC\x01'
    image[0x48:0x4c] = (128).to_bytes(4, 'big')
    location = 0xe0 if case == 'raw_pointer' else 0x80
    image[location:location+4] = (low+4).to_bytes(4, 'big')
    def unpack(*args, **kwargs):
        if case == 'invalid_block':raise rnc.RncError('bad checksum')
        decoded = b'\xff' + (low+4).to_bytes(4, 'big') if case == 'decoded_pointer' else b'no target pointers'
        return decoded, 146
    monkeypatch.setattr(rnc, 'unpack_at', unpack)
    assert reimport_preserve._uncompressed_pointer(bytes(image), low, low+16) == (case != 'compressed')
def test_matched_module_entry_is_preserved_only_with_unchanged_unoccupied_bank():
    import json
    from types import SimpleNamespace

    from decompnet.consoles.n64.reimport_preserve import _matched_module_entries
    from decompnet.rom.split import Segment, function_from_bytes

    target = bytes.fromhex('03e0000800000000')
    image = bytes(0x110) + target + bytes(0x28)
    meta = {'uso': {'section': 0x40, 'start': 0x10, 'size': 8, 'relocs': []}}
    row = {'name': 'saved', 'status': 'matched', 'unit': 'donor_uv_40', 'rom_offset': 0x110,
           'vram': 0x10, 'size': 8, 'target': target, 'meta': json.dumps(meta), 'gone_mark': None}

    def result():
        return SimpleNamespace(functions=[], analysis_banks={'64': 0x100},
                               segments=[Segment('donor_uv_40', 0x100, 0x140, 0)])

    restored = result()
    assert _matched_module_entries(restored, image, [row]) == 1
    assert restored.functions[0].uso == meta['uso']
    assert restored.functions[0].rom == 0x110
    for altered in (row | {'status': 'open'}, row | {'target': bytes(8)}, row | {'gone_mark': 'removed'},
                    row | {'vram': 0x14}, row | {'unit': 'donor_uv_41'}):
        assert _matched_module_entries(result(), image, [altered]) == 0
    occupied = result()
    occupied.functions.append(function_from_bytes('new', target, 0x110, 0x10, 'donor_uv_40'))
    assert _matched_module_entries(occupied, image, [row]) == 0


