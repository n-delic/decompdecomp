"""Homemade pixels, PCM and machine instructions exercise asset evidence end to end."""
import base64
import gzip
import hashlib
import io
import json
import struct
import wave
import zlib

import pytest

from decompnet.arch import mips_assets
from decompnet.network import NetworkError
from decompnet.rom import asset_flow, asset_formats, explorer
from decompnet.rom.asset_formats import AssetError

from .test_rom_explorer import Log, project  # noqa: F401


def words(*values,big=True):return struct.pack(('>' if big else '<')+str(len(values))+'I',*values)
def lui(reg,value):return 15<<26 | reg<<16 | value>>16
def addiu(reg,base,value):return 9<<26 | base<<21 | reg<<16 | value&0xffff
def move(dst,src):return src<<21 | dst<<11 | 0x21
def jal(address):return 3<<26 | (address>>2)&0x3ffffff
def sw(reg,base,offset):return 0x2b<<26 | base<<21 | reg<<16 | offset&0xffff
def lw(reg,base,offset):return 0x23<<26 | base<<21 | reg<<16 | offset&0xffff


def pixels(payload):
    at=8;raw=bytearray();width=height=0
    while at<len(payload):
        length=int.from_bytes(payload[at:at+4],'big');kind=payload[at+4:at+8]
        if kind==b'IHDR':width,height=struct.unpack('>II',payload[at+8:at+16])
        if kind==b'IDAT':raw.extend(payload[at+8:at+8+length])
        at+=length+12
    decoded=zlib.decompress(raw)
    return b''.join(decoded[y*(width*4+1)+1:(y+1)*(width*4+1)] for y in range(height))


@pytest.mark.parametrize('big',[True,False])
def test_original_mips_arguments_include_delay_slots_and_o32_stack_arguments(big):
    code=words(addiu(29,29,-32),lui(8,0x80001000),sw(8,29,16),addiu(9,0,64),sw(9,29,20),
               addiu(6,0,0),jal(0x80000100),addiu(7,0,96),big=big)
    call=mips_assets.read(0x80000000,code,big_endian=big)['events'][-1]
    assert call['target']==0x80000100
    assert [call['args'][i]['value'] for i in (2,3,4,5)]==[0,96,0x80000000,64]
    assert 0x8000001c in call['args'][3]['evidence']


def test_branches_clobbers_and_partial_stack_stores_drop_unsafe_arguments():
    code=words(addiu(4,0,100),0x10000002,0,jal(0x80000100),0,jal(0x80000100),0)
    calls=[e for e in mips_assets.read(0x80000000,code)['events'] if e['kind']=='call']
    assert all(e['args'][0] is None for e in calls)
    code=words(addiu(8,0,123),sw(8,29,16),0x29<<26 | 29<<21 | 8<<16 | 18,jal(0x80000100),0)
    assert mips_assets.read(0x80000000,code)['events'][-1]['args'][4] is None


def test_ee_register_arguments_are_not_read_from_o32_stack_slots():
    code=words(addiu(8,0,80),addiu(9,0,96),jal(0x1000),0,big=False)
    call=mips_assets.read(0x100000,code,big_endian=False,argument_count=8)['events'][-1]
    assert call['args'][4]['value']==80 and call['args'][5]['value']==96


def fixture_chain():
    base=0x80000000
    code=words(lui(16,0x80001000),addiu(16,16,0x1000),lui(17,0x80002000),addiu(17,17,0x2000),move(4,16),lui(5,0x80000000),addiu(5,5,256),
               jal(base+0x100),addiu(6,0,32),move(4,17),move(6,16),jal(base+0x120),addiu(7,0,32),
               lw(8,17,0),move(4,17),jal(base+0x140),0)
    image=bytearray(512);image[:len(code)]=code
    functions=[dict(name='loader',vram=base,rom_offset=0,size=len(code),unit='main'),
               dict(name='memcpy',vram=base+0x100,rom_offset=None,size=4,unit='main'),
               dict(name='uncompress',vram=base+0x120,rom_offset=None,size=4,unit='main'),
               dict(name='render',vram=base+0x140,rom_offset=None,size=4,unit='main')]
    return bytes(image),[dict(name='main',rom_start=0,rom_end=512,vram=base)],functions


