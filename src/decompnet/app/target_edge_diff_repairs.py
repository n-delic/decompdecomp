"""Select bounded C rewrites using the measured MIPS instruction pair."""

import re
from collections import Counter

from decomprepair import target_edge_repairs as repairs

from .measured_mips_repairs import ADD, BRANCH, CALL, MEMORY, NUM, REG, SYMBOL, _sext16, _written, labelled_code
from .target_schedule_diff_repairs import THREE

SAVED_FP = re.compile(r'\$(?:f(?:2[0-9]|3[01])|fs[0-9]+)')
LIKELY = {'beql','bnel','beqzl','bnezl'}


def _edges(rows,asm):
    instructions,labels = labelled_code(asm or '')
    if not instructions:
        return False
    for i,(flag,left,right) in enumerate(rows[:-1]):
        op = left.partition(' ')[0]
        if flag==' ' or op not in LIKELY or right.partition(' ')[0] not in {'beq','bne','beqz','bnez'}:
            continue
        _,slot,other = rows[i+1]
        step = ADD.fullmatch(slot)
        if not step or step[1]!=step[2] or _sext16(step[3])!=1 or not other.startswith(('andi ','sltu ','move ')):
            continue
        label = left.rsplit(',',1)[-1].strip()
        if label in labels:
            start = labels[label]
            compared = {step[1]}
            for mnemonic,operand in instructions[start:start+5]:
                if mnemonic in {'slt','sltu','slti','sltiu'} and step[1] in re.findall(REG,operand)[1:]:
                    compared.add(operand.split(',',1)[0].strip())
                if (BRANCH.match(mnemonic+' ') and labels.get(operand.rsplit(',',1)[-1].strip(),start)<start
                        and compared.intersection(re.findall(REG,operand))):
                    return True
    return False


def _float_calls(rows,target,candidate):
    def sites(code):
        calls = [(i,hit[1]) for i,(_,line) in enumerate(code) if (hit:=CALL.fullmatch(line))]
        saved = {m['reg'] for _,line in code if (m:=MEMORY.fullmatch(line)) and m['base']=='$sp'
                 and m['op']=='sdc1' and SAVED_FP.fullmatch(m['reg'])}
        for (i,first),(j,second) in zip(calls,calls[1:]):
            if not 2<j-i<=16:
                continue
            before,after = code[i+2:j+1],code[j+2:j+22]
            live = {m[1] for _,line in before if (m:=re.fullmatch(rf'mov\.s\s+({REG}),\s*{REG}',line)) and m[1] in saved}
            read = {r for _,line in after for r in re.findall(REG,line)[1:]
                    if line.startswith(('mov.s ','add.s ','mul.s ','sub.s '))}
            if any(BRANCH.match(line) or line.startswith('bc1') for _,line in after):
                yield (first,second),live & read

    other = dict(sites(candidate))
    return {pair for pair,live in sites(target) if len(live)==2 and len(other.get(pair,set()))==1
            and any(mark!=' ' and any(r in re.findall(REG,left) for r in live) for mark,left,_ in rows)}


def _narrow_mult(rows):
    changed = [(i,left,right) for i,(mark,left,right) in enumerate(rows) if mark!=' ']
    if len(changed)!=1:
        return False
    i,a,b = changed[0]
    shape = rf'(multu?)\s+({REG}),\s*({REG})'
    x,y = re.fullmatch(shape,a),re.fullmatch(shape,b)
    if not x or not y or x[1]!=y[1] or (x[2],x[3])!=(y[3],y[2]) or x[2]==x[3]:
        return False
    before = [line for _,line,_ in rows[max(0,i-9):i]]
    half = any(line.startswith('lhu ') and _written(line) in (x[2],x[3]) for line in before)
    return half and any(line.startswith('mflo ') for _,line,_ in rows[i+1:i+5])


def _bias(target,candidate):
    def corrections(code):
        for i,(changed,line) in enumerate(code):
            cvt = re.fullmatch(rf'cvt\.d\.w\s+({REG}),\s*{REG}',line)
            if not cvt:
                continue
            window = code[max(0,i-5):i+14]
            sign = any(re.match(r'b(?:ltz|gez|lez|gtz)l?\s',op) for _,op in window)
            if not sign:
                continue
            adds = [j for j,(_,op) in enumerate(window) if (m:=THREE.fullmatch(op)) and m['op']=='add.d'
                    and cvt[1] in (m['a'],m['b'])]
            pairs = [window[j] for j in adds if any(op.startswith('ldc1 ') or re.search(r'\b(?:0x41f0|16880)\b',op)
                                                   for _,op in window[max(0,j-4):j])]
            if pairs:
                yield len(pairs),changed or any(flag for flag,_ in pairs)
    return any(n==1 for n,_ in corrections(target)) and any(n>=2 and diff for n,diff in corrections(candidate))


