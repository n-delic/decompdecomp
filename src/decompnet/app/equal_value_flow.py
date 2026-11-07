"""Must-equal scalar values across explicit CFG edges, for bounded lifetime hypotheses.

Joins intersect facts from every reachable predecessor. Calls preserve only unescaped
locals. Unknown writes kill facts; no alias, memory or path-condition inference is made.
"""
import re
from collections import deque

from ..forks.csource import _blank
from . import local_flow
from .integer_repair_support import IDENT, edit, refs

FAMILIES = ('cfg_equal_value_increment', 'cfg_equal_value_alias')
LIMIT = 8
MAX_FACTS = 512
MAX_WORK = 200000
UNSIGNED = {'u8':8, 'unsigned char':8, 'u16':16, 'unsigned short':16,
            'u32':32, 'unsigned int':32}


def canonical(a, b, delta, types):
    if a==b or types.get(a)!=types.get(b):
        return None
    bits = UNSIGNED.get(types.get(a))
    if bits:
        delta %= 1 << bits
    elif delta:
        return None  # signed offsets can overflow in a differently associated expression
    if a>b:
        a,b,delta=b,a,-delta
        if bits:
            delta %= 1 << bits
    return (a,b),delta


def changed(text, names):
    return {n for n in names if re.search(r'(?<![\w.>])'+re.escape(n)+r'\s*(?:\+\+|--|(?:<<|>>|[+*/%&|^\-])?=(?!=))|(?:\+\+|--)\s*\b'+re.escape(n)+r'\b',text)}


def transfer(facts, text, types):
    """Recognize a tiny scalar expression language; all other writes invalidate facts."""
    text=text.strip().rstrip(';').strip()
    writes=changed(text,types)
    result={key:value for key,value in facts.items() if not writes.intersection(key)}
    assign=re.fullmatch(rf'({IDENT})\s*=\s*({IDENT})(?:\s*([+-])\s*1)?',text)
    update=re.fullmatch(rf'(?:\+\+\s*({IDENT})|({IDENT})\s*\+\+|--\s*({IDENT})|({IDENT})\s*--|({IDENT})\s*([+-])=\s*1)',text)
    prefix=re.fullmatch(rf'({IDENT})\s*=\s*(\+\+|--)\s*({IDENT})',text)
    dest=base=None;delta=0
    if assign:
        dest,base=assign[1],assign[2]
        delta=1 if assign[3]=='+' else -1 if assign[3]=='-' else 0
    if update or prefix:
        variable=prefix[3] if prefix else next(update[i] for i in (1,2,3,4,5) if update[i])
        step=(1 if prefix[2]=='++' else -1) if prefix else (1 if update[1] or update[2] or update[6]=='+' else -1)
        for (a,b),value in facts.items():
            if variable not in (a,b):continue
            item=canonical(a,b,value+(step if a==variable else -step),types)
            if item:result[item[0]]=item[1]
        if prefix:
            dest,base,delta=prefix[1],variable,0
            facts=result.copy()
    if dest in types and base in types and dest!=base:
        item=canonical(dest,base,delta,types)
        if item:result[item[0]]=item[1]
        for (a,b),value in facts.items():
            other=b if a==base else a if b==base else None
            if other and other!=dest:
                item=canonical(dest,other,delta+(value if a==base else -value),types)
                if item:result[item[0]]=item[1]
    elif dest==base and delta:
        for (a,b),value in facts.items():
            if dest in (a,b):
                item=canonical(a,b,value+(delta if a==dest else -delta),types)
                if item:result[item[0]]=item[1]
    return result


def macros_safe(source, header, body, names):
    """Allow simple read-only expression macros, never hidden writes/control or local aliases."""
    definitions = re.findall(r'^\s*#\s*define[ \t]+('+IDENT+r')([^\n]*)',source+'\n'+header,re.M)
    for macro,expression in definitions:
        if not re.search(r'\b'+re.escape(macro)+r'\b',body):continue
        if macro in names:return False
        if any(re.search(r'\b'+re.escape(other)+r'\b',expression) for other,_ in definitions):return False
        if expression.startswith('('):
            parameters=re.match(r'\([^()]*\)',expression)
            if not parameters:return False
            expression=expression[parameters.end():]
        if (re.search(r'[;{}#=&\\]|\+\+|--|\b(?:goto|return|break|continue|setjmp|longjmp)\b',expression)
                or re.search(r'\b'+IDENT+r'\s*\(',expression)
                or any(re.search(r'\b'+re.escape(n)+r'\b',expression) for n in names)):
            return False
    return True


