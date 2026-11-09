"""Local asset hypotheses: conservative grouping, texture comparisons and raw geometry.

These interpretations never alter the importer, symbols or the byte-exact judge.
"""
from __future__ import annotations

import base64
import bisect
import math
import struct

from . import asset_formats as formats
from .asset_formats import AssetError, integer


def groups(report):
    """Join display pages only where one symbol or transfer gives an explicit extent.

Overlapping hypotheses stay in the detailed loader evidence. The map chooses disjoint
extents, preferring imported symbols to named contracts and unknown-call guesses.
"""
    rows=report['regions'];starts=[r['start'] for r in rows];by_start={r['start']:i for i,r in enumerate(rows)}
    candidates={}
    for row in rows:
        for symbol in row['symbols']:
            mapping=next((m for m in row['mappings'] if m['segment']==symbol['segment']),None)
            if not mapping:continue
            start=row['start']+symbol['address']-mapping['address']
            end=start+symbol['size']
            key=(start,end)
            if key not in candidates:candidates[key]=dict(rank=0,label=symbol['name'],basis='Imported data symbol extent')
    for chain in report.get('loader_flow',{}).get('chains',[]):
        source=chain['source'];key=(source['start'],source['end'])
        rank=2 if chain['role']=='candidate' else 1
        if key not in candidates or rank<candidates[key]['rank']:
            candidates[key]=dict(rank=rank,label=chain['function'],basis='Tentative call argument extent' if rank==2 else 'Static loader source extent')
    result=[];occupied=set()
    for (start,end),clue in sorted(candidates.items(),key=lambda v:(v[1]['rank'],v[0])):
        first=by_start.get(start)
        if first is None:continue
        members=rows[first:bisect.bisect_left(starts,end)]
        if len(members)<2 or members[-1]['end']!=end or any(r['functions'] or r['kind']=='code' or r['id'] in occupied for r in members):continue
        kinds={r.get('annotation',{}).get('kind') or r['kind'] for r in members}
        kind=next(iter(kinds)) if len(kinds)==1 else 'data'
        identity=f'asset-{start:x}-{end:x}'
        result.append(dict(id=identity,start=start,end=end,size=end-start,label=clue['label'],basis=clue['basis'],
                           confidence='recorded extent' if clue['rank']==0 else 'tentative',kind=kind,
                           region_ids=[r['id'] for r in members],function_refs=sorted({f for r in members for f in r.get('function_refs',[])})))
        occupied.update(r['id'] for r in members)
    return sorted(result,key=lambda g:g['start'])


def usage(row,flow):
    """Summarise direct references and loader/pass/read evidence without claiming execution."""
    items={}
    def add(name,role,instruction,reason):
        item=items.setdefault(name,dict(function=name,roles=[],evidence=[]))
        if role not in item['roles']:item['roles'].append(role)
        evidence=dict(role=role,instruction=instruction,reason=reason)
        if evidence not in item['evidence']:item['evidence'].append(evidence)
    for ref in row['references']:add(ref['function'],'address reference',ref['instruction'],ref['reason'])
    for ref in (row.get('detected_asset') or {}).get('loader_refs',[]):
        add(ref['function'],ref.get('role','native asset loader argument'),ref['instruction'],
            ref.get('reason','Static named-loader argument; runtime use is not established.'))
    for chain in flow.get('chains',[]):
        add(chain['function'],'loader caller',chain['instruction'],chain['reason'])
        for step in chain['steps']:
            if step['kind']=='read':add(chain['function'],'buffer reader',step['instruction'],step.get('reason','Static buffer read'))
            if step.get('callee_known'):
                role={'copy':'copy helper','dma':'DMA helper','decompress':'decompressor','pass':'buffer consumer','candidate':'candidate helper'}.get(step['kind'],'buffer consumer')
                add(step['callee'],role,step['instruction'],step.get('reason',chain['reason']))
    return sorted(items.values(),key=lambda item:item['function'])


