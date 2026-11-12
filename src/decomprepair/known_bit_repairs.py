"""Bounded low-zero-bit masks from range-proven scalar initializers, not guessed constants."""
import re

from . import integer_abi, value_range_repairs
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, INTEGER, NUMBER, edit, integer
from .repair_source_view import read

FAMILY='known_zero_mask'
LIMIT=6


def variants(source,name,header='',arch=None):
    part=read(source,name);context=header+'\n'+source
    if not part or re.search(r'\b(?:volatile|asm|__asm__|goto)\b',part.body):return []
    macros=set(re.findall(r'^\s*#\s*define\s+('+IDENT+')',context,re.M))
    decls=list(re.finditer(r'\b(?P<type>'+INTEGER+r')\s+(?P<name>'+IDENT+r')\s*=\s*(?P<rhs>[^;{}]+);',part.clean))
    def declaration(var,at):
        matches=[d for d in decls if d['name']==var and part.opening<d.start()<at]
        if len(matches)!=1:return None
        d=matches[0];tail=part.clean[d.end():at]
        # A literal AND assignment only clears bits. Other writes invalidate the proof.
        updates=list(re.finditer(r'\b'+re.escape(var)+r'\s*&=\s*'+NUMBER+r'\s*;',tail))
        if any(part.clean[:d.end()+m.start()].rstrip()[-1:] not in ';{}' or block_path(part,d.end()+m.start())!=block_path(part,d.start()) for m in updates):return None
        tail=re.sub(r'\b'+re.escape(var)+r'\s*&=\s*'+NUMBER+r'\s*;', '', tail)
        if len(re.findall(r'\b'+INTEGER+r'\s+\**\s*'+re.escape(var)+r'\b',part.body))!=1:return None
        if re.search(r'(?<!&)&(?!&)\s*\(*\s*'+re.escape(var)+r'\b',part.body):return None
        if re.search(r'\b'+re.escape(var)+r'\s*(?:[+\-*/%&|^]?=(?!=)|\+\+|--)|(?:\+\+|--)\s*\b'+re.escape(var)+r'\b',tail):return None
        if block_path(part,at)[:len(block_path(part,d.start()))]!=block_path(part,d.start()):return None
        if macros & set(re.findall(IDENT,part.clean[d.start():at])):return None
        return d
    def interval(expr,at,depth=0):
        if depth>4:return None
        cast=re.fullmatch(r'\(\s*('+INTEGER+r')\s*\)\s*('+IDENT+r')',expr.strip())
        if cast:
            abi=integer_abi.scalar(cast[1],context,arch)
            # A local scalar operand, not a global/volatile load disguised by a cast.
            if abi and not abi[1] and abi[0] in (8,16) and re.search(r'\b'+INTEGER+r'\s+'+re.escape(cast[2])+r'\s*[;=]',part.body):return (0,(1<<abi[0])-1)
            return None
        known={}
        for word in set(re.findall(IDENT,expr)):
            d=declaration(word,at)
            if not d:return None
            value=interval(d['rhs'],d.start(),depth+1)
            if not value:return None
            updates=re.findall(r'\b'+re.escape(word)+r'\s*&=\s*('+NUMBER+r')\s*;',part.clean[d.end():at])
            if updates:value=(0,min(value[1],*(integer(v) for v in updates)))
            known[word]=value
        return value_range_repairs.bounds(expr,known)
    result=[]
    for d in decls:
        if not part.opening<d.start()<part.end:continue
        abi=integer_abi.scalar(d['type'],context,arch)
        if not abi or abi[0]!=32:continue
        bounds=interval(d['rhs'],d.start())
        if not bounds or bounds[0]<0 or bounds[1]>0x7fffffff:continue
        multiply=re.fullmatch(r'\s*(.+)\s*\*\s*('+NUMBER+r')\s*',d['rhs'])
        shift=re.fullmatch(r'\s*(.+)\s*<<\s*(\d+)\s*',d['rhs'])
        factor=integer(multiply[2]) if multiply else 1<<int(shift[2]) if shift and int(shift[2])<31 else 1
        zeros=(factor & -factor).bit_length()-1 if factor>0 else 0
        if not 1<=zeros<=8:continue
        mask=0xffffffff ^ ((1<<zeros)-1);var=d['name']
        pattern=r'\b'+re.escape(var)+r'\s*<<\s*(\d+)'
        for use in re.finditer(pattern,part.clean[d.end():part.end]):
            at=d.end()+use.start()
            if int(use[1])>=32 or not declaration(var,at):continue
            result.append((FAMILY,edit(source,[(at,at+len(var),'('+var+' & '+hex(mask)+'u)')])) )
        pattern=r'\b'+re.escape(var)+r'\s*&\s*('+NUMBER+r')'
        for use in re.finditer(pattern,part.clean[d.end():part.end]):
            at=d.end()+use.start()
            if integer(use[1])!=mask or not declaration(var,at):continue
            result.append((FAMILY,edit(source,[(at,d.end()+use.end(),'((unsigned int)'+var+')')])) )
    return list(dict.fromkeys(result))[:LIMIT]
