"""ROM-proven scalar references for private export repair; never create or move game data."""
import ast
import math
import re
import struct

from ..forks.csource import _blank

NUMBER=r'(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?[fF]?'
ID=r'[A-Za-z_]\w*'


def value(text, macros, depth=0):
    """Evaluate only bounded numeric arithmetic and actual object macros, never C execution."""
    if depth>6 or len(text)>128:raise ValueError('Expression exceeds scalar repair bounds')
    text=re.sub(r'(?<=\d)[fF]\b','',text)
    def expand(match):
        name=match[0]
        if name not in macros:raise ValueError('Unknown scalar macro')
        return repr(value(macros[name],macros,depth+1))
    text=re.sub(r'\b'+ID+r'\b',expand,text)
    tree=ast.parse(text,mode='eval')
    def evaluate(node):
        if isinstance(node,ast.Expression):return evaluate(node.body)
        if isinstance(node,ast.Constant) and type(node.value) in (int,float):return node.value
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            return evaluate(node.operand)*(1 if isinstance(node.op,ast.UAdd) else -1)
        if isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div)):
            a,b=evaluate(node.left),evaluate(node.right)
            if isinstance(node.op,ast.Add):return a+b
            if isinstance(node.op,ast.Sub):return a-b
            if isinstance(node.op,ast.Mult):return a*b
            return a/b
        raise ValueError('Not plain scalar arithmetic')
    result=float(evaluate(tree))
    if not math.isfinite(result):raise ValueError('Nonfinite scalar')
    return result


def variants(source, header, asm, read, resolve, order='big'):
    """Try one complete substitution and bounded keep-one-pool alternatives; judge every result."""
    macros=dict(re.findall(r'(?m)^\s*#define\s+('+ID+r')[ \t]+([^\n]+)',_blank(header)))
    # Only symbol-labelled floating loads in the target authorize reading a constant.
    symbols={}
    for line in asm.splitlines():
        match=re.search(r'\b(lwc1|ldc1)\b[^\n]*\b(D_[0-9A-Fa-f]{8})\b',line)
        if match:symbols.setdefault(match[2],set()).add('float' if match[1]=='lwc1' else 'double')
    if not symbols:return []
    clean=_blank(source);candidates=[]
    # Whole parenthesized arithmetic before bare literals; no calls, casts or side effects.
    atom=r'(?:'+NUMBER+'|'+ID+')'
    pattern=r'\(\s*'+atom+r'(?:\s*[+*/-]\s*'+atom+r')+\s*\)|(?<![\w.])'+NUMBER+r'(?![\w.])'
    for match in re.finditer(pattern,clean):
        # Never touch directives, array extents or declaration initializers outside the body.
        line=clean[clean.rfind('\n',0,match.start())+1:match.start()]
        if '#' in line or clean.find('{')<0 or match.start()<clean.find('{'):continue
        try:number=value(match[0],macros)
        except (ValueError,SyntaxError,ZeroDivisionError,OverflowError):continue
        matches=[]
        for symbol,types in sorted(symbols.items())[:32]:
            if len(types)!=1:continue
            kind=next(iter(types));address=resolve(symbol)
            if address is None:continue
            try:payload=struct.pack(('>' if order=='big' else '<')+('f' if kind=='float' else 'd'),number)
            except (OverflowError,struct.error):continue
            if read(address,len(payload))==payload:matches.append((symbol,kind))
        if len(matches)==1:candidates.append((match.start(),match.end(),*matches[0]))
    groups=sorted({symbol for _,_,symbol,_ in candidates})
    if not 1<=len(groups)<=8:return []
    out=[]
    # Retaining a generated pool can preserve scheduling and contiguous standalone placement.
    for keep in [None,*groups[:3]]:
        edits=[entry for entry in candidates if entry[2]!=keep]
        if not edits:continue
        body=source;private=header;declarations=[]
        for a,b,symbol,kind in reversed(edits):body=body[:a]+symbol+body[b:]
        for symbol,kind in sorted({(s,k) for _,_,s,k in edits}):
            if re.search(r'\b'+symbol+r'\b',_blank(private)):
                if not re.search(r'\bextern\s+const\s+'+kind+r'\s+'+symbol+r'\s*;',_blank(private)):break
            else:declarations.append('extern const '+kind+' '+symbol+';')
        else:out.append(('cpu-rom-scalar-pools','\n'.join(declarations)+'\n'+body,private))
    return out
