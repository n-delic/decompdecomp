"""Recognize trailer-count texture packs whose loader rebuilds pointer tables.

The on-disk records contain palette/image counts and dimensions; pointer fields
are runtime placeholders. All texture records and payload extents must fit exactly.
"""
from __future__ import annotations

import struct

from ...rom import asset_archives, asset_formats
from ...rom.asset_formats import AssetError

MAX_EXPANDED=64*1024*1024


def layout(data):
    """Validate each record against the loader's alignment and payload-size rules."""
    if len(data)<49 or data[1:4]!=b'\x02\0\0' or not data[-1]:return None
    count=data[-1];cursor=(8+4*count+7)//8*8;items=[]
    for _ in range(count):
        cursor=(cursor+7)//8*8
        if cursor+32>len(data)-1:return None
        fmt,width,height,palettes,frames=struct.unpack_from('>HHHBB',data,cursor+4)
        if fmt not in (0x10,0x100,0xa0) or not 1<=width<=2048 or not 1<=height<=2048 or width*height>1024*1024 or not palettes or not frames:return None
        if palettes>1 and frames>1 or fmt==0xa0 and (palettes,frames)!=(1,1) or fmt==0x100 and frames>1:return None
        # The shared CI4 reader uses byte-padded rows; odd-width packed rows need an adapter.
        if fmt==0x10 and width%2:return None
        cursor+=32+4*(palettes+frames)
        palette_offsets=[];frame_offsets=[]
        if fmt!=0xa0:
            for _ in range(palettes):palette_offsets.append(cursor);cursor+=fmt*2
        pixel_bytes=width*height*(4 if fmt==0xa0 else 1) if fmt!=0x10 else width*height//2
        for _ in range(frames):
            if frames>1 or palettes>1:cursor=(cursor+7)//8*8
            frame_offsets.append(cursor);cursor+=pixel_bytes
        if cursor>len(data)-1:return None
        options=dict(format={0x10:'ci4',0x100:'ci8',0xa0:'rgba8888'}[fmt],width=width,height=height,offset=frame_offsets[0])
        if palette_offsets:options.update(palette_offset=palette_offsets[0],palette_format='rgba5551-be')
        items.append(dict(preview_settings=options,frames=frames,palettes=len(palette_offsets),frame_offsets=frame_offsets,palette_offsets=palette_offsets,levels=[dict(width=width,height=height,offset=0)]))
    if cursor!=len(data)-1:return None
    return items


def regions(image, *, check=lambda:None, progress=lambda _:None):
    """Inspect every bounded archive member; only matching packs are fully expanded."""
    members=asset_archives.zip_members(image,check=check);expanded=0;result=[]
    for index,member in enumerate(members):
        check()
        if index%64==0:progress(dict(phase='Recognizing archived texture packs',done=index,total=len(members)))
        try:
            head=asset_archives.member_bytes(image,member,prefix=True)
            if len(head)<4 or head[1:4]!=b'\x02\0\0':continue
            if expanded+member['expanded']>MAX_EXPANDED:break
            expanded+=member['expanded'];raw=asset_archives.member_bytes(image,member)
        except AssetError:continue
        items=layout(raw)
        if not items:continue
        result.append(dict(offset=member['offset'],size=member['size'],kind='image',label=member['name'],confidence='validated',layout='trailer-count-texture-pack',archive=member,textures=items,
                           **items[0],reason='ZIP member CRC and complete stream checked. Trailer-count texture records, palette sizes, frame sizes and all payload extents agree. Runtime use is not established.'))
        # These settings select the detected container; pixel offsets belong to decoded member bytes.
        result[-1]['preview_settings']={**items[0]['preview_settings'],'source':'rom','offset':member['offset']}
    return result


def preview(image,asset,selection):
    """Revalidate the member and its layout before applying local texture/frame choices."""
    raw=asset_archives.member_bytes(image,asset['archive']);items=layout(raw)
    if not items:raise AssetError('The archived texture layout changed. Rescan the ROM.')
    texture=asset_formats.integer(selection.get('texture',0),'Texture',0,len(items)-1)
    selected=items[texture]
    frame=asset_formats.integer(selection.get('frame',0),'Frame',0,selected['frames']-1)
    palette=asset_formats.integer(selection.get('palette',0),'Palette',0,max(0,selected['palettes']-1))
    asset_formats.integer(selection.get('mip',0),'Mip level',0,0)
    options={**selected['preview_settings'],'offset':selected['frame_offsets'][frame]}
    if selected['palettes']:options['palette_offset']=selected['palette_offsets'][palette]
    result=asset_formats.preview(raw,options)
    result['details'].update(texture=texture,frame=frame,palette=palette if selected['palettes'] else 'none',note=asset['reason'])
    result['decoded']=dict(codec='ZIP deflate' if asset['archive']['method']==8 else 'ZIP stored',bytes=len(raw),block_offset=asset['offset'],member=asset['archive']['name'])
    result['settings']=dict(source='rom',format='auto',offset=asset['offset'],texture=texture,frame=frame,palette=palette,mip=0)
    return result
