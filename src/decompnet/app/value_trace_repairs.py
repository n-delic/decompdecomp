"""A tiny two-edit pool around explicitly traced lines; ordinary judging and budgets apply."""
import difflib

from . import (
    focused_lifetime_repairs,
    loop_weight_repairs,
    operand_order_repairs,
    register_reuse_repairs,
    rtl_value_trace,
)
from .integer_repair_support import edit

FAMILY = 'value_trace_bundle'
LIMIT = 6


def changes(before, after):
    # Line spans keep generation bounded on large, repetitive C. Reject edits sharing a
    # physical line rather than composing two changes whose parser assumptions may interact.
    left=before.splitlines(keepends=True);right=after.splitlines(keepends=True)
    offsets=[0]
    for line in left:offsets.append(offsets[-1]+len(line))
    return [(offsets[a],offsets[b],''.join(right[c:d]))
            for tag,a,b,c,d in difflib.SequenceMatcher(None,left,right,autojunk=False).get_opcodes() if tag!='equal']


def touches(source, edits, lines, margin=0):
    return any(any(source[:a].count('\n')+1-margin<=line<=source[:b].count('\n')+1+margin for line in lines)
               for a,b,_ in edits)


def variants(source, name, trace, rows, header=''):
    lines=rtl_value_trace.focus(trace,source,rows,header)
    if not lines:return []
    primary=focused_lifetime_repairs.variants(source,name)+register_reuse_repairs.variants(source,name)
    secondary=operand_order_repairs.variants(source,name,header)+loop_weight_repairs.variants(source,name,header)
    first=[];second=[]
    for options,selected,margin in ((primary,first,0),(secondary,second,2)):
        for family,candidate in options:
            edits=changes(source,candidate)
            if len(edits)<=8 and touches(source,edits,lines,margin):selected.append((family,edits))
            if len(selected)==4:break
    result=[]
    for family,left in first:
        for other,right in second:
            if family==other or any(not (b<=c or d<=a) or a==b==c==d for a,b,_ in left for c,d,_ in right):continue
            candidate=edit(source,left+right)
            if candidate!=source and (FAMILY,candidate) not in result:result.append((FAMILY,candidate))
            if len(result)==LIMIT:return result
    return result
