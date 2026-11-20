"""Select guard and width candidates from measured MIPS differences."""

import re

from decomprepair import target_guard_width_repairs as repairs

from .measured_mips_repairs import ADD, BRANCH, CALL, MEMORY, NUM, REG, SYMBOL, _sext16, _value, labelled_code
from .target_schedule_diff_repairs import THREE, _branch

MOVE = re.compile(rf'move\s+({REG}),\s*({REG})')


def _nonnull(target,candidate):
    for i,(_,line) in enumerate(target[:-2]):
        load = MEMORY.fullmatch(line)
        op,args = _branch(target[i+1][1])
        copy = MOVE.fullmatch(target[i+2][1])
        if (not load or load['op']!='lw' or not copy or op not in {'beq','bne'}
                or len(args)!=3 or set(args[:2])!={load['reg'],'$zero'} or copy[2]!=load['reg'] or copy[1]==copy[2]):
            continue
        if sum(bool((m:=MEMORY.fullmatch(text)) and m['base']==copy[1]) for _,text in target[i+3:i+12])<2:
            continue
        for j,(changed,text) in enumerate(candidate[:-2]):
            peer = MEMORY.fullmatch(text)
            if (peer and peer['op']=='lw' and peer['reg']==copy[1]
                    and (peer['base'],peer['offset'])==(load['base'],load['offset'])
                    and candidate[j+1][1].startswith(('beq','bne')) and copy[1] in re.findall(REG,candidate[j+1][1])
                    and (changed or any(flag for flag,_ in target[i+1:i+3]))):
                return True
    return False


def _fallthrough(rows,asm):
    if not asm:
        return False
    code,labels = labelled_code(asm)
    changed = {a for flag,a,_ in rows if flag!=' '}
    for i,(op,args) in enumerate(code):
        if op not in {'bc1t','bc1f','bc1tl','bc1fl'}:
            continue
        join = labels.get(args,-1)
        if not i+2<join<=min(i+7,len(code)):
            continue
        for j in range(i+2,join):
            line = ' '.join(code[j])
            load = MEMORY.fullmatch(line)
            if not load or load['op']!='lwc1':
                continue
            compare = any(name.startswith('c.') and name.endswith('.s') and load['reg'] in re.findall(REG,operands)
                          for name,operands in code[join:join+4])
            prior = any(' '.join(pair)==line for pair in code[max(0,i-8):i])
            if compare and prior and (line in changed or f'{op} {args}' in changed):
                return True
    return False


def _literals(rows,resolve):
    if not resolve:
        return set()
    evidence = set()
    for i,(flag,left,right) in enumerate(rows):
        high = re.fullmatch(rf'lui\s+({REG}),\s*({NUM})',left)
        if flag==' ' or not high or not right.startswith('lui '):
            continue
        for j in range(i+1,min(i+5,len(rows))):
            marker,_,text = rows[j]
            low = re.fullmatch(rf'addiu\s+({REG}),\s*({REG}),\s*%lo\(({SYMBOL})\)',text)
            if (marker==' ' or not low or low[1]!=low[2] or low[1]!=high[1]):
                continue
            value = resolve(low[3])
            upper = int(high[2],0)&0xffff
            mode = re.fullmatch(rf'lui\s+{re.escape(low[1])},\s*(?:%hi\({low[3]}\)|{NUM})',right)
            if not mode or not value or value!=upper<<16 or not 0<value<=0xffffffff:
                continue
            if any((a:=THREE.fullmatch(x)) and (b:=THREE.fullmatch(y))
                   and a['op']==b['op'] and a['op'] in {'and','or','xor'}
                   and high[1] in (a['a'],a['b']) and low[1] in (b['a'],b['b']) for _,x,y in rows[j+1:j+6]):
                evidence.add((low[3],value))
    return evidence


