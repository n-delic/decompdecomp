"""Try a consumed scalar as a constant-store lifetime inside a one-shot scope."""
import re

from . import local_flow
from .integer_repair_support import IDENT, NUMBER, edit, locals_for, refs, view
from .pattern_repairs import close
from .pointer_pair_repairs import qualified_types

FAMILY = 'constant_store_scope'
LIMIT = 4


def variants(source, name, header=''):
    part = view(source, name)
    flow = local_flow.analyze(source, name)
    if not part or not flow:
        return []
    context = source+'\n'+header
    if '__LINE__' in context:
        return []
    qualified = qualified_types(re.sub(r'\bconst\b', 'volatile', context))
    declarations = {d['name']: d for d in locals_for(part)
                    if d['type'] in {'s32', 'u32', 'int', 'signed int', 'unsigned int'}
                    and d['type'] not in qualified}
    directives = re.findall(r'^\s*#.*$', context, re.M)
    if any(line.rstrip().endswith('\\') for line in directives):
        return []
    result = []
    pattern = rf'\b({IDENT})\s*=\s*([-+]?\s*{NUMBER})\s*;\s*({IDENT}\s*->\s*{IDENT})\s*=\s*\1\s*;'
    for hit in re.finditer(pattern, part.body):
        origin = hit[1]
        if origin not in declarations or origin not in flow.names:
            continue
        start, stop = part.opening+1+hit.start(), part.opening+1+hit.end()
        assignment = flow.at(start)
        store = flow.at(stop-1)
        if (not assignment or not store or assignment is store
                or part.clean[part.opening+1:start].rstrip()[-1:] not in {';', '{', '}'}):
            continue
        if any(n is not assignment and flow.nodes.index(store) in n.successors for n in flow.nodes):
            continue
        if origin in set().union(*(flow.nodes[n].live for n in store.successors)):
            continue
        candidates = []
        for variable, decl in declarations.items():
            if variable not in flow.names or variable == origin or decl['type'] != declarations[origin]['type'] or decl.end() >= start:
                continue
            scopes = [a for a in range(part.opening, decl.start()) if part.clean[a] == '{'
                      and (close(part.clean, a, '{', '}') or 0) > decl.end()]
            if not scopes or (close(part.clean, max(scopes), '{', '}') or 0) <= stop:
                continue
            previous = [r.start() for r in refs(part, decl) if r.start() < start]
            if not previous or not flow.dead(variable, start):
                continue
            tokens = {origin, variable, decl['type']} | set(re.findall(IDENT, hit[3]))
            if any(set(re.findall(IDENT, line)) & tokens for line in directives):
                continue
            candidates.append((max(previous), variable))
        for _, variable in sorted(candidates, reverse=True)[:2]:
            replacement = f'do {{{variable}={hit[2]};{hit[3]}={variable};}}while(0);'
            result.append((FAMILY, edit(source, [(start, stop, replacement)])))
            if len(result) >= LIMIT:
                return result
    return result
