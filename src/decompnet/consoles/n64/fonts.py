"""Source-family I4 fonts corroborated by a named writer and mapped glyph tables."""
import struct

from ...rom.asset_formats import AssetError


def regions(image,*,context=None,known=(),check=lambda:None,**kwargs):
    context=context or {};segments=list(context.get('segments',[]));results=[];seen=set()
    # Some importers map instructions only. Extend to data only when a checked
    # packed program exactly matches this expanded copy and its segments agree.
    for asset in known:
        info=asset.get('resource_fields',{});start=info.get('expanded_image_offset')
        if asset.get('resource_type')!='packed program image' or start is None:continue
        end=start+asset['compression']['expanded_bytes']
        offsets={s['rom_start']-s['vram'] for s in segments if start<=s['rom_start']<s['rom_end']<=end}
        if len(offsets)==1:
            bias=offsets.pop();segments.append(dict(rom_start=start,rom_end=end,vram=start-bias))
    def mapped(address,size):
        offsets={s['rom_start']+address-s['vram'] for s in segments if s['vram']<=address and address+size<=s['vram']+s['rom_end']-s['rom_start']}
        return next(iter(offsets)) if len(offsets)==1 and 0<=next(iter(offsets))<=len(image)-size else None
    for function in context.get('functions',[]):
        if not function['name'].endswith('__WriteText'):continue
        for _,_,address in function.get('relocs',[]):
            for adjust in (0,4):
                check();at=mapped(address+adjust,4)
                if at is None or at in seen:continue
                pointers=[];offsets=[]
                for i in range(257):
                    pos=mapped(address+adjust+i*4,4)
                    if pos is None:break
                    pointer=struct.unpack_from('>I',image,pos)[0];glyph=mapped(pointer,128)
                    if glyph is None or pointers and pointer!=pointers[-1]+128:break
                    pointers.append(pointer);offsets.append(glyph)
                if len(pointers) not in (10,26):continue
                # Empty or constant blocks do not corroborate a glyph bank.
                if any(len(set(image[o:o+128]))<3 for o in offsets):continue
                seen.add(at);count=len(pointers)
                results.append(dict(offset=at,size=count*4,kind='font',media='metadata',font_layout='native-i4-pointer-glyphs',
                    label='Native digit font' if count==10 else 'Native alphabet font',confidence='inferred',
                    glyph_offsets=offsets,glyph_labels=list('0123456789' if count==10 else 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'),
                    reason='Named source-family text writer references a mapped table of contiguous 16 × 16 I4 glyphs. Character mapping follows this source family, not a bitmap signature.',
                    preview_settings=dict(format='auto',source='rom',offset=at),font_writer=function['name']))
    return results


def preview(image,asset,selection):
    from .resource_inspection import table
    offsets=asset['glyph_offsets'];labels=asset['glyph_labels'];glyphs=[]
    for i,offset in enumerate(offsets):
        if not 0<=offset<=len(image)-128:raise AssetError('Font glyph exceeds the original image. Rescan ROM Explorer.')
        pixels=image[offset:offset+128].hex();glyphs.append(dict(index=i,label=labels[i],pixels=[pixels[y*16:(y+1)*16] for y in range(16)]))
    return dict(media='metadata',title=asset['label'],fields=dict(glyphs=len(offsets),format='I4',writer=asset['font_writer']),
                font_atlas=dict(width=16,height=16,levels=15,glyphs=glyphs),
                table=table('Native glyphs',['index','character','offset'],len(offsets),lambda i:dict(character=labels[i],offset=offsets[i]),selection),
                note='Source-family character order. Grayscale intensity preview; runtime colour ramps and text effects are not applied.',
                settings=dict(format='auto',source='rom',offset=asset['offset']))