def textures(data,options):
    """Compare bounded common row-major interpretations; scores rank clues, not certainty."""
    offset=integer(options.get('offset',0),'Data offset',0,len(data))
    limit=min(len(data)-offset,65536)
    if limit<32:raise AssetError('Texture discovery needs at least 32 bytes at the selected offset. Choose an earlier offset or another source.')
    dimensions={(min(128,integer(options.get('width',32),'Width',1,2048)),min(128,integer(options.get('height',32),'Height',1,2048)))}
    dimensions.update((w,h) for w in (8,16,32,64) for h in (16,32,64) if .25<=w/h<=4)
    guesses=[];tried=0
    for fmt in ('rgba5551-be','rgb565-le','rgb565-be','rgba8888','ia8','i8','ci4','ci8'):
        for width,height in sorted(dimensions):
            size=((width*formats._bits(fmt)+7)//8)*height
            if size>limit:continue
            palette_formats=formats.PALETTES if fmt.startswith('ci') else (options.get('palette_format','rgba5551-be'),)
            for palette in palette_formats:
                selected={**options,'format':fmt,'width':width,'height':height,'palette_format':palette}
                tried+=1
                try:pixels,details=formats.texture_pixels(data,selected)
                except AssetError:continue
                stride=max(1,width*height//512)
                variation=[]
                for i in range(0,width*height-width,stride):
                    variation.append(sum(abs(pixels[4*i+c]-pixels[4*(i+width)+c]) for c in range(3))/765)
                diversity=len({pixels[i:i+4] for i in range(0,len(pixels),4*stride)})
                visible=sum(pixels[i]>0 for i in range(3,len(pixels),4*stride))/max(1,math.ceil(width*height/stride))
                score=round(100*(1-sum(variation)/max(1,len(variation)))*min(1,max(0,diversity-1)/15)*visible,1)
                guesses.append(dict(settings=selected,details=details,score=score))
    guesses.sort(key=lambda g:(-g['score'],g['settings']['format'],g['settings']['width'],g['settings']['height']))
    # Keep the comparison useful rather than filling it with near-identical dimensions.
    result=[];per_format={}
    for guess in guesses:
        fmt=guess['settings']['format']
        if per_format.get(fmt,0)>=2:continue
        png,_=formats.texture(data,guess['settings'])
        guess['data_url']='data:image/png;base64,'+base64.b64encode(png).decode()
        result.append(guess);per_format[fmt]=per_format.get(fmt,0)+1
        if len(result)==16:break
    return dict(candidates=result,tried=tried,note='Heuristic row continuity, colour diversity and visibility ranking; these are layout guesses, not detected textures. Indexed layouts try the selected palette offset in three formats. Only row-major data and up to 64 KiB are compared; tiled or swizzled textures need other layouts.')


MODEL_DEFAULTS=dict(vertex_format='s16be',stride=16,position_offset=0,count=32,index_format='none',index_offset=0,index_count=0,topology='points')
MODEL_KEYS=set(MODEL_DEFAULTS)


def model_settings(options):
    if not isinstance(options,dict) or set(options)-MODEL_KEYS:raise AssetError('Use only the listed vertex and index layout controls.')
    result=MODEL_DEFAULTS | options
    if result['vertex_format'] not in ('s16be','s16le','f32be','f32le'):raise AssetError('Choose signed 16-bit or float32 positions and their byte order.')
    if result['index_format'] not in ('none','u8','u16be','u16le','u32be','u32le'):raise AssetError('Choose a listed index format or sequential vertices.')
    if result['topology'] not in ('triangles','strip','lines','points'):raise AssetError('Choose triangles, a triangle strip, lines or points.')
    for key,lo,hi in [('stride',6,256),('position_offset',0,244),('count',1,4096),('index_offset',0,2**63-1),('index_count',0,24576)]:
        result[key]=integer(result[key],key.replace('_',' ').capitalize(),lo,hi)
    width=6 if result['vertex_format'].startswith('s16') else 12
    if result['position_offset']+width>result['stride']:raise AssetError('Position fields must fit inside the vertex stride. Increase the stride or reduce the position offset.')
    return result


def model(data,options,layout):
    """Read explicit raw vertex/index buffers; never interpret display-list commands as indices."""
    layout=model_settings(layout)
    offset=integer(options.get('offset',0),'Vertex data offset',0,len(data));count=layout['count']
    fmt=layout['vertex_format'];position=layout['position_offset'];stride=layout['stride'];width=6 if fmt.startswith('s16') else 12
    formats._take(data,offset,(count-1)*stride+position+width)
    code=('>' if fmt.endswith('be') else '<')+('hhh' if width==6 else 'fff')
    vertices=[struct.unpack_from(code,data,offset+i*stride+position) for i in range(count)]
    if any(not math.isfinite(v) or abs(v)>1e12 for point in vertices for v in point):raise AssetError('Positions include invalid or extreme values. Check the format, offset and stride.')
    index_format=layout['index_format']
    if index_format=='none':indices=list(range(count))
    else:
        n=layout['index_count']
        if not n:raise AssetError('Enter an index count when using an index buffer.')
        width={'u8':1,'u16be':2,'u16le':2,'u32be':4,'u32le':4}[index_format]
        raw=formats._take(data,layout['index_offset'],n*width)
        indices=[int.from_bytes(raw[i:i+width],'little' if index_format.endswith('le') else 'big') for i in range(0,len(raw),width)]
        if any(i>=count for i in indices):raise AssetError('An index exceeds the vertex count. Check index offset, format and vertex count.')
    topology=layout['topology'];edges=set();triangles=0
    if topology=='triangles' and len(indices)%3:raise AssetError('Triangle lists need a vertex or index count divisible by three. Use points or a strip to inspect an incomplete layout.')
    if topology=='lines' and len(indices)%2:raise AssetError('Line lists need an even vertex or index count.')
    faces=[indices[i:i+3] for i in range(0,len(indices),3)] if topology=='triangles' else [indices[i:i+3] for i in range(len(indices)-2)] if topology=='strip' else []
    if len(faces)>8192:raise AssetError('Choose at most 8,192 triangles for a wireframe preview.')
    for face in faces:
        if len(set(face))<3:continue
        triangles+=1
        edges.update(tuple(sorted((face[i],face[(i+1)%3]))) for i in range(3))
    if topology=='lines':edges.update(tuple(sorted(indices[i:i+2])) for i in range(0,len(indices),2))
    return dict(vertices=vertices,edges=sorted(edges),points=sorted(set(indices)),triangles=triangles,model_settings=layout,
                note='Manual geometry interpretation. A plausible wireframe does not prove this is a model. Strips use repeated indices for degenerates; primitive restart and display lists are not decoded.')
