"""Known empty void(void) calls and unchanged incoming-parameter copies.

These are source hypotheses, never arity inferred from argument registers. Selected
matched C must supply the empty, explicit zero-argument contract. Only ordinary
scalar values may be discarded; no shared declarations or callee bodies change.
"""
import re

from . import local_flow, matched_prototype_repairs
from .allocator_call_guards import dominators
from .call_lifetime_repairs import block_path
from .csource import _blank, find
from .integer_repair_support import IDENT, NUMBER, calls, edit, locals_for, refs, split_args, view

CALL = 'matched_zero_argument_call'
ALIAS = 'incoming_parameter_alias'
COMBINED = 'zero_argument_parameter_alias'
FAMILIES = (CALL, ALIAS, COMBINED)
LIMIT = 12
TYPE_WORDS = {word for spelling in matched_prototype_repairs.SPELLINGS for word in spelling.split()} | {
    'void', 'const', 'volatile', 'register', 'static', 'extern', 'inline',
}


def conditional_at(text, pos):
    depth = 0
    for hit in re.finditer(r'^\s*#\s*(if|ifdef|ifndef|endif)\b', text[:pos], re.M):
        depth += -1 if hit[1] == 'endif' else 1
    return depth != 0


def parameters(part, name, context):
    signature = re.search(r'\b'+re.escape(name)+r'\s*\(([^{};()]*)\)\s*{\s*$', part.clean[:part.opening+1])
    if not signature:
        return {}
    result = {}
    for a, b in split_args(signature[1]):
        scalar = matched_prototype_repairs.scalar(signature[1][a:b], context, named=True)
        if scalar:
            hit, canonical = scalar
            if hit['name'] in result:
                return {}
            result[hit['name']] = canonical
    return result


def stable(part, variable, *, assignment=None):
    """No escapes, shadow declarations, or direct/parenthesized scalar updates."""
    body = part.body
    if re.search(r'(?<!&)&(?!&)\s*(?:\(\s*)*'+re.escape(variable)+r'\b', body):
        return False
    for hit in re.compile(r'\b'+re.escape(variable)+r'\b').finditer(part.clean, part.opening+1, part.end):
        if re.search(r'(?:\.|->)\s*$', part.clean[:hit.start()]):
            continue
        if assignment and assignment[0] <= hit.start() < assignment[1]:
            continue
        after = part.clean[hit.end():]
        if re.match(r'\s*\)*\s*(?:(?:<<|>>|[+\-*/%&|^])?=(?!=)|\+\+|--)', after):
            return False
        if re.search(r'(?:\+\+|--)\s*(?:\(\s*)*$', part.clean[:hit.start()]):
            return False
    return True


def shadowed(part, variable, context, ignore=None):
    body = part.body
    if ignore:
        lo, hi = ignore[0]-part.opening-1, ignore[1]-part.opening-1
        body = body[:lo]+' '*(hi-lo)+body[hi:]
    aliases = re.findall(r'\btypedef\s+[^;{}]+\b('+IDENT+r')\s*;', context)
    aliases += re.findall(r'}\s*('+IDENT+r')\s*;', context)
    types = matched_prototype_repairs.TYPE+'|float|double|void'
    if aliases:
        types += '|'+ '|'.join(re.escape(a) for a in set(aliases))
    if re.search(r'}\s*\**\s*'+re.escape(variable)+r'\b', body):
        return True  # Inline record/union declarators can shadow a scalar too.
    return bool(re.search(r'\b(?:(?:const|volatile|static|register)\s+)*(?:'+types
                          +r'|(?:struct|union)\s+'+IDENT+r')\s+(?:\(*\s*\**\s*)*'
                          +re.escape(variable)+r'\s*(?=[;=,\[)])', body))


def erase_span(source, start, end, compact):
    if compact:
        begin = source.rfind('\n', 0, start)+1
        stop = source.find('\n', end)
        if stop >= 0 and not source[begin:start].strip() and not source[end:stop].strip():
            return begin, stop+1, ''
        return start, end, ''
    return start, end, ''.join('\n' if c == '\n' else ' ' for c in source[start:end])


