"""Read a uniform MIPS frame-size difference from scored instruction pairs; no match verdicts."""
import re


def frame_growth(rows):
    """A small positive frame delta with unchanged saved-register instructions, or no evidence."""
    if not rows:
        return None
    changed = [row for row in rows if row[0] != ' ']
    if len(changed) < 4 or any(row[0] != 's' for row in changed):
        return None
    frame = re.compile(r'addiu\s+\$sp,\s*\$sp,\s*(-?0x[0-9a-fA-F]+|-?\d+)$')
    ends = []
    for _, target, candidate in changed:
        a, b = frame.fullmatch(target), frame.fullmatch(candidate)
        if a and b:
            ends.append((int(a[1], 0), int(b[1], 0)))
    if len(ends) != 2 or ends[0][0] >= 0 or ends[0][1] >= 0 or ends[1] != (-ends[0][0], -ends[0][1]):
        return None
    delta = abs(ends[0][0])-abs(ends[0][1])
    if delta not in {8, 16, 24, 32}:
        return None
    # Saved-register loads/stores must all show the same shift, without changing registers/opcodes.
    slot = re.compile(r'((?:sw|lw|sdc1|ldc1)\s+\$\w+,\s*)(0x[0-9a-fA-F]+|\d+)\(\$sp\)$')
    for _, target, candidate in changed:
        if frame.fullmatch(target) and frame.fullmatch(candidate):
            continue
        a, b = slot.fullmatch(target), slot.fullmatch(candidate)
        if not a or not b or a[1] != b[1] or int(a[2], 0)-int(b[2], 0) != delta:
            return None
    return delta


def layout(rows):
    """Separate frame, prologue/epilogue saves and other stack operands; never infer object sizes."""
    frame = re.compile(r'addiu\s+\$sp,\s*\$sp,\s*(-?0x[0-9a-fA-F]+|-?\d+)$')
    slot = re.compile(r'(\w+)\s+(\$\w+),\s*(-?0x[0-9a-fA-F]+|-?\d+)\(\$sp\)$')
    target_frame = candidate_frame = None
    saves, locals_ = [], []
    target_count = sum(bool(row[1]) for row in rows)
    index = 0
    for _, target, candidate in rows:
        a, b = frame.fullmatch(target), frame.fullmatch(candidate)
        if a and b and int(a[1], 0) < 0 and int(b[1], 0) < 0:
            target_frame, candidate_frame = -int(a[1], 0), -int(b[1], 0)
        a, b = slot.fullmatch(target), slot.fullmatch(candidate)
        if a and b and (a[1], a[2]) == (b[1], b[2]):
            delta = int(a[3], 0)-int(b[3], 0)
            saved = a[2] in {'$ra', '$fp', '$s8', '$gp', *('$s'+str(n) for n in range(8))}
            record = {'register': a[2], 'target': int(a[3], 0), 'candidate': int(b[3], 0), 'delta': delta}
            (saves if saved and (index < 32 or index >= target_count-32) else locals_).append(record)
        index += bool(target)
    return {'target_frame': target_frame, 'candidate_frame': candidate_frame,
            'frame_delta': target_frame-candidate_frame if target_frame is not None and candidate_frame is not None else None,
            'saved_slots': saves, 'local_operands': locals_,
            'saved_deltas': sorted({r['delta'] for r in saves}), 'local_deltas': sorted({r['delta'] for r in locals_})}


def pointer_swap(rows):
    """A coherent two-register exchange present in memory bases, not a general allocation verdict."""
    if not rows or any(not isinstance(r,(tuple,list)) or len(r)!=3 or not all(isinstance(v,str) for v in r) for r in rows):return False
    changed=[r for r in rows if r[0]!=' ']
    if not changed or any(r[0]!='r' for r in changed):return False
    register=re.compile(r'\$\w+');mapping={};reverse={};bases=set()
    for _,left,right in changed:
        if '$sp' in left+right or register.sub('$reg',left)!=register.sub('$reg',right):return False
        for a,b in zip(register.findall(left),register.findall(right)):
            if mapping.get(a,b)!=b or reverse.get(b,a)!=a:return False
            mapping[a]=b;reverse[b]=a
        bases.update(re.findall(r'\((\$\w+)\)',left+' '+right))
    swap={a:b for a,b in mapping.items() if a!=b}
    return len(swap)==2 and all(swap.get(b)==a for a,b in swap.items()) and set(swap)<=bases


def repair_layout(rows):
    """Preserve a strict frame proof while separately recognizing one non-stack pointer swap."""
    if not rows or any(not isinstance(r,(tuple,list)) or len(r)!=3 or not all(isinstance(v,str) for v in r) for r in rows):return {}
    result=layout(rows)
    register_rows=[r for r in rows if r[0]=='r']
    swap=pointer_swap([r for r in rows if r[0]!='s'])
    delta=frame_growth([r for r in rows if r[0]!='r']) if not register_rows or swap else None
    return dict(result,inferred_growth=delta,pointer_swap=swap,frame_only_gap=frame_only_gap(rows))


def frame_only_gap(rows):
    """Uniform save-area growth with equal body/local accesses; displayed advice, not a verdict."""
    if not rows or len(rows)>16384 or any(not isinstance(r,(list,tuple)) or len(r)!=3
            or not all(isinstance(s,str) for s in r) for r in rows):return None
    info=layout(rows)
    gap=info['frame_delta']
    if gap is None or not 0<gap<=64 or gap%8 or info['local_deltas']!=[0]:return None
    if sum(bool(r[1]) for r in rows)!=sum(bool(r[2]) for r in rows):return None
    frame=re.compile(r'addiu\s+\$sp,\s*\$sp,\s*(-?0x[\da-fA-F]+|-?\d+)$')
    slot=re.compile(r'(sw|lw)\s+(\$(?:ra|fp|s[0-8])),\s*(0x[\da-fA-F]+|\d+)\(\$sp\)$')
    frames=[];stores=[];loads=[];index=0
    count=sum(bool(r[1]) for r in rows)
    for marker,left,right in rows:
        position=index;index+=bool(left)
        if marker==' ':continue
        if marker!='s':return None
        a,b=frame.fullmatch(left),frame.fullmatch(right)
        if (a and b and abs(int(a[1],0))==info['target_frame'] and abs(int(b[1],0))==info['candidate_frame']
                and int(a[1],0)*int(b[1],0)>0):
            frames.append(-1 if int(a[1],0)<0 else 1);continue
        a,b=slot.fullmatch(left),slot.fullmatch(right)
        if not a or not b or (a[1],a[2])!=(b[1],b[2]) or int(a[3],0)-int(b[3],0)!=gap:return None
        if not (position<32 if a[1]=='sw' else position>=count-32):return None
        if not info['target_frame']-64<=int(a[3],0)<info['target_frame']:return None
        (stores if a[1]=='sw' else loads).append((a[2],int(a[3],0),int(b[3],0)))
    return gap if sorted(frames)==[-1,1] and stores and sorted(stores)==sorted(loads) else None
