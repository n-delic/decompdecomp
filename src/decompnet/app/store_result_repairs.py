"""Reuse an arithmetic operand after its last CFG use, without adding a conversion.

The existing RHS is evaluated once, assigned to an unescaped same-type local,
then consumed by the same store. This changes a lifetime, never the stored value.
"""
import re
from collections import deque

from . import local_flow
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, edit, view
from .pattern_repairs import close
from .permuter_macro_effects import DEFINE

FAMILY = 'store_result_reuse'
LIMIT = 6
DECL = re.compile(r'\b(?P<type>unsigned\s+int|signed\s+int|int|s32|u32)\s+'
                  r'(?P<name>'+IDENT+r')\s*(?:=\s*(?P<initial>[^;{}]+))?\s*;')


def signed_word(typ, context):
    if typ in {'int', 'signed int'}:
        return True
    if typ != 's32':
        return False
    aliases = re.findall(r'\btypedef\s+([^;{}]+)\s+s32\s*;', context)
    return len(aliases) == 1 and bool(re.fullmatch(r'(?:signed\s+)?int', aliases[0].strip()))


def initialized_before(flow):
    """Must-initialization on reachable edges, including declaration initializers."""
    reachable, pending = set(), [flow.entry]
    while pending:
        index = pending.pop()
        if index in reachable:
            continue
        reachable.add(index)
        pending.extend(flow.nodes[index].successors)
    parents = {i:set() for i in reachable}
    for i in reachable:
        for child in flow.nodes[i].successors:
            parents[child].add(i)
    before = {i:set(flow.names) for i in reachable}
    after = {i:set(flow.names) for i in reachable}
    pending = deque(sorted(reachable))
    queued = set(pending)
    work = 0
    while pending:
        i = pending.popleft()
        queued.remove(i)
        node = flow.nodes[i]
        prior = set.intersection(*(after[p] for p in parents[i])) if parents[i] else set()
        if i == flow.entry:
            prior = set()
        defined = set()
        text = flow.part.clean[node.start:node.end]
        assignment = re.fullmatch(r'\s*('+IDENT+r')\s*=(?!=).*;\s*', text, re.S)
        declarations = [d for d in flow.locals if node.start <= d.start() < node.end]
        if node.uses <= prior:
            if assignment and assignment[1] in flow.names:
                defined.add(assignment[1])
            defined.update(d['name'] for d in declarations if d['initial'] is not None)
        result = (prior-node.defines)|defined
        before[i] = prior
        if after[i] != result:
            after[i] = result
            for child in node.successors:
                if child not in queued:
                    pending.append(child)
                    queued.add(child)
        work += 1+len(parents[i])
        if work > 65536:
            return {}
    return before


