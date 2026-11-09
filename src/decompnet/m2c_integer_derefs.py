"""Dereferences of integers in an m2c draft, given the pointer type they were read through.

m2c writes `*(var_v1 + arg1)` when it never learned that `var_v1` is a pointer: every local it could not type is an
`s32`, and a sum of integers cannot be dereferenced, so the draft is rejected before the judge sees it (Penny Racers:
43 of the 122 drafts that had C but did not compile). The original code loaded or stored through that address with a
width the instruction says; the draft's assignment says the same thing (`temp_a0 = *(...)` with `s16 temp_a0`).

Only a `*` whose operand is provably an integer is touched: no pointer, array or address-of in it, and no field of
pointer type. The cast takes the width of the variable the value goes to or comes from, else `s32`, and a typed
pointer is never recast. Used on the refined draft, next to m2c's own, never instead of it.
"""
from __future__ import annotations

import re

SCALARS = {'s8', 'u8', 's16', 'u16', 's32', 'u32', 's64', 'u64', 'f32', 'f64', 'char', 'short', 'int', 'float',
           'double', 'unsigned', 'signed', 'M2C_UNK', 'M2C_UNK8', 'M2C_UNK16', 'M2C_UNK32'}
_UNKNOWN = {'M2C_UNK': 's32', 'M2C_UNK8': 's8', 'M2C_UNK16': 's16', 'M2C_UNK32': 's32'}
_KEYWORDS = {'return', 'goto', 'else', 'break', 'continue', 'case', 'default', 'do'}
_BEFORE_UNARY = set('(,=+-*/%<>!?:|&^~{;[')
_DECLARATION = re.compile(r'^[ \t]*(?:extern[ \t]+)?((?:struct[ \t]+)?[A-Za-z_]\w*(?:[ \t]+[A-Za-z_]\w*)*?)[ \t]*'
                          r'(\**)[ \t]*([A-Za-z_]\w*)(\[[^\]]*\])?[ \t]*(?:=[^;]*)?;[ \t]*$', re.M)


def _blank(text: str) -> str:
    from .forks.csource import _blank as blank
    return blank(text)


def _closing(text: str, start: int) -> int:
    from .m2cfix import _closing as closing
    return closing(text, start)


def declared(source: str, name: str) -> tuple[set[str], dict[str, str]]:
    """(identifiers that are pointers or arrays, the scalar type of each other identifier) in the source."""
    from .m2cfix import _definition, _split_top_level
    pointers: set[str] = set()
    scalars: dict[str, str] = {}

    def note(kind: str, stars: str, ident: str, array: str | None) -> None:
        if kind in _KEYWORDS:
            return
        if kind == 'void' and stars == '*' and not array:
            return  # (a `void *` can't be dereferenced either: it needs the cast like an integer)
        if stars or array:
            pointers.add(ident)
        else:
            scalars[ident] = _UNKNOWN.get(kind.split()[-1], kind)

    for match in _DECLARATION.finditer(_blank(source)):
        note(match[1], match[2], match[3], match[4])
    signature = _definition(source, name)
    for parameter in _split_top_level(signature[2]) if signature else []:
        found = re.fullmatch(r'\s*((?:struct\s+)?[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*?)\s*(\**)\s*([A-Za-z_]\w*)(\[[^\]]*\])?\s*', parameter)
        if found:
            note(found[1], found[2], found[3], found[4])
    return pointers, scalars


def _is_pointer(expr: str, pointers: set[str]) -> bool:
    """Whether the expression, as C reads it, can be a pointer (so dereferencing it is not our business)."""
    i, previous, unary = 0, '', True
    while i < len(expr):
        ch = expr[i]
        if ch.isspace():
            i += 1
        elif ch == '(':
            end = _closing(expr, i + 1)
            inner = expr[i + 1:end - 1]
            if re.fullmatch(r'\s*[A-Za-z_][\w\s]*\*+\s*', inner):
                return True  # a cast to a pointer type
            if re.fullmatch(r'\w+', previous) and previous != 'return':
                if previous == 'M2C_FIELD':
                    kind = _split_arguments(inner)
                    if len(kind) > 1 and kind[1].count('*') >= 2 and not re.fullmatch(r'void\s*\*\s*\*', kind[1]):
                        return True  # a field that itself holds a typed pointer
            elif previous == '*' and unary:
                pass  # the integer a dereference reads
            elif _is_pointer(inner, pointers):
                return True
            i, previous, unary = end, ')', False
        elif ch.isalpha() or ch == '_':
            end = i
            while end < len(expr) and (expr[end].isalnum() or expr[end] == '_'):
                end += 1
            word = expr[i:end]
            if word in pointers:
                return True
            i, previous, unary = end, word, False
        elif ch.isdigit():
            end = i
            while end < len(expr) and (expr[end].isalnum() or expr[end] in '._'):
                end += 1
            i, previous, unary = end, '0', False
        else:
            if ch == '&' and unary:
                return True  # an address
            i, previous, unary = i + 1, ch, True
    return False


