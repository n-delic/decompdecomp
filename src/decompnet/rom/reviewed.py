"""Explicitly reviewed extensions into raw binary; never automatic discovery or target editing."""
import copy


def restore_cut_banks(config, *, restored=None):
    """Rejoin redundant fragments that cut a scoped bank before its own children.

    Old split configurations can contain top-level discoveries inside a code bank,
    truncating that bank even though its original children continue afterwards.
    Only discard plain fragments with the same mapping and compatible scope. This
    operates on the export copy; the project file itself stays untouched.
    """
    result = copy.deepcopy(config)
    segments = result.get('segments', [])
    index = 0
    while index < len(segments):
        bank = segments[index]
        if not isinstance(bank, dict) or bank.get('type') != 'code' or not bank.get('exclusive_ram_id'):
            index += 1
            continue
        children = bank.get('subsegments') or []
        starts = [child[0] for child in children if isinstance(child, list) and child and isinstance(child[0], int)]
        if not starts or index + 1 >= len(segments):
            index += 1
            continue
        def start(segment):
            return segment[0] if isinstance(segment, list) and segment else segment.get('start') if isinstance(segment, dict) else None
        next_start = start(segments[index + 1])
        if not isinstance(next_start, int) or max(starts) < next_start:
            index += 1
            continue
        end_index = next((i for i in range(index + 1, len(segments))
                          if isinstance(start(segments[i]), int) and start(segments[i]) > max(starts)), None)
        if end_index is None:
            index += 1
            continue
        fragments = segments[index + 1:end_index]
        delta = bank['vram'] - bank['start']
        def redundant(fragment, delta=delta, scope=bank['exclusive_ram_id']):
            if not isinstance(fragment, dict) or fragment.get('type') not in ('bin', 'code'):
                return False
            if fragment['type'] == 'bin':
                return set(fragment) <= {'type', 'start', 'name'}
            subs = fragment.get('subsegments') or []
            return (fragment.get('vram', 0) - fragment['start'] == delta
                    and fragment.get('exclusive_ram_id', scope) == scope
                    and set(fragment) <= {'type', 'start', 'name', 'vram', 'subsegments', 'exclusive_ram_id',
                                           'symbol_name_format', 'symbol_name_format_no_rom'}
                    and len(subs) == 1 and isinstance(subs[0], list) and len(subs[0]) >= 2
                    and subs[0][0] == fragment['start'] and subs[0][1] == 'c')
        if fragments and all(redundant(fragment) for fragment in fragments):
            if restored is not None:
                restored.append((bank['start'], next_start, start(segments[end_index])))
            del segments[index + 1:end_index]
        index += 1
    return result


def restore_matched_entries(config, functions, restored):
    """Keep saved entries visible in the portion of a rejoined bank Splat missed."""
    for bank_start, tail_start, bank_end in restored:
        bank = next(s for s in config['segments'] if isinstance(s, dict) and s.get('start') == bank_start)
        subs = bank.get('subsegments', [])
        originals = [list(s) for s in subs if isinstance(s, list) and len(s) >= 2 and isinstance(s[0], int)]
        for fn in functions:
            row = dict(fn)
            lo, size, vram = row.get('rom_offset'), row.get('size'), row.get('vram')
            if (row.get('status') != 'matched' or type(lo) is not int or type(size) is not int
                    or type(vram) is not int or size <= 0 or not tail_start <= lo < lo + size <= bank_end
                    or bank['vram'] + lo - bank_start != vram):
                continue
            hi = lo + size
            current = [s for s in subs if isinstance(s, list) and len(s) >= 2 and isinstance(s[0], int)]
            source = max((s for s in current if s[0] <= lo), key=lambda s: s[0], default=None)
            original = max((s for s in originals if s[0] <= lo), key=lambda s: s[0], default=None)
            if (source is None or original is None or source[1] not in ('c', 'bin', 'hasm')
                    or any(lo < s[0] < hi and s[1] != source[1] for s in current)):
                continue
            parent = max((s for s in originals if s[0] <= lo and s[1] == 'c' and len(s) > 2),
                         key=lambda s: s[0], default=None)
            for at in (lo, hi):
                if at == bank_end:
                    continue
                previous = next((s for s in current if s[0] == at), None)
                if previous is not None and not (at == lo and previous[1] == 'bin'):
                    continue
                prefix = str(parent[2] if parent else bank.get('name', 'code'))
                directory, _, basename = prefix.rpartition('/')
                stem = f'{basename[:180]}_{at:06X}'
                name = f'{directory}/{stem}' if directory else stem
                used = {s[2] for s in subs if isinstance(s, list) and len(s) > 2}
                suffix = 1
                while name in used:
                    name = f'{directory}/{stem}_{suffix}' if directory else f'{stem}_{suffix}'
                    suffix += 1
                kind = 'c' if at == lo else original[1]
                if previous is None:
                    subs.append([at, kind, name])
                else:
                    previous[1:] = [kind, name]
            def offset(sub, bank_start=bank_start, bank=bank):
                if isinstance(sub, list) and sub:
                    return sub[0]
                if isinstance(sub, dict):
                    if isinstance(sub.get('start'), int):
                        return sub['start']
                    if isinstance(sub.get('vram'), int):
                        return bank_start + sub['vram'] - bank['vram']
                return float('inf')
            subs.sort(key=offset)


def extend_config(config, functions):
    """Extend only a code parent's immediate raw tail, stopping before the next parent.

    No data/rodata child is consumed and no independent code mapping is crossed.
    The caller must already have validated original bytes and reviewed the extent.
    """
    from .export import ExportError, _segments
    result=copy.deepcopy(config)
    for fn in functions:
        parents=_segments(result)
        rom = dict(fn).get('rom_offset')
        owner=next((s for s in parents if s.get('type')=='code' and 'vram' in s and s['end'] is not None
                    and s['vram']<=fn['vram']<s['vram']+s['end']-s['start']
                    and (rom is None or rom == s['start'] + fn['vram'] - s['vram'])),None)
        if owner is None:
            name = dict(fn).get('name') or f"function at 0x{fn['vram']:X}"
            where = f' at ROM offset 0x{rom:X}' if isinstance(rom, int) else ''
            raise ExportError(f'{name}{where} has no matching code segment. Review its ROM-to-memory mapping.')
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
