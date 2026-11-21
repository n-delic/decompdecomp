"""Select source shapes from measured MIPS instruction differences."""

import re
from collections import defaultdict

from decomprepair import measured_arithmetic_repairs as arithmetic
from decomprepair import measured_control_repairs as control
from decomprepair import measured_storage_repairs as storage
from decomprepair.target_call_constant_repairs import _code as labelled_code

from .allocation_search import floating_only

REG = r'\$[a-z0-9]+'
NUM = r'-?(?:0[xX][0-9a-fA-F]+|0|[1-9][0-9]*)'
SYMBOL = r'[A-Za-z_]\w*'
MEMORY = re.compile(rf'(?P<op>\w+)\s+(?P<reg>{REG}),\s*(?P<offset>{NUM})?\((?P<base>{REG})\)')
ADD = re.compile(rf'addiu\s+({REG}),\s*({REG}),\s*({NUM})')
BRANCH = re.compile(r'b(?:eq|ne|gez|gtz|lez|ltz)(?:z|l|zl)?\s')
TRANSFER = re.compile(r'(?:b\w*|j\w*)\s')
MOVE = re.compile(rf'(?:move\s+({REG}),\s*({REG})|addu\s+({REG}),\s*({REG}),\s*\$zero)')
CALL = re.compile(rf'jal\s+({SYMBOL})')
LOAD_WIDTH = {'lb':1,'lbu':1,'lh':2,'lhu':2,'lw':4,'lwc1':4}


def _value(line):
    if match := re.fullmatch(rf'(addiu|ori)\s+({REG}),\s*\$zero,\s*({NUM})',line):
        return match[2],_sext16(match[3]) if match[1]=='addiu' else int(match[3],0)
    if match := re.fullmatch(rf'li\s+({REG}),\s*({NUM})',line):
        return match[1],int(match[2],0)
    if (move := _move(line)) and move[1]=='$zero':
        return move[0],0
    return None


def _move(line):
    match = MOVE.fullmatch(line)
    return (match[1] or match[3], match[2] or match[4]) if match else None


def _sext16(number):
    value = int(number,0)
    return value-0x10000 if 0x8000<=value<=0xffff else value


def _call_init(code, *, before):
    for i, (_,text) in enumerate(code):
        call = CALL.fullmatch(text)
        if not call:
            continue
        region = reversed(code[max(0,i-8):i]) if before else iter(code[i+2:i+10])
        for changed,line in region:
            if TRANSFER.match(line):
                break
            if changed and (value := _value(line)):
                register, immediate = value
                yield call[1], immediate, register


def _selectors(target, candidate):
    target_after = {(callee,value) for callee,value,_ in _call_init(target,before=False)}
    # Delaying the initializer is useful when a saved register no longer has
    # to stay live over this call. Require the corresponding saved-slot loss.
    def saves(code):
        return {m['reg'] for _,line in code if (m := MEMORY.fullmatch(line))
                and m['op']=='sw' and m['base']=='$sp' and re.fullmatch(r'\$s[0-7]',m['reg'])}
    removed = saves(candidate)-saves(target)
    return {(callee,value) for callee,value,reg in _call_init(candidate,before=True)
            if reg in removed and (callee,value) in target_after}


def _field_first(target, candidate):
    if not any(changed and re.match(r'(?:xor|sltu|sltiu|and|or)\s',text) for changed,text in candidate):
        return set()
    offsets = set()
    for i,(changed,text) in enumerate(target):
        load = MEMORY.fullmatch(text)
        if not load or load['op'] not in LOAD_WIDTH or load['base']=='$sp':
            continue
        branch = next((j for j in range(i+1,min(i+5,len(target))) if TRANSFER.match(target[j][1])),None)
        if branch is None or not BRANCH.match(target[branch][1]) or load['reg'] not in re.findall(REG,target[branch][1]):
            continue
        later = next((item for item in target[branch+2:branch+8] if TRANSFER.match(item[1])),None)
        if (later and re.match(r'b(?:eq|ne)l?\s',later[1])
                and load['reg'] not in re.findall(REG,later[1]) and (changed or target[branch][0] or later[0])):
            offsets.add(int(load['offset'] or '0',0))
    return offsets


