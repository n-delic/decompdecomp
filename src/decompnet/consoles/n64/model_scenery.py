"""Split scenery: explicit model, XZ/command and Y streams from loader scripts.

Triangle/quad commands are source-assisted. Type 35 dynamic strip records are
bounds checked but omitted until their rendering semantics are established.
"""
from __future__ import annotations

import struct

from ...rom import asset_archives
from ...rom.asset_formats import AssetError

IDENTITY=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]


def layout(data,xz,y):
    if len(data)<116 or data[:4]!=b'PD3V' or struct.unpack_from('>I',data,32)[0]!=10:return None
    roots=[struct.unpack_from('>IHH',data,36+i*8) for i in range(10)]
    if [r[2] for r in roots]!=[0,14,10,11,9,12,13,4,5,6]:return None
    material_at,nmaterials,_=roots[0]
    if material_at!=116 or not 0<nmaterials<=256 or material_at+nmaterials*20>len(data):return None
    models={};meshes=[];seen=set();vertices_total=0;triangles_total=0;command_total=0
    for root,instance in ((roots[2],False),(roots[3],True)):
        start,count,_=root
        if not 0<count<=2048 or start+count*8>len(xz):return None
        for i in range(count):
            at,n,_=struct.unpack_from('>IHH',xz,start+i*8);width=36 if instance else 24
            if n>256 or at+n*width>len(xz):return None
            for j in range(n):
                row=at+j*width
                if not instance:
                    if row in models:continue
                    if len(models)>=4096:return None
                    position=struct.unpack_from('>I',xz,row+12)[0];groups=struct.unpack_from('>H',xz,row+16)[0];table=struct.unpack_from('>I',xz,row+20)[0]
                    if groups>32:return None
                    if not groups or table==0xffffffff:models[row]=None;continue
                    if table+groups*8>len(xz):return None
                    records=[];max_index=-1;first=len(xz);skipped=0
                    for g in range(groups):
                        p,total,typ=struct.unpack_from('>IHH',xz,table+g*8)
                        command_total+=total
                        if typ not in (26,30,35) or total>8192 or command_total>262144:return None
                        stride=14 if typ==26 else 18 if typ==30 else 8
                        if p+total*stride>len(xz):return None
                        first=min(first,p)
                        for k in range(total):
                            addr=p+k*stride
                            if typ==35:
                                max_index=max(max_index,struct.unpack_from('>H',xz,addr)[0]);skipped+=1;continue
                            corners=3 if typ==26 else 4
                            indices=struct.unpack_from('>'+str(corners)+'H',xz,addr)
                            uv=xz[addr+corners*2:addr+corners*4];material=struct.unpack_from('>H',xz,addr+stride-2)[0]
                            if material>=nmaterials:return None
                            max_index=max(max_index,*indices)
                            records.append(dict(indices=indices,uv=[[uv[k*2]/128,uv[k*2+1]/128] for k in range(corners)],material=material))
                    count_vertices=max_index+1
                    if not 0<count_vertices<=4096 or position+count_vertices*4>first:return None
                    models[row]=dict(vertex_offset=position,vertices=count_vertices,records=records,skipped=skipped)
                else:
                    model=struct.unpack_from('>I',xz,row+24)[0]
                    if model not in models:return None
                    info=models[model]
                    if not info or not info['records']:continue
                    height,shade=struct.unpack_from('>II',xz,row+16);key=(model,height,shade)
                    if key in seen:continue
                    seen.add(key);nv=info['vertices']
                    if height+nv*2>len(y) or shade+nv>len(xz):return None
                    triangles=sum(len(r['indices'])-2 for r in info['records'])
                    if len(meshes)>=4096 or vertices_total+nv>262144 or triangles_total+triangles>262144:return None
                    positions=[]
                    for v in range(nv):
                        vx,vz=struct.unpack_from('>hh',xz,info['vertex_offset']+v*4);vy=struct.unpack_from('>h',y,height+v*2)[0]
                        positions.append([vx,vy,vz])
                    records=[{**r,'colors':[[xz[shade+idx]]*3+[255] for idx in r['indices']]} for r in info['records']]
                    triangles=sum(len(r['indices'])-2 for r in records);vertices_total+=nv;triangles_total+=triangles
                    if len(meshes)>=4096 or vertices_total>262144 or triangles_total>262144:return None
                    meshes.append(dict(name=xz[row:row+8].split(b'\0')[0].decode('ascii','replace')+f' · section {i+1}',node=i,part=j,section=0,
                                       positions=positions,vertices=nv,triangles=triangles,records=records,transform=IDENTITY))
    if not meshes:return None
    return dict(scenery=True,omitted_commands=sum(m['skipped'] for m in models.values() if m),nodes=len(meshes),meshes=meshes,vertices=vertices_total,triangles=triangles_total,
                materials=[data[material_at+i*20:material_at+i*20+12].split(b'\0')[0].decode('ascii','replace') for i in range(nmaterials)])


def from_links(image,data,records):
    candidates={tuple(c['offset'] for c in r['companions']):r['companions'] for r in records if len(r['companions'])==2}
    if len(candidates)!=1:return None
    streams=next(iter(candidates.values()))
    try:found=layout(data,*(asset_archives.member_bytes(image,m) for m in streams))
    except AssetError:return None
    if found:found['streams']=streams
    return found
