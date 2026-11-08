"""Bounded, guarded source phases for legacy register allocation and scheduling.

No registers, addresses or scores are prescribed. Each hypothesis is compiled by the
ordinary judge. Keep declaration conversions, evaluation counts and observable reads.
"""
import re
from functools import lru_cache
from itertools import combinations

from . import integer_abi, local_flow
from .call_lifetime_repairs import block_path
from .equal_value_flow import macros_safe
from .integer_repair_support import IDENT, NUMBER, edit, integer, locals_for, view
from .pattern_repairs import close
from .pointer_pair_repairs import qualified_types

FAMILIES = ('loop_test_snapshot', 'staged_scalar_arithmetic', 'unscaled_address_snapshot', 'byte_pointer_operand_order', 'loop_constant_snapshot')
LIMIT = 4  # per family; the shared search owns the compile/time budget


@lru_cache(maxsize=1)
def facts(source, name, header, arch):
    part = view(source, name)
    context = source+'\n'+header
    if (not part or arch not in integer_abi.ARCHES or len(source)+len(header)>150000
            or re.search(r'\b(?:_Atomic|asm|__asm__|setjmp|longjmp|typeof|__typeof__|_Generic|__LINE__|__FILE__|__COUNTER__)\b', context)
            or not macros_safe(source, header, part.body, {d['name']: d['type'] for d in locals_for(part)})):
        return None
    macros=set(re.findall(r'^\s*#\s*define\s+('+IDENT+')',context,re.M))
    if macros & {'unsigned','char','int'}:return None
    words=set(re.findall(IDENT,part.body))
    builtin=set('volatile const unsigned signed char short int long float double void u8 u16 u32 s8 s16 s32 f32 f64 typedef extern static struct'.split())
    if words & qualified_types(context) or 'volatile' in part.body or any((set(re.findall(IDENT,m[0]))-builtin)&words
            for m in re.finditer(r'\bvolatile\b[^;{}]*;',context)):
        return None
    return part


def fresh(source, header, stem):
    while re.search(r'\b'+re.escape(stem)+r'\b', source+'\n'+header):
        stem += '_'
    return stem


def loop_snapshots(source, name, header, arch):
    """Reuse only top-level locals whose incoming values are dead on every latch edge."""
    part = facts(source, name, header, arch)
    flow = local_flow.analyze(source, name) if part else None
    if not flow:
        return []
    declarations = [d for d in flow.locals if block_path(part, d.start()) == [part.opening]]
    out = []
    for hit in re.finditer(r'\bwhile\s*\(', part.body):
        opening = part.opening+hit.end()
        stop = close(part.clean, opening)
        node = flow.at(opening+1)
        if not stop or node is None or node.start != opening+1 or node.end != stop-1:
            continue
        text = part.clean[node.start:node.end]
        # Plain scalar predicates only. In particular, never alter sizeof, a call,
        # short-circuit evaluation, an update, or a type-observing expression.
        if re.search(r'[^\w\s()&|^~<>!0-9]|&&|\|\||'+IDENT+r'\s*\(', text):
            continue
        for counter in declarations:
            var = counter['name']
            if len(re.findall(r'\b'+re.escape(var)+r'\b', text)) != 1:
                continue
            kind = integer_abi.scalar(counter['type'], source+'\n'+header, arch)
            if not kind or kind[0] != 32:
                continue
            donors = [d for d in declarations if d['name'] != var and d.end()<node.start
                      and d['name'] not in node.live
                      and integer_abi.scalar(d['type'], source+'\n'+header, arch) == kind
                      and any(d['name'] in n.uses and n.start<node.start for n in flow.nodes)]
            donors.sort(key=lambda d:max(n.start for n in flow.nodes if d['name'] in n.uses and n.start<node.start), reverse=True)
            match = re.search(r'\b'+re.escape(var)+r'\b', text)
            a, b = node.start+match.start(), node.start+match.end()
            for group in [(d,) for d in donors[:3]]+list(combinations(donors[:3], 2)):
                expression = '('+' = '.join([d['name'] for d in group]+[var])+')'
                out.append((FAMILIES[0], edit(source, [(a, b, expression)])))
    return list(dict.fromkeys(out))[:LIMIT]


