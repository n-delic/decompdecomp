"""Preserve target-backed independent address loads instead of deriving every address from one base.

Only address-valued expressions change. Fresh automatic aliases resolve through the existing linker;
no data, headers or symbol-table entries are changed, and the normal judge remains authoritative.
"""
import re

from ..arch.mips_materializations import addresses
from ..forks.csource import find
from ..rom.symbols import SymbolTable
from .equal_value_flow import macros_safe
from .integer_repair_support import IDENT, NUMBER, edit, integer, view

FAMILIES = ('independent_address_materialization',)
LIMIT = 2


def variants(source, name, arch, rows, header=''):
    part=view(source,name)
    if arch!='mips' or not part or re.search(r'\b(?:volatile|asm|__asm__|goto)\b',part.body):return []
    wanted,compiled=addresses(rows,1),addresses(rows,2)
    missing={address for address,count in wanted.items() if count>=2 and count>compiled[address]}
    if not missing:return []
    # Arbitrary macro expansion can hide pointer writes and control transfers.
    if not macros_safe(source,header,part.body,set(re.findall(r'\b('+IDENT+r')\s*=(?!=)',part.body))):return []
    result=[]
    pattern=rf'\b({IDENT})\s*=\s*\(\s*(?:u8|char|unsigned char)\s*\*\s*\)\s*\(?\s*&\s*(D_[0-9A-Fa-f]{{8}})\s*\)?\s*;'
    for definition in re.finditer(pattern,part.body):
        variable,symbol=definition[1],definition[2]
        if len(re.findall(r'\b(?:u8|char|unsigned char)\s*\*\s*'+re.escape(variable)+r'\s*;',part.body))!=1:continue
        base=SymbolTable().resolve(symbol)
        if base is None:continue
        if len(re.findall(r'\b'+re.escape(variable)+r'\s*=(?!=)',part.body))!=1:continue
        if re.search(r'&\s*(?:\(\s*)*'+re.escape(variable)+r'\b|\b'+re.escape(variable)+r'\s*(?:\+\+|--|[+\-*/]=)',part.body):continue
        if re.search(r'(?:\+\+|--)\s*'+re.escape(variable)+r'\b',part.body):continue
        for use in re.finditer(r'\b'+re.escape(variable)+rf'\s*\+\s*({NUMBER})\b',part.body):
            if use.start()<definition.end():continue
            between=part.body[definition.end():use.start()]
            if re.search(r'[{}]|\b(?:if|else|goto|while|for|switch|case)\b',between):continue
            offset=integer(use[1]);destination=base+offset
            if not 0<offset<=65535 or destination not in missing:continue
            for prefix in ('RO','data'):
                alias=f'{prefix}_{destination:08X}'
                if re.search(r'\b'+alias+r'\b',source+'\n'+header):continue
                if SymbolTable().resolve(alias)!=destination:continue
                start,end=part.opening+1+use.start(),part.opening+1+use.end()
                candidate=edit(source,[(start,end,'&'+alias),
                    (find(source,name)[0],find(source,name)[0],'extern char '+alias+';\n')])
                result.append((FAMILIES[0],candidate))
                break
            if len(result)==LIMIT:return result
    return result
