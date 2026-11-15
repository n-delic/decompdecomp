"""Invented bitstreams, glyphs and linked containers; no original assets."""
import struct

import pytest

from decompnet.consoles.n64 import animation, fonts, native_scene, resources
from decompnet.rom.asset_formats import AssetError

from .test_native_models import container, mesh
from .test_native_resources import fixture, stream
from .test_native_textures import indexed_set
from .test_rom_explorer import project  # noqa: F401


def packed(frames,signals,codes=None,initials=None):
    codes=codes or [0]*((frames+3)//4);initials=initials or [0]*len(signals)
    bits=[]
    def put(value,width):bits.extend((value>>i)&1 for i in range(width))
    for code in codes:put(code,6)
    for initial,signal in zip(initials,signals):
        put(initial,8)
        for value in signal:put(value&7,3)
    data=bytearray((len(bits)+7)//8)
    for i,bit in enumerate(bits):data[i//8]|=bit<<(i%8)
    return struct.pack('>H',frames)+data


def anim():
    frames=4;translation=struct.pack('>I4f',1,0,0,0,32767)+packed(frames,[[1]*frames,[0]*frames,[0]*frames])
    rotation=packed(frames,[[0]*frames]*4,initials=[0,0,0,127])
    return indexed_set([stream(2,frames),stream(4,1,struct.pack('>hh',0,0)),stream(20,1,struct.pack('>3f4h',0,0,0,0,0,0,32767)),
                        stream(len(translation),1,translation),stream(len(rotation),1,rotation),indexed_set([]),stream(20)])


def test_shared_headers_nonbyte_alignment_and_prediction():
    raw=packed(8,[[1,2,-1,0,1,1,1,1],[0]*8],codes=[0,16],initials=[0,128])
    assert animation.adpcm(raw,2,8)==[[1,3,2,2,3,4,5,6],[-32768]*8]
    assert animation.adpcm(packed(1,[[-4]],codes=[13],initials=[128]),1,1)==[[-32768]]
    for invalid in (raw[:-1],bytes(2),b''):
        with pytest.raises(AssetError):animation.adpcm(invalid,2,8)
    with pytest.raises(AssetError):animation.adpcm(raw,5,8)


def test_tracks_decode_translation_scale_and_rotation():
    result=animation.tracks(anim())
    assert result['frames']==4 and result['nodes'][0]['positions']==[[1,0,0],[2,0,0],[3,0,0],[4,0,0]]
    assert result['nodes'][0]['rotations'][0][3]==pytest.approx(32512/32767)
    raw=anim();parts=resources.blocks(raw,(0,len(raw)),7)
    missing=indexed_set([raw[a:b] if i not in (1,2) else stream(4,1,struct.pack('>hh',-1,-1)) if i==1 else stream(20) for i,(a,b) in enumerate(parts)])
    with pytest.raises(AssetError,match='initial'):animation.tracks(missing)


def glyph_image():
    base=0x80001000;pointer_at=16;glyph_at=128
    pointers=struct.pack('>26I',*(base+glyph_at+i*128 for i in range(26)))
    image=bytes(pointer_at)+pointers+bytes(glyph_at-pointer_at-len(pointers))+bytes(range(128))*26
    context=dict(segments=[dict(rom_start=0,rom_end=len(image),vram=base)],functions=[dict(name='Overlay__WriteText',relocs=[(0,5,base+pointer_at-4)])])
    return image,context


def test_native_font_requires_named_writer_unique_mapping_and_complete_glyphs():
    image,context=glyph_image();found=fonts.regions(image,context=context)
    assert len(found)==1 and found[0]['offset']==16 and found[0]['confidence']=='inferred'
    result=fonts.preview(image,found[0],{})
    assert result['font_atlas']['levels']==15 and result['table']['rows'][0]['character']=='A'
    assert result['font_atlas']['glyphs'][0]['pixels'][0]=='0001020304050607'
    assert not fonts.regions(image[:-1],context=context)
    context['functions'][0]['name']='unknown';assert not fonts.regions(image,context=context)


def test_linked_scene_uvs_textures_and_animated_assembly():
    from decompnet.consoles import get
    image=fixture();assets=get('n64').asset_regions(image);model=next(a for a in assets if a.get('media')=='model')
    result=native_scene.preview(image,model,{},assets)
    assert result['materials'][0]['state']=='linked' and result['materials'][0]['image'].startswith('data:image/png;base64,')
    assert result['surfaces'][0]['uv']==[[0,0]]*3
    assert result['surfaces'][0]['colors'][0][:3]==[255,255,255]  # primitive RGB does not blacken ordinary textures
    # Replace only the invented animation leaf and rebuild its directory parents.
    root=resources.blocks(image,(0,len(image)));objects=resources.blocks(image,root[0]);obj=resources.blocks(image,objects[0])
    rebuilt_obj=indexed_set([image[a:b] if i!=2 else indexed_set([anim()]) for i,(a,b) in enumerate(obj)])
    rebuilt=indexed_set([image[a:b] if i!=0 else indexed_set([rebuilt_obj]) for i,(a,b) in enumerate(root)])
    assets=get('n64').asset_regions(rebuilt);model=next(a for a in assets if a.get('media')=='model')
    result=native_scene.preview(rebuilt,model,dict(assembly='animated'),assets)
    assert result['animation']['frames']==4 and result['parts'][0]['node']==0
    assert result['animation']['nodes'][0]['positions'][-1]==[4,0,0]
    with pytest.raises(AssetError):native_scene.preview(rebuilt,model,dict(animation=9),assets)


def test_standalone_models_do_not_guess_texture_links():
    from decompnet.consoles import get
    section,_=mesh();image=container(section);asset=get('n64').asset_regions(image)[0]
    assert native_scene.preview(image,asset,{},[asset]) is None


def test_native_playback_browser_lifecycle():
    import shutil
    import subprocess
    from pathlib import Path
    node=shutil.which('node')
    if not node:pytest.skip('needs node')
    subprocess.run([node,str(Path(__file__).with_name('gui_native_model_animation.js'))],check=True,timeout=30,capture_output=True)


def test_guarded_scene_api_and_image_changes(project):  # noqa: F811
    import copy
    import hashlib
    import json

    from decompnet.app import rom_explorer
    from decompnet.network import NetworkError

    from .test_rom_explorer import Log
    net,pid,root=project;image=fixture();(root/'game.rom').write_bytes(image)
    cfg=copy.deepcopy(net.cfg(net.project_row('game')));cfg['rom']['sha1']=hashlib.sha1(image).hexdigest();cfg['segments'][0]['rom_end']=len(image)
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),pid));db.execute('DELETE FROM functions WHERE project_id=?',(pid,))
    rom_explorer.run(net,'game',Log());report=rom_explorer.listing(net,'game');row=next(r for r in report['regions'] if r['kind']=='3d model')
    request=dict(region=row['id'],image_hash=report['image_hash'],options=dict(format='auto',offset=row['start']))
    result=rom_explorer.asset_preview(net,'game',request)
    assert result['materials'][0]['state']=='linked'
    (root/'game.rom').write_bytes(image[:-1]+b'!')
    with pytest.raises(NetworkError,match='changed'):rom_explorer.asset_preview(net,'game',request)


