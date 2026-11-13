"""Bounded target-backed pointer lifetimes and explicit commented retail conditions."""
import re

from decomprepair.pattern_repairs import close

from ..forks.csource import _blank
from .reference_stage_repairs import declaration_block

ID=r'[A-Za-z_]\w*'


def draft_prefix(source, name, header, draft, rows, arch):
    """Restore one supplied leading conditional with a measured missing target call, not new CFG."""
    from ..forks.csource import find
    from .source_context import prototypes
    from .source_lead_analysis import features
    from .source_match_context import macro_context, macros
    from .source_next_steps import called
    from .source_preparation import rename
    if not draft or draft==source:return []
    own=declaration_block(source,name);span=find(draft,name)
    if not own or not span:return []
    target=features([r[1] for r in rows if r[1]],arch)['calls']
    current=features([r[2] for r in rows if r[2]],arch)['calls']
    missing=set(target)-set(current)
    if not missing:return []
    clean=_blank(draft);opening=clean.find('{',span[0],span[1])
    first=re.search(r'\bif\s*\(',clean[opening+1:span[1]])
    if not first:return []
    start=opening+1+first.start();condition=start+first[0].index('(');end=close(clean,condition)
    block=re.match(r'\s*{',clean[end:]) if end else None
    if not block:return []
    stop=close(clean,end+block.end()-1,'{','}')
    if not stop or re.match(r'\s*else\b',clean[stop:]):return []
    prefix=draft[start:stop]
    definitions=macros(draft);calls=called(prefix)-{'if'}
    for _ in range(4):
        expanded=set().union(*(called(definitions[n][0]) for n in calls & definitions.keys())) if calls & definitions.keys() else set()
        calls|=expanded
    calls-=definitions.keys()
    if len(calls & missing)!=1 or not calls<=set(target):return []
    if len(prefix)>3000 or re.search(r'\b(?:for|while|switch|goto|return|asm)\b|#',_blank(prefix)):return []
    # Parameter names may differ between the supplied machine draft and actual typed source.
    def params(text, at):
        hit=re.search(r'\b'+re.escape(name)+r'\s*\(([^()]*)\)\s*{',_blank(text[:at+1]))
        if not hit:return None
        result=[]
        for arg in hit[1].split(','):
            m=re.fullmatch(r'[\w\s*]+\b('+ID+r')\s*',arg.strip())
            if not m:return None
            result.append(m[1])
        return result
    old=params(draft,opening);new=params(source,own[0]-1)
    if not old or not new or len(old)!=len(new):return []
    leading=draft[opening+1:start];declarations=[];assignments=[];locals=[];seen_assignment=False
    for statement in leading.split(';'):
        if not statement.strip():continue
        plain=_blank(statement)
        declaration=re.fullmatch(r'\s*(?:'+ID+r'\s+)+\**\s*('+ID+r')(?:\s*=[\s\S]+)?\s*',plain)
        if declaration and not seen_assignment:
            locals.append(declaration[1]);declarations.append(statement+';')
        elif re.fullmatch(r'\s*'+ID+r'\s*=\s*'+ID+r'\s*',plain):
            assignments.append(statement+';');seen_assignment=True
        else:return []
    if len(locals)>8 or any(re.search(r'\b'+re.escape(v)+r'\b',_blank(source)) for v in locals):return []
    # Initializers may cast or offset parameters; no extra calls, writes, volatile reads or controls.
    if called('\n'.join(declarations)) or re.search(r'\bvolatile\b|\+\+|--|->',_blank(leading)):return []
    aliases=dict(zip(old,new));prefix=rename('\n'.join(assignments)+prefix,aliases)
    declarations=rename('\n'.join(declarations),aliases)
    body=source[:own[1]]+'\n'+prefix+'\n'+source[own[1]:]
    body=body[:own[0]]+'\n'+declarations+'\n'+body[own[0]:]
    private=header+macro_context(draft,prefix+'\n'+declarations,header)
    private+=prototypes(draft,prefix,private,name)
    return [('cpu-retail-draft-prefix',body,private)]


def array_indices(source, header, rows, resolve):
    """A repeated target base versus a compiled neighboring element can select element zero."""
    from collections import Counter
    pairs=Counter()
    for mark,target,own in rows:
        if mark==' ':continue
        a=re.search(r'%(?:hi|lo)\((D_[0-9A-Fa-f]{8})\)',target)
        b=re.search(r'%(?:hi|lo)\((D_[0-9A-Fa-f]{8})\)',own)
        if a and b and a[1]!=b[1]:pairs[a[1],b[1]]+=1
    results=[]
    for (base,neighbor),count in pairs.items():
        address=resolve(base)
        if count<2 or address is None or address!=int(base[2:],16):continue
        declarations=re.findall(r'\bextern\s+float\s+'+base+r'\s*\[\s*(\d*)\s*\]\s*;',_blank(header))
        if len(declarations)!=1:continue
        offset=int(neighbor[2:],16)-address
        if offset<=0 or offset%4 or offset>64:continue
        index=offset//4
        if declarations[0] and index>=int(declarations[0]):continue
        pattern=r'\b'+base+r'\s*\[\s*'+str(index)+r'\s*\]'
        matches=list(re.finditer(pattern,_blank(source)))
        if not matches:continue
        body=source
        for hit in reversed(matches):body=body[:hit.start()]+base+'[0]'+body[hit.end():]
        results.append(('cpu-retail-array-index',body,header))
    return results[:1]


