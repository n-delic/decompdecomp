"""Reuse real selector, byte-pointer and sentinel phases on known 32-bit ABIs.

This is a bounded source hypothesis, never a physical-register binding. CFG liveness
protects previous values; all candidates still need the ordinary linked-byte judge.
"""
import re

from . import integer_abi, local_flow
from .allocator_call_guards import dominators
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, NUMBER, edit, integer, locals_for, refs, view
from .pattern_repairs import close

FAMILY = 'scratch_role_reuse'
FAMILIES = (FAMILY,)
LIMIT = 12
POINTER = re.compile(r'\b(?P<type>(?:unsigned\s+char|signed\s+char|[su]8)\s*\*)\s*(?P<name>'+IDENT+r')\s*;')


def safe(source, name, header, arch):
    part = view(source,name)
    context = source+'\n'+header
    if not part or arch not in integer_abi.ARCHES:return None
    if re.search(r'\b(?:__LINE__|__COUNTER__|__FILE__|typeof|__typeof__|_Generic)\b|\\\n',context):return None
    if re.search(r'\bvolatile\b',part.body):return None
    macros=set(re.findall(r'(?m)^\s*#\s*define\s+('+IDENT+r')',context))
    if macros & set(re.findall(IDENT,part.body)):return None
    return part


def unparen(text):
    text=text.strip()
    while text.startswith('(') and close(text,0)==len(text):text=text[1:-1].strip()
    return text


def selectors(part, context, arch):
    """Only an actual narrow load in a braced, standalone if; no repeated/hoisted read."""
    hits=list(re.finditer(r'\bif\s*\(',part.clean[part.opening+1:part.end]))[:12]
    for hit in hits:
        start=part.opening+1+hit.start();opening=part.opening+hit.end()
        end=close(part.clean,opening)
        if not end or part.clean[end:].lstrip()[:1]!='{':continue
        if part.clean[part.opening+1:start].rstrip()[-1:] not in ('',';','{','}'):continue
        match=re.fullmatch(r'(?P<load>.+?)\s*(?:==|!=)\s*'+NUMBER,part.clean[opening+1:end-1],re.S)
        if not match:continue
        load=unparen(match['load'])
        # Reject calls, increments, assignments and compound predicates. The cast's
        # pointee must promote losslessly to int, including unsigned halfwords.
        operand=load[1:].strip()
        if not operand.startswith('(') or close(operand,0)!=len(operand):continue
        if not load.startswith('*') or re.search(r'\+\+|--|[=,?:!&|]|\b(?:sizeof|_Alignof)\b',load):continue
        if re.search(IDENT+r'\s*\(',load):continue
        casts=re.findall(r'\(\s*([^()]+?)\s*\*\s*\)',load)
        if len(casts)!=1 or integer_abi.scalar(casts[0],context,arch) not in {(8,True),(8,False),(16,True),(16,False)}:continue
        yield start,opening+1+match.start('load'),opening+1+match.end('load'),load


def pointer_phases(source,name,header,arch):
    part=safe(source,name,header,arch)
    if not part:return []
    context=header+'\n'+source
    pointers=[d for d in POINTER.finditer(part.clean,part.opening+1,part.end)
              if block_path(part,d.start())==[part.opening]
              and part.clean[part.opening+1:d.start()].rstrip()[-1:] in ('',';','{','}')]
    result=[]
    for decl in pointers[:6]:
        var=decl['name']
        if integer_abi.scalar(decl['type'].rstrip().rstrip('*').strip(),context,arch) not in {(8,True),(8,False)}:continue
        # No shadows, escaped address, preprocessor use or type observers. Other
        # pointer operations retain their original pointer type through casts.
        if len(re.findall(r'\b'+IDENT+r'(?:\s*\*+\s*|\s+)'+re.escape(var)+r'\s*[;=,\[]',part.body))!=1:continue
        if re.search(r'(?<!&)&(?!&)\s*(?:\(\s*)*'+re.escape(var)+r'\b',part.body):continue
        if re.search(r'\b(?:sizeof|_Alignof)\s*\(?\s*'+re.escape(var)+r'\b',part.body):continue
        if any(re.search(r'\b'+re.escape(var)+r'\b',m) for m in re.findall(r'(?m)^\s*#.*',context)):continue
        for at,begin,end,load in selectors(part,context,arch):
            if at<decl.end():continue
            fresh='repair_scratch'
            while re.search(r'\b'+fresh+r'\b',context):fresh+='_'  # not a compiler/register directive
            staged=edit(source,[(decl.start(),decl.end(),source[decl.start():decl.end()]+' int '+fresh+';'),
                                (at,at,fresh+' = '+load+';\n  '),(begin,end,fresh)])
            staged_part=view(staged,name)
            pointer=next((d for d in POINTER.finditer(staged_part.clean) if d['name']==var),None)
            flow=local_flow.analyze(staged,name,declarations=locals_for(staged_part)+[pointer]) if pointer else None
            if not flow:continue
            donor=next((d for d in flow.locals if d['name']==fresh),None)
            if not donor or not local_flow.disjoint(flow,donor,pointer):continue
            definitions=[n for n in flow.nodes if var in n.defines and not n.start<=pointer.start()<n.end]
            if len(definitions)!=1:continue
            node=definitions[0]
            assignment=re.fullmatch(r'\s*'+re.escape(var)+r'\s*=\s*(?P<rhs>[^;{}]+);\s*',flow.part.clean[node.start:node.end])
            if not assignment or var in node.uses:continue
            dom=dominators(flow);definition=flow.nodes.index(node)
            if any(var in n.uses and definition not in dom.get(i,set()) for i,n in enumerate(flow.nodes)):continue
            uses=refs(flow.part,pointer)
            # All writes must be the one recognized assignment; ++/compound stores
            # are reads to the CFG and cannot silently become cast lvalues.
            if any(re.search(r'(?:\+\+|--)\s*(?:\(\s*)*'+re.escape(var)+r'\b|\b'+re.escape(var)+r'\s*\)*\s*(?:\+\+|--|(?:<<|>>|[+*/%&|^\-])=|=(?!=))',flow.part.clean[n.start:n.end]) for n in flow.nodes if n is not node):continue
            rhs=staged[node.start+assignment.start('rhs'):node.start+assignment.end('rhs')].strip()
            changes=[(pointer.start(),pointer.end(),''),(node.start,node.end,fresh+' = (int)('+rhs+');')]
            changes += [(h.start(),h.end(),'('+decl['type']+')'+fresh) for h in uses if not node.start<=h.start()<node.end]
            # Parenthesize the whole pointer cast, preserving byte arithmetic.
            changes=[(a,b,'('+c+')' if c=='('+decl['type']+')'+fresh else c) for a,b,c in changes]
            candidate=edit(staged,changes)
            # Put the single scratch in the original declaration slot. Avoid moving
            # unrelated declarations, scopes, reads or existing line boundaries.
            candidate=candidate.replace(' int '+fresh+';','int '+fresh+';',1)
            result.append((FAMILY,candidate))
            if len(result)>=6:return list(dict.fromkeys(result))
    return list(dict.fromkeys(result))[:LIMIT]


