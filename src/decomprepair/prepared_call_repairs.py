"""Bounded assigned-return scopes and typed argument captures; never change a callee's ABI."""
import re

from . import branch_call_support as support
from . import integer_abi, local_flow
from .integer_repair_support import IDENT, edit, view
from .pattern_repairs import close

FAMILIES = ('assigned_call_scope', 'prepared_call_scope', 'prepared_return_scope')
LIMIT = 12


def variants(source, name, header='', arch=None):
    part = view(source, name)
    context = header+'\n'+source
    if not part or re.search(support.FORBIDDEN+r'|\b(?:__LINE__|__COUNTER__|restrict)\b', source):return []
    volatile_declarations=re.findall(r'\bvolatile\b[^;{}]*;',header)
    volatile_names={m[1] for d in volatile_declarations for m in re.finditer(r'\b('+IDENT+r')\s*(?:\[[^]]*\])?\s*(?=;|,|=)',d)}
    if volatile_names.intersection(re.findall(IDENT,part.body)):return []
    macros = set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')',context,re.M))
    if macros.intersection(re.findall(IDENT, part.body)):return []
    try:flow = local_flow.analyze(source,name)
    except (ValueError,RecursionError):return []
    if not flow:return []
    result=[]

    def typed_call(destination, callee, argument, replacement_start, replacement_end, suffix='', result_type=None):
        signature = support.prototype(context[:len(header)+1+part.opening],callee,arch)
        if not signature or len(signature[1])!=1:return
        returns, types = signature
        if result_type and returns != result_type:return
        # One real argument only: capture preserves evaluation order and formal conversion.
        if re.search(r'[,;{}=?]|\+\+|--|\b'+IDENT+r'\s*\(',argument):return
        ty=types[0]
        if not (re.fullmatch(IDENT+r'(?:\s+'+IDENT+r')*\s*\*',ty) or integer_abi.scalar(ty,context,arch)):return
        capture='__repair_call_argument'
        if re.search(r'\b'+capture+r'\b',context):return
        statement=f'{destination} = {callee}({capture});'
        replacement=f'{capture} = {argument};\n    do {{\n      {statement}\n    }} while (0);'+suffix
        text=edit(source,[(part.opening+1,part.opening+1,'\n  '+ty+' '+capture+';'),
                          (replacement_start,replacement_end,replacement)])
        result.append(('prepared_return_scope' if suffix else 'prepared_call_scope',text))

    for node in flow.nodes:
        call=support.call(part,node)
        if not call or not call[0] or len(call[2])!=1:continue
        destination,callee,args=call
        # Require an actual unescaped local, not a global store or shadowed declaration.
        declarations=list(re.finditer(r'\b('+IDENT+r')\s+'+re.escape(destination)+r'\s*;',part.body))
        if len(declarations)!=1 or re.search(r'&\s*\(*\s*'+re.escape(destination)+r'\b',part.body):continue
        if not integer_abi.scalar(declarations[0][1],context,arch):continue
        if re.search(r'\b(?:extern|static)\s+[^;{}]*\b'+re.escape(destination)+r'\s*;',part.body):continue
        prefix=part.clean[:node.start]
        if re.search(r'\bdo\s*{\s*$',prefix):continue
        original=source[node.start:node.end]
        result.append(('assigned_call_scope',edit(source,[(node.start,node.end,'do { '+original+' } while (0);')])))
        typed_call(destination,callee,args[0],node.start,node.end)

    # Existing short-lived result copy with one independent pointer initialization between
    # call and copy. Move only a non-escaping local assignment, not reads or calls.
    pattern=rf'{{\s*({IDENT})\s+({IDENT})\s*=\s*({IDENT})\s*\('
    for hit in re.finditer(pattern,part.clean[part.opening+1:part.end-1]):
        if integer_abi.scalar(hit[1],context,arch) is None:continue
        start=part.opening+1+hit.start();opening=part.opening+hit.end()
        stop=close(part.clean,opening)
        if not stop:continue
        tail=re.match(rf'\s*;\s*({IDENT})\s*=\s*(&?\s*{IDENT})\s*;\s*({IDENT})\s*=\s*{hit[2]}\s*;\s*}}',part.clean[stop:])
        if not tail:continue
        pointer,address,destination=tail.groups()
        if len({pointer,address.strip('& '),destination,hit[2]})!=4:continue
        if not re.search(r'\b'+IDENT+r'(?:\s+'+IDENT+r')*\s*\*\s*'+re.escape(pointer)+r'\s*;',part.body):continue
        if len(re.findall(r'\b'+re.escape(hit[1])+r'\s+'+re.escape(destination)+r'\s*;',part.body))!=1:continue
        if re.search(r'&\s*\(*\s*(?:'+re.escape(destination)+'|'+re.escape(hit[2])+r')\b',part.body):continue
        if re.search(r'\b(?:extern|static)\s+[^;{}]*\b'+re.escape(destination)+r'\s*;',part.body):continue
        block_end=stop+tail.end()
        if len(re.findall(r'\b'+re.escape(hit[2])+r'\b',part.clean[start:block_end]))!=2:continue
        # No declarations outside this block may reuse the temporary's name.
        if re.search(r'\b'+re.escape(hit[2])+r'\b',part.clean[:start]+part.clean[block_end:]):continue
        argument=source[opening+1:stop-1]
        typed_call(destination,hit[3],argument,start,block_end,
                   '\n    '+pointer+' = '+address+';',hit[1])
    return list(dict.fromkeys(result))[:LIMIT]
