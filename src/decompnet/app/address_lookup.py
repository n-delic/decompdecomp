"""Read-only symbol/address ownership, preserving ambiguity between original images."""
import json
import re

from ..network import NetworkError

COLUMNS = 'id,name,unit,vram,rom_offset,size,status'


def image(row):
    return (row['unit'], row['rom_offset']-row['vram'] if row['rom_offset'] is not None and row['vram'] is not None else None)


def function_info(row, address=None):
    return {'function':row['name'], 'unit':row['unit'], 'address':row['vram'], 'rom_offset':row['rom_offset'],
            'size':row['size'], 'status':row['status'],
            'offset':address-row['vram'] if address is not None and row['vram'] is not None else 0}


def resolve(net, agent, slug, queries, context_function=''):
    if not isinstance(queries,list) or not 1<=len(queries)<=20 or any(not isinstance(q,str) or not 0<len(q.strip())<=200 for q in queries):
        raise NetworkError('Supply 1–20 symbol names or hexadecimal addresses, each at most 200 characters.')
    proj=net._project(slug)
    if context_function:
        context=[net._function(net.resolve_function(agent,slug,context_function),slug)]
    else:
        context=net.store.all('SELECT f.* FROM functions f JOIN claims c ON c.function_id=f.id WHERE f.project_id=? AND c.agent_id=? AND c.expires_at>strftime(\'%s\',\'now\')',proj['id'],agent.id) if agent else []
    scopes={image(r) for r in context}
    result=[]
    for query in queries:
        query=query.strip().strip('`')
        exact=net.store.all(f'SELECT {COLUMNS} FROM functions WHERE project_id=? AND name=?',proj['id'],query)
        if not exact and not re.fullmatch(r'(?:0[xX][0-9a-fA-F]+|(?:D_|func_)[0-9a-fA-F]{8,16})',query) and '+' not in query:
            exact=getattr(net,'_loose_functions',lambda *args:[])(query,slug)
            if len(exact)>1 and len(scopes)==1:
                scoped=[r for r in exact if image(r) in scopes]
                if scoped:exact=scoped
        if exact:
            result.append({'query':query,'kind':'function_entry' if len(exact)==1 else 'ambiguous','candidate_count':len(exact),'candidates':[function_info(r) for r in exact[:12]]})
            continue
        base, offset=query,0
        suffix=re.fullmatch(r'(.+)\+(0x[0-9a-fA-F]+|[0-9]+)',query)
        if suffix:
            base=suffix[1];offset=int(suffix[2],16 if suffix[2].startswith('0x') else 10)
        symbols=net.store.all('SELECT name,address,size,kind FROM symbols WHERE project_id=? AND name=?',proj['id'],base)
        named=net.store.all(f'SELECT {COLUMNS} FROM functions WHERE project_id=? AND name=?',proj['id'],base)
        numeric=re.fullmatch(r'0[xX]([0-9a-fA-F]+)',base)
        generated=re.fullmatch(r'(?:D_|func_)([0-9a-fA-F]{8,16})',base)
        address=(symbols[0]['address'] if symbols else named[0]['vram'] if named else
                 int((numeric or generated)[1],16) if numeric or generated else None)
        if address is None:
            result.append({'query':query,'kind':'unknown','candidates':[],
                           'next':'Use the exact stored symbol, or a hexadecimal address from read_data/get_assembly.'})
            continue
        address+=offset
        owners=net.store.all(f'SELECT {COLUMNS} FROM functions WHERE project_id=? AND vram<=? AND vram+MAX(size,1)>? ORDER BY unit,rom_offset,name',proj['id'],address,address)
        owner_scopes={image(r) for r in named} if named else scopes
        if len(owner_scopes)==1 and len(owners)>1:
            scoped=[r for r in owners if image(r) in owner_scopes]
            if scoped:owners=scoped
        # A registered data name is not silently reinterpreted as code, even when analysis overlaps it.
        if not symbols:
            symbols=net.store.all('SELECT name,address,size,kind FROM symbols WHERE project_id=? AND address<=? AND address+MAX(size,1)>? ORDER BY name LIMIT 16',proj['id'],address,address)
        # Saved/imported symbol tables use 'func'; older integrations also use
        # 'function'. Neither spelling turns a conflicting data record into code.
        data=any(r['kind'] not in ('func','function') for r in symbols)
        if data:
            kind='data'
        elif len(owners)>1:
            kind='ambiguous'
        elif owners:
            kind='function_entry' if owners[0]['vram']==address else 'function_interior'
        else:
            kind='data' if net.store.one('SELECT 1 FROM symbols WHERE project_id=? AND kind NOT IN (\'func\',\'function\') AND address<=? AND address+MAX(size,1)>?',proj['id'],address,address) else 'unknown'
        result.append({'query':query,'kind':kind,'address':address,'symbols':[dict(r) for r in symbols],
                       'candidates':[function_info(r,address) for r in owners[:12]],'candidate_count':len(owners),
                       'next':'Choose a full function name or supply context_function to identify the overlay.' if kind=='ambiguous' else
                              'Use read_data for bytes; listed function extents are ownership evidence, not proof this is code.' if kind=='data' else
                              'The address is inside the listed function; use its canonical name for reads. No entry was created.' if kind=='function_interior' else ''})
    return {'results':result,'scope':'Project-local read-only ownership. No names, boundaries, claims or matching status changed.'}


def read_function(net, agent, slug, query):
    item=resolve(net,agent,slug,[query])['results'][0]
    if item['kind'] in {'function_entry','function_interior'} and len(item['candidates'])==1:
        return item['candidates'][0]['function']
    raise NetworkError('Cannot resolve this as one function read. '+json.dumps(item,ensure_ascii=False)+
                       '\nUse resolve_addresses(queries=[...], context_function=...) to inspect ownership without guessing.')
