"""Candidate-local typed pointer cells for equivalent absolute and byte-base accesses.

Only literal byte-array offsets are resolved; pointer arithmetic, volatile cells and ambiguous
macros stay opaque. This changes compiler alias information, not a verdict of equivalence.
"""
import re

from .integer_repair_support import edit, view
from .mips_addresses import addresses

TYPE = r'(?:struct\s+)?[A-Za-z_]\w*'
CELL = re.compile(r'\(\s*\*\s*\(\s*(?P<type>'+TYPE+r')\s*\*\s*\*\s*\)\s*'
                  r'(?:\(\s*(?P<base>D_[0-9A-Fa-f]{8})\s*\+\s*(?P<offset>0[xX][0-9A-Fa-f]+|[0-9]+)\s*\)'
                  r'|(?P<address>0[xX][0-9A-Fa-f]{8}))\s*\)')


FAMILIES = ('typed_global_identity',)

def variants(source, name, arch, asm='', header=''):
    part = view(source, name)
    if arch != 'mips' or not part:
        return []
    declarations = header+'\n'+source[:part.opening]
    arrays = set(re.findall(r'\bextern\s+(?:u8|s8|char|unsigned char|signed char)\s+(D_[0-9A-Fa-f]{8})\s*\[\s*\]\s*;', declarations))
    targets = addresses(asm)
    aliases = []
    for macro in re.finditer(r'^\s*#\s*define\s+(\w+)[ \t]+([^\n]+)$', declarations, re.M):
        if match := CELL.fullmatch(macro[2].strip()):
            aliases.append((macro[1], match))
    groups = {}
    def collect(match, spans):
        if match['type'] in {'volatile', 'const'}:
            return
        if match['base'] and match['base'] not in arrays:
            return
        address = int(match['address'],16) if match['address'] else int(match['base'][2:],16)+int(match['offset'],0)
        if address not in targets:
            return
        groups.setdefault((match['type'],address), []).extend(spans)
    for match in CELL.finditer(part.clean, part.opening+1, part.end-1):
        collect(match, [(match.start(), match.end())])
    for alias, match in aliases:
        hits = list(re.finditer(r'\b'+re.escape(alias)+r'\b', part.body))
        # Any local declaration of this name makes the macro's binding ambiguous.
        if re.search(r'\b(?:'+TYPE+r')\s+\**\s*'+re.escape(alias)+r'\s*[;=,]',part.body):
            continue
        collect(match, [(part.opening+1+h.start(),part.opening+1+h.end()) for h in hits])
    result = []
    for (ctype,address), spans in groups.items():
        if len(spans)<2:
            continue
        symbol = f'D_{address:08X}'
        existing = re.search(r'\b'+re.escape(symbol)+r'\b', declarations)
        declaration = f'extern {ctype} *{symbol};'
        if existing and declaration not in declarations:
            continue
        edits = [(a,b,symbol) for a,b in set(spans)]
        if not existing:
            # Place before the definition, not inside its parameter list or shared header.
            from .csource import find
            start = find(source,name)[0]
            edits.append((start,start,declaration+'\n'))
        candidate = edit(source,edits)
        if candidate != source:
            result.append(('typed_global_identity',candidate))
    return result[:4]
