"""ROM ranges, copy/decompress calls and later buffer uses from processor evidence.

These are inspectable static chains, not runtime traces. No discovered range changes
an import mapping, an original byte or the matching judge. Names select documented
call contracts; unknown helpers remain transfer candidates even when arguments fit.
"""
from __future__ import annotations

import bisect
from itertools import permutations

COMMON_CALLS = {
    'memcpy':dict(role='copy',source=1,destination=0,length=2,domain='address'),
    'memmove':dict(role='copy',source=1,destination=0,length=2,domain='address'),
    'bcopy':dict(role='copy',source=0,destination=1,length=2,domain='address'),
    'uncompress':dict(role='decompress',source=2,destination=0,length=3,domain='address'),
    'uncompress2':dict(role='decompress',source=2,destination=0,domain='address'),
}
MAX_CHAINS = 4096
MAX_STEPS = 24
MAX_TRANSFER = 32 * 1024 * 1024


def _number(value):
    return value['value'] if value and value['kind']=='constant' else None


def _same(a,b):
    if a is None or b is None:return False
    return {k:v for k,v in a.items() if k!='evidence'}=={k:v for k,v in b.items() if k!='evidence'}


def _inside(value,base,size):
    if _same(value,base):return True
    a,b=_number(value),_number(base)
    if a is not None and b is not None:return size is not None and b<=a<b+size
    if value is None or base is None:return False
    return value['kind']==base['kind'] and value['value']==base['value'] and size is not None and base.get('offset',0)<=value.get('offset',0)<base.get('offset',0)+size


def _contract_value(call,contract,key):
    value=call['args'][contract[key]]
    field=contract.get('fields',{}).get(key)
    if field is None:return value
    if value is None:return None
    place={**value,'offset':value.get('offset',0)+field}
    if place['kind']=='constant':place.update(value=(place['value']+field)&0xffffffff,offset=0)
    matches=[entry['value'] for entry in call.get('memory',[]) if _same(entry['place'],place)]
    return matches[0] if len(matches)==1 else None


