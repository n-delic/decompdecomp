"""Count explicit adjacent full-address constructions for repair advice, not match verdicts."""
import re
from collections import Counter

from .symbols import SymbolTable

REG = r'(\$\w+)'
IMM = r'(-?(?:0[xX][0-9a-fA-F]+|\d+))'


def number(text):
    return int(text,16 if '0x' in text.lower() else 10)


def addresses(rows, side):
    instructions=[r[side] for r in rows or [] if isinstance(r,(list,tuple)) and len(r)==3 and isinstance(r[side],str) and r[side]]
    found=Counter()
    for high,low in zip(instructions,instructions[1:]):
        a=re.fullmatch(r'lui\s+'+REG+r',\s*(.+)',high)
        b=re.fullmatch(r'(addiu|ori)\s+'+REG+r',\s*'+REG+r',\s*(.+)',low)
        if not a or not b or a[1]!=b[2] or a[1]!=b[3]:continue
        symbolic=re.fullmatch(r'%hi\((\w+)\)',a[2])
        if symbolic:
            value=SymbolTable().resolve(symbolic[1])
            if value is not None and b[1]=='addiu' and b[4]=='%lo('+symbolic[1]+')':found[value]+=1
        elif re.fullmatch(IMM,a[2]) and re.fullmatch(IMM,b[4]):
            hi,lo=number(a[2]),number(b[4])
            if not 0<=hi<=65535 or not -32768<=lo<=65535:continue
            if b[1]=='addiu':value=(hi<<16)+(lo if lo<32768 else lo-65536)
            else:
                if lo<0:continue
                value=(hi<<16)|lo
            found[value&0xffffffff]+=1
    return found
