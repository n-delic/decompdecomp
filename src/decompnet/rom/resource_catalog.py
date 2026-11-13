"""Portable font recognition and bounded text/pointer inspection for every console."""
import re
import struct

from .asset_formats import AssetError, integer

LIMIT=4*1024*1024
PAGE=64


def font(data,start):
    """PSF bitmap fonts without Unicode tables have exact, self-describing extents."""
    if data[start:start+2]==b'\x36\x04':
        if start+4>len(data):return None
        mode,height=data[start+2:start+4]
        if mode not in (0,1) or not height:return None
        count=512 if mode else 256;width=8;header=4;size=height
    elif data[start:start+4]==b'\x72\xb5\x4a\x86':
        if start+32>len(data):return None
        magic,version,header,flags,count,size,height,width=struct.unpack_from('<8I',data,start)
        if version or flags or header!=32 or not 1<=width<=64 or not 1<=height<=64 or not 1<=count<=4096 or size!=height*((width+7)//8):return None
    else:return None
    extent=header+count*size
    if extent>LIMIT or start+extent>len(data):return None
    return dict(width=width,height=height,glyphs=count,glyph_bytes=size,header=header,size=extent)


def regions(image,*,check=lambda:None):
    result=[]
    for i,match in enumerate(re.finditer(rb'\x72\xb5\x4a\x86',image)):
        if i>=16384:break
        check();offset=match.start();info=font(image,offset)
        if info:
            result.append(dict(offset=offset,size=info['size'],kind='font',media='metadata',provider='portable-font',
                               label='PSF bitmap font',confidence='validated',resource_fields=info,
                               reason='Validated PSF header and complete glyph bitmap extent. Character mapping and runtime use are not established.',
                               preview_settings=dict(format='auto',source='rom',offset=offset)))
    return result


def attach(report):
    """Offer inspectors for existing tentative clues without strengthening confidence."""
    for row in report['regions']:
        if row.get('detected_asset') or row['kind'] not in ('strings','pointer table'):continue
        row['detected_asset']=dict(offset=row['start'],size=row['size'],kind=row['kind'],media='metadata',provider='portable-records',
                                  label='Text candidates' if row['kind']=='strings' else 'Pointer candidates',confidence=row['confidence'],
                                  preview_settings=dict(format='auto',source='rom',offset=row['start']))


def preview(data,asset,selection,segments=(),big_endian=True):
    start=asset['offset'];end=min(len(data),start+asset['size'],start+LIMIT)
    page=integer(selection.get('record_page',0),'Record page',0,65535);rows=[];geometry=None
    if asset['provider']=='portable-font':
        info=font(data,start)
        if not info:raise AssetError('The font header changed. Reopen ROM Explorer.')
        total=info['glyphs'];columns=['index','bitmap']
        if page*PAGE>=total:raise AssetError('Choose an existing glyph page.')
        for i in range(page*PAGE,min(total,(page+1)*PAGE)):
            at=start+info['header']+i*info['glyph_bytes'];raw=data[at:at+info['glyph_bytes']]
            rows.append(dict(index=i,bitmap=raw.hex()))
        # A compact atlas is sent as pixels; the renderer uses the shared canvas.
        width=info['width'];height=info['height'];stride=(width+7)//8
        geometry=dict(width=width,height=height,glyphs=[dict(index=r['index'],pixels=[''.join('1' if data[start+info['header']+r['index']*info['glyph_bytes']+y*stride+x//8]&(128>>(x%8)) else '0' for x in range(width)) for y in range(height)]) for r in rows])
        fields=info;title='PSF glyphs';note='Bitmap order only; no Unicode mapping or game-specific character set is inferred.'
    elif asset['kind']=='strings':
        # Strict UTF-8, NUL termination and visible characters avoid replacement
        # decoding turning random bytes into apparent game dialogue.
        candidates=[]
        for match in re.finditer(rb'[^\x00]{4,1024}\x00',data[start:end]):
            try:text=match[0][:-1].decode('utf-8')
            except UnicodeDecodeError:continue
            if all(c.isprintable() or c in '\n\r\t' for c in text):candidates.append(dict(offset=start+match.start(),text=text))
        total=len(candidates);rows=[dict(index=i,**candidates[i]) for i in range(page*PAGE,min(total,(page+1)*PAGE))]
        columns=['index','offset','text'];fields=dict(inspected_bytes=end-start);title='UTF-8 text candidates';note='Printable NUL-terminated strings; dialogue, labels, paths and format strings are not distinguished. Inspection is limited to the first 4 MiB.'
    else:
        at=(start+3)//4*4;total=max(0,(end-at)//4);columns=['index','offset','address','destinations'];fields=dict(inspected_bytes=end-start);title='Pointer candidates'
        for i in range(page*PAGE,min(total,(page+1)*PAGE)):
            offset=at+i*4;address=int.from_bytes(data[offset:offset+4],'big' if big_endian else 'little')
            destinations=[s['rom_start']+address-s['vram'] for s in segments if s['vram']<=address<s['vram']+s['rom_end']-s['rom_start']] if address not in (0,0xffffffff) else []
            rows.append(dict(index=i,offset=offset,address=address,destinations=destinations))
        note='Aligned words mapped through imported segments. Overlay alternatives and integer coincidences remain possible; a load or runtime use is not proven. Inspection is limited to the first 4 MiB.'
    if page and page*PAGE>=total:raise AssetError('Choose an existing record page.')
    result=dict(media='metadata',title=title,fields=fields,table=dict(title=title,columns=columns,rows=rows,total=total,page=page,page_size=PAGE),note=note,settings=dict(format='auto',source='rom',offset=start,record_page=page))
    if geometry:result['font_atlas']=geometry
    return result
