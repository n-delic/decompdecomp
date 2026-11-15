"""Select configured numeric C branches without executing source; retain every original newline."""
import ast
import re

from ..forks.csource import _blank
from ..network import NetworkError

BUILTINS={'__GNUC__','__GNUC_MINOR__','__sgi','__mips','__cplusplus','__STDC__','_MIPS_ISA','_MIPS_SIM','_MIPS_SZINT','_MIPS_SZLONG','_MIPS_SZPTR','_LANGUAGE_C','_ULTRA64'}


def defined(name,macros):
    if name in BUILTINS and name not in macros:
        raise NetworkError('Set the compiler-specific condition '+name+' in numeric overrides or supply a prepared subset.')
    return name in macros


def expression(text, macros, depth=0):
    if len(text)>2048 or depth>12:
        raise NetworkError('Recursive source condition. Supply a prepared source subset.')
    text=re.sub(r'/\*.*?\*/|//[^\n]*',' ',text)
    text=re.sub(r'\bdefined\s*(?:\(\s*(\w+)\s*\)|(\w+))',lambda m:str(int(defined(m[1] or m[2],macros))),text)
    def expand(m):
        name=m[0]
        if name in {'and','or','not'}:return name
        if not defined(name,macros):return '0'
        return str(expression(macros[name],macros,depth+1))
    text=re.sub(r'\b[A-Za-z_]\w*\b',expand,text)
    text=re.sub(r'(?<=[0-9a-fA-F])[uUlL]+\b','',text).replace('&&',' and ').replace('||',' or ')
    text=re.sub(r'!(?!=)',' not ',text)
    try:tree=ast.parse(text.strip(),mode='eval')
    except (SyntaxError,ValueError) as exc:raise NetworkError('Unsupported source condition. Set numeric overrides or supply a prepared subset.') from exc
    allowed=(ast.Expression,ast.Constant,ast.BoolOp,ast.And,ast.Or,ast.UnaryOp,ast.Not,ast.USub,ast.UAdd,ast.Invert,ast.Compare,ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE,ast.BinOp,ast.BitAnd,ast.BitOr,ast.BitXor)
    if any(not isinstance(n,allowed) for n in ast.walk(tree)):
        raise NetworkError('Unsupported source condition. Supply numeric overrides or a prepared subset.')
    def value(n):
        if isinstance(n,ast.Expression):return value(n.body)
        if isinstance(n,ast.Constant) and type(n.value) is int:return n.value
        if isinstance(n,ast.BoolOp):return int(all(value(x) for x in n.values) if isinstance(n.op,ast.And) else any(value(x) for x in n.values))
        if isinstance(n,ast.UnaryOp) and isinstance(n.op,(ast.Not,ast.USub,ast.UAdd,ast.Invert)):
            v=value(n.operand);return int(not v) if isinstance(n.op,ast.Not) else -v if isinstance(n.op,ast.USub) else v if isinstance(n.op,ast.UAdd) else ~v
        if isinstance(n,ast.Compare) and len(n.ops)==1:
            a,b=value(n.left),value(n.comparators[0]);op=n.ops[0]
            if isinstance(op,ast.Eq):return int(a==b)
            if isinstance(op,ast.NotEq):return int(a!=b)
            if isinstance(op,ast.Lt):return int(a<b)
            if isinstance(op,ast.LtE):return int(a<=b)
            if isinstance(op,ast.Gt):return int(a>b)
            if isinstance(op,ast.GtE):return int(a>=b)
        if isinstance(n,ast.BinOp) and isinstance(n.op,(ast.BitAnd,ast.BitOr,ast.BitXor)):
            a,b=value(n.left),value(n.right)
            return a&b if isinstance(n.op,ast.BitAnd) else a|b if isinstance(n.op,ast.BitOr) else a^b
        raise NetworkError('Unsupported source condition. Supply numeric overrides or a prepared subset.')
    return value(tree)


def select(text, initial=None, overrides=None):
    forced=overrides or {};macros={**(initial or {}),**forced};stack=[];out=[];active=True
    for line,clean in zip(text.splitlines(True),_blank(text).splitlines(True)):
        m=re.match(r'\s*#\s*(if|ifdef|ifndef|elif|else|endif|define|undef)\b(.*)',clean)
        if m:
            directive,arg=m[1],m[2].strip()
            if directive in {'if','ifdef','ifndef'}:
                condition=False
                if active:
                    condition=defined(arg,macros) if directive=='ifdef' else not defined(arg,macros) if directive=='ifndef' else bool(expression(arg,macros))
                stack.append([active,bool(condition),False]);active=active and condition
            elif directive in {'else','elif'}:
                if not stack or stack[-1][2]:raise NetworkError('Unbalanced source condition. Supply a prepared subset.')
                parent,taken,_=stack[-1]
                condition=not taken if directive=='else' else not taken and parent and bool(expression(arg,macros))
                stack[-1][1]=taken or condition;stack[-1][2]=directive=='else';active=parent and condition
            elif directive=='endif':
                if not stack:raise NetworkError('Unbalanced source condition. Supply a prepared subset.')
                active=stack.pop()[0]
            elif active and directive=='define':
                define=re.match(r'([A-Za-z_]\w*)(?!\w)([ \t]+.*|$)',arg)
                if define:macros[define[1]]=forced.get(define[1],define[2].strip())
            elif active and directive=='undef':macros.pop(arg,None)
            if directive in {'if','ifdef','ifndef','elif','else','endif'}:
                out.append(re.sub(r'[^\n]',' ',line));continue
        out.append(line if active else re.sub(r'[^\n]',' ',line))
    if stack:raise NetworkError('Unclosed source condition. Supply a prepared subset.')
    return ''.join(out),macros
