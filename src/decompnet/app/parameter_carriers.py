"""Permute equal-width scalar storage after every incoming value is dead.

This changes allocation opportunities, not parameter declarations or the ABI. A
simultaneous, bijective rewrite avoids capturing an overwritten pointer/value.
"""
import itertools
import re

from . import integer_abi, local_flow
from .allocator_call_guards import dominators
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, INTEGER, edit, locals_for, refs, split_args, split_plain_declarations, view
from .scalar_storage_reuse import initialized

FAMILY = 'parameter_carriers'
FAMILIES = (FAMILY,)
LIMIT = 30


def variants(source, name, header='', arch=None):
    source=split_plain_declarations(source,name,header)
    part = view(source, name)
    if not part or arch not in integer_abi.ARCHES:return []
    # A textual suffix must also be a CFG phase: no entry through a backedge,
    # label, jump, macro expansion, escaped address or source-sensitive operator.
    if re.search(r'\b(?:for|while|do|goto|switch|case|sizeof|_Alignof|typeof|__typeof__|__LINE__|__COUNTER__)\b',part.body):return []
    signature = re.search(r'\b'+re.escape(name)+r'\s*\(([^{};]*)\)\s*$',part.clean[:part.opening])
    if not signature:return []
    declaration = re.compile(rf'\s*(?P<type>{INTEGER})\s+(?P<name>{IDENT})\s*')
    parameters = []
    for lo,hi in split_args(signature[1]):
        param = declaration.fullmatch(part.clean,signature.start(1)+lo,signature.start(1)+hi)
        if param and integer_abi.scalar(param['type'],header+'\n'+source,arch)==(32,True):parameters.append(param)
    locals_ = [d for d in locals_for(part) if block_path(part,d.start())==[part.opening]
               and integer_abi.scalar(d['type'],header+'\n'+source,arch)==(32,True)]
    flow = local_flow.analyze(source,name,declarations=locals_+parameters)
    if not flow:return []
    from .equal_value_flow import macros_safe
    if not macros_safe(source,header,part.body,flow.names):return []
    dom = dominators(flow)
    locals_ = [d for d in locals_ if d['name'] in flow.names and initialized(flow,d,dom)]
    macros = '\n'.join(line for line in (header+'\n'+source).splitlines() if line.lstrip().startswith('#'))
    if re.search(r'__LINE__|__COUNTER__|__FILE__|typeof|_Generic',macros):return []
    aliases = re.findall(r'\btypedef[^;]*?\b('+IDENT+r')\s*;',header+'\n'+source)
    types = INTEGER+'|void|float|double|'+ '|'.join(map(re.escape,aliases)) if aliases else INTEGER+'|void|float|double'
    def unshadowed(decl):
        pattern = rf'\b(?:(?:const|volatile|static|register)\s+)*(?:{types}|(?:struct|union|enum)\s+{IDENT})\s*\**\s*'+re.escape(decl['name'])+r'\s*(?=[;=,\[])'
        hits = list(re.compile(pattern).finditer(part.clean,part.opening+1,part.end))
        return not hits if decl in parameters else len(hits)==1 and hits[0].start()==decl.start()
    locals_ = [d for d in locals_ if unshadowed(d)]
    result = []
    for param in parameters:
        if param['name'] not in flow.names or not unshadowed(param):continue
        pairs = list(itertools.combinations(locals_[:6],2))+[(d,) for d in locals_[:6]]
        for pair in pairs:
            slots = [param,*pair]
            names = {d['name'] for d in slots}
            if len(names)!=len(slots) or any(re.search(r'\b'+re.escape(n)+r'\b',macros) for n in names):continue
            cuts = sorted(n.start for n in flow.nodes if n.defines & names and not n.live & names
                          and all(d.end()<n.start for d in pair)
                          and block_path(part,n.start)==[part.opening])
            if not cuts:continue
            cut = cuts[0]
            uses = {d['name']:[h for h in refs(part,d) if h.start()>=cut] for d in slots}
            if any(not uses[d['name']] for d in pair):continue
            for order in itertools.permutations(slots):
                mapping = {old['name']:new['name'] for old,new in zip(slots,order)}
                if all(a==b for a,b in mapping.items()):continue
                changes = [(hit.start(),hit.end(),mapping[old]) for old,hits in uses.items() for hit in hits]
                result.append((FAMILY,edit(source,changes)))
    return list(dict.fromkeys(result))[:LIMIT]
