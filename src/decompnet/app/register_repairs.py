"""Bounded register-allocation hypotheses; only the ordinary exact judge can accept them."""
import re

from ..forks.csource import _blank, find
from .pattern_repairs import close

IDENT = r'[A-Za-z_]\w*'
NUMBER = r'(?:0[xX][0-9a-fA-F]+|[0-9]+)'
ARCH32 = {'mips', 'ppc', 'x86', 'arm', 'thumb'}


def unwrap(text):
    text = text.strip()
    while text.startswith('(') and close(text, 0) == len(text):
        text = text[1:-1].strip()
    return text


def variants(source, name, arch):
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    opening = clean.find('{', *span)
    if opening < 0 or '#' in clean[opening:span[1]] or re.search(r'\bvolatile\b', clean):
        return []
    body = clean[opening:span[1]]
    original = source[opening:span[1]]
    result = []
    # A dead, already declared integer temporary can hold a repeated field comparison. Restrict
    # reuse to locals unread before their next simple assignment, never parameters or live values.
    compare = re.compile(rf'\bif\s*\(\s*(?P<field>{IDENT}\s*(?:->|\.)\s*{IDENT})\s*(?:<|<=|>|>=)\s*{NUMBER}\s*\)')
    groups = {}
    for m in compare.finditer(body):
        groups.setdefault(re.sub(r'\s+', '', m['field']), []).append(m)
    for field, matches in groups.items():
        if len(matches) < 2:
            continue
        first, last = matches[0], matches[-1]
        gap = body[first.end():last.start()]
        if re.search(r'\b(?:while|for|goto|switch)\b|\w\s*\(', gap):
            continue
        for decl in re.finditer(rf'\b(?:s32|u32|int|unsigned\s+int)\s+({IDENT})\s*;', body[:first.start()]):
            var = decl[1]
            if re.search(r'\b'+re.escape(var)+r'\b', body[decl.end():last.end()]):
                continue
            later = re.search(r'\b'+re.escape(var)+r'\b', body[last.end():])
            if not later:
                continue
            assignment = re.match(r'\s*=(?!=)([^;{}]*);', body[last.end()+later.end():])
            if not assignment or re.search(r'\b'+re.escape(var)+r'\b', assignment[1]):
                continue
            for cast in ('unsigned int', 'int'):
                changed = original
                for m in reversed(matches):
                    changed = changed[:m.start('field')]+f'({cast}){var}'+changed[m.end('field'):]
                changed = changed[:first.start()]+f'{var} = {source[opening+first.start("field"):opening+first.end("field")]};\n  '+changed[first.start():]
                result.append(('comparison_reuse', source[:opening]+changed+source[span[1]:]))
            if len(result) >= 4:
                break
        if len(result) >= 4:
            break
    if arch not in ARCH32:
        return result
    # Repeated byte-pointer table loads plus a byte offset. Convert the coordinated group, retaining
    # every load/evaluation in place. Integer widths are only valid on these 32-bit processors.
    address_groups = {}
    load = re.compile(rf'^\*\s*\(\s*\(\s*(?:u8|s8|char|unsigned\s+char)\s*\*\s*\*\s*\)\s*\(\s*{IDENT}\s*\+\s*{NUMBER}\s*\)\s*\)$')
    for start, ch in enumerate(body):
        if ch != '(':
            continue
        end = close(body, start)
        if end is None:
            continue
        inner = body[start+1:end-1]
        depth = 0
        for pos, token in enumerate(inner):
            depth += (token == '(')-(token == ')')
            if token != '+' or depth:
                continue
            lhs, rhs = inner[:pos].strip(), inner[pos+1:].strip()
            if re.fullmatch(IDENT, rhs) and load.fullmatch(unwrap(lhs)):
                address_groups.setdefault(rhs, []).append((start, end, lhs))
    for index, edits in list(address_groups.items())[:2]:
        for cast in ('unsigned int', 'int'):
            changed = original
            for start, end, lhs in reversed(edits):
                changed = changed[:start]+f'({index} + ({cast}){lhs})'+changed[end:]
            result.append(('integer_address_order', source[:opening]+changed+source[span[1]:]))
    return result
