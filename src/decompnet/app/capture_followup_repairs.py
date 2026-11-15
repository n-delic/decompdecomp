"""Bounded hypotheses from captured successes, using existing types and known callee bodies only."""
import re

from decomprepair.integer_repair_support import IDENT, INTEGER, edit, locals_for, split_args, view
from decomprepair.pattern_repairs import close

from ..forks.csource import _blank, find

FAMILIES = ('typed_clamp_load','typed_pointer_load','loop_update_order','call_postincrement','callee_buffer_extent','known_call_signature','known_call_excess_arguments')
TYPE = rf'(?:{INTEGER}|void|float|double|f32|f64|{IDENT})(?:\s*\*)*'
PARAM = re.compile(rf'\s*(?P<type>{TYPE})\s*\b(?P<name>{IDENT})\s*')


def callee_evidence(net, proj, fn, source):
    """Generation-only evidence; never inserted into compiler headers or shared source."""
    from ..candidate_scope import callees
    if not hasattr(net,'store'):
        return ''
    rows = callees(net,proj,fn,source)
    parts = []
    for row in rows:
        text = row['matched_source'] or ''
        span = find(text,row['name']) if text else None
        text = text[span[0]:span[1]] if span else ''
        if text and sum(map(len,parts))+len(text) <= 32000:
            parts.append(text)
    return '\n'.join(parts)


def definitions(context):
    """Plain C definitions with explicit named parameters; ambiguous definitions are excluded."""
    clean = _blank(context)
    result = {}
    for hit in re.finditer(rf'\b({IDENT})\s*\(([^(){{}};]*)\)\s*{{', clean):
        if hit[1] in {'if','for','while','switch'}:
            continue
        end = close(clean,hit.end()-1,'{','}')
        params = [PARAM.fullmatch(hit[2][a:b]) for a,b in split_args(hit[2])]
        if not end or not params or not all(params) or '...' in hit[2]:
            continue
        item = ([(p['type'].strip(),p['name']) for p in params],clean[hit.end():end-1])
        result[hit[1]] = item if hit[1] not in result else None
    return {k:v for k,v in result.items() if v}


def member_type(part, header, expression):
    records = {}
    combined = _blank(header+'\n'+part.source[:part.opening])
    for hit in re.finditer(rf'\btypedef\s+struct(?:\s+{IDENT})?\s*{{([^{{}}]*)}}\s*({IDENT})\s*;',combined):
        if hit[2] in records:
            records[hit[2]] = {}
            continue
        fields = {}
        for field in re.finditer(rf'(?:^|(?<=;))\s*({TYPE})\s*\b({IDENT})\s*;',hit[1]):
            fields[field[2]] = field[1].strip()
        records[hit[2]] = fields
    bits = re.split(r'\s*->\s*',expression)
    declarations = list(re.finditer(rf'\b({IDENT})\s*\*\s*{re.escape(bits[0])}\b',part.clean))
    if len(declarations)!=1:
        return None
    typ = declarations[0][1]+'*'
    for member in bits[1:]:
        if not typ.endswith('*'):
            return None
        typ = records.get(typ[:-1].strip(),{}).get(member)
        if typ is None:return None
    return typ


