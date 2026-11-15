"""Remove or shrink unused local padding; exact comparison decides the original frame size."""
import re

from .. import arch as arches
from ..forks.csource import _blank, find

IDENT = r'[A-Za-z_]\w*'
TYPES = r'(?:[su](?:8|16|32)|char|short|int|unsigned\s+(?:char|short|int))'


def variants(source, name):
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    opening = clean.find('{', *span)
    if opening < 0 or '#' in clean[opening:span[1]]:
        return []
    body = clean[opening:span[1]]
    declaration = re.compile(r'(?:^|(?<=[;{}]))\s*(?P<decl>'+TYPES+r'\s+(?P<name>(?:pad|padding)\w*)\s*'
                             r'\[\s*(?P<count>0x[\da-fA-F]+|\d+)\s*\]\s*;)')
    result = []
    for hit in declaration.finditer(body):
        # Only ordinary declarations in the function's outer scope, never aggregate fields,
        # initialized arrays, qualifiers or storage whose address/sizeof is referenced.
        prefix = body[:hit.start('decl')]
        if prefix.count('{')-prefix.count('}') != 1:
            continue
        if len(re.findall(r'\b'+re.escape(hit['name'])+r'\b', body)) != 1:
            continue
        count = int(hit['count'], 16 if hit['count'].startswith('0x') else 10)
        if not 1 <= count <= 64:
            continue
        a, b = opening+hit.start('decl'), opening+hit.end('decl')
        result.append(source[:a]+source[b:])
        if count > 1:
            a, b = opening+hit.start('count'), opening+hit.end('count')
            result.append(source[:a]+str(count//2)+source[b:])
        if len(result) >= 4:
            break
    return result


def grow(source, name, arch, rows):
    """Try missing local storage only when every differing instruction agrees on one frame delta."""
    reader = arches.get(arch).stack_frame_growth
    delta = reader(rows) if reader else None
    if delta is None:
        return []
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    opening = clean.find('{', *span)
    fresh = '__repair_stack_padding'
    if opening < 0 or '#' in clean[opening:span[1]] or re.search(r'\b(?:volatile|'+fresh+r')\b', clean):
        return []
    return [source[:opening+1]+'\nchar '+fresh+'['+str(count)+'];'+source[opening+1:]
            for count in (delta, delta//2)]


def measured(source, name, arch, rows):
    """Place one explicitly inferred reservation after existing outer declarations.

    A consistent frame/save delta is evidence for storage, not its original purpose.
    Existing objects and callee contracts keep their types and extents.
    """
    from decomprepair.integer_repair_support import view
    from decomprepair.pattern_repairs import close
    part=view(source,name)
    fresh='__repair_stack_padding'
    if not part or re.search(r'\b(?:volatile|asm|__asm__|'+fresh+r')\b',part.clean):return []
    reader=arches.get(arch).stack_layout
    evidence=reader(rows) if reader else {}
    delta=evidence.get('inferred_growth')
    if delta is None:return []
    # Keep all existing declaration order and physical lines. In particular, do
    # not insert before escaped vectors/matrices and move their observed offsets.
    pos=part.opening+1;last=pos
    while pos<part.end-1:
        while part.clean[pos:pos+1].isspace():pos+=1
        if not re.match(r'(?:typedef\b|struct\b|union\b|'+IDENT+r'(?:[ \t]+'+IDENT+r')*[ \t]+\**[ \t]*'+IDENT+r'\s*(?=[;=,\[]))',part.clean[pos:]):break
        end=pos
        while end<part.end-1 and part.clean[end]!=';':
            if part.clean[end] in '({[':
                opening=part.clean[end];end=close(part.clean,end,opening,{'(':')','{':'}','[':']'}[opening])
                if not end:return []
            else:end+=1
        if end>=part.end-1:return []
        last=pos=end+1
    note='\n/* Inferred frame reservation; original purpose unknown. */\nchar '+fresh+'['+str(delta)+'];'
    return [('inferred_frame_reservation',source[:last]+note+source[last:])]