def _tail_bounds(target, candidate, asm):
    changed_terms = {(add[1],_sext16(add[3])) for diff,text in target if diff and (add := ADD.fullmatch(text))}
    candidate_terms = {_sext16(add[3]) for diff,text in candidate if diff and (add := ADD.fullmatch(text))}
    changed_moves = {move for diff,text in target if diff and (move := _move(text))}
    candidate_moves = [move for diff,text in candidate if diff and (move := _move(text))]
    if not asm or not (changed_terms or changed_moves):
        return set()
    instructions, labels = labelled_code(asm)
    code = [f'{op} {args}' for op,args in instructions]
    result = set()
    for i,(op,args) in enumerate(instructions):
        if not BRANCH.match(op+' '):
            continue
        fields = [arg.strip() for arg in args.split(',')]
        start = labels.get(fields[-1],i)
        if start >= i:
            continue
        tested = set(fields[:-1])
        if i and (cmp := re.fullmatch(rf'sltu?\s+({REG}),\s*({REG}),\s*({REG})',code[i-1])) and cmp[1] in tested:
            tested.update((cmp[2],cmp[3]))
        for j in range(max(start,i-4),i):
            add = ADD.fullmatch(code[j])
            if add and add[1] in tested and '$sp' not in add.groups():
                bound = _sext16(add[3])
                if (add[1],bound) in changed_terms and bound in candidate_terms:
                    result.add(bound)
        # KMC may hoist the adjusted bound and copy it just before the loop.
        # Follow only a changed copy feeding the backedge, with a visible
        # unchanged source register and an adjusted bound leading into it.
        for j in range(max(0,start-10),min(start+2,i)):
            move = _move(code[j])
            if not move or move not in changed_moves or move[0] not in tested or not any(
                    source==move[1] for _,source in candidate_moves):
                continue
            if any(_written(text)==move[0] for text in code[j+1:i]):
                continue
            for prior in reversed(code[max(0,j-18):j]):
                if _written(prior)!=move[1]:
                    continue
                if (add := ADD.fullmatch(prior)) and '$sp' not in add.groups():
                    result.add(_sext16(add[3]))
                break
    return result


def _written(text):
    opcode,_,rest = text.partition(' ')
    if opcode in {'sw','sh','sb','swc1','sdc1','mult','multu','div','divu'} or TRANSFER.match(text):
        return None
    dest = rest.split(',',1)[0].strip()
    return dest if re.fullmatch(REG,dest) else None


def _scaled_stores(code, *, early):
    for i,(shift_diff,shift) in enumerate(code):
        match = re.fullmatch(rf'sll\s+({REG}),\s*{REG},\s*({NUM})',shift)
        if not match or not 1 <= int(match[2],0) <= 5:
            continue
        window = code[i+1:i+6]
        adds = [(j,line) for j,(_,line) in enumerate(window) if line.startswith(('addu ','daddu '))
                and match[1] in re.findall(REG,line)[1:]]
        if not adds:
            continue
        j,line = adds[0]
        dest = re.findall(REG,line)[0]
        nearby = (window[j+1:] if early else list(reversed(code[max(0,i-4):i])))
        store = next((m for _,text in nearby if (m := MEMORY.fullmatch(text)) and m['op'] in {'sb','sh','sw'}),None)
        read = next((m for _,text in window[j+1:] if (m := MEMORY.fullmatch(text))
                     and m['op']=='lw' and m['base']==dest),None)
        if store and read and (shift_diff or any(flag for flag,_ in window)) and store['base']!=dest:
            yield 1 << int(match[2],0),store['op'],int(store['offset'] or '0',0)


