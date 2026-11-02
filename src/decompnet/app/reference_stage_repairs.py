"""Small reference-context stages before permutations; every proposal still needs the judge."""
import re

from ..forks.csource import _blank, find
from .pattern_repairs import close
from .source_next_steps import called

ID=r'[A-Za-z_]\w*'


def declaration_block(source, name):
    span=find(source,name)
    if not span:return None
    clean=_blank(source);opening=clean.find('{',span[0],span[1]);at=opening+1;items=[]
    while len(items)<48:
        hit=re.match(r'\s*((?:(?:register|const|unsigned|signed|long|short)\s+)*'+ID+r'\s+[\w\s*,\[\]]+);',clean[at:span[1]-1])
        if not hit:break
        text=hit[1]
        if re.search(r'\b(?:return|break|continue|goto|typedef|static|volatile)\b',text):break
        declaration=re.match(r'(.+?\s+)(\**\s*'+ID+r'(?:\s*\[[\w\s]+\])?(?:\s*,\s*\**\s*'+ID+r'(?:\s*\[[\w\s]+\])?)*)$',text)
        if not declaration:break
        kind=re.sub(r'\bregister\s+','',declaration[1]);kind=' '.join(kind.split())
        for item in declaration[2].split(','):
            m=re.fullmatch(r'(\**\s*)('+ID+r')(\s*\[[\w\s]+\])?',item.strip())
            if not m:return None
            items.append((m[2],kind+re.sub(r'\s+','',m[1])+(re.sub(r'\s+','',m[3]) if m[3] else '')))
        at+=hit.end()
    return (opening+1,at,items) if items and len({n for n,_ in items})==len(items) else None


def declarations(source, name, reference):
    """Restore actual declaration order only when the complete uninitialized local set agrees."""
    own=declaration_block(source,name);actual=declaration_block(reference,name)
    if not own or not actual or dict(own[2])!=dict(actual[2]) or own[2]==actual[2]:return []
    a,b,_=actual;x,y,_=own
    return [('cpu-reference-declaration-order',source[:x]+reference[a:b]+source[y:])]


def inline_helpers(source, header, reference, target_calls, clobbers=False):
    """Actual small non-recursive wrappers absent from retail's out-of-line call set."""
    from .source_context import prototypes
    from .source_match_context import macro_context, macros
    result=[];targets=set(target_calls);combined=header
    for name in sorted(called(source))[:32]:
        span=find(reference,name)
        if not span or (name not in targets if clobbers else name in targets) or find(source,name) or find(header,name):continue
        code=reference[span[0]:span[1]];plain=_blank(code);opening=plain.find('{')
        if opening<0 or len(code)>(3000 if clobbers else 1800) or len(plain.split(';'))>(32 if clobbers else 9):continue
        if re.search(r'\b(?:static|inline|__inline|volatile|asm|for|while|switch|goto)\b|#',plain):continue
        children=called(code)-{name}-set(macros(header))
        if (not clobbers and not children<=targets) or len(re.findall(r'\b'+re.escape(name)+r'\s*\(',plain))!=1:continue
        # Actual leaf getters matter too: a macro with the same expression can allocate
        # registers differently. Do not turn arbitrary no-call bodies into helpers.
        if not clobbers and not children and (len(re.findall(r'\breturn\b',plain))!=1
                or re.search(r'(?<![=!<>])=(?!=)|\+\+|--',plain[opening+1:])):continue
        private=combined if not clobbers else header
        # A provisional function-like macro may stand in for the actual getter.
        # Replace only a single-line macro, never an object alias or continuation.
        macro=re.search(r'(?m)^[ \t]*#define[ \t]+'+re.escape(name)+r'\([^\n]*$',private)
        if macro:
            if clobbers or macro[0].rstrip().endswith('\\'):continue
            private=private[:macro.start()]+'\n'*macro[0].count('\n')+private[macro.end():]
        pattern=r'(?m)^[ \t]*(?:extern\s+)?(?:'+ID+r'[ \t*]+)+'+re.escape(name)+r'\s*\('
        edits=[];clean=_blank(private)
        for match in re.finditer(pattern,clean):
            end=close(clean,match.end()-1);tail=re.match(r'\s*;',clean[end:]) if end else None
            if tail:edits.append((match.start(),end+tail.end()))
        if not edits and not macro and re.search(r'\b'+re.escape(name)+r'\b',_blank(private)):continue
        for a,b in reversed(edits):private=private[:a]+'\n'*private[a:b].count('\n')+private[b:]
        private+=macro_context(reference,code,private)+prototypes(reference,code,private,name)
        line=reference[:span[0]].count('\n')+1
        private+=f'\n#line {line} "reference.c"\n'+('static ' if clobbers else 'static __inline ')+code+'\n'
        if not clobbers:combined=private
        result.append(('cpu-reference-callee-context' if clobbers else 'cpu-reference-inline-wrapper',source,private))
        if len(result)>=2:break
    return result


def stages(source, name, header, reference, target_calls):
    helpers=inline_helpers(source,header,reference,target_calls)
    ordered=declarations(source,name,reference)
    result=list(helpers)
    for _,s in ordered:
        result.extend((family+'-declarations',s,h) for family,_,h in helpers)
        result.append(('cpu-reference-declaration-order',s,header))
    result+=inline_helpers(source,header,reference,target_calls,clobbers=True)[:1]
    for _,s,h in list(helpers[-1:])+[('',source,header)]:
        result+=definition_order(s,h)
    return result[:6]


def definition_order(source, header):
    """One coherent after-entry layout for actual private helper bodies already supplied.

    IDO's interprocedural choices can differ when a definition precedes the caller.
    Keep prototypes before use; no helper is synthesized and the normal emitted-body
    and exact byte/data gates still decide whether the bundle is usable.
    """
    from .source_match_context import dependencies
    used=dependencies(source,header);clean=_blank(header);spans=[]
    for match in re.finditer(r'\bstatic\s+(?:(?:__inline|inline)\s+)?(?:'+ID+r'[\s*]+)+('+ID+r')\s*\(',clean):
        if match[1] not in used:continue
        span=find(header,match[1])
        if not span or span in spans:continue
        code=header[span[0]:span[1]]
        if len(code)>3000 or '#' in _blank(code):continue
        spans.append(span)
        if len(spans)>3:return []
    if not spans:return []
    private=header;definitions=[]
    for a,b in sorted(spans):definitions.append(header[a:b])
    for a,b in sorted(spans,reverse=True):
        code=header[a:b];opening=_blank(code).find('{')
        prototype=code[:opening].rstrip()+';'
        private=private[:a]+prototype+private[b:]
    return [('cpu-reference-definition-order',source+'\n'+'\n'.join(definitions)+'\n',private)]
