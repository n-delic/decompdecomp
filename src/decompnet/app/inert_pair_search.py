"""A few pairs of fresh, individually inert C edits; the normal judge verifies their interaction."""
import hashlib

from . import paired_local_repairs

MAX_TRIALS = 4
PER_PARENT = 2


def offer(state, base, source, family, score, baseline, parent_output, output, *, header='', path=()):
    if (len(path)>1 or state.get('paired_search',True) is False or parent_output is None or output is None or output!=parent_output or score is None
            or baseline is None or not 0<score==baseline or state.get('paired_trials',0)>=MAX_TRIALS
            or paired_local_repairs.edit(base,source,header) is None):
        return []
    pools = state.setdefault('inert_pair_parents',{})
    key = hashlib.sha256((base+repr(parent_output)).encode()).hexdigest()
    if key not in pools and len(pools)>=4:return []
    base_key=hashlib.sha256(base.encode()).hexdigest()
    pool = pools.setdefault(key,dict(candidates=[],trials=0,base_key=base_key))
    legacy=state.get('paired_parents',{}).get(base_key,{}).get('trials',0)
    choices=[]
    for prior in pool['candidates']:
        if prior['family']==family or pool['trials']+legacy>=PER_PARENT or state.get('paired_trials',0)>=MAX_TRIALS:continue
        combined=paired_local_repairs.combine(base,prior['source'],source,header)
        if combined is None or combined in state.get('seen',[]):continue
        seen=state.setdefault('seen',[])
        if isinstance(seen,set):seen.add(combined)
        else:seen.append(combined)
        choices.append(dict(source=combined,path=list(path)+[prior['family'],family,'paired_inert']))
        pool['trials']+=1;state['paired_trials']=state.get('paired_trials',0)+1
    if len(pool['candidates'])<8 and sum(c['family']==family for c in pool['candidates'])<2:
        pool['candidates'].append(dict(source=source,family=family))
    return choices
