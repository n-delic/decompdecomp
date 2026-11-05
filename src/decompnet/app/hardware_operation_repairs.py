"""Recognize a fixed supported hardware operation, never infer arbitrary instruction streams."""
import re

from ..forks.csource import _blank, find

FAMILY = 'mips_debug_break'
FAMILIES = (FAMILY,)


def variants(source,name,arch='',asm=''):
    if arch!='mips' or not re.search(r'\bbreak\s+(?:9|0x0*9)\b',asm or ''):return []
    span=find(source,name)
    if not span:return []
    clean=_blank(source)
    if re.search(r'^\s*#\s*define\s+M2C_BREAK\b',clean,re.M):return []
    # Replace only m2c's explicit operation at its original control-flow position.
    hits=list(re.finditer(r'\bM2C_BREAK\s*\(\s*(?:9|0x0*9)[uU]?\s*\)\s*;',clean[span[0]:span[1]]))
    if not hits:return []
    for hit in reversed(hits):
        start,stop=span[0]+hit.start(),span[0]+hit.end()
        source=source[:start]+'__builtin_mips_debug_break();'+source[stop:]
    return [(FAMILY,source)]


def hints(source,asm,arch,rows=None):
    if arch!='mips':return []
    result=[]
    if re.search(r'\bbreak\s+(?:9|0x0*9)\b',asm or '') and '__builtin_mips_debug_break' not in _blank(source):
        result.append('The target contains BREAK 9. A fixed __builtin_mips_debug_break() compatibility intrinsic '
                      'is available with gcc-2.7.2-kmc; it may resume and is not noreturn. Preserve its original '
                      'control-flow position. M2C_BREAK(9) can be repaired locally. Other compiler packs need '
                      'explicit support; ordinary unknown builtins can become external calls. Diagnose this '
                      'missing operation before compiler sweeps or padding/scheduling edits.')
    if any(len(r)==3 and r[0]=='<' and r[1].strip() and r[1].split()[0] not in {'nop','b','beq','bne','j'} for r in rows or []):
        result.append('A target-only non-NOP operation remains. Check the earliest missing operation before '
                      'chasing downstream branch offsets: an extra mask or padding instruction elsewhere may '
                      'lower the scalar score without restoring the operation. Structural alternatives remain '
                      'search hypotheses; only the normal byte-exact judge proves a match.')
    return result
