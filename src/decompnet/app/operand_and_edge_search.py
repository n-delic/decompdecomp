"""Diff gates for computed operands, loop edges and verified object stores."""

import re
from collections import Counter

from decomprepair import loop_edge_repairs, operand_lifetime_repairs, packed_layout_repairs

from .measured_mips_repairs import (
    ADD,
    BRANCH,
    CALL,
    MEMORY,
    NUM,
    REG,
    SYMBOL,
    TRANSFER,
    _indexed_fields,
    _move,
    _sext16,
    _value,
    _written,
    labelled_code,
)

FPR = r'\$(?:f\d+|f[astv]\d+)'
FP = re.compile(rf'(?:add|sub|mul|div)\.([sd])\s+({FPR}),\s*({FPR}),\s*({FPR})')
LIKELY = re.compile(r'b(?:eq|ne)(?:z)?l\s')
WORD_STORES = {'sb':1,'sh':2,'sw':4}


def _call_slots(code):
    """Track two computed o32 single-precision arguments at a named call."""
    result = {}
    duplicates = set()
    for call_at,(_,instruction) in enumerate(code):
        call = CALL.fullmatch(instruction)
        if not call:
            continue
        callee = call[1]
        if callee in result:
            duplicates.add(callee)
        region = []
        for item in reversed(code[max(0,call_at-28):call_at]):
            if TRANSFER.match(item[1]):
                break
            region.append(item)
        region.reverse()
        definitions = {}
        arguments = {}
        for position,(changed,line) in enumerate(region+code[call_at+1:call_at+2]):
            if store := MEMORY.fullmatch(line):
                offset = int(store['offset'] or '0',0)
                if store['base']=='$sp' and 16<=offset<=128 and offset%4==0:
                    index = offset//4
                    argument = definitions.get(store['reg']) if store['op']=='swc1' else None
                    if argument:
                        arguments[index] = (argument[0],argument[1] or changed)
                    elif store['op'] in WORD_STORES or store['op']=='swc1':
                        arguments.pop(index,None)
            if (definition := FP.fullmatch(line)) and definition[1]=='s':
                definitions[definition[2]] = (position,changed)
            elif copy := re.fullmatch(rf'mov\.s\s+({FPR}),\s*({FPR})',line):
                if copy[2] in definitions:
                    pos,flag = definitions[copy[2]]
                    definitions[copy[1]] = (pos,flag or changed)
                else:
                    definitions.pop(copy[1],None)
            elif destination := re.match(rf'(?:lwc1|mtc1|cvt\.s\.\w+)\s+({FPR}),',line):
                definitions.pop(destination[1],None)
            elif transfer := re.fullmatch(rf'mtc1\s+{REG},\s*({FPR})',line):
                definitions.pop(transfer[1],None)
        for index,regs in enumerate((('$f12','$fa0'),('$f14','$fa1'))):
            if slot := next((definitions[r] for r in regs if r in definitions),None):
                arguments[index] = slot
        result[callee] = arguments
    return {name:args for name,args in result.items() if name not in duplicates}


def _float_argument_sites(target, candidate):
    left,right = _call_slots(target),_call_slots(candidate)
    output = set()
    for callee, slots in left.items():
        before = right.get(callee,{})
        indexes = sorted(slots.keys() & before.keys())
        pairs = []
        for i,index in enumerate(indexes):
            for other in indexes[i+1:]:
                a,b = slots[index],slots[other]
                c,d = before[index],before[other]
                if (a[0]-b[0])*(c[0]-d[0])<0 and any(flag for _,flag in (a,b,c,d)):
                    pairs.append((index,other))
        if len(pairs)==1:
            output.add((callee,pairs[0]))
    return output


def _square_chains(code):
    for i,(changed,text) in enumerate(code):
        div = FP.fullmatch(text)
        if not div or not text.startswith('div.'):
            continue
        for flag,line in code[i+1:i+5]:
            square = FP.fullmatch(line)
            if square and line.startswith('mul.') and square[1]==div[1] and square[3]==square[4]==div[2]:
                yield div[1], div[2]==div[3]==square[2], changed or flag
                break
            if TRANSFER.match(line):
                break


