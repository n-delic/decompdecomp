"""Invented records verify source layout, bounded paging and conservative links."""
import copy
import hashlib
import json
import struct

import pytest

from decompnet.app import rom_explorer
from decompnet.consoles.n64 import models, resources
from decompnet.consoles.n64 import resource_inspection as inspect
from decompnet.network import NetworkError
from decompnet.rom import explorer, resource_catalog
from decompnet.rom.asset_formats import AssetError

from .test_native_models import container, mesh
from .test_native_resources import animation, fixture, stream
from .test_native_textures import indexed_set
from .test_rom_explorer import Log, project  # noqa: F401


def planes(width,rows):
    return stream(width,len(rows),bytes(row[j] for j in range(width) for row in rows))


def collision(neighbour=65535,corner=2):
    attrs=bytearray(72);struct.pack_into('>I',attrs,52,0x1234);attrs[66:68]=b'\x02\x03'
    return indexed_set([planes(72,[attrs]),planes(16,[struct.pack('>4i',*(v*1000 for v in xyz),9000) for xyz in [(0,0,0),(1,0,0),(0,1,0)]]),
                        planes(16,[struct.pack('>8H',0,9,0,1,corner,neighbour,65535,65535)]),stream(20)])


def test_collision_planes_attributes_connectivity_and_bad_indices():
    result=inspect.inspect(collision(),'collision',{})
    row=result['table']['rows'][0]
    assert row['corners']==[0,1,2] and row['neighbours']==[None]*3
    assert row['surface_flags']==0x1234 and row['ground_material']==2 and row['wall_material']==3
    assert result['geometry']['vertices'][1][0]==pytest.approx(29.296875)
    for raw in (collision(neighbour=1),collision(corner=3)):
        with pytest.raises(resources.SchemaError,match='absent'):inspect.inspect(raw,'collision',{})


def placement(object_id=7,x=1):
    return struct.pack('>4Hh2B6f',object_id,4,2,3,-90,1,8,x,2,3,1,1,1)


def test_placements_planes_full_paging_and_nonfinite_coordinates():
    raw=planes(36,[placement(x=i) for i in range(130)])
    result=inspect.inspect(raw,'object placements',dict(record_page=2))
    assert result['table']['total']==130 and [r['position'][0] for r in result['table']['rows']]==[128,129]
    assert result['table']['rows'][0]['object_id']==7
    with pytest.raises(AssetError):inspect.inspect(raw,'object placements',dict(record_page=3))
    with pytest.raises(resources.SchemaError):inspect.inspect(planes(36,[placement(x=float('nan'))]),'object placements',{})


def test_animation_tracks_raw_frames_and_missing_orientation():
    raw=animation();parts=resources.blocks(raw,(0,len(raw)),7)
    changed=indexed_set([raw[a:b] if i not in (1,3) else stream(4,1,struct.pack('>hh',0,-1)) if i==1 else stream(44,1,struct.pack('>I10f',0,0,0,0,1,1,2,3,4,5,6)) for i,(a,b) in enumerate(parts)])
    result=inspect.inspect(changed,'animation',dict(frame=1))['table']['rows'][0]
    assert result['frame_position']==[4,5,6] and result['translation_encoding']=='float XYZ'
    assert result['initial_rotation']==[0]*4
    with pytest.raises(AssetError):inspect.inspect(changed,'animation',dict(frame=2))


def test_hierarchy_rejects_cycles_and_material_flags_are_inspectable():
    section,_=mesh();raw=container(section);result=models.preview(raw,models.regions(raw)[0],{})
    assert result['table']['rows'][0]['parent'] is None
    assert result['material']['texture_id']==0
    node=indexed_set([stream(4,1,struct.pack('>I',0)),stream(4),indexed_set([])])
    with pytest.raises(resources.SchemaError,match='hierarchy'):inspect.hierarchy(indexed_set([bytes(8),indexed_set([node])]))
    nodes=[indexed_set([stream(4,1,struct.pack('>I',j)),stream(4),indexed_set([])]) for j in (1,0)]
    with pytest.raises(resources.SchemaError,match='cycle'):inspect.hierarchy(indexed_set([bytes(8),indexed_set(nodes)]))


def psf():
    return struct.pack('<8I',0x864ab572,0,32,0,130,1,1,8)+bytes(range(130))


def test_portable_font_extents_unicode_flags_and_glyph_pages():
    image=bytes(9)+psf();asset=resource_catalog.regions(image)[0]
    assert asset['offset']==9 and asset['kind']=='font'
    result=resource_catalog.preview(image,asset,dict(record_page=2))
    assert result['table']['total']==130 and len(result['font_atlas']['glyphs'])==2
    assert result['font_atlas']['glyphs'][0]['pixels']==['10000000']
    assert not resource_catalog.regions(image[:-1])
    bad=bytearray(psf());struct.pack_into('<I',bad,12,1)
    assert not resource_catalog.regions(bad)
    assert not resource_catalog.regions(b'\x36\x04\x00\x10'+bytes(4096))  # weak two-byte magic is not an automatic detection