def variants(source, name, header='', callees='', _masked=False):
    part = view(source,name)
    context = header+'\n'+source+'\n'+callees
    if not part or re.search(r'\b(?:volatile|restrict|asm|__asm__|union)\b',_blank(context)):
        return []
    from decomprepair.pointer_pair_repairs import qualified_types
    if qualified_types(_blank(context)) & set(re.findall(IDENT,part.clean)):
        return []
    if re.search(r'^\s*#',_blank(context),re.M):
        if _masked:return []
        from decomprepair import macro_repairs
        safe_header,_,_ = macro_repairs.prepare(header+'\n'+callees)
        if safe_header is None:return []
        return macro_repairs.variants(source,name,lambda masked:variants(masked,name,safe_header,'',True),header+'\n'+callees)
    out = []
    def add(family, edits):
        proposal = edit(source,edits)
        if proposal != source and (family,proposal) not in out:
            out.append((family,proposal))
    # Preserve the actual field type; evaluate the ordinary field once at this point.
    scalar = {d['name']:d for d in locals_for(part)}
    clamp = re.compile(rf'\bif\s*\(\s*(?P<v>{IDENT})\s*(?P<op><|>)\s*(?P<field>{IDENT}\s*->\s*{IDENT}(?:\s*->\s*{IDENT})*)\s*\)\s*(?P=v)\s*=\s*(?P=field)\s*;')
    for hit in list(clamp.finditer(part.clean,part.opening,part.end))[:3]:
        if hit['v'] not in scalar or '__repair_field' in source:
            continue
        field_type = member_type(part,header,hit['field'])
        if not field_type or not re.fullmatch(INTEGER,field_type):
            continue
        # Insert only at a statement boundary, leaving the original control flow in place.
        prefix = part.clean[part.opening+1:hit.start()].rstrip()
        if prefix and prefix[-1] not in ';{}':
            continue
        add(FAMILIES[0],[(part.opening+1,part.opening+1,f'\n{field_type} __repair_field;'),
                        (hit.start(),hit.end(),f'__repair_field = {hit["field"]}; if ({hit["v"]} {hit["op"]} __repair_field) {hit["v"]} = __repair_field;')])
    # Stage a typed record pointer once, in a lexical block which cannot carry it across a call/loop.
    pointer = re.compile(rf'\b(?P<type>{IDENT})\s*\*\s*(?P<var>{IDENT})\s*;')
    for decl in list(pointer.finditer(part.clean,part.opening+1,part.end))[:8]:
        typ,var = decl['type'],decl['var']
        if re.search(rf'\b{re.escape(typ)}\b',_blank(header+source[:part.opening])) is None:
            continue
        pattern = re.compile(rf'(?<=[;{{}}])\s*{var}\s*=\s*&\s*(?P<base>{IDENT})\s*->\s*(?P<field>{IDENT})\s*\[\s*(?P<index>{IDENT})\s*\]\s*;')
        for hit in list(pattern.finditer(part.clean,decl.end(),part.end))[:2]:
            if re.sub(r'\s+','',member_type(part,header,hit['base']+'->'+hit['field']) or '') != typ+'*' or '__repair_list' in source:
                continue
            assignment = f'{{ {typ} *__repair_list = {hit["base"]}->{hit["field"]}; {var} = &__repair_list[{hit["index"]}]; }}'
            add(FAMILIES[1],[(hit.start(),hit.end(),assignment)])
    # Two independent unescaped local induction updates can be emitted in either comma order.
    local_names = set(scalar)
    signature = part.clean[:part.opening]
    local_names |= set(re.findall(rf'\*\s*({IDENT})\s*(?=[,)])',signature))
    local_names |= set(re.findall(rf'\*\s*({IDENT})\s*;',part.body))
    for hit in re.finditer(rf'\bfor\s*\([^;{{}}]*;[^;{{}}]*;\s*({IDENT})\+\+\s*,\s*({IDENT})\+\+\s*\)',part.clean[part.opening:part.end]):
        a,b=hit[1],hit[2]
        if a==b or not {a,b}<=local_names or any(re.search(rf'&\s*{v}\b',part.body) for v in (a,b)):
            continue
        start=part.opening+hit.start(1);end=part.opening+hit.end(2)+2
        add(FAMILIES[2],[(start,end,f'{b}++, {a}++')])
    # Exactly one simple pointer argument, then increment that same unescaped local after the call.
    call_inc = re.compile(rf'(?<=[;{{}}])\s*(?P<dest>\*\s*{IDENT}(?:\+\+)?)\s*=\s*(?P<call>{IDENT})\(\s*(?P<p>{IDENT})\s*\)\s*;\s*(?P=p)\+\+\s*;')
    for hit in list(call_inc.finditer(part.clean,part.opening,part.end))[:3]:
        p=hit['p'];dest=hit['dest'].replace(' ','')
        dest_name=dest.lstrip('*').removesuffix('++')
        if p not in local_names or dest_name not in local_names or any(re.search(rf'&\s*\(*\s*{v}\b',part.body) for v in (p,dest_name)) or re.search(rf'\b{p}\b',dest):
            continue
        # Preserve destination update timing by spelling it after the call assignment.
        tail = ''
        if dest.endswith('++'):
            dest=dest[:-2];tail=dest[1:]+'++;'
        add(FAMILIES[3],[(hit.start(),hit.end(),f'{dest} = {hit["call"]}({p}++); {tail}')])
    known = definitions(context)
    from decomprepair.repair_preflight import _PROTO
    for call in re.finditer(rf'\b({IDENT})\s*\(',part.clean[part.opening+1:part.end-1]):
        callee=call[1]
        if callee==name or callee not in known or len(re.findall(rf'\b{callee}\s*\(',part.body))!=1:
            continue
        start=part.opening+1+call.start();opening=part.opening+call.end();end=close(part.clean,opening)
        if not end:
            continue
        args=[source[opening+1+a:opening+1+b].strip() for a,b in split_args(part.clean[opening+1:end-1])]
        params,body=known[callee]
        # A contiguous, constant-index write footprint is evidence for the actual pointed-to object's extent.
        for index,arg in enumerate(args):
            if index>=len(params) or not re.fullmatch(r'&\s*'+IDENT,arg):
                continue
            var=arg.lstrip('&').strip();matches=list(re.finditer(rf'\b({INTEGER})\s+{var}\s*;',part.body))
            if len(matches)!=1 or re.search(rf'\b(?:sizeof|typeof)\s*\(?\s*{var}\b',part.body):
                continue
            typ,param=params[index]
            if re.sub(r'\s+','',typ)!=re.sub(r'\s+','',matches[0][1]+'*'):
                continue
            writes=list(re.finditer(rf'\b{re.escape(param)}\s*\[\s*(\d+)\s*\]\s*=(?!=)',body))
            indices={int(w[1]) for w in writes}
            # No aliases, dynamic indexes, nested calls, casts, loops or pointer arithmetic in the known callee.
            if not indices or indices != set(range(max(indices)+1)) or not 2<=max(indices)+1<=32:
                continue
            if len(re.findall(rf'\b{param}\b',body))!=len(writes) or re.search(r'\b(?:for|while|goto)\b|\b'+IDENT+r'\s*\(',body):
                continue
            if re.search(rf'(?:\.|->)\s*{var}\b|\bgoto\s+{var}\b|\b{var}\s*:',part.body):continue
            occurrences=list(re.finditer(rf'\b{var}\b',part.body));address_uses=list(re.finditer(rf'&\s*\(*\s*{var}\b',part.body))
            if len(address_uses)!=1 or re.search(rf'\b(?:volatile|register)\s+[^;]*\b{var}\b',part.body):
                continue
            decl=matches[0];base=part.opening+1
            edits=[(base+decl.start(),base+decl.end(),f'{decl[1]} {var}[{max(indices)+1}];')]
            for use in occurrences:
                if decl.start()<=use.start()<decl.end():continue
                pos=base+use.start()
                if part.clean[:pos].rstrip().endswith('&'):
                    amp=part.clean.rfind('&',0,pos);edits.append((amp,base+use.end(),var))
                else:edits.append((pos,base+use.end(),var+'[0]'))
            add(FAMILIES[4],edits)
        # A matched callee definition, not a score or guessed prototype, establishes arity.
        # Only drop literal trailing arguments (no reads/side effects), and update every direct site.
        prototypes=[p for p in _PROTO.finditer(part.clean[:part.opening]) if p[1]==callee]
        if (len(prototypes)==1 and len(args)>len(params) and len(params)<=8
            and not re.search(rf'^\s*#\s*define\s+{callee}\b',header+'\n'+source,re.M)
            and not re.search(rf'\b{callee}\b(?!\s*\()',part.body)):
            proto=prototypes[0]
            normalize=lambda t: re.sub(r'\s+','',t).replace('s32','int').replace('u32','unsignedint')
            declared=[proto[2][a:b].strip() for a,b in split_args(proto[2])]
            def type_only(text):
                named=PARAM.fullmatch(text)
                return named['type'] if named else text
            if (len(declared)==len(args) and all((normalize(t)==normalize(p[0]) or normalize(type_only(t))==normalize(p[0])) for t,p in zip(declared,params))
                and all(re.fullmatch(r'-?(?:0[xX][0-9a-fA-F]+|[0-9]+)[uUlL]*',a) for a in args[len(params):])
                and not re.search(rf'\b{re.escape(callee)}\b(?!\s*\()',part.clean[proto.end():part.opening])):
                add(FAMILIES[6],[(opening+1,end-1,', '.join(args[:len(params)])),
                                  (proto.start(2),proto.end(2),', '.join(t+' '+p for t,p in params))])
        # Canonical named formals provide missing arguments only when the incoming caller parameter has that name/type.
        prototypes=[p for p in _PROTO.finditer(part.clean[:part.opening]) if p[1]==callee]
        if len(prototypes)!=1 or len(args)>=len(params) or len(params)>8 or any(re.search(r'\+\+|--|(?<![=!<>])=(?!=)|\b'+IDENT+r'\s*\(',a) for a in args):
            continue
        signature=part.clean[find(source,name)[0]:part.opening]
        caller=re.search(r'\(([^()]*)\)\s*$',signature)
        caller_params=[PARAM.fullmatch(caller[1][a:b]) for a,b in split_args(caller[1])] if caller else []
        available={p['name']:p['type'].strip() for p in caller_params if p}
        def normalized(typ):
            return re.sub(r'\s+','',typ).replace('s32','int').replace('u32','unsignedint').replace('f32','float').replace('s16','short').replace('u16','unsignedshort')
        def compatible(actual,expected):
            return actual is not None and (normalized(actual)==normalized(expected) or (normalized(expected)=='void*' and actual.endswith('*')))
        def argument_type(arg, available=available):
            if arg in available:return available[arg]
            if arg.startswith('*') and arg[1:] in available and available[arg[1:]].endswith('*'):
                return available[arg[1:]][:-1].strip()
            return None
        count=len(params)-len(args)
        if not all(compatible(argument_type(arg),typ) for arg,(typ,_) in zip(args,params[count:])):
            continue
        prefix=[]
        for typ,param in params[:count]:
            choices=[n for n,t in available.items() if compatible(t,typ)]
            if param in choices:
                prefix.append(param)
            elif len(choices)==1:
                prefix.append(choices[0])
            else:break
        else:
            proto=prototypes[0]
            add(FAMILIES[5],[(opening+1,end-1,', '.join(prefix+args)),
                             (proto.start(2),proto.end(2),', '.join(t+' '+p for t,p in params))])
    return list(dict.fromkeys(out))[:16]
