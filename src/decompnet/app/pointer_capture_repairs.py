"""Reuse a dead typed pointer for a later byte-pointer capture, without moving evaluations.

A bounded pair matters: one lifetime change can initially worsen allocation even when two
independent captures recover it. CFG liveness, not textual ordering, protects the old value.
"""
import itertools
import re

from ..forks.csource import _blank
from . import local_flow, pointer_pair_repairs
from .integer_repair_support import IDENT, INTEGER, edit, locals_for, refs

FAMILY = 'pointer_capture_reuse'
FAMILIES = (FAMILY,)
LIMIT = 12


def byte_type(typ, combined):
    typ = ''.join(typ.split())
    if typ in {'char*', 'unsignedchar*', 'signedchar*'}:
        return True
    return typ in {'u8*', 's8*'} and bool(re.search(
        rf'\btypedef\s+(?:unsigned|signed)\s+char\s+{typ[:-1]}\s*;', combined))


def table_type(expression, combined, local_names):
    """Only a constant in-bounds cell of one explicitly typed pointer array."""
    hit = re.fullmatch(rf'\s*({IDENT})\s*\[\s*(0[xX][0-9a-fA-F]+|[0-9]+)\s*\]\s*', expression)
    if not hit or hit[1] in local_names:
        return None
    decls = list(re.finditer(rf'\b(?:extern\s+)?((?:struct\s+)?{IDENT})\s*\*\s*{hit[1]}\s*'
                            r'\[\s*(0[xX][0-9a-fA-F]+|[0-9]+)\s*\]\s*;', combined))
    if len(decls) != 1:
        return None
    decl = decls[0]
    # Reject qualified declarations instead of matching just the suffix after a qualifier.
    prefix = combined[max(combined.rfind(c, 0, decl.start()) for c in ';{}')+1:decl.start()]
    try:
        index, bound = int(hit[2], 0), int(decl[2], 0)
    except ValueError:
        return None
    if prefix.strip() or index >= bound:
        return None
    return ''.join((decl[1]+'*').split())


def indirect_call(expression):
    if re.search(r'\]\s*\(', expression):
        return True
    for hit in re.finditer(r'\)\s*\(', expression):
        depth = 1
        opening = hit.start()-1
        while opening >= 0 and depth:
            depth += (expression[opening] == ')') - (expression[opening] == '(')
            opening -= 1
        content = expression[opening+2:hit.start()]
        if depth or not re.fullmatch(rf'\s*(?:struct\s+)?{IDENT}\s*\*+\s*', content):
            return True
    return False


def transparent(expression, macros):
    """Known expression macros may not duplicate arguments or hide a pointer use."""
    if indirect_call(expression):
        return False
    pending = [key for key in re.findall(IDENT, expression) if key in macros]
    seen = set()
    while pending:
        key = pending.pop()
        if key in seen:
            return False  # cycles/repeated dependencies have no useful simple proof here
        seen.add(key)
        params, body = macros[key]
        if indirect_call(body):
            return False
        if params is not None:
            params = [p.strip() for p in params.split(',')]
            if not all(re.fullmatch(IDENT, p) and len(re.findall(rf'\b{p}\b', body)) == 1 for p in params):
                return False
        pending.extend(k for k in re.findall(IDENT, body) if k in macros)
    return pointer_pair_repairs.pure(expression, macros)