def _split_arguments(text: str) -> list[str]:
    from .m2cfix import _split_top_level
    return [part.strip() for part in _split_top_level(text)]


def _unary(text: str, at: int) -> bool:
    """Whether the `*` at `at` is a dereference (it follows an operator, not an operand or a type: `u8 **p`)."""
    before = text[:at].rstrip()
    if not before:
        return True
    if before[-1] in _BEFORE_UNARY:
        chain = before.rstrip('* \t')  # (a run of stars after a type name is a declarator)
        return not (chain and (chain[-1].isalnum() or chain[-1] == '_') and not re.search(r'\breturn$', chain)
                    and before[-1] == '*')
    return bool(re.search(r'\breturn$', before)
                or re.search(r'\(\s*(?:s8|u8|s16|u16|s32|u32|s64|u64|f32|f64)\s*\)$', before))  # (u8) *p


_DECLARES = re.compile(r'\s*(?:(?:unsigned|signed|struct|const|volatile|static|extern)\s+)*'
                       r'(?:s8|u8|s16|u16|s32|u32|s64|u64|f32|f64|void|char|short|int|float|double)\b')


def _width(text: str, at: int, end: int, scalars: dict[str, str]) -> str:
    """The type of the access: what the surrounding assignment or cast says, else a word."""
    start = max(text.rfind(';', 0, at), text.rfind('{', 0, at), text.rfind('}', 0, at)) + 1
    stop = text.find(';', end)
    statement = text[start:stop if stop >= 0 else len(text)]
    local = at - start
    cast = re.search(r'\(\s*(s8|u8|s16|u16|s32|u32|f32|f64)\s*\)\s*$', statement[:local])
    if cast:
        return cast[1]
    load = re.match(r'\s*(\w+)\s*=\s*$', statement[:local])
    if load and scalars.get(load[1]) in SCALARS:
        return scalars[load[1]]
    store = re.match(r'\s*\*\s*\(', statement) and statement[:local].strip() == ''
    value = re.match(r'\s*=\s*(\w+(?:\.\d+)?f?)\s*$', text[end:stop]) if store and stop >= 0 else None
    if value:
        word = value[1]
        if scalars.get(word) in SCALARS:
            return scalars[word]
        if re.fullmatch(r'\d+\.\d*f?', word):
            return 'f32'
    return 's32'


def typed_dereferences(source: str, name: str) -> str:
    """`source` with each dereference of an integer given the pointer type its access implies."""
    from .forks.csource import find
    span = find(source, name)
    if not span:
        return source
    pointers, scalars = declared(source, name)
    opening = _blank(source).index('{', span[0], span[1])
    text = source
    clean = _blank(text)
    stars = [m.start() for m in re.finditer(r'\*', clean[opening:span[1]])]
    for relative in reversed(stars):
        at = opening + relative
        clean = _blank(text)
        if clean[at] != '*' or not _unary(clean, at) or re.match(r'[\s*]*[),]', clean[at + 1:]):
            continue  # (`(**)` is a function pointer type, not a dereference)
        start = max(clean.rfind(';', 0, at), clean.rfind('{', 0, at), clean.rfind('}', 0, at)) + 1
        if _DECLARES.match(clean[start:at]) and '=' not in clean[start:at]:
            continue  # a declaration: `u16 (*p)[];`
        k = at + 1
        while k < len(clean) and clean[k] in ' \t':
            k += 1
        if k >= len(clean):
            continue
        if clean[k] == '(':
            end = _closing(clean, k + 1)
        elif clean[k] == '*':
            end = k  # a dereference of a dereference: what the inner one reads (it is settled first) is an integer
        elif clean[k].isalpha() or clean[k] == '_':
            end = k
            while end < len(clean) and (clean[end].isalnum() or clean[end] == '_'):
                end += 1
            if (clean[end:end + 1] in ('[', '.', '(') or clean[end:end + 2] == '->' or clean[k:end] in pointers
                    or re.match(r'\s*\)\s*[\[(]', clean[end:])):
                continue
        else:
            continue
        if clean[k] != '*' and _is_pointer(clean[k:end], pointers):
            continue
        kind = _width(clean, at, end, scalars)
        text = text[:at + 1] + '(' + kind + ' *)' + text[at + 1:]
    return text
