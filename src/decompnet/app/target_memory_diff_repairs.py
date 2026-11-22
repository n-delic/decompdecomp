"""Gate storage, arithmetic and loop rewrites on measured MIPS evidence."""

import re

from decomprepair import byte_countdown_order
from decomprepair import target_arithmetic_repairs as arithmetic
from decomprepair import target_memory_repairs as memory

from .allocation_search import floating_only
from .measured_mips_repairs import (
    ADD,
    BRANCH,
    CALL,
    MEMORY,
    NUM,
    REG,
    SYMBOL,
    TRANSFER,
    _sext16,
    _value,
    _written,
    labelled_code,
)
from .operand_and_edge_search import FP, FPR

RELOC = re.compile(rf'(?P<op>lbu|lh|lw|sb)\s+(?P<reg>{REG}),\s*%lo\((?P<symbol>{SYMBOL})\)\((?P<base>{REG})\)')
SHIFT = re.compile(rf'andi\s+({REG}),\s*({REG}),\s*({NUM})')


def _increment_sites(code, reload):
    for i,(changed,line) in enumerate(code):
        store = RELOC.fullmatch(line)
        if not store or store['op']!='sb':
            continue
        symbol,reg = store['symbol'],store['reg']
        before = code[max(0,i-6):i]
        increment = next((j for j in range(len(before)-1,-1,-1) if (step:=ADD.fullmatch(before[j][1]))
                          and step[1]==step[2]==reg and _sext16(step[3])==1),None)
        if increment is None or any(_written(text)==reg for _,text in before[increment+1:]):
            continue
        if not any((m:=RELOC.fullmatch(text)) and m['symbol']==symbol and m['op']=='lbu' and m['reg']==reg
                   for _,text in before[:increment]):
            continue
        found = None
        for j,(flag,text) in enumerate(code[i+1:i+6]):
            if (m:=RELOC.fullmatch(text)) and m['op']=='lbu' and m['symbol']==symbol:
                found = flag
                break
            if TRANSFER.match(text) and not CALL.fullmatch(text):
                break
            if j and CALL.fullmatch(code[i+j][1]):
                break
        if reload and found is not None and (changed or found):
            yield symbol
        if not reload and found is None:
            yield symbol


def _one_arm(rows, code, labels):
    touched = set()
    for i,(marker,left,right) in enumerate(rows):
        a,b = RELOC.fullmatch(left),RELOC.fullmatch(right)
        if not a or not b or a['op']!=b['op'] or a['op']!='lbu' or a['symbol']!=b['symbol']:
            continue
        if marker!=' ' or any(m!=' ' and (x.startswith('addu ') or y.startswith(('addu ','move ')))
                              for m,x,y in rows[max(0,i-2):i]):
            touched.add(a['symbol'])
    result = set()
    for i,(op,args) in enumerate(code):
        if not BRANCH.match(op+' '):
            continue
        alternative = labels.get(args.split(',')[-1].strip(),0)
        if not i+2<alternative<=i+14:
            continue
        jump = next((j for j in range(i+2,alternative) if code[j][0]=='j'),None)
        join = labels.get(code[jump][1],0) if jump is not None else 0
        if not alternative<join<=alternative+12 or not any(mnemonic=='jal' for mnemonic,_ in code[join:join+4]):
            continue
        arms = [{(m['symbol'],m['reg']) for mnemonic,operands in region
                 if mnemonic=='lbu' and (m:=RELOC.fullmatch('lbu '+operands))}
                for region in (code[i+2:jump],code[alternative:join])]
        if not all(len(arm)==1 for arm in arms):
            continue
        pair = [next(iter(arm)) for arm in arms]
        if pair[0][1]==pair[1][1] and pair[0][0]!=pair[1][0]:
            symbols = {symbol for symbol,_ in pair} & touched
            if len(symbols)==1:
                result.update(symbols)
    return result


def _decrements(code, same_register):
    for i,(changed,text) in enumerate(code):
        load = RELOC.fullmatch(text)
        if not load or load['op'] not in {'lw','lh'}:
            continue
        reg = load['reg']
        for flag,next_ in code[i+1:i+4]:
            step = ADD.fullmatch(next_)
            if step and step[2]==reg and _sext16(step[3])==-1:
                if (step[1]==reg)==same_register and (changed or flag):
                    if not same_register or any((old:=ADD.fullmatch(prior)) and old[1]==old[2]==reg
                                                and _sext16(old[3])==-1 for _,prior in code[:i]):
                        yield load['symbol']
                break
            if _written(next_)==reg or TRANSFER.match(next_):
                break


