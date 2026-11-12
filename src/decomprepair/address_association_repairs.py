"""Bounded byte-pointer address association hypotheses; the compiler/link judge decides every result.

Only scalar identifier indices and literal displacements are reassociated, without reordering loads,
function calls or updates. Pointer bounds/types remain the game's responsibility, not an equivalence claim.
"""
import re

from .csource import _blank, find
from .integer_repair_support import IDENT, NUMBER, edit
from .pattern_repairs import close
from .register_repairs import unwrap

LIMIT = 12


def terms(text):
    text = unwrap(text)
    depth = 0
    for index, char in enumerate(text):
        depth += (char in '([')-(char in ')]')
        if char == '+' and depth == 0:
            if text[index-1:index] == '+' or text[index+1:index+2] == '+':
                return [text]
            return terms(text[:index])+terms(text[index+1:])
    return [text]


def variants(source, name):
    """Try base-plus-displacement before index, including candidate-owned inline address helpers."""
    clean = _blank(source)
    span = find(source, name)
    if not span or re.search(r'\b(?:volatile|asm|__asm__)\b', clean):
        return []
    spans = [span]
    # A helper must be a candidate-owned inline definition called by this function, not another game body.
    calls = set(re.findall(rf'\b({IDENT})\s*\(', clean[span[0]:span[1]]))
    # Candidate-owned inline helpers are often reached through one-line wrapper
    # macros. Follow invocation dependencies only; never rewrite the macro itself.
    macros = {m[1]: m[2] for m in re.finditer(r'^\s*#\s*define\s+('+IDENT+r')(?:\([^\n)]*\))?[ \t]+([^\n]+)', clean, re.M)
              if not re.search(r'[#\\]', m[2])}
    pending = list(calls)
    seen = set()
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.add(key)
        if key in macros:
            nested = set(re.findall(rf'\b({IDENT})\s*\(', macros[key]))
            calls.update(nested)
            pending.extend(nested)
    for helper in re.finditer(rf'\binline\b[^;{{}}]*?\b({IDENT})\s*\([^;{{}}]*\)\s*{{', clean):
        if helper[1] in calls and (owned := find(source, helper[1])):
            spans.append(owned)
    result = []
    for start, end in spans:
        opening = clean.find('{', start, end)
        if opening < 0 or '#' in clean[opening:end]:
            continue
        signature = clean[start:opening]
        pointers = set(re.findall(rf'\b(?:char|u8|s8|unsigned\s+char|signed\s+char)\s*\*\s*({IDENT})\b',
                                  signature + clean[opening:end]))
        for base in sorted(pointers):
            # Flatten only addition-only operands, then reintroduce deliberate association. No casts,
            # signed shifts, subtraction, dereference, increments, or another arithmetic operand.
            term = rf'(?:{IDENT}(?:\s*\*\s*{NUMBER})?|{NUMBER})'
            pattern = re.compile(rf'\b{base}\s*\+\s*(?:\(\s*)?(?P<a>{term})\s*\+\s*(?P<b>{term})(?:\s*\))?')
            for hit in pattern.finditer(clean, opening, end):
                a, b = hit['a'].strip(), hit['b'].strip()
                if re.match(r'\s*(?:[\w*+/%&|^.<>=?-]|->)', clean[hit.end():end]):
                    continue
                before = clean[:hit.start()].rstrip()
                if before[-1:] not in {'(', '=', ',', '['}:
                    continue
                # Do not consume a close parenthesis belonging to an outer cast/address wrapper.
                old = source[hit.start():hit.end()]
                if old.count('(') != old.count(')'):
                    old = old.rstrip()
                    while old.endswith(')') and old.count(')') > old.count('('):
                        old = old[:-1].rstrip()
                stop = hit.start()+len(old)
                if re.fullmatch(NUMBER, a):
                    displacement, index = a, b
                elif re.fullmatch(NUMBER, b):
                    displacement, index = b, a
                else:
                    continue
                for expression in (f'({base} + {displacement}) + ({index})',
                                   f'{base} + (({index}) + {displacement})'):
                    candidate = edit(source, [(hit.start(), stop, expression)])
                    if candidate != source and candidate not in result:
                        result.append(candidate)
                if len(result) >= LIMIT:
                    return [('address_association', text) for text in result]
            # Array subscript association also matters inside candidate-owned
            # inline helpers. Keep the base and index evaluations exactly once.
            subscript = re.compile(rf'\b{base}\s*\[\s*(?P<index>{IDENT})\s*\+\s*(?P<n>{NUMBER})\s*\]')
            for hit in subscript.finditer(clean, opening, end):
                result.append(edit(source, [(hit.start(), hit.end(),
                    f"({base} + {hit['index']})[{hit['n']}]")]))
        grouped = re.compile(rf'\(\s*(?P<base>{IDENT})\s*\+\s*(?P<index>{IDENT}(?:\s*\*\s*{NUMBER})?)\s*\)\s*\+\s*(?P<n>{NUMBER})(?!\w)')
        for hit in grouped.finditer(clean, opening, end):
            if hit['base'] in pointers and not re.match(r'\s*(?:[\w*+/%&|^.<>=?-]|->)', clean[hit.end():end]):
                result.append(edit(source, [(hit.start(), hit.end(), f"({hit['base']} + {hit['n']}) + ({hit['index']})")]))
        for opening_match in re.finditer(r'\(', clean[opening:end]):
            begin = opening+opening_match.start()
            stop = close(clean,begin)
            if not stop or stop-begin > 250:
                continue
            parts = terms(clean[begin:stop])
            bases = [part for part in parts if part in pointers]
            numbers = [part for part in parts if re.fullmatch(NUMBER,part)]
            if len(parts) == 3 and len(bases) == len(numbers) == 1:
                index = next(part for part in parts if part not in bases+numbers)
                if re.fullmatch(rf'{IDENT}(?:\s*\*\s*{NUMBER})?',index):
                    result.append(edit(source,[(begin,stop,f"(({bases[0]} + {numbers[0]}) + ({index}))")]))
        # A signed computed offset in a raw byte-pointer field load can use an owned inline helper.
        # Each operand is still evaluated once; the helper is an allocation hypothesis, not a type inference.
        helper = '__repair_byte_address'
        all_pointers = set(re.findall(rf'\b(?:char|u8|s8)\s*\*\s*({IDENT})\b',clean))
        if helper not in clean and (start,end) == span:
            for cast in re.finditer(r'\(\s*(?:u16|s16|u32|s32|short|int)\s*\*\s*\)\s*(?=\()',clean[opening:end]):
                begin = opening+cast.end()
                stop = close(clean,begin)
                if not stop:
                    continue
                outer = terms(clean[begin:stop])
                if len(outer) != 2 or not re.fullmatch(NUMBER,outer[1]):
                    continue
                left = unwrap(outer[0])
                left = re.sub(r'^\(\s*(?:char|u8|s8)\s*\*\s*\)\s*','',left)
                inner = terms(left)
                if len(inner) != 2:
                    continue
                base = next((part for part in inner if part in all_pointers),None)
                if not base:
                    continue
                offset = inner[1] if inner[0] == base else inner[0]
                if not re.search(r'\(\s*(?:s32|int)\s*\)',offset) or re.search(r'\+\+|--|=|\b'+IDENT+r'\s*\(',offset):
                    continue
                prefix = f'static inline char *{helper}(char *base, int offset) {{ return (base + {outer[1]}) + offset; }}\n'
                result.append(prefix+edit(source,[(begin,stop,f'{helper}({base}, {offset})')]))
                break
        # A pre-associated base can also be flattened, preserving each operand once.
        pattern = re.compile(rf'\(\s*(?P<base>{IDENT})\s*\+\s*(?P<n>{NUMBER})\s*\)\s*\+\s*(?P<index>{IDENT})(?!\w)')
        for hit in pattern.finditer(clean, opening, end):
            if hit['base'] in pointers and not re.match(r'\s*(?:\+\+|--|\[|->|\.)', clean[hit.end():]):
                result.append(edit(source, [(hit.start(), hit.end(), f"{hit['base']} + ({hit['index']} + {hit['n']})")]))
    return [('address_association', text) for text in dict.fromkeys(result)][:LIMIT]
