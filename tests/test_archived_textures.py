"""Embedded archives and pointer-rebuilt texture records use only homemade bytes."""
import base64
import hashlib
import io
import json
import struct
import zipfile

import pytest

from decompnet.app import rom_explorer
from decompnet.consoles import get
from decompnet.consoles.n64 import texture_packs
from decompnet.rom import asset_archives, asset_formats, explorer

from .test_rom_assets import pixels
from .test_rom_explorer import Log, project  # noqa: F401


def pack(records=((16,8,4,1,1),)):
    data=bytearray(bytes([len(records),2])+bytes(6+len(records)*4))
    for fmt,width,height,palettes,frames in records:
        data.extend(bytes(-len(data)%8));head=bytearray(32)
        struct.pack_into('>HHHBB',head,4,fmt,width,height,palettes,frames)
        data.extend(head);data.extend(bytes(4*(palettes+frames)))
        if fmt!=160:
            for index in range(palettes):
                color=b'\xf8\x01' if index==0 else b'\x00\x3f'
                data.extend(color*fmt)
        for frame in range(frames):
            if frames>1 or palettes>1:data.extend(bytes(-len(data)%8))
            if fmt==16:data.extend(bytes([frame%16*17])*(width*height//2))
            elif fmt==256:data.extend(bytes([frame])*width*height)
            else:data.extend(bytes([240,16,32,255])*(width*height))
    data.append(len(records));return bytes(data)


def archive(data=None,method=zipfile.ZIP_DEFLATED,*,name='textures/demo.pack'):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',compression=method) as z:
        z.writestr(name,data if data is not None else pack())
        z.writestr('other/notes.txt','A synthetic non-texture member')
    return bytes(64)+out.getvalue()+bytes(100000)


@pytest.mark.parametrize('method',[zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED])
def test_embedded_zip_offsets_padding_crc_and_no_extraction(method,tmp_path):
    image=archive(method=method,name='../display-only.pack')
    members=asset_archives.zip_members(image)
    assert len(members)==2 and members[0]['name']=='../display-only.pack'
    assert asset_archives.member_bytes(image,members[0])==pack()
    assert asset_archives.member_bytes(image,members[0],prefix=True)==pack()[:32]
    assert members[0]['offset']==64 and not list(tmp_path.iterdir())
    corrupted=bytearray(image);corrupted[members[0]['payload']+members[0]['packed']//2]^=1
    with pytest.raises(asset_formats.AssetError):asset_archives.member_bytes(corrupted,members[0])
    # A forged expansion length cannot turn the bounded inflater into a bomb.
    with pytest.raises(asset_formats.AssetError):asset_archives.member_bytes(image,{**members[0],'expanded':1})
    with pytest.raises(asset_formats.AssetError):asset_archives.member_bytes(image,{**members[0],'expanded':asset_archives.MAX_MEMBER+1})


def test_zip_central_local_consistency_truncation_and_cancellation():
    image=bytearray(archive());local=image.find(b'PK\x03\x04');central=image.find(b'PK\x01\x02')
    assert not asset_archives.zip_members(image[:central+10])
    image[local+8]=99
    assert not asset_archives.zip_members(image)
    image=bytearray(archive());image[central+42:central+46]=b'\xff'*4
    assert not asset_archives.zip_members(image)
    def stop():raise RuntimeError('Stopped')
    with pytest.raises(RuntimeError,match='Stopped'):texture_packs.regions(archive(),check=stop)


@pytest.mark.parametrize('records',[
    ((16,8,4,1,1),),((256,8,4,1,1),),((160,4,4,1,1),),
    ((16,8,4,2,1),),((16,8,4,1,2),),((16,8,4,1,1),(160,4,4,1,1)),
])
def test_pack_layout_matches_complete_payload_and_previews_each_texture(records):
    data=pack(records);found=texture_packs.layout(data)
    assert found and len(found)==len(records)
    image=archive(data);assets=texture_packs.regions(image)
    assert len(assets)==1 and assets[0]['confidence']=='validated'
    for i,record in enumerate(records):
        result=get('n64').asset_preview(image,assets[0],dict(texture=i))
        rgba=pixels(base64.b64decode(result['data_url'].split(',')[1]))
        assert (result['details']['width'],result['details']['height'])==record[1:3] and len(rgba)==record[1]*record[2]*4
        assert result['decoded']['codec']=='ZIP deflate' and result['settings']['texture']==i
    with pytest.raises(asset_formats.AssetError):texture_packs.preview(image,assets[0],dict(texture=len(records)))


def test_palette_and_frame_choices_are_checked_against_selected_texture():
    image=archive(pack(((16,8,4,2,1),(16,4,4,1,2))))
    asset=texture_packs.regions(image)[0]
    first=texture_packs.preview(image,asset,dict(texture=0,palette=0))
    second=texture_packs.preview(image,asset,dict(texture=0,palette=1))
    assert first['data_url']!=second['data_url']
    assert pixels(base64.b64decode(first['data_url'].split(',')[1]))[:4]==b'\xff\0\0\xff'
    assert pixels(base64.b64decode(second['data_url'].split(',')[1]))[:4]==b'\0\0\xff\xff'
    assert texture_packs.preview(image,asset,dict(texture=1,frame=1))['settings']['frame']==1
    with pytest.raises(asset_formats.AssetError):texture_packs.preview(image,asset,dict(texture=0,frame=1))
    with pytest.raises(asset_formats.AssetError):texture_packs.preview(image,asset,dict(texture=1,palette=1))


def test_invalid_or_partial_packs_never_become_detected_textures():
    original=pack()
    for data in (original[:-2]+original[-1:],original+b'\0',bytes(64),pack(((16,8,4,2,2),)),pack(((16,3,4,1,1),))):
        assert texture_packs.layout(data) is None
        assert texture_packs.regions(archive(data))==[]
    image=archive(original);member=asset_archives.zip_members(image)[0]
    assert texture_packs.regions(image[:member['payload']+member['packed']])==[]


def test_archive_scan_honours_expansion_budget(monkeypatch):
    monkeypatch.setattr(texture_packs,'MAX_EXPANDED',len(pack())-1)
    assert texture_packs.regions(archive())==[]


def test_archived_texture_reaches_saved_map_preview_and_annotation(project):  # noqa: F811
    net,pid,root=project;image=archive(pack(((16,8,4,2,1),(160,4,4,1,1))))
    (root/'game.rom').write_bytes(image)
    with net.progress.view():cfg=json.loads(json.dumps(net.cfg(net.project_row('game'))))
    cfg['rom']['sha1']=hashlib.sha1(image).hexdigest()
    cfg['segments'][0]['rom_end']=len(image)
    with net.store.tx() as db:db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),pid))
    before=[tuple(r) for r in net.store.all('SELECT * FROM functions')]
    rom_explorer.run(net,'game',Log());report=rom_explorer.listing(net,'game')
    rows=[r for r in report['regions'] if (r.get('detected_asset') or {}).get('archive')]
    assert rows and sum(r['size'] for r in report['regions'])==len(image)
    request=dict(region=rows[0]['id'],image_hash=report['image_hash'],options=dict(format='auto',texture=1))
    result=rom_explorer.asset_preview(net,'game',request)
    assert result['details']['width']==4 and result['settings']['texture']==1
    rom_explorer.annotate(net,'game',dict(region=rows[0]['id'],image_hash=report['image_hash'],name='Synthetic texture',preview_settings=result['settings']))
    assert rom_explorer.detail(net,'game',rows[0]['id'],report['image_hash'])['annotation']['preview_settings']['texture']==1
    assert before==[tuple(r) for r in net.store.all('SELECT * FROM functions')]
    assert not net.store.all('SELECT * FROM attempts')


def test_asset_metadata_does_not_depend_on_file_extensions():
    image=archive(name='arbitrary.dat');assets=texture_packs.regions(image)
    report=explorer.analyse(image,[],[],[],assets=assets)
    assert any(r.get('detected_asset',{}).get('archive') for r in report['regions'] if r.get('detected_asset'))


def test_zip_data_descriptors_and_duplicate_end_records_are_bounded():
    class Streaming(io.BytesIO):
        def seek(self,*args):raise OSError('Streaming writer')
    out=Streaming()
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:z.writestr('stream.pack',pack())
    image=bytes(32)+out.getvalue()+bytes(20)
    members=asset_archives.zip_members(image)
    assert len(members)==1 and asset_archives.member_bytes(image,members[0])==pack()
    assert len(texture_packs.regions(image))==1