def _mask_or(target,candidate):
    def sites(code, normalized):
        for i,(_,line) in enumerate(code):
            combine = THREE.fullmatch(line)
            if not combine or combine['op']!='or' or combine['a']==combine['b']:
                continue
            window = code[max(0,i-5):i]
            masks = {}
            for changed,op in window:
                m = THREE.fullmatch(op)
                if not m:
                    continue
                previous = masks.get(m['b'])
                if m['op']=='andi' and re.fullmatch(NUM,m['b']):
                    masks[m['dst']] = (int(m['b'],0),False,changed)
                elif m['op']=='sltu' and m['a']=='$zero' and previous:
                    masks[m['dst']] = (previous[0],True,previous[2] or changed)
                else:
                    masks.pop(m['dst'],None)
            inputs = [masks.get(combine[key]) for key in ('a','b')]
            if all(item and item[1]==normalized for item in inputs) and any(
                BRANCH.match(op) and combine['dst'] in re.findall(REG,op) for _,op in code[i+1:i+4]
            ):
                yield tuple(item[0] for item in inputs),any(item[2] for item in inputs)
    plain = {m for m,_ in sites(candidate,False)}
    return {m for m,changed in sites(target,True) if changed and m in plain}


def _unbiased(target,candidate,asm):
    if not asm:
        return False
    code,labels = labelled_code(asm)
    if not any(BRANCH.match(op+' ') and labels.get(args.rsplit(',',1)[-1].strip(),i)<i
               for i,(op,args) in enumerate(code)):
        return False

    def stores(code):
        return [(i,m['op'],m['base'],int(m['offset'] or '0',0)) for i,(_,line) in enumerate(code)
                if (m:=MEMORY.fullmatch(line)) and m['op'] in {'sb','sh','sw'} and m['base']!='$sp']

    left,right = stores(target),stores(candidate)
    for a,b in zip(left,left[1:]):
        if a[2]!=b[2] or a[3]==b[3] or b[0]-a[0]>10:
            continue
        stride = {(_sext16(m[3])) for _,line in target[b[0]+1:b[0]+6]
                  if (m:=ADD.fullmatch(line)) and m[1]==m[2]==a[2] and _sext16(m[3])>0}
        if not stride:
            continue
        for x,y in zip(right,right[1:]):
            n = a[3]-x[3]
            if (not 0<n<=4096 or (a[1],b[1])!=(x[1],y[1]) or x[2]!=y[2] or b[3]-y[3]!=n
                    or not any(flag for flag,_ in target[a[0]:b[0]+1])):
                continue
            setup = any((m:=ADD.fullmatch(line)) and m[1]==x[2] and _sext16(m[3])==n
                        for _,line in candidate[max(0,x[0]-20):x[0]])
            moves = any((m:=ADD.fullmatch(line)) and m[1]==m[2]==x[2] and _sext16(m[3]) in stride
                        for _,line in candidate[y[0]+1:y[0]+6])
            if setup and moves:
                return True
    return False


def _byte_flag(rows):
    for i,(flag,left,right) in enumerate(rows[:-3]):
        a,b = THREE.fullmatch(left),THREE.fullmatch(right)
        if (flag==' ' or not a or not b or a['op']!=b['op'] or a['op']!='andi'
                or a['b'] not in {'1','0x1'} or a['b']!=b['b']):
            continue
        after = [line for _,line,_ in rows[i+1:i+4]]
        copy = MOVE.fullmatch(after[0])
        mask = THREE.fullmatch(after[1])
        if (copy and re.fullmatch(r'\$s[0-7]',copy[1]) and copy[1]==b['dst'] and copy[2]==a['dst']
                and mask and mask['op']=='andi' and mask['a']==a['dst'] and mask['b'] in {'255','0xff'}
                and BRANCH.match(after[2]) and mask['dst'] in re.findall(REG,after[2])):
            return True
    return False


def _guard_immediate(target,candidate):
    values = set()
    for i,(_,line) in enumerate(target[:-4]):
        op,args = _branch(line)
        if op!='beq' or len(args)!=3 or '$zero' not in args[:2]:
            continue
        value = _value(target[i+1][1])
        call = next((m[1] for _,text in reversed(target[max(0,i-5):i]) if (m:=CALL.fullmatch(text))),None)
        if not value or not call or not 0<value[1]<256:
            continue
        stores = [m for _,text in target[i+2:i+10] if (m:=MEMORY.fullmatch(text)) and m['op'] in {'sh','sb'}]
        if not stores or stores[0]['op']!='sh' or len([m for m in stores if m['op']=='sb' and m['reg']==value[0]])<2:
            continue
        for j,(_,peer) in enumerate(candidate[:-1]):
            if peer!=f'jal {call}':
                continue
            for k in range(j+2,min(j+7,len(candidate)-1)):
                flag,text = candidate[k]
                if _branch(text)[0] in {'beq','beql'} and candidate[k+1][1]!=target[i+1][1] and (
                    flag or candidate[k+1][0] or target[i+1][0]
                ):
                    values.add(value[1])
    return values