def sentinel_phases(source,name,header,arch):
    part=safe(source,name,header,arch)
    if not part:return []
    flow=local_flow.analyze(source,name)
    if not flow:return []
    dom=dominators(flow);result=[]
    donors=[d for d in flow.locals if integer_abi.scalar(d['type'],source+'\n'+header,arch)==(32,True)
            and block_path(part,d.start())==[part.opening]]
    for hit in re.finditer(r'\bif\s*\(\s*'+IDENT+r'\s*!=\s*(?P<number>'+NUMBER+r')\s*\)',part.clean[part.opening+1:part.end]):
        at=part.opening+1+hit.start();begin=part.opening+1+hit.start('number');end=part.opening+1+hit.end('number')
        literal=source[begin:end]
        if re.search('[uUlL]',literal) or not 1<integer(literal)<=0x7fffffff:continue
        if part.clean[part.opening+1:at].rstrip()[-1:] not in ('',';','{','}'):continue
        node=flow.at(begin)
        if node is None:continue
        index=flow.nodes.index(node)
        reachable=set();pending=list(node.successors)
        while pending:
            i=pending.pop()
            if i in reachable:continue
            reachable.add(i);pending.extend(flow.nodes[i].successors-reachable)
        for donor in donors:
            var=donor['name']
            if donor.end()>at or var in node.live or any(var in flow.nodes[i].defines for i in reachable):continue
            if not any(var in n.defines and n.start>=donor.end() and n.start<at for n in flow.nodes):continue
            changes=[(at,at,var+' = '+literal+';\n  '),(begin,end,var)]
            for i in reachable:
                n=flow.nodes[i]
                if index not in dom.get(i,set()):continue
                text=part.clean[n.start:n.end]
                # A plain XOR RHS constant, not a bound, case label or offset. Its
                # original unsuffixed int type is unchanged by the signed-int donor.
                for number in re.finditer(r'\^\s*(?P<number>'+re.escape(literal)+r')(?![\w.])',text):
                    if text[number.end():].lstrip().startswith(';'):
                        changes.append((n.start+number.start('number'),n.start+number.end('number'),var))
            if len(changes)>2:result.append((FAMILY,edit(source,changes)))
            if len(result)>=LIMIT:return list(dict.fromkeys(result))
    return list(dict.fromkeys(result))[:LIMIT]


def variants(source,name,header='',arch=None):
    phases=pointer_phases(source,name,header,arch)
    # Keep both the first measurable step and the bounded composition. The same
    # sentinel step can resume from a saved partially improved source next run.
    combined=[v for _,candidate in phases for v in sentinel_phases(candidate,name,header,arch)]
    return list(dict.fromkeys(combined+phases+sentinel_phases(source,name,header,arch)))[:LIMIT]


def brief(source,name,header,arch,rows):
    from .allocation_evidence import diagnose
    if not diagnose(rows,arch)['register_only']:return ''
    choices=variants(source,name,header,arch)
    if not choices:return ''
    from ..arch import get
    from .permuter_focus import normalized
    reader=get(arch).source_local_storage
    witness=reader(rows) if reader else None
    clue=''
    if witness and witness.get('kind')=='register':
        register=witness['target']
        operations=[r[1] for r in normalized(rows) if re.search(re.escape(register)+r'(?![\w])',r[1])]
        if operations:
            snippets=list(dict.fromkeys(operations[:1]+operations[-3:]))
            clue='Target '+register+' appears in: '+'; '.join(snippets)+'. '
    return clue+('Scratch-role plan: ordered instruction forms agree. Test scratch_role_reuse: stage an actual narrow condition load, '
            'then reuse its signed 32-bit temporary for a disjoint byte-pointer phase (cast back at every pointer use), '
            'then the genuinely consumed comparison/XOR constant after the pointer dies. Inspect the target register across '
            'earlier matching rows too; it ranks a hypothesis, never binds a register. Keep the compiler, real reads, scopes and '
            'unrelated line breaks fixed. Retain a partial improvement and extend it; use local variants before rewriting C. '
            'Skip exact source/build trials already recorded as ineffective; no dummy uses or wider carriers.')
