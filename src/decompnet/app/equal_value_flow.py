"""Must-equal scalar values across explicit CFG edges, for bounded lifetime hypotheses.

Joins intersect facts from every reachable predecessor. Calls preserve only unescaped
locals. Unknown writes kill facts; no alias, memory or path-condition inference is made.
"""
import re
from collections import deque

from . import local_flow
from .integer_repair_support import IDENT, edit

FAMILIES = ('cfg_equal_value_increment',)
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
    out=[]
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
