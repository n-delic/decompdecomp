"""Recognize an indexed cartridge directory by relationships, never release offsets.

Only a complete, bounded directory tree with matching object/binary ID counts
supplies semantic roles. Individual payloads are checked separately; broken or
unknown schemas do not become animation/collision labels from a short signature.
"""
from __future__ import annotations

import bisect
import re
import struct

from ...rom.asset_formats import AssetError
from . import rnc

MAX_BYTES=4*1024*1024
MAX_EXPANDED=64*1024*1024
MAX_TABLES=16384
MAX_ENTRIES=16384
MAX_ROOTS=128
ROOT_HEADER=re.compile(rb'\x00\x00\x00\x0b\x00\x00\x00\x38')
ROOT_ROLES=('objects','object attributes','object type IDs','textures','particle effects','sound-effect metadata',
            'levels','persistent counts','warp destinations','binaries','binary type IDs')
FIXED={'directory':ROOT_ROLES,'object':('object metadata','geometry','animations','static events'),
       'level':('level metadata','collision','object placements','grid bounds','grid sections','sky texture bank')}
COLLECTIONS={'objects':'object','textures':'texture bank','levels':'level','animations':'animation',
             'grid sections':'grid section','binaries':'binary resource'}
KINDS={'animation':'animation','collision':'collision','object placements':'object placements',
       'grid section':'world grid','grid bounds':'world grid','music sequence':'music sequence','demo recording':'demo'}


class SchemaError(ValueError):
    pass


def blocks(data,span,expected=None):
    lo,hi=span
    if hi-lo<8:raise SchemaError('Missing indexed table.')
    n=int.from_bytes(data[lo:lo+4],'big')
    if n>MAX_ENTRIES or 4*(n+2)>hi-lo:raise SchemaError('Indexed table exceeds its bounds.')
    if expected is not None and n!=expected:raise SchemaError('Unexpected directory table count.')
    offsets=struct.unpack_from('>'+str(n+1)+'I',data,lo+4)
    if offsets[0]!=(4*(n+2)+7)//8*8 or offsets[-1]!=hi-lo or any(a>b or a%8 for a,b in zip(offsets,offsets[1:])):
        raise SchemaError('Directory offsets do not match their container.')
    return [(lo+a,lo+b) for a,b in zip(offsets,offsets[1:])]


def stream(data,span,width=None):
    lo,hi=span
    if hi-lo<8:raise SchemaError('Missing record-stream header.')
    size,count=struct.unpack_from('>II',data,lo)
    extent=8+size*count
    if count>262144 or extent>hi-lo or hi-lo-extent>7 or size==0 and count:
        raise SchemaError('Record counts do not match the payload.')
    if width is not None and size not in ((width,0) if not count else (width,)):
        raise SchemaError('Unexpected record width.')
    return dict(record_bytes=size,records=count)


