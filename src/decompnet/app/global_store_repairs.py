"""Scalar-address lifetimes and split bitmask stores, proposed to the ordinary exact judge."""
import re

from ..forks.csource import _blank
from .integer_repair_support import IDENT, INTEGER, NUMBER, edit, refs, view
from .pattern_repairs import close


def variants(source,name,header=''):
    part=view(source,name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__|goto)\b',part.body):return []
    out=[]
    def add(family,edits):
        text=edit(source,edits)
        if (family,text) not in out and len(out)<6:out.append((family,text))
    declarations=list(re.finditer(rf'\b(?P<type>{INTEGER})\s+(?P<name>{IDENT})\s*;',part.clean,))
    for decl in declarations:
        if not part.opening<decl.start()<part.end:continue
        variable=decl['name']
        if re.search(rf'&\s*{variable}\b',part.body):continue
        pair=re.compile(rf'\b{variable}\s*=\s*(?P<global>{IDENT})\s*;\s*(?P=global)\s*=\s*(?P<callee>{IDENT})\s*\(')
        for hit in pair.finditer(part.clean,decl.end(),part.end):
            if part.clean[part.opening+1:hit.start()].rstrip()[-1:] not in {'',';','{','}'}:continue
            end=close(part.clean,hit.end()-1)
            tail=re.match(r'\s*;',part.clean[end:]) if end else None
            if not tail:continue
            global_=hit['global'];callee=hit['callee']
            global_decl=re.search(rf'\bextern\s+{re.escape(decl["type"])}\s+{global_}\s*;',_blank(header+'\n'+source[:part.opening]))
            if not global_decl:continue
            uses=refs(part,decl)
            if len(uses)!=2 or not all(hit.start()<=use.start()<end for use in uses):continue
            args=part.clean[hit.end():end-1]
            if not all(re.fullmatch(rf'(?:{IDENT}|-?{NUMBER})',arg.strip()) for arg in args.split(',')):continue
            if len(re.findall(rf'\b{variable}\b',args))!=1:continue
            pointer='__repair_scalar_address'
            if re.search(rf'\b{pointer}\b',part.clean):continue
            actual_args=source[hit.end():end-1]
            arg_use=re.search(rf'\b{variable}\b',args)
            actual_args=actual_args[:arg_use.start()]+'*'+pointer+actual_args[arg_use.end():]
            text=f'{variable} = {callee}({actual_args}); *{pointer} = {variable};'
            add('scalar_global_address',[(part.opening+1,part.opening+1,f'\n{decl["type"]} *{pointer} = &{global_};\n'),
                                          (hit.start(),end+tail.end(),text)])
    # Read/write the same explicit integer lvalue. Keep both masks and their order; no calls,
    # increments, computed indexing or pointer expressions with side effects are duplicated.
    member=rf'{IDENT}(?:\s*->\s*{IDENT})+'
    memory=rf'\*\(\s*(?:s32|u32|int|unsigned\s+int)\s*\*\s*\)\s*\(\s*(?:\(\s*(?:u8|char)\s*\*\s*\)\s*)?{IDENT}\s*\+\s*{NUMBER}\s*\)'
    mask=re.compile(rf'(?P<lhs>{memory}|{member})\s*=(?!=)\s*\(\s*(?P<again>[^;{{}}]+?)\s*&\s*(?P<a>0x[0-9a-fA-F]+[uU]?)\s*\)\s*&\s*(?P<b>0x[0-9a-fA-F]+[uU]?)\s*;')
    for hit in mask.finditer(part.clean,part.opening+1,part.end):
        if part.clean[part.opening+1:hit.start()].rstrip()[-1:] not in {'',';','{','}'}:continue
        normalize=lambda text:re.sub(r'\s+','',text)
        if normalize(hit['lhs'])!=normalize(hit['again']):continue
        lhs=source[hit.start('lhs'):hit.end('lhs')]
        add('split_mask_stores',[(hit.start(),hit.end(),f'{lhs} &= {hit["a"]}; {lhs} &= {hit["b"]};')])
        unsigned=re.sub(r'\b(?:s32|int)\b',lambda m:'u32' if m[0]=='s32' else 'unsigned int',lhs,count=1)
        if unsigned!=lhs and 'unsigned' not in lhs:
            add('split_mask_stores',[(hit.start(),hit.end(),f'{unsigned} &= {hit["a"]}; {unsigned} &= {hit["b"]};')])
    return out
