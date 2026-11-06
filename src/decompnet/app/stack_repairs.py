"""Small storage and declaration hypotheses for excess stack frames, before random permutation.

Keep source layout and shared headers intact. These are candidates, not equivalence proofs: every result must
pass the ordinary compiler and linked/object judge. The shared draft pass reaches ROM and imported projects.
"""
import re

from ..forks.csource import _blank, find

IDENT = r'[A-Za-z_]\w*'
SCALAR = r'(?:s8|u8|s16|u16|s32|u32|s64|u64|f32|f64|char|short|int|long|float|double|unsigned\s+(?:char|short|int|long)|signed\s+(?:char|short|int|long))'
DECL = re.compile(r'\s*(?P<storage>register\s+)?(?P<type>(?:' + SCALAR + r'\s*\**|'+IDENT+r'\s*\*+))(?:\s+|(?<=\*))(?P<name>'+IDENT+r')\s*;')
LIMIT = 16


def uses(text, name):
    """A field with the same spelling is not a use of the local variable."""
    return [m for m in re.finditer(r'\b'+re.escape(name)+r'\b', text)
            if not text[:m.start()].rstrip().endswith(('.', '->'))]


def variants(source, name):
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    signature = re.search(r'\b'+re.escape(name)+r'\s*\((?P<params>[^{};]*)\)\s*{', clean[span[0]:span[1]])
    if not signature:
        return []
    opening = span[0]+signature.end()-1
    body = clean[opening+1:span[1]]
    if '#' in body or re.search(r'\b(?:asm|__asm__|__asm|goto)\b', body):
        return []
    declarations, pos = [], 0
    while match := DECL.match(body, pos):
        declarations.append(match)
        pos = match.end()
    if not declarations:
        return []  # do not spend repair trials on parameter-only wrappers with no local storage
    result = []

    def emit(family, edits):
        text = source
        for a, b, value in sorted(edits, reverse=True):
            text = text[:a]+value+text[b:]
        if text != source and text not in [s for _, s in result]:
            result.append((family, text))

    # Only ordinary, uninitialized declarations in the leading local block. Blank their bytes rather
    # than deleting lines; a historical compiler can schedule differently when source layout changes.
    unused = []
    for decl in declarations:
        if len(uses(body, decl['name'])) == 1:
            a, b = opening+1+decl.start(), opening+1+decl.end()
            unused.append((a,b,''.join(char if clean[a+i].isspace() else ' ' for i,char in enumerate(source[a:b]))))
            emit('unused_scalar_local', [unused[-1]])
    if len(unused) > 1:
        emit('unused_scalar_local', unused)
    for decl in declarations[:6]:
        var = decl['name']
        if re.search(r'&\s*'+re.escape(var)+r'\b', body):
            continue
        a = opening+1+(decl.start('storage') if decl['storage'] else decl.start('type'))
        b = opening+1+decl.end('storage') if decl['storage'] else a
        emit('local_storage', [(a,b,'' if decl['storage'] else 'register ')])
    # Parameter homes can account for a frame mismatch too. Do not try register for address-taken parameters.
    params = signature['params']
    for param in list(re.finditer(r'(?:^|,)\s*(?P<storage>register\s+)?(?:'+SCALAR+r'\s*\**|'+IDENT+r'\s*\*+)(?:\s+|(?<=\*))(?P<name>'+IDENT+r')\s*(?=,|$)', params))[:3]:
        if re.search(r'&\s*'+re.escape(param['name'])+r'\b', body):
            continue
        base = span[0]+signature.start('params')
        if param['storage']:
            a,b = base+param.start('storage'),base+param.end('storage')
        else:
            a = base+param.start()+len(param[0])-len(param[0].lstrip(' ,\t\r\n'))
            b = a
        emit('parameter_storage', [(a,b,'' if param['storage'] else 'register ')])
    # Adjacent uninitialized locals only, not statements, initialized objects or nested scopes.
    for first, second in list(zip(declarations, declarations[1:]))[:12]:
        a,b = opening+1+first.start('type'),opening+1+first.end()
        c,d = opening+1+second.start('type'),opening+1+second.end()
        if first['storage'] or second['storage']:
            continue
        emit('local_declaration_order', [(a,b,source[c:d]),(c,d,source[a:b])])
    swaps = [r for r in result if r[0] == "local_declaration_order"]
    other = [r for r in result if r[0] != "local_declaration_order"]
    return (swaps + other)[:LIMIT]
