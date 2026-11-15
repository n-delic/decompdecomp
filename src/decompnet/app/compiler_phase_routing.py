"""Advisory routing from verified GCC dumps; never declare a family impossible.

A constant absent after first CSE cannot explain a late saved load in that build.
Defer tiny constant-only spellings of that same input and try a structural dispatch.
Unknown/truncated/stale captures cannot reorder anything. No proposal is removed.
"""
import hashlib
import re

from .repair_tokens import tokens


def constants(texts, name):
    from .rtl_value_trace import function
    initial=function(texts.get('rtl',''),name)
    cse=function(texts.get('cse',''),name)
    if not initial or not cse:return {}
    values=lambda text:set(map(int,re.findall(r'\(const_int\s+(-?\d+)\)',text)))
    return {'removed_before_allocation':sorted(values(initial)-values(cse))[:32],
            'note':'Constants present in initial RTL and absent after first CSE; not a variable identity or proof that a family is exhausted.'}


def order(source, proposals, trace, header='', *, allocation=None):
    if (not trace or trace.get('truncated') or trace.get('source_sha256')!=hashlib.sha256(source.encode()).hexdigest()
            or trace.get('header_sha256')!=hashlib.sha256(header.encode()).hexdigest()):
        return proposals
    removed=set(trace.get('early_constants',{}).get('removed_before_allocation',[]))
    if allocation and not allocation.get('truncated') and allocation.get('values'):
        # Real candidate allocation counts justify a short structural lane. Source/hash
        # checks above prevent an earlier source or header from guiding this input.
        preferred={'inline_bit_predicate','masked_role_sequence','operation_scope_merge','operation_scope_unwrap'}
        proposals=sorted(proposals,key=lambda p:p[0] not in preferred)
    if not removed:return proposals
    original=[t for t in tokens(source) if not t.isspace()]
    def tiny_constant_edit(candidate):
        updated=[t for t in tokens(candidate) if not t.isspace()]
        if len(original)!=len(updated):return False
        edits=[(a,b) for a,b in zip(original,updated) if a!=b]
        if not 0<len(edits)<=2:return False
        def number(t):
            return int(t,0) if re.fullmatch(r'(?:0[xX][0-9a-fA-F]+|[1-9][0-9]*|0)',t) else None
        return all(number(a) in removed and number(b) is not None for a,b in edits)
    structural=[p for p in proposals if p[0]=='effectful_dispatch_specialization']
    ordinary=[p for p in proposals if p not in structural and not tiny_constant_edit(p[1])]
    deferred=[p for p in proposals if p not in structural and tiny_constant_edit(p[1])]
    return structural+ordinary+deferred
