"""Delay an unescaped local narrow capture until after its sole intervening call."""
import re

from . import integer_abi
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, INTEGER, edit, split_args
from .pattern_repairs import close
from .repair_source_view import read

FAMILY='post_call_narrow_capture'


def variants(source,name,header='',arch=None):
    part=read(source,name);context=header+'\n'+source
    if not part or re.search(r'\b(?:volatile|asm|__asm__|goto|setjmp|longjmp)\b',part.body):return []
    macros=set(re.findall(r'^\s*#\s*define\s+('+IDENT+')',context,re.M));result=[]
    pattern=r'\b('+INTEGER+r')\s+('+IDENT+r')\s*=\s*(\(\s*('+INTEGER+r')\s*\)\s*('+IDENT+r'))\s*;'
    for d in re.finditer(pattern,part.clean):
        if not part.opening<d.start()<part.end:continue
        abi=integer_abi.scalar(d[4],context,arch)
        if not abi or abi[0] not in (8,16) or integer_abi.scalar(d[1],context,arch) not in {(32,True),(32,False)}:continue
        dest,operand=d[2],d[5]
        if len(re.findall(r'\b'+INTEGER+r'\s+\**\s*'+re.escape(dest)+r'\b',part.body))!=1:continue
        if not re.search(r'\b'+INTEGER+r'\s+'+re.escape(operand)+r'\s*[;=]',part.body):continue
        if re.search(r'(?<!&)&(?!&)\s*\(*\s*(?:'+re.escape(dest)+'|'+re.escape(operand)+r')\b',part.body):continue
        if len(re.findall(r'\b'+INTEGER+r'\s+\**\s*'+re.escape(operand)+r'\b',part.body))!=1:continue
        for call in re.finditer(r'\b('+IDENT+r')\s*\(',part.clean[d.end():part.end]):
            start=d.end()+call.start();opening=d.end()+call.end()-1;end=close(part.clean,opening)
            if not end or not re.match(r'\s*;',part.clean[end:]):continue
            stop=end+re.match(r'\s*;',part.clean[end:]).end()
            if block_path(part,d.start())!=block_path(part,start) or part.clean[:start].rstrip()[-1:] not in ';{}':continue
            between=part.clean[d.end():start]
            # Only declarations may intervene; no hidden loads, control or side effects.
            if between.strip() and not re.fullmatch(r'(?:\s*'+IDENT+r'\s*\*?\s*'+IDENT+r'\s*;\s*)+',between):continue
            args=part.clean[opening+1:end-1];spans=split_args(args)
            hits=[(a,b) for a,b in spans if args[a:b].strip()==dest]
            if len(hits)!=1 or len(re.findall(r'\b'+re.escape(dest)+r'\b',args))!=1:continue
            # Evaluating another argument must not mutate the captured local.
            if re.search(r'\+\+|--|(?<![=!<>])=(?!=)|'+IDENT+r'\s*\(',args):continue
            if macros & set(re.findall(IDENT,part.clean[d.start():stop])):continue
            a,b=hits[0];newargs=args[:a]+d[3]+args[b:]
            calltext=source[start:opening+1]+newargs+source[end-1:stop]
            result.append((FAMILY,edit(source,[(d.start(),d.end(),d[1]+' '+dest+';'),(start,stop,calltext+'\n'+dest+' = '+d[3]+';')])) )
            if len(result)>=4:return result
    return result