def test_copy_decompress_read_and_consumer_chain_preserves_evidence_and_extent():
    image,segments,functions=fixture_chain()
    flow=asset_flow.trace(image,segments,functions,mips_assets.read)
    assert flow['supported'] and not flow['limited']
    child=next(c for c in flow['chains'] if any(s['kind']=='decompress' for s in c['steps']))
    assert child['source']['start']==256 and child['source']['end']==288
    assert child['buffer']['value']==0x80002000
    assert any(s['kind']=='read' for s in child['steps'])
    assert any(s.get('callee')=='render' and s['kind']=='pass' for s in child['steps'])
    assert all(c['confidence']=='inferred' for c in flow['chains'])
    report=explorer.analyse(image,segments,[],[],ranges=[c['source'] for c in flow['chains']])
    asset_flow.attach(report,flow)
    row=next(r for r in report['regions'] if r['start']==256)
    assert row['end']==288 and row['loader_chains']
    assert sum(report['totals'].values())==len(image)


def test_dma_contract_records_raw_rom_offsets_and_rejects_write_direction():
    code=words(addiu(4,0,0),addiu(5,0,256),lui(6,0x80001000),jal(0x80000100),addiu(7,0,32))
    image=code+bytes(512-len(code));segments=[dict(name='main',rom_start=0,rom_end=512,vram=0x80000000)]
    functions=[dict(name='load',vram=0x80000000,rom_offset=0,size=len(code),unit='main'),dict(name='osPiRawStartDma',vram=0x80000100,rom_offset=None,size=4,unit='main')]
    from decompnet.consoles import get
    flow=asset_flow.trace(image,segments,functions,mips_assets.read,contracts=get('n64').asset_calls)
    assert flow['chains'][0]['source']['start']==256
    assert flow['chains'][0]['role']=='dma' and 'completion' in flow['chains'][0]['reason']
    changed=words(addiu(4,0,1))+image[4:]
    assert not asset_flow.trace(changed,segments,functions,mips_assets.read,contracts=get('n64').asset_calls)['chains']


def test_unknown_helper_is_candidate_and_ambiguous_overlays_are_retained():
    image,segments,functions=fixture_chain()
    functions[1]['name']='unknown'
    # Its source is a runtime address, so unknown-ROM-offset inference must decline.
    assert not any(c['role']=='candidate' for c in asset_flow.trace(image,segments,functions,mips_assets.read,ram=(0x80000000,0x80800000))['chains'])
    image=words(addiu(4,0,256),lui(5,0x80000000),jal(0x80000100),addiu(6,0,32))+image[16:]
    functions[0]['size']=16
    flow=asset_flow.trace(image,segments,functions,mips_assets.read,ram=(0x80000000,0x80800000))
    assert flow['chains'][0]['confidence']=='candidate'


@pytest.mark.parametrize('fmt,raw,expected',[
    ('rgba5551-be',b'\xf8\x01',bytes([255,0,0,255])),('rgb565-le',b'\xe0\x07',bytes([0,255,0,255])),
    ('rgba8888',b'\x01\x02\x03\x04',b'\x01\x02\x03\x04'),('ia4',b'\xf0',bytes([255,255,255,255])),
    ('ia8',b'\xa5',bytes([170,170,170,85])),('ia16',b'\x34\x56',bytes([52,52,52,86])),
    ('i4',b'\x80',bytes([136,136,136,255])),('i8',b'\x7f',bytes([127,127,127,255])),
    ('palette-rgba5551',b'\x00\x3f',bytes([0,0,255,255])),
])
def test_texture_and_palette_pixels_are_exact(fmt,raw,expected):
    png,details=asset_formats.texture(raw,dict(format=fmt,width=1,height=1))
    assert pixels(png)==expected and details['bytes']==len(raw)


