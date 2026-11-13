"""CRC-checked PD3V archive members with indexed local-coordinate meshes.

Self-contained meshes and split scenery use checked coordinate streams. Material
links come from loader scripts; no scripts are executed or extracted to paths.
"""
from __future__ import annotations

import math
import struct

from ...rom import asset_archives
from ...rom.asset_formats import AssetError, integer
from . import model_links, texture_packs

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
        faces=[];records=[];cursor=poly_at;maximum=-1
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
                uv=struct.unpack_from('>'+str(n*2)+'B',data,start+i*width+index+n*2)
                colors=[list(data[start+i*width+j*4:start+i*width+j*4+3])+[255] for j in range(n)]
                records.append(dict(indices=face,uv=[[uv[j*2]/128,uv[j*2+1]/128] for j in range(n)],colors=colors,material=material))
            cursor=end
        nvertices=maximum+1
        if not 3<=nvertices<=4096 or (nvertices*6+3)//4*4!=poly_at-vertex_at:return None
        if any(data[vertex_at+nvertices*6:poly_at]) or (cursor+3)//4*4!=table or any(data[cursor:table]):return None
        if sum(m['vertices'] for m in meshes)+nvertices>65536 or sum(m['triangles'] for m in meshes)+len(faces)*2>131072:return None
        spans.append((vertex_at,table+groups*8))
        matrix=struct.unpack_from('>16f',data,at+12)
        transform=[v/4096 if i<12 else v for i,v in enumerate(matrix)] if all(math.isfinite(v) and abs(v)<1e9 for v in matrix) and matrix[15]==1 else None
        meshes.append(dict(transform=transform,records=records,node=node,part=0,section=0,name=data[at:at+12].split(b'\0')[0].decode('ascii','replace'),
                           vertex_offset=vertex_at,vertices=nvertices,triangles=sum(len(f)-2 for f in faces),faces=faces))
    # No unexplained payload or overlapping mesh records is accepted.
    cursor=0
    for start,end in sorted(spans):
        if start!=cursor:return None
        cursor=end
    if cursor!=len(data):return None
    return dict(materials=[data[materials+i*20:materials+i*20+12].split(b'\0')[0].decode('ascii','replace') for i in range(nmaterials)],nodes=nnodes,meshes=meshes,vertices=sum(m['vertices'] for m in meshes),triangles=sum(m['triangles'] for m in meshes))


def regions(image, *, check=lambda:None, progress=lambda _:None):
    members=asset_archives.zip_members(image,check=check);names,links=model_links.index(image,members,check);expanded=0;result=[]
    for index,member in enumerate(members):
        check()
        if index%64==0:progress(dict(phase='Recognizing archived models',done=index,total=len(members)))
        try:
            if asset_archives.member_bytes(image,member,prefix=True)[:4]!=b'PD3V':continue
            if expanded+member['expanded']>MAX_EXPANDED:break
            expanded+=member['expanded'];raw=asset_archives.member_bytes(image,member)
        except AssetError:continue
        found=layout(raw)
        records=links.get(member['offset'],[])
        if not found:
            stream_bytes=sum(c['expanded'] for r in records for c in r['companions'])
            if expanded+stream_bytes>MAX_EXPANDED:continue
            expanded+=stream_bytes
            from . import model_scenery
            found=model_scenery.from_links(image,raw,records)
        if not found:continue
        found['materials']=model_links.resolve(found['materials'],names,records)
        found['relationships']=[dict(role='loader script',basis='Explicit '+r['command']+' command',line=r['line'],archive=r['script']) for r in records]
        for r in records:
            found['relationships'].extend(dict(role='coordinate stream',basis='Explicit LOADV3D command',archive=c) for c in r['companions'])
        found['assembled']=all(m.get('transform') is not None for m in found['meshes'])
        # Faces are re-read on preview; keep the saved report compact.
        for mesh in found['meshes']:
            for key in ('faces','records','positions'):mesh.pop(key,None)
        result.append(dict(offset=member['offset'],size=member['size'],kind='3d model',media='model',label=member['name'],confidence='validated',
                           layout='archived-pd3v-model',archive=member,preview_settings=dict(format='auto',source='rom',offset=member['offset']),
                           reason='ZIP CRCs, coordinate extents, material indices and triangle/quad references passed validation. Texture links use explicit loader commands; runtime execution is not established. Special strip commands are omitted.',**found))
    return result


