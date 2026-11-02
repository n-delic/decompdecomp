"""Bounded scalar reuse and affine-counter hypotheses, checked by the ordinary judge.

CFG liveness protects old scratch values, including across gotos. Unknown expressions and
control flow produce no candidate. These rules never prescribe registers or change flags.
"""
import itertools
import re

from .integer_repair_support import IDENT, edit, refs, split_args
from .local_flow import analyze
from .pattern_repairs import close

FAMILIES = ('paired_operand_reuse', 'affine_counter', 'affine_counter_guard')
LIMIT = 48
MAX_SCRATCH = 16
WORD = {'int', 'signed int', 's32'}


def pure(text):
    # No calls, mutation, comma expressions or conditional evaluation. Casts/loads are allowed.
    return not re.search(r'\b(?:volatile|sizeof)\b|\+\+|--|[=,?:]|\b'+IDENT+r'\s*\(', text)


def differences(text):
    depth = 0
    for i, char in enumerate(text):
        depth += (char in '([') - (char in ')]')
        if char == '-' and depth == 0 and text[i+1:i+2] != '>':
            return text[:i].strip(), text[i+1:].strip()
    return None


def unparen(text):
    text = text.strip()
    while text.startswith('(') and close(text, 0) == len(text):
        text = text[1:-1].strip()
    return text


def narrow_load(text, clean):
    # Recognize the whole value, not merely a halfword cast somewhere in wide arithmetic.
    text = unparen(text)
    if text.startswith('*'):
        pointer = unparen(text[1:])
        return bool(re.match(r'\(\s*(?:s16|signed\s+short|short)\s*\*\s*\)', pointer))
    array = re.fullmatch(r'(' + IDENT + r')\s*\[\s*\d+\s*\]', text)
    if not array:
        return False
    declarations = list(re.finditer(r'\b(?P<type>(?:unsigned\s+|signed\s+)?short|s16|u16)\s+(?:\*\s*)?'
                                   + re.escape(array[1])+r'\s*(?:\[|[,;)])', clean))
    return len(declarations) == 1 and not re.match(r'unsigned\b|u16\b', declarations[0]['type'])


def operand_variants(source, name):
    flow = analyze(source, name)
    if not flow:
        return []
    part, sites = flow.part, []
    for node in sorted(flow.nodes, key=lambda n: n.start):
        text = part.clean[node.start:node.end]
        store = re.fullmatch(r'\s*(.+?)\s*=(?!=)\s*(.*?)\s*;\s*', text, re.S)
        if not store or not pure(store[1]) or not pure(store[2]):
            continue
        operands = differences(store[2])
        if not operands or not all(narrow_load(s, part.clean) for s in operands):
            continue
        # Array or explicit pointer stores only; never change a scalar's own lifetime here.
        if '[' not in store[1] and not store[1].lstrip().startswith('*'):
            continue
        destination = unparen(store[1])
        halfword = bool(destination.startswith('*') and re.match(
            r'\(\s*(?:s16|u16|short|signed\s+short|unsigned\s+short)\s*\*\s*\)',
            unparen(destination[1:])))
        sites.append((node, store[1], operands, halfword))
    if len(sites) < 2:
        return []
    locals_ = [d for d in flow.locals if d['type'] in WORD
               and part.clean[part.opening+1:d.start()].count('{') == part.clean[part.opening+1:d.start()].count('}')]
    result = []
    # Prefer an unused scratch plus an existing dead one, then other pairs in declaration order.
    locals_ = locals_[:MAX_SCRATCH]
    used = {d['name']: bool(refs(part, d)) for d in locals_}
    pairs = list(itertools.permutations(locals_, 2))
    pairs.sort(key=lambda pair: (used[pair[0]['name']], pair[0].start(), pair[1].start()))
    for left, right in pairs:
        selected = [(node, dest, operands, halfword) for node, dest, operands, halfword in sites
                    if left.end() < node.start and right.end() < node.start
                    and flow.dead(left['name'], node.start) and flow.dead(right['name'], node.start)
                    and not re.search(r'\b(?:'+left['name']+'|'+right['name']+r')\b', dest+' '.join(operands))]
        if len(selected) < 2:
            continue
        # Compare the narrow-store cluster, full-width cluster and their bounded combination.
        for group in (selected, [s for s in selected if s[3]], [s for s in selected if not s[3]]):
            if len(group) < 2:
                continue
            edits = []
            for node, dest, (a, b), halfword in group:
                if halfword:
                    a, b = f'(unsigned short)({a})', f'(unsigned short)({b})'
                edits.append((node.start, node.end,
                              f"{left['name']} = {a}; {right['name']} = {b}; {dest} = {left['name']} - {right['name']};"))
            candidate = edit(source, edits)
            if candidate not in [s for _, s in result]:
                result.append((FAMILIES[0], candidate))
            if len(result) >= LIMIT:
                return result
    return result