def aliases(source, name, header):
    part = view(source, name)
    if not part:
        return []
    context = _blank(source+'\n'+header)
    macros = set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')\b', context, re.M))
    if macros & TYPE_WORDS:
        return []
    formals = parameters(part, name, context)
    declarations = locals_for(part)
    flow = local_flow.analyze(source, name, declarations=declarations)
    if not flow:
        return []
    dom = dominators(flow)
    groups = []
    for decl in declarations:
        variable = decl['name']
        if variable in formals or block_path(part, decl.start()) != [part.opening]:
            continue
        hits = refs(part, decl)
        if len(hits) < 2:
            continue
        transition = flow.at(hits[0].start())
        if not transition:
            continue
        text = part.clean[transition.start:transition.end]
        assignment = re.fullmatch(r'\s*'+re.escape(variable)+r'\s*=\s*('+IDENT+r')\s*;\s*', text)
        if not assignment or assignment[1] not in formals:
            continue
        formal = assignment[1]
        typ = matched_prototype_repairs.scalar(decl['type'], context)
        if not typ or typ[1] != formals[formal]:
            continue  # An assignment conversion is observable; don't erase it.
        # Reject a formal shadow, including a local pointer or aggregate alias.
        if shadowed(part, formal, context) or shadowed(part, variable, context, (decl.start(), decl.end())):
            continue
        if not stable(part, formal) or not stable(part, variable, assignment=(transition.start, transition.end)):
            continue
        definitions = [i for i, n in enumerate(flow.nodes) if variable in n.defines
                       and not n.start <= decl.start() < n.end]
        start_index = flow.nodes.index(transition)
        if definitions != [start_index]:
            continue
        if any(variable in n.uses and i != start_index and start_index not in dom.get(i, set())
               for i, n in enumerate(flow.nodes)):
            continue
        replacement = [(h.start(), h.end(), formal) for h in hits
                       if not transition.start <= h.start() < transition.end]
        groups.append((decl, transition, replacement))
        if len(groups) == 3:
            break
    bundles = [[g] for g in groups]
    if len(groups) > 1:
        bundles.insert(0, groups)
    result = []
    for group in bundles:
        for compact in (True, False):
            edits = []
            for decl, transition, replacements in group:
                edits.extend(replacements)
                edits.append(erase_span(source, decl.start(), decl.end(), compact))
                edits.append(erase_span(source, transition.start, transition.end, compact))
            result.append(edit(source, edits))
    return list(dict.fromkeys(result))


def variants(source, name, header='', callees=''):
    if max(len(source), len(header), len(callees)) > 64000:
        return []
    part = view(source, name)
    if not part or conditional_at(part.clean, part.opening):
        return []
    if re.search(r'\b(?:volatile|asm|__asm__|setjmp|_setjmp|sigsetjmp|longjmp|alloca|__builtin_alloca|__builtin_frame_address|__builtin_return_address)\b', part.clean):
        return []
    context = _blank(source+'\n'+header)
    selected = _blank(callees)
    macros = set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')\b', context+'\n'+selected, re.M))
    if macros & (TYPE_WORDS | {name}) or any(re.search(r'\b'+re.escape(m)+r'\b', part.body) for m in macros):
        return []
    formals = {p for p in parameters(part, name, context) if not shadowed(part, p, context)}
    local_declarations = locals_for(part)
    ordinary = {}
    for decl in local_declarations:
        if matched_prototype_repairs.scalar(decl['type'], context):
            ordinary[decl['name']] = decl
    flow = local_flow.analyze(source, name, declarations=local_declarations)
    dom = dominators(flow) if flow else {}

    def pure_value(text, position):
        text = text.strip()
        if re.fullmatch(NUMBER, text) or text in formals:
            return True
        if text not in ordinary or not flow:
            return False
        node = flow.at(position)
        if node is None:
            return False
        index = flow.nodes.index(node)
        decl = ordinary[text]
        return any(i != index and i in dom.get(index, set()) and text in n.defines
                   and not n.start <= decl.start() < n.end for i,n in enumerate(flow.nodes))
    groups = []
    for proto in matched_prototype_repairs.PROTO.finditer(part.clean, 0, part.opening):
        callee = proto['callee']
        if proto['result'] != 'void' or callee in macros or callee == name or find(source, callee):
            continue
        if part.clean[:proto.start()].count('{') != part.clean[:proto.start()].count('}'):
            continue
        if conditional_at(part.clean, proto.start()) or re.search(r'\b'+re.escape(callee)+r'\b', _blank(header)):
            continue
        outside = part.clean[:proto.start()]+part.clean[proto.end():part.opening]+part.clean[part.end:]
        if re.search(r'\b'+re.escape(callee)+r'\b', outside):
            continue
        span = find(selected, callee)
        if not span or conditional_at(selected, span[0]):
            continue
        definition = re.fullmatch(r'(?:static\s+)?(?:inline\s+)?void\s+'+re.escape(callee)
                                  +r'\s*\(\s*void\s*\)\s*{\s*(?:return\s*;\s*)?}', selected[span[0]:span[1]].strip())
        if not definition or len(re.findall(r'\b'+re.escape(callee)+r'\s*\([^;{}]*\)\s*{', selected)) != 1:
            continue
        sites = list(calls(part, callee))
        if not sites or len(sites) != len(re.findall(r'\b'+re.escape(callee)+r'\b', part.body)):
            continue
        changes = [(proto.start('params'), proto.end('params'), 'void')]
        for begin, end in sites:
            args = split_args(part.clean[begin:end])
            if not 1 <= len(args) <= 4:
                break
            if not all(pure_value(part.clean[begin+a:begin+b], begin) for a,b in args):
                break
            changes.append((begin, end, ''))
        else:
            groups.append(changes)
        if len(groups) == 3:
            break
    if not groups:
        return []
    changes = [g for group in groups for g in group]
    call_sources = list(dict.fromkeys([edit(source, changes)]+[edit(source, g) for g in groups]))
    result = []
    # Try the measured composition before exhausting unrelated lifetime families.
    for changed in call_sources:
        result.extend((COMBINED, s) for s in aliases(changed, name, header))
        result.append((CALL, changed))
    result.extend((ALIAS, s) for s in aliases(source, name, header))
    return list(dict.fromkeys(result))[:LIMIT]