def pointer_lifetimes(source, name, header, evidence, rows):
    clean=_blank(source);block=declaration_block(source,name)
    if not block or not evidence:return []
    signature=clean[:clean.find('{')]
    param=re.search(r'\(\s*('+ID+r')\s*\*\s*('+ID+r')\s*[,)]',signature)
    if not param:return []
    kind,obj=param.groups()
    zeros=list(re.finditer(r'\b'+re.escape(obj)+r'\s*->\s*('+ID+r')\s*=\s*(?:NULL|0)\s*;',clean))
    branch=re.search(r'\b(?:if|for|while|switch)\s*\(',clean[block[1]:])
    if branch:zeros=[m for m in zeros if m.start()<block[1]+branch.start()]
    # A unique cleared pointer field is required; no guessed offset-to-member map.
    if len(zeros)!=1:return []
    field=zeros[0][1]
    records=re.findall(r'\btypedef\s+struct(?:\s+'+ID+r')?\s*\{([^{}]*)\}\s*'+re.escape(kind)+r'\s*;',_blank(header),re.S)
    if len(records)!=1:return []
    member=re.findall(r'(?:^|;)\s*('+ID+r'\s*\*)\s*'+re.escape(field)+r'\s*;',records[0])
    if len(member)!=1:return []
    aliases=dict(re.findall(r'(?m)^\s*#define\s+('+ID+r')\s+('+ID+r')\s*$',_blank(header)))
    results=[]
    for proof in evidence:
        # The unique source clear must compile to the measured retail field offset.
        stores=[re.fullmatch(r'sw\s+\$zero,\s*(0x[0-9a-fA-F]+|\d+)\(\$(?:a0|s\d)\)',r[2]) for r in rows[:32]]
        if not any(m and int(m[1],0)==proof['offset'] for m in stores):continue
        names={proof['callee']}|{n for n,t in aliases.items() if t==proof['callee']}
        hits=[m for m in re.finditer(r'\b('+ID+r')\s*\(',clean) if m[1] in names]
        if len(hits)<proof['ordinal']:continue
        hit=hits[proof['ordinal']-1];end=close(clean,hit.end()-1)
        if not end or not re.match(r'\s*;',clean[end:]):continue
        stop=end+re.match(r'\s*;',clean[end:]).end()
        before=clean[clean.rfind('\n',0,hit.start())+1:hit.start()]
        if before.strip() or hit.start()<=zeros[0].end():continue
        local='cpu_saved_'+field
        if re.search(r'\b'+local+r'\b',clean):continue
        edits=[(block[1],block[1],f'\n {member[0]}{local};\n'),
               (zeros[0].start(),zeros[0].start(),f'{local} = {obj}->{field};\n'),
               (hit.start(),hit.start(),f'{obj}->{field} = {local};\n'),
               (stop,stop,f'\n {obj}->{field} = 0;')]
        s=source
        for a,b,text in sorted(edits,reverse=True):s=s[:a]+text+s[b:]
        results.append(('cpu-retail-pointer-lifetime',s,header))
    return results[:1]


def commented_conditions(source, header, target_symbols):
    """Restore only explicit commented if/else stores supported by two target data operands."""
    aliases=dict(re.findall(r'(?m)^\s*#define\s+('+ID+r')\s+('+ID+r')\s*$',_blank(header)))
    result=[]
    for hit in re.finditer(r'(?m)(?:^[ \t]*//[^\n]*\n){4,8}',source):
        if _blank(source)[hit.start():hit.end()].strip():continue
        if source[:hit.start()].rfind('/*')>source[:hit.start()].rfind('*/'):continue
        text=re.sub(r'(?m)^[ \t]*//[ \t]*','',hit[0]);clean=_blank(text)
        m=re.fullmatch(r'\s*if\s*\(([^;{}]+)\)\s*('+ID+r'\s*\[[\w\s]+\])\s*=\s*('+ID+r')\s*;\s*else\s*\2\s*=\s*('+ID+r')\s*;\s*',clean)
        if not m or '(' in m[1] or not re.search(r'&',m[1]):continue
        targets={aliases.get(m[3],m[3]),aliases.get(m[4],m[4])}
        if len(targets)!=2 or not targets<=set(target_symbols):continue
        # Unavailable enum names or objects are left for another coherent reference context.
        words=set(re.findall(ID,clean))-{'if','else'}
        if any(not re.search(r'\b'+re.escape(w)+r'\b',_blank(header)+'\n'+_blank(source[:hit.start()])) for w in words):continue
        result.append(('cpu-retail-commented-condition',source[:hit.start()]+text+source[hit.end():],header))
    return result[:2]
