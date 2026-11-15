"""Homemade indexed meshes exercise byte planes and the rotating vertex cache."""
import copy
import hashlib
import json
import struct

import pytest

from decompnet.app import rom_explorer
from decompnet.consoles import get
from decompnet.consoles.n64 import models, textures
from decompnet.network import NetworkError
from decompnet.rom import explorer
from decompnet.rom.asset_formats import AssetError

from .test_native_textures import indexed_set, literal2
from .test_rnc import literal_block
from .test_rom_explorer import Log, project  # noqa: F401


def stream(width,values):
    raw=struct.pack('>II',width,len(values))+b''.join(values)
    return raw.ljust((len(raw)+7)//8*8,b'\0')


def mesh(count=3,commands=None):
    points=[(i*7-200,(i*i%13)*30-10,100-(i%7)*29) for i in range(count)]
    rows=[struct.pack('>hhhHhh4B',*v,0,0,0,255,255,255,255) for v in points]
    planes=bytes(rows[i][column] for column in range(16) for i in range(count))
    vertices=(struct.pack('>II',16,count)+planes).ljust((8+len(planes)+7)//8*8,b'\0')
    commands=commands if commands is not None else [0x8000|0|(1<<5)|(2<<10)]
    poly=stream(2,[struct.pack('>H',word) for word in commands])
    return indexed_set([bytes(24),poly,vertices]),points


def container(*sections):
    node=indexed_set([stream(0,[]),stream(4,[bytes(4)]),indexed_set([indexed_set(list(sections))])])
    return indexed_set([bytes(8),indexed_set([node])])


@pytest.mark.parametrize('pack',[lambda x:x,literal_block,literal2])
def test_validated_raw_and_compressed_models_preview_exact_positions(pack):
    section,points=mesh();raw=container(section,section);payload=pack(raw);image=bytes(29)+payload+bytes(13)
    found=get('n64').asset_regions(image)
    assert len(found)==1
    asset=found[0];assert asset['kind']=='3d model' and len(asset['meshes'])==2
    assert asset['vertices']==6 and asset['triangles']==2
    data=textures.decode(payload)[0] if pack(raw)!=raw else image
    result=get('n64').asset_preview(data,asset,dict(mesh=1))
    assert result['vertices']==[list(v) for v in points] and result['edges']==[(0,1),(0,2),(1,2)]
    assert result['media']=='model' and result['settings']['mesh']==1
    report=explorer.analyse(image,[],[],[],assets=found)
    assert any(row['kind']=='3d model' and row['detected_asset'] for row in report['regions'])
    assert not any(row['kind']=='image' for row in report['regions'])


def test_cache_replacement_uses_retained_and_reloaded_halves():
    word=lambda a,b,c:0x8000|a|(b<<5)|(c<<10)
    section,_=mesh(49,[word(0,1,31),word(0,16,31),word(0,16,31)])
    raw=container(section);asset=models.regions(raw)[0];preview=models.preview(raw,asset,{})
    assert preview['edges']==[(0,1),(0,31),(1,31),(16,31),(16,32),(31,32),(31,48),(32,48)]
    assert preview['triangles']==3


@pytest.mark.parametrize('count,commands',[(3,[0x8000|(3<<10)]),(33,[0x8000]),(3,[0]),(3,[0x8000,0x8000]),(0,[])])
def test_invalid_or_incomplete_cache_streams_are_not_models(count,commands):
    section,_=mesh(count,commands);assert not models.regions(container(section))


def test_corrupt_tables_stream_extents_and_crc_are_not_models():
    section,_=mesh();raw=container(section)
    for offset in (4,12,28):
        broken=bytearray(raw)
        broken[offset]=255;assert not models.regions(broken)
    payload=bytearray(literal2(raw));payload[-2]^=1
    assert not models.regions(payload)
    assert not models.regions(raw[:-1])
    assert not models.regions(bytes(24)*300)


def test_indices_limits_and_cancellation(monkeypatch):
    section,_=mesh();raw=container(section);asset=models.regions(raw)[0]
    for value in (-1,1,'garbage',True):
        with pytest.raises(AssetError,match='Mesh'):models.preview(raw,asset,dict(mesh=value))
    monkeypatch.setattr(models,'MAX_SECTIONS',0)
    assert not models.regions(raw)
    def stop():raise RuntimeError('stop')
    with pytest.raises(RuntimeError,match='stop'):models.regions(raw,check=stop)
    monkeypatch.setattr(models,'MAX_CANDIDATES',0);assert not models.regions(raw)


@pytest.mark.parametrize('pack',[lambda x:x,literal2])
def test_guarded_api_preserves_bank_identity_across_pages_and_saved_notes(project,pack):  # noqa: F811
    net,pid,root=project;section,points=mesh(49,[0x8000,0x8000,0x8000]);raw=container(*([section]*12));image=bytes(128)+pack(raw)
    (root/'game.rom').write_bytes(image)
    cfg=copy.deepcopy(net.cfg(net.project_row('game')));cfg['rom']['sha1']=hashlib.sha1(image).hexdigest();cfg['segments'][0]['rom_end']=len(image)
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),pid))
        db.execute('DELETE FROM functions WHERE project_id=?',(pid,))
    rom_explorer.run(net,'game',Log());report=rom_explorer.listing(net,'game')
    rows=[r for r in report['regions'] if (r.get('detected_asset') or {}).get('media')=='model']
    assert rows and all(r['kind']=='3d model' for r in rows)
    row=rows[-1];request=dict(region=row['id'],image_hash=report['image_hash'],options=dict(format='auto',offset=row['start'],mesh=11))
    result=rom_explorer.asset_preview(net,'game',request)
    assert result['vertices']==[list(v) for v in points] and result['settings']['offset']==128
    assert ('decoded' in result)==(pack(raw)!=raw)
    saved=rom_explorer._preview_settings(result['settings']);assert saved['mesh']==11
    again=rom_explorer.asset_preview(net,'game',{**request,'options':saved});assert again['vertices']==result['vertices']
    with pytest.raises(NetworkError,match='Mesh'):rom_explorer.asset_preview(net,'game',{**request,'options':dict(format='auto',mesh=12)})
    (root/'game.rom').write_bytes(image[:-1]+b'!')
    with pytest.raises(NetworkError,match='changed'):rom_explorer.asset_preview(net,'game',request)


def test_stream_widths_counts_and_total_decode_budget_are_checked(monkeypatch):
    section,_=mesh();raw=container(section)
    root=models.blocks(raw,(0,len(raw)),2)
    node=models.blocks(raw,root[1])[0]
    part=models.blocks(raw,models.blocks(raw,node,3)[2])[0]
    material,poly,vertices=models.blocks(raw,models.blocks(raw,part)[0],3)
    for offset,value in [(poly[0],4),(poly[0]+4,8193),(vertices[0],8),(vertices[0]+4,4097)]:
        broken=bytearray(raw);struct.pack_into('>I',broken,offset,value)
        assert not models.regions(broken)
    monkeypatch.setattr(models,'MAX_EXPANDED',len(raw)-1)
    assert not models.regions(literal2(raw))
    monkeypatch.setattr(models,'MAX_BYTES',len(raw)-1)
    assert not models.regions(raw)


def test_imported_code_retains_priority_over_native_layout_clues():
    section,_=mesh();image=container(section);assets=models.regions(image)
    report=explorer.analyse(image,[dict(name='main',rom_start=0,rom_end=len(image),vram=0x80000000)],
                            [dict(name='known_function',vram=0x80000000,rom_offset=0,size=16,relocs=[])],[],assets=assets)
    assert report['regions'][0]['kind']=='code'
