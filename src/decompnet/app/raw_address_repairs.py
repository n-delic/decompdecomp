"""Bounded expression-local high-half corrections supported by raw target addresses.

These are hypotheses only: the ordinary linked-byte judge remains authoritative.
"""
import re
from itertools import combinations

from ..arch.mips_addresses import addresses
from .integer_repair_support import edit, integer, view

FAMILIES = ('raw_address_correction', 'raw_address_pair')
NUMBER = r'0[xX][0-9A-Fa-f]{8}[uUlL]*'
AUTO = r'D_[0-9A-Fa-f]{8}'


def variants(source, name, arch, asm='', header=''):
    if arch != 'mips' or not (part := view(source, name)):
        return []
    # Macro expansion and shadowed globals make source-to-address correspondence ambiguous.
    macros = re.findall(r'^\s*#\s*define\s+(\w+)\(', header+'\n'+source[:part.opening], re.M)
    if '#' in part.body or any(re.search(r'\b'+re.escape(m)+r'\s*\(',part.body) for m in macros):
        return []
    targets = addresses(asm)
    if not targets:
        return []
    declarations = header+'\n'+part.clean[:part.opening]
    byte_arrays = set(re.findall(r'\bextern\s+(?:char|unsigned char|signed char|[su]8)\s+('+AUTO+r')\s*\[\s*\]\s*;', declarations))
    scalars = {m[2]:m[1] for m in re.finditer(r'\bextern\s+((?:volatile\s+)?(?:[su](?:8|16|32)|(?:unsigned\s+|signed\s+)?(?:char|short|int)))\s+('+AUTO+r')\s*;', declarations)}
    pattern = re.compile(r'\b(?P<base>'+AUTO+r')\s*(?P<sign>[+-])\s*(?P<off>0[xX][0-9a-fA-F]+|[0-9]+)\b|\b(?P<literal>'+NUMBER+r')\b|\b(?P<scalar>'+AUTO+r')\b')
    edits = []
    for m in pattern.finditer(part.body):
        if m['base']:
            if m['base'] not in byte_arrays:
                continue  # no guessed pointer scaling
            old = (int(m['base'][2:],16) + integer(m['off'])*(1 if m['sign']=='+' else -1)) & 0xffffffff
        elif m['literal']:
            old = integer(m['literal'])
        else:
            if m['scalar'] not in scalars or re.search(r'\b(?:int|short|char|[su](?:8|16|32))\s+'+m['scalar']+r'\b',part.body):
                continue
            old = int(m['scalar'][2:],16)
        if old in targets or not 0x80000000 <= old < 0xc0000000:
            continue
        nearby = [target for target in targets if abs(target-old)==65536]
        if len(nearby)!=1:
            continue
        new = nearby[0]
        if m['base']:
            # Retain the byte-array base; change only this occurrence's addend.
            delta = new-int(m['base'][2:],16)
            value = m['base']+(' + ' if delta>=0 else ' - ')+hex(abs(delta))
        elif m['literal']:
            suffix = re.search(r'[uUlL]*$',m['literal'])[0]
            value = hex(new)+suffix
        else:
            value = '(*('+scalars[m['scalar']]+' *)'+hex(new)+')'
        start = part.opening+1+m.start()
        edits.append((start,part.opening+1+m.end(),value))
    # Keep the search small, and do not modify any other function or shared declaration.
    edits = edits[:4]
    singles = [(FAMILIES[0],edit(source,[item])) for item in edits]
    pairs = [(FAMILIES[1],edit(source,list(pair))) for pair in combinations(edits,2)]
    return list(dict.fromkeys(singles+pairs))[:10]
