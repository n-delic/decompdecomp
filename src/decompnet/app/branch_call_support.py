"""Conservative spans and scalar facts for branch/call lowering candidates.

These readers never infer missing prototypes or evaluate macros. Unknown syntax yields no proposal.
"""
import re

from ..forks.csource import _blank
from . import integer_abi, local_flow
from .integer_repair_support import IDENT, INTEGER, split_args, view
from .pattern_repairs import close

DECL = re.compile(rf'\b(?P<type>{INTEGER})\s+(?P<name>{IDENT})\s*(?:=\s*(?P<value>[^;{{}}]+))?;')
FORBIDDEN = r'\b(?:volatile|asm|__asm__|setjmp|longjmp|_setjmp|sigsetjmp)\b'


def facts(source, name, header='', arch=None):
    part = view(source,name)
    if not part or re.search(FORBIDDEN,part.body) or re.search(r'^\s*#\s*(?:line\b|\d)',source,re.M):return None
    context = _blank(header+'\n'+source)
    macros = set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')\b',context,re.M))
    if macros & set(re.findall(IDENT,part.body)):return None
    declarations = []
    records=[]
    for record in re.finditer(r'\b(?:struct|union)(?:\s+'+IDENT+r')?\s*{',part.body):
        start=part.opening+record.end()
        stop=close(part.clean,start,'{','}')
        if stop:records.append((start,stop))
    for decl in DECL.finditer(part.clean,part.opening+1,part.end-1):
        if any(a<decl.start()<b for a,b in records):continue
        prefix = part.clean[:decl.start()].rstrip()
        if prefix[-1:] not in ';{}':continue
        variable = decl['name']
        shadows = re.findall(rf'\b(?:{IDENT})(?:\s+\**\s*|\s*\*+\s*){variable}\s*(?=[;=,\[])',part.body)
        siblings=[d for d in DECL.finditer(part.body) if d['name']==variable]
        if len(shadows)!=len(siblings) or any(d['type']!=decl['type'] for d in siblings) or re.search(rf'(?<!&)&(?!&)\s*\(*\s*{variable}\b',part.body):continue
        if integer_abi.scalar(decl['type'],context,arch) in {(32,True),(32,False)}:declarations.append(decl)
    try:flow = local_flow.Flow(part,declarations)
    except (ValueError,RecursionError):return None
    signature = re.search(r'\b'+re.escape(name)+r'\s*\(([^{};]*)\)\s*$',part.clean[:part.opening])
    scalars = {d['name']:d['type'] for d in declarations}
    if signature:
        for a,b in split_args(signature[1]):
            param = re.fullmatch(r'\s*(.+?)\s+('+IDENT+r')\s*',signature[1][a:b])
            if (param and integer_abi.scalar(param[1],context,arch) in {(32,True),(32,False)}
                    and not re.search(r'(?<!&)&(?!&)\s*\(*\s*'+re.escape(param[2])+r'\b',part.body)
                    and not re.search(r'\b'+IDENT+r'\s+\**\s*'+re.escape(param[2])+r'\s*(?=[;=,\[])',part.body)):
                scalars[param[2]]=param[1]
    return part,flow,scalars


def pure(text):
    """No loads, calls, assignments, comma expressions, increments or opaque C extensions."""
    return bool(text.strip()) and not re.search(r'->|\.|\[|[=,{};?:]|\+\+|--|'+IDENT+r'\s*\(',text) and bool(
        re.fullmatch(r'[\w\s()+\-*/%&|^~<>]*',text)) and not re.search(r'(?:^|[+(\-])\s*[*&]\s*'+IDENT,text)


def call(part, node):
    """A complete statement call, possibly assigning its return to a scalar or pointer local."""
    text = part.clean[node.start:node.end]
    hit = re.fullmatch(rf'\s*(?:({IDENT})\s*=\s*)?({IDENT})\s*\((.*)\)\s*;\s*',text,re.S)
    if not hit or hit[2] in {'if','for','while','switch','return','sizeof'}:return None
    args = [hit[3][a:b].strip() for a,b in split_args(hit[3])]
    return hit[1],hit[2],args


def prototype(context, name, arch=None):
    """Only consistent explicit ANSI prototypes with scalar/pointer formals, never variadics."""
    clean = _blank(context)
    matches = list(re.finditer(rf'(?:^|(?<=[;}}]))\s*(?:extern\s+)?({IDENT}(?:\s+{IDENT})*\s*\**\s*)\b{re.escape(name)}\s*\(([^;{{}}]*)\)\s*;',clean))
    signatures = []
    for match in matches:
        types=[]
        for a,b in split_args(match[2]):
            spelling=match[2][a:b].strip()
            named=re.fullmatch(r'(.+?)(?:\s+|(?<=\*))('+IDENT+r')',spelling)
            if named and (integer_abi.scalar(named[1].strip(),context,arch) is not None or re.fullmatch(IDENT+r'\s*\*',named[1].strip())):
                spelling=named[1].strip()
            if not re.fullmatch(rf'{IDENT}(?:\s+{IDENT})*\s*\**',spelling):return None
            types.append(spelling)
        if not types or types==['void']:return None
        signatures.append((match[1].strip(),types))
    return signatures[0] if signatures and all(s==signatures[0] for s in signatures) else None


def local_expression(text, part, scalars, context, arch, *, arithmetic=False):
    """Allow scalar arithmetic or passing declared pointer/function values, never a global read."""
    if not pure(text):return False
    words=set(re.findall(r'\b'+IDENT+r'\b',text))
    allowed=set(scalars)
    if not arithmetic:
        signature=re.search(r'\b'+IDENT+r'\s*\(([^{};]*)\)\s*$',part.clean[:part.opening])
        pointer_scope=part.body+(' '+signature[1] if signature else '')
        allowed.update(re.findall(r'\b'+IDENT+r'\s*\*+\s*('+IDENT+r')\s*(?=[;=,)]|$)',pointer_scope))
        allowed.update(re.findall(r'\b('+IDENT+r')\s*\([^;{}]*\)\s*;',_blank(context)))
    return all(w in allowed or integer_abi.scalar(w,context,arch) is not None or w in {'signed','unsigned','int','short','long'} for w in words)
