"""Share proven-disjoint integer storage, preserving the replaced local's conversions.

Compiler lines rank hypotheses only. CFG liveness and dominating definitions establish
eligibility; the ordinary judge alone establishes a match. No register is prescribed.
"""
import hashlib
import re

from . import integer_abi, local_flow, repair_localisation
from .allocator_call_guards import dominators
from .call_lifetime_repairs import block_path
from .integer_repair_support import edit, refs

FAMILY = 'scalar_storage_reuse'
FAMILIES = (FAMILY,)
LIMIT = 12


def contains(wide, narrow):
    if wide is None or narrow is None:return False
    a, signed_a = wide
    b, signed_b = narrow
    return a >= b if signed_a == signed_b else signed_a and a > b


def initialized(flow, decl, dom):
    name = decl['name']
    definitions = [i for i,n in enumerate(flow.nodes) if name in n.defines
                   and not n.start <= decl.start() < n.end]
    return bool(definitions) and all(name not in n.uses or any(j != i and j in dom.get(i,set()) for j in definitions)
                                     for i,n in enumerate(flow.nodes))


def variants(source, name, header='', arch=None, locations=None):
    flow = local_flow.analyze(source,name)
    if not flow or arch not in integer_abi.ARCHES:return []
    part = flow.part
    context = header+'\n'+source
    from .equal_value_flow import macros_safe
    if not macros_safe(source,header,part.body,{d['name']:d['type'] for d in flow.locals}):return []
    # Macro aliases, type-observing expressions and source-dependent expansion need
    # a richer parser. Keep ordinary unrelated macros opaque, but never rewrite them.
    macros = re.findall(r'(?m)^\s*#\s*define(?:[^\n\\]|\\[^\n]|\\\n)*',context)
    if re.search(r'\b(?:__LINE__|__COUNTER__|__FILE__|typeof|__typeof__|_Generic)\b',context):return []
    declarations = [d for d in flow.locals if block_path(part,d.start()) == [part.opening]]
    dom = dominators(flow)
    declarations = [d for d in declarations if refs(part,d) and initialized(flow,d,dom)
                    and not any(re.search(r'\b'+re.escape(d['name'])+r'\b',m) for m in macros)]
    relevant = repair_localisation.lines(source,locations)
    registers=((locations or {}).get('mapping') or {}).get('scalar_registers',{}) if (
        isinstance(locations,dict) and locations.get('source_sha256')==hashlib.sha256(source.encode()).hexdigest()) else {}
    def target_register(decl):
        nodes=[n for n in flow.nodes if n.defines=={decl['name']} and not n.start <= decl.start() < n.end]
        values={registers.get(str(source.count('\n',0,n.start)+1)) for n in nodes}
        values.discard(None)
        return next(iter(values)) if len(values)==1 else None
    choices = []
    for donor in declarations:
        wide = integer_abi.scalar(donor['type'],context,arch)
        if not wide or wide[0] != 32:continue
        for old in declarations:
            narrow = integer_abi.scalar(old['type'],context,arch)
            if (donor is old or not contains(wide,narrow) or (wide != narrow and not wide[1])
                    or not local_flow.disjoint(flow,donor,old)):continue
            variable = old['name']
            # Keeping the declaration avoids an unrelated debug-line/declaration-order
            # mutation. It becomes unused; the compiler remains free to allocate storage.
            uses = refs(part,old)
            definitions=[n for n in flow.nodes if variable in n.defines and not n.start <= old.start() < n.end]
            if len(definitions)!=1:continue
            # Prefer a value phase over changing branch selectors/loop conditions.
            # Reusing selectors is a separate family; it can crowd this bounded lane.
            if any(not part.clean[n.start:n.end].rstrip().endswith(';') for n in flow.nodes if variable in n.uses):continue
            if any(re.search(r'\b(?:sizeof|_Alignof)\s*\(?\s*'+re.escape(variable)+r'\b',part.clean[n.start:n.end])
                   for n in flow.nodes):continue
            changes = []
            covered = set()
            safe = True
            for hit in uses:
                node = flow.at(hit.start())
                if node is None:safe=False;break
                text = part.clean[node.start:node.end]
                writes = re.search(r'\b'+re.escape(variable)+r'\s*(?:\+\+|--|(?:<<|>>|[+*/%&|^\-])?=(?!=))|(?:\+\+|--)\s*\b'+re.escape(variable)+r'\b',text)
                if not writes:continue
                assignment = re.fullmatch(r'\s*'+re.escape(variable)+r'\s*=(?!=)\s*(?P<rhs>[^;{}]+);\s*',text)
                if not assignment or variable in node.uses:safe=False;break
                start,end=node.start+assignment.start('rhs'),node.start+assignment.end('rhs')
                rhs=source[start:end].rstrip()
                # A narrow value must be normalized at the original assignment, not
                # merely at a later call: promotions and subsequent uses depend on it.
                replacement = rhs if wide == narrow else f'({old["type"]})({rhs})'
                changes.append((start,end,replacement))
                changes.append((hit.start(),hit.end(),donor['name']))
                covered.update(h.start() for h in uses if node.start <= h.start() < node.end)
            if not safe:continue
            changes += [(h.start(),h.end(),donor['name']) for h in uses if h.start() not in covered]
            candidate=edit(source,list(dict.fromkeys(changes)))
            if candidate==source:continue
            touched={source.count('\n',0,h.start())+1 for h in uses+refs(part,donor)}
            # Fresh compiler associations guide the shortlist without removing broad
            # exploration when mappings are absent/stale or allocation moved elsewhere.
            affinity=target_register(donor) is not None and target_register(donor)==target_register(old)
            rank=(not affinity,not bool(touched & relevant), wide == narrow,
                  -sum(donor['name'] in n.uses for n in flow.nodes),donor.start(),old.start())
            choices.append((rank,candidate))
    return [(FAMILY,s) for s in dict.fromkeys(s for _,s in sorted(choices,key=lambda p:p[0]))][:LIMIT]
