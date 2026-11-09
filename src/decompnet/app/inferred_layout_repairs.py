"""Candidate-local records from repeated, consistent explicit field accesses.

This does not assert a game's object size or publish a shared type. Conflicting/overlapping fields,
qualified accesses, dynamic offsets and unknown pointer widths remain for an agent to investigate.
The normal linked/object judge is the only acceptance path.
"""
import re

from ..forks.csource import find
from .compiler_shape_repairs import NUMBER, SCALARS, number
from .integer_repair_support import IDENT, edit, view
from .pattern_repairs import close

MAX_OFFSET, MAX_FIELDS, MAX_RECORDS = 1024, 12, 2
TYPE = r'(?:unsigned\s+(?:char|short|int)|signed\s+char|void\s*\*|char|short|int|float|[su](?:8|16|32)|f32)'
CANONICAL = {'s8':'signed char', 'u8':'unsigned char', 's16':'short', 'u16':'unsigned short',
             's32':'int', 'u32':'unsigned int', 'f32':'float'}


def strip_outer(text):
    text = text.strip()
    while text.startswith('(') and close(text, 0) == len(text):
        text = text[1:-1].strip()
    return text


def accesses(body, implicit_bases=()):
    """Read only complete cast-and-byte-offset dereferences, including m2c's extra parentheses."""
    found = []
    for star in re.finditer(r'\*', body):
        for wrappers in range(3):
            pos = star.end()
            for _ in range(wrappers):
                prefix = re.match(r'\s*\(', body[pos:])
                if not prefix:
                    break
                pos += prefix.end()
            else:
                cast = re.match(rf'\s*\(\s*({TYPE})\s*\*\s*\)\s*', body[pos:])
                if not cast:
                    continue
                address_start = pos+cast.end()
                if body[address_start:address_start+1] != '(':
                    continue
                end = close(body, address_start)
                if not end:
                    continue
                address = strip_outer(body[address_start:end])
                # Split at a top-level plus, so byte-pointer casts cannot be mistaken for scaling.
                depth, split = 0, None
                for index, char in enumerate(address):
                    depth += (char == '(')-(char == ')')
                    if char == '+' and depth == 0:
                        split = index
                        break
                if split is None:
                    continue
                left, right = strip_outer(address[:split]), address[split+1:].strip()
                base = re.fullmatch(rf'\(\s*(?:char|[su]8|unsigned\s+char|signed\s+char)\s*\*\s*\)\s*({IDENT})', left)
                if not re.fullmatch(NUMBER, right):
                    continue
                base_name = base[1] if base else left if left in implicit_bases else None
                if base_name is None:
                    continue
                finish = end
                for _ in range(wrappers):
                    tail = re.match(r'\s*\)', body[finish:])
                    if not tail:
                        break
                    finish += tail.end()
                else:
                    typ = ' '.join(cast[1].split())
                    found.append((star.start(), finish, base_name, CANONICAL.get(typ, typ), number(right)))
                    break
    return found


def local_byte_bases(part):
    """Only initialized local byte pointers whose every later reference is a scalar offset access."""
    if re.search(r'^\s*#\s*(?:define|if|elif|else|endif|undef)\b', part.clean, re.M):
        return set()
    bases = set()
    byte = r'(?:char|[su]8|unsigned\s+char|signed\s+char)'
    declarations = re.finditer(rf'(?:^|(?<=[;{{}}]))\s*{byte}\s*\*\s*({IDENT})\s*=\s*{IDENT}\s*;', part.body)
    for declaration in declarations:
        base = declaration[1]
        hits = accesses(part.body, (base,))
        # Counting every token also excludes shadows, reassignment, address escape and pointer aliases.
        refs = list(re.finditer(rf'\b{re.escape(base)}\b', part.body))
        if all(declaration.start() <= ref.start() < declaration.end()
               or any(start <= ref.start() < end for start, end, *_ in hits) for ref in refs):
            bases.add(base)
    return bases


def variants(source, name, arch):
    part = view(source, name)
    if not part or arch not in {'mips', 'ppc', 'x86', 'arm', 'thumb'}:
        return []
    if re.search(r'\b(?:volatile|restrict|asm|__asm__)\b', part.clean):
        return []
    groups = {}
    for hit in accesses(part.body, local_byte_bases(part)):
        groups.setdefault(hit[2], []).append(hit)
    result = []
    for base, hits in groups.items():
        fields, conflict = {}, False
        for _, _, _, typ, offset in hits:
            size = 4 if typ == 'void *' else SCALARS.get(typ)
            if size is None or offset > MAX_OFFSET or offset % size or fields.get(offset, (typ, size)) != (typ, size):
                conflict = True
                break
            fields[offset] = (typ, size)
        if conflict or not 2 <= len(fields) <= MAX_FIELDS or len(hits) < 3:
            continue
        layout, cursor = [], 0
        for offset, (typ, size) in sorted(fields.items()):
            if offset < cursor:
                conflict = True
                break
            if offset > cursor:
                layout.append(f'unsigned char pad_{cursor:X}[{offset-cursor}];')
            layout.append(f'{typ} field_{offset:X};')
            cursor = offset+size
        if conflict:
            continue
        fresh = '__repair_record_' + base
        while re.search(rf'\b{fresh}\b', part.clean):
            fresh += '_'
        offset = part.opening+1
        edits = [(offset+a, offset+b, f'(({fresh}*){base})->field_{field:X}') for a,b,_,_,field in hits]
        edits.append((find(source, name)[0], find(source, name)[0],
                      'typedef struct { ' + ' '.join(layout) + ' } ' + fresh + ';\n'))
        result.append(('inferred_record_layout', edit(source, edits)))
        if len(result) >= MAX_RECORDS:
            break
    return result