def scalar_stages(source, name, header, arch):
    """Split a homogeneous 32-bit multiply/add without introducing a new conversion."""
    part = facts(source, name, header, arch)
    if not part:
        return []
    declarations = {d['name']: d for d in locals_for(part)}
    out = []
    pattern = re.compile(rf'\b(?P<dst>{IDENT})\s*=\s*\(\s*(?P<left>{IDENT})\s*\*\s*(?P<scale>{NUMBER})\s*\)\s*(?P<op>[+|])\s*(?P<right>{IDENT})\s*;')
    for hit in pattern.finditer(part.clean, part.opening+1, part.end-1):
        dst, left, right = (declarations.get(hit[k]) for k in ('dst','left','right'))
        if not all((dst,left,right)) or hit['dst'] == hit['right']:
            continue  # staging would overwrite a RHS value before reading it
        if part.clean[:hit.start()].rstrip()[-1:] not in {';','{','}'}:
            continue
        kinds = [integer_abi.scalar(d['type'], source+'\n'+header, arch) for d in (dst,left,right)]
        if (not kinds[0] or kinds[0][0] != 32 or len(set(kinds)) != 1
                or re.search('[uUlL]',hit['scale']) or not 0<integer(hit['scale'])<0x80000000):
            continue
        scope = block_path(part, hit.start())
        if any(d.end()>hit.start() or scope[:len(block_path(part,d.start()))] != block_path(part,d.start()) for d in (dst,left,right)):
            continue
        for boundary in (' ', '\n'):
            replacement = f'{hit["dst"]} = {hit["left"]} * {hit["scale"]};{boundary}{hit["dst"]} = {hit["dst"]} {hit["op"]} {hit["right"]};'
            out.append((FAMILIES[1], edit(source, [(hit.start(),hit.end(),replacement)])))
    # An explicit 32-bit conversion followed by OR may be staged in-place.
    # The cast fixes the intermediate width; no implicit narrow/float conversion is added.
    for hit in re.compile(rf'\b(?P<dst>{IDENT})\s*=(?!=)\s*(?P<rhs>[^;{{}}]+);').finditer(part.clean,part.opening+1,part.end-1):
        d=declarations.get(hit['dst'])
        if not d or part.clean[:hit.start()].rstrip()[-1:] not in {';','{','}'}:continue
        if integer_abi.scalar(d['type'],source+'\n'+header,arch) not in {(32,True),(32,False)}:continue
        rhs=hit['rhs'].strip()
        while rhs.startswith('(') and close(rhs,0)==len(rhs):rhs=rhs[1:-1].strip()
        depth=0;cut=None
        for i,ch in enumerate(rhs):
            depth+=(ch=='(')-(ch==')')
            if ch=='|' and depth==0:cut=i;break
        if cut is None or not re.fullmatch(NUMBER,rhs[cut+1:].strip()):continue
        left=rhs[:cut].strip()
        while left.startswith('(') and close(left,0)==len(left):left=left[1:-1].strip()
        cast=re.match(r'\(\s*([^()]+)\s*\)',left)
        if not cast or integer_abi.scalar(cast[1].strip(),source+'\n'+header,arch) not in {(32,True),(32,False)}:continue
        expression=left[cast.end():]
        if expression.strip().strip('() ') == hit['dst']:continue
        if re.search(r'[,;{}=?:]|\+\+|--',expression):
            continue
        # Casts nested inside the conversion are allowed, ordinary calls are not.
        if any(m[1] not in {'s32','u32','int','unsigned','signed'} for m in re.finditer(r'\b('+IDENT+r')\s*\(',expression)):
            continue
        for boundary in (' ','\n'):
            replacement=f'{hit["dst"]} = {rhs[:cut].strip()};{boundary}{hit["dst"]} = {hit["dst"]} | {rhs[cut+1:].strip()};'
            out.append((FAMILIES[1],edit(source,[(hit.start(),hit.end(),replacement)])))
    return list(dict.fromkeys(out))[:LIMIT]


