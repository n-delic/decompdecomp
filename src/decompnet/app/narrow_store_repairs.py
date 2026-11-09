"""Typed store lifetimes and field-update folding, with explicit intermediate conversions.

These are ordinary-judge proposals. No field layout, integer width or dead value is guessed.
"""
import ast
import re

from ..forks.csource import _blank
from . import branch_call_support as support
from . import integer_abi, local_flow
from .call_lifetime_repairs import block_path
from .compiler_shape_repairs import fields
from .integer_repair_support import IDENT, edit, view

FAMILIES = ('dead_assignment_narrow_capture', 'narrow_field_update_fold', 'narrow_store_sequence')
FIELD = rf'{IDENT}(?:->|\.){IDENT}'


def declarations(part):
    return [d for d in support.DECL.finditer(part.clean, part.opening+1, part.end)
            if part.clean[:d.start()].rstrip()[-1:] in ';{}']


def declared(part, variable, pos, header):
    """Resolve an unqualified object in the enclosing lexical scopes, not a sibling arm."""
    signature=part.clean[:part.opening].rsplit('(',1)[-1]
    pattern=rf'\b({IDENT}(?:[ \t]+{IDENT})*?)[ \t]+(\*?)[ \t]*{re.escape(variable)}\b[ \t]*(?=[;=,)])'
    choices=[]
    for hit in re.finditer(pattern, signature):choices.append(([],hit[1].strip(),hit[2]))
    path=block_path(part,pos)
    for hit in re.finditer(pattern,part.clean[part.opening+1:pos]):
        start=part.opening+1+hit.start()
        scope=block_path(part,start)
        if path[:len(scope)]==scope:choices.append((scope,hit[1].strip(),hit[2]))
    if not choices:return None
    depth=max(len(c[0]) for c in choices);choices=[c for c in choices if len(c[0])==depth]
    return choices[0][1:] if len(choices)==1 else None


def field_type(part, expression, pos, header, arch, *, narrow=True):
    hit=re.fullmatch(rf'({IDENT})(->|\.)({IDENT})',expression)
    if not hit:return None
    obj=declared(part,hit[1],pos,header)
    if obj is None:
        # Only a uniquely declared external record object, never a guessed raw address.
        external=re.findall(rf'\bextern\s+({IDENT})\s+{re.escape(hit[1])}\s*;',_blank(header+'\n'+part.source[:part.opening]))
        if len(external)!=1:return None
        obj=(external[0],'')
    if re.search(r'(?m)^\s*#\s*define\s+'+re.escape(obj[0])+r'\b',header+'\n'+part.source):return None
    if (hit[2]=='->') != bool(obj[1]):return None
    members=[t for record,t,member,_ in fields(header+'\n'+part.source[:part.opening])
             if record==obj[0] and member==hit[3]]
    if len(members)!=1:return None
    return members[0] if integer_abi.scalar(members[0],header+'\n'+part.source,arch) in ({(8,True),(8,False),(16,True),(16,False)} if narrow else {(8,True),(8,False),(16,True),(16,False),(32,True),(32,False)}) else None


def scalar_type(part, variable, pos, header, arch):
    obj=declared(part,variable,pos,header)
    if not obj or obj[1]:return None
    return integer_abi.scalar(obj[0],header+'\n'+part.source,arch)


def opaque_use(part, variable, header):
    """A macro can hide a read/write/address escape that the plain C CFG cannot see."""
    definitions=dict(re.findall(r'(?m)^\s*#\s*define\s+('+IDENT+r')([^\n]*)',_blank(header+'\n'+part.source)))
    pending=list(set(re.findall(IDENT,part.body))&definitions.keys());seen=set()
    while pending:
        macro=pending.pop()
        if macro in seen:return True
        seen.add(macro)
        body=definitions[macro]
        if '\\' in body or re.search(r'\b'+re.escape(variable)+r'\b',body):return True
        if body.startswith('(') and re.search(r'\b'+re.escape(macro)+r'\s*\([^;{}]*\b'+re.escape(variable)+r'\b',part.body):return True
        pending.extend((set(re.findall(IDENT,body))&definitions.keys())-seen)
    return False


def expression_ok(part, expression, pos, header, arch):
    if not support.pure(expression) or re.search(r'\b(?:0[xX][\da-fA-F]+|\d+)[uUlL]+\b',expression):return False
    if any(len(v)>12 or int(v,16 if v.lower().startswith('0x') else 10)>2147483647 for v in re.findall(r'\b(?:0[xX][0-9a-fA-F]+|[0-9]+)\b',expression)):return False
    words=set(re.findall(r'\b'+IDENT+r'\b',expression))
    return bool(words) and all(scalar_type(part,w,pos,header,arch) in {(8,True),(16,True),(32,True)}
                              and not opaque_use(part,w,header)
                              and not re.search(rf'(?<!&)&(?!&)\s*\(*\s*{re.escape(w)}\b',part.body) for w in words)


