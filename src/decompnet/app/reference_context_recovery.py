"""Private reference hypotheses before permutations; no guessed ABI or shared-header edits."""
import re
from collections import defaultdict

from ..forks.csource import _blank, find
from ..network import NetworkError
from .source_next_steps import called

ID=r'[A-Za-z_]\w*'


def static_helpers(source, header, reference):
    """Try actual same-file static definitions with their original physical line numbers."""
    from .pattern_repairs import close
    from .source_match_context import macro_context
    shadowed=lambda text:set(re.findall(r'\(\s*\*\s*('+ID+r')\s*\)',_blank(text)))
    selected=[];used=called(source)-shadowed(source);seen=set()
    for _ in range(4):
        pending=sorted(used-seen);seen.update(pending)
        for name in pending:
            span=find(reference,name)
            if not span or find(header,name):continue
            code=reference[span[0]:span[1]]
            head=_blank(code).split('{',1)[0]
            if not re.search(r'\bstatic\b',head) or ';' in head or len(code)>16000:continue
            if len(selected)>=4:return []
            selected.append((name,code,reference[:span[0]].count('\n')+1))
            used.update(called(code)-{name}-shadowed(code))
    line_variants=helper_lines(source,header,reference)
    if not selected:return line_variants
    private=header
    for name,_,_ in selected:
        # A stale external prototype conflicts with the real static definition.
        pattern=r'(?m)^[ \t]*(?:(?:extern|static|inline)\s+)?(?:'+ID+r'[ \t*]+)+'+re.escape(name)+r'\s*\('
        clean=_blank(private);edits=[]
        for match in re.finditer(pattern,clean):
            end=close(clean,match.end()-1)
            tail=re.match(r'\s*;',clean[end:]) if end else None
            if tail:edits.append((match.start(),end+tail.end()))
        for a,b in reversed(edits):private=private[:a]+'\n'*private[a:b].count('\n')+private[b:]
    bodies='\n'.join(code for _,code,_ in selected)
    private+=macro_context(reference,bodies,private)
    for _,code,_ in selected:
        # Keep actual callback parameter declarators too; simple signature readers omit these.
        private+='\n'+code[:_blank(code).index('{')].strip()+';\n'
    for _,code,line in selected:
        private+=f'\n#line {line} "reference.c"\n'+code+'\n'
    return [('cpu-reference-static-helpers',source,private),*line_variants]


def helper_lines(source, header, reference):
    """Restore physical lines only for identical existing private helper definitions."""
    private=header;edits=[]
    normalize=lambda text:re.sub(r'\s+','',_blank(text))
    for name in sorted(called(source))[:32]:
        actual=find(reference,name);existing=find(header,name)
        if not actual or not existing:continue
        a,b=actual;x,y=existing
        code=reference[a:b];own=header[x:y]
        original_head=_blank(code).split('{',1)[0];own_head=_blank(own).split('{',1)[0]
        if not re.search(r'\bstatic\b',own_head):continue
        if not re.search(r'\bstatic\b',original_head) and not re.search(r'\b(?:inline|__inline(?:__)?)\b',own_head):continue
        strip=lambda text:re.sub(r'\b(?:static|inline|__inline(?:__)?)\b','',text)
        if normalize(strip(code))!=normalize(strip(own)):continue
        line=reference[:a].count('\n')+1
        directive=f'#line {line} "reference.c"'
        if header[:x].rstrip().endswith(directive):continue
        edits.append((x,'\n'+directive+'\n'))
        if len(edits)>=4:break
    for at,text in sorted(edits,reverse=True):private=private[:at]+text+private[at:]
    return [('cpu-reference-helper-lines',source,private)] if edits else []


