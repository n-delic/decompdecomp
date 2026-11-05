"""Replace one uninitialized draft placeholder with a target-backed live link-register read."""
import re

from ..arch.mips_live_return_address import stores
from ..forks.csource import _blank
from .integer_repair_support import IDENT, INTEGER, edit, view

FAMILY = 'live_return_address'
FAMILIES = (FAMILY,)
LIMIT = 4


def variants(source,name,arch='',asm='',header=''):
    if arch!='mips' or not (part:=view(source,name)):return []
    addresses=stores(asm)
    if not addresses:return []
    context=_blank(header+'\n'+source)
    if re.search(r'^\s*#|\b(?:volatile|asm|__asm__|setjmp|longjmp)\b',part.body,re.M):return []
    result=[]
    for decl in re.finditer(r'\b(?:void\s*\*|'+INTEGER+r'\s+)\s*('+IDENT+r')\s*;',part.body):
        variable=decl[1]
        # Exactly declaration plus one use: no initializer, escape, update, shadow or other read.
        hits=list(re.finditer(r'\b'+variable+r'\b',part.body))
        if len(hits)!=2:continue
        pattern=r'\b(D_[0-9A-Fa-f]{8})\s*=\s*('+variable+r')\s*;'
        for assignment in re.finditer(pattern,part.body):
            dest=assignment[1]
            if int(dest[2:],16) not in addresses:continue
            # Source identity is provisional; only an explicitly declared global at this raw
            # address qualifies. Real-name symbol adapters can extend this without guessing.
            if not re.search(r'\bextern\s+(?:void\s*\*|'+INTEGER+r'\s+)\s*'+dest+r'\s*;',context):continue
            if re.search(r'\b(?:'+INTEGER+r'|void)\s*\**\s*'+dest+r'\b',part.body):continue
            if re.search(r'^\s*#\s*define[^\n]*\b(?:'+dest+'|'+variable+r')\b',context,re.M):continue
            start=part.opening+1+assignment.start(2);end=part.opening+1+assignment.end(2)
            result.append((FAMILY,edit(source,[(start,end,'__builtin_mips_live_return_address()')])))
            if len(result)>=LIMIT:return result
    return list(dict.fromkeys(result))


def hint(asm,arch):
    if arch!='mips' or not stores(asm):return ''
    return ('The target stores the current live MIPS return-address register to an absolute global. '
            'A preceding callback changes it: __builtin_return_address(0) reads the entry caller instead. '
            'The registered KMC pack supports fixed __builtin_mips_live_return_address(); try the '
            'live_return_address family for an uninitialized draft placeholder. It is hardware context, '
            'not a reason to sweep compiler flags or invent a normal local value. Exact byte judging remains required.')