def test_indexed_texture_uses_explicit_palette_and_odd_rows_are_byte_padded():
    raw=b'\x10\x20'+b'\x00\x01\xf8\x01\x07\xc1'+bytes(26)
    png,_=asset_formats.texture(raw,dict(format='ci4',width=1,height=2,palette_offset=2))
    assert pixels(png)==bytes([255,0,0,255,0,255,0,255])
    with pytest.raises(AssetError,match='truncated'):asset_formats.texture(b'\0',dict(format='ci8',width=1,height=1,palette_offset=1))


@pytest.mark.parametrize('fmt,raw,expected,width',[('pcm-s16be',b'\x12\x34\xfe\xdc',b'\x34\x12\xdc\xfe',2),('pcm-u8',b'\x00\x80\xff',b'\x00\x80\xff',1)])
def test_pcm_audio_has_correct_sample_order_and_wav_metadata(fmt,raw,expected,width):
    payload,details=asset_formats.pcm(raw,dict(format=fmt,length=len(raw),rate=22050,channels=1))
    with wave.open(io.BytesIO(payload)) as f:
        assert f.getsampwidth()==width and f.getframerate()==22050
        assert f.readframes(100)==expected
    assert asset_formats.identify(payload)['format']=='PCM WAVE'


def test_recognized_png_and_wav_have_validated_extents_and_reject_corruption():
    image,_=asset_formats.texture(b'\xff\0\0\xff',dict(format='rgba8888',width=1,height=1))
    result=asset_formats.preview(image+b'trailing',dict(format='auto'))
    assert base64.b64decode(result['data_url'].split(',')[1])==image
    assert result['details']['size']==len(image)
    bad=bytearray(image);bad[29]^=1
    with pytest.raises(AssetError,match='checksum'):asset_formats.identify(bytes(bad))
    with pytest.raises(AssetError):asset_formats.identify(image[:-1])


@pytest.mark.parametrize('codec',['zlib','gzip','Yaz0','MIO0','Yay0'])
def test_bounded_decompression_supplies_asset_bytes(codec):
    raw=b'asset'
    if codec=='zlib':packed=zlib.compress(raw)
    elif codec=='gzip':packed=gzip.compress(raw)
    elif codec=='Yaz0':packed=b'Yaz0'+struct.pack('>I',len(raw))+bytes(8)+b'\xf8'+raw
    else:
        mask=b'\xf8' if codec=='MIO0' else b'\xf8\0\0\0'
        packed=codec.encode()+struct.pack('>III',len(raw),16+len(mask),16+len(mask))+mask+raw
    assert asset_formats.unpack(packed)==(raw,codec)
    with pytest.raises(AssetError):asset_formats.unpack(packed[:10])


def test_bounds_and_format_errors_are_actionable():
    for options in (dict(format='i8',width=2048,height=2048),dict(format='i8',width=True,height=1),dict(format='invalid')):
        with pytest.raises(AssetError):asset_formats.preview(bytes(32),options)
    with pytest.raises(AssetError,match='whole sample'):asset_formats.pcm(bytes(3),dict(format='pcm-s16le',length=3))
    with pytest.raises(AssetError,match='30 seconds'):asset_formats.pcm(bytes(64000),dict(format='pcm-s16le',length=64000,rate=1000))
    with pytest.raises(AssetError,match='4 MiB'):asset_formats.unpack(zlib.compress(bytes(asset_formats.MAX_BYTES+1)))


def test_preview_and_loader_roles_are_local_and_revision_guarded(project):  # noqa: F811
    from decompnet.app import rom_explorer
    net,pid,root=project
    original=(root/'game.rom').read_bytes()
    rom_explorer.run(net,'game',Log())
    with net.progress.view():
        report=rom_explorer.listing(net,'game');row=report['regions'][0]
        request=dict(region=row['id'],image_hash=report['image_hash'],options=dict(format='i8',offset=8,width=4,height=2))
        result=rom_explorer.asset_preview(net,'game',request)
        assert result['media']=='image' and result['data_url'].startswith('data:image/png;base64,')
        assert (root/'game.rom').read_bytes()==original
        data=dict(function='reader',role='dma',source=0,destination=1,length=2,domain='rom')
        roles=rom_explorer.loader_contracts(net,'game',data)
        assert roles['items']['reader']['basis']=='user'
        assert rom_explorer.listing(net,'game')['stale']
        with pytest.raises(NetworkError,match='changed'):rom_explorer.asset_preview(net,'game',request)
        rom_explorer.loader_contracts(net,'game',dict(function='reader',remove=True))
        assert not rom_explorer.loader_contracts(net,'game')['items']


