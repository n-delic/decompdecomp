"""Explicitly reviewed extensions into raw binary; never automatic discovery or target editing."""
import copy


def extend_config(config, functions):
    """Extend only a code parent's immediate raw tail, stopping before the next parent.

    No data/rodata child is consumed and no independent code mapping is crossed.
    The caller must already have validated original bytes and reviewed the extent.
    """
    from .export import ExportError, _segments
    result=copy.deepcopy(config)
    for fn in functions:
        parents=_segments(result)
        owner=next((s for s in parents if s.get('type')=='code' and 'vram' in s and s['end'] is not None
                    and s['vram']<=fn['vram']<s['vram']+s['end']-s['start']),None)
        if owner is None:raise ExportError('The reviewed function has no existing code parent.')
        end=owner['start']+fn['vram']-owner['vram']+fn['size']
        if end<=owner['end']:continue
        raw=result['segments'];i=next(i for i,s in enumerate(raw) if isinstance(s,dict) and s.get('start')==owner['start'])
        if i+1>=len(raw):raise ExportError('No raw tail follows the reviewed code parent.')
        tail=raw[i+1];next_end=parents[next(j for j,s in enumerate(parents) if s['start']==owner['start'])+1]['end']
        if not isinstance(tail,dict) or tail.get('type')!='bin' or next_end is None or end>next_end:
            raise ExportError('The reviewed extent crosses another code/data parent; review it separately.')
        if end==next_end:raw.pop(i+1)
        else:tail['start']=end
    return result
