"""Select source candidates from measured KMC inline and storage differences."""

import re
from collections import Counter, defaultdict

from decomprepair import target_inline_storage_repairs as repairs

from .measured_mips_repairs import BRANCH, CALL, MEMORY, NUM, REG, SYMBOL, TRANSFER, _value, _written, labelled_code
from .target_schedule_diff_repairs import THREE, _branch

SAVED_FLOAT = re.compile(r'\$(?:f(?:2[0-9]|3[01])|fs\d+)')
MOVE = re.compile(rf'move\s+({REG}),\s*({REG})')
HALF_SHIFT = re.compile(rf'(sll|sra)\s+({REG}),\s*({REG}),\s*(?:16|0x10)')


def _short_return(target,candidate,asm):
    if not asm:
        return False
    code,labels = labelled_code(asm)
    for i,(_,line) in enumerate(target[:-1]):
        left,right = HALF_SHIFT.fullmatch(line),HALF_SHIFT.fullmatch(target[i+1][1])
        if not left or not right or (left[1],right[1])!=('sll','sra') or left[2]!=right[3]:
            continue
        zero = next((value for _,text in target[max(0,i-18):i]
                     if (value:=_value(text)) and value==(left[3],0)),None)
        if not zero:
            continue
        joins = Counter()
        for j,(op,args) in enumerate(code[:-1]):
            label = args.rsplit(',',1)[-1].strip()
            dest = labels.get(label,-1)
            copy = MOVE.fullmatch(' '.join(code[j+1]))
            if (dest>=0 and BRANCH.match(op+' ') and copy and copy[2]==left[3]
                    and any(mnemonic in {'sll','sra'} for mnemonic,_ in code[dest:dest+5])):
                joins[label]+=1
        if any(count>=2 for count in joins.values()) and sum(
            bool(flag and (v:=_value(text)) and v[1]==0) for flag,text in candidate
        )>=2:
            return True
    return False


def _byte_address(rows):
    changed = [(i,a,b) for i,(flag,a,b) in enumerate(rows) if flag!=' ']
    if len(changed)!=1:
        return False
    i,left,right = changed[0]
    a,b = THREE.fullmatch(left),THREE.fullmatch(right)
    if (not a or not b or a['op']!=b['op'] or a['op']!='addu' or a['dst']!=b['dst']
            or (a['a'],a['b'])!=(b['b'],b['a']) or a['a']==a['b']):
        return False
    return (any((m:=MEMORY.fullmatch(text)) and m['op'] in {'lb','lbu'} and m['base']==a['dst']
                for _,text,_ in rows[i+1:i+4])
            and any((m:=THREE.fullmatch(text)) and m['op'] in {'sll','addu'} and m['dst'] in (a['a'],a['b'])
                    for _,text,_ in rows[max(0,i-6):i]))


def _float_reuse(rows,target,candidate):
    changed = [(a,b) for flag,a,b in rows if flag!=' ']
    if not changed or any(not a or not b or re.sub(REG,'_',a)!=re.sub(REG,'_',b)
                          or any(not SAVED_FLOAT.fullmatch(x) or not SAVED_FLOAT.fullmatch(y)
                                 for x,y in zip(re.findall(REG,a),re.findall(REG,b)) if x!=y)
                          for a,b in changed):
        return set()

    def live(code):
        for i,(_,line) in enumerate(code):
            callee = CALL.fullmatch(line)
            if not callee:
                continue
            before = {regs[0] for _,op in code[max(0,i-10):i] if op.startswith(('lwc1 ','mov.s ','add.s ','mul.s ','sub.s '))
                      and (regs:=re.findall(REG,op)) and SAVED_FLOAT.fullmatch(regs[0])}
            after = {r for _,op in code[i+2:i+14] if op.startswith(('mov.s ','add.s ','mul.s ','sub.s ','c.'))
                     for r in re.findall(REG,op)[0 if op.startswith('c.') else 1:]}
            yield callee[1],before & after

    peers = defaultdict(set)
    for name,regs in live(candidate):
        peers[name].update(regs)
    return {name for name,regs in live(target) if regs and peers[name] and regs!=peers[name]}


def _unsigned_entry(rows):
    for i,(_,left,right) in enumerate(rows[:-1]):
        a,b = MEMORY.fullmatch(left),MEMORY.fullmatch(right)
        if (not a or not b or a['op']!='lbu' or b['op'] not in {'lbu','lb'}
                or (a['offset'],a['base'])!=(b['offset'],b['base'])):
            continue
        flag,x,y = rows[i+1]
        if (flag!=' ' and x.startswith('blez ') and a['reg'] in re.findall(REG,x)
                and y.startswith(('beqz ','beql ','blez ')) and b['reg'] in re.findall(REG,y)
                and x.rsplit(',',1)[-1].strip()==y.rsplit(',',1)[-1].strip()
                and any(op.startswith('addiu ') for _,op,_ in rows[i+2:i+17])):
            return True
    return False


