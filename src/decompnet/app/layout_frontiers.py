"""Reapply a structural edit using bounded saved source layouts, preserving candidate tokens.

Equivalent tokens are required for edit transplantation. Related donors can instead
lend whitespace along shared token runs; their code is never copied. Comments and
directives stay opaque. Old layouts are recompiled under today's frozen build before any
result is adopted; a historical scalar score is never reused as current evidence.
"""
import difflib
import re

from .integer_repair_support import edit
from .repair_tokens import TOKEN

FAMILY = 'saved_layout_followup'
LIMIT = 3
STRUCTURAL = {'effectful_dispatch_specialization','aggregate_pointer_dispatch','aggregate_call_preparation'}


def spans(source):
    return [m for m in TOKEN.finditer(source) if not m[0].isspace()]


def safe(source, header=''):
    # Even identical macro invocation tokens can expand differently on a new line.
    return not re.search(r'\b(?:__LINE__|__FILE__|__COUNTER__)\b|\\\n|(?m:^[ \t]*#\s*(?:line\b|[0-9]))', source+'\n'+header)


def equivalent(left, right, header=''):
    return safe(left,header) and safe(right,header) and [m[0] for m in spans(left)] == [m[0] for m in spans(right)]


def project(source, layout, header=''):
    """Borrow whitespace only from long shared token runs, never borrow donor code.

    Printers may expand a macro or add redundant parentheses. Those unmatched tokens
    remain untouched. Directives and comments are never crossed or edited.
    """
    if not safe(source,header) or not safe(layout,header) or len(source)+len(layout)>150000:
        return None
    left,right=spans(source),spans(layout)
    if max(len(left),len(right))>12000:return None
    blocks=difflib.SequenceMatcher(None,[m[0] for m in left],[m[0] for m in right],autojunk=False).get_matching_blocks()
    if not left or sum(b.size for b in blocks)<len(left)*.75:
        return None
    changes=[]
    for block in blocks:
        if block.size<4:
            continue
        for offset in range(block.size-1):
            a,b=left[block.a+offset],left[block.a+offset+1]
            c,d=right[block.b+offset],right[block.b+offset+1]
            old,new=source[a.end():b.start()],layout[c.end():d.start()]
            line=source[source.rfind('\n',0,a.start())+1:a.start()]
            if old==new or (old and not old.isspace()) or (new and not new.isspace()):continue
            if line.lstrip().startswith('#') or a[0].startswith(('//','/*')) or b[0].startswith(('//','/*')):continue
            changes.append((a.end(),b.start(),new))
    candidate=edit(source,changes)
    return candidate if candidate!=source and equivalent(source,candidate,header) else None


def transplant(before, after, layout, header=''):
    """Apply only token edits; retain the donor's untouched whitespace and comments verbatim."""
    if not equivalent(before,layout,header) or not safe(after,header) or len(before)+len(after)>150000:
        return None
    old,new,donor = spans(before),spans(after),spans(layout)
    if max(len(old),len(new))>12000:return None
    changes=[]
    for kind,a,b,c,d in difflib.SequenceMatcher(None,[m[0] for m in old],[m[0] for m in new],autojunk=False).get_opcodes():
        if kind == 'equal':
            continue
        # Include new leading whitespace only for an insertion at the beginning.
        start = donor[a].start() if a<len(donor) else len(layout)
        stop = donor[b-1].end() if b>a else start
        replacement = after[new[c].start():new[d-1].end()] if d>c else ''
        if kind == 'insert':
            replacement += '\n' if start == 0 or (d and new[d-1][0] in {';', '}'}) else ' '
        changes.append((start,stop,replacement))
    candidate=edit(layout,changes)
    # A splice boundary must not merge tokens or swallow code into a // comment.
    if [m[0] for m in spans(candidate)] != [m[0] for m in new]:
        return None
    return candidate if candidate != after else None


def offer(state, source, header=''):
    """Keep a tiny reservoir per token sequence, independent of numeric score."""
    if not safe(source,header):
        return
    pool=state.setdefault('layout_frontiers',[])
    if source not in pool:
        pool.append(source)
    if len(pool)>8:del pool[4:-4]  # protect saved donors while rotating measured alternatives


def followups(state, before, after, family, header=''):
    if family not in STRUCTURAL:
        return []
    result=[]
    for donor in reversed(state.get('layout_frontiers',[])):
        if donor == before:
            continue
        candidate=transplant(before,after,donor,header) or project(after,donor,header)
        if candidate and candidate not in [text for _,text in result]:
            result.append((FAMILY,candidate))
        if len(result)>=LIMIT:
            break
    return result


def saved(net, proj, fn, source, header=''):
    """Read at most 32 private attempts; old scores only choose candidates, never verdicts."""
    result=[]
    for row in net.project_store(proj['id']).all(
            'SELECT source FROM attempts WHERE function_id=? AND score IS NOT NULL ORDER BY id DESC LIMIT 32', fn['id']):
        candidate=row['source']
        if candidate and candidate != source and project(source,candidate,header) and candidate not in result:
            result.append(candidate)
        if len(result)>=LIMIT:
            break
    return result
