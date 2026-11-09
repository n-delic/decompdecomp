"""Small scalar assignment shapes with explicit arithmetic and return contracts."""
import re
from dataclasses import dataclass

from ..forks.csource import _blank
from .integer_repair_support import IDENT, NUMBER, integer, split_args

OPS = r'(?:<<|>>|[+\-&|^])'


@dataclass
class Assignment:
    name: str
    compound: str
    expression: str
    operator: str
    left: str
    right: str
    call_type: tuple | None = None


def parse(text, context, operand_type):
    context = _blank(context)
    binary = re.fullmatch(rf'\s*({IDENT})\s*(\+=|-=|&=|\|=|\^=|<<=|>>=|=)\s*({IDENT})\s*({OPS})\s*({IDENT}|{NUMBER})\s*;\s*',text)
    if binary:
        name,compound,left,operator,right=binary.groups()
        return Assignment(name,compound,f'{left} {operator} {right}',operator,left,right)
    simple = re.fullmatch(rf'\s*({IDENT})\s*(\+=|-=|&=|\|=|\^=|<<=|>>=|=)\s*({IDENT}|{NUMBER})\s*;\s*',text)
    if simple:
        name,compound,left=simple.groups()
        if compound=='=':return None
        return Assignment(name,compound,left,'',left,'')
    call = re.fullmatch(rf'\s*({IDENT})\s*=\s*({IDENT})\s*\(([^;{{}}]*)\)\s*;\s*',text)
    if not call:return None
    # Only an explicit scalar-return prototype, with plain arguments. No macro calls,
    # variadics, unspecified return width, pointer returns, nested calls or invented extra evaluations.
    returns=re.findall(r'\b(?:extern\s+)?([A-Za-z_][A-Za-z_0-9\s*]*?)\s+'+re.escape(call[2])+r'\s*\(([^;{}]*)\)\s*;',context)
    if any('...' in args for _,args in returns):return None
    types=[operand_type(t,True) for t,_ in returns]
    if not types or types[0] is None or not all(t==types[0] for t in types):return None
    args=call[3]
    for a,b in split_args(args):
        arg=args[a:b].strip()
        if not re.fullmatch(IDENT,arg):return None
    return Assignment(call[1],'=',f'{call[2]}({args})','call','','',types[0])


def subtotal_type(assignment, old_type, resolve, spelling):
    """Keep signedness/conversions and shift direction; never reassociate subtraction/shifts."""
    if assignment.operator=='call':
        return spelling if assignment.call_type==old_type else None
    def scalar(value):
        if re.fullmatch(NUMBER,value):
            digits=re.sub(r'[uUlL]+$','',value)
            suffix=value[len(digits):].lower()
            n=integer(value)
            if suffix.count('l')>1 or n>0xFFFFFFFF or (n>0x7FFFFFFF and 'u' not in suffix and not digits.lower().startswith('0x')):
                return None
            return 32,not ('u' in suffix or n>0x7FFFFFFF)
        return resolve(value)
    left=scalar(assignment.left)
    right=scalar(assignment.right) if assignment.right else left
    if any(t is None or t[0]>32 for t in (left,right)):return None
    if assignment.operator in {'<<','>>'} or assignment.compound in {'<<=','>>='}:
        # Only unsigned 32-bit shifts with a literal in range; avoid signed-left-shift UB and
        # implementation-dependent signed right shifts or uncertain dynamic counts.
        if (assignment.operator not in {'<<','>>'} or not re.fullmatch(NUMBER,assignment.right)
                or not 0<=integer(assignment.right)<32 or left!=(32,False) or old_type!=(32,False)
                or assignment.compound!='='):
            return None
    return 'unsigned int' if old_type[1] and (32,False) in (left,right) else spelling