def plain_store(part, start, end, header, arch):
    text=part.clean[start:end].strip()
    hit=re.fullmatch(rf'({FIELD})\s*=(?!=)\s*({IDENT}|\d+)\s*;',text)
    return bool(hit and field_type(part,hit[1],start,header,arch,narrow=False)
                and (hit[2].isdigit() or scalar_type(part,hit[2],start,header,arch)))


def captures(part, header, arch):
    pattern=rf'({FIELD})\s*=(?!=)\s*\(\s*({IDENT})\s*=(?!=)\s*([^;{{}}]+)\)\s*;'
    if not re.search(pattern,part.body):return []
    decls=declarations(part)
    try:flow=local_flow.Flow(part,decls)
    except (ValueError,RecursionError):return []
    result=[]
    for hit in re.finditer(pattern,part.clean[part.opening+1:part.end-1]):
        start=part.opening+1+hit.start();end=part.opening+1+hit.end()
        node=flow.at(start)
        if not node or node.start!=start or node.end!=end:continue
        variable=hit[2];typ=field_type(part,hit[1],start,header,arch)
        if not typ or scalar_type(part,variable,start,header,arch)!=(32,True):continue
        if opaque_use(part,variable,header):continue
        if sum(d['name']==variable for d in decls)!=1 or variable not in flow.names or any(variable in flow.nodes[n].live for n in node.successors):continue
        rhs=hit[3].strip()
        if not expression_ok(part,rhs,start,header,arch):continue
        # Stage only across adjacent ordinary stores; no calls, labels or unknown memory reads.
        first=start;last=end
        for _ in range(2):
            previous=next((n for n in flow.nodes if n.end<=first and not part.clean[n.end:first].strip()
                           and plain_store(part,n.start,n.end,header,arch)),None)
            if not previous:break
            first=previous.start
        for _ in range(2):
            following=next((n for n in flow.nodes if n.start>=last and not part.clean[last:n.start].strip()
                            and plain_store(part,n.start,n.end,header,arch)),None)
            if not following:break
            last=following.end
        temp='__repair_next_field'
        while re.search(rf'\b{temp}\b',header+'\n'+part.source):temp+='_'
        macros=set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')\b',header+'\n'+part.source,re.M))
        if macros.intersection(re.findall(IDENT,part.clean[first:last])):continue
        for a,b in dict.fromkeys([(first,last),(start,end)]):
            body=part.source[a:start]+hit[1]+' = '+temp+';'+part.source[end:b]
            replacement='{\n'+typ+' '+temp+' = '+rhs+';\n'+body+'\n}'
            result.append((FAMILIES[0],edit(part.source,[(a,b,replacement)])))
        if len(result)>=6:break
    return result


