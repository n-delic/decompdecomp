"""Bounded conversion/CSE scheduling hypotheses; preserve physical lines for legacy debug builds.

These are source candidates, never verdicts. Scope and ABI facts gate movement across calls;
only the normal compiler/link judge may retain an improvement.
"""
import re

from . import branch_call_support as support
from . import integer_abi
from .call_lifetime_repairs import block_path
from .csource import _code_only
from .integer_repair_support import IDENT, INTEGER, edit, integer, locals_for, refs, view

FAMILIES = ('debug_conversion_tail', 'delayed_narrow_capture', 'shared_shift_mask')
LIMIT = 8


def _part(source, name, header):
    part = view(_code_only(source),name)
    if not part or re.search(support.FORBIDDEN,part.body):return None
    # Line-sensitive macros and source mappings make physical-line edits observable in C itself.
    context = header+'\n'+source
    if re.search(r'\b__LINE__\b|^\s*#\s*(?:line\b|\d)',context,re.M):return None
    return part


def conversion_tails(source, name, header='', arch='', asm='', flags=()):
    """Materialize assignment conversion in a braced arm, on distinct physical lines."""
    part = _part(source,name,header)
    if not part or arch!='mips' or not re.search(r'\bmfc1\b',asm or ''):return []
    debug=[f for f in flags if re.fullmatch(r'-g[0-3]?',f)]
    if not debug or debug[-1]=='-g0':return []
    result=[]
    for decl in locals_for(part):
        if integer_abi.scalar(decl['type'],header+'\n'+source,arch) not in {(32,True),(32,False)}:continue
        for hit in refs(part,decl):
            match=re.match(r'\s*=(?!=)\s*([^;{}]+);',part.clean[hit.end():])
            if not match or part.clean[:hit.start()].rstrip()[-1:] not in ';{}':continue
            stop=hit.end()+match.end()
            if not re.match(r'\s*}',part.clean[stop:]):continue
            # Existing destination conversion is preserved; never introduce a float intermediate.
            expr=source[hit.end()+match.start(1):hit.end()+match.end(1)]
            if re.search(r'\b(?:sizeof|__LINE__)\b',expr):continue
            temp='decomp_conversion'
            while re.search(r'\b'+temp+r'\b',header+'\n'+source):temp+='_'
            replacement='{ '+decl['type']+' '+temp+';\n '+temp+' = '+expr+';\n '+decl['name']+' = '+temp+';\n }'
            result.append((FAMILIES[0],edit(source,[(hit.start(),stop,replacement)])))
            if len(result)>=LIMIT:return result
    return result


def delayed_captures(source, name, header='', arch=''):
    """Let a known narrow formal convert the argument, then capture the same scalar after the call."""
    if not _part(source,name,header):return []
    facts=support.facts(_code_only(source),name,header,arch)
    if not facts:return []
    part,flow,scalars=facts;context=header+'\n'+source;result=[]
    pattern=rf'\b(?P<type>{INTEGER})\s+(?P<dest>{IDENT})\s*=\s*\(\s*(?P<narrow>{INTEGER})\s*\)\s*(?P<value>{IDENT})\s*;'
    for decl in re.finditer(pattern,part.clean, re.M):
        if not part.opening<decl.start()<part.end:continue
        dest,value=decl['dest'],decl['value'];width=integer_abi.scalar(decl['narrow'],context,arch)
        if any(re.search(r'^\s*#\s*define[^\n]*\b'+v+r'\b',context,re.M) for v in (dest,value)):continue
        if not width or width[0] not in (8,16) or value not in scalars:continue
        if integer_abi.scalar(decl['type'],context,arch) not in {(32,True),(32,False)}:continue
        if len(re.findall(r'\b'+INTEGER+r'\s+'+dest+r'\s*(?=[;=,\[])',part.body))!=1:continue
        if re.search(r'(?<!&)&(?!&)\s*\(*\s*'+dest+r'\b',part.body):continue
        for node in sorted(flow.nodes,key=lambda n:n.start):
            if node.start<decl.end():continue
            between=part.clean[decl.end():node.start]
            # Only uninitialized declarations can intervene, no evaluation or control transfer.
            if re.sub(r'\b'+IDENT+r'(?:\s+'+IDENT+r')*\s*\*?\s*'+IDENT+r'\s*;','',between).strip():break
            call=support.call(part,node)
            if not call:
                if re.fullmatch(r'\s*'+IDENT+r'(?:\s+'+IDENT+r')*\s*\*?\s*'+IDENT+r'\s*;\s*',part.clean[node.start:node.end]):continue
                break
            _,callee,args=call;proto=support.prototype(context,callee,arch)
            if not proto or len(proto[1])!=len(args):break
            positions=[i for i,a in enumerate(args) if a==dest]
            if len(positions)!=1:break
            i=positions[0]
            if integer_abi.scalar(proto[1][i],context,arch)!=width:break
            if re.search(r'\b(?:'+value+'|'+dest+r')\b',between):break
            if call[0] in {value,dest} or block_path(part,decl.start())!=block_path(part,node.start):break
            if any(re.search(r'\b'+dest+r'\b',a) for j,a in enumerate(args) if j!=i):break
            # Other arguments may load records, but cannot modify the captured scalar or escape it.
            if any(re.search(r'\+\+|--|=(?!=)|(?<!&)&(?!&)|\b'+IDENT+r'\s*\(',a) for j,a in enumerate(args) if j!=i):break
            updated=list(args);updated[i]=value
            replacement=(call[0]+' = ' if call[0] else '')+callee+'('+', '.join(updated)+');\n'+dest+' = ('+decl['narrow']+')'+value+';'
            result.append((FAMILIES[1],edit(source,[(decl.start(),decl.end(),decl['type']+' '+dest+';'),(node.start,node.end,replacement)])))
            break
        if len(result)>=LIMIT:break
    return result