def configurations(reader, cfg, mapping, aliases, fallback, prepare):
    """One boolean source switch at a time, only if it reveals additional real calls."""
    from .source_conditions import select
    raw=reader.file(reader.root/mapping[2]);result=[]
    guards=set(re.findall(r'(?m)^\s*#\s*(?:if|ifdef|ifndef)\s+('+ID+r')\s*$',_blank(raw)))
    definitions=dict(re.findall(r'(?m)^\s*#\s*define\s+('+ID+r')\s+([01])\s*$',_blank(raw)))
    try:baseline=prepare(reader,cfg,mapping,aliases,fallback)
    except NetworkError:return []
    for name in sorted(guards & definitions.keys()-reader.overrides.keys()):
        value=str(1-int(definitions[name]))
        overrides={**reader.overrides,name:value}
        selected,_=select(raw,{},overrides)
        span=find(selected,mapping[1])
        if not span or not (called(selected[span[0]:span[1]])-{mapping[1]}):continue
        # Use a private reader. Never mutate configured overrides, even on failure.
        from .source_preparation import Reader
        private=Reader({**cfg,'overrides':'\n'.join(k+' = '+v for k,v in overrides.items())})
        try:pair=prepare(private,cfg,mapping,aliases,fallback)
        except NetworkError:continue
        if len(called(pair[0]))<=len(called(baseline[0])):continue
        result.append(('cpu-reference-config-'+name,pair[0],pair[1]))
        if len(result)>=2:break
    return result


def binding_variants(source, header, asm, references):
    """Consensus from independent exact neighbours, anchored by target symbol operands."""
    pattern=r'(?m)^\s*#define\s+('+ID+r')\s+(func_[0-9A-Fa-f]{8})\s*$'
    votes=defaultdict(set);values=defaultdict(set)
    for identity,text in references:
        for name,target in re.findall(pattern,_blank(text)):
            votes[name,target].add(identity);values[name].add(target)
    used=set(re.findall(ID,_blank(source)));targets=set(re.findall(r'\bfunc_[0-9A-Fa-f]{8}\b',asm))
    edits=[]
    for match in re.finditer(pattern,_blank(header)):
        name,old=match.groups()
        if name not in used or len(values[name])!=1:continue
        target=next(iter(values[name]))
        if old==target or target not in targets or len(votes[name,target])<2:continue
        edits.append((match.start(2),match.end(2),target))
    additions=[]
    from .cpu_source_context import declarations
    from .source_match_context import macros
    declared=declarations(header,called(source))
    shadowed=set(re.findall(r'\(\s*\*\s*('+ID+r')\s*\)',_blank(source)))
    for name in sorted(called(source)-shadowed):
        if name not in declared or name in macros(header) or find(header,name) or len(values[name])!=1:continue
        target=next(iter(values[name]))
        if target in targets and len(votes[name,target])>=2:additions.append((name,target))
        if len(additions)+len(edits)>=4:break
    if not 0<len(edits)+len(additions)<=4:return []
    private=header
    for a,b,target in reversed(edits):private=private[:a]+target+private[b:]
    # The alias precedes the actual prototype so float/aggregate ABI types follow the call.
    private=''.join('#define '+name+' '+target+'\n' for name,target in additions)+private
    return [('cpu-verified-callback-bindings',source,private)]


def neighbours(net, proj, fn, source, header):
    """Bounded exact evidence lookup; conflicting identities remain a manual review."""
    if not net.project_store(proj['id']).one("SELECT 1 FROM sqlite_master WHERE name='verified_matches'"):return []
    rows=net.project_store(proj['id']).all("SELECT f.id,v.header FROM functions f JOIN verified_matches v ON v.function_id=f.id "
                       "WHERE f.project_id=? AND f.status='matched' AND f.best_score=0 AND v.source=f.matched_source "
                       "AND f.vram BETWEEN ? AND ? ORDER BY ABS(f.vram-?) LIMIT 16",
                       proj['id'],fn['vram']-65536,fn['vram']+65536,fn['vram'])
    return binding_variants(source,header,fn['asm'] or '',[(r['id'],r['header']) for r in rows if len(r['header'])<=1024*1024])