def byte_arrays(part, header='', arch='mips'):
    """Require an ordinary byte array declaration; no pointer-valued global reads."""
    return {m['name'] for m in re.finditer(rf'\b(?:extern\s+)?(?P<type>u8|unsigned\s+char|char)\s+(?P<name>{IDENT})\s*\[\s*(?:\d+)?\s*\]\s*;', part.clean) if m.start()<part.opening and (m['type']=='char' or integer_abi.scalar(m['type'],part.source+'\n'+header,arch)==(8,False))}


def address_snapshots(source, name, header, arch):
    part = facts(source, name, header, arch)
    if not part or arch != 'mips':
        return []  # byte-address arithmetic here assumes the adapter's 32-bit pointer ABI
    declarations = {d['name']: d for d in locals_for(part)}
    arrays = byte_arrays(part,header,arch)
    out = []
    pattern = re.compile(rf'\b(?P<dst>{IDENT})\s*=\s*\(\s*(?P<type>{IDENT})\s*\*\s*\)\s*\(\s*\(\s*(?P<base>{IDENT})\s*\+\s*(?P<offset>{NUMBER})\s*\)\s*\+\s*\(\s*\(\s*(?P<a>{IDENT})\s*\+\s*(?P<b>{IDENT})\s*\)\s*\*\s*(?P<scale>[1248])\s*\)\s*\)\s*;')
    for hit in pattern.finditer(part.clean, part.opening+1, part.end-1):
        a,b = declarations.get(hit['a']),declarations.get(hit['b'])
        if not a or not b or hit['base'] not in arrays:
            continue
        if part.clean[:hit.start()].rstrip()[-1:] not in {';','{','}'}:
            continue
        # Do not capture an escaped pointer, a parameter, a shadow, or a member.
        pointer = re.findall(rf'\b{IDENT}\s*\*\s*{re.escape(hit["dst"])}\s*;', part.body)
        if len(pointer)!=1 or re.search(r'&\s*'+re.escape(hit['dst'])+r'\b',part.body):
            continue
        kind = integer_abi.scalar(a['type'],source+'\n'+header,arch)
        other=integer_abi.scalar(b['type'],source+'\n'+header,arch)
        if not kind or kind[0]!=32 or not other or other[0]!=32:
            continue
        scope=block_path(part,hit.start())
        if any(d.end()>hit.start() or scope[:len(block_path(part,d.start()))]!=block_path(part,d.start()) for d in (a,b)):
            continue
        index=fresh(source,header,'__repair_unscaled_index')
        base=fresh(source,header,'__repair_byte_table')
        # Braced statement allows C89 declarations without widening their scope.
        index_type='int' if kind[1] and other[1] else 'unsigned int'
        replacement=(f'{{ {index_type} {index}; unsigned char *{base};\n'
                     f'{index} = {hit["a"]} + {hit["b"]};\n'
                     f'{base} = (unsigned char *)({hit["base"]} + {hit["offset"]});\n'
                     f'{hit["dst"]} = ({hit["type"]} *)({base} + {index} * {hit["scale"]}); }}')
        out.append((FAMILIES[2],edit(source,[(hit.start(),hit.end(),replacement)])))
    return out[:LIMIT]


def pointer_operands(source, name, header, arch):
    part=facts(source,name,header,arch)
    if not part or arch!='mips':return []
    declarations={d['name']:d for d in locals_for(part)}
    pointers={m[2] for m in re.finditer(rf'\b(u8|unsigned\s+char|char)\s*\*\s*({IDENT})\s*;',part.body)
              if m[1]=='char' or integer_abi.scalar(m[1],source+'\n'+header,arch)==(8,False)}
    pointers={p for p in pointers if len(re.findall(r'\b'+IDENT+r'\s*\*\s*'+re.escape(p)+r'\s*[;=,]',part.body))==1}
    out=[]
    pattern=re.compile(rf'\b(?P<dst>{IDENT})\s*=\s*\(\s*(?P<type>{IDENT})\s*\*\s*\)\s*\(\s*(?P<base>{IDENT})\s*\+\s*(?P<index>{IDENT})\s*\)\s*;')
    for hit in pattern.finditer(part.clean,part.opening+1,part.end-1):
        decl=declarations.get(hit['index'])
        kind=integer_abi.scalar(decl['type'],source+'\n'+header,arch) if decl else None
        if (hit['base'] not in pointers or not kind or kind[0]>32 or hit['dst']==hit['base']
                or part.clean[:hit.start()].rstrip()[-1:] not in {';','{','}'}):continue
        # Integer-address representation is a MIPS hypothesis, not a portable-C rewrite.
        replacement=f'{hit["dst"]} = ({hit["type"]} *)((unsigned int){hit["index"]} + (unsigned int){hit["base"]});'
        out.append((FAMILIES[3],edit(source,[(hit.start(),hit.end(),replacement)])))
    return out[:LIMIT]


