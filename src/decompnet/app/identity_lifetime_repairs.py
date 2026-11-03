"""Relocate an existing, proven zero identity; never invent padding reads or new identities."""
import re

from . import local_flow
from .allocator_call_guards import context, dominators
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, edit, locals_for, split_args

FAMILY = 'existing_identity_lifetime'
LIMIT = 12
WORD = r'(?:u32|unsigned\s+int)'
SCALAR = r'(?:[su]32|int|unsigned\s+int|signed\s+int)'


def variants(source, name, header='', arch=None):
    if arch not in {'mips','ppc','x86','arm','thumb'}:
        return []
    types = {'u32':r'unsigned\s+int', 's32':r'(?:signed\s+)?int', 's16':r'(?:signed\s+)?short(?:\s+int)?'}
    for alias,allowed in types.items():
        definitions = re.findall(r'\btypedef\s+([^;{}]+?)\s+'+alias+r'\s*;',header+'\n'+source)
        if not definitions or any(not re.fullmatch(allowed,v.strip()) for v in definitions):
            return []
    part,_,macros = context(source,name,header)
    if not part:
        return []
    # Exact algebraic shape, with every operand a plain unescaped 32-bit scalar. The signed
    # halfword value is converted to unsigned before multiplication; the factor is exactly zero.
    pattern = (rf'\b(?P<zero>{IDENT})\s*=\s*\(\s*{WORD}\s*\)\s*(?P<a>{IDENT})\s*\^\s*'
               rf'\(\s*{WORD}\s*\)\s*(?P<context>{IDENT})\s*;\s*'
               rf'(?P=zero)\s*\^=\s*\(\s*{WORD}\s*\)\s*(?P=context)\s*;\s*'
               rf'(?P=zero)\s*\^=\s*\(\s*{WORD}\s*\)\s*(?P=a)\s*;\s*'
               rf'(?P=context)\s*=\s*\(\s*{WORD}\s*\)\s*(?P=context)\s*\+\s*'
               rf'\(\s*{WORD}\s*\)\s*\(\s*s16\s*\)\s*(?P<value>{IDENT})\s*\*\s*(?P=zero)\s*;')
    flow = local_flow.analyze(source,name)
    if not flow:
        return []
    dom = dominators(flow)
    signature_match = re.search(r'\b'+re.escape(name)+r'\s*\(([^{}]*)\)\s*$',part.clean[:part.opening])
    if not signature_match or {'u32','s32','s16'} & macros.keys():
        return []
    signature = signature_match[1]
    parameters = {m[1] for a,b in split_args(signature)
                  if (m := re.fullmatch(rf'\s*{SCALAR}\s+({IDENT})\s*',signature[a:b]))}
    declarations = {d['name']:d for d in locals_for(part) if re.fullmatch(SCALAR,d['type'])}
    result = []
    for hit in re.finditer(pattern,part.body):
        start,end = part.opening+1+hit.start(),part.opening+1+hit.end()
        zero = hit['zero'];read = {hit['a'],hit['context'],hit['value']}
        if zero in read or zero not in declarations or declarations[zero]['type'] not in {'u32','unsigned int'}:
            continue
        if len(re.findall(rf'\b{zero}\b',part.body)) != 5:
            continue  # declaration plus these four statements; no other observable use of scratch
        if (not read <= declarations.keys() | parameters or (read | {zero}) & macros.keys()
                or read & declarations.keys() & parameters):
            continue
        if any(re.search(rf'&\s*\(*\s*{v}\b',part.body) for v in read | {zero}):
            continue
        prefix = part.clean[part.opening+1:start].rstrip()
        if prefix and prefix[-1] not in ';{}':
            continue
        path = block_path(part,start)
        block = source[start:end]
        anchors = []
        for index,node in enumerate(flow.nodes):
            text = part.clean[node.start:node.end].strip()
            call = re.match(rf'({IDENT})\s*\(',text)
            if (not call or call[1] in macros or call[1] in {'if','while','for','switch','sizeof'}
                    or not text.endswith(';') or start <= node.start < end
                    or block_path(part,node.start) != path):
                continue
            prefix = part.clean[part.opening+1:node.start].rstrip()
            if prefix and prefix[-1] not in ';{}':
                continue  # never insert several statements into an unbraced conditional/loop body
            for pos in (node.start,node.end):
                eligible = declarations[zero].end() <= pos
                for v in read - parameters:
                    decl = declarations[v]
                    if decl.end() > pos:
                        eligible = False;break
                    definitions = [i for i in dom.get(index,set()) if i!=index
                                   and re.fullmatch(rf'\s*{v}\s*=(?!=)[^;]*;\s*',part.clean[flow.nodes[i].start:flow.nodes[i].end])
                                   and not re.search(rf'\b{v}\b',part.clean[flow.nodes[i].start:flow.nodes[i].end].split('=',1)[1])]
                    if not definitions:
                        eligible = False;break
                if eligible:
                    anchors.append(pos)
        # Later call boundaries first; no factorial placement search and no control-flow edits.
        for pos in sorted(set(anchors),reverse=True)[:LIMIT]:
            candidate = edit(source,[(start,end,''),(pos,pos,'\n'+block+'\n')])
            if candidate != source and (FAMILY,candidate) not in result:
                result.append((FAMILY,candidate))
    return result[:LIMIT]
