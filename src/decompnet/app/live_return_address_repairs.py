"""Replace one uninitialized draft placeholder with a target-backed live link-register read."""
import re

from ..arch.mips_live_return_address import stores
from ..forks.csource import _blank
from . import constant_initializer_staging, integer_abi
from .integer_repair_support import IDENT, INTEGER, edit, view

FAMILY = 'live_return_address'
BUNDLE = 'live_return_address_initializers'
FAMILIES = (FAMILY,BUNDLE)
LIMIT = 4


def variants(source,name,arch='',asm='',header=''):
    if arch!='mips' or not (part:=view(source,name)):return []
    addresses=stores(asm)
    if not addresses:return []
    context=_blank(header+'\n'+source)
    if re.search(r'^\s*#|\b(?:asm|__asm__|setjmp|longjmp)\b',part.body,re.M):return []
    result=[]
    for decl in re.finditer(r'\b(?:void\s*\*|'+INTEGER+r'\s+)\s*('+IDENT+r')\s*;',part.body):
        # Unrelated volatile locals stay intact; only this placeholder must be unqualified.
        prefix=part.body[:decl.start()].rstrip()
        if prefix and prefix[-1:] not in ';{}':continue
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
            declaration=re.search(r'\bextern\s+(void\s*\*|'+INTEGER+r'\s+)\s*'+dest+r'\s*;',context)
            if not declaration:continue
            ctype=declaration[1].strip()
            if '*' not in ctype and integer_abi.scalar(ctype,context,arch) not in {(32,True),(32,False)}:continue
            if re.search(r'\b(?:'+INTEGER+r'|void)\s*\**\s*'+dest+r'\b',part.body):continue
            if re.search(r'^\s*#\s*define[^\n]*\b(?:'+dest+'|'+variable+r')\b',context,re.M):continue
            start=part.opening+1+assignment.start(2);end=part.opening+1+assignment.end(2)
            value=('('+ctype+')' if '*' not in ctype else '')+'__builtin_mips_live_return_address()'
            result.append((FAMILY,edit(source,[(start,end,value)])))
            if len(result)>=LIMIT:break
        if len(result)>=LIMIT:break
    bases=[text for _,text in result]
    # A previously saved hardware read may still need the companion allocation shape.
    direct=re.finditer(r'\b(D_[0-9A-Fa-f]{8})\s*=\s*(?:\(\s*'+INTEGER+r'\s*\)\s*)?__builtin_mips_live_return_address\s*\(\s*\)\s*;',part.body)
    if any(int(m[1][2:],16) in addresses and re.search(r'\bextern\s+(?:void\s*\*|'+INTEGER+r'\s+)\s*'+m[1]+r'\s*;',context) for m in direct):
        bases.append(source)
    bundles=[(BUNDLE,text) for base in bases for text in constant_initializer_staging.variants(base,name,header,arch)]
    return list(dict.fromkeys(result+bundles[:LIMIT]))


def hint(asm,arch):
    if arch!='mips' or not stores(asm):return ''
    return ('The target stores the current live MIPS return-address register to an absolute global. '
            'A preceding callback changes it: __builtin_return_address(0) reads the entry caller instead. '
            'The registered KMC pack supports fixed __builtin_mips_live_return_address(); try the '
            'live_return_address family for an uninitialized draft placeholder. It is hardware context, '
            'not a reason to sweep compiler flags or invent a normal local value. The live_return_address_initializers '
            'bundle preserves one-shot scopes and initializer evaluation order while testing declaration staging; '
            'the intermediate hardware-only change can score worse. Exact byte judging remains required.')