def constant_snapshots(source,name,header,arch):
    part=facts(source,name,header,arch)
    if not part:return []
    flow=local_flow.analyze(source,name)
    if not flow:return []
    arrays=byte_arrays(part,header,arch)
    constants={m['name']:m['type'] for m in re.finditer(
        rf'\b(?:extern\s+)?const\s+(?P<type>double|float|f32|f64)\s+(?P<name>{IDENT})\s*;',source+'\n'+header) if m['type'] not in qualified_types(source+'\n'+header)}
    out=[]
    # A do-loop or an already explicit backedge gives a real preheader. No load
    # from mutable storage is cached: only array addresses and declared const FP.
    loops=[]
    for hit in re.finditer(r'\bdo\s*{',part.body):
        start=part.opening+1+hit.start();opening=part.opening+hit.end()
        end=close(part.clean,opening,'{','}')
        if end:loops.append((start,opening+1,end-1))
    for label,index in flow.labels.items():
        jumps=[flow.nodes[i] for i,key in flow.gotos if key==label]
        if len(jumps)==1 and jumps[0].start>flow.nodes[index].end:
            start=flow.nodes[index].start;loops.append((start,flow.nodes[index].end,jumps[0].start))
    for start,a,b in loops[:2]:
        if block_path(part,start)!=[part.opening]:continue
        for var in sorted(arrays)+sorted(constants):
            hits=list(re.finditer(r'\b'+re.escape(var)+r'\b',part.clean[a:b]))
            if len(hits)<2 or re.search(r'\bsizeof\b|&\s*'+re.escape(var)+r'\b',part.clean[a:b]):continue
            if re.search(r'\b'+IDENT+r'\s+\**\s*'+re.escape(var)+r'\s*(?=[;=,\[])',part.body):continue
            if var in arrays and any(not re.match(r'\s*(?:[+\[]|-(?!>))',part.clean[a+h.end():]) for h in hits):continue
            token=fresh(source,header,'__repair_loop_constant')
            kind=(('char' if re.search(r'\bchar\s+'+re.escape(var)+r'\s*\[',part.clean) else 'unsigned char')+' *') if var in arrays else constants[var]
            # Insert ordinary declarations before the first statement for C89.
            changes=[(part.opening+1,part.opening+1,f'\n{kind} {token};\n')]
            changes += [(a+h.start(),a+h.end(),token) for h in hits]
            positions=[start]
            earlier=[n for n in flow.nodes if n.end<=start and block_path(part,n.start)==[part.opening]
                     and re.fullmatch(r'\s*'+IDENT+r'\s*=(?!=)[^;{}]+;\s*',part.clean[n.start:n.end])]
            if earlier:positions.append(sorted(earlier,key=lambda n:n.start)[-min(5,len(earlier))].start)
            for pos in dict.fromkeys(positions):
                out.append((FAMILIES[4],edit(source,changes+[(pos,pos,f'{token} = {var};\n')])))
                if len(out)>=LIMIT:return out
    return out


def variants(source, name, header='', arch=None):
    return (loop_snapshots(source,name,header,arch)+scalar_stages(source,name,header,arch)
            +address_snapshots(source,name,header,arch)+pointer_operands(source,name,header,arch)
            +constant_snapshots(source,name,header,arch))
