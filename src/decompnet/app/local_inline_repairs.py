"""Inline a once-assigned local at one of its uses, or all of them.

m2c names every intermediate (`off = (a & 0xFFFF) * 100;`) and reuses it. The original often wrote the
expression again at some uses, and the compiler's value reuse follows that: an agent matched a function by
replacing the temporary at just one of its reads. The expression must be pure (no calls, assignments,
increments or address-of) and none of its operands may change after the temporary is set.
"""
import re

from .integer_repair_support import edit, view

FAMILY = 'single_use_inline'
FAMILIES = (FAMILY,)
_TYPE = r'(?:unsigned\s+(?:char|short|int)|[su](?:8|16|32)|int|short|char|f32)\s*\**'
_PURE = re.compile(r'[\w\s()+\-*/%&|^~<>!.\[\]]+\Z')


def _plain(expression):
    """Arithmetic on names and numbers: no call, no assignment, no increment, no address-of."""
    # (the address of a game global is a constant; the address of a local is not)
    return bool(_PURE.match(expression)) and not re.search(r'\w\s*\(|\+\+|--|(?:^|[(+\-*/%&|^~<>!\[])\s*&(?!\s*D_[0-9A-Fa-f]+\b)|&(?=[A-Za-z_(])(?!D_[0-9A-Fa-f]+\b)', expression)


def _changed(name, text):
    """`name` is assigned, stepped or address-taken in `text` (writing through a pointer, `*name = x`, is not)."""
    return bool(re.search(rf'(?<![*\w]){name}\s*(?:[-+*/|&^]|<<|>>)?=(?!=)|\b{name}\s*(?:\+\+|--)|(?:\+\+|--)\s*{name}\b|&\s*{name}\b', text))


def _assignments(clean, base, end):
    """(start, stop, local, expression) of `T local = e;` and of `local = e;` for a local declared earlier."""
    text = clean[base:end]
    for decl in re.finditer(rf'(?m)^[ \t]*{_TYPE}\s*(\w+)\s*=\s*([^;{{}}]+);', text):
        yield base + decl.start(), base + decl.end(), decl[1], base + decl.start(2), base + decl.end(2)
    for decl in re.finditer(rf'(?m)^[ \t]*{_TYPE}\s*(\w+)\s*;', text):
        later = re.search(rf'(?m)^[ \t]*{decl[1]}\s*=\s*([^;{{}}=][^;{{}}]*);', text[decl.end():])
        if later:
            first = decl.end() + later.start()
            yield base + first, base + decl.end() + later.end(), decl[1], base + decl.end() + later.start(1), base + decl.end() + later.end(1)


def _adjacent(source, part):
    """`x = E;` followed at once by the only statement that reads `x`: E (calls allowed) moves into it.

    The reading statement may only store: another call or step in it could swap order with E."""
    clean, base, result = part.clean, part.opening + 1, []
    for decl in re.finditer(r'(?m)^([ \t]*)(\w+)\s*=\s*([^;{}=][^;{}]*);[ \t]*\n([ \t]*[^;{}\n]+;)', clean[base:part.end]):
        local, nxt = decl[2], decl[4]
        if not re.search(rf'^\s*[*\w\[\]().+\- >]*=\s*(?:\([^()]*\)\s*)?{local}\s*;$', nxt) or re.search(r'\w\s*\(\s*[^)]*\)\s*[^;]*\b' + local, nxt.split('=', 1)[0]):
            continue
        rest = clean[base + decl.end():part.end]
        if re.search(rf'\b{local}\b', rest) or len(re.findall(rf'\b{local}\b', nxt)) != 1:
            continue
        if re.search(r'\w\s*\(\s*[^)]*\)\s*\(|\+\+|--', nxt) or re.search(r'\b\w+\s*\(', nxt.replace('(u8 *)', '').replace('(u32)', '')):
            continue
        expression = source[base + decl.start(3):base + decl.end(3)].strip()
        use = base + decl.start(4) + nxt.index(local, nxt.index('='))
        start, end = base + decl.start(), base + decl.end(3) + 1
        # drop the assignment (its line), then put E in the use; the declaration of `x` stays unused
        text = edit(source, [(use, use + len(local), '(' + expression + ')'), (start, source.index('\n', end) + 1, '')])
        result.append((FAMILY, text))
    return result


def variants(source, name, limit=8):
    part = view(source, name)
    if not part:
        return []
    clean = part.clean
    result = _adjacent(source, part)
    for start, end, local, first, last in _assignments(clean, part.opening + 1, part.end):
        expression = source[first:last].strip()
        if clean[part.opening+1:start].count('{') != clean[part.opening+1:start].count('}') or not _plain(expression):
            continue
        body = clean[end:part.end]
        if _changed(local, body) or any(_changed(o, body) for o in set(re.findall(r'[A-Za-z_]\w*', expression))):
            continue
        uses = [end + h.start() for h in re.finditer(rf'\b{local}\b', body)]
        if len(uses) < 2:
            continue

        def spelled(position, expression=expression, local=local):
            if re.fullmatch(r'\w+', expression):
                return expression
            before, after = clean[:position].rstrip()[-1:], clean[position + len(local):].lstrip()[:1]
            return expression if before == '[' and after == ']' else '(' + expression + ')'

        edits = [[(u, u + len(local), spelled(u))] for u in uses]
        for group in edits + ([[e for g in edits for e in g]] if len(uses) > 1 else []):
            result.append((FAMILY, edit(source, group)))
        if len(result) >= limit:
            break
    return result[:limit]
