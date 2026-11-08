"""Bounded copy, typed-address and lexical-scope hypotheses for old C compilers.

No shared types are invented or edited. Shapes are alternatives, never replacements for saved C;
only the ordinary compiler and exact judge can accept them.
"""
import re

from ..forks.csource import _blank
from .integer_repair_support import IDENT, edit, view
from .pattern_repairs import close

LIMIT = 12
NUMBER = r'(?:0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)'
SCALARS = {'char': 1, 'signed char': 1, 'unsigned char': 1, 's8': 1, 'u8': 1,
           'short': 2, 'unsigned short': 2, 's16': 2, 'u16': 2,
           'int': 4, 'unsigned int': 4, 's32': 4, 'u32': 4, 'float': 4, 'f32': 4}


def number(text):
    return int(text, 16 if text.lower().startswith('0x') else 8 if text.startswith('0') else 10)


def small_copies(source, name, header=''):
    """Recognize a halfword plus byte copied between declared byte arrays of sufficient size.

Distinct local/global arrays prove non-overlap and bounds. Pointer arguments, scalar pseudo-globals,
volatile objects and guessed adjacent stack locals must not become an out-of-bounds memcpy.
"""
    part = view(source, name)
    if not part or re.search(r'\bvolatile\b', part.body):
        return []
    arrays = {}
    for hit in re.finditer(rf'\b(?:const\s+)?(?:char|signed\s+char|unsigned\s+char|[su]8)\s+({IDENT})\s*\[\s*({NUMBER})\s*\]', _blank(header+'\n'+source)):
        arrays[hit[1]] = number(hit[2])
    pattern = re.compile(
        rf'\*\s*\(\s*(?:s16|u16|short|unsigned\s+short)\s*\*\s*\)\s*({IDENT})\s*=\s*'
        rf'\*\s*\(\s*(?:s16|u16|short|unsigned\s+short)\s*\*\s*\)\s*({IDENT})\s*;\s*'
        rf'\1\s*\[\s*2\s*\]\s*=\s*\2\s*\[\s*2\s*\]\s*;')
    result, edits = [], []
    for hit in pattern.finditer(part.body):
        dest, src = hit[1], hit[2]
        # Reject shadows and macro aliases, even if an outer declaration had an adequate bound.
        if dest == src or min(arrays.get(dest, 0), arrays.get(src, 0)) < 3:
            continue
        if re.search(rf'\bvolatile\b[^;{{}}]*\b(?:{dest}|{src})\b', _blank(header+'\n'+source)):
            continue
        if any(len(re.findall(rf'\b(?:char|[su]8)\s+{obj}\s*\[\s*{NUMBER}\s*\]', _blank(header+'\n'+source))) != 1
               or re.search(rf'^\s*#\s*define\s+{obj}\b', source+'\n'+header, re.M) for obj in (dest, src)):
            continue
        replacement = (part.opening+1+hit.start(), part.opening+1+hit.end(), f'__builtin_memcpy({dest}, {src}, 3);')
        edits.append(replacement)
        result.append(edit(source, [replacement]))
    if len(edits) > 1:
        result.insert(0, edit(source, edits))
    return list(dict.fromkeys(result))[:4]


def fields(header):
    """Only ordinary scalar/padding records whose 32-bit C layout can be established here."""
    clean = _blank(header)
    if re.search(r'^\s*#\s*pragma\s+pack\b', clean, re.M):
        return []
    result = []
    field = re.compile(rf'\s*([A-Za-z_][\w ]*?|[A-Za-z_]\w*\s*\*)\s+({IDENT})(?:\s*\[\s*({NUMBER})\s*\])?\s*;')
    for record in re.finditer(rf'\btypedef\s+struct\s*(?:{IDENT}\s*)?{{([^{{}}]*)}}\s*({IDENT})\s*;', clean):
        offset = 0
        members = list(field.finditer(record[1]))
        if field.sub('', record[1]).strip():
            continue
        known = []
        for member in members:
            typ = ' '.join(member[1].split())
            size = 4 if typ.endswith('*') else SCALARS.get(typ)
            if size is None:
                break
            offset = (offset+size-1)//size*size
            if not member[3]:
                known.append((record[2], typ, member[2], offset))
            offset += size * (number(member[3]) if member[3] else 1)
        else:
            result.extend(known)
    return result


