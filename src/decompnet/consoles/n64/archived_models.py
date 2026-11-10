"""CRC-checked PD3V archive members with indexed local-coordinate meshes.

Only the self-contained variant is rendered. The split scenery variant has
external coordinate streams whose interpretation still needs validation.
"""
from __future__ import annotations

import struct

from ...rom import asset_archives
from ...rom.asset_formats import AssetError, integer

MAX_EXPANDED=64*1024*1024


def layout(data):
    """Require complete tables, aligned coordinate spans and every face reference."""
    if len(data)<116 or data[:4]!=b'PD3V':return None
    count=struct.unpack_from('>I',data,32)[0]
    if count!=2:return None
    materials,nmaterials,kind=struct.unpack_from('>IHH',data,36)
    nodes,nnodes,nodekind=struct.unpack_from('>IHH',data,44)
    if kind!=0 or nodekind!=8 or not 0<nmaterials<=256 or not 0<nnodes<=256:return None
    if materials!=116 or materials+nmaterials*20>nodes or nodes+nnodes*108!=len(data):return None
    meshes=[];spans=[(0,materials+nmaterials*20),(nodes,len(data))]
    for node in range(nnodes):
        at=nodes+node*108
        vertex_at,poly_at=struct.unpack_from('>II',data,at+80)
        groups=struct.unpack_from('>H',data,at+100)[0];table=struct.unpack_from('>I',data,at+104)[0]
        if not 0<groups<=32 or not materials+nmaterials*20<=vertex_at<poly_at<=table or table+groups*8>nodes:return None
        faces=[];cursor=poly_at;maximum=-1
        for group in range(groups):
            start,total,typ=struct.unpack_from('>IHH',data,table+group*8)
            aligned=(cursor+3)//4*4
            if typ not in (6,10) or not total or start!=aligned or any(data[cursor:aligned]):return None
            width,index,n=(26,12,3) if typ==6 else (34,16,4)
            end=start+total*width
            if end>table or len(faces)+total>8192:return None
            for i in range(total):
                face=struct.unpack_from('>'+str(n)+'H',data,start+i*width+index)
                material=struct.unpack_from('>H',data,start+(i+1)*width-2)[0]
                if material>=nmaterials:return None
                maximum=max(maximum,*face);faces.append(face)
            cursor=end
        nvertices=maximum+1
        if not 3<=nvertices<=4096 or (nvertices*6+3)//4*4!=poly_at-vertex_at:return None
        if any(data[vertex_at+nvertices*6:poly_at]) or (cursor+3)//4*4!=table or any(data[cursor:table]):return None
        if sum(m['vertices'] for m in meshes)+nvertices>65536 or sum(m['triangles'] for m in meshes)+len(faces)*2>131072:return None
        spans.append((vertex_at,table+groups*8))
        meshes.append(dict(node=node,part=0,section=0,name=data[at:at+12].split(b'\0')[0].decode('ascii','replace'),
                           vertex_offset=vertex_at,vertices=nvertices,triangles=sum(len(f)-2 for f in faces),faces=faces))
    # No unexplained payload or overlapping mesh records is accepted.
    cursor=0
    for start,end in sorted(spans):
        if start!=cursor:return None
        cursor=end
    if cursor!=len(data):return None
    return dict(nodes=nnodes,meshes=meshes,vertices=sum(m['vertices'] for m in meshes),triangles=sum(m['triangles'] for m in meshes))


def regions(image, *, check=lambda:None, progress=lambda _:None):
    members=asset_archives.zip_members(image,check=check);expanded=0;result=[]
    for index,member in enumerate(members):
        check()
        if index%64==0:progress(dict(phase='Recognizing archived models',done=index,total=len(members)))
        try:
            if asset_archives.member_bytes(image,member,prefix=True)[:4]!=b'PD3V':continue
            if expanded+member['expanded']>MAX_EXPANDED:break
            expanded+=member['expanded'];raw=asset_archives.member_bytes(image,member)
        except AssetError:continue
        found=layout(raw)
        if not found:continue
        # Faces are re-read on preview; keep the saved report compact.
        for mesh in found['meshes']:mesh.pop('faces')
        result.append(dict(offset=member['offset'],size=member['size'],kind='3d model',media='model',label=member['name'],confidence='validated',
                           layout='archived-pd3v-model',archive=member,preview_settings=dict(format='auto',source='rom',offset=member['offset']),
                           reason='ZIP CRC, complete mesh extents, material indices and every triangle/quad reference passed validation. Meshes use local coordinates; textures, poses and runtime use are not established.',**found))
    return result


def preview(image,asset,selection):
    raw=asset_archives.member_bytes(image,asset['archive']);found=layout(raw)
    if not found:raise AssetError('The archived model layout changed. Rescan ROM Explorer.')
    mesh=integer(selection.get('mesh',0),'Mesh',0,len(found['meshes'])-1);selected=found['meshes'][mesh]
    vertices=[list(struct.unpack_from('>hhh',raw,selected['vertex_offset']+i*6)) for i in range(selected['vertices'])]
    edges=set()
    for face in selected['faces']:
        for i in range(len(face)):
            a,b=face[i],face[(i+1)%len(face)]
            if a!=b:edges.add(tuple(sorted((a,b))))
    return dict(media='model',vertices=vertices,edges=sorted(edges),points=list(range(len(vertices))),triangles=selected['triangles'],
                details=dict(mesh=selected['name'],vertices=len(vertices),polygons=len(selected['faces'])),
                note='Validated local mesh. Drag to rotate. Textures, object transforms and animation are not applied.',
                decoded=dict(codec='ZIP deflate' if asset['archive']['method']==8 else 'ZIP stored',bytes=len(raw),member=asset['archive']['name']),
                settings=dict(format='auto',source='rom',offset=asset['offset'],mesh=mesh))