def test_native_uv_scaling_and_pseudocolor_endpoints():
    from decompnet.consoles import get
    material=struct.pack('>II8BHHI',0,8|1024,255,0,0,255,0,0,255,255,65535,32768,0)
    rows=[struct.pack('>3hH2h4B',i*10,0,0,0,64,128,255,255,255,255) for i in range(3)]
    vertices=stream(16,3,bytes(row[j] for j in range(16) for row in rows))
    section=indexed_set([material,stream(2,1,struct.pack('>H',0x8000|(1<<5)|(2<<10))),vertices])
    original=fixture();root=resources.blocks(original,(0,len(original)));obj=resources.blocks(original,resources.blocks(original,root[0])[0])
    object_data=indexed_set([original[a:b] if i!=1 else container(section) for i,(a,b) in enumerate(obj)])
    image=indexed_set([original[a:b] if i!=0 else indexed_set([object_data]) for i,(a,b) in enumerate(root)])
    assets=get('n64').asset_regions(image);asset=next(a for a in assets if a.get('media')=='model');texture=next(a for a in assets if a.get('resource_type')=='texture bank')
    result=native_scene.preview(image,asset,{},assets);uv=result['surfaces'][0]['uv'][0]
    assert uv[0]==pytest.approx(2*65535/65536/texture['preview_settings']['width'])
    assert uv[1]==pytest.approx(2/texture['preview_settings']['height'])
    assert result['materials'][0]['pseudo_color'] is True
    assert result['materials'][0]['color']==[1,0,0,1] and result['materials'][0]['black_color']==[0,0,1,1]