def _indexed_store_strides(target, candidate):
    target_groups,changed = _indexed_fields(target,WORD_STORES)
    candidate_groups,changed_candidate = _indexed_fields(candidate,WORD_STORES)
    return {key[1] for key,fields in target_groups.items() if key in changed and len(fields)>=3 and any(
        other[1]==key[1] and other in changed_candidate and len(fields.items() & matched.items())>=3
        for other,matched in candidate_groups.items())}


def _saves_before_branch(code):
    for i,(branch_changed,branch) in enumerate(code):
        if not BRANCH.match(branch):
            continue
        before = code[max(0,i-10):i]
        saves = {m['reg'] for _,line in before if (m:=MEMORY.fullmatch(line)) and m['op']=='sw' and m['base']=='$sp'}
        for changed,text in before:
            if (value:=_value(text)) and value[1]==0 and value[0] in saves and (changed or branch_changed):
                yield value[0]


def _guard_steps(target, candidate):
    early = set(_saves_before_branch(candidate))
    for i,(branch_changed,branch) in enumerate(target[:-2]):
        if not BRANCH.match(branch):
            continue
        delay_changed,delay = target[i+1]
        save = MEMORY.fullmatch(delay)
        if not save or save['op']!='sw' or save['base']!='$sp' or not re.fullmatch(r'\$s[0-7]',save['reg']):
            continue
        register = save['reg']
        after = target[i+2:i+7]
        if register not in early or not any(_value(text)==(register,0) for _,text in after):
            continue
        if not (branch_changed or delay_changed or any(changed for changed,_ in after)):
            continue
        for _,text in target[i+2:i+65]:
            add = ADD.fullmatch(text)
            if add and add[1]==add[2]==register and 1<=_sext16(add[3])<=4096:
                yield _sext16(add[3])


def _retry_stride(target, candidate, code, labels):
    moved = {value[0] for changed,line in candidate if changed and (value:=_value(line)) and value[1]==0}
    branch_changed = {line for changed,line in target if changed and BRANCH.match(line)}
    target_zeros = {value[0] for changed,line in target if changed and (value:=_value(line)) and value[1]==0}
    if not moved or not (branch_changed or target_zeros):
        return set()
    steps = set()
    for i,branch in enumerate(code[:-1]):
        match = re.fullmatch(rf'bne\s+({REG}),\s*{REG},\s*(\.\w+)',branch)
        if not match or _value(code[i+1])!=(match[1],0):
            continue
        if branch not in branch_changed and match[1] not in target_zeros:
            continue
        entry = labels.get(match[2],i)
        if not i-30<=entry<i or not (reset:=_value(code[entry])) or reset[1]!=0 or reset[0]==match[1] or not moved & {match[1],reset[0]}:
            continue
        if not any(text.startswith('lbu ') for text in code[entry:i]):
            continue
        if not any((m:=ADD.fullmatch(text)) and m[1]==m[2]==match[1] and _sext16(m[3])==1 for text in code[entry:i]):
            continue
        for text in code[entry:i]:
            if (m:=ADD.fullmatch(text)) and m[1]==m[2]==reset[0] and 1<_sext16(m[3])<=32767:
                steps.add(_sext16(m[3]))
    return steps


def _ordinary_byte_loop(target, candidate, code, labels):
    reads = Counter((m['base'],int(m['offset'] or '0',0)) for _,line in candidate
                    if (m:=MEMORY.fullmatch(line)) and m['op']=='lbu')
    candidate_bytes = {offset for (_,offset),count in reads.items() if count>=2}
    if not any(changed and (LIKELY.match(line) or ADD.fullmatch(line)) for changed,line in target):
        return False
    if not any(changed and BRANCH.match(line) for changed,line in candidate):
        return False
    for i,branch in enumerate(code[:-1]):
        if not LIKELY.match(branch):
            continue
        dest = labels.get(branch.split(',')[-1].strip(),i)
        add = ADD.fullmatch(code[i+1])
        if not i-22<=dest<i or not add or add[1]!=add[2] or _sext16(add[3])!=1:
            continue
        related = {(m['base'],int(m['offset'] or '0',0)) for line in code[dest:i] if (m:=MEMORY.fullmatch(line)) and m['op']=='lbu'}
        initial = {(m['base'],int(m['offset'] or '0',0)) for line in code[max(0,dest-12):dest] if (m:=MEMORY.fullmatch(line)) and m['op']=='lbu'}
        if any(offset in candidate_bytes for _,offset in related & initial):
            return True
    return False