def analyze(source, name, header=''):
    flow=local_flow.analyze(source,name)
    if not flow or len(flow.locals)>96 or flow.entry is None:
        return None
    # The liveness CFG deliberately overapproximates every loop. This analysis
    # can remove a proven-false do latch without assuming arbitrary conditions.
    for hit in re.finditer(r'\bdo\b',flow.part.clean[flow.part.opening:flow.part.end]):
        start=flow.part.opening+hit.end()
        body=flow.skip(start,flow.part.end)
        stop=flow.statement_end(body,flow.part.end)
        opening,end=flow.condition(stop)
        if flow.part.clean[opening+1:end-1].strip()=='0':
            node=flow.at(opening+1)
            if node:
                node.successors={i for i in node.successors if not body<=flow.nodes[i].start<stop}
    types={d['name']:d['type'] for d in flow.locals}
    if not macros_safe(source,header,flow.part.body,types):return None
    incoming=[None]*len(flow.nodes);incoming[flow.entry]={}
    pending=deque([flow.entry]);steps=work=0
    while pending:
        index=pending.popleft();node=flow.nodes[index]
        outgoing=transfer(incoming[index],flow.part.clean[node.start:node.end],types)
        work+=len(incoming[index])+len(outgoing)+1
        if len(outgoing)>MAX_FACTS or work>MAX_WORK:return None
        for nxt in node.successors:
            old=incoming[nxt]
            merged=outgoing.copy() if old is None else {k:v for k,v in old.items() if outgoing.get(k)==v}
            if old!=merged:
                incoming[nxt]=merged;pending.append(nxt)
        steps+=1
        if steps>len(flow.nodes)*128:return None
    return flow,types,incoming


def variants(source, name, header=''):
    analysis=analyze(source,name,header)
    if not analysis:return []
    flow,types,incoming=analysis
    out=alias_variants(source,flow,types,incoming,header)
    for node,facts in zip(flow.nodes,incoming):
        if not facts:continue
        text=flow.part.clean[node.start:node.end]
        hit=re.fullmatch(rf'\s*(?:\+\+\s*({IDENT})|({IDENT})\s*\+\+|({IDENT})\s*\+=\s*1)\s*;\s*',text)
        if not hit:continue
        variable=next(hit[i] for i in (1,2,3) if hit[i])
        for (a,b),delta in sorted(facts.items()):
            if variable not in (a,b) or delta!=0:continue
            earlier=b if a==variable else a
            replacement=f'{variable} = {earlier} + 1;'
            out.append((FAMILIES[0],edit(source,[(node.start,node.end,replacement)])))
    return list(dict.fromkeys(out))[:LIMIT]


def statement_span(source, start, end):
    """Drop a whole standalone line, preserving neighboring statements and comments."""
    left=source.rfind('\n',0,start)+1
    right=source.find('\n',end)
    if right<0:right=len(source)
    if not source[left:start].strip() and not source[end:right].strip():
        return left,min(right+1,len(source))
    return start,end


def alias_variants(source,flow,types,incoming,header=''):
    """Remove one scalar copy only when every remaining read has the same proven value."""
    part=flow.part
    if (re.search(r'\b(?:_asm|__asm|__builtin_[A-Za-z_]\w*|'
                  r'__attribute__|__declspec|typeof|__typeof|__typeof__|auto|decltype|using|typedef|'
                  r'try|catch|throw|__try|__except|__finally|__leave)\b',part.body)
            or re.search(r'\(\s*{',part.body)):return []
    context=_blank(source+'\n'+header)
    if '&' in part.clean[:part.opening]+part.clean[part.end:]+_blank(header):return []
    # Unexpanded headers/modules or an ampersand hidden in a type macro could
    # supply a reference parameter that this small C analysis cannot see.
    if re.search(r'^\s*#\s*(?:include|include_next|import|if|ifdef|ifndef|elif|else|endif)\b|^\s*#\s*define[^\n]*&|'
                 r'^\s*(?:export\s+)?(?:import|module)\b',context,re.M):return []
    # C++ references can escape a local without an explicit address expression.
    # Reject reference declarations/parameters and implicit-reference captures;
    # this deliberately does not infer overload types from a call's spelling.
    reference=(rf'(?:^|[;{{}}(,])\s*(?:(?:const|volatile|signed|unsigned)\s+)*'
               rf'{IDENT}(?:\s+{IDENT})?\s*(?:\(\s*)?&&?\s*(?:{IDENT})?\s*\)?\s*(?=[;,)=])')
    if re.search(reference,context,re.M) or re.search(r'\[[^\]\n]*&[^\]\n]*\]',part.body):return []
    scalar_types=ordinary_scalar_types(context)
    declarations={d['name']:d for d in flow.locals
                  if part.clean[part.opening+1:d.start()].count('{')
                  ==part.clean[part.opening+1:d.start()].count('}')}
    initialized=initialized_locals(flow)
    if initialized is None:return []
    result=[]
    for index,node in enumerate(flow.nodes):
        text=part.clean[node.start:node.end]
        copy=re.fullmatch(rf'\s*({IDENT})\s*=\s*({IDENT})\s*;\s*',text)
        if not copy:continue
        alias,base=copy.groups()
        if (alias==base or alias not in declarations or base not in declarations
                or types[alias]!=types[base] or types[alias] not in scalar_types
                or incoming[index] is None):continue
        # A removed unbraced body or labeled statement could change control flow.
        if part.clean[part.opening+1:node.start].rstrip()[-1:] not in {';','{','}'}:continue
        if any(alias in changed(part.clean[n.start:n.end],{alias})
               for i,n in enumerate(flow.nodes) if i!=index):continue
        if not {base,alias}<=initialized:continue
        uses=[h for h in refs(part,declarations[alias]) if not node.start<=h.start()<node.end]
        if not uses or len(uses)>64:continue
        pair=tuple(sorted((alias,base)))
        safe=True
        for hit in uses:
            owner=next((i for i,n in enumerate(flow.nodes) if n.start<=hit.start()<n.end),None)
            if owner is None or incoming[owner] is None or incoming[owner].get(pair)!=0:
                safe=False;break
            # Equality on entry cannot justify a replacement alongside an update
            # to its source (including unsequenced call arguments).
            n=flow.nodes[owner]
            if base in changed(part.clean[n.start:n.end],{base}):
                safe=False;break
        if not safe:continue
        declaration=declarations[alias]
        edits=[(*statement_span(source,declaration.start(),declaration.end()),''),
               (*statement_span(source,node.start,node.end),''),
               *((h.start(),h.end(),base) for h in uses)]
        result.append((FAMILIES[1],edit(source,edits)))
        if len(result)>=4:break
    return result