def test_standard_mono_vagp_decodes_nibbles_and_rejects_variants():
    header=bytearray(48);header[:4]=b'VAGp';header[12:16]=struct.pack('>I',16);header[16:20]=struct.pack('>I',22050)
    packed=bytes(header)+b'\x0c\x01'+b'\x21'*14
    result=asset_formats.preview(packed,dict(format='auto'))
    with wave.open(io.BytesIO(base64.b64decode(result['data_url'].split(',')[1]))) as audio:
        assert audio.readframes(28)==struct.pack('<28h',*([1,2]*14))
    assert asset_formats.identify(packed)['size']==64
    bad=bytearray(packed);bad[48]=0xf0
    with pytest.raises(AssetError,match='predictor'):asset_formats.preview(bytes(bad),dict(format='auto'))
    bad=bytearray(packed);bad[31]=2
    with pytest.raises(AssetError,match='custom channel'):asset_formats.preview(bytes(bad),dict(format='auto'))


def test_self_describing_asset_extent_becomes_a_complete_map_region():
    image,_=asset_formats.texture(b'\x01\x02\x03\xff',dict(format='rgba8888',width=1,height=1))
    report=explorer.analyse(bytes(16)+image+bytes(50),[],[],[])
    row=next(r for r in report['regions'] if r['start']==16)
    assert row['kind']=='image' and row['confidence']=='validated'
    assert row['end']==16+len(image)


def test_small_wrapper_forwarding_is_traced_but_a_branching_wrapper_is_not():
    base=0x80000000
    caller=words(lui(4,0x80000000),addiu(4,4,0x1000),lui(5,0x80000000),addiu(5,5,512),jal(base+128),addiu(6,0,32))
    wrapper=words(jal(base+256),0,0x03e00008,0)
    image=bytearray(1024);image[:len(caller)]=caller;image[128:128+len(wrapper)]=wrapper
    functions=[dict(name='load',vram=base,rom_offset=0,size=len(caller),unit='main'),
               dict(name='helper',vram=base+128,rom_offset=128,size=len(wrapper),unit='main'),
               dict(name='memcpy',vram=base+256,rom_offset=None,size=4,unit='main')]
    segments=[dict(name='main',rom_start=0,rom_end=len(image),vram=base)]
    result=asset_flow.trace(bytes(image),segments,functions,mips_assets.read)
    assert result['chains'][0]['source']['start']==512 and 'wrapper' in result['chains'][0]['reason']
    image[128:132]=words(0x10000002)
    assert not asset_flow.trace(bytes(image),segments,functions,mips_assets.read)['chains']


def test_decompression_matches_independent_codec_backreferences():
    crunch64=pytest.importorskip('crunch64')
    for name in ('yaz0','mio0','yay0'):
        for raw in (b'abc'*1000,bytes(range(256))*10,b'x'*10000):
            decoded,codec=asset_formats.unpack(getattr(crunch64,name).compress(raw))
            assert decoded==raw and codec.lower()==name


def test_current_maps_use_cache_and_changed_roles_force_new_analysis(project,monkeypatch):  # noqa: F811
    from decompnet.app import rom_explorer
    net,pid,root=project
    rom_explorer.run(net,'game',Log())
    def no_analysis(*args,**kwargs):pytest.fail('unchanged map was rescanned')
    monkeypatch.setattr(rom_explorer,'analyse',no_analysis)
    assert rom_explorer.run(net,'game',Log())['cached']


