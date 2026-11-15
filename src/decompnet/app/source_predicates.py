"""Private copies of actual mapped range predicates; no inferred helper implementation."""
import re

from ..forks.csource import _blank, find
from .source_context import prototypes
from .source_match_context import macro_context, macros

ID=r'[A-Za-z_]\w*'


def variants(source, name, header, reference):
    result=[];clean=_blank(source)
    for helper in sorted(set(re.findall(r'\b('+ID+r')\s*\(',clean))-{name}):
        span=find(reference,helper)
        if not span or find(header,helper) or helper in macros(header):continue
        code=reference[span[0]:span[1]];plain=_blank(code)
        signature=re.match(r'\s*(?:static\s+)?(?:BOOL|int|s32)\s+'+re.escape(helper)+r'\s*\([^;{}]*\)\s*{',plain)
        if not signature:continue
        body=plain[signature.end():].strip().removesuffix('}').strip()
        match=re.fullmatch(r'(?:int|s32)\s+('+ID+r')\s*;\s*\1\s*=\s*('+ID+r')\s*\(([\w\s,*]+)\)\s*;\s*return\s+(.+);',body,re.S)
        if not match or match[2]==helper:continue
        value,callee,args,condition=match.groups()
        # One real callee result, then a pure lower/upper range test. No calls or duplicated work.
        bound=rf'(?:-?\d+|0[xX][0-9a-fA-F]+|{ID})'
        if not re.fullmatch(r'\(*\s*'+re.escape(value)+r'\s*>=\s*'+bound+r'\s*\)*\s*&&\s*\(*\s*'+re.escape(value)+r'\s*<=\s*'+bound+r'\s*\)*',condition.strip().strip('()')):continue
        if not all(re.fullmatch(ID,a.strip()) for a in args.split(',')):continue
        # Only remove a plain declaration of this exact helper from the candidate-private header.
        declaration=re.compile(r'(?m)^[ \t]*(?:extern\s+)?(?:BOOL|int|s32)\s+'+re.escape(helper)+r'\s*\([^;{}]*\)\s*;[ \t]*')
        private=declaration.sub('',header)
        prefix=code[:plain.index('{')]
        if not re.search(r'\bstatic\b',prefix):code='static '+code
        private+=macro_context(reference,code,private)+prototypes(reference,code,private,helper)
        if not re.search(r'\b'+re.escape(callee)+r'\s*\(',_blank(private)) and callee not in macros(private):
            prototype=re.search(r'(?:^|[;\n])[ \t]*((?:extern\s+)?(?:'+ID+r'[ \t*]+)+'+re.escape(callee)+r'\s*\([^;{}()]*\)\s*;)',_blank(reference))
            if not prototype:continue
            private+='\n'+reference[prototype.start(1):prototype.end(1)]+'\n'
        private+=f'\n#line {reference[:span[0]].count(chr(10))+1} "reference-predicate.c"\n'+code+'\n'
        result.append((source,private))
        if len(result)>=2:break
    return result