def _coordinates(target,candidate):
    def sites(code, grouped):
        ops = [(flag,line) for flag,line in code if line.startswith(('lwc1 ','sub.s '))]
        for i in range(len(ops)-8):
            group = ops[i:i+9]
            kinds = [op.partition(' ')[0] for _,op in group]
            recipe = ['lwc1','lwc1','sub.s']*3
            if (kinds==recipe)!=grouped or Counter(kinds)!=Counter(recipe):
                continue
            loads = Counter(op.split(',',1)[1].strip() for _,op in group if op.startswith('lwc1 '))
            if grouped and not all(set(re.findall(REG,group[j+2][1])[1:])=={
                re.findall(REG,group[j][1])[0],re.findall(REG,group[j+1][1])[0]
            } for j in (0,3,6)):
                continue
            yield tuple(sorted(loads.items())),any(flag for flag,_ in group)
    others = {shape for shape,_ in sites(candidate,False)}
    return any(changed and shape in others for shape,changed in sites(target,True))


def _volatile_pointers(target,candidate):
    direct = Counter()
    indirect = Counter()
    changed = set()
    for flag,line in target:
        if m:=re.fullmatch(rf'lw\s+{REG},\s*%lo\(({SYMBOL})\)\({REG}\)',line):
            direct[m[1]]+=1
            if flag:
                changed.add(m[1])
    addresses = {}
    for flag,line in candidate:
        if TRANSFER.match(line):
            addresses.clear()
        elif m:=re.fullmatch(rf'addiu\s+({REG}),\s*{REG},\s*%lo\(({SYMBOL})\)',line):
            addresses[m[1]] = m[2],flag
        else:
            load = MEMORY.fullmatch(line)
            if load and load['op']=='lw' and load['base'] in addresses and load['offset'] in {None,'0','0x0'}:
                symbol,relocated = addresses[load['base']]
                indirect[symbol] += 1
                if flag or relocated:
                    changed.add(symbol)
            addresses.pop(_written(line),None)
    return {s for s,n in direct.items() if n>=2 and indirect[s]==n and s in changed}


def _narrow_calls(target,candidate):
    out = defaultdict(set)
    candidate_calls = Counter(m[1] for _,line in candidate if (m:=CALL.fullmatch(line)))
    for width,mask in ((8,255),(16,65535)):
        calls = Counter()
        disturbed = False
        for i,(_,line) in enumerate(target):
            call = CALL.fullmatch(line)
            if not call:
                continue
            for flag,text in target[max(0,i-4):i+2]:
                m = THREE.fullmatch(text)
                if (m and m['op']=='andi' and re.fullmatch(r'\$a[0-3]',m['dst'])
                        and re.fullmatch(NUM,m['b']) and int(m['b'],0)==mask):
                    calls[call[1],int(m['dst'][-1])] += 1
                    disturbed |= flag
                    break
        if disturbed and any(flag and BRANCH.match(line) for flag,line in target):
            out[width].update(pair for pair,count in calls.items() if count>=2 and candidate_calls[pair[0]]==count)
    return out


def _noop_cases(rows,asm):
    code,labels = labelled_code(asm or '')
    changed = {left for flag,left,_ in rows if flag!=' '}

    def destination(start,register,selector,limit):
        values = {'$zero':0,register:selector}
        pc = start
        visited = set()
        while start<=pc<limit and pc not in visited:
            visited.add(pc)
            op,args = code[pc]
            text = f'{op} {args}'
            if op in {'j','b','beq','beqz','bne','bnez'}:
                branch, operands = _branch(text)
                dest = labels.get(operands[-1],-1)
                if pc+1>=len(code) or code[pc+1][0]!='nop' or dest<0:
                    return None
                if branch in {'beq','bne'}:
                    if len(operands)!=3 or any(reg not in values for reg in operands[:2]):
                        return None
                    equal = values[operands[0]]==values[operands[1]]
                    dest = dest if equal==(branch=='beq') else pc+2
                pc = dest
                continue
            if immediate:=_value(text):
                values[immediate[0]] = immediate[1]
            elif (cmp:=THREE.fullmatch(text)) and op in {'slti','sltiu'} and (
                cmp['a'] in values and re.fullmatch(NUM,cmp['b'])
            ):
                values[cmp['dst']] = int(values[cmp['a']]<int(cmp['b'],0))
            elif op!='nop':
                return pc
            pc += 1
        return None

    result = set()
    for i,(op,args) in enumerate(code[:-1]):
        cmp = THREE.fullmatch(f'{op} {args}')
        if op not in {'slti','sltiu'} or not cmp or not re.fullmatch(NUM,cmp['b']) or f'{op} {args}' not in changed:
            continue
        cutoff = int(cmp['b'],0)
        end = min(len(code),i+20)
        tests = set()
        for j in range(i+3,end):
            mnemonic,arguments = code[j]
            branch,operands = _branch(f'{mnemonic} {arguments}')
            if branch not in {'beq','bne'} or len(operands)!=3 or cmp['a'] not in operands[:2]:
                continue
            peer = next((x for x in operands[:2] if x!=cmp['a']),None)
            immediate = next((v for p in reversed(code[max(i,j-3):j]) if (v:=_value(' '.join(p))) and v[0]==peer),None)
            if immediate:
                tests.add(immediate[1])
        for low in sorted(tests):
            if not 0<low<=255 or low+1 not in tests or cutoff not in (low,low+1):
                continue
            absent,neighbor,active = (destination(i,cmp['a'],value,end) for value in (low-1,low,low+1))
            if absent is not None and absent==neighbor and active is not None and absent!=active:
                result.add(low-1)
    return result


