"""Bounded source-layout records. Coordinates and IDs never imply runtime use."""
import math
import struct

from ...rom.asset_formats import integer
from .resources import SchemaError, blocks, stream

PAGE=64
MATERIAL_FLAGS=('flat shaded','self illuminated','two sided','texture map','reflection map','decal texture','prelit','mask','transparency','intersection','pseudo color','mirror texture','shade alpha')


def records(data,span,width,*,planes=False):
    info=stream(data,span,width);count=info['records'];at=span[0]+8
    def read(i):
        if planes:return bytes(data[at+j*count+i] for j in range(width))
        return data[at+i*width:at+(i+1)*width]
    return count,read


def finite(values):
    if not all(math.isfinite(v) for v in values):raise SchemaError('Non-finite coordinates in resource.')
    return [round(v,6) for v in values]


def table(title,columns,total,read,selection):
    page=integer(selection.get('record_page',0),'Record page',0,max(0,(total-1)//PAGE))
    start=page*PAGE
    return dict(title=title,columns=columns,rows=[dict(index=i,**read(i)) for i in range(start,min(total,start+PAGE))],total=total,page=page,page_size=PAGE)


def inspect(data,role,selection):
    result={}
    if role=='object placements':
        total,read=records(data,(0,len(data)),36,planes=True)
        def placement(i):
            raw=read(i);obj,flags,variation,region,rotation=struct.unpack_from('>4Hh',raw)
            return dict(object_id=obj,type_flags=flags,variation=variation,region=region,rotation_y=rotation,
                        flags=raw[10],active_flags=raw[11],position=finite(struct.unpack_from('>3f',raw,12)),scale=finite(struct.unpack_from('>3f',raw,24)))
        result['table']=table('Object placements',['index','object_id','variation','region','position','scale','rotation_y','flags','active_flags'],total,placement,selection)
    elif role=='collision':
        attrs,corners,regions,bounds=blocks(data,(0,len(data)),4)
        na,attribute=records(data,attrs,72,planes=True);nc,corner=records(data,corners,16,planes=True);nr,region=records(data,regions,16,planes=True)
        def read(i):
            attr,flags,*indices=struct.unpack('>8H',region(i))
            if attr>=na or any(v>=nc for v in indices[:3]) or any(v!=65535 and v>=nr for v in indices[3:]):raise SchemaError('Collision references an absent corner, attribute or neighbour.')
            raw=attribute(attr)
            return dict(attributes=attr,flags=flags,corners=indices[:3],neighbours=[None if v==65535 else v for v in indices[3:]],surface_flags=struct.unpack_from('>I',raw,52)[0],ground_material=raw[66],wall_material=raw[67],warp_id=struct.unpack_from('>H',raw,56)[0])
        result['table']=table('Collision surfaces',['index','attributes','flags','corners','neighbours','surface_flags','ground_material','wall_material','warp_id'],nr,read,selection)
        # Source encodes signed 32-bit positions with 20-bit precision and a
        # 1500-unit range, then scales into engine coordinates by 1024/100.
        vertices=[];edges=set();lookup={}
        for row in result['table']['rows']:
            ids=[]
            for index in row['corners']:
                if index not in lookup:
                    lookup[index]=len(vertices);vertices.append([v*(1500*4096*10.24)/2147483648 for v in struct.unpack('>4i',corner(index))[:3]])
                ids.append(lookup[index])
            edges.update(tuple(sorted((ids[j],ids[(j+1)%3]))) for j in range(3))
        if vertices:result['geometry']=dict(vertices=vertices,edges=sorted(edges),points=list(range(len(vertices))),triangles=len(result['table']['rows']),note='Current page of collision surfaces in engine coordinates; flags and neighbour IDs are shown below.')
    elif role=='animation':
        parts=blocks(data,(0,len(data)),7);nn,node=records(data,parts[1],4);ni,orient=records(data,parts[2],20)
        nf=stream(data,parts[0],2)['records'];translations=stream(data,parts[3]);rotations=stream(data,parts[4])
        def read(i):
            t,r=struct.unpack('>hh',node(i));row=dict(translation_set=t,rotation_set=r)
            if i<ni:
                raw=orient(i);row.update(initial_position=finite(struct.unpack_from('>3f',raw)),initial_rotation=[round(v/32767,6) for v in struct.unpack_from('>4h',raw,12)])
            else:row.update(initial_position=None,initial_rotation=None)
            row['translation_encoding']='initial orientation' if t==-1 else 'ADPCM or raw track'
            if t>=0:
                width=translations['record_bytes'];at=parts[3][0]+8+t*width
                if width<20:raise SchemaError('Translation track header is truncated.')
                compressed=struct.unpack_from('>I',data,at)[0]
                if compressed not in (0,1):raise SchemaError('Unknown translation track encoding.')
                row['translation_encoding']='ADPCM' if compressed else 'float XYZ'
                if not compressed:
                    if width<20+nf*12:raise SchemaError('Translation frame data is truncated.')
                    frame=integer(selection.get('frame',0),'Animation frame',0,nf-1)
                    row['frame_position']=finite(struct.unpack_from('>3f',data,at+20+frame*12))
            row['rotation_encoding']='initial orientation' if r==-1 else f'ADPCM ({rotations["record_bytes"]} bytes)'
            return row
        result['table']=table('Animation nodes and tracks',['index','translation_set','rotation_set','initial_position','initial_rotation','translation_encoding','rotation_encoding','frame_position'],nn,read,selection)
    elif role=='object metadata':
        bounds,types=blocks(data,(0,len(data)),2)
        if bounds[1]-bounds[0]!=24:raise SchemaError('Unsupported object bounds extent.')
        minimum=finite(struct.unpack_from('>3f',data,bounds[0]));maximum=finite(struct.unpack_from('>3f',data,bounds[0]+12))
        if any(a>b for a,b in zip(minimum,maximum)):raise SchemaError('Object bounds are inverted.')
        total,read=records(data,types,4)
        result['object_bounds']=dict(minimum=minimum,maximum=maximum)
        result['table']=table('Object animation types',['index','animation_type'],total,lambda i:dict(animation_type=int.from_bytes(read(i),'big')),selection)
    elif role=='grid section':
        parts=blocks(data,(0,len(data)))
        if not parts:return result
        component=integer(selection.get('record_component',0),'Placement component',0,2)
        width=(24,48,28)[component];total,read=records(data,parts[component],width,planes=True)
        def instance(i):
            raw=read(i)
            if component==0:
                scale=finite(struct.unpack_from('>f',raw))[0];obj,region,variation,flags,y=struct.unpack_from('>4Hh',raw,4)
                return dict(object_id=obj,region=region,variation=variation,flags=flags,scale=scale,quantized_x=raw[16],quantized_y=y,quantized_z=raw[17],position_flags=raw[14])
            if component==1:
                obj,region,variation=struct.unpack_from('>3H',raw,36)
                return dict(object_id=obj,region=region,variation=variation,flags=raw[42],position=finite(struct.unpack_from('>3f',raw)),scale=finite(struct.unpack_from('>3f',raw,12)))
            obj,region,variation,flags,identity=struct.unpack_from('>5H',raw,16)
            return dict(object_id=obj,region=region,variation=variation,flags=flags,position=finite(struct.unpack_from('>3f',raw)),radius=finite(struct.unpack_from('>f',raw,12))[0],instance_id=identity,rotation_y=struct.unpack_from('>b',raw,26)[0])
        columns=['index','object_id','region','variation','flags']+(['quantized_x','quantized_y','quantized_z','position_flags','scale'] if component==0 else ['position','scale'] if component==1 else ['position','radius','instance_id','rotation_y'])
        result['table']=table(('Extended static instances','Static instances','Simple instances')[component],columns,total,instance,selection)
        if component==0:result['inspection_warning']='Extended positions need the containing grid origin and quantization flags; these are stored components, not world coordinates.'
    elif role=='grid bounds':
        parts=blocks(data,(0,len(data)))
        result['table']=table('Component streams',['index','record_bytes','records'],len(parts),lambda i:stream(data,parts[i]),selection)
    elif role=='geometry':
        rows=hierarchy(data)
        result['table']=table('Skeleton nodes',['index','parent','children','part_variants'],len(rows),lambda i:{k:v for k,v in rows[i].items() if k!='index'},selection)
    return result


def hierarchy(data):
    """Expose child relationships and alternative part mappings without assembling them."""
    nodes=blocks(data,blocks(data,(0,len(data)),2)[1]);rows=[];parents={}
    for i,node in enumerate(nodes):
        children,indices,parts=blocks(data,node,3)
        nc,child=records(data,children,4);np,part=records(data,indices,4)
        ids=[int.from_bytes(child(j),'big') for j in range(nc)]
        if len(set(ids))!=len(ids) or any(j>=len(nodes) or j==i or j in parents for j in ids):raise SchemaError('Skeleton is not a single-parent hierarchy.')
        parents.update({j:i for j in ids})
        rows.append(dict(index=i,children=ids,part_variants=[int.from_bytes(part(j),'big') for j in range(np)]))
    visited=set()
    for i in range(len(nodes)):
        seen=set();j=i
        while j in parents and j not in visited:
            if j in seen:raise SchemaError('Skeleton contains a cycle.')
            seen.add(j);j=parents[j]
        visited.update(seen)
    for row in rows:row['parent']=parents.get(row['index'])
    return rows