def trace(image,segments,functions,adapter,*,contracts=None,argument_count=4,big_endian=True,gp=0,cpu='',ram=(0,0),check=lambda:None,progress=lambda _:None):
    """Keep call-site provenance and whole transfer extents before display partitioning.

    Source mapping candidates are retained per overlay. A same-buffer argument at
    a later call is a pass, not proof that the callee consumes or transforms it.
    """
    if adapter is None:return dict(chains=[],limited=False,supported=False,note='This processor needs a call-argument adapter.')
    contracts=COMMON_CALLS | (contracts or {})
    by_address={}
    for f in functions:by_address.setdefault(f['vram'],[]).append(f)
    wrappers={}
    def wrapper_contract(targets,depth=0):
        if len(targets)!=1 or depth>2:return None
        target=targets[0];name=target['name'];identity=(name,target.get('rom_offset'))
        if identity in wrappers:return wrappers[identity]
        wrappers[identity]=None
        offset=target.get('rom_offset')
        if offset is None or target['size']>1024 or not 0<=offset<offset+target['size']<=len(image):return None
        facts=adapter(target['vram'],image[offset:offset+target['size']],big_endian=big_endian,
                      argument_count=argument_count,gp=gp,cpu=cpu,check=check)
        if facts.get('branched') or facts['limited'] or any(e['kind']=='store' and (e['place'] is None or e['place']['kind']!='stack') for e in facts['events']):return None
        matches=[]
        for call in (event for event in facts['events'] if event['kind']=='call'):
            nested=by_address.get(call['target'],[])
            owned=[f for f in nested if f.get('unit')==target.get('unit')];nested=owned or nested
            if len(nested)!=1:continue
            inner=contracts.get(nested[0]['name']) or wrapper_contract(nested,depth+1)
            if inner and inner['role']!='opaque':matches.append((call,nested,inner))
        if len(matches)!=1:return None
        call,nested,inner=matches[0]
        result={k:v for k,v in inner.items() if k not in ('source','destination','length','direction','fields')}
        for key in ('source','destination','length'):
            if key not in inner:continue
            value=_contract_value(call,inner,key)
            if not value or value['kind']!='argument' or value.get('offset'):return None
            result[key]=value['value']
        if 'direction' in inner:
            value=call['args'][inner['direction']]
            if _number(value)==0:pass
            elif value and value['kind']=='argument' and not value.get('offset'):result['direction']=value['value']
            else:return None
        result.update(basis='wrapper',via=nested[0]['name'],wrapper_instruction=call['instruction'])
        wrappers[identity]=result
        return result
    chains=[];limited=False
    for n,fn in enumerate(functions):
        check();offset=fn.get('rom_offset')
        if offset is None or not 0<=offset<offset+fn['size']<=len(image):continue
        facts=adapter(fn['vram'],image[offset:offset+fn['size']],big_endian=big_endian,
                      argument_count=argument_count,gp=gp,cpu=cpu,check=check)
        limited |= facts['limited']
        local=[s['name'] for s in segments if s['rom_start']<=offset<s['rom_end']]
        active=[]
        def mapped(address,local=local):
            matches=[dict(start=s['rom_start']+address-s['vram'],segment=s['name'],limit=s['rom_end']) for s in segments
                     if s['vram']<=address<s['vram']+s['rom_end']-s['rom_start']]
            owned=[m for m in matches if m['segment'] in local]
            return owned or matches
        for event in facts['events']:
            check()
            active=[chain for chain in active if chain['block']==event['block']]
            if event['kind'] in ('load','store'):
                for chain in list(active):
                    inside=_inside(event['place'],chain['buffer'],chain.get('buffer_size'))
                    if event['kind']=='load' and inside and len(chain['steps'])<MAX_STEPS:
                        chain['steps'].append(dict(kind='read',instruction=event['instruction'],width=event['width'],buffer=event['place'],reason='Original instruction loads from this buffer address.'))
                    elif event['kind']=='store':
                        a,b=_number(event['place']),_number(chain['buffer']);size=chain.get('buffer_size')
                        disjoint=a is not None and b is not None and size is not None and (a+event['width']<=b or a>=b+size)
                        if not disjoint and event['place'] and chain['buffer'] and event['place']['kind']==chain['buffer']['kind'] and event['place']['value']==chain['buffer']['value'] and size is not None:
                            a,b=event['place'].get('offset',0),chain['buffer'].get('offset',0)
                            disjoint=a+event['width']<=b or a>=b+size
                        if not disjoint:
                            if len(chain['steps'])<MAX_STEPS:chain['steps'].append(dict(kind='overwrite',instruction=event['instruction'],reason='A store may overwrite or alias this buffer; later links are not followed.'))
                            active.remove(chain)
                continue
            if event['kind']!='call':continue
            args=event['args'];candidates=by_address.get(event['target'],[])
            owned=[f for f in candidates if f.get('unit')==fn.get('unit')]
            targets=owned or candidates
            callee=targets[0]['name'] if len(targets)==1 else (f"0x{event['target']:08X}" if event['target'] is not None else 'indirect call')
            contract=contracts.get(callee) or contracts.get(callee.lstrip('_')) or wrapper_contract(targets)
            for chain in active:
                positions=[i for i,v in enumerate(args) if _inside(v,chain['buffer'],chain.get('buffer_size'))]
                if positions and len(chain['steps'])<MAX_STEPS:
                    chain['steps'].append(dict(kind='pass',instruction=event['instruction'],callee=callee,callee_known=len(targets)==1,arguments=positions,
                                              reason='The same buffer value is passed to this call; callee behavior is not established.'))
            layouts=[]
            if contract:
                if contract['role']=='opaque':continue
                if 'direction' in contract and _number(args[contract['direction']])!=0:continue
                layouts=[contract]
            else:
                # Unknown helpers are only leads. Retain plausible permutations
                # explicitly; swapping source and length can fit the same bytes.
                for source,dest,length in permutations(range(min(argument_count,len(args))),3):
                    lo,sz=_number(args[source]),_number(args[length]);destination=args[dest]
                    address=_number(destination)
                    if lo is not None and sz is not None and 4<=sz<=MAX_TRANSFER and 0<=lo<lo+sz<=len(image) and destination and (
                            address is not None and ram[0]<=address<ram[1] or destination['kind']=='return'):
                        layouts.append(dict(role='candidate',source=source,destination=dest,length=length,domain='rom'))
                layouts=layouts[:6]
            for layout in layouts:
                source=_contract_value(event,layout,'source');destination=_contract_value(event,layout,'destination')
                length=_number(_contract_value(event,layout,'length')) if 'length' in layout else None
                if not source or not destination or length is not None and not 0<length<=MAX_TRANSFER:continue
                number=_number(source)
                origins=[]
                if number is not None:
                    if layout['domain']=='rom':origins=[dict(start=number,segment=None)]
                    else:origins=mapped(number)
                parents=[chain for chain in active if _inside(source,chain['buffer'],chain.get('buffer_size'))]
                if parents and layout['role']=='decompress':
                    for parent in parents:
                        child={**parent,'steps':parent['steps'][:MAX_STEPS-1]+[dict(kind='decompress',instruction=event['instruction'],callee=callee,callee_known=len(targets)==1,
                                source=source,destination=destination,reason='Named decompression contract with the traced input buffer; successful execution is not proven.')],
                               'buffer':destination,'buffer_size':None,'block':event['block']}
                        chains.append(child);active.append(child)
                    continue
                for origin in origins:
                    lo=origin['start']
                    if length is None or not 0<=lo<lo+length<=min(len(image),origin.get('limit',len(image))):continue
                    if length>layout.get('max_length',MAX_TRANSFER) or length%layout.get('length_alignment',1) or lo%layout.get('source_alignment',1):continue
                    destination_address=_number(destination)
                    if destination_address is not None and destination_address%layout.get('destination_alignment',1):continue
                    chain=dict(function=fn['name'],instruction=event['instruction'],callee=callee,role=layout['role'],
                               source=dict(start=lo,end=lo+length,size=length,segment=origin['segment']),
                               buffer=destination,buffer_size=None if layout['role']=='decompress' else length,block=event['block'],
                               confidence='candidate' if layout['role']=='candidate' or len(origins)>1 else 'inferred',
                               reason='Plausible constant argument layout; source/length permutations may be ambiguous and copy behavior is unverified.' if layout['role']=='candidate' else 'User-declared call role and recovered arguments; behavior is a hypothesis.' if layout.get('basis')=='user' else f"Small wrapper forwards arguments to {layout['via']} at {layout['wrapper_instruction']:#x}; named implementation and successful execution remain unverified." if layout.get('basis')=='wrapper' else 'Named call contract and recovered arguments; implementation, successful execution and DMA completion are not proven.',
                               steps=[dict(kind=layout['role'],instruction=event['instruction'],callee=callee,callee_known=len(targets)==1,source=source,destination=destination,
                                           length=length,argument_layout={key:layout[key] for key in ('source','destination','length') if key in layout},argument_evidence=sorted({at for v in (source,destination) if v for at in v.get('evidence',[])}))])
                    # Reuse of a destination replaces the prior buffer contents.
                    active=[prior for prior in active if not _same(prior['buffer'],destination)]
                    chains.append(chain);active.append(chain)
            if len(chains)>=MAX_CHAINS:
                limited=True;break
        progress(dict(phase='Tracing loader arguments',done=n+1,total=len(functions)))
        if len(chains)>=MAX_CHAINS:break
    # Stable extents allow the map to group actual transfer boundaries rather
    # than treating arbitrary 64 KiB display pages as asset definitions.
    return dict(chains=chains[:MAX_CHAINS],limited=limited,supported=True,
                note='Static, block-local evidence. Named contracts and unknown-helper candidates are distinct. DMA may be asynchronous; pass/read links do not prove successful loading or asset type. Branch joins, indirect values, file IDs and mutable memory can remain unresolved.')


def attach(report,flow):
    """Attach bounded chain IDs to every intersecting display row, preserving full extents."""
    report['loader_flow']=flow
    rows=report['regions'];starts=[r['start'] for r in rows]
    for row in rows:row['loader_chains']=[]
    for i,chain in enumerate(flow['chains']):
        chain['id']=str(i)
        source=chain['source']
        first=max(0,bisect.bisect_right(starts,source['start'])-1)
        for row in rows[first:]:
            if row['start']>=source['end']:break
            row['loader_chains'].append(str(i))
