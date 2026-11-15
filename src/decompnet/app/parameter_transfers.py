"""Bounded whole-parameter transfers from register-identical MIPS siblings.

A threshold, its negative coefficient adjustment and positive signed-division reciprocal
must agree. This only proposes C; the normal destination linker/judge still decides.
"""
import re

from ..arch.mips_parameters import scale_changes


def variants(source, a, b, big):
    from .similar_matches import INTEGER, TOKENS

    result = []
    masked = TOKENS.sub(lambda m: ' ' * len(m[0]) if m[0].startswith(('/*', '//', '"', "'")) else m[0], source)
    for old, new in scale_changes(a, b, big):
        edits, roles = [], set()
        for token in TOKENS.finditer(source):
            m = INTEGER.fullmatch(token[0])
            if not m:
                continue
            try:
                value = int(m[1], 16 if m[1].lower().startswith('0x') else 8 if m[1].startswith('0') and len(m[1]) > 1 else 10)
            except ValueError:
                continue
            if value != old:
                continue
            before = masked[:token.start()].rstrip()
            # Limit edits to these arithmetic roles, leaving array sizes and additive offsets alone.
            role = next((op for op in ('/', '-', '<') if before.endswith(op) and not before.endswith(op*2)), None)
            if role is None or re.search(r'^\s*#', before.split('\n')[-1]):
                continue
            roles.add(role)
            edits.append((token.start(), token.end(), str(new) + m[2]))
        if roles != {'/', '-', '<'} or not 3 <= len(edits) <= 32:
            continue
        text = source
        for start, end, value in reversed(edits):
            text = text[:start] + value + text[end:]
        result.append(text)
    return result


def destination_signature(source, name, destination):
    """Keep donor parameter names/body, taking plain parameter types from the saved destination.

    Complex declarators, macros, K&R and ambiguous signatures remain unchanged.
    """
    from ..forks.csource import _code_only, find

    def parameters(text):
        span = find(text, name)
        if span is None:
            return None
        match = re.search(r'\b' + re.escape(name) + r'\s*\(([^()]*)\)\s*\{', _code_only(text)[span[0]:span[1]])
        if not match:
            return None
        items = []
        offset = span[0] + match.start(1)
        for param in match[1].split(','):
            declaration = re.fullmatch(r'\s*((?:[A-Za-z_]\w*\s+|\*\s*)+)([A-Za-z_]\w*)\s*', param)
            if not declaration:
                return None
            items.append((declaration[1], declaration[2], offset + declaration.start(1)))
            offset += len(param) + 1
        return span[0] + match.start(1), span[0] + match.end(1), items

    own, donor = parameters(destination), parameters(source)
    if not own or not donor or len(own[2]) != len(donor[2]):
        return source
    edits = []
    for (wanted, _, _), (existing, _, start) in zip(own[2], donor[2]):
        tokens = list(re.finditer(r'[A-Za-z_]\w*|\*', existing))
        replacement = re.findall(r'[A-Za-z_]\w*|\*', wanted)
        if len(tokens) != len(replacement):
            return source
        edits.extend((start + t.start(), start + t.end(), r) for t, r in zip(tokens, replacement))
    for start, end, replacement in reversed(edits):
        source = source[:start] + replacement + source[end:]
    return source