def _packed(target,candidate,big_endian):
    stride = 3 if big_endian else -3

    def blocks(code,left,right):
        for i,(_,line) in enumerate(code):
            a = MEMORY.fullmatch(line)
            if not a or a['op']!=left:
                continue
            offset = int(a['offset'] or '0',0)
            pairs = set()
            for j,(_,text) in enumerate(code[i:i+17],i):
                b = MEMORY.fullmatch(text)
                if not b or b['op']!=left or b['base']!=a['base']:
                    continue
                point = int(b['offset'] or '0',0)
                if point not in (offset,offset+4,offset+8):
                    continue
                if any((m:=MEMORY.fullmatch(op)) and m['op']==right and m['reg']==b['reg']
                       and m['base']==b['base'] and int(m['offset'] or '0',0)==point+stride
                       for _,op in code[j+1:j+4]):
                    pairs.add(point)
            if len(pairs)==3:
                yield i
    def found(code):
        stores = list(blocks(code,'swl','swr'))
        return any(0<=b-a<=25 for a in blocks(code,'lwl','lwr') for b in stores)
    return any(flag and line.startswith(('lwl ','lwr ','swl ','swr ')) for flag,line in target) and found(target) and not found(candidate)


def _zero_calls(target,candidate):
    result = set()
    for i,(changed,line) in enumerate(target):
        hit = re.fullmatch(rf'mtc1\s+\$zero,\s*({REG})',line)
        if not hit or not SAVED_FP.fullmatch(hit[1]):
            continue
        region = target[i+1:i+100]
        for j,(_,op) in enumerate(region):
            call = CALL.fullmatch(op)
            if not call:
                continue
            stores = [m for _,text in region[:j+2] if (m:=MEMORY.fullmatch(text)) and m['op']=='swc1' and m['reg']==hit[1]]
            stack = [m for m in stores if m['base']=='$sp']
            if len(stores)-len(stack)<2 or not stack or not any(BRANCH.match(text) for _,text in region[:j]):
                continue
            for k,(_,peer) in enumerate(candidate):
                if peer==op and any(flag and (store:=MEMORY.fullmatch(text)) and store['op']=='sw'
                                    and store['reg']=='$zero' and store['base']=='$sp'
                                    and any(store['offset']==m['offset'] for m in stack)
                                    for flag,text in candidate[max(0,k-7):k+2]):
                    result.add(call[1])
    return result


def _store_polarity(rows,asm):
    code, labels = labelled_code(asm or '')
    for i,(flag,left,right) in enumerate(rows[:-1]):
        a,_,args = left.partition(' ')
        b = right.partition(' ')[0]
        if flag==' ' or {a,b}!={'beql','bnel'} or re.findall(REG,left)!=re.findall(REG,right):
            continue
        lp,rp = (THREE.fullmatch(s) for s in rows[i+1][1:])
        if not lp or not rp or lp['op']!=rp['op'] or lp['op'] not in {'andi','sll','sra'} or lp['a']==rp['a']:
            continue
        entry = labels.get(args.rsplit(',',1)[-1].strip(),-1)
        if entry>=0 and any(op in {'sw','sh'} and lp['dst'] in re.findall(REG,values)[:1]
                            for op,values in code[entry:entry+12]):
            return True
    return False


def _key_stages(target,candidate):
    def sites(code,interleaved):
        for i,(_,line) in enumerate(code):
            shift = THREE.fullmatch(line)
            if not shift or shift['op']!='srl' or not re.fullmatch(NUM,shift['b']):
                continue
            region = code[max(0,i-5):i+15]
            loads = [(j,m) for j,(_,text) in enumerate(region) if (m:=MEMORY.fullmatch(text)) and m['op']=='lbu']
            if len(loads)!=2 or loads[0][1]['base']!=loads[1][1]['base']:
                continue
            shifts = []
            for j,m in loads:
                matched = next(((k,int(s['b'],0)) for k,(_,text) in enumerate(region[j+1:],j+1)
                                if (s:=THREE.fullmatch(text)) and s['op']=='sll' and s['a']==m['reg']
                                and re.fullmatch(NUM,s['b'])),None)
                if matched:
                    shifts.append(matched)
            if (len(shifts)==2 and shifts[0][0]!=shifts[1][0]
                    and (shifts[0][0]<loads[1][0])==interleaved
                    and sum(text.startswith('or ') for _,text in region)>=2):
                yield (int(shift['b'],0),int(loads[0][1]['offset'] or '0',0),shifts[0][1],
                       int(loads[1][1]['offset'] or '0',0),shifts[1][1])
    return set(sites(target,True)) & set(sites(candidate,False))