def _float_literal_calls(target,candidate):
    saved = {m['reg'] for flag,text in candidate if flag and (m:=MEMORY.fullmatch(text))
             and m['op']=='sdc1' and m['base']=='$sp'}
    if not saved:
        return set()
    result = set()
    for i,(_,text) in enumerate(target):
        move = re.fullmatch(rf'mtc1\s+{REG},\s*({FPR})',text)
        if not move or re.fullmatch(r'\$(?:fs\d+|f(?:2[0-9]|3[01]))',move[1]):
            continue
        stores = [m for _,line in target[i+1:i+9] if (m:=MEMORY.fullmatch(line))
                  and m['op']=='swc1' and m['reg']==move[1]]
        if len(stores)<2 or stores[0]['base']!=stores[1]['base']:
            continue
        offsets = {int(m['offset'] or '0',0) for m in stores[:2]}
        if len(offsets)<2:
            continue
        call = next((call[1] for _,line in reversed(target[:i]) if (call:=CALL.fullmatch(line))),None)
        if not call:
            continue
        for j,(_,line) in enumerate(candidate):
            if line!='jal '+call:
                continue
            staged = {m[1] for _,earlier in candidate[max(0,j-32):j] if
                      (m:=re.fullmatch(rf'mtc1\s+{REG},\s*({FPR})',earlier)) and m[1] in saved}
            for register in staged:
                before = {int(m['offset'] or '0',0) for _,later in candidate[j+2:j+90]
                          if (m:=MEMORY.fullmatch(later)) and m['op']=='swc1' and m['reg']==register}
                if offsets<=before:
                    result.add(call)
    return result


def _reset_bounds(target,candidate,code,labels):
    empty_calls = {call[1] for (_,text),(changed,delay) in zip(candidate,candidate[1:])
                   if (call:=CALL.fullmatch(text)) and delay=='nop' and changed}
    target_moves = {_value(text) for changed,text in target if changed and _value(text)}
    bounds = set()
    callees = set()
    for i,(op,args) in enumerate(code[:-4]):
        test = re.fullmatch(rf'({REG}),\s*({REG}),\s*({NUM})',args) if op in {'slti','sltiu'} else None
        if not test or not re.fullmatch(rf'bnez\s+{re.escape(test[1])},\s*\S+', ' '.join(code[i+1])):
            continue
        entry = labels.get(code[i+1][1].split(',')[-1].strip(),i)
        call = code[i+3][1]
        if not i-32<=entry<i or code[i+3][0]!='jal' or call not in empty_calls:
            continue
        if _value(' '.join(code[i+4]))!=(test[2],0) or (test[2],0) not in target_moves:
            continue
        bounds.add(int(test[3],0))
        callees.add(call)
    return bounds,callees


def _mult_high(target,candidate):
    if not any(changed and text.startswith('mflo ') for changed,text in candidate):
        return False
    if not any(changed and re.match(r'(?:sra|dsra|dsra32|sll)\s',text) for changed,text in candidate):
        return False
    for i,(flag,text) in enumerate(target):
        if not text.startswith('mult '):
            continue
        region = target[i+1:i+8]
        if (any(line.startswith('mfhi ') for _,line in region)
                and not any(line.startswith('mflo ') for _,line in region)
                and (flag or any(changed for changed,_ in region))):
            return True
    return False


def _float_chain(target, candidate):
    def chains(code, same):
        for (_,a),(_,b) in zip(code,code[1:]):
            first,second = FP.fullmatch(a),FP.fullmatch(b)
            if (first and second and a.startswith('add.') and b.startswith('add.') and first[1]==second[1]
                    and first[2] in (second[3],second[4]) and (first[2]==second[2])==same):
                yield first[1]
    return set(chains(target,True)) & set(chains(candidate,False))


def _timer_fields(target, candidate):
    offsets = set()
    for i,(changed,text) in enumerate(target[:-4]):
        load = MEMORY.fullmatch(text)
        if not load or load['op']!='lw' or not target[i+1][1].startswith('blez '):
            continue
        sub = re.fullmatch(rf'subu\s+({REG}),\s*{re.escape(load["reg"])},\s*{REG}',target[i+2][1])
        if not sub or not target[i+3][1].startswith('bgtz '+sub[1]+','):
            continue
        store = MEMORY.fullmatch(target[i+4][1])
        if not store or store['op']!='sw' or store['reg']!=sub[1] or (
                store['base'],store['offset'])!=(load['base'],load['offset']):
            continue
        if (changed or any(flag for flag,_ in target[i+1:i+5])) and any(
            (m:=MEMORY.fullmatch(line)) and m['op'] in {'lw','sw'}
            and m['offset']==load['offset'] for _,line in candidate
        ) and any(flag and (BRANCH.match(line) or line.startswith(('subu ','lw ','sw '))) for flag,line in candidate):
            offsets.add(int(load['offset'] or '0',0))
    return offsets


