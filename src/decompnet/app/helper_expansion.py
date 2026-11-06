"""Expand a tiny candidate-owned pointer initializer; no guessed helpers or repeated side effects."""
import re

from ..forks.csource import _blank, find
from .integer_repair_support import edit

FAMILIES = ('small_helper_expansion',)
ID = r'[A-Za-z_]\w*'


def variants(source, name, header=''):
    span=find(source,name)
    if not span:return []
    clean=_blank(source);context=_blank(header);result=[]
    # Only one pointer parameter and straight-line field assignments. No control flow, calls,
    # locals, return values, volatile accesses or argument expressions to evaluate repeatedly.
    for definition in re.finditer(r'\bstatic\s+(?:(?:inline|__inline__)\s+)?void\s+('+ID+r')\s*\(\s*(?:struct\s+)?'+ID+r'\s*\*\s*('+ID+r')\s*\)\s*\{([^{}]*)\}',context):
        helper,param,body=definition.groups()
        if helper==name or not 1<=body.count(';')<=6 or re.search(r'\b(?:volatile|asm|return|goto)\b|\+\+|--|[?()]',body):continue
        statements=[x.strip() for x in body.split(';') if x.strip()]
        lhs=param+r'\s*->\s*'+ID+r'(?:\s*\.\s*'+ID+r')*'
        if not all(re.fullmatch(lhs+r'\s*=\s*[^;=]+',statement) for statement in statements):continue
        # Unknown macros can hide side effects; only transparent expressions are eligible.
        from .source_match_context import macros
        if any(re.search(r'\b'+re.escape(m)+r'\b',body) and not re.fullmatch(r'\s*#\s*define\s+'+re.escape(m)+r'\s+[-+]?(?:0[xX][0-9A-Fa-f]+|[0-9]+)[uUlL]*\s*',definition[0]) for m,definition in macros(header).items()):continue
        edits=[]
        for call in re.finditer(r'\b'+re.escape(helper)+r'\s*\(\s*('+ID+r')\s*\)\s*;',clean[span[0]:span[1]]):
            arg=call[1]
            if re.search(r'\bvolatile\b[^;{}]*\b'+re.escape(arg)+r'\b',clean+context):continue
            expanded=re.sub(r'\b'+re.escape(param)+r'\b','('+arg+')',body)
            edits.append((span[0]+call.start(),span[0]+call.end(),'{'+expanded+'}'))
        if edits:result.append((FAMILIES[0],edit(source,edits[:4])))
        if len(result)>=2:break
    return result
