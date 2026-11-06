"""Small target-backed source hypotheses; compilation and the ordinary byte/data judge decide."""
import re
from collections import Counter

from ..forks.csource import _blank
from .pattern_repairs import close

ID=r'[A-Za-z_]\w*'


def commented_calls(source, header, target_calls):
    """Restore complete single-line calls only when retail has more of that exact callee."""
    from .cpu_source_context import declarations
    from .source_match_context import macros
    clean=_blank(source)
    aliases=dict(re.findall(r'(?m)^\s*#define\s+('+ID+r')\s+('+ID+r')\s*$',_blank(header)))
    def canonical(name):
        for _ in range(8):
            if name not in aliases:return name
            name=aliases[name]
        return name
    counts=Counter(canonical(n) for n in re.findall(r'\b('+ID+r')\s*\(',clean))
    missing=Counter(target_calls)-counts
    names=set(re.findall(r'(?m)^[ \t]*//[ \t]*('+ID+r')\s*\(',source))
    declared=declarations(header,names | {canonical(n) for n in names})
    edits=[];seen=Counter()
    for m in re.finditer(r'(?m)^[ \t]*//[ \t]*('+ID+r')\s*\([^\n]*;[ \t]*$',source):
        # Ensure // is a real comment, not text inside a block comment or literal.
        slash=source.index('//',m.start(),m.end())
        if clean[m.start():m.end()].strip():continue
        if source[:slash].rfind('/*')>source[:slash].rfind('*/'):continue
        name=m[1]
        target=canonical(name)
        if not ({name,target} & declared.keys()) or (name in macros(header) and name not in aliases) or seen[target]>=missing[target]:continue
        call=source[slash+2:m.end()].strip();opening=call.find('(');end=close(_blank(call),opening)
        if not end or call[end:].strip()!=';' or '#' in call or '{' in call:continue
        edits.append((slash,slash+2,'  '));seen[target]+=1
        if len(edits)>=4:break
    if not edits:return []
    out=source
    for a,b,s in reversed(edits):out=out[:a]+s+out[b:]
    return [('cpu-commented-target-calls',out,header)]


def field_order(source, header, fields, lines, spans):
    """A repeated load/store of one mapped word can suggest a local adjacent-field swap."""
    if len(fields)<2 or len({x['new']-x['old'] for x in fields})!=1:return []
    if not any(x.get('store') for x in fields) or not any(not x.get('store') for x in fields):return []
    names=[]
    for x in fields:
        matches=re.findall(r'(?:->|\.)\s*('+ID+r')',_blank(lines.get(x['row'],'')))
        if len(matches)!=1:return []
        names.append(matches[0])
    if len(set(names))!=1:return []
    delta=fields[0]['new']-fields[0]['old']
    if delta not in {4,-4}:return []
    name=names[0];clean=_blank(header);out=[]
    word=r'(?:int|unsigned int|signed int|float|s32|u32|f32)'
    decl=word+r'\s+('+ID+r')\s*;'
    for a,b in spans:
        matches=list(re.finditer(r'(?=('+decl+r')\s*('+decl+r'))',clean[a:b]))
        for m in matches:
            if (m[2],m[4])[0 if delta==4 else 1]!=name:continue
            # Both fields are explicitly four bytes on supported 32-bit ROM targets.
            start=a+m.start(1);end=a+m.end(3)
            first=header[a+m.start(1):a+m.end(1)];second=header[a+m.start(3):a+m.end(3)]
            middle=header[a+m.end(1):a+m.start(3)]
            out.append(('cpu-field-order',source,header[:start]+second+middle+first+header[end:]))
    return out[:1] if len(out)==1 else []


def data_bindings(source, header, rows, resolve):
    """Try actual symbolic target operands, never fabricate storage or initializers."""
    pairs=Counter()
    pattern=r'((?:lui|addiu)\s+[^%]+)%((?:hi|lo))\((D_[0-9A-Fa-f]{8})\)(.*)'
    for mark,target,own in rows:
        if mark==' ':continue
        a=re.fullmatch(pattern,target);b=re.fullmatch(pattern,own)
        if a and b and (a[1],a[2],a[4])==(b[1],b[2],b[4]) and a[3]!=b[3]:pairs[b[3],a[3]]+=1
    out=[]
    for (old,new),count in pairs.items():
        if count<2 or resolve(new) is None:continue
        clean=_blank(header)
        # Only an unambiguous extern array. No local objects, macros with bodies or scalars.
        hits=list(re.finditer(r'\bextern\s+([^;{}()]*\b)'+re.escape(old)+r'(\s*\[[^;{}()]*\]\s*;)',clean))
        if len(hits)!=1 or re.search(r'\b'+re.escape(new)+r'\b',clean):continue
        m=hits[0];h=header+'\nextern '+m[1]+new+m[2]+'\n'
        # Keep the original declarations; change only references in the candidate body.
        if old in _blank(source):
            s=source
            for hit in reversed(list(re.finditer(r'\b'+re.escape(old)+r'\b',_blank(source)))):
                s=s[:hit.start()]+new+s[hit.end():]
        else:
            aliases=list(re.finditer(r'(?m)^\s*#define\s+('+ID+r')\s+'+re.escape(old)+r'\s*$',clean))
            if len(aliases)!=1:continue
            alias=aliases[0];h=h[:alias.start()]+h[alias.start():alias.end()].replace(old,new)+h[alias.end():];s=source
        out.append(('cpu-target-data-binding',s,h))
    return out[:2]


def terminal_branch(source, name, header, evidence, rows):
    if not evidence:return []
    from ..forks.csource import find
    span=find(source,name)
    if not span:return []
    clean=_blank(source);head=clean[span[0]:clean.find('{',span[0])]
    sig=re.search(r'\bvoid\s+'+re.escape(name)+r'\s*\(([^()]*)\)\s*$',head)
    if not sig or re.search(r'\b(?:return|goto)\b',clean[span[0]:span[1]]):return []
    params=[]
    for arg in sig[1].split(','):
        m=re.fullmatch(r'\s*(?:'+ID+r'\s+)+\*\s*('+ID+r')\s*',arg)
        if not m:return []
        params.append(m[1])
    if max(evidence['condition_arg'],evidence['object_arg'])>=len(params):return []
    # Only a measured missing target block authorizes a trial, not assembly resemblance alone.
    missing={target for mark,target,_ in rows if mark=='<'}
    normalize=lambda s:' '.join(s.lower().split())
    if sum(normalize(s) in {normalize(t) for t in missing} for s in evidence['instructions'])<3:return []
    p=params[evidence['condition_arg']];q=params[evidence['object_arg']]
    access=lambda obj,off:f'*(int *)((unsigned char *){obj} + {off})'
    statement=f'\n if ({access(p,evidence["test"])}) {{\n  {access(p,evidence["clear"])} = 0;\n  {access(q,evidence["flags"])} |= {evidence["mask"]};\n }}\n'
    end=clean.rfind("}",span[0],span[1])
    if end<0:return []
    return [('cpu-terminal-retail-branch',source[:end]+statement+source[end:],header)]
