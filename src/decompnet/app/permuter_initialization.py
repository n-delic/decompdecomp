"""Reject new uninitialized reads in generated integer and pointer locals.

This is neither a C undefined-behavior checker nor a semantic equivalence proof. Unknown syntax,
qualified/shadowed/escaped locals receive no verdict. Existing bindings are rejected only when
the parent has a supported initialized verdict for that binding. Use the bounded local CFG,
with separate initialization transfer rules: its liveness definitions include bare declarations.
"""
import re
from functools import lru_cache

from decomprepair import local_flow
from decomprepair.integer_repair_support import IDENT, INTEGER, NUMBER, view
from decomprepair.pattern_repairs import close
from decomprepair.pointer_pair_repairs import qualified_types

# Pointer captures need the same must-initialization check as integer captures.
# Keep unknown pointee aliases, qualifiers, arrays and function pointers opaque.
_TYPE = rf'(?:{INTEGER}(?=\s)|(?:{INTEGER}|void)(?:\s*\*)+)'
_DECL = re.compile(rf'\b(?P<type>{_TYPE})\s*(?P<name>{IDENT})\s*(?:=(?P<initial>[^;{{}}]*))?;')


def unsafe_temporary(parent, candidate, name, header=""):
    """Whether a new local is unsafe, or an existing local loses proven initialization."""
    original = view(parent, name)
    if not original:
        return False
    old_names = set(re.findall(IDENT, original.clean))
    verdicts = _initialization_verdicts(candidate, name, header)
    unsafe = {variable for variable, verdict in verdicts.items() if verdict is True}
    if unsafe-old_names:
        return True
    if not unsafe:
        return False
    previous = _parent_verdicts(parent, name, header)
    return any(previous.get(variable) is False for variable in unsafe)


@lru_cache(maxsize=8)
def _parent_verdicts(source, name, header):
    # A batch shares its parent. Do not rebuild its CFG for each rejected proposal.
    return _initialization_verdicts(source, name, header)


def _initialization_verdicts(candidate, name, header):
    part = view(candidate, name)
    if not part:
        return {}
    combined = header+"\n"+candidate
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
        if prefix and prefix[-1] not in ';{}':
            continue
        if len(re.findall(rf'\b(?:{INTEGER}|void|float|double)(?:\s+|(?:\s*\*)+\s*)'
                          rf'{variable}\s*(?=[;=,\[])', part.body)) != 1:
            continue
        if re.search(rf'&\s*(?:\(\s*)*{variable}\b', part.body):
            continue
        declarations.append(decl)
    if not declarations or re.search(r"\bfor\s*\([^;]*;\s*;", part.body):
        return {}
    flow = local_flow.analyze(_without_type_only_records(candidate, part, macros), name,
                              declarations=declarations)
    if not flow or flow.entry is None:
        return {}
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
    # Unsupported writes to one temporary must not hide a separate unsafe capture.
    # Reuse the bounded CFG, but keep each binding's initialization verdict independent.
    return {decl['name']: _unsafe_binding(part, candidate, flow, decl, macros, combined, one_shot_conditions)
            for decl in declarations}


def _without_type_only_records(candidate, part, macros):
    """Hide only inert record typedefs from the statement CFG, preserving every offset.

    The CFG deliberately rejects braced expressions. Literal-sized scalar fields in a local
    typedef are not executable statements; VLA bounds, macros and other declarations stay opaque.
    This is private to the initialization guard, not permission for lifetime repairs to move code.
    """
    scalar = rf'(?:{INTEGER}|float|double|f32|f64)'
    field = rf'{scalar}\s+{IDENT}(?:\s*\[\s*{NUMBER}\s*\])?\s*;'
    result = list(candidate)
    for hit in re.finditer(r'\btypedef\s+struct(?:\s+'+IDENT+r')?\s*{', part.body):
        start = part.opening+1+hit.start()
        opening = part.opening+1+hit.end()-1
        stop = close(part.clean, opening, '{', '}')
        if not stop:
            continue
        tail = re.match(r'\s*('+IDENT+r')\s*;', part.clean[stop:])
        if not tail:
            continue
        end = stop+tail.end()
        fields = part.clean[opening+1:stop-1]
        if (not re.fullmatch(rf'(?:\s*{field})+\s*', fields)
                or set(re.findall(IDENT, part.clean[start:end])) & macros):
            continue
        # The final semicolon becomes an empty statement. All declaration/read positions survive.
        for index in range(start, end-1):
            if result[index] != '\n':
                result[index] = ' '
    return ''.join(result)