def guards(source, name):
    """One identical-arm guard at a time; its condition must be an ordinary scalar local."""
    flow = analyze(source, name)
    if not flow:
        return []
    result, part = [], flow.part
    for hit in re.finditer(r'\bif\s*\(\s*('+IDENT+r')\s*\)\s*{', part.body):
        declaration = re.search(r'\b(?:int|s32|u32|unsigned(?:\s+int)?)\s+'
                                + re.escape(hit[1])+r'\s*=\s*[^;{}]+;', part.clean)
        if (not declaration or declaration.start() < part.opening
                or part.clean[part.opening+1:declaration.start()].rstrip()[-1:] not in {'', ';', '}'}
                or part.clean[part.opening+1:declaration.start()].count('{') != part.clean[part.opening+1:declaration.start()].count('}')
                or len(re.findall(r'\b(?:int|s32|u32|unsigned|short|char)\s+\**\s*'+re.escape(hit[1])+r'\b', part.body)) != 1):
            continue
        opening = part.opening+1+hit.end()-1
        end = close(part.clean, opening, '{', '}')
        other = re.match(r'\s*else\s*{', part.clean[end:]) if end else None
        if not other:
            continue
        second = end+other.end()-1
        stop = close(part.clean, second, '{', '}')
        arm = part.clean[opening+1:end-1].strip()
        if (stop and arm == part.clean[second+1:stop-1].strip() and arm.count(';') == 1
                and not re.search(r'[{}:]|\b(?:int|unsigned|signed|short|long|char|float|double|struct|union|goto|break|continue)\b', arm)):
            result.append((part.opening+1+hit.start(), stop, source[opening+1:end-1].strip()))
    return result[:4]


def counter_variants(source, name):
    flow = analyze(source, name)
    if not flow:
        return []
    part, result = flow.part, []
    for hit in re.finditer(r'\bfor\s*\(', part.body):
        opening = part.opening+hit.end()
        end = close(part.clean, opening)
        if not end:
            continue
        sections = part.clean[opening+1:end-1].split(';')
        if len(sections) != 3:
            continue
        init, condition, step = sections
        initial = [init[a:b].strip() for a, b in split_args(init)]
        steps = [step[a:b].strip() for a, b in split_args(step)]
        index = re.fullmatch(r'('+IDENT+r')\s*=\s*0', initial[0]) if initial else None
        if not index:
            continue
        idx = index[1]
        bound = re.fullmatch(re.escape(idx)+r'\s*<\s*([1-9]\d{0,3})', condition.strip())
        if not bound or not steps or steps[0] not in (idx+'++', '++'+idx):
            continue
        body = end
        while part.clean[body:body+1].isspace():
            body += 1
        stop = close(part.clean, body, '{', '}') if part.clean[body:body+1] == '{' else None
        if not stop or len(initial) != 2 or len(steps) != 2:
            continue
        counter = re.fullmatch(r'('+IDENT+r')\s*=\s*0', initial[1])
        if not counter:
            continue
        variable = counter[1]
        stride = re.fullmatch(re.escape(variable)+r'\s*\+=\s*([1-9]\d{0,3})', steps[1])
        decl = next((d for d in flow.locals if d['name'] == variable and d['type'] in WORD), None)
        # Require an ordinary unsigned or signed word index, not a field, narrow value or alias.
        if (not stride or not decl or variable == idx or
                not re.search(r'\b(?:unsigned(?:\s+int)?|int|u32|s32)\s+'+re.escape(idx)+r'\s*;', part.clean) or
                re.search(r'&\s*'+re.escape(idx)+r'\b', part.body) or
                int(bound[1])*int(stride[1]) > 2147483647):
            continue
        body_text = part.clean[body+1:stop-1]
        mutation = r'\b(?:'+re.escape(variable)+'|'+re.escape(idx)+r')\s*(?:[+*/%&|^\-]?=|\+\+|--)|(?:\+\+|--)\s*(?:'+re.escape(variable)+'|'+re.escape(idx)+r')\b'
        if (re.search(mutation, body_text) or
                re.search(r'\b(?:unsigned(?:\s+int)?|int|u32|s32|short|char|long)\s+\**\s*'+re.escape(idx)+r'\b', body_text)):
            continue
        uses = [h for h in refs(part, decl) if body < h.start() < stop]
        if not uses or any(h.start() >= stop for h in refs(part, decl)):
            continue
        # The first read must be a whole assignment statement; its insertion must dominate all reads.
        first = flow.at(uses[0].start())
        if (not first or first.start < body or first.start > uses[0].start()
                or not re.fullmatch(IDENT+r'\s*=(?!=)[^;]+;', part.clean[first.start:first.end].strip(), re.S)):
            continue
        first_index = flow.nodes.index(first)
        loop_nodes = {i for i, n in enumerate(flow.nodes) if body < n.start < stop}
        # Reject incoming jumps into the loop, even if textual ordering looks harmless.
        if any(j in loop_nodes for i, n in enumerate(flow.nodes) if i not in loop_nodes
               for j in n.successors if i in {g[0] for g in flow.gotos}):
            continue
        # Any path to a read without visiting the insertion point is unsafe (including back jumps).
        seen, pending = set(), [i for i, n in enumerate(flow.nodes) if n.start == opening+1]
        while pending:
            i = pending.pop()
            if i in seen or i == first_index:
                continue
            seen.add(i)
            pending.extend(flow.nodes[i].successors)
        if any(flow.nodes.index(flow.at(h.start())) in seen for h in uses):
            continue
        assignment = f'{variable} = {idx} * {stride[1]}; '
        candidate = edit(source, [(opening+1, end-1, f'{initial[0]};{condition}; {steps[0]}'),
                                  (first.start, first.start, assignment)])
        result.append((FAMILIES[1], candidate))
        for guard in guards(candidate, name):
            result.append((FAMILIES[2], edit(candidate, [guard])))
        if len(result) >= LIMIT:
            break
    return result[:LIMIT]


def variants(source, name):
    return operand_variants(source, name) + counter_variants(source, name)
