"""Prove bounded straight-line integer locals before proposing narrower storage.

No CFG guesses: branches, calls, escaped locals and unknown expressions stop inference.
Signed-wide inputs only: narrow signed values retain int promotions on the tested ABI.
"""
import ast
import re

from .integer_repair_support import edit, locals_for, view

FAMILIES = ('proven_local_width',)
LIMIT = 2147483647


def bounds(expression, known):
    """Intervals for a deliberately small, side-effect-free integer expression grammar."""
    # Unsigned arithmetic has different wrapping rules; reject suffixed literals.
    expression=expression.strip()
    if len(expression)>1024:
        return None
    try:
        tree=ast.parse(expression,mode='eval').body
    except (SyntaxError,ValueError,RecursionError):
        return None
    if sum(1 for _ in ast.walk(tree))>128:
        return None
    def visit(node):
        if isinstance(node,ast.Constant) and type(node.value) is int and 0<=node.value<=LIMIT:
            return node.value,node.value
        if isinstance(node,ast.Name):
            return known.get(node.id)
        if not isinstance(node,ast.BinOp):
            return None
        left,right=visit(node.left),visit(node.right)
        if not left or not right:
            return None
        lo,hi=left;low,high=right
        if isinstance(node.op,ast.Add):
            result=lo+low,hi+high
        elif isinstance(node.op,ast.Sub):
            result=lo-high,hi-low
        elif isinstance(node.op,ast.Mult):
            result=min(lo*low,lo*high,hi*low,hi*high),max(lo*low,lo*high,hi*low,hi*high)
        elif isinstance(node.op,ast.BitAnd) and low==high and low>=0:
            result=0,low
        elif isinstance(node.op,(ast.LShift,ast.RShift)) and lo>=0 and low==high and 0<=low<31:
            result=(lo<<low,hi<<low) if isinstance(node.op,ast.LShift) else (lo>>low,hi>>low)
        else:
            return None
        return result if -LIMIT-1<=result[0]<=result[1]<=LIMIT else None
    return visit(tree)


def variants(source, name, header=''):
    part=view(source,name)
    if not part or re.search(r'[{}?:]|\b(?:goto|if|for|while|switch|sizeof|typeof|__typeof__|_Generic|asm|__asm__)\b',part.body):
        return []
    if re.search(r'^\s*#\s*define',header+'\n'+source,re.M):
        return []
    declarations={d['name']:d for d in locals_for(part) if d['type'] in {'int','signed int'}}
    body=part.body
    for decl in reversed(list(locals_for(part))):
        a,b=decl.start()-part.opening-1,decl.end()-part.opening-1
        body=body[:a]+' '*(b-a)+body[b:]
    known={};observed={};blocked=set()
    signature=re.search(r'\b'+re.escape(name)+r'\s*\(([^()]*)\)\s*$',part.clean[:part.opening])
    if signature:
        for parameter in signature[1].split(','):
            hit=re.fullmatch(r'\s*(int|signed\s+int|unsigned\s+short|short)\s+([A-Za-z_]\w*)\s*',parameter)
            if hit:
                known[hit[2]]=(0,65535) if hit[1].startswith('unsigned') else ((-32768,32767) if hit[1]=='short' else (-LIMIT-1,LIMIT))
    for statement in body.split(';'):
        statement=statement.strip()
        if not statement:
            continue
        hit=re.fullmatch(r'([A-Za-z_]\w*)\s*=(?!=)\s*(.+)',statement,re.S)
        if hit and hit[1] in declarations:
            result=bounds(hit[2],known)
            if result is None:
                blocked.add(hit[1]);known.pop(hit[1],None)
            else:
                known[hit[1]]=result
                old=observed.get(hit[1],result)
                observed[hit[1]]=min(old[0],result[0]),max(old[1],result[1])
        elif re.fullmatch(r'return\s+.+',statement,re.S) and bounds(statement[7:],known) is not None:
            continue
        else:
            # Unknown statements might observe an address, shadow a name or change a value.
            return []
    result=[]
    for name,(lo,hi) in observed.items():
        if name in blocked or not -32768<=lo<=hi<=32767:
            continue
        decl=declarations[name]
        contract='typedef char __repair_range_abi[(sizeof(int)==4 && sizeof(short)==2 && (short)0x8000u == -32768 && (int)0xffffffffu == -1)?1:-1];'
        if '__repair_range_abi' in source:
            continue
        candidate=edit(source,[(decl.start(),decl.end(),'short '+name+';'),
                               (part.opening+1,part.opening+1,contract)])
        result.append(('proven_local_width',candidate))
    return result[:4]