def _guard_globals(target, candidate):
    result = set()
    for i,(changed,line) in enumerate(target):
        high = re.fullmatch(rf'lui\s+({REG}),\s*%hi\(({SYMBOL})\)',line)
        if not high:
            continue
        symbol = high[2]
        for j in range(i+1,min(i+5,len(target))):
            low = re.fullmatch(rf'addiu\s+({REG}),\s*{re.escape(high[1])},\s*%lo\({symbol}\)',target[j][1])
            if not low:
                continue
            load_pos = next((k for k in range(j+1,min(j+6,len(target))) if (load:=MEMORY.fullmatch(target[k][1]))
                            and load['op']=='lw' and load['base']==low[1] and not int(load['offset'] or '0',0)),None)
            if load_pos is None:
                continue
            reg = MEMORY.fullmatch(target[load_pos][1])['reg']
            compares = {reg}
            tested = False
            for k in range(load_pos+1,min(load_pos+8,len(target)-1)):
                flag,branch = target[k]
                store = MEMORY.fullmatch(target[k+1][1])
                operands = re.findall(REG,branch)
                if BRANCH.match(branch) and compares.intersection(operands):
                    tested = True
                if (re.match(r'b(?:eq|ne)(?:z)?l\s',branch) and tested
                        and store and store['op']=='sw' and store['base']==low[1] and not int(store['offset'] or '0',0)
                        and (changed or flag or target[j][0] or target[k+1][0])):
                    direct = [text for _,text in candidate if f'%lo({symbol})' in text]
                    if (any(t.startswith('lw ') for t in direct) and any(t.startswith('sw ') for t in direct)
                            and any(diff and f'%lo({symbol})' in text for diff,text in candidate)):
                        result.add(symbol)
                if _written(branch)==low[1] or re.match(r'j\w*\s',branch):
                    break
                if branch.startswith(('slt ','sltu ')) and compares.intersection(operands[1:]):
                    compares.add(operands[0])
    return result


def _copy_order(code, *, early):
    for i,(changed,line) in enumerate(code):
        tail = MEMORY.fullmatch(line)
        if not tail or tail['op']!='swc1' or tail['base']=='$sp':
            continue
        offset = int(tail['offset'] or '0',0)
        region = code[i+1:i+8] if early else code[max(0,i-7):i]
        words = [int(m['offset'] or '0',0) for _,text in region if (m:=MEMORY.fullmatch(text))
                 and m['op']=='sw' and m['base']==tail['base']]
        if not changed and not any(flag for flag,_ in region):
            continue
        triples = (words[j:j+3] for j in range(len(words)-2))
        if any(block==list(range(block[0],block[0]+12,4)) and 12<=offset-block[0]<=24 for block in triples):
            yield offset


def _changed_stack_offsets(rows):
    for changed,left,right in rows:
        if changed==' ':
            continue
        a,b = MEMORY.fullmatch(left),MEMORY.fullmatch(right)
        if a and b and a['op']==b['op'] and a['base']==b['base']=='$sp':
            if any(re.fullmatch(r'\$(?:ra|gp|fp|s[0-7])',m['reg']) for m in (a,b)):
                continue
            target,candidate = (int(m['offset'] or '0',0) for m in (a,b))
            if target!=candidate:
                yield target
        a,b = ADD.fullmatch(left),ADD.fullmatch(right)
        if a and b and a[2]==b[2]=='$sp' and a[1]==b[1]!='$sp' and a[3]!=b[3]:
            yield _sext16(a[3])


def _stack_slots(code):
    slots = set()
    for _,text in code:
        if (access := MEMORY.fullmatch(text)) and access['base']=='$sp' and access['reg'] not in {'$ra','$gp','$fp'}:
            slots.add(int(access['offset'] or '0',0))
        elif (add := ADD.fullmatch(text)) and add[1]!='$sp' and add[2]=='$sp':
            slots.add(int(add[3],0))
    return slots


