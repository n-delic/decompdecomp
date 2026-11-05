"""Reject a limited, possible uninitialized-read pattern in newly generated scalar locals.

This is neither a C undefined-behavior checker nor a semantic equivalence proof. Unknown syntax,
qualified/shadowed/escaped locals and existing names receive no verdict. Use the bounded local CFG,
with separate initialization transfer rules: its liveness definitions include bare declarations.
"""
import re

from . import local_flow
from .integer_repair_support import IDENT, INTEGER, NUMBER, view
from .pattern_repairs import close
from .pointer_pair_repairs import qualified_types

_DECL = re.compile(rf'\b(?P<type>{INTEGER})\s+(?P<name>{IDENT})\s*(?:=(?P<initial>[^;{{}}]*))?;')


def unsafe_temporary(parent, candidate, name, header=""):
    """Whether a syntactically reachable introduced scalar read lacks definite initialization."""
    part = view(candidate, name)
    original = view(parent, name)
    if not part or not original:
        return False
    old_names = set(re.findall(IDENT, original.clean))
    combined = header+"\n"+parent+"\n"+candidate
    macros = set(re.findall(r"^\s*#\s*define\s+("+IDENT+r")", combined, re.M))
    # This guard excludes both const and volatile aliases, including alias chains.
    qualified = qualified_types(re.sub(r"\bconst\b", "volatile", combined))
    records = []
    for record in re.finditer(r"\b(?:struct|union)(?:\s+"+IDENT+r")?\s*{", part.body):
        start = part.opening+1+record.end()-1
        end = close(part.clean, start, "{", "}")
        if end:
            records.append((start, end))
    declarations = []
    for decl in _DECL.finditer(part.clean, part.opening+1, part.end):
        prefix = part.clean[part.opening+1:decl.start()].rstrip()
        variable = decl['name']
        if any(start < decl.start() < end for start, end in records):
            continue
        if (set(re.findall(IDENT, decl['type'])) | {variable}) & (qualified | macros):
            continue
        if variable in old_names or (prefix and prefix[-1] not in ';{}'):
            continue
        if len(re.findall(rf'\b(?:{INTEGER}|void|float|double)\s*\**\s*{variable}\s*(?=[;=,\[])', part.body)) != 1:
            continue
        if re.search(rf'&\s*(?:\(\s*)*{variable}\b', part.body):
            continue
        declarations.append(decl)
    if not declarations or re.search(r"\bfor\s*\([^;]*;\s*;", part.body):
        return False
    flow = local_flow.analyze(candidate, name, declarations=declarations)
    if not flow or flow.entry is None:
        return False
    names = flow.names
    # The native walk commonly wraps a single statement in do {...} while (0).
    # Prune only its literal-zero back edge; other constant controls receive no verdict.
    one_shot_conditions = set()
    for hit in re.finditer(r'\bdo\b', part.body):
        body = flow.skip(part.opening+1+hit.end(), part.end-1)
        end = flow.statement_end(body, part.end-1)
        opening, stop = flow.condition(end)
        if re.fullmatch(r'\s*0[uUlL]*\s*', part.clean[opening+1:stop-1]):
            for index, node in enumerate(flow.nodes):
                if node.start == opening+1 and node.end == stop-1:
                    node.successors = {n for n in node.successors if not body <= flow.nodes[n].start < end}
                    one_shot_conditions.add(index)
    transfers = []
    for index, node in enumerate(flow.nodes):
        text = part.clean[node.start:node.end]
        # The shared CFG does not prune constant conditions or expand macros.
        numbers_only = re.sub(rf'(?<![\w.]){NUMBER}(?![\w.])', '0', text)
        if ((index not in one_shot_conditions and re.search(r'\d', text)
             and re.fullmatch(r'[\s0()+*/%<>=!&|^~.-]+', numbers_only))
                or (not text.strip() and "'" in candidate[node.start:node.end])
                or set(re.findall(IDENT, text)) & macros
                or re.search(r'\b(?:sizeof|_Alignof|typeof|__typeof__|true|false)\b', text)):
            return False
        # Expression sequencing, embedded writes and macro-like calls are beyond this guard.
        if names & set(re.findall(IDENT, text)) and re.search(r'\?|&&|\|\||\b(?:sizeof|_Alignof|typeof|__typeof__)\b|\b[A-Z_][A-Z_0-9]*\s*\(', text):
            return False
        uses = set(node.uses)
        generates, kills = set(), set()
        contained = [d for d in declarations if node.start <= d.start() and d.end() <= node.end]
        for decl in contained:
            variable = decl['name']
            kills.add(variable)
            uses.discard(variable)
            if decl['initial'] is not None:
                initializer = decl['initial']
                if ',' in initializer or re.search(rf'\b(?:{"|".join(map(re.escape, names))})\s*=(?!=)', initializer):
                    return False
                initial = {hit[0] for hit in re.finditer(IDENT, initializer)
                           if not re.search(r'(?:\.|->)\s*$', initializer[:hit.start()])} & names
                uses |= initial
                generates.add(variable)
        assignment = re.fullmatch(rf'\s*({IDENT})\s*=(?!=)(.*?);?\s*', text, re.S)
        if assignment and assignment[1] in names:
            if ',' in assignment[2] or re.search(rf'\b(?:{"|".join(map(re.escape, names))})\s*=(?!=)', assignment[2]):
                return False
            generates.add(assignment[1])
        elif not contained and re.search(rf'\b(?:{"|".join(map(re.escape, names))})\s*=(?!=)', text):
            return False
        transfers.append((uses, generates, kills))
    reachable, pending = set(), [flow.entry]
    while pending:
        index = pending.pop()
        if index not in reachable:
            reachable.add(index)
            pending.extend(flow.nodes[index].successors)
    predecessors = {index: set() for index in reachable}
    for index in reachable:
        for successor in flow.nodes[index].successors:
            predecessors[successor].add(index)
    # Greatest fixed point for a must analysis; the entry also has the empty initial state.
    outgoing = {index: set(names) for index in reachable}
    changed = True
    while changed:
        changed = False
        for index in reachable:
            incoming = set.intersection(*(outgoing[p] for p in predecessors[index])) if predecessors[index] else set()
            if index == flow.entry:
                incoming = set()
            _, generates, kills = transfers[index]
            result = (incoming-kills) | generates
            if result != outgoing[index]:
                outgoing[index] = result
                changed = True
    for index in reachable:
        incoming = set.intersection(*(outgoing[p] for p in predecessors[index])) if predecessors[index] else set()
        if index == flow.entry:
            incoming = set()
        uses, _, kills = transfers[index]
        if uses - (incoming-kills):
            return True
    return False