def _multipliers(target,candidate):
    values = {}
    uses = Counter()
    for flag,line in target:
        immediate = _value(line)
        insn = re.match(rf'(?:mult|multu)\s+({REG}),\s*({REG})',line)
        if insn and flag:
            for reg in insn.groups():
                if reg in values:
                    uses[values[reg]]+=1
        elif immediate:
            values[immediate[0]] = immediate[1]
        elif TRANSFER.match(line):
            values.clear()
        elif m:=THREE.fullmatch(line):
            values.pop(m['dst'],None)
    witnesses = {}
    expanded = Counter()
    for flag,line in candidate:
        if TRANSFER.match(line):
            witnesses.clear()
            continue
        insn = THREE.fullmatch(line)
        if not insn:
            witnesses.pop(_written(line),None)
            continue
        dst,left,right = (insn[k] for k in ('dst','a','b'))
        base,factor,shifted,combined,measured = witnesses.get(left,(left,1,False,False,False))
        previous = witnesses.get(right,(right,1,False,False,False))
        witnesses.pop(dst,None)
        if insn['op']=='sll' and re.fullmatch(NUM,right) and 0<int(right,0)<32:
            current = (base,(factor<<int(right,0))&0xffffffff,True,combined,measured or flag)
        elif insn['op'] in {'addu','subu'} and previous[0]==base:
            coefficient = factor+(previous[1] if insn['op']=='addu' else -previous[1])
            current = (base,coefficient&0xffffffff,shifted or previous[2],True,measured or flag or previous[4])
        else:
            continue
        witnesses[dst] = current
        if all(current[2:]) and current[1] in uses:
            expanded[current[1]] += 1
    return {value for value,n in uses.items() if n>=3 and 2<value<=65535 and expanded[value]>=n}


def _stored_float_bits(target,candidate,read_data,big_endian):
    def stores(code,wanted):
        words,floats = {'$zero':0},{}
        for flag,line in code:
            if TRANSFER.match(line):
                words,floats = {'$zero':0},{}
                continue
            if hit:=MEMORY.fullmatch(line):
                if hit['op']==wanted:
                    registers = floats if wanted=='swc1' else words
                    if hit['reg'] in registers:
                        yield (hit['base'],hit['offset'],registers[hit['reg']]),flag
                elif hit['op'].startswith('l'):
                    (floats if hit['op'].endswith('c1') else words).pop(hit['reg'],None)
                continue
            if m:=re.fullmatch(rf'mtc1\s+({REG}),\s*({REG})',line):
                floats.pop(m[2],None)
                if m[1] in words:
                    floats[m[2]]=words[m[1]]
                continue
            if m:=re.fullmatch(rf'lwc1\s+({REG}),\s*%lo\(({SYMBOL})\)\({REG}\)',line):
                floats.pop(m[1],None)
                if wanted=='swc1' and read_data:
                    raw = read_data(m[2],4)
                    if raw and len(raw)==4:
                        floats[m[1]]=int.from_bytes(raw,'big' if big_endian else 'little')
                continue
            if m:=re.fullmatch(rf'lui\s+({REG}),\s*({NUM})',line):
                words[m[1]]=(int(m[2],0)&65535)<<16
            elif v:=_value(line):
                words[v[0]]=v[1]&0xffffffff
            elif m:=THREE.fullmatch(line):
                known = words.get(m['a'])
                words.pop(m['dst'],None)
                floats.pop(m['dst'],None)
                if m['op']=='ori' and re.fullmatch(NUM,m['b']) and known is not None:
                    words[m['dst']]=known|(int(m['b'],0)&65535)
            elif written:=_written(line):
                words.pop(written,None)
                floats.pop(written,None)

    ints = {item for item,_ in stores(candidate,'sw')}
    if not ints:
        return set()
    return {item[2] for item,changed in stores(target,'swc1') if changed and item in ints}


def proposals(source,name,header,rows,target,candidate,asm,*,read_data=None,big_endian=False):
    evidence = (
        (repairs.shared_signed_return,_short_return(target,candidate,asm)),
        (repairs.split_byte_address,_byte_address(rows)),
        (repairs.reuse_dead_float,_float_reuse(rows,target,candidate)),
        (repairs.unsigned_byte_entry,_unsigned_entry(rows)),
        (repairs.coordinate_blocks,_coordinates(target,candidate)),
        (repairs.pointer_volatile,_volatile_pointers(target,candidate)),
        (repairs.narrow_arm_calls,_narrow_calls(target,candidate)),
        (repairs.missing_noop_case,_noop_cases(rows,asm)),
        (repairs.multiply_constant,_multipliers(target,candidate)),
        (repairs.float_bits,_stored_float_bits(target,candidate,read_data,big_endian)),
    )
    return repairs.variants(source,name,header,[(method,value) for method,value in evidence if value])