def _indexed_fields(code, widths=LOAD_WIDTH, *, separate_accesses=False):
    groups = defaultdict(dict)
    changed = set()
    high, linear, indexed = {}, {}, {}
    for position,(diff,text) in enumerate(code):
        op,_,rest = text.partition(' ')
        args = [arg.strip() for arg in rest.split(',')]
        dst = args[0]
        if op in {'jal','jalr'}:
            for state in (high,linear,indexed):
                for register in set(state):
                    if not re.fullmatch(r'\$s[0-7]', register):
                        state.pop(register)
            continue
        if TRANSFER.match(text):
            high.clear()
            linear.clear()
            indexed.clear()
            continue
        if match := re.fullmatch(rf'({REG}),\s*%lo\(({SYMBOL})\)\(({REG})\)',rest):
            addr = indexed.get(match[3])
            if op in widths and addr and addr[0]==match[2]:
                key = ((addr[1], position), addr[2]) if separate_accesses else addr[1:]
                groups[key][match[2]] = widths[op]
                if diff:
                    changed.add(key)
        if not re.fullmatch(REG,dst) or op in {'sb','sh','sw','swc1','mult','multu','div','divu'}:
            continue
        added = {}
        if op=='lui' and (match:=re.fullmatch(rf'{REG},\s*%hi\(({SYMBOL})\)',rest)):
            added['high'] = match[1]
        elif op=='sll' and len(args)==3 and re.fullmatch(NUM,args[2]) and 1<=int(args[2],0)<=5:
            root,factor = linear.get(args[1],(args[1],1))
            added['linear'] = (root,factor << int(args[2],0))
        elif op in {'addu','subu'} and len(args)==3:
            left,right = args[1:]
            a,b = linear.get(left,(left,1)),linear.get(right,(right,1))
            if op=='addu' and left in high and 4<=b[1]<=128:
                added['indexed'] = (high[left],*b)
            elif op=='addu' and right in high and 4<=a[1]<=128:
                added['indexed'] = (high[right],*a)
            elif a[0]==b[0]:
                added['linear'] = (a[0],a[1]+b[1] if op=='addu' else a[1]-b[1])
        # Unknown definitions invalidate stale constant/base information.
        for key,values in (('high',high),('linear',linear),('indexed',indexed)):
            values.pop(dst,None)
            if key in added:
                values[dst] = added[key]
        if not added:
            linear[dst] = ((dst,position),1)
    return groups,changed


def _relocated_views(code, other):
    groups, changed = _indexed_fields(code)
    candidate, _ = _indexed_fields(other)
    return [(key[1],widths) for key,widths in groups.items() if key in changed and len(widths)>=2
            and not any(other_key[1]==key[1] and widths.items() <= other_widths.items()
                        for other_key,other_widths in candidate.items())]


def _halfword_masks(target, candidate):
    if not any(flag and re.match(r'(?:lh|andi)\s',line) for flag,line in target):
        return set()
    if not any(flag and re.match(r'(?:lhu|and|andi|lui|addiu)\s',line) for flag,line in candidate):
        return set()
    masks = set()
    for i,(_,text) in enumerate(target):
        if not text.startswith('lh '):
            continue
        reg = re.findall(REG,text)[0]
        for j,(changed,line) in enumerate(target[i+1:i+19],i+1):
            mask = re.fullmatch(rf'andi\s+({REG}),\s*{re.escape(reg)},\s*({NUM})',line)
            if not mask or not changed or not 0x8000 <= int(mask[2],0) <= 0xffff:
                continue
            carried = {mask[1]}
            for _,use in target[j+1:j+6]:
                regs = re.findall(REG,use)
                if use.startswith('sh ') and regs and regs[0] in carried:
                    masks.add(int(mask[2],0))
                    break
                if use.startswith(('addu ','or ')) and carried.intersection(regs[1:]):
                    carried.add(regs[0])
    return masks


def proposals(source, name, header, rows, asm, target, candidate, frame):
    out = []
    if sites := _selectors(target,candidate):
        out += control.selector_after_call(source,name,header,sites)
    if fields := _field_first(target,candidate):
        out += control.field_first(source,name,header,fields)
    if bounds := _tail_bounds(target,candidate,asm):
        out += control.loop_tail_bound(source,name,header,bounds)
    if scales := {scale for scale,_,_ in set(_scaled_stores(target,early=False)) & set(_scaled_stores(candidate,early=True))}:
        out += storage.delayed_pointer(source,name,header,scales)
    if globals_ := _guard_globals(target,candidate):
        out += storage.guarded_global_address(source,name,header,globals_)
    if offsets := set(_copy_order(target,early=False)) & set(_copy_order(candidate,early=True)):
        out += storage.post_copy_store(source,name,header,offsets)
    moved = {slot['target'] for slot in (frame or {}).get('local_operands',()) if slot['delta']}
    moved.update(_changed_stack_offsets(rows))
    if len(moved)>=2:
        out += storage.stack_array_record(source,name,header,_stack_slots(target),moved)
    if views := _relocated_views(target,candidate):
        out += storage.strided_fields(source,name,header,views)
    if masks := _halfword_masks(target,candidate):
        out += arithmetic.halfword_use(source,name,header,masks)
    if (floating_only(rows,'mips') and any(CALL.fullmatch(text) for _,text in target)
            and any(changed and text.startswith(('mul.d ','mul.s ','div.d ','div.s ')) for changed,text in target)):
        out += arithmetic.reused_float(source,name,header)
    return out