def _embedded_pointer_copy(text, variable, local_names=()):
    """Recognize one unconditional pointer copy in two bounded expression forms.

    Retain the ordinary indirect store-address copy, or accept a single capture
    in another plain local assignment's arithmetic RHS. Calls, conditional
    evaluation and other reads/writes of the copied binding remain opaque.
    """
    if len(re.findall(rf'\b{variable}\b', text)) != 1:
        return False
    if not text.lstrip().startswith('*'):
        # A single unconditional capture in another plain local's arithmetic RHS.
        # No sequencing inference: the copied binding occurs only at this write.
        assignment = re.fullmatch(rf'\s*({IDENT})\s*=(?!=)(.*?)\s*;?\s*', text, re.S)
        if not assignment or assignment[1] not in local_names or assignment[1] == variable:
            return False
        rhs = assignment[2]
        if (re.search(r'[,?;]|&&|\|\||\+\+|--|(?:<<|>>|[+*/%&|^\-])=', rhs)
                or re.search(rf'\b{IDENT}\s*\(', rhs)
                or re.search(r'\b(?:sizeof|_Alignof|typeof|__typeof__)\b', rhs)):
            return False
        capture = re.search(rf'\(\s*{variable}\s*=(?!=)\s*{IDENT}(?:\s*[+\-]\s*(?:{NUMBER}|{IDENT}))?\s*\)', rhs)
        if capture is None:
            return False
        remaining = rhs[:capture.start()]+'0'+rhs[capture.end():]
        # Other writes, member/index accesses and calls remain unknown. This is
        # an initialization transfer, not an expression purity proof: arithmetic
        # (including unary *) may evaluate operands in either order, but none
        # reads/writes this p outside its single capture.
        return (not re.search(r'=(?!=)|->|[\[\].]', remaining)
                and re.fullmatch(r'[\s()A-Za-z_0-9+*/%&|^~<>\-]+', remaining) is not None)
    depth = 0
    for pos, char in enumerate(text):
        if char in '([':
            depth += 1
        elif char in ')]':
            depth -= 1
        elif (char == '=' and depth == 0 and text[pos+1:pos+2] != '='
              and text[pos-1:pos] not in '<>!=+-*/%&|^'):
            address = text[:pos]
            return (not re.search(rf'\b{IDENT}\s*\(', address)
                    and re.search(rf'\(\s*{variable}\s*=(?!=)\s*{IDENT}\s*\)', address) is not None)
    return False


def _unsafe_binding(part, candidate, flow, decl, macros, combined, one_shot_conditions):
    """True for a possible uninitialized read, False for initialized reads, None for unknown flow."""
    names = {decl['name']}
    declarations = [decl]
    pointers = names if '*' in decl['type'] else set()
    from decomprepair.permuter_macro_effects import transparent_macros
    opaque_macros = macros - transparent_macros(combined, names)
    local_names = set(flow.names)
    transfers = []
    for index, node in enumerate(flow.nodes):
        text = part.clean[node.start:node.end]
        # The shared CFG does not prune constant conditions or expand macros.
        numbers_only = re.sub(rf'(?<![\w.]){NUMBER}(?![\w.])', '0', text)
        if ((index not in one_shot_conditions and re.search(r'\d', text)
             and re.fullmatch(r'[\s0()+*/%<>=!&|^~.-]+', numbers_only))
                or (not text.strip() and "'" in candidate[node.start:node.end])
                or set(re.findall(IDENT, text)) & opaque_macros
                or re.search(r'\b(?:sizeof|_Alignof|typeof|__typeof__|true|false)\b', text)):
            return None
        # A field named x is not the local x. Preserve the base and index expressions,
        # but hide member identifiers from the binding-specific write/sequence checks.
        text = re.sub(r'(\.|->)(\s*)('+IDENT+r')',
                      lambda hit: hit[1]+hit[2]+' '*len(hit[3]), text)
        # Expression sequencing, embedded writes and macro-like calls are beyond this guard.
        if names & set(re.findall(IDENT, text)) and re.search(r'\?|&&|\|\||\b(?:sizeof|_Alignof|typeof|__typeof__)\b|\b[A-Z_][A-Z_0-9]*\s*\(', text):
            return None
        uses = set(node.uses) & names
        generates, kills = set(), set()
        contained = [d for d in declarations if node.start <= d.start() and d.end() <= node.end]
        for decl in contained:
            variable = decl['name']
            kills.add(variable)
            uses.discard(variable)
            if decl['initial'] is not None:
                initializer = decl['initial']
                if ',' in initializer or re.search(rf'\b(?:{"|".join(map(re.escape, names))})\s*=(?!=)', initializer):
                    return None
                initial = {hit[0] for hit in re.finditer(IDENT, initializer)
                           if not re.search(r'(?:\.|->)\s*$', initializer[:hit.start()])} & names
                uses |= initial
                generates.add(variable)
        assignment = re.fullmatch(rf'\s*({IDENT})\s*=(?!=)(.*?);?\s*', text, re.S)
        indirect = re.fullmatch(rf'\s*\*\s*({IDENT})\s*=(?!=)(.*?);?\s*', text, re.S)
        if indirect and indirect[1] in pointers:
            if ',' in indirect[2] or re.search(rf'\b(?:{"|".join(map(re.escape, names))})\s*=(?!=)', indirect[2]):
                return None
            # Writing through a pointer reads its value; it does not assign that local.
            uses.add(indirect[1])
        elif assignment and assignment[1] in names:
            if ',' in assignment[2] or re.search(rf'\b(?:{"|".join(map(re.escape, names))})\s*=(?!=)', assignment[2]):
                return None
            generates.add(assignment[1])
        elif not contained and re.search(rf'\b(?:{"|".join(map(re.escape, names))})\s*=(?!=)', text):
            if decl['name'] not in pointers or not _embedded_pointer_copy(text, decl['name'], local_names):
                return None
            # Completing this supported expression necessarily evaluates its capture.
            # Track the copy on this CFG edge, not on paths bypassing the statement.
            uses.discard(decl['name'])
            generates.add(decl['name'])
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