def variants(source, name, header=''):
    part = view(source, name)
    if not part or re.search(r'\b(?:asm|__asm__|setjmp|longjmp|sigsetjmp|_setjmp|volatile|restrict|_Atomic)\b', part.clean):
        return []
    context = header+'\n'+part.clean[:part.opening]
    if re.search(r'\b(?:class|template|namespace|new|delete|throw|try|catch|decltype|auto|this)\b|\[\s*&|(?<=[;{}])\s*(?:'+IDENT+r'\s+)+&', part.clean):
        return []
    signature = re.search(r'\b'+re.escape(name)+r'\s*\(([^()]*)\)\s*$', part.clean[:part.opening])
    if not signature:
        return []
    macros = list(DEFINE.finditer(header+'\n'+part.clean))
    macro_names = {m[1] for m in macros}
    if macro_names & {'int', 'signed', 's32'}:
        return []
    # Permit only inert literals and the app's explicit fixed global-pointer slots.
    # Other macros can hide a local read, escape, control edge or pasted identifier.
    for macro in macros:
        if not re.search(r'\b'+re.escape(macro[1])+r'\b', part.body):
            continue
        literal = r'\s*\(*\s*(?:0[xX][0-9a-fA-F]+|[0-9]+)[uUlL]*\s*\)*\s*'
        slot = (r'\s*\(\s*\*\s*\(\s*'+IDENT+r'\s*\*\s*\*\s*\)\s*\(\s*'
                +IDENT+r'\s*\+\s*(?:0[xX][0-9a-fA-F]+|[0-9]+)\s*\)\s*\)\s*')
        if macro[2] is not None or not re.fullmatch(literal+'|'+slot, macro[3]):
            return []
        if re.fullmatch(slot, macro[3]):
            base = re.search(r'('+IDENT+r')\s*\+', macro[3])[1]
            typ = re.search(r'\(\s*('+IDENT+r')\s*\*\s*\*', macro[3])[1]
            # Only an explicitly declared global slot base, never a hidden local
            # dependency. Its token must not be redefined or shadowed in the body.
            if (base in macro_names or typ in macro_names or re.search(r'\b'+re.escape(base)+r'\b', part.body)
                    or not re.search(r'\bextern\s+'+IDENT+r'\s+'+re.escape(base)+r'\s*\[\s*\]\s*;', context)
                    or re.search(r'\b'+re.escape(base)+r'\b', signature[1])):
                return []
    records = []
    for record in re.finditer(r'\b(?:struct|union)(?:\s+'+IDENT+r')?\s*{', part.body):
        start = part.opening+1+record.end()-1
        end = close(part.clean, start, '{', '}')
        if end:
            records.append((start, end))
    if (re.search(r'\btypedef\b', part.body)
            or re.search(r'\bfor\s*\(\s*'+IDENT+r'\s+'+IDENT+r'\b', part.body)
            or re.search(r'(?<=[;{}])\s*(?:'+IDENT+r'\s+)+(?:\*\s*)*'+IDENT+r'\s*(?=[=;,\[])[^;{}]*,', part.clean[part.opening:part.end])):
        return []
    declarations = []
    for decl in DECL.finditer(part.clean, part.opening+1, part.end):
        if (any(start < decl.start() < end for start, end in records)
                or part.clean[:decl.start()].rstrip()[-1:] not in {';', '{', '}'}):
            continue
        var = decl['name']
        # Generic declaration spelling also catches pointer/qualified/alias shadows.
        shadows = re.findall(r'(?<=[;{}])\s*(?:'+IDENT+r'\s+)+(?:\*\s*)*'+re.escape(var)+r'\s*(?=[;=,\[])', part.clean[part.opening:part.end])
        if (len(shadows) != 1 or var in macro_names or re.search(r'\b'+re.escape(var)+r'\b', signature[1])
                or decl['initial'] is not None and re.search(r'\b'+re.escape(var)+r'\b', decl['initial'])
                or re.search(r'(?<!&)&(?!&)\s*(?:\(\s*)*'+re.escape(var)+r'\b', part.body)):
            continue
        declarations.append(decl)
    try:
        flow = local_flow.Flow(part, declarations)
    except (ValueError, RecursionError):
        return []
    before = initialized_before(flow)
    types = {d['name']:d for d in declarations if signed_word(d['type'], context)}
    out = []
    for index, node in enumerate(flow.nodes):
        if index not in before or part.clean[:node.start].rstrip()[-1:] not in {';', '{', '}'}:
            continue
        # Only ordinary member stores. Pointer/index evaluation is unchanged, and
        # a local operand's address cannot escape into the destination expression.
        hit = re.fullmatch(r'\s*'+IDENT+r'\s*(?:->|\.)\s*'+IDENT+r'\s*=(?!=)\s*(?P<rhs>[^;]+);\s*', part.clean[node.start:node.end])
        if not hit:
            continue
        rhs = hit['rhs']
        # Same signed-word + and - association only. No casts, calls, loads,
        # suffix/promotion changes, assignment, comma, conditionals or unary updates.
        tokens = re.findall(IDENT+r'|0[xX][0-9a-fA-F]+|[0-9]+|[()+-]', rhs)
        if ''.join(tokens) != re.sub(r'\s+', '', rhs) or not re.search(r'[+-]', rhs) or re.search(r'\+\+|--', rhs):
            continue
        operands = [token for token in tokens if re.fullmatch(IDENT, token)]
        if not operands or not set(operands) <= types.keys() or not set(operands) <= before[index]:
            continue
        if any(token[0].isdigit() and (len(token)>1 and token[0]=='0' and token[1] not in 'xX' or int(token, 0 if token.lower().startswith('0x') else 10)>0x7fffffff) for token in tokens):
            continue
        if any(types[v].end() > node.start or block_path(part, node.start)[:len(block_path(part, types[v].start()))] != block_path(part, types[v].start()) for v in operands):
            continue
        for var in dict.fromkeys(operands):
            decl = types[var]
            scope = block_path(part, decl.start())
            if (decl.end() > node.start or block_path(part, node.start)[:len(scope)] != scope
                    or any(var in flow.nodes[n].live for n in node.successors)):
                continue
            start, end = node.start+hit.start('rhs'), node.start+hit.end('rhs')
            out.append((FAMILY, edit(source, [(start, end, '('+var+' = '+source[start:end]+')')])))
            if len(out) == LIMIT:
                return out
    return list(dict.fromkeys(out))