def _entry_seed(target,candidate):
    values = set()
    for i,(_,line) in enumerate(target[:-2]):
        cmp = THREE.fullmatch(line)
        if not cmp or cmp['op']!='sltu' or not BRANCH.match(target[i+1][1]):
            continue
        first = next((v for _,text in reversed(target[max(0,i-5):i]) if (v:=_value(text)) and v[0]==cmp['a']),None)
        second = _value(target[i+2][1])
        if not first or not second or not 0<first[1]<256 or first[1]!=second[1] or first[0]==second[0]:
            continue
        if cmp['dst'] not in re.findall(REG,target[i+1][1]) or not target[i+2][0]:
            continue
        if not any((m:=ADD.fullmatch(text)) and m[1]==m[2]==second[0] and _sext16(m[3])==1
                   for _,text in target[i+3:i+30]):
            continue
        for j,(_,text) in enumerate(candidate):
            p = THREE.fullmatch(text)
            if p and p['op']=='sltu' and sum(bool((v:=_value(x)) and v[1]==first[1])
                                           for _,x in candidate[max(0,j-5):j+3])==1:
                values.add(first[1])
    return values


def _ordinary_word(rows):
    for i,(_,left,right) in enumerate(rows[:-1]):
        a,b = MEMORY.fullmatch(left),MEMORY.fullmatch(right)
        if not a or not b or a['op']!=b['op'] or a['op']!='lhu' or a['base']!='$sp' or (
            a['base'],a['offset']
        )!=(b['base'],b['offset']):
            continue
        mark,x,y = rows[i+1]
        mask = THREE.fullmatch(y)
        if (mark!=' ' and not x.startswith('andi ') and mask and mask['op']=='andi'
                and mask['a']==b['reg'] and mask['b'] in {'65535','0xffff'}):
            return True
    return False


def _signed_adjust(rows):
    for i,(_,left,right) in enumerate(rows):
        if not left.startswith(('bltz ','bgez ')) or left!=right:
            continue
        region = rows[i+1:i+10]
        for j,(flag,text,peer) in enumerate(region):
            a,b = ADD.fullmatch(text),ADD.fullmatch(peer)
            if (flag==' ' or not a or not b or a[1]!=a[2] or b[1]==b[2]
                    or _sext16(a[3])!= -1 or _sext16(b[3])!= -1):
                continue
            negation = any((m:=THREE.fullmatch(x)) and m['op']=='subu' and m['a']=='$zero' and m['b']==m['dst']==a[1]
                           and y.startswith('subu ') for _,x,y in region[j+1:j+4])
            store = next((k for k,(_,x,y) in enumerate(region[j+1:],j+1)
                          if (m:=MEMORY.fullmatch(x)) and (n:=MEMORY.fullmatch(y)) and m['op']==n['op']=='sb'
                          and m['reg']==a[1] and (m['offset'],m['base'])==(n['offset'],n['base'])),None)
            if negation and store is not None:
                return True
    return False


def proposals(source,name,header,rows,target,candidate,asm,*,resolve=None):
    evidence = (
        (repairs.nonnull_pointer,_nonnull(target,candidate)),
        (repairs.fallthrough_float,_fallthrough(rows,asm)),
        (repairs.mask_literal,_literals(rows,resolve)),
        (repairs.normalized_mask_or,_mask_or(target,candidate)),
        (repairs.unbiased_cursor,_unbiased(target,candidate,asm)),
        (repairs.embedded_flag,_byte_flag(rows)),
        (repairs.null_immediate,_guard_immediate(target,candidate)),
        (repairs.split_seed,_entry_seed(target,candidate)),
        (repairs.ordinary_halfword,_ordinary_word(rows)),
        (repairs.signed_adjust,_signed_adjust(rows)),
    )
    return repairs.variants(source,name,header,[(method,value) for method,value in evidence if value])
