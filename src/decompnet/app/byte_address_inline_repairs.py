"""Private pure byte-address helpers and bounded legacy debug-scheduling bridges.

No expression is evaluated twice or moved. Only uniquely declared void pointers,
constant byte displacements and resolved ordinary integer pointees are supported.
Unknown C and preprocessing give no proposal, never an inferred ABI or qualifier.
"""
import re

from ..forks.csource import _blank, find
from . import byte_increment_repairs as expressions
from . import debug_boundary_repairs, integer_abi, pointer_pair_repairs
from .integer_repair_support import IDENT, edit, view
from .pattern_repairs import close

FAMILIES = ('inline_byte_address', 'debug_branch_scope', 'inline_byte_address_bundle')
SITE_LIMIT = 4
BRIDGE_LIMIT = 8


def branch_scopes(source, name):
    """Brace only one complete if/else whose arms are scalar assignment statements."""
    part = view(source, name)
    if not part:
        return []
    result = []
    for head in re.finditer(r'\bif\s*\(', part.body):
        opening = part.opening+1+head.end()-1
        ending = close(part.clean, opening)
        if not ending:
            continue
        # Both arms have exactly one assignment expression, no declaration or nested control.
        arm = rf'\s*({IDENT}\s*=(?!=)\s*{IDENT}\s*;)'
        arms = re.match(arm+r'\s*else\b'+arm, part.clean[ending:])
        if not arms:
            continue
        start, stop = ending, ending+arms.end()
        original = source[start:stop]
        if '#' in original or '/*' in original or '//' in original:
            continue
        text = ' {\n '+arms[1]+'\n} else {\n '+arms[2]+'\n}'
        result.append(edit(source, [(start, stop, text)]))
        if len(result) == 2:
            break
    return result



def variants(source, name, flags=(), header='', arch='mips'):
    part = view(source, name)
    text = header+'\n'+source
    if not part or arch not in integer_abi.ARCHES:
        return []
    if (re.search(r'\b(?:volatile|const|_Atomic|asm|__asm__|register|__LINE__)\b', part.clean)
            or re.search(r'\b__LINE__\b|^\s*#\s*(?:line\b|\d)|\\\n', text, re.M)
            or '#' in part.clean or re.search(r'\b(?:class|template|namespace|typeof|__typeof__)\b|::', text)):
        return []
    if re.search(r'\btypedef\b', part.body):
        return []
    macros = set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')', text, re.M))
    introduced = {'static','inline','signed','unsigned','char','short','int','long','void','return','base','offset','__repair_byte_address'}
    if macros & (set(re.findall(IDENT, part.clean)) | introduced):
        return []
    helper = '__repair_byte_address'
    if re.search(r'\b'+helper+r'\b', text):
        return []
    # A fresh void* argument performs no conversion beyond the original byte-pointer cast.
    definition_start = find(source, name)[0]
    roots = {d['name']:d for d in pointer_pair_repairs.declarations(part) if d['type'].replace(' ', '')=='void*'}
    signature = part.clean[definition_start:part.opening]
    for param in re.finditer(r'\bvoid\s*\*\s*('+IDENT+r')\s*(?=[,)])', signature):
        if param[1] not in roots:
            roots[param[1]] = param
    bases = []
    pattern = re.compile(r'\(\s*('+IDENT+r'(?:\s+'+IDENT+r')*)\s*\*\s*\)')
    context = _blank(text)
    for hit in pattern.finditer(part.clean, part.opening+1, part.end):
        resolved = integer_abi.scalar(hit[1], context, arch)
        if resolved is None:
            continue
        tail = re.match(r'\s*\(', part.clean[hit.end():])
        if not tail:
            continue
        opening = hit.end()+tail.end()-1
        ending = close(part.clean, opening)
        if not ending:
            continue
        binary = expressions.binary(expressions.strip(part.clean[opening:ending]), ('+',))
        if not binary:
            continue
        left, _, right = binary
        cast = expressions.cast(left)
        offset = expressions.number(expressions.strip(right))
        if not cast or offset is None or not 0 <= offset <= 32767:
            continue
        byte_type, root = cast
        if not byte_type.endswith("*"):
            continue
        byte_type = byte_type[:-1].strip()
        if integer_abi.scalar(byte_type, context, arch) not in {(8, True), (8, False)}:
            continue
        if root not in roots:
            continue
        # Any same-named declaration or macro obscures which pointer the site reads.
        declarations = re.findall(r'\b'+IDENT+r'(?:\s+\**\s*|\s*\*+\s*)'+re.escape(root)+r'\s*(?=[;=,)\[])', part.clean[:part.end])
        if len(declarations) != 1:
            continue
        # Width alone does not establish C pointer compatibility (long* is not int*).
        # These exact spellings are visible before the function; local typedefs were refused.
        returned = ' '.join(hit[1].split())
        byte = byte_type
        definition = (f'static inline {returned} *{helper}(void *base, int offset)\n'
                      '{\n  return ('+returned+' *)(('+byte+' *)base + offset);\n}\n\n')
        candidate = edit(source, [(hit.start(), ending, f'{helper}({root}, {offset})')])
        start = find(source, name)[0]
        candidate = candidate[:start]+definition+candidate[start:]
        bases.append(candidate)
        if len(bases) == SITE_LIMIT:
            break
    result = [(FAMILIES[0],s) for s in bases]
    debug = [f for f in flags if re.fullmatch(r'-g(?:[0-3]|gdb[0-3]?)?', f)]
    if not debug or debug[-1] in {'-g0','-ggdb0'}:
        return result
    # Bracing only expression statements cannot change scope, lifetime or dangling-else binding.
    scoped = []
    for candidate in bases:
        scopes = branch_scopes(candidate, name)
        scoped += scopes[:1]
        result += [(FAMILIES[1],s) for s in scopes[:1]]
    groups = [debug_boundary_repairs.call_pairs(s, name, header) for s in (scoped or bases)]
    # Round-robin over accessor sites: four sites each get two adjacent-call probes.
    bridges = [group[i] for i in range(4) for group in groups if i < len(group)]
    result += [(FAMILIES[2],s) for s in bridges[:BRIDGE_LIMIT]]
    return list(dict.fromkeys(result))