def variants(source, name, header=''):
    part, pointers, macros = pointer_pair_repairs.context(source, name, header)
    if not part or not pointers:
        return []
    combined = _blank(header)+'\n'+part.clean
    # The old value must not be hidden by preprocessing, qualifiers or C++ semantics.
    if (re.search(r'^\s*#\s*(?:if|elif|else|endif|include|undef)\b', combined, re.M)
            or re.search(r'\b(?:typeof|__typeof__|__auto_type)\b',combined)):
        return []
    unsafe = pointer_pair_repairs.qualified_types(re.sub(r'\b(?:const|_Atomic|__atomic)\b', 'volatile', combined))
    pointers = [d for d in pointers if d['name'] not in macros
                and not (unsafe | macros.keys()).intersection(re.findall(IDENT, d['type']))]
    scalars = locals_for(part)
    declarations = pointers+scalars
    flow = local_flow.analyze(source, name, declarations=declarations)
    if not flow:
        return []
    signature = part.clean[max(part.clean.rfind(ch, 0, part.opening) for ch in ';{}')+1:part.opening]
    names = {d['name'] for d in declarations} | set(re.findall(IDENT, signature))
    scalar_names = {d['name'] for d in scalars if d['name'] not in macros
                    and re.fullmatch(INTEGER, d['type'])}
    indices = {id(node): i for i, node in enumerate(flow.nodes)}
    predecessors = {i: set() for i in range(len(flow.nodes))}
    for i, node in enumerate(flow.nodes):
        for successor in node.successors:
            predecessors[successor].add(i)
    groups = {}
    for dest in pointers:
        dst = dest['name']
        if not byte_type(dest['type'], combined):
            continue
        for first in flow.nodes:
            initial = re.fullmatch(rf'\s*{dst}\s*=\s*\(\s*([^()]+)\s*\)\s*([^;{{}}]+);\s*', part.clean[first.start:first.end])
            if not initial or ''.join(initial[1].split()) != ''.join(dest['type'].split()):
                continue
            if macros.keys() & set(re.findall(IDENT, initial[2])):
                continue
            root = re.match(IDENT, initial[2].strip())
            # Selected locals deliberately exclude qualified/complex declarations. Such a
            # declaration can still shadow the global table inside a nested block.
            if root and re.search(rf'\b{IDENT}(?:\s+\**\s*|\s*\*+\s*){re.escape(root[0])}\s*(?=[;=,)\[])',part.body):
                continue
            typ = table_type(initial[2], combined, names)
            if not typ:
                continue
            # Follow at most two literal scalar initializations. No calls, reads, stores,
            # branches or incoming gotos may observe the removed intermediate dst value.
            current = first
            sink = None
            for _ in range(3):
                if len(current.successors) != 1:
                    break
                nxt = next(iter(current.successors))
                node = flow.nodes[nxt]
                if predecessors[nxt] != {indices[id(current)]} or node.start < current.end:
                    break
                text = part.clean[node.start:node.end]
                assignment = re.fullmatch(rf'\s*{dst}\s*=(?!=)([^;{{}}]+);\s*', text)
                if assignment and len(re.findall(rf'\b{dst}\b', assignment[1])) == 1 and transparent(assignment[1], macros):
                    sink = node
                    break
                literal = re.fullmatch(rf'\s*({IDENT})\s*=\s*(?:0[xX][0-9a-fA-F]+|[0-9]+)[uUlL]*\s*;\s*', text)
                if not literal or literal[1] not in scalar_names:
                    break
                current = node
            if sink is None:
                continue
            for reused in pointers:
                var = reused['name']
                if var == dst or ''.join(reused['type'].split()) != typ or var not in flow.names:
                    continue
                # Protect both entry and exit, including backedges and branch joins.
                if var in first.live or any(var in flow.nodes[i].live for i in sink.successors):
                    continue
                if not any(h.start() < first.start for h in refs(part, reused)):
                    continue
                sink_text = part.clean[sink.start:sink.end]
                if re.search(rf'\b{var}\b', sink_text):
                    continue
                rhs_start = first.start+initial.start(2)
                changes = [(first.start, first.end, var+' = '+source[rhs_start:first.start+initial.end(2)]+';')]
                rhs = re.search(r'=(?!=)', sink_text).end()
                hits = list(re.finditer(rf'\b{dst}\b', sink_text[rhs:]))
                assert len(hits) == 1
                hit = hits[0]
                # Retain the old expression's byte-pointer type: replacing p with a
                # record pointer directly would change the stride of p+1.
                replacement = '(('+dest['type']+')'+var+')'
                changes.append((sink.start+rhs+hit.start(), sink.start+rhs+hit.end(), replacement))
                groups.setdefault((var, dst), []).append(changes)
    result = []
    for sites in groups.values():
        sites = sites[:6]
        # Independent pairs first: a worse single must not prevent testing the pair.
        choices = list(itertools.combinations(sites, 2))+[(site,) for site in sites]
        for chosen in choices:
            changes = [change for site in chosen for change in site]
            spans = sorted((a, b) for a, b, _ in changes)
            if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
                continue
            text = edit(source, changes)
            if text != source and (FAMILY, text) not in result:
                result.append((FAMILY, text))
            if len(result) == LIMIT:
                return result
    return result
