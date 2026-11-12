"""A narrow unsigned parameter written as a full word that is masked where it is used.

m2c declares `u16 arg0` from the caller's prototype. The original took an `int` and masked at each use
(`(arg0 & 0xFFFF) * 100`), so the compiler loads and combines the parameter differently. The signature
changes to the word type and every read of the parameter gets the mask. The caller's prototype is
unchanged: a narrow argument is promoted either way.
"""
import re

from .integer_repair_support import edit, view

FAMILY = 'parameter_mask'
FAMILIES = (FAMILY,)
_MASKS = {'u16': '0xFFFF', 'unsigned short': '0xFFFF', 'u8': '0xFF', 'unsigned char': '0xFF'}


def variants(source, name):
    part = view(source, name)
    if not part:
        return []
    clean = part.clean
    header = re.search(r'\(([^()]*)\)\s*$', clean[:part.opening])
    if not header:
        return []
    result = []
    base = header.start(1)
    for arg in re.finditer(r'(?<![\w])(u16|u8|unsigned short|unsigned char)\s+([A-Za-z_]\w*)\s*(?=,|$)', header[1]):
        mask, local = _MASKS[arg[1]], arg[2]
        body = clean[part.opening+1:part.end]
        uses = [part.opening + 1 + h.start() for h in re.finditer(rf'(?<![\w.>]){local}\b', body)]
        # Only plain reads: a parameter that is assigned or addressed keeps its narrow type.
        if not uses or re.search(rf'(?<![*\w]){local}\s*(?:[-+*/|&^]|<<|>>)?=(?!=)|&\s*{local}\b|\+\+\s*{local}|{local}\s*\+\+|--\s*{local}|{local}\s*--', body):
            continue
        typ = base + arg.start(1), base + arg.end(1)
        masked = [(u, u + len(local), f'({local} & {mask})') for u in uses]
        result.append((FAMILY, edit(source, masked + [(typ[0], typ[1], 's32')])))
    # The same rewrite usually also wrote the index temporary out at its reads.
    from .local_inline_repairs import variants as inlined
    result += [(FAMILY, text) for _, masked in result[:2] for _, text in inlined(masked, name, limit=4)]
    return result[:6]
