"""Bundle a pointer's branch-local integer storage with a sibling debug scope.

Some debug-enabled MIPS compilers schedule these coupled edits differently even when either edit
alone is neutral or worse. Keep the bundle small; the normal linked/object judge decides acceptance.
"""
import re

from ..forks.csource import _blank, find

IDENT = r'[A-Za-z_]\w*'


def _close(text, start, left, right):
    depth = 0
    for pos in range(start, len(text)):
        depth += (text[pos] == left) - (text[pos] == right)
        if depth == 0:
            return pos
    return None


def variants(source, name, arch, flags):
    # Imported builds supply no flags until their per-file settings can be verified (ROADMAP).
    if arch != 'mips' or not any(re.fullmatch(r'-g[1-3]?', flag) for flag in flags):
        return []
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    definition = re.search(r'\b' + re.escape(name) + r'\s*\([^{};]*\)\s*{', clean[span[0]:span[1]])
    if not definition:
        return []
    opening = span[0] + definition.end() - 1
    body = clean[opening:span[1]]
    if any(quote in source[opening:span[1]] for quote in ('\"', "'")) or '#' in body or re.search(r'\b(?:volatile|goto|asm|__asm__)\b', body):
        return []
    declarations = list(re.finditer(r'(?:^|(?<=[;{}]))\s*void\s*\*\s*(' + IDENT + r')\s*;', body))
    result = []
    for test in re.finditer(r'\bif\s*\(', body):
        end = _close(body, test.end()-1, '(', ')')
        arm = re.match(r'\s*{', body[end+1:]) if end is not None else None
        if not arm:
            continue
        first = end + 1 + arm.end() - 1
        middle = _close(body, first, '{', '}')
        sibling = re.match(r'\s*else\s*{', body[middle+1:]) if middle is not None else None
        if not sibling:
            continue
        second = middle + 1 + sibling.end() - 1
        last = _close(body, second, '{', '}')
        if last is None:
            continue
        # The existing lexical scope is evidence for trying the corresponding sibling scope.
        if not re.search(r'\bdo\s*{[^{}]+}\s*while\s*\(\s*0\s*\)\s*;', body[first:middle]):
            continue
        for decl in declarations:
            var = decl[1]
            if len([d for d in declarations if d[1] == var]) != 1 or re.search(r'&\s*' + var + r'\b', body):
                continue
            if decl.end() >= test.start() or len(re.findall(r'\b' + var + r'\b', body[:first])) != 1:
                continue
            load_pattern = r'\b' + var + r'\s*=\s*\*\s*\(\s*void\s*\*\s*\*\s*\)\s*([^;{}]+);'
            load = re.search(load_pattern, body[first:middle])
            other = list(re.finditer(load_pattern, body[second:last]))
            if not load or len(other) != 1 or re.search(r'\+\+|--|=|\b' + IDENT + r'\s*\(', load[1]):
                continue
            first_body = body[first:middle]
            if re.search(r'\b' + var + r'\b', first_body[:load.start()] + body[last+1:]):
                continue
            # All reads must be explicit byte-address casts; no escaping or other pointer uses.
            remaining = first_body[:load.start()] + first_body[load.end():]
            remaining = re.sub(r'\(\s*char\s*\*\s*\)\s*' + var + r'\b', '', remaining)
            if re.search(r'\b' + var + r'\b', remaining):
                continue
            tail_start = second + other[0].end()
            tail = body[tail_start:last]
            statements = re.findall(r'[^;]+;', tail)
            if not 2 <= len(statements) <= 6 or ''.join(statements).strip() != tail.strip():
                continue
            if not all(re.fullmatch(r'\s*' + IDENT + r'\s*=(?!=)[^;{}]+;', stmt) for stmt in statements):
                continue
            if re.search(r'\b(?:if|for|while|return|break|continue)\b|\+\+|--|\b' + IDENT + r'\s*\(', tail):
                continue
            fresh = '__repair_pointer_bits'
            while re.search(r'\b' + fresh + r'\b', clean):
                fresh += '_'
            text = source[opening+first:opening+middle]
            loaded = re.sub(r'\(\s*void\s*\*\s*\*\s*\)', '(int*)',
                            text[load.start():load.end()], count=1)
            text = text[:load.start()] + loaded + text[load.end():]
            text = re.sub(r'\b' + var + r'\b', fresh, text)
            edits = [(first, middle, text), (decl.end(), decl.end(), '\nint ' + fresh + ';'),
                     (tail_start, tail_start, '\ndo {\n'), (last, last, '\n} while(0);\n')]
            candidate = source
            for a, b, replacement in sorted(edits, reverse=True):
                candidate = candidate[:opening+a] + replacement + candidate[opening+b:]
            result.append(candidate)
            if len(result) == 2:
                return result
    return list(dict.fromkeys(result))
