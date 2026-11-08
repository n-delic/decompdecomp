"""Bounded normalization/lifetime hypotheses; only the ordinary judge accepts candidates."""
import re

from ..forks.csource import _blank
from .integer_repair_support import IDENT, NUMBER, edit, integer, locals_for, refs, view

FAMILIES = ('call_return_normalization', 'split_narrow_lifetime', 'spill_normalization')
SIGNED = {'int', 's32', 'signed int'}
NARROW = r'(?:s16|short|signed short)'


def context(source, name):
    part = view(source, name)
    if not part or re.search(r'\b(?:asm|__asm__|sizeof|typeof|__typeof__)\b', part.clean):
        return None, {}
    return part, {d['name']: d for d in locals_for(part)}


def call_returns(source, name, header=''):
    part, declarations = context(source, name)
    fresh = '__repair_call_return'
    if not part or fresh in source or fresh in header:
        return []
    # One ordinary normalization followed immediately by a void setter. The other
    # arguments must be a local pointer and a literal: no hidden evaluation changes.
    pattern = rf'\b({IDENT})\s*=\s*\(\s*({NARROW})\s*\)\s*({IDENT})\s*;\s*({IDENT})\s*\(\s*({IDENT})\s*,\s*\1\s*,\s*({NUMBER})\s*\)\s*;'
    result = []
    for hit in re.finditer(pattern, part.body):
        dest, narrow, value, callee, pointer, constant = hit.groups()
        prefix = part.body[:hit.start()].rstrip()
        if prefix and prefix[-1] not in ';{}':
            continue
        if dest not in declarations or value not in declarations or declarations[dest]['type'] not in SIGNED:
            continue
        if declarations[value]['type'] not in SIGNED | {'short', 's16'}:
            continue
        # Reject ambiguous prototypes and local/shadowed callees, and keep the
        # original callee declaration: the repair never changes its ABI.
        prototypes = list(re.finditer(rf'\bvoid\s+{re.escape(callee)}\s*\(\s*((?:void|char|s32|u32|int)\s*\*)\s*,\s*{NARROW}\s*,\s*(?:s32|int)\s*\)\s*;', _blank(header)+'\n'+part.clean[:part.opening]))
        pointers = re.findall(rf'(?:^|(?<=[;{{}}]))\s*(?:void|char|s32|u32|int)\s*\*\s*{re.escape(pointer)}\s*;', part.body)
        pointer_declarations = re.findall(rf'\b(?:void|char|s32|u32|int)\s*\*\s*{re.escape(pointer)}\s*(?=[;=,\[])', part.body)
        if len(prototypes) != 1 or len(pointers) != 1 or len(pointer_declarations) != 1 or callee == name:
            continue
        if re.search(rf'\b{re.escape(callee)}\s*(?:=|;)|\(\s*\*\s*{re.escape(callee)}\b|&\s*{re.escape(pointer)}\b', part.body):
            continue
        typ = declarations[dest]['type']
        definition = f'static inline {typ} {fresh}({prototypes[0][1]} p, {typ} h){{h=({narrow})h;{callee}(p,h,{constant});return h;}}\n'
        a, b = part.opening+1+hit.start(), part.opening+1+hit.end()
        candidate = edit(source, [(a, b, f'{dest}={fresh}({pointer},{value});')])
        # Insert after declarations/includes and before this definition, not at
        # the top of the translation unit where typedefs/prototypes may be absent.
        result.append(definition_before(candidate, name, definition))
        if len(result) == 4:
            break
    return result


def definition_before(source, name, definition):
    part = view(source, name)
    header = list(re.finditer(rf'\b{re.escape(name)}\s*\(', part.clean[:part.opening]))[-1]
    start = max(part.clean.rfind(c, 0, header.start()) for c in ';{}')+1
    return source[:start]+'\n'+definition+source[start:]


def split_lifetimes(source, name):
    part, declarations = context(source, name)
    fresh = '__repair_saved_narrow'
    if not part or fresh in source:
        return []
    result = []
    for key, decl in declarations.items():
        if decl['type'] not in SIGNED:
            continue
        uses = refs(part, decl)
        casts = list(re.finditer(rf'\(\s*({NARROW})\s*\)\s*\b{re.escape(key)}\b', part.body))
        if not casts:
            continue
        first_read = part.opening+1+casts[0].start()
        # Start a new phase after the last earlier read. All remaining accesses
        # must be simple assignments or signed halfword reads, never RMW/escape.
        assignments = list(re.finditer(rf'\b{re.escape(key)}\s*=\s*({IDENT})\s*;', part.body))
        for assignment in assignments:
            pivot = part.opening+1+assignment.start()
            if pivot >= first_read:
                continue
            tail = [u for u in uses if u.start() >= pivot]
            writes = [h for h in assignments if part.opening+1+h.start() >= pivot]
            if not writes or len(writes) > 4 or any(h[1] not in declarations for h in writes):
                continue
            allowed = {part.opening+1+h.start() for h in writes}
            allowed |= {part.opening+1+h.end()-len(key) for h in casts}
            if any(u.start() not in allowed for u in tail):
                continue
            before = part.clean[part.opening+1:pivot]
            if re.search(rf'\b{re.escape(key)}\s*(?:[+*/%&|^-]=|\+\+|--)|(?:\+\+|--)\s*\b{re.escape(key)}\b', before):
                continue
            previous = list(re.finditer(rf'\b{re.escape(key)}\s*=(?!=)\s*[^;{{}}]+;', before))
            if not previous or len(previous) > 8:
                continue
            # Mirror every earlier write, not only an initial zero: if the later
            # phase is skipped, its narrowed fallback must still equal the old
            # variable. Braces preserve an unbraced conditional's scope.
            if any(before[:h.start()].count('(') != before[:h.start()].count(')') for h in previous):
                continue
            narrow = casts[0][1]
            edits = [(decl.end(), decl.end(), '\n'+narrow+' '+fresh+';')]
            edits += [(part.opening+1+h.start(), part.opening+1+h.end(),
                       '{'+source[part.opening+1+h.start():part.opening+1+h.end()]+fresh+'=('+narrow+')'+key+';}')
                      for h in previous]
            edits += [(u.start(), u.end(), fresh) for u in tail]
            result.append(edit(source, edits))
            break
        if len(result) == 4:
            break
    return result