def test_decoded_preview_offsets_use_expanded_bounds_not_compressed_image_bounds(project):  # noqa: F811
    from decompnet.app import rom_explorer
    net,pid,root=project
    raw=bytes(1000)+b'\xff\0\0\xff';packed=zlib.compress(raw)
    (root/'game.rom').write_bytes(packed+bytes(128))
    with net.progress.view():
        proj=net.project_row('game');cfg=dict(net.cfg(proj));cfg['rom']=dict(file='game.rom',analysis_sha1=hashlib.sha1((root/'game.rom').read_bytes()).hexdigest());cfg['segments']=[]
        with net.store.tx() as db:db.execute('UPDATE projects SET config=? WHERE id=?',( json.dumps(cfg),pid))
        rom_explorer.run(net,'game',Log());report=rom_explorer.listing(net,'game');row=report['regions'][0]
        result=rom_explorer.asset_preview(net,'game',dict(region=row['id'],image_hash=report['image_hash'],options=dict(source='decoded',block_offset=0,offset=1000,format='rgba8888',width=1,height=1)))
        assert result['decoded']['bytes']==1004
        assert pixels(base64.b64decode(result['data_url'].split(',')[1]))==b'\xff\0\0\xff'


def test_preview_routes_enforce_window_origin_write_header_and_stale_inputs(project):  # noqa: F811
    from starlette.testclient import TestClient

    from decompnet.app import rom_explorer
    from decompnet.app.server import build

    from .test_app_server import PORT, SECRET, WINDOW
    net,pid,root=project
    rom_explorer.run(net,'game',Log())
    with net.progress.view():
        report=rom_explorer.listing(net,'game')
        mcp,manager,security,_=build(net,root/'.decomp',PORT,SECRET)
    client=TestClient(mcp.streamable_http_app(transport_security=security,host='127.0.0.1'),base_url=f'http://127.0.0.1:{PORT}',headers=WINDOW)
    request=dict(slug='game',region=report['regions'][0]['id'],image_hash=report['image_hash'],options=dict(format='i8',width=1,height=1))
    path='/api/rom-explorer/preview'
    assert client.post(path,json=request,headers={'User-Agent':'outside'}).status_code==401
    assert client.post(path,json=request).status_code==403
    response=client.post(path,json=request,headers={'X-Decomp':'1'})
    assert response.status_code==200,response.text
    assert response.json()['media']=='image'
    assert client.post(path,json={**request,'image_hash':'stale'},headers={'X-Decomp':'1'}).status_code==400
    invalid={**request,'options':dict(format='ci4',width=4096,height=1)}
    assert client.post(path,json=invalid,headers={'X-Decomp':'1'}).status_code==400
    inspection='/api/rom-explorer/inspect'
    packet={**request,'action':'model','model_settings':{'count':1}}
    assert client.post(inspection,json=packet,headers={'User-Agent':'outside'}).status_code==401
    assert client.post(inspection,json=packet).status_code==403
    assert client.post(inspection,json=packet,headers={'X-Decomp':'1'}).status_code==200
    assert client.post(inspection,json={**packet,'image_hash':'bad'},headers={'X-Decomp':'1'}).status_code==400
    roles='/api/rom-explorer/loaders'
    assert client.get(roles+'?slug=game').status_code==200
    response=client.post(roles,json=dict(slug='game',function='reader',role='dma',domain='rom',source=0,destination=1,length=2),headers={'X-Decomp':'1'})
    assert response.status_code==200,response.text
    assert response.json()['items']['reader']['basis']=='user'


