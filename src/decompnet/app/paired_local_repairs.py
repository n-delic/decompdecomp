"""Bounded combinations of independently measured local edits; only the normal judge accepts C."""
import difflib
import hashlib
import re

from . import allocation_evidence, macro_repairs
from .repair_tokens import tokens

MAX_TRIALS = 4
PER_PARENT = 2


def edit(before, after, header=''):
    if len(before)>128000 or len(after)>128000 or re.search(r'\b(?:asm|__asm__|volatile)\b',before+after):return None
    masked,protected,_=macro_repairs.prepare(before,header)
    if masked is None or macro_repairs.prepare(after,header)[0] is None:return None
    directives=lambda text:re.findall(r'(?m)^[ \t]*#[^\n]*(?:\n|$)',macro_repairs._blank(text))
    if directives(before)!=directives(after):return None
    left,right=tokens(before),tokens(after)
    if max(len(left),len(right))>8192:return None
    changes=[p for p in difflib.SequenceMatcher(None,left,right,autojunk=False).get_opcodes() if p[0]!='equal']
    if not 1<=len(changes)<=3:return None
    edits=[];positions=[0]
    for token in left:positions.append(positions[-1]+len(token))
    literals=lambda seq:[t for t in seq if t[:1].isdigit() or t.startswith(('"',"'"))]
    for _,a,b,c,d in changes:
        if max(b-a,d-c)>24 or (a==b and c==d):return None
        if literals(left[a:b])!=literals(right[c:d]):return None
        edits.append((a,b,right[c:d]))
    if edits[-1][1]-edits[0][0]>96:return None
    start,end=positions[edits[0][0]],positions[edits[-1][1]]
    if any(start<stop and end>begin or start==end and begin<=start<=stop for begin,stop in protected):return None
    return edits


def combine(base, first, second, header=''):
    """Merge two small disjoint edits in the same local token window, preserving exact layout."""
    a,b=edit(base,first,header),edit(base,second,header)
    if a is None or b is None:return None
    edits=sorted(a+b,key=lambda v:v[0])
    if any(left[1]>=right[0] for left,right in zip(edits,edits[1:])) or edits[-1][1]-edits[0][0]>96:return None
    original=tokens(base);positions=[0]
    for token in original:positions.append(positions[-1]+len(token))
    _,protected,_=macro_repairs.prepare(base,header)
    start,end=positions[edits[0][0]],positions[edits[-1][1]]
    if any(start<stop and end>begin or start==end and begin<=start<=stop for begin,stop in protected):return None
    pieces=[];cursor=0
    for start,end,replacement in edits:
        pieces+=original[cursor:start]+replacement;cursor=end
    return ''.join(pieces+original[cursor:])


def offer(state, node, report, arch, header=''):
    """At most four pair trials per persisted frontier, two per parent; ordinary budgets still apply."""
    if state.get('paired_search',True) is False or state.get('paired_trials',0)>=MAX_TRIALS:return
    base=node.get('parent_source');path=node.get('path') or []
    score=report.get('score');ceiling=node.get('ceiling',0)
    if arch!='mips' or not base or node.get('depth')!=1 or len(path)!=1 or not isinstance(score,(int,float)) or not 0<ceiling<=score<=ceiling*2:return
    before=allocation_evidence.diagnose(node.get('baseline_rows'),arch)
    after=allocation_evidence.diagnose(report.get('rows'),arch)
    if (not (before['register_only'] or before['scheduling'] or before['allocation_schedule'])
            or before['target_instructions']!=after['target_instructions']
            or before['candidate_instructions']!=after['candidate_instructions']
            or before['target_frame']!=after['candidate_frame']
            or edit(base,node['source'],header) is None):return
    key=hashlib.sha256(base.encode()).hexdigest()
    parents=state.setdefault('paired_parents',{})
    if key not in parents and len(parents)>=4:return
    pool=parents.setdefault(key,dict(candidates=[],trials=0))
    inert=sum(p.get('trials',0) for p in state.get('inert_pair_parents',{}).values() if p.get('base_key')==key)
    if pool['trials']+inert>=PER_PARENT:return
    for prior in pool['candidates']:
        if prior['family']==path[-1]:continue
        source=combine(base,prior['source'],node['source'],header)
        if source is None or source in state['seen']:continue
        state['seen'].append(source)
        state['pending'].insert(0,dict(source=source,depth=node['depth']+1,ceiling=ceiling,
            path=[prior['family'],path[-1],'paired_local'],parent_source=base,
            baseline_rows=node.get('baseline_rows'),region_baseline=node.get('region_baseline')))
        pool['trials']+=1;state['paired_trials']=state.get('paired_trials',0)+1
        if pool['trials']+inert>=PER_PARENT or state['paired_trials']>=MAX_TRIALS:break
    if len(pool['candidates'])<6 and not any(p['source']==node['source'] for p in pool['candidates']):
        pool['candidates'].append(dict(family=path[-1],source=node['source']))


def coverage(source, header=''):
    """Explain macro eligibility without exposing source or claiming every edit is supported."""
    masked,protected,note=macro_repairs.prepare(source,header)
    return dict(supported=masked is not None,protected_regions=len(protected),note=note,
                scope='Unaffected local windows only; directives, dependencies and crossings stay opaque.')