def frame_gap(rows):
    """A partial MIPS frame gap, not an address/constant/inserted-code mismatch."""
    if not rows or any(not isinstance(r, (tuple, list)) or len(r) != 3 or
                       any(not isinstance(c, str) for c in r) for r in rows):
        return None
    delta = None
    unchanged = False
    offsets = []
    for marker, left, right in rows or []:
        if marker in {'<', '>', '|', 'o'}:
            return None
        if marker == ' ' and re.search(r'\b(?:lw|sw|lh|sh|lhu)\b.*\(\$sp\)', left):
            unchanged = True
        if marker != 's':
            continue
        a = re.fullmatch(r'addiu \$sp, \$sp, (-?0x[\da-f]+|-?\d+)', left)
        b = re.fullmatch(r'addiu \$sp, \$sp, (-?0x[\da-f]+|-?\d+)', right)
        if a and b:
            gap = abs(int(a[1], 0))-abs(int(b[1], 0))
            if delta is not None and delta != gap:
                return None
            delta = gap
        else:
            a = re.fullmatch(r'(.+), (-?0x[\da-f]+|-?\d+)\(\$sp\)', left)
            b = re.fullmatch(r'(.+), (-?0x[\da-f]+|-?\d+)\(\$sp\)', right)
            if not a or not b or a[1] != b[1]:
                return None
            # Defer consistency until the prologue has supplied its gap.
            offsets.append(int(a[2], 0)-int(b[2], 0))
    return delta if unchanged and delta and 0 < delta <= 64 and delta % 8 == 0 and all(d in (0, delta) for d in offsets) else None


def frame_variants(source, name, rows):
    gap = frame_gap(rows)
    part, declarations = context(source, name)
    fresh = '__repair_spill_normalize'
    if not gap or not part or fresh in source:
        return []
    sites = []
    # Narrow only values subsequently masked to low 15 bits. This preserves
    # every bit the expression observes; no mask, array extent or load changes.
    for hit in re.finditer(rf'\b({IDENT})\s*&=\s*({NUMBER})\s*;', part.body):
        if hit[1] in declarations and declarations[hit[1]]['type'] in SIGNED and 0 < integer(hit[2]) <= 0x7fff:
            a = part.opening+1+hit.start()
            sites.append((a, a, f'{hit[1]}={fresh}({hit[1]});'))
    for hit in re.finditer(rf'\(\s*({IDENT})\s*&\s*({NUMBER})\s*\)', part.body):
        if hit[1] in declarations and declarations[hit[1]]['type'] in SIGNED and 0 < integer(hit[2]) <= 0x7fff:
            a = part.opening+1+hit.start(1)
            sites.append((a, a+len(hit[1]), f'{fresh}({hit[1]})'))
    sites = sorted(set(sites))[-12:]
    needed = gap//8
    if len(sites) < needed:
        return []
    definition = f'static inline short {fresh}(int x){{x=(unsigned int)x<<16;x=(int)x>>16;return x;}}\n'
    result = []
    # Direct bundles cross six-step score plateaus without unbounded beam depth.
    # Try both source orders and a few windows; every bundle spends one compile.
    for count in dict.fromkeys([needed, 1, min(2, needed)]):
        for chosen in [sites[-count:], sites[:count], sites[max(0, len(sites)//2-count//2):][:count]]:
            if len(chosen) == count:
                result.append(definition_before(edit(source, chosen), name, definition))
    return list(dict.fromkeys(result))[:8]


def variants(source, name, header=''):
    return [('call_return_normalization', c) for c in call_returns(source, name, header)] + [
        ('split_narrow_lifetime', c) for c in split_lifetimes(source, name)]


def extend_frame(candidates, labels, source, name, arch, rows):
    if arch != 'mips':
        return
    for candidate in frame_variants(source, name, rows):
        if candidate not in labels:
            candidates.append(candidate)
            labels[candidate] = 'spill_normalization'