def _clamp_bounds(target, candidate, code, labels):
    if not any(diff and LIKELY.match(line) for diff,line in target) or not any(
        diff and BRANCH.match(line) for diff,line in candidate
    ):
        return set()
    bounds = set()
    compare = re.compile(rf'sltiu?\s+({REG}),\s*({REG}),\s*({NUM})')
    for i in range(len(code)-8):
        a,b = compare.fullmatch(code[i]),compare.fullmatch(code[i+3])
        if not a or not b or a[2]!=b[2]:
            continue
        first,second = code[i+1],code[i+4]
        if not first.startswith(('bnel ','bnezl ')) or not second.startswith(('beql ','beqzl ')):
            continue
        if a[1] not in re.findall(REG,first) or b[1] not in re.findall(REG,second):
            continue
        join = labels.get(first.split(',')[-1].strip())
        if join is None or join!=labels.get(second.split(',')[-1].strip()) or not i+6<join<len(code):
            continue
        low,high = int(a[3],0),int(b[3],0)
        x,y = _value(code[i+2]),_value(code[i+5])
        if not x or not y or x[0]!=y[0] or (x[1],y[1])!=(low,high-1):
            continue
        if (code[i+6].startswith('j ') and labels.get(code[i+6][2:],-1)>join and
                re.fullmatch(rf'andi\s+{REG},\s*{re.escape(x[0])},\s*(?:0[xX][fF]{{4}}|65535)',code[join])):
            bounds.add((low,high))
    return bounds


def _decoder_sites(code):
    for i,(flag,line) in enumerate(code):
        load = MEMORY.fullmatch(line)
        if not load or load['op']!='lbu' or int(load['offset'] or '0',0)!=1:
            continue
        reg = load['reg']
        window = code[i+1:i+19]
        for j,(changed,operation) in enumerate(window):
            if not operation.startswith('or '):
                continue
            regs = re.findall(REG,operation)
            if len(regs)!=3 or reg not in regs[1:]:
                continue
            if any(text.startswith('sll ') and text.rsplit(',',1)[1].strip() in ('8','0x8') for _,text in window[:j]):
                tail = window[j+1:j+4]
                signed = next((re.findall(REG,t) for _,t in tail if t.startswith('sra ')),None)
                yield (signed[0] if signed else regs[0])==reg, flag or changed or any(f for f,_ in tail)
                break


def _wide_zero_objects(target, candidate):
    relocation = re.compile(rf'sw\s+({REG}),\s*%lo\(({SYMBOL})(?P<add>\+(?:0[xX]0*4|4))?\)\({REG}\)')
    found = {}
    zeros = {}
    for i,(changed,line) in enumerate(target):
        if value := _value(line):
            if value[1]==0 and value[0]!='$zero':
                zeros[value[0]]=i
        if TRANSFER.match(line):
            zeros.clear()
            continue
        if match:=relocation.fullmatch(line):
            if i-8<=zeros.get(match[1],-9)<i:
                found.setdefault(match[2],{})[bool(match['add'])] = (match[1],changed,i)
        elif (dest:=_written(line)) and _value(line)!=(dest,0):
            zeros.pop(dest,None)
    plain = {}
    for changed,line in candidate:
        if (match:=relocation.fullmatch(line)) and match[1]=='$zero':
            plain.setdefault(match[2],{})[bool(match['add'])]=changed
    return {symbol for symbol,pair in found.items() if set(pair)=={False,True}
            and pair[False][0]!=pair[True][0] and set(plain.get(symbol,{}))=={False,True}
            and abs(pair[False][2]-pair[True][2])<=6
            and (any(flag for _,flag,_ in pair.values()) or any(plain[symbol].values()))}


