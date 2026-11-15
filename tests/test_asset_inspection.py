"""Synthetic assets exercise discovery without any commercial game data."""
import base64
import struct
import zlib

import pytest

from decompnet.app import rom_explorer
from decompnet.network import NetworkError
from decompnet.rom import asset_inspection as inspect
from decompnet.rom.asset_formats import AssetError

from .test_rom_assets import pixels
from .test_rom_explorer import Log, project  # noqa: F401


def test_texture_gallery_uses_real_pixels_bounded_search_and_explicit_palette():
    image=bytes(range(256))*16
    result=inspect.textures(image,dict(offset=0,width=16,height=16,palette_offset=2048))
    assert 8<=len(result['candidates'])<=16 and result['tried']<=400
    assert {g['settings']['format'] for g in result['candidates']} >= {'rgba5551-be','rgba8888','ci4','ci8','i8'}
    for guess in result['candidates']:
        assert 0<=guess['score']<=100
        data=base64.b64decode(guess['data_url'].split(',')[1]);settings=guess['settings']
        raw=pixels(data)
        assert len(raw)==settings['width']*settings['height']*4
        if settings['format']=='rgba8888':assert raw==image[:len(raw)]
        if settings['format']=='ci4':assert settings['palette_offset']==2048
    assert 'guesses' in result['note']


def test_texture_discovery_handles_blank_truncated_and_odd_indexed_rows():
    with pytest.raises(AssetError,match='32 bytes'):inspect.textures(bytes(31),{})
    blank=inspect.textures(bytes(256),dict(palette_offset=9999))
    assert blank['candidates'] and all(g['score']==0 for g in blank['candidates'])
    assert not any(g['settings']['format'].startswith('ci') for g in blank['candidates'])
    result=inspect.textures(bytes(range(256))*8,dict(width=9,height=7,palette_offset=512))
    assert len(result['candidates'])<=16


@pytest.mark.parametrize('fmt,code,stride',[('s16be','>hhh',16),('s16le','<hhh',8),('f32be','>fff',16),('f32le','<fff',12)])
def test_geometry_vertex_layouts_and_indexed_triangle_edges(fmt,code,stride):
    points=[(1,2,3),(4,5,6),(7,8,9),(10,11,12)]
    data=b''.join(struct.pack(code,*point).ljust(stride,b'\0') for point in points)+b'\x00\x02\x01\x00\x03\x02'
    model=inspect.model(data,dict(offset=0),dict(vertex_format=fmt,stride=stride,count=4,index_format='u8',index_offset=stride*4,index_count=6,topology='triangles'))
    assert model['vertices']==points
    assert model['triangles']==2 and model['edges']==[(0,1),(0,2),(0,3),(1,2),(2,3)]


@pytest.mark.parametrize('fmt,code',[('u16be','>HHH'),('u16le','<HHH'),('u32be','>III'),('u32le','<III')])
def test_geometry_index_byte_orders(fmt,code):
    data=bytes(48)+struct.pack(code,0,1,2)
    model=inspect.model(data,{},dict(count=3,index_format=fmt,index_offset=48,index_count=3,topology='triangles'))
    assert model['edges']==[(0,1),(0,2),(1,2)]


def test_strips_degenerate_indices_lines_points_and_position_offsets():
    data=bytes(64)+bytes([0,1,2,2,3,0])
    model=inspect.model(data,{},dict(count=4,index_format='u8',index_offset=64,index_count=6,topology='strip'))
    assert model['triangles']==2
    points=inspect.model(b'junk'+struct.pack('>hhh',1,2,3),{},dict(count=1,stride=10,position_offset=4))
    assert points['vertices']==[(1,2,3)] and not points['edges']
    assert inspect.model(bytes(64),{},dict(count=4,topology='lines'))['edges']==[(0,1),(2,3)]


@pytest.mark.parametrize('settings,error',[
    (dict(stride=5),'Stride'),(dict(count=4097),'Count'),(dict(vertex_format='f32be',stride=6),'stride'),
    (dict(position_offset=12,stride=16),'stride'),(dict(count=True),'Count'),(dict(count=1.5),'Count'),
    (dict(index_format='u8',index_count=0),'index count'),(dict(topology='triangles',count=4),'divisible'),
    (dict(topology='lines',count=3),'even'),(dict(vertex_format='double'),'16-bit'),(dict(index_format='commands'),'index format'),
])
def test_geometry_rejects_bad_controls(settings,error):
    with pytest.raises(AssetError,match=error):inspect.model(bytes(1024),{},settings)


def test_geometry_rejects_bad_extents_indices_nan_and_excessive_strips():
    with pytest.raises(AssetError,match='truncated'):inspect.model(bytes(8),{},dict(count=2))
    with pytest.raises(AssetError,match='exceeds'):inspect.model(bytes(16)+b'\x02',{},dict(count=1,index_format='u8',index_offset=16,index_count=1))
    with pytest.raises(AssetError,match='invalid'):inspect.model(struct.pack('>fff',float('nan'),0,0),{},dict(vertex_format='f32be',stride=12,count=1))
    with pytest.raises(AssetError,match='8,192'):inspect.model(bytes(65536),{},dict(count=4096,index_format='u8',index_count=10000,index_offset=0,topology='strip'))


