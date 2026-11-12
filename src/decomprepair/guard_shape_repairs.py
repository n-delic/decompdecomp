"""Guard shapes m2c writes from the branch layout, where the original source stated them the other way round.

Two rewrites, both exact in meaning, so the compiler's own output is the only thing that changes:
- `if (p == NULL) { return V; } do { ... } while (p != NULL); return V;` is `while (p != NULL) { ... }`: m2c
  puts the loop's entry test in front of a bottom-tested loop, the source had one top-tested loop.
- a void function's leading `if (c) { A; return; } REST` is `if (!c) { REST } else { A }`: m2c turns the else
  into an early return.
Both are repairs agents made by hand in an N64 game built with gcc 2.7.2; the compiler decides, as for every family.
"""
import re

from .integer_repair_support import edit, view
from .pattern_repairs import close, negate

FAMILY = 'guard_shape'
FAMILIES = (FAMILY,)


def opposite(condition):
    """The condition's exact logical inverse for a single comparison, `x` or `!x`; None otherwise."""
    condition = condition.strip()
    if re.fullmatch(r'!\s*\w+', condition):
        return condition[1:].strip()
    if re.fullmatch(r'\w+', condition):
        return '!' + condition
    return negate(condition)


def _guards(part):
    """Each `if (cond) {` at the function's top level, as (start, condition, block start, block end)."""
    clean, base = part.clean, part.opening + 1
    for hit in re.finditer(r'\bif\s*\(', clean[base:part.end]):
        start = base + hit.start()
        if clean[base:start].count('{') != clean[base:start].count('}'):
            continue
        end = close(clean, base + hit.end() - 1)
        block = re.match(r'\s*{', clean[end:]) if end else None
        if not block:
            continue
        opening = end + block.end() - 1
        after = close(clean, opening, '{', '}')
        if after:
            yield start, part.source[base+hit.end():end-1], opening, after


def _entry_tests(source, part):
    """`if (c) { return V; } do { BODY } while (not c); return V;` as one while loop."""
    clean = part.clean
    result = []
    for start, condition, opening, after in _guards(part):
        arm = re.fullmatch(r'\s*return(\s+[^;{}]+)?;\s*', clean[opening+1:after-1])
        loop = re.match(r'\s*do\s*{', clean[after:])
        inverse = opposite(condition)
        if not arm or not loop or not inverse:
            continue
        body_open = after + loop.end() - 1
        body_end = close(clean, body_open, '{', '}')
        tail = re.match(r'\s*while\s*\(', clean[body_end:]) if body_end else None
        if not tail:
            continue
        test_end = close(clean, body_end + tail.end() - 1)
        test = source[body_end+tail.end():test_end-1] if test_end else ''
        finish = re.match(r'\s*;\s*return(\s+[^;{}]+)?;', clean[test_end:]) if test_end else None
        if not finish or ' '.join(test.split()) != ' '.join(inverse.split()):
            continue
        if ' '.join((arm[1] or '').split()) != ' '.join((finish[1] or '').split()):
            continue
        # The test is now evaluated on entry and after each pass, exactly where the guard and the latch were.
        result.append((start, test_end + 1, 'while (' + test + ') {' + source[body_open+1:body_end-1] + '}'))
    return result


def _early_returns(source, part):
    """A void function's `if (c) { A; return; } REST` as `if (not c) { REST } else { A }`."""
    clean = part.clean
    stop = clean.rfind('}', 0, part.end)
    result = []
    for start, condition, opening, after in _guards(part):
        inverse = opposite(condition)
        arm = re.fullmatch(r'(.*?)\breturn\s*;\s*', clean[opening+1:after-1], re.S)
        rest = clean[after:stop]
        if not inverse or not arm or not rest.strip() or re.match(r'\s*else\b', rest):
            continue
        # Only the arm's last statement leaves; a goto or label could enter REST from outside the new block.
        if re.search(r'\breturn\b', arm[1]) or re.search(r'\bgoto\b|(?m:^\s*(?!case\b|default\b)\w+\s*:)', rest + arm[1]):
            continue
        arm_text = source[opening+1:opening+1+len(arm[1])].rstrip()
        indented = re.sub(r'(?m)^(?=[ \t]*\S)', '    ', source[after:stop].rstrip() + '\n')
        text = 'if (' + inverse + ') {' + indented + '    }'
        if arm_text.strip():
            text += ' else {' + arm_text + '\n    }'
        result.append((start, stop, text + '\n'))
    return result


def _value_returns(source, part):
    """`if (c) { A; return X; } return Y;` as `if (not c) { return Y; } A; return X;`: the guard first."""
    clean = part.clean
    stop = clean.rfind('}', 0, part.end)
    result = []
    for start, condition, opening, after in _guards(part):
        inverse = opposite(condition)
        tail = re.fullmatch(r'\s*return\s+([\w.\->\s]+?)\s*;\s*', clean[after:stop])
        arm = clean[opening+1:after-1]
        if not inverse or not tail or not re.search(r'\breturn\b[^;{}]*;\s*$', arm) or re.search(r'\bgoto\b', arm):
            continue
        # (the arm's own text, one level out)
        lines = source[opening+1:after-1].strip('\n').splitlines()
        body = '\n'.join(l[4:] if l.startswith('    ') else l for l in lines).rstrip()
        result.append((start, stop, f'if ({inverse}) {{\n        return {tail[1]};\n    }}\n{body}\n'))
    return result


def variants(source, name):
    part = view(source, name)
    if not part or part.opening < 0:
        return []
    result = []
    for rewrite in (_entry_tests, _early_returns, _value_returns):
        for start, stop, text in rewrite(source, part):
            result.append((FAMILY, edit(source, [(start, stop, text)])))
    return result[:4]
