"""Share a pure scaled local across nearby address expressions; judge every candidate normally."""
import re

from . import pointer_pair_repairs
from .call_lifetime_repairs import block_path
from .integer_repair_support import DECL, IDENT, NUMBER, edit, integer

FAMILY = 'shared_scaled_offset'


def variants(source, name, header=''):
    part, _, macros = pointer_pair_repairs.context(source, name, header)
    fresh = '__repair_scaled_offset'
    if not part or re.search(rf'\b{fresh}\b', source+'\n'+header):
        return []
    out = []
    declarations = [d for d in DECL.finditer(part.clean, part.opening+1, part.end-1)
                    if part.clean[:d.start()].rstrip()[-1:] in {';', '{', '}'}]
    for decl in declarations:
        var = decl['name']
        if re.search(rf'(?<!&)&(?!&)\s*(?:\(\s*)*{var}\b', part.body):
            continue
        scope = block_path(part, decl.start())
        pattern = re.compile(rf'\b{var}\s*\*\s*({NUMBER})\b')
        groups = {}
        for hit in pattern.finditer(part.clean, decl.end(), part.end-1):
            path = tuple(block_path(part, hit.start()))
            visible = [d for d in declarations if d['name'] == var and d.end() < hit.start()
                       and list(path[:len(block_path(part, d.start()))]) == block_path(part, d.start())]
            if (not visible or max(visible, key=lambda d: len(block_path(part, d.start()))) != decl
                    or list(path[:len(scope)]) != scope):
                continue
            groups.setdefault((hit[1], path), []).append(hit)
        for (scale, path), hits in groups.items():
            if len(hits) < 2 or len(hits) > 8 or re.search(r'[lL]', scale) or not 1 <= integer(scale) <= 256:
                continue
            # Only scaled address operands, with no calls, jumps, index writes or
            # unknown macros between their evaluations. Local storage cannot escape.
            first, last = hits[0], hits[-1]
            begin = max(part.clean.rfind(ch, path[-1], first.start()) for ch in ';{}')+1
            stop = part.clean.find(';', last.end(), part.end)
            if stop < 0 or not pointer_pair_repairs.pure(part.clean[begin:first.start()], macros):
                # Prefix includes the store's '=' only after the address expression.
                continue
            region = part.clean[begin:stop+1]
            if re.search(r'\b(?:if|else|for|do|while|switch|goto|return|case|break|continue)\b|[{}?:]', region):
                continue
            without = pattern.sub('0', region)
            if re.search(rf'\b{var}\b', without):
                continue  # even an apparent later definition needs a fresh computation
            # The original product must be an address, never a multiplicative value
            # passed to a call or a standalone scalar result. Only raw typed stores.
            stores = re.findall(r'\*\s*\(\s*(?:s32|u32|int|unsigned\s+int)\s*\*\s*\)\s*\([^;]+?\)\s*=\s*(?:0|1)\s*;', region)
            if len(stores) != len(hits) or any(not pattern.search(store) for store in stores):
                continue
            remainder = region
            for store in stores:
                remainder = remainder.replace(store, '', 1)
            # Intervening pointer rebases may read memory, but cannot contain
            # hidden calls/writes or change the index through an alias.
            assignments = [s for s in remainder.split(';') if s.strip()]
            if not all(re.fullmatch(rf'\s*{IDENT}\s*=(?!=)[^;]+', s)
                       and pointer_pair_repairs.pure(s.split('=', 1)[1], macros) for s in assignments):
                continue
            typ = decl['type'] if decl['type'] in {'s32', 'u32', 'int', 'unsigned int'} else 'int'
            if re.search(r'[uU]', scale):
                typ = 'unsigned int'
            changes = [(hit.start(), hit.end(), fresh) for hit in hits]
            changes += [(part.opening+1, part.opening+1, f'\n{typ} {fresh};\n'),
                        (begin, begin, f'\n{fresh} = {var} * {scale};\n')]
            out.append((FAMILY, edit(source, changes)))
            if len(out) >= 4:
                return out
    return list(dict.fromkeys(out))
