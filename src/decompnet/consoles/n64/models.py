"""Recognize indexed geometry with byte-plane vertices and cache-relative polygons.

The nested tables, complete stream extents and every cache reference must agree.
Sections are previewed in local coordinates: animation, skeleton transforms and
material rendering are deliberately not inferred from plausible vertex positions.
"""
from __future__ import annotations

import re
import struct

from ...rom.asset_formats import AssetError, integer
from . import rnc, textures

MAX_BYTES = 4*1024*1024
MAX_EXPANDED = 64*1024*1024
MAX_CANDIDATES = 16384
MAX_SECTIONS = 2048
RAW_HEADER = re.compile(rb'\x00\x00\x00\x02\x00\x00\x00\x10\x00\x00\x00\x18')


class ModelError(ValueError):
    pass


def blocks(data, span, count=None):
    result=textures._blocks(data,*span,empty=True)
    if result is None or count is not None and len(result)!=count:raise ModelError('Invalid indexed geometry table.')
    return result


def stream(data,span,width,limit):
    start,end=span
    if end-start<8:raise ModelError('Missing geometry stream header.')
    size,count=struct.unpack_from('>II',data,start)
    if (size!=width and (count or size!=0)) or count>limit or (8+count*width+7)//8*8!=end-start:
        raise ModelError('Invalid geometry stream extent.')
    return start+8,count


def polygons(data,span,count):
    """Resolve the initial 32 vertices and alternating 16-slot replacements."""
    at,total=stream(data,span,2,8192)
    cache=[None]*32;loaded=0;cursor=0;run=0;faces=[]
    while loaded<count:
        slot=0 if run==0 or run%2 else 16
        size=min(count-loaded,32 if run==0 else 16)
        cache[slot:slot+size]=range(loaded,loaded+size);loaded+=size;run+=1
        while True:
            if cursor>=total:raise ModelError('Missing polygon run terminator.')
            word=struct.unpack_from('>H',data,at+cursor*2)[0];cursor+=1
            face=[cache[(word>>shift)&31] for shift in (0,5,10)]
            if None in face:raise ModelError('Polygon references an unloaded vertex.')
            faces.append(face)
            if word&0x8000:break
    if cursor!=total:raise ModelError('Unused polygon commands remain.')
    return faces


def layout(data):
    root=blocks(data,(0,len(data)),2)
    if root[0]!=(16,24):raise ModelError('Invalid geometry information record.')
    nodes=blocks(data,root[1]);meshes=[];vertex_total=0;triangle_total=0
    for node,node_span in enumerate(nodes):
        children,indices,parts_span=blocks(data,node_span,3)
        child_at,nchildren=stream(data,children,4,256)
        if any(struct.unpack_from('>I',data,child_at+i*4)[0]>=len(nodes) for i in range(nchildren)):
            raise ModelError('Child node index exceeds the node table.')
        parts=blocks(data,parts_span)
        part_at,nparts=stream(data,indices,4,256)
        if any(struct.unpack_from('>I',data,part_at+i*4)[0]>=len(parts) for i in range(nparts)):
            raise ModelError('Part index exceeds the part table.')
        for part,part_span in enumerate(parts):
            for section,section_span in enumerate(blocks(data,part_span)):
                if len(meshes)>=MAX_SECTIONS:raise ModelError('Geometry section limit reached.')
                material,poly,vertices=blocks(data,section_span,3)
                if material[1]-material[0]!=24:raise ModelError('Invalid geometry material extent.')
                vertex_at,count=stream(data,vertices,16,4096)
                faces=polygons(data,poly,count)
                if not count or not faces:raise ModelError('Empty geometry section.')
                vertex_total+=count;triangle_total+=len(faces)
                if vertex_total>65536 or triangle_total>131072:raise ModelError('Geometry scan limit reached.')
                texture,flags=struct.unpack_from('>II',data,material[0])
                meshes.append(dict(node=node,part=part,section=section,offset=section_span[0],
                                   vertices=count,triangles=len(faces),texture=texture,material_flags=flags,vertex_offset=vertex_at))
    if not meshes:raise ModelError('No geometry sections found.')
    return dict(nodes=len(nodes),meshes=meshes,vertices=vertex_total,triangles=triangle_total)