def variants(source, name, arch, flags=(), header=''):
    part = view(source, name)
    if not part or arch not in {'mips', 'ppc', 'x86', 'arm', 'thumb'}:
        return []
    if re.search(r'\b(?:volatile|asm|__asm__)\b', part.body):
        return []
    result = [('small_copy', text) for text in small_copies(source, name, header)]
    body = part.body
    offset = part.opening+1
    # Existing header fields supply type/offset evidence, rather than a guessed new shared layout.
    for record, typ, member, address in fields(header+'\n'+source[:part.opening]):
        pattern = re.compile(rf'\*\s*\(\s*{re.escape(typ)}\s*\*\s*\)\s*\(\s*\(\s*(?:char|[su]8)\s*\*\s*\)\s*({IDENT})\s*\+\s*({NUMBER})\s*\)')
        hits = [hit for hit in pattern.finditer(body) if number(hit[2]) == address]
        if hits:
            result.append(('typed_record_address', edit(source, [
                (offset+h.start(), offset+h.end(), f'(({record}*){h[1]})->{member}') for h in hits])))
        if len(result) >= LIMIT:
            break
    # An artificial one-shot loop can constrain lifetimes even without break/continue.
    for wrapper in re.finditer(r'\bdo\s*{', body):
        end = close(body, wrapper.end()-1, '{', '}')
        tail = re.match(r'\s*while\s*\(\s*0\s*\)\s*;', body[end:]) if end else None
        inner = body[wrapper.end():end-1] if end else ''
        if not tail or re.search(r'\b(?:break|continue|goto|case|default)\b|\b\w+\s*:', inner):
            continue
        # Keep the original scope: removing only the synthetic loop cannot expose shadowed locals.
        result.append(('one_shot_scope', edit(source, [(offset+wrapper.start(), offset+wrapper.end()-1, ''),
                                                     (offset+end, offset+end+tail.end(), '')])))
        if not re.search(r'\b(?:char|short|int|long|float|double|void|[su]\d+|[A-Z]\w*)\s+\**\s*[A-Za-z_]\w*\s*(?:[;=,\[])', inner):
            result.append(('one_shot_unwrap', edit(source, [(offset+wrapper.start(), offset+wrapper.end(), ''),
                                                           (offset+end-1, offset+end+tail.end(), '')])))
    # Shrink a pointer's declaration into its only straight-line use region, SDK-macro style.
    for decl in re.finditer(rf'\bvoid\s*\*\s*({IDENT})\s*;', body):
        var = decl[1]
        hits = list(re.finditer(rf'\b{var}\b', body))
        if len(hits) < 3 or hits[0].start() != decl.start(1):
            continue
        start = hits[1].start()
        end = body.find(';', hits[-1].end())+1
        region = body[start:end]
        prefix = body[:start].rstrip()
        if not end or not prefix.endswith((';', '{', '}')) or not re.match(rf'{var}\s*=(?!=)', region):
            continue
        if any(mark in region for mark in '{}') or re.search(r'\b(?:if|for|while|do|goto|return|switch)\b|&\s*'+var+r'\b', region):
            continue
        if region.count(';') > 6 or re.search(rf'\b(?:void|int|char|[su]\d+)\s+\**\s*{var}\b', body[decl.end():]):
            continue
        result.append(('pointer_block_scope', edit(source, [(offset+decl.start(), offset+decl.end(), ''),
                      (offset+start, offset+end, '{ void *'+var+'; '+source[offset+start:offset+end]+' }')])))
    # Share an already computed array element address, including a negative field offset.
    for hit in re.finditer(rf'\b({IDENT})\s*=\s*&\s*({IDENT})\s*\[\s*({NUMBER})\s*\]\s*;\s*({IDENT})\s*=\s*&\s*\2\s*\[\s*0\s*\]\s*;', body):
        pointer, array, index, base = hit.groups()
        if pointer == base or number(index) > 4096:
            continue
        typed = re.search(rf'\b(int|s32|u32|float|f32)\s*\*\s*{pointer}\s*;', body)
        if not typed or not re.search(rf'\b{typed[1]}\s*\*\s*{base}\s*;', body):
            continue
        array_decl = re.search(rf'\b{typed[1]}\s+{array}\s*\[\s*({NUMBER})\s*\]', _blank(header+'\n'+source[:part.opening]))
        if not array_decl or number(index) >= number(array_decl[1]):
            continue
        statement = re.search(rf'\b{base}\s*=', hit[0])
        start = offset+hit.start()+statement.start()
        result.append(('array_address_share', edit(source, [(start, offset+hit.end(), f'{base} = {pointer} - {index};')])))
    groups = {}
    for family, text in dict.fromkeys(result):
        groups.setdefault(family, []).append(text)
    ordered = []
    while any(groups.values()) and len(ordered) < LIMIT:
        for family, texts in groups.items():
            if texts and len(ordered) < LIMIT:
                ordered.append((family, texts.pop(0)))
    return ordered


def machine_draft_copies(drafts, name, header=''):
    """Keep each m2c draft first, then try its bounded copy-recognition alternatives lazily."""
    for family, source in drafts:
        yield family, source
        for candidate in small_copies(source, name, header):
            yield family + '_small_copy', candidate