def ordinary_scalar_types(context):
    """Require visible conventional typedefs; a familiar name could name an aggregate."""
    primitive={'int','signed int','unsigned int','char','signed char','unsigned char',
               'short','signed short','unsigned short'}
    expected={'s8':{'signed char'},'u8':{'unsigned char'},
              's16':{'short','signed short','short int','signed short int'},
              'u16':{'unsigned short','unsigned short int'},
              's32':{'int','signed','signed int'},'u32':{'unsigned','unsigned int'}}
    # Keywords can themselves be macros. Do not trust either a declaration or
    # the apparent primitive RHS of its typedef when those tokens are replaced.
    macros=set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')\b',context,re.M))
    if macros.intersection({'signed','unsigned','int','char','short'}):return set()
    result=primitive.copy();depth=0
    pattern=r'\btypedef\s+([A-Za-z_\s]+?)\s+([su](?:8|16|32))\s*;|[{}]'
    for hit in re.finditer(pattern,context):
        if hit[0]=='{':depth+=1
        elif hit[0]=='}':depth-=1
        elif depth==0 and hit[2] not in macros and ' '.join(hit[1].split()) in expected[hit[2]]:
            result.add(hit[2])
    return result


def initialized_locals(flow):
    """Bounded must-initialization; declarations reset storage, joins intersect paths."""
    names=sorted(flow.names);bits={n:1<<i for i,n in enumerate(names)}
    reachable=set();pending=[flow.entry]
    while pending:
        i=pending.pop()
        if i in reachable:continue
        reachable.add(i);pending.extend(flow.nodes[i].successors)
    predecessors={i:set() for i in reachable}
    for i in reachable:
        for nxt in flow.nodes[i].successors:predecessors[nxt].add(i)
    full=(1<<len(names))-1
    incoming={i:full for i in reachable};outgoing=incoming.copy()
    pending=deque(sorted(reachable));queued=set(pending);work=0
    while pending:
        i=pending.popleft();queued.remove(i);node=flow.nodes[i]
        prior=full if i!=flow.entry and predecessors[i] else 0
        for p in predecessors[i]:prior&=outgoing[p]
        killed=sum(bits[n] for n in node.defines)
        assignment=re.fullmatch(rf'\s*({IDENT})\s*=(?!=).*;\s*',flow.part.clean[node.start:node.end],re.S)
        assigned=bits.get(assignment[1],0) if assignment else 0
        after=(prior&~killed)|assigned
        incoming[i]=prior
        if outgoing[i]!=after:
            outgoing[i]=after
            for nxt in node.successors:
                if nxt not in queued:pending.append(nxt);queued.add(nxt)
        work+=1+len(predecessors[i])
        if work>MAX_WORK:return None
    return {n for n in names if all(n not in flow.nodes[i].uses or incoming[i]&bits[n] for i in reachable)}