def regions(image, *, check=lambda:None, progress=lambda _:None):
    candidates=sorted({m.start() for m in RAW_HEADER.finditer(image)} |
                      {m.start() for m in re.finditer(b'RNC[\x01\x02]',image)})
    expanded=0;contained_end=0;result=[]
    for index,offset in enumerate(candidates[:MAX_CANDIDATES]):
        check()
        if offset<contained_end:continue
        if index%64==0:progress(dict(phase='Recognizing model geometry',done=index,total=min(len(candidates),MAX_CANDIDATES)))
        compressed=image[offset:offset+4] in (b'RNC\1',b'RNC\2')
        if compressed:
            if offset+18>len(image):continue
            size,packed=struct.unpack_from('>II',image,offset+4);extent=packed+18
            if not 0<size<=MAX_BYTES or not 2<=packed<=MAX_BYTES-18 or offset+extent>len(image):continue
            if expanded+size>MAX_EXPANDED:break
            expanded+=size
            try:data=(rnc.unpack if image[offset+3]==1 else rnc.unpack_m2)(image[offset:offset+extent],max_output=MAX_BYTES)
            except rnc.RncError:continue
            contained_end=offset+extent
        else:
            if offset+16>len(image):continue
            extent=int.from_bytes(image[offset+12:offset+16],'big')
            if not 32<=extent<=MAX_BYTES or offset+extent>len(image):continue
            data=image[offset:offset+extent]
        try:found=layout(data)
        except ModelError:continue
        settings=dict(format='auto',source='decoded' if compressed else 'rom',offset=0 if compressed else offset)
        if compressed:settings['block_offset']=offset
        result.append(dict(offset=offset,size=extent,kind='3d model',media='model',label='Indexed model geometry',confidence='validated',
                           reason='Source-assisted geometry: nested tables, byte-plane vertex extents and every polygon cache reference passed validation.'+
                                  (' Both RNC checksums passed.' if compressed else '')+' Meshes use local coordinates; runtime use and animation are not established.',
                           preview_settings=settings,**found))
    return result


def preview(data,asset,selection):
    start=0 if asset['preview_settings']['source']=='decoded' else asset['offset']
    # Recheck the container rather than trusting browser-supplied layout metadata.
    try:
        end=int.from_bytes(data[start+12:start+16],'big')+start
        if not 0<end-start<=MAX_BYTES or end>len(data):raise ModelError('Invalid geometry extent.')
        raw=data[start:end];found=layout(raw)
        mesh=integer(selection.get('mesh',0),'Mesh',0,len(found['meshes'])-1)
        selected=found['meshes'][mesh];span=blocks(raw,(0,len(raw)),2)[1]
        node=blocks(raw,span)[selected['node']]
        part=blocks(raw,blocks(raw,node,3)[2])[selected['part']]
        material,poly,vertex_span=blocks(raw,blocks(raw,part)[selected['section']],3)
        at,count=stream(raw,vertex_span,16,4096)
        vertices=[]
        for i in range(count):
            vertices.append([int.from_bytes(bytes([raw[at+axis*2*count+i],raw[at+(axis*2+1)*count+i]]),'big',signed=True) for axis in range(3)])
        faces=polygons(raw,poly,count);edges=set();triangles=0
        for face in faces:
            if len(set(face))<3:continue
            triangles+=1;edges.update(tuple(sorted((face[i],face[(i+1)%3]))) for i in range(3))
        from . import resource_inspection
        inspection={}
        try:
            rows=resource_inspection.hierarchy(raw)
            inspection['table']=resource_inspection.table('Skeleton nodes',['index','parent','children','part_variants'],len(rows),lambda i:{k:v for k,v in rows[i].items() if k!='index'},selection)
        except resource_inspection.SchemaError as exc:inspection['inspection_warning']=str(exc)
        texture,flags=struct.unpack_from('>II',raw,material[0])
        inspection['material']=dict(texture_id=texture if texture!=0xffffffff else None,flags=flags,features=[name for bit,name in enumerate(resource_inspection.MATERIAL_FLAGS) if flags&(1<<bit)],color=list(raw[material[0]+8:material[0]+12]),black_color=list(raw[material[0]+12:material[0]+16]),uv_multiplier=list(struct.unpack_from('>HH',raw,material[0]+16)))
        return dict(media='model',vertices=vertices,**inspection,edges=sorted(edges),points=list(range(count)),triangles=triangles,
                    surfaces=[dict(indices=face,uv=[[0,0]]*3,colors=[[255,255,255,255]]*3,material=0) for face in faces if len(set(face))==3],materials=[],parts=[dict(name=f'Node {selected["node"]} / part {selected["part"]} / section {selected["section"]}',start=0,vertices=count)],
                    details={key:selected[key] for key in ('node','part','section','texture','material_flags')},
                    note='Validated native mesh in local coordinates. Skeleton poses, animation, textures and material shading are not applied.',
                    settings=dict(format='auto',source='rom',offset=asset['offset'],mesh=mesh))
    except ModelError as exc:raise AssetError(str(exc)+' Refresh ROM Explorer or select another region.') from None
