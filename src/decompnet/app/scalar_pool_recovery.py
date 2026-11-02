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


def scalars(source, header, asm, read, resolve, order='big'):
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
    macro=r'(?:'+'|'.join(re.escape(n) for n in sorted(macros))+r')' if macros else r'(?!)'
    numeric=r'(?:'+NUMBER+'|'+macro+r')'
    pattern=r'(?<![\w.])'+numeric+r'(?:\s*[+*/-]\s*'+numeric+r')*(?![\w.])'
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
        if len(matches)==1:
            a,b=match.start(),match.end()
            # Preserve call parentheses; remove only an enclosing arithmetic grouping pair.
            left=clean[:a].rstrip();right=clean[b:].lstrip()
            if left.endswith('(') and right.startswith(')') and not re.search(r'[\w)]\s*\($',left):
                a=len(left)-1;b=len(clean)-len(right)+1
            candidates.append((a,b,*matches[0]))
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


def strings(source, header, asm, read, resolve):
    """Only a unique target-labelled address containing the complete actual literal plus NUL."""
    from ..repair_changes import without_comments
    clean=without_comments(source);edits=[]
    symbols=sorted(set(re.findall(r'%lo\((D_[0-9A-Fa-f]{8})\)',asm)))[:128]
    for match in re.finditer(r'"(?:[^"\\\n]|\\.)*"',clean):
        line=clean[clean.rfind('\n',0,match.start())+1:match.start()]
        if '#' in line or re.search(r'[\w"]\s*$',line):continue
        if re.match(r'\s*"',clean[match.end():]):continue
        try:text=ast.literal_eval(match[0]);payload=text.encode('ascii')+b'\0'
        except (ValueError,SyntaxError,UnicodeEncodeError):continue
        if not 2<=len(payload)<=256 or '\0' in text:continue
        matches=[]
        for name in symbols:
            address=resolve(name)
            if address is not None and read(address,len(payload))==payload:matches.append(name)
        if len(matches)==1:edits.append((match.start(),match.end(),matches[0]))
        if len(edits)>=8:break
    if not edits:return []
    names=sorted({name for _,_,name in edits});body=source
    for a,b,name in reversed(edits):body=body[:a]+name+body[b:]
    declarations=[]
    for name in names:
        if re.search(r'\b'+name+r'\b',_blank(header)):
            if not re.search(r'\bextern\s+(?:const\s+)?char\s+'+name+r'\s*\[\s*\]\s*;',_blank(header)):return []
        else:declarations.append('extern const char '+name+'[];')
    return [('cpu-rom-string-pools','\n'.join(declarations)+'\n'+body,header)]


def variants(source, header, asm, read, resolve, order='big'):
    """Strings first, then scalar alternatives that retain one scheduling-sensitive pool."""
    string_pairs=strings(source,header,asm,read,resolve)
    out=[]
    for _,body,private in string_pairs:
        out+=scalars(body,private,asm,read,resolve,order)
    return list(dict.fromkeys(out+string_pairs+scalars(source,header,asm,read,resolve,order)))[:8]