def interval(part, expression, pos, header, arch, seen=()):
    """Single-definition scalar intervals; branches, updates, aliases and unknown syntax fail closed."""
    expression=expression.strip()
    if len(expression)>512 or len(seen)>6:return None
    cast=re.fullmatch(rf'\(\s*({IDENT}|signed short|unsigned short)\s*\)\s*(.+)',expression,re.S)
    if cast:
        bits=integer_abi.scalar(cast[1],header+'\n'+part.source,arch)
        if bits not in {(8,True),(8,False),(16,True),(16,False)}:return None
        inner=cast[2].strip()
        if not (re.fullmatch(FIELD,inner) and field_type(part,inner,pos,header,arch)
                or re.fullmatch(IDENT,inner) and interval(part,inner,pos,header,arch,seen)):
            return None
        return (-(1<<(bits[0]-1)),(1<<(bits[0]-1))-1) if bits[1] else (0,(1<<bits[0])-1)
    if re.fullmatch(FIELD,expression):
        typ=field_type(part,expression,pos,header,arch)
        bits=integer_abi.scalar(typ,header+'\n'+part.source,arch) if typ else None
        return ((-(1<<(bits[0]-1)),(1<<(bits[0]-1))-1) if bits[1] else (0,(1<<bits[0])-1)) if bits else None
    try:tree=ast.parse(expression,mode='eval').body
    except (SyntaxError,ValueError,RecursionError):return None
    def visit(node):
        if isinstance(node,ast.Constant) and type(node.value) is int and 0<=node.value<=2147483647:return node.value,node.value
        if isinstance(node,ast.Name):
            var=node.id
            if var in seen or opaque_use(part,var,header) or re.search(rf'&\s*\(*\s*{re.escape(var)}\b',part.body):return None
            bits=scalar_type(part,var,pos,header,arch)
            if bits not in {(8,True),(16,True),(32,True)}:return None
            defs=[d for d in declarations(part) if d['name']==var]
            writes=list(re.finditer(rf'(?<![\w.>]){re.escape(var)}\s*=(?!=)\s*([^;{{}}]+);',part.body))
            if len(writes)!=1 or re.search(rf'(?<![\w.>])(?:\(\s*)*{re.escape(var)}\b(?:\s*\))*\s*(?:\+\+|--|(?:<<|>>|[+*/%&|^\-])=)|(?:\+\+|--)\s*(?:\(\s*)*{re.escape(var)}\b',part.body):return None
            write=writes[0];at=part.opening+1+write.start()
            if len(defs)!=1 or at>=pos or block_path(part,pos)[:len(block_path(part,at))]!=block_path(part,at):return None
            return interval(part,write[1],at,header,arch,seen+(var,))
        if not isinstance(node,ast.BinOp):return None
        a,b=visit(node.left),visit(node.right)
        if not a or not b:return None
        if isinstance(node.op,ast.Add):r=(a[0]+b[0],a[1]+b[1])
        elif isinstance(node.op,ast.Sub):r=(a[0]-b[1],a[1]-b[0])
        elif isinstance(node.op,ast.Div) and b[0]==b[1] and b[0]>0:
            trunc=lambda n:(abs(n)//b[0])*(-1 if n<0 else 1)
            r=(trunc(a[0]),trunc(a[1]))
        else:return None
        return r if -2147483648<=r[0]<=r[1]<=2147483647 else None
    return visit(tree)


def folds(part, header, arch):
    result=[]
    pattern=rf'(?P<field>{FIELD})\s*=\s*(?P<seed>{IDENT});\s*(?P=field)\s*=\s*(?P<a>{FIELD}|{IDENT})\s*\+\s*(?P<b>{FIELD}|{IDENT});\s*(?P=field)\s*=\s*(?P=field)\s*\+\s*(?P<n>0|[1-9][0-9]*);'
    for hit in re.finditer(pattern,part.clean[part.opening+1:part.end-1]):
        start=part.opening+1+hit.start();end=part.opening+1+hit.end()
        macros=set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')\b',header+'\n'+part.source,re.M))
        if macros.intersection(re.findall(IDENT,hit[0])):continue
        typ=field_type(part,hit['field'],start,header,arch)
        if not typ or (hit['a']==hit['field'])==(hit['b']==hit['field']):continue
        other=hit['b'] if hit['a']==hit['field'] else hit['a']
        if not re.fullmatch(IDENT,other):continue
        a=interval(part,hit['seed'],start,header,arch);b=interval(part,other,start,header,arch)
        if not a or not b:continue
        bits=integer_abi.scalar(typ,header+'\n'+part.source,arch)
        lo,hi=(-(1<<(bits[0]-1)),(1<<(bits[0]-1))-1) if bits[1] else (0,(1<<bits[0])-1)
        if len(hit['n'])>10:continue
        n=int(hit['n'])
        if not lo<=a[0]<=a[1]<=hi or not -2147483648<=lo+b[0]<=hi+b[1]<=2147483647 or hi+n>2147483647:continue
        # Preserve the second assignment's narrowing even when the sum exceeds the field range.
        replacement=f"{hit['field']} = ({typ})({other} + {hit['seed']}) + {n};"
        result.append((FAMILIES[1],edit(part.source,[(start,end,replacement)])))
        if len(result)>=4:break
    return result


def variants(source, name, header='', arch=None):
    part=view(source,name)
    if not part or re.search(support.FORBIDDEN,part.body):return []
    # Unrelated object macros are allowed, but never macros in a rewritten statement/operand.
    captures_=captures(part,header,arch)
    result=captures_+folds(part,header,arch)
    # A bounded pair avoids requiring the search to cross a worse scalar-only intermediate.
    if captures_:
        next_part=view(captures_[0][1],name)
        paired=folds(next_part,header,arch)
        if paired:result.insert(0,(FAMILIES[2],paired[0][1]))
    return list(dict.fromkeys(result))


def machine_drafts(candidates, name, header, arch):
    """Keep each original m2c draft; spend at most three extra ordinary-judge trials."""
    remaining=3
    for family,source in candidates:
        yield family,source
        if remaining:
            from . import branch_tail_distribution, record_lifetime_recovery
            choices=branch_tail_distribution.variants(source,name,header,arch)+record_lifetime_recovery.variants(source,name,header,arch)+variants(source,name,header,arch)
            for kind,candidate in list(dict.fromkeys(choices))[:remaining]:
                yield kind,candidate
                remaining-=1