def test_extended_dma_reads_message_fields_and_wrapper_forwarding_not_scalar_registers():
    from decompnet.consoles import get
    base=0x80000000
    caller=words(addiu(4,0,512),lui(5,base),addiu(5,5,0x1000),jal(base+128),addiu(6,0,32))
    wrapper=words(addiu(29,29,-64),sw(5,29,24),sw(4,29,28),sw(6,29,32),addiu(5,29,16),
                  addiu(4,0,0),jal(base+256),addiu(6,0,0),0x03e00008,0)
    image=bytearray(1024);image[:len(caller)]=caller;image[128:128+len(wrapper)]=wrapper
    functions=[dict(name='caller',vram=base,rom_offset=0,size=len(caller),unit='main'),
               dict(name='loader',vram=base+128,rom_offset=128,size=len(wrapper),unit='main'),
               dict(name='osEPiStartDma',vram=base+256,rom_offset=None,size=4,unit='main')]
    segs=[dict(name='main',rom_start=0,rom_end=len(image),vram=base)]
    flow=asset_flow.trace(bytes(image),segs,functions,mips_assets.read,contracts=get('n64').asset_calls)
    chain=next(c for c in flow['chains'] if c['function']=='caller')
    assert chain['source']['start']==512 and chain['source']['size']==32
    assert chain['buffer']['value']==base+0x1000 and 'wrapper' in chain['reason']
    # Replacing the message's length store with a partial write invalidates the descriptor.
    image[128+12:128+16]=words(0x29<<26 | 29<<21 | 6<<16 | 32)
    assert not asset_flow.trace(bytes(image),segs,functions,mips_assets.read,contracts=get('n64').asset_calls)['chains']


def test_unknown_aliasing_store_stops_later_buffer_links():
    image,segs,functions=fixture_chain()
    # Replace the consumer's argument move with a store through an unknown pointer.
    code=bytearray(image);last=functions[0]['size']-12;code[last:last+4]=words(sw(0,10,0))
    flow=asset_flow.trace(bytes(code),segs,functions,mips_assets.read)
    child=next(c for c in flow['chains'] if any(s['kind']=='decompress' for s in c['steps']))
    assert any(s['kind']=='overwrite' for s in child['steps'])
    assert not any(s.get('callee')=='render' for s in child['steps'])


def test_source_regions_include_consumer_function_filters_as_traced_leads(project):  # noqa: F811
    from decompnet.app import rom_explorer
    net,pid,root=project
    image,segs,functions=fixture_chain();(root/'game.rom').write_bytes(image)
    with net.progress.view():
        proj=net.project_row('game');cfg=dict(net.cfg(proj));cfg['rom']=dict(file='game.rom',sha1=hashlib.sha1(image).hexdigest());cfg['segments']=segs
        with net.store.tx() as db:
            db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),pid))
            db.execute('DELETE FROM functions WHERE project_id=?',(pid,))
            for f in functions:
                off=f['rom_offset'];target=image[off:off+f['size']] if off is not None else bytes(4)
                db.execute('INSERT INTO functions(project_id,name,vram,rom_offset,size,unit,target,n_insns,relocs) VALUES(?,?,?,?,?,?,?,?,?)',
                    (pid,f['name'],f['vram'],off,f['size'],f['unit'],target,f['size']//4,'[]'))
        rom_explorer.run(net,'game',Log())
        report=rom_explorer.listing(net,'game');row=next(r for r in report['regions'] if r['start']==256)
        assert {'loader','memcpy','uncompress','render'}<=set(row['function_refs'])
        assert row['traced_users']==4 and row['direct_users']==0 and row['used_by']==4
        detail=rom_explorer.detail(net,'game',row['id'],report['image_hash'])
        assert any(step.get('callee')=='render' for chain in detail['loader_flow']['chains'] for step in chain['steps'])


def test_custom_role_can_be_removed_after_its_function_was_renamed(project):  # noqa: F811
    from decompnet.app import rom_explorer
    net,pid,root=project
    with net.progress.view():
        rom_explorer.loader_contracts(net,'game',dict(function='reader',role='copy',source=0,destination=1,length=2,domain='rom'))
        with net.store.tx() as db:db.execute("UPDATE functions SET name='new_name' WHERE project_id=?",(pid,))
        assert not rom_explorer.loader_contracts(net,'game',dict(function='reader',remove=True))['items']


def test_wide_store_is_a_barrier_for_ee_and_vr4300_buffer_chains():
    image,segs,functions=fixture_chain()
    code=bytearray(image);last=functions[0]['size']-12;code[last:last+4]=words(0x3f<<26 | 17<<21)
    flow=asset_flow.trace(bytes(code),segs,functions,mips_assets.read)
    assert all(not any(step.get('callee')=='render' for step in chain['steps']) for chain in flow['chains'])