def preview(image,asset,selection):
    raw=asset_archives.member_bytes(image,asset['archive'])
    if asset.get('scenery'):
        from . import model_scenery
        streams=[asset_archives.member_bytes(image,m) for m in asset['streams']]
        found=model_scenery.layout(raw,*streams)
    else:found=layout(raw)
    if not found:raise AssetError('The archived model layout changed. Rescan ROM Explorer.')
    mode=selection.get('assembly','mesh')
    if mode not in ('mesh','assembled'):raise AssetError('Choose one mesh or the assembled model.')
    mesh=integer(selection.get('mesh',0),'Mesh',0,len(found['meshes'])-1)
    selected=found['meshes'] if mode=='assembled' else [found['meshes'][mesh]]
    if mode=='assembled' and any(m.get('transform') is None for m in selected):raise AssetError('This model has no validated stored assembly. Choose an individual mesh.')
    vertices=[];edges=set();surfaces=[];parts=[]
    for m in selected:
        base=len(vertices)
        local=m.get('positions') or [list(struct.unpack_from('>hhh',raw,m['vertex_offset']+i*6)) for i in range(m['vertices'])]
        if mode=='assembled':
            t=m['transform'];local=[[sum(v[k]*t[k*4+axis] for k in range(3))+t[12+axis] for axis in range(3)] for v in local]
        vertices.extend(local);parts.append(dict(name=m['name'],start=base,vertices=len(local)))
        for rec in m['records']:
            face=rec['indices']
            for i in range(len(face)):
                a,b=base+face[i],base+face[(i+1)%len(face)]
                if a!=b:edges.add(tuple(sorted((a,b))))
            for i in range(1,len(face)-1):
                corners=(0,i,i+1)
                surfaces.append(dict(indices=[base+face[c] for c in corners],uv=[rec['uv'][c] for c in corners],colors=[rec['colors'][c] for c in corners],material=rec['material']))
    materials=[];budget=0;used={f['material'] for f in surfaces}
    for entry in asset.get('materials',[]):
        material=dict(name=entry['name'],state=entry['state'],offset=(entry.get('archive') or {}).get('offset'))
        if entry.get('archive') and len(materials) in used:
            try:
                budget+=entry['archive']['expanded']
                if budget>16*1024*1024:raise AssetError('Texture preview budget reached.')
                material['image']=texture_packs.preview(image,dict(archive=entry['archive'],offset=entry['archive']['offset'],reason='Explicit loader-script material link'),{})['data_url']
            except AssetError as exc:material.update(state='unsupported',error=str(exc))
        materials.append(material)
    return dict(media='model',up_axis='-Y',vertices=vertices,edges=sorted(edges),points=list(range(len(vertices))),triangles=len(surfaces),surfaces=surfaces,materials=materials,parts=parts,
                details=dict(mesh='Assembled' if mode=='assembled' else selected[0]['name'],vertices=len(vertices),polygons=sum(len(m['records']) for m in selected)),
                relationships=asset.get('relationships',[]),omitted_commands=found.get('omitted_commands',0),
                note=('Stored assembly.' if mode=='assembled' else 'Local mesh.')+ ' Textures follow explicit loader-script links; first frame/palette is shown. Runtime lighting, animation and special strip commands are not reproduced.',
                decoded=dict(codec='ZIP deflate' if asset['archive']['method']==8 else 'ZIP stored',bytes=len(raw),member=asset['archive']['name']),
                settings=dict(format='auto',source='rom',offset=asset['offset'],mesh=mesh,assembly=mode))