def _allocation_guards(code):
    for i,(changed,branch) in enumerate(code[:-2]):
        test = re.fullmatch(rf'b(?:eq|ne)z\s+({REG}),\s*\S+',branch)
        if not test:
            continue
        if test[1]!='$v0' and not any(_move(text)==(test[1],'$v0') for _,text in code[max(0,i-2):i]):
            continue
        callee = next((call[1] for _,line in reversed(code[max(0,i-5):i]) if (call:=CALL.fullmatch(line))),None)
        if callee:
            yield callee, i, changed or code[i+1][0], _value(code[i+1][1])


def _guard_constants(target, candidate):
    candidate_sites = {}
    for callee,_,changed,value in _allocation_guards(candidate):
        candidate_sites.setdefault(callee,[]).append((changed,value))
    results = []
    for callee,i,changed,value in _allocation_guards(target):
        if not value or not 0<=value[1]<=255 or not target[i][1].startswith('beqz '):
            continue
        old = candidate_sites.get(callee,())
        if len(old)!=1 or not (changed or old[0][0]) or old[0][1] and old[0][1][1]==value[1]:
            continue
        register,constant = value
        offsets = set()
        for _,line in target[i+2:i+15]:
            if TRANSFER.match(line):
                break
            if (store:=MEMORY.fullmatch(line)) and store['op']=='sb' and store['reg']==register and store['base']!='$sp':
                offsets.add(int(store['offset'] or '0',0))
        if len(offsets)>=2:
            results.append((constant,offsets))
    return results[:4]


def proposals(source,name,header,asm,target,candidate,flags,resolve=None):
    out = []
    flags = flags.split() if isinstance(flags,str) else flags
    if ('-mgp64' not in flags and any(diff and (FP.fullmatch(line) or line.startswith(('swc1 ','mov.s ')))
                                      for diff,line in target+candidate)
            and (sites := _float_argument_sites(target,candidate))):
        out += operand_lifetime_repairs.float_argument_order(source,name,header,sites)
    widths = {width for width,in_place,changed in _square_chains(target) if in_place and changed}
    widths &= {width for width,in_place,_ in _square_chains(candidate) if not in_place}
    if widths:
        out += operand_lifetime_repairs.divide_square(source,name,header,widths)
    if (any(diff and line.startswith(('sw ','sh ','sb ')) and '%lo(' in line for diff,line in target)
            and (strides := _indexed_store_strides(target,candidate))):
        out += operand_lifetime_repairs.final_offset(source,name,header,strides)
    if steps := set(_guard_steps(target,candidate)):
        out += loop_edge_repairs.sink_entry_initializer(source,name,header,steps)
    if asm and any(diff and BRANCH.match(line) for diff,line in target+candidate):
        instructions, labels = labelled_code(asm)
        code = [f'{op} {args}'.strip() for op,args in instructions]
        if steps := _retry_stride(target,candidate,code,labels):
            out += loop_edge_repairs.reset_retry_counter(source,name,header,steps)
        if _ordinary_byte_loop(target,candidate,code,labels):
            out += loop_edge_repairs.ordinary_backedge_read(source,name,header)
        if bounds := _clamp_bounds(target,candidate,code,labels):
            out += loop_edge_repairs.shared_remask(source,name,header,bounds)
    shapes = list(_decoder_sites(target))
    if len(shapes)==2 and all(same for same,_ in shapes) and any(changed for _,changed in shapes) and any(
        not same for same,_ in _decoder_sites(candidate)
    ):
        out += packed_layout_repairs.paired_decoders(source,name,header)
    if resolve is not None and '-mgp32' in flags:
        symbols = {symbol for symbol in _wide_zero_objects(target,candidate)
                   if (address:=resolve(symbol)) is not None and address%8==0}
        if symbols:
            out += packed_layout_repairs.aligned_clear(source,name,header,symbols)
    if evidence := _guard_constants(target,candidate):
        out += loop_edge_repairs.null_guard_constant(source,name,header,evidence)
    return out