def _trim_pads(target,candidate,code,labels):
    if not any(flag and BRANCH.match(text) for flag,text in target) or not any(
        flag and BRANCH.match(text) for flag,text in candidate
    ):
        return set()
    pads = set()
    for i,(op,args) in enumerate(code):
        load = MEMORY.fullmatch(' '.join((op,args)))
        if not load or load['op'] not in {'lb','lbu'} or load['offset'] not in {'-1','-0x1'}:
            continue
        before = code[max(0,i-10):i]
        after = code[i+1:i+14]
        match = next((args.split(',') for op,args in after[:2] if op=='bne'),None)
        if not match or len(match)!=3 or load['reg'] not in {item.strip() for item in match[:2]}:
            continue
        other = next((item.strip() for item in match[:2] if item.strip()!=load['reg']),None)
        if other is None:
            continue
        pad = next((value[1] for op,args in reversed(before) if (value:=_value(' '.join((op,args))))
                    and value[0]==other),None)
        back = any(mnemonic=='bnez' and labels.get(ops.split(',')[-1].strip(),i+30)<=i
                   for mnemonic,ops in after)
        masks = sum(bool((m:=SHIFT.fullmatch(' '.join(row))) and int(m[3],0)==255) for row in before+after)
        if pad is not None and back and masks>=2 and any(mnemonic=='beqz' for mnemonic,_ in before+after):
            pads.add(pad)
    return pads


def _packed_constants(target,candidate):
    result = set()
    highs = re.compile(rf'lui\s+({REG}),\s*({NUM})')
    for i,(flag,line) in enumerate(target):
        high = highs.fullmatch(line)
        if not high or not 0x1000<=int(high[2],0)<=0xffff:
            continue
        before = target[max(0,i-5):i]
        for j,(diff,op) in enumerate(target[i+1:i+9],i+1):
            combine = re.fullmatch(rf'or\s+({REG}),\s*({REG}),\s*{re.escape(high[1])}',op)
            if not combine or combine[1]!=combine[2]:
                continue
            mask = next((int(m[3],0) for _,text in before if (m:=SHIFT.fullmatch(text)) and m[1]==combine[1]),None)
            if mask is None or not (flag or diff):
                continue
            next_ = target[j+1:j+4]
            if not any(text.startswith('or ') and combine[1] in re.findall(REG,text)[1:] for _,text in next_):
                continue
            if not any(any_change and (m:=highs.fullmatch(other)) and int(m[2],0)==int(high[2],0)
                       for any_change,other in candidate) and not any(
                any_change and other.startswith('or ') for any_change,other in candidate):
                continue
            result.add((int(high[2],0)<<16,mask))
    return result


def proposals(source,name,header,rows,asm,target,candidate,toolchain, *, big_endian):
    if toolchain.startswith('ido-5.3'):
        offsets = _timer_fields(target,candidate)
        return memory.direct_timer(source,name,header,offsets) if offsets else []
    result = []
    if toolchain.startswith('gcc-2.7.2-kmc'):
        result += byte_countdown_order.variants(source, name, rows, header=header)
    symbols = set(_increment_sites(target,True)) & set(_increment_sites(candidate,False))
    if symbols:
        result += memory.volatile_postfix(source,name,header,symbols)
    labelled = labelled_code(asm) if asm and any(changed and (
        BRANCH.match(text) or text.startswith(('lbu ','addu ','move '))) for changed,text in target+candidate) else None
    if labelled and (symbols:=_one_arm(rows,*labelled)):
        result += memory.branch_offset(source,name,header,symbols)
    symbols = set(_decrements(target,True)) & set(_decrements(candidate,False))
    if symbols:
        result += memory.split_decrement(source,name,header,symbols)
    if calls:=_float_literal_calls(target,candidate):
        result += memory.post_call_float_literal(source,name,header,calls)
    if labelled:
        bounds,calls = _reset_bounds(target,candidate,*labelled)
        if bounds:
            result += arithmetic.exit_reset(source,name,header,bounds,calls)
    if big_endian and _mult_high(target,candidate):
        result += arithmetic.multiply_high(source,name,header)
    if floating_only(rows,'mips') and (widths:=_float_chain(target,candidate)):
        result += arithmetic.float_accumulator(source,name,header,widths)
    if labelled and (pads:=_trim_pads(target,candidate,*labelled)):
        result += arithmetic.top_tested_trim(source,name,header,pads)
    if constants:=_packed_constants(target,candidate):
        result += arithmetic.packed_opcode(source,name,header,constants)
    return result