def region(start,end,**extra):
    return dict(id=f'{start:x}-{end:x}',start=start,end=end,size=end-start,kind='data',symbols=[],mappings=[],functions=[],**extra)


def test_grouping_joins_pages_by_full_symbol_extent_and_keeps_unknown_gaps():
    rows=[region(0,8),region(8,16),region(16,24),region(24,32)]
    for row in rows[:2]:
        row['symbols']=[dict(name='texture',address=0x80000000,size=16,segment='data')]
        row['mappings']=[dict(segment='data',address=0x80000000+row['start'])]
    report=dict(regions=rows)
    groups=inspect.groups(report)
    assert len(groups)==1 and groups[0]['region_ids']==['0-8','8-10']
    assert groups[0]['basis']=='Imported data symbol extent' and groups[0]['end']==16
    # Adjacent same-kind bytes do not establish a shared asset.
    assert not inspect.groups(dict(regions=rows[2:]))


def test_grouping_prefers_symbols_and_never_merges_code_partial_or_overlapping_spans():
    rows=[region(i,i+8) for i in range(0,40,8)]
    rows[0]['symbols']=[dict(name='table',address=100,size=16,segment='data')];rows[0]['mappings']=[dict(segment='data',address=100)]
    def chain(lo,hi):return dict(source=dict(start=lo,end=hi),role='copy',function='loader')
    report=dict(regions=rows,loader_flow=dict(chains=[chain(8,24),chain(16,32),chain(32,39)]))
    rows[3]['functions']=['code_owner']
    groups=inspect.groups(report)
    assert len(groups)==1 and groups[0]['start']==0
    rows[3]['functions']=[];rows[3]['kind']='unknown'
    assert [g['start'] for g in inspect.groups(report)]==[0,16]


def test_usage_links_loader_helpers_consumers_and_direct_reads_separately():
    row=dict(references=[dict(function='direct',instruction=100,reason='Imported address')])
    chain=dict(function='loader',instruction=200,reason='Named contract',steps=[dict(kind='dma',callee='copy',callee_known=True,instruction=200),dict(kind='pass',callee='render',callee_known=True,instruction=204,reason='Buffer passed'),dict(kind='read',instruction=208),dict(kind='pass',callee='indirect',callee_known=False,instruction=212)])
    users={item['function']:item for item in inspect.usage(row,dict(chains=[chain]))}
    assert users['loader']['roles']==['loader caller','buffer reader']
    assert users['render']['roles']==['buffer consumer'] and users['render']['evidence'][0]['instruction']==204
    assert 'indirect' not in users and users['direct']['roles']==['address reference']


def test_discovery_api_integrity_decoded_geometry_settings_and_no_progress_mutation(project,monkeypatch):  # noqa: F811
    net,pid,root=project
    monkeypatch.setattr(net.progress,'sync',lambda *a,**k:pytest.fail('Inspection must not judge or synchronize'))
    with net.progress.view():
        rom_explorer.run(net,'game',Log());report=rom_explorer.listing(net,'game');row=report['regions'][0]
        packet=dict(region=row['id'],image_hash=report['image_hash'],action='model',options=dict(offset=0),model_settings=dict(count=1))
        result=rom_explorer.asset_inspect(net,'game',packet)
        assert result['vertices']==[(0,0,0)]
        rom_explorer.annotate(net,'game',dict(packet,model_settings=dict(vertex_format='f32le',stride=12,count=2)))
        assert rom_explorer.detail(net,'game',row['id'],report['image_hash'])['annotation']['model_settings']['vertex_format']=='f32le'
        with pytest.raises(NetworkError,match='changed'):rom_explorer.asset_inspect(net,'game',dict(packet,image_hash='bad'))
        with pytest.raises(NetworkError,match='Stride'):rom_explorer.asset_inspect(net,'game',dict(packet,model_settings=dict(stride=1)))
        with pytest.raises(NetworkError,match='Choose texture'):rom_explorer.asset_inspect(net,'game',dict(packet,action='unknown'))


def test_geometry_can_read_a_decompressed_block(project):  # noqa: F811
    net,pid,root=project
    raw=struct.pack('>hhh',1,2,3);packed=zlib.compress(raw);(root/'game.rom').write_bytes(packed+bytes(128))
    # Update the synthetic import fingerprint, then scan its own replacement image.
    import hashlib
    import json
    cfg=json.loads(json.dumps(net.cfg(net.project_row('game'))));cfg['rom']['sha1']=hashlib.sha1((root/'game.rom').read_bytes()).hexdigest()
    with net.store.tx() as db:db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),pid))
    rom_explorer.run(net,'game',Log());report=rom_explorer.listing(net,'game')
    packet=dict(action='model',model_settings=dict(count=1),region=report['regions'][0]['id'],image_hash=report['image_hash'],options=dict(source='decoded',block_offset=0,offset=0))
    assert rom_explorer.asset_inspect(net,'game',packet)['vertices']==[(1,2,3)]