def _float_stores(target,candidate):
    def sites(code,interleaved):
        for i,(_,line) in enumerate(code):
            load = MEMORY.fullmatch(line)
            if not load or load['op']!='lwc1':
                continue
            region = code[i+1:i+16]
            calc = next(((j,m) for j,(_,text) in enumerate(region) if (m:=THREE.fullmatch(text))
                         and m['op']=='sub.s' and m['a']==load['reg']),None)
            if not calc:
                continue
            j,op = calc
            stores = [(k,m) for k,(_,text) in enumerate(region) if (m:=MEMORY.fullmatch(text))
                      and m['base']==load['base']]
            out = next((k for k,m in stores if m['op']=='swc1' and m['reg']==op['dst'] and m['offset']==load['offset']),None)
            half = [(k,m['offset']) for k,m in stores if m['op']=='sh' and m['offset']!=load['offset']]
            if (out is not None and len(half)==2 and j<out
                    and (all(k<j for k,_ in half) if interleaved else j<half[0][0])):
                yield (load['offset'],tuple(m for _,m in half))
    return bool(set(sites(target,True)) & set(sites(candidate,False)))


def _adjacent_calls(source,header,target,candidate,resolve):
    if not resolve:
        return set()
    def sites(code):
        previous = -2
        for i,(_,line) in enumerate(code):
            call = CALL.fullmatch(line)
            if not call:
                continue
            for _,prior in reversed(code[max(0,previous+2,i-5):i+2]):
                addr = re.fullmatch(rf'addiu\s+(\$a[0-3]),\s*{REG},\s*%lo\(({SYMBOL})\)',prior)
                if addr:
                    yield i,call[1],addr[1],addr[2]
                    break
            previous = i
    all_sites = list(sites(target))
    counts = Counter((reg,symbol) for _,_,reg,symbol in sites(candidate))
    saved = {m['reg'] for flag,line in candidate if flag and (m:=MEMORY.fullmatch(line))
             and m['op']=='sw' and m['base']=='$sp' and re.fullmatch(r'\$s[0-7]',m['reg'])}
    if not saved:
        return set()
    moved = {(call,reg) for i,(_,line) in enumerate(candidate) if (m:=CALL.fullmatch(line))
             for call in (m[1],) for _,arg in candidate[max(0,i-3):i+2]
             if (m:=re.fullmatch(rf'move\s+(\$a[0-3]),\s*({REG})',arg)) and m[2] in saved for reg in (m[1],)}
    declared = set(re.findall(rf'\bextern\s+[^;{{}}\n]*?\b({SYMBOL})\s*;',header+'\n'+source))
    result = set()
    for i,first,reg,symbol in all_sites:
        for j,second,r,s in all_sites:
            if not i<j or (r,s)!=(reg,symbol) or counts[reg,symbol]>=2 or (second,reg) not in moved:
                continue
            address = resolve(symbol)
            if address is None:
                continue
            for neighbor in sorted(declared-{symbol}):
                other = resolve(neighbor)
                if other is not None and 0<address-other<=16:
                    result.add((first,second,symbol,neighbor,address-other))
    return result


def proposals(source,name,header,rows,target,candidate,asm,*,resolve=None,big_endian=False,flags=()):
    evidence = (
        (repairs.increment_edge,_edges(rows,asm)),
        (repairs.float_call_reuse,_float_calls(rows,target,candidate)),
        (repairs.narrow_multiply,_narrow_mult(rows)),
        (repairs.duplicate_bias,_bias(target,candidate)),
        (repairs.packed_copy,'-fstrict-aliasing' not in flags and _packed(target,candidate,big_endian)),
        (repairs.saved_float_zero,_zero_calls(target,candidate)),
        (repairs.exchange_store_arms,_store_polarity(rows,asm)),
        (repairs.packed_key,_key_stages(target,candidate)),
        (repairs.interleaved_field,_float_stores(target,candidate)),
        (repairs.adjacent_call,_adjacent_calls(source,header,target,candidate,resolve)),
    )
    return repairs.variants(source,name,header,[(repair,finding) for repair,finding in evidence if finding])
