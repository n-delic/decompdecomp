"""Native selections use homemade pixels and metadata, never private assets."""
import base64
import copy
import hashlib
import json

import pytest

from decompnet.app import rom_explorer
from decompnet.consoles.n64 import textures
from decompnet.network import NetworkError
from decompnet.rom import asset_formats

from .test_native_textures import bank, indexed_set, literal2
from .test_rom_assets import pixels
from .test_rom_explorer import Log, project  # noqa: F401


def direct_bank(mip=False,frames=2):
    if mip:
        raw=bytearray(textures.MIP_BYTES)
        for n,level in enumerate(textures.MIP_LEVELS):
            for y in range(level['height']):
                for x in range(level['width']):
                    pixel=((x%32)<<11)|((y%32)<<6)|((n%32)<<1)|1
                    at=level['offset']+y*level['stride']
                    for i,value in enumerate(pixel.to_bytes(2,'big')):raw[at+((x*2+i)^(4 if y%2 else 0))]=value
        fmt=bytes([4,1,5,5,255,0,0,0])
    else:raw=bytes([255,0,0,255])*64;fmt=bytes([2,1,3,3,255,0,0,0])
    return indexed_set([fmt,indexed_set([bytes(raw)]*frames),indexed_set([])])


@pytest.mark.parametrize('mip',[False,True])
@pytest.mark.parametrize('packed',[False,True])
def test_direct_color_and_every_mip_level_match_known_pixels(mip,packed):
    raw=direct_bank(mip);payload=literal2(raw) if packed else raw
    image=bytes(39)+payload
    asset=textures.regions(image)[0]
    assert asset['frames']==2 and asset['palettes']==0
    data=raw if packed else image
    for frame in range(2):
        for n,level in enumerate(asset['levels']):
            result=textures.preview(data,asset,dict(frame=frame,mip=n))
            decoded=pixels(base64.b64decode(result['data_url'].split(',')[1]))
            assert len(decoded)==level['width']*level['height']*4
            if mip:
                expected=b''.join(asset_formats._rgba((((x%32)<<11)|((y%32)<<6)|(n<<1)|1).to_bytes(2,'big'),'rgba5551-be',1)
                                  for y in range(level['height']) for x in range(level['width']))
                assert decoded==expected
            else:assert decoded==bytes([255,0,0,255])*64


def test_frame_and_palette_indices_select_exact_independent_payloads():
    frames=[bytes(32),bytes([0x11])*32]
    palettes=[bytes.fromhex('f80107c1')*8,bytes.fromhex('003fffff')*8]
    raw=indexed_set([bytes([1,1,3,3,255,0,0,0]),indexed_set(frames),indexed_set(palettes)])
    asset=textures.regions(raw)[0]
    expected=[bytes([255,0,0,255]),bytes([0,255,0,255]),bytes([0,0,255,255]),bytes([255,255,255,255])]
    for p in range(2):
        for f in range(2):
            result=textures.preview(raw,asset,dict(frame=f,palette=p))
            assert pixels(base64.b64decode(result['data_url'].split(',')[1]))==expected[p*2+f]*64
    for key,value in [('frame',2),('palette',2),('mip',1),('frame',True),('palette',-1)]:
        with pytest.raises(asset_formats.AssetError):textures.preview(raw,asset,{key:value})


def test_mip_extents_dimensions_and_empty_palette_tables_are_checked():
    raw=bytearray(direct_bank(True))
    raw[26]=4
    assert textures.bank_layout(raw) is None
    raw=direct_bank(True)[:-8]
    assert textures.bank_layout(raw) is None
    raw=bytearray(direct_bank(False));raw[-4:]=bytes(4)
    assert textures.bank_layout(raw) is None


def test_api_selections_persist_and_usage_keeps_recorded_references_separate(project):  # noqa: F811
    net,pid,root=project;image=bytes(128)+literal2(bank(1,2))
    (root/'game.rom').write_bytes(image)
    proj=net.project_row('game');cfg=copy.deepcopy(net.cfg(proj));cfg['rom']['sha1']=hashlib.sha1(image).hexdigest()
    cfg['segments'][0]['rom_end']=len(image)
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),pid))
        db.execute('UPDATE functions SET relocs=? WHERE project_id=?',(json.dumps([[4,5,0x80000080]]),pid))
    rom_explorer.run(net,'game',Log());report=rom_explorer.listing(net,'game')
    row=next(r for r in report['regions'] if r['detected_asset'])
    assert row['direct_function_refs']==['reader'] and not row['traced_function_refs']
    request=dict(region=row['id'],image_hash=report['image_hash'],options=dict(format='auto',frame=1,palette=0,mip=0))
    result=rom_explorer.asset_preview(net,'game',request)
    assert result['details']['frame']==1 and result['settings']['frame']==1
    rom_explorer.annotate(net,'game',{**request,'name':'Animated bank','notes':'Homemade fixture','kind':'image','preview_settings':result['settings']})
    assert rom_explorer.detail(net,'game',row['id'],report['image_hash'])['annotation']['preview_settings']['frame']==1
    for selection in [dict(frame=2),dict(palette=1),dict(mip=1)]:
        with pytest.raises(NetworkError):rom_explorer.asset_preview(net,'game',{**request,'options':dict(format='auto',**selection)})


def test_saved_native_selection_reopens_from_a_display_page_inside_the_bank(project):  # noqa: F811
    net,pid,root=project
    raw=indexed_set([bytes([0,1,8,8,255,0,0,0]),indexed_set([bytes(65536)]),indexed_set([bytes(512)])])
    payload=literal2(raw);image=bytes(65504)+payload
    (root/'game.rom').write_bytes(image)
    proj=net.project_row('game');cfg=copy.deepcopy(net.cfg(proj));cfg['rom']['sha1']=hashlib.sha1(image).hexdigest();cfg['segments']=[]
    with net.store.tx() as db:db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),pid))
    rom_explorer.run(net,'game',Log());report=rom_explorer.listing(net,'game')
    row=next(r for r in report['regions'] if r['detected_asset'] and r['start']>65504)
    request=dict(region=row['id'],image_hash=report['image_hash'],options=dict(format='auto'))
    result=rom_explorer.asset_preview(net,'game',request)
    assert result['details']['width']==256 and result['settings']['offset']==65504
    reopened=rom_explorer.asset_preview(net,'game',{**request,'options':result['settings']})
    assert reopened['data_url']==result['data_url']
