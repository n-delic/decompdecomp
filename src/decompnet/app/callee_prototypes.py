"""Prototypes of a function's matched callees, for its machine draft.

The shared header often doesn't declare a function that is already matched: its prototype is only in its own source.
m2c then guesses the parameters from the caller's registers and stack words, so floats passed to the callee came out
as integers and bytes as words. A spawn routine of 896 instructions that calls a matched 18-parameter function drafted
at 25161 without the prototype and 635 with it (0 after three library steps).

The prototype is a hypothesis for the draft only (like return_contract_evidence): it is given to m2c and, marked so
seeds.generate_from_asm keeps it, written into the draft's own source (m2c declares nothing it was given), never into
the shared header. A definition whose types the draft can't name is left out.

ROM projects only: a project-mode draft has its source file's own headers (ROADMAP.md).
"""
import re

from ..forks.csource import _blank, find
from ..seeds import CALLEE_MARK

LIMIT = 24
_BUILTIN = {'void', 'char', 'short', 'int', 'long', 'float', 'double', 'signed', 'unsigned'}
_QUALIFIERS = {'const', 'volatile', 'register'}
_JAL = re.compile(r'\bjal\s+([A-Za-z_]\w*)')


def _type(text, header, named):
    """The type of one return value or parameter as the draft can spell it, or None. A pointer to a type only the
    callee's source declares becomes `void *`; typedef names the header declares are collected in `named`."""
    stars = text.count('*') + text.count('[')
    words = [w for w in re.sub(r'\[[^\]]*\]|\*', ' ', text).split() if w not in _QUALIFIERS]
    if not words or any(not re.fullmatch(r'[A-Za-z_]\w*', w) for w in words):
        return None
    unknown = []
    for word in words:
        if word in _BUILTIN:
            continue
        if re.search(rf'\btypedef\b[^;{{}}()]*[\s*]{word}\s*;', header):
            named.add(word)
        else:
            unknown.append(word)
    if unknown:
        return 'void ' + '*' * stars if stars else None
    return ' '.join(words) + (' ' + '*' * stars if stars else '')


def prototype(source, name, header):
    """(prototype, typedef names it needs) of `name`'s definition in `source`, or None."""
    span = find(source, name) if source else None
    if not span:
        return None
    text = _blank(source[span[0]:span[1]])
    head = re.match(rf'\s*([^(){{}};=#]*?)\b{re.escape(name)}\s*\(([^(){{}};]*)\)\s*\{{', text)
    if not head or re.search(r'\b(?:static|inline|extern)\b', head[1]):
        return None
    named = set()
    returns = _type(head[1], header, named)
    parameters = head[2].strip()
    if returns is None or not parameters or '...' in parameters:
        return None
    types = ['void']
    if parameters != 'void':
        types = []
        for parameter in parameters.split(','):
            # the name is the last word (with any array brackets); an unnamed parameter is not a definition's
            match = re.fullmatch(r'(.*?[\s*])[A-Za-z_]\w*\s*(\[[^\]]*\])?', parameter.strip(), re.S)
            kind = _type(match[1] + (match[2] or ''), header, named) if match else None
            if kind is None:
                return None
            types.append(kind)
    return f'{returns} {name}({", ".join(types)});', named


def of_callees(net, proj, fn, asm, header):
    """([prototype lines], {typedef names}) of the matched functions `fn` calls that the header doesn't declare."""
    blank = _blank(header)
    names = sorted({n for n in _JAL.findall(asm or '') if n != fn['name']
                    and not re.search(rf'\b{re.escape(n)}\b', blank)})[:LIMIT]
    if not names:
        return [], set()
    marks = ','.join('?' * len(names))
    rows = net.project_store(proj['id']).all(
        f"SELECT name, matched_source FROM functions WHERE project_id=? AND status='matched' AND name IN ({marks}) "
        'ORDER BY name', proj['id'], *names)
    lines, needed = [], set()
    for row in rows:
        if found := prototype(row['matched_source'], row['name'], blank):
            lines.append(f'{found[0]} {CALLEE_MARK}')
            needed |= found[1]
    return lines, needed