@pytest.mark.parametrize('big_endian',[True,False])
def test_portable_pointer_overlay_alternatives_and_text_confidence(big_endian):
    data=(0x80000004).to_bytes(4,'big' if big_endian else 'little')*70
    segments=[dict(rom_start=0,rom_end=len(data),vram=0x80000000),dict(rom_start=8,rom_end=len(data),vram=0x80000000)]
    asset=dict(offset=0,size=len(data),kind='pointer table',provider='portable-records')
    result=resource_catalog.preview(data,asset,dict(record_page=1),segments,big_endian)
    assert len(result['table']['rows'])==6 and result['table']['rows'][0]['destinations']==[4,12]
    data='Hello world\0café\0'.encode();report=explorer.analyse(data,[],[],[]);resource_catalog.attach(report)
    row=next(r for r in report['regions'] if r['kind']=='strings')
    assert row['confidence']=='tentative'
    result=resource_catalog.preview(data,row['detected_asset'],{})
    assert [r['text'] for r in result['table']['rows']]==['Hello world','café']


def test_guarded_linked_inspectors_across_rom_adapters(project):  # noqa: F811
    net,pid,root=project;image=bytes(128)+fixture()+psf();(root/'game.rom').write_bytes(image)
    cfg=copy.deepcopy(net.cfg(net.project_row('game')));cfg['rom']['sha1']=hashlib.sha1(image).hexdigest();cfg['segments'][0]['rom_end']=len(image)
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),pid));db.execute('DELETE FROM functions WHERE project_id=?',(pid,))
    rom_explorer.run(net,'game',Log());report=rom_explorer.listing(net,'game')
    row=next(r for r in report['regions'] if (r.get('detected_asset') or {}).get('resource_type')=='object')
    request=dict(region=row['id'],image_hash=report['image_hash'],options=dict(offset=row['start']))
    result=rom_explorer.asset_preview(net,'game',request)
    assert result['fields']['object_id']==0 and result['fields']['type_flag']==7
    assert all(r['links'] for r in result['table']['rows'])
    font_row=next(r for r in report['regions'] if r['kind']=='font')
    request.update(region=font_row['id'],options=dict(offset=font_row['start'],record_page=1))
    assert rom_explorer.asset_preview(net,'game',request)['table']['rows'][0]['index']==64
    request['options']['record_page']=100
    with pytest.raises(NetworkError):rom_explorer.asset_preview(net,'game',request)
    assert (root/'game.rom').read_bytes()==image


def test_repeated_type_flags_do_not_replace_object_indices():
    raw=fixture();parts=resources.blocks(raw,(0,len(raw)),11);obj=resources.blocks(raw,parts[0])[0]
    replaced=indexed_set([raw[a:b] if i not in (0,2) else indexed_set([raw[obj[0]:obj[1]]]*2) if i==0 else stream(2,2,b'\xff\xff'*2) for i,(a,b) in enumerate(parts)])
    found=resources.regions(replaced);objects=[a for a in found if a['resource_type']=='object']
    assert [(a['resource_fields']['object_id'],a['resource_fields']['type_flag']) for a in objects]==[(0,65535),(1,65535)]


def test_object_bounds_and_animation_type_records():
    raw=indexed_set([struct.pack('>6f',-1,-2,-3,1,2,3),stream(4,2,struct.pack('>II',10,20))])
    result=inspect.inspect(raw,'object metadata',{})
    assert result['object_bounds']['minimum']==[-1,-2,-3]
    assert [r['animation_type'] for r in result['table']['rows']]==[10,20]


@pytest.mark.parametrize('component',[0,1,2])
def test_grid_placement_variants(component):
    extended=struct.pack('>f4Hh10B',2,7,3,4,5,-8,16,0,20,30,0,0,0,0,0,0)
    static=struct.pack('>6f',1,2,3,2,2,2)+bytes(12)+struct.pack('>3H6B',7,3,4,5,0,0,0,0,0)
    simple=struct.pack('>4f5HbB',1,2,3,9,7,3,4,5,6,-2,0)
    raw=indexed_set([planes(w,[r]) for w,r in zip((24,48,28),(extended,static,simple))])
    result=inspect.inspect(raw,'grid section',dict(record_component=component))['table']['rows'][0]
    assert result['object_id']==7 and result['region']==3 and result['variation']==4
    if component==0:assert result['quantized_y']==-8 and 'position' not in result
    else:assert result['position']==[1,2,3]


def test_empty_bounds_placeholder_does_not_invent_coordinates():
    result=inspect.inspect(indexed_set([bytes(8),stream(4)]),'object metadata',{})
    assert 'object_bounds' not in result and 'inspection_notice' in result
    assert result['table']['total']==0


@pytest.mark.parametrize('platform',['n64','ps1','ps2','psp'])
def test_portable_font_inspection_reaches_each_rom_adapter(project,platform):  # noqa: F811
    net,pid,root=project;image=psf();(root/'game.rom').write_bytes(image)
    cfg=copy.deepcopy(net.cfg(net.project_row('game')));cfg['platform']=platform;cfg['rom']['sha1']=hashlib.sha1(image).hexdigest();cfg['segments'][0]['rom_end']=len(image)
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),pid));db.execute('DELETE FROM functions WHERE project_id=?',(pid,))
    rom_explorer.run(net,'game',Log());report=rom_explorer.listing(net,'game')
    row=next(r for r in report['regions'] if r['kind']=='font')
    result=rom_explorer.asset_preview(net,'game',dict(region=row['id'],image_hash=report['image_hash'],options=dict(offset=row['start'])))
    assert result['font_atlas']['glyphs'][0]['index']==0
