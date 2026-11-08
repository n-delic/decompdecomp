"""Resolve conservative scalar widths for the app's supported 32-bit repair targets."""
import re

from ..forks.csource import _blank

ARCHES = {'mips', 'ppc', 'x86', 'arm', 'thumb'}


def scalar(spelling, context='', arch=None, seen=()):
    """Return (bits, signed), or None for unknown, conflicting or qualified declarations.

    Never use the host Python/C ABI: these architecture adapters describe 32-bit targets.
    Unknown architectures and preprocessor-defined aliases do not establish a width.
    """
    if arch not in ARCHES:
        return None
    spelling = ' '.join(spelling.split())
    if not spelling or spelling in seen or len(seen) > 8 or re.search(r'\b(?:volatile|const|_Atomic)\b',spelling):
        return None
    clean = _blank(context)
    if re.search(r'(?m)^\s*#\s*define\s+'+re.escape(spelling)+r'\b',clean):
        return None
    if re.search(r'\btypedef[^;{}]*(?:\(\s*\*\s*'+re.escape(spelling)+r'\b|\b'+re.escape(spelling)+r'\s*\[)',clean):
        return None
    definitions = re.findall(r'\btypedef\s+([^;{}]+?)\b'+re.escape(spelling)+r'\s*;',clean)
    if definitions:
        types = [scalar(t,context,arch,seen+(spelling,)) for t in definitions]
        return types[0] if types[0] is not None and all(t==types[0] for t in types) else None
    words = spelling.split()
    unsigned = 'unsigned' in words
    words = [w for w in words if w not in {'signed','unsigned','int'}]
    if words == ['char']:
        return (8,not unsigned) if spelling != 'char' else None
    if words == ['short']:
        return 16,not unsigned
    if words in ([],['long']):
        return 32,not unsigned
    if words == ['long','long']:
        return 64,not unsigned
    return None