def shift_masks(source, name, header='', arch=''):
    """Keep a pure shared unsigned shift intermediate through a redundant known-zero-bit mask."""
    part=_part(source,name,header)
    if not part:return []
    result=[]
    pattern=rf'\b(?P<type>{INTEGER})\s+(?P<name>{IDENT})\s*=\s*(?P<expr>[^;{{}}]+);'
    for decl in re.finditer(pattern,part.clean):
        if not part.opening<decl.start()<part.end:continue
        if integer_abi.scalar(decl['type'],header+'\n'+source,arch) not in {(32,False),(32,True)}:continue
        expr=decl['expr'].strip()
        # Multiplication by a power of two, or left shift, guarantees low zero bits.
        scale=re.fullmatch(r'(.+)\s*\*\s*(2|4|8|16)\s*',expr)
        shift=re.fullmatch(r'(.+)\s*<<\s*([1-4])\s*',expr)
        if not (scale or shift) or not support.pure((scale or shift)[1]):continue
        bits=(integer(scale[2]).bit_length()-1) if scale else int(shift[2])
        variable=decl['name'];path=block_path(part,decl.start())
        if re.search(r'^\s*#\s*define[^\n]*\b'+variable+r'\b',header+'\n'+source,re.M):continue
        if len(re.findall(r'\b'+INTEGER+r'\s+'+variable+r'\s*(?=[;=,\[])',part.body))!=1:continue
        uses=list(re.finditer(r'\b'+variable+r'\b',part.clean[decl.end():part.end-1]))
        if len(uses)<2:continue
        tail=part.clean[decl.end():part.end-1]
        if re.search(r'(?<!&)&(?!&)\s*\(*\s*'+variable+r'\b|\b'+variable+r'\s*(?:\+\+|--|[+*/%&|^\-]?=(?!=))|(?:\+\+|--)\s*\b'+variable+r'\b',tail):continue
        for use in uses:
            start=decl.end()+use.start();stop=decl.end()+use.end()
            if block_path(part,start)[:len(path)]!=path:continue
            if not re.match(r'\s*<<\s*\d+',part.clean[stop:]):continue
            mask=0xFFFFFFFF^((1<<bits)-1)
            result.append((FAMILIES[2],edit(source,[(start,stop,f'({variable} & 0x{mask:08X}U)')])) )
            if len(result)>=LIMIT:return result
    return result


def variants(source,name,header='',arch='',asm='',flags=()):
    return (conversion_tails(source,name,header,arch,asm,flags)
            + delayed_captures(source,name,header,arch) + shift_masks(source,name,header,arch))
