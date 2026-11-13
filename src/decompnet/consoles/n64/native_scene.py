"""Resolve native meshes, textures and animations inside one checked directory."""
import re
import struct

from ...rom.asset_formats import AssetError, integer
from . import animation, models, resource_inspection, textures


def link(assets):
    animations={}
    for asset in assets:
        if asset.get('resource_type')=='animation':
            parent=asset['resource_path'].split('/animations/')[0]
            animations.setdefault((asset['directory_offset'],parent),[]).append(dict(offset=asset['offset'],label=asset['resource_path'].rsplit('/',1)[-1],frames=asset['resource_fields']['frames']))
    for asset in assets:
        if asset.get('media')=='model' and asset.get('resource_type')=='geometry':
            asset['native_animations']=animations.get((asset['directory_offset'],asset['resource_path'].removesuffix('/geometry')),[])


def raw(image,asset):
    if asset['size']>4*1024*1024:raise AssetError('Native resource exceeds the 4 MiB preview limit.')
    data=image[asset['offset']:asset['offset']+asset['size']]
    if asset['preview_settings'].get('source')=='decoded':return textures.decode(data)[0]
    return data


def preview(image,asset,selection,assets):
    if asset.get('font_layout')=='native-i4-pointer-glyphs':
        from . import fonts
        return fonts.preview(image,asset,selection)
    if asset.get('media')!='model' or asset.get('resource_type')!='geometry':return None
    data=raw(image,asset);found=models.layout(data);nodes=resource_inspection.hierarchy(data)
    options=asset.get('native_animations',[]);selected_animation=integer(selection.get('animation',0),'Animation',0,max(0,len(options)-1))
    assembled=selection.get('assembly','mesh')=='animated'
    if selection.get('assembly','mesh') not in ('mesh','animated'):raise AssetError('Choose a local mesh or an animated native assembly.')
    selected=integer(selection.get('mesh',0),'Mesh',0,len(found['meshes'])-1)
    meshes=found['meshes'] if assembled else [found['meshes'][selected]]
    if assembled:
        meshes=[m for m in meshes if nodes[m['node']]['part_variants'] and m['part']==nodes[m['node']]['part_variants'][0]]
    if len(meshes)>256 or sum(m['vertices'] for m in meshes)>32768 or sum(m['triangles'] for m in meshes)>65536:
        raise AssetError('Assembly exceeds the preview limit. Choose one local mesh instead.')
    available={a['offset']:a for a in assets};root=asset.get('directory_offset');banks={}
    for candidate in available.values():
        match=re.fullmatch(r'directory/textures/texture bank\[(\d+)\]',candidate.get('resource_path',''))
        if match and candidate.get('directory_offset')==root:banks[int(match[1])]=candidate
    result=dict(media='model',vertices=[],edges=[],surfaces=[],points=[],parts=[],materials=[],triangles=0)
    edges=set();budget=0;image_bytes=0;cache={}
    for mesh in meshes:
        node=models.blocks(data,models.blocks(data,(0,len(data)),2)[1])[mesh['node']]
        part=models.blocks(data,models.blocks(data,node,3)[2])[mesh['part']]
        material,poly,span=models.blocks(data,models.blocks(data,part)[mesh['section']],3)
        at,count=models.stream(data,span,16,4096);rows=[bytes(data[at+j*count+i] for j in range(16)) for i in range(count)]
        color=list(data[material[0]+8:material[0]+12]);flags=mesh['material_flags'];index=len(result['materials']);entry=dict(name=f'Texture {mesh["texture"]}',state='untextured',wrap='clamp' if flags&32 else 'mirror' if flags&2048 else 'repeat')
        width=height=1
        if mesh['texture'] in banks and not flags&16:
            texture=banks[mesh['texture']];entry['offset']=texture['offset']
            try:
                if texture['offset'] not in cache:
                    decoded=raw(image,texture);budget+=len(decoded)
                    if budget>16*1024*1024:raise AssetError('Texture preview budget of 16 MiB reached.')
                    cache[texture['offset']]=textures.preview(decoded if texture['preview_settings']['source']=='decoded' else image,texture,{})
                rendered=cache[texture['offset']];width=rendered['details']['width'];height=rendered['details']['height']
                image_bytes+=len(rendered['data_url'])
                if image_bytes>16*1024*1024:raise AssetError('Texture image response budget reached; choose one mesh for its texture.')
                entry.update(image=rendered['data_url'],state='linked')
            except AssetError as exc:entry.update(state='unsupported',error=str(exc))
        elif flags&16:entry.update(state='unsupported',error='Reflection coordinates require the runtime camera.')
        elif mesh['texture']!=0xffffffff:entry.update(state='unresolved',error='No validated texture bank matches this directory index.')
        entry.update(pseudo_color=bool(flags&1024),color=[v/255 for v in color],black_color=[v/255 for v in data[material[0]+12:material[0]+16]])
        result['materials'].append(entry);base=len(result['vertices']);uv=[];colors=[];multu,multv=struct.unpack_from('>HH',data,material[0]+16)
        for row in rows:
            result['vertices'].append(list(struct.unpack_from('>3h',row)))
            s,t=struct.unpack_from('>2h',row,8);uv.append([s*multu/(65536*32*width),t*multv/(65536*32*height)])
            # Ordinary textured combiners use TEXEL × SHADE, not primitive RGB.
            base_rgb=[255]*3 if mesh['texture']!=0xffffffff else color[:3]
            colors.append([round(base_rgb[k]*row[12+k]/255) if flags&64 and not flags&2 else base_rgb[k] for k in range(3)]+[round(color[3]*row[15]/255) if flags&4096 and flags&1024 else color[3]])
        for face in models.polygons(data,poly,count):
            if len(set(face))<3:continue
            ids=[base+i for i in face];result['surfaces'].append(dict(indices=ids,uv=[uv[i] for i in face],colors=[colors[i] for i in face],material=index))
            edges.update(tuple(sorted((ids[i],ids[(i+1)%3]))) for i in range(3))
        result['parts'].append(dict(name=f'Node {mesh["node"]} / part {mesh["part"]} / section {mesh["section"]}',node=mesh['node'],start=base,vertices=count))
    result.update(edges=sorted(edges),points=list(range(len(result['vertices']))),triangles=len(result['surfaces']),
                  details=dict(mesh='Animated assembly' if assembled else selected,texture_frame=0),
                  note='Native UVs and material colours; unlit preview with texture frame/palette 0. Runtime lighting, reflection coordinates and game-driven material effects are not reproduced.',
                  settings=dict(format='auto',source='rom',offset=asset['offset'],mesh=selected,assembly='animated' if assembled else 'mesh',animation=selected_animation))
    result['table']=resource_inspection.table('Skeleton nodes',['index','parent','children','part_variants'],len(nodes),lambda i:{k:v for k,v in nodes[i].items() if k!='index'},selection)
    result['material']=dict(texture_id=found['meshes'][selected]['texture'],flags=found['meshes'][selected]['material_flags'])
    if assembled:
        if not options:raise AssetError('This model has no linked animation. Choose a local mesh.')
        target=available.get(options[selected_animation]['offset'])
        if target is None:raise AssetError('The linked animation is missing. Rescan ROM Explorer.')
        decoded=animation.tracks(raw(image,target))
        if len(decoded['nodes'])!=len(nodes):raise AssetError('Animation and geometry node counts disagree. Choose another animation.')
        result['animation']={**decoded,'parents':[n['parent'] for n in nodes]}
        result['note']+=' First part variant per node. Playback uses a user-selected preview rate; runtime blending, root-motion removal and gameplay overrides are not applied.'
    return result