def directory(image,start,check):
    if start+52>len(image):raise SchemaError('Missing directory header.')
    end=start+int.from_bytes(image[start+48:start+52],'big')
    if end>len(image) or end<=start:raise SchemaError('Directory exceeds the image.')
    root=blocks(image,(start,end),11)
    objects=blocks(image,root[0]);textures=blocks(image,root[3]);levels=blocks(image,root[6]);binaries=blocks(image,root[9])
    if not objects or not textures or not levels:raise SchemaError('Core directory collections are empty.')
    if stream(image,root[2],2)['records']!=len(objects) or stream(image,root[10],4)['records']!=len(binaries):
        raise SchemaError('Object or binary ID counts disagree with their directories.')
    object_ids=[struct.unpack_from('>H',image,root[2][0]+8+i*2)[0] for i in range(len(objects))]
    binary_ids=[struct.unpack_from('>I',image,root[10][0]+8+i*4)[0] for i in range(len(binaries))]
    if binary_ids!=sorted(set(binary_ids)):raise SchemaError('Binary IDs are not unique and ordered.')
    tables=[];leaves=[]
    def walk(span,role,path,identity=None):
        check()
        if len(tables)+len(leaves)>=MAX_TABLES:raise SchemaError('Directory entry limit reached.')
        if role not in FIXED and role not in COLLECTIONS:
            if span[0]<span[1]:leaves.append((span,role,path,identity))
            return
        children=blocks(image,span,len(FIXED[role]) if role in FIXED else None)
        tables.append(dict(span=(span[0],span[0]+(4*(len(children)+2)+7)//8*8),role=role,path=path,count=len(children),identity=identity,type_flag=object_ids[identity] if role=='object' else None,children=[dict(offset=a,size=b-a,label=FIXED[role][i] if role in FIXED else f'{COLLECTIONS[role]}[{i}]') for i,(a,b) in enumerate(children)]))
        for i,child in enumerate(children):
            label=FIXED[role][i] if role in FIXED else COLLECTIONS[role]
            token=label if role in FIXED else f'{label}[{i}]'
            walk(child,label,path+'/'+token,binary_ids[i] if role=='binaries' else i if role=='objects' else identity)
    walk((start,end),'directory','directory')
    return tables,leaves


def fields(data,role):
    """Check source-defined record widths; return bounded metadata, not guessed rendering."""
    span=(0,len(data));info={}
    if role=='animation':
        parts=blocks(data,span,7)
        streams=[stream(data,parts[i],{0:2,1:4,2:20,6:20}.get(i)) for i in (0,1,2,3,4,6)]
        frames,nodes,orientations,translations,rotations,events=streams
        if not frames['records']:raise SchemaError('Animation has no frames.')
        for at in range(parts[1][0]+8,parts[1][0]+8+nodes['records']*4,4):
            t,r=struct.unpack_from('>hh',data,at)
            if not -1<=t<translations['records'] or not -1<=r<rotations['records']:
                raise SchemaError('Animation references an absent translation or rotation set.')
        blocks(data,parts[5])
        info=dict(frames=frames['records'],nodes=nodes['records'],initial_orientations=orientations['records'],initial_orientation_gap=orientations['records']<nodes['records'],translation_sets=translations['records'],rotation_sets=rotations['records'],events=events['records'])
    elif role in ('collision','grid bounds','grid section'):
        parts=blocks(data,span)
        expected={'collision':4,'grid bounds':5,'grid section':3}[role]
        if len(parts)!=expected and not(role=='grid section' and not parts):raise SchemaError('Unexpected world-data component count.')
        names={'collision':('region attributes','corners','regions','block bounds'),
               'grid bounds':('minimum X','maximum X','minimum Z','maximum Z','bounds'),
               'grid section':('extended static instances','static instances','simple instances')}[role]
        widths={'collision':(72,16,16,20),'grid bounds':(4,4,4,4,16),'grid section':(24,48,28)}[role]
        for name,part,width in zip(names,parts,widths):info[name]=stream(data,part,width)['records']
        info['empty']=not parts or not any(info.values())
    elif role=='object placements':info=stream(data,span,36)
    elif role in ('object attributes','object type IDs','binary type IDs','static events'):
        info=stream(data,span)
    elif role in ('texture bank','sky texture bank','geometry','object metadata','particle effects','sound-effect metadata','persistent counts','warp destinations'):
        parts=blocks(data,span);info=dict(components=len(parts),empty=not parts)
        if role=='geometry':
            if len(parts)!=2:raise SchemaError('Unexpected geometry header.')
            nodes=blocks(data,parts[1]);sections=0
            for node in nodes:
                child,indices,parts_span=blocks(data,node,3)
                stream(data,child,4);stream(data,indices,4)
                for part in blocks(data,parts_span):sections+=len(blocks(data,part))
            # Nonempty supported geometry already has a dedicated vertex/cache parser.
            # Never bless an unsupported mesh merely because its parent says geometry.
            if sections:raise SchemaError('Geometry needs a supported mesh decoder.')
            info=dict(nodes=len(nodes),mesh_sections=0,empty=True)
    elif role=='binary resource':
        if len(data)>=68:
            tracks=struct.unpack_from('>16I',data);active=[n for n in tracks if n];division=int.from_bytes(data[64:68],'big')
            if active and min(active)==68 and len(set(active))==len(active) and all(68<=n<len(data) for n in active) and 0<division<=65535:
                return 'music sequence',dict(tracks=len(active),division=division,track_offsets=active)
        if len(data)>=13:
            warp,frames,*starts=struct.unpack_from('>H5h',data)
            if warp and frames>0 and starts[0]==96 and starts==sorted(set(starts)) and all(96<=n<len(data)*8 for n in starts):
                return 'demo recording',dict(start_location_id=warp,frames=frames,bitstream_offsets=starts)
        return role,dict(bytes=len(data))
    else:info=dict(bytes=len(data))
    return role,info


def regions(image,*,known=(),check=lambda:None,progress=lambda _:None):
    existing={a['offset']:a for a in known};result=[];expanded=0;claimed=set()
    for index,match in enumerate(ROOT_HEADER.finditer(image)):
        if index>=MAX_ROOTS or len(result)+len(existing)>=MAX_TABLES:break
        check();start=match.start()
        try:tables,leaves=directory(image,start,check)
        except SchemaError:continue
        # A complete directory is accepted before inspecting compressed payloads.
        # Bad leaf bytes cannot make this parser invent another directory layout.
        for table in tables:
            if len(result)+len(existing)>=MAX_TABLES:break
            lo,hi=table['span']
            if lo in claimed or lo in existing:continue
            claimed.add(lo)
            asset=descriptor(lo,hi-lo,table['role'],table['path'],dict(entries=table['count']),start)
            if table['role']=='object':asset['resource_fields'].update(object_id=table['identity'],type_flag=table['type_flag'])
            asset['resource_children']=table['children']
            result.append(asset)
        for i,(span,role,path,identity) in enumerate(leaves):
            check();lo,hi=span
            if len(result)+len(existing)>=MAX_TABLES:break
            if hi-lo>MAX_BYTES+7:continue
            if lo in claimed:continue
            if i%128==0:progress(dict(phase='Recognizing directory resources',done=i,total=len(leaves)))
            compression=None;raw=image[lo:hi]
            if raw[:4] in (b'RNC\1',b'RNC\2'):
                if len(raw)<18:continue
                size,packed=struct.unpack_from('>II',raw,4)
                if not 0<size<=MAX_BYTES or packed+18>len(raw) or len(raw)-(packed+18)>7:continue
                compression=dict(codec='RNC method '+str(raw[3]),packed_bytes=packed+18,expanded_bytes=size)
                if lo in existing:
                    existing[lo].update(resource_type=role,resource_path=path,directory_offset=start,compression=compression)
                    claimed.add(lo);continue
                if expanded+size>MAX_EXPANDED:continue
                expanded+=size
                try:raw=(rnc.unpack if raw[3]==1 else rnc.unpack_m2)(raw[:18+packed],max_output=MAX_BYTES)
                except rnc.RncError:continue
            elif hi-lo>MAX_BYTES:continue
            if lo in existing:
                existing[lo].update(resource_type=role,resource_path=path,directory_offset=start)
                claimed.add(lo);continue
            if compression is None:
                if expanded+len(raw)>MAX_EXPANDED:continue
                expanded+=len(raw)
            try:category,info=fields(raw,role)
            except SchemaError:continue
            if identity is not None:info['resource_id']=identity
            claimed.add(lo)
            result.append(descriptor(lo,hi-lo,category,path,info,start,compression))
    # Standalone RNC streams also get checked extents. A directory is not required
    # to confirm compression; only the semantic directory roles require one.
    spans=sorted((a['offset'],a['offset']+a['size']) for a in [*known,*result])
    starts=[a for a,b in spans];ends=[]
    for a,b in spans:ends.append(max(b,ends[-1] if ends else 0))
    until=0
    for index,match in enumerate(re.finditer(b'RNC[\x01\x02]',image)):
        if index>=MAX_ENTRIES or len(result)+len(existing)>=MAX_TABLES:break
        check();lo=match.start();at=bisect.bisect_right(starts,lo)-1
        if lo<until or at>=0 and lo<ends[at] or lo+18>len(image):continue
        size,packed=struct.unpack_from('>II',image,lo+4)
        if not 0<size<=MAX_BYTES or not 2<=packed<=MAX_BYTES-18 or lo+18+packed>len(image):continue
        if expanded+size>MAX_EXPANDED:break
        expanded+=size
        try:raw=(rnc.unpack if image[lo+3]==1 else rnc.unpack_m2)(image[lo:lo+18+packed],max_output=MAX_BYTES)
        except rnc.RncError:continue
        role='compressed data';info=dict(bytes=len(raw))
        from .code import _looks_like_code
        if _looks_like_code(raw):
            match_at=image.find(raw,lo+18+packed)
            if match_at>=0:
                role='packed program image';info['expanded_image_offset']=match_at
                info['basis']='Exact decoded-byte match elsewhere in this analysis image; code-like MIPS contents.'
        compression=dict(codec='RNC method '+str(image[lo+3]),packed_bytes=18+packed,expanded_bytes=size)
        asset=descriptor(lo,18+packed,role,'',info,None,compression);asset['kind']='compressed'
        asset['reason']='Validated RNC extent, declared output size and both checksums. '+('Contents match an expanded copy; not every byte is an instruction.' if role=='packed program image' else 'Payload type is not established.')
        result.append(asset);until=lo+18+packed
    return result


def descriptor(offset,size,role,path,info,root,compression=None):
    settings=dict(format='auto',source='rom',offset=offset)
    if compression:settings.update(source='decoded',offset=0,block_offset=offset)
    return dict(offset=offset,size=size,kind=KINDS.get(role,'container' if role in FIXED or role in COLLECTIONS else 'data'),
                media='metadata',label=role.capitalize(),confidence='validated',resource_type=role,resource_path=path,
                directory_offset=root,resource_fields=info,compression=compression,preview_settings=settings,
                reason='Validated indexed directory relationships and resource extent. '+
                       ('Both RNC checksums passed. ' if compression else '')+
                       'The role follows this directory schema; runtime use and semantic names are not established.')


def preview(data,asset,selection):
    """The enclosing guarded reader checks original-image identity before metadata inspection."""
    role=asset['resource_type'];info=asset['resource_fields']
    if role not in FIXED and role not in COLLECTIONS:
        lo=0 if asset['compression'] else asset['offset']
        size=asset['compression']['expanded_bytes'] if asset['compression'] else asset['size']
        try:
            parsed,checked=fields(data[lo:lo+size], 'binary resource' if role in ('music sequence','demo recording') else role)
            if parsed!=role:raise SchemaError('The resource header changed.')
        except SchemaError as exc:raise AssetError(str(exc)+' Rescan ROM Explorer.') from None
        info={**info,**checked}
    from . import resource_inspection
    extra={}
    try:
        if asset.get('resource_children') is not None:
            children=asset['resource_children']
            extra['table']=resource_inspection.table('Directory resources',['index','label','offset','size'],len(children),lambda i:children[i],selection)
        else:
            extra=resource_inspection.inspect(data[lo:lo+size],role,selection)
    except SchemaError as exc:
        extra['inspection_warning']=str(exc)+' Header counts remain available; unsupported records are not rendered.'
    return dict(media='metadata',title=asset['label'],fields=info,resource_path=asset['resource_path'],**extra,
                compression=asset.get('compression'),note='Source-layout records and directory links. ADPCM animation tracks are identified but not played; object behaviour, music synthesis and scene reconstruction remain unresolved.',
                settings=dict(format='auto',source='rom',offset=asset['offset']))
