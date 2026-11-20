"""Measured MIPS witnesses for targeted scheduling repairs."""

import re
from collections import Counter

from decomprepair import target_schedule_repairs as source_repairs

from .measured_mips_repairs import ADD, BRANCH, MEMORY, NUM, REG, SYMBOL, _sext16, _value, _written

THREE = re.compile(rf'(?P<op>\S+)\s+(?P<dst>{REG}),\s*(?P<a>{REG}),\s*(?P<b>{REG}|{NUM})')
MFC = re.compile(rf'mfc1\s+({REG}),\s*({REG})')
GLOBAL_LOAD = re.compile(rf'(lbu|lb|lhu|lh|lw)\s+({REG}),\s*%lo\(({SYMBOL})\)\(({REG})\)')
FLOAT_STORE = re.compile(rf'swc1\s+({REG}),\s*(.+)')
WORD_STORE = re.compile(rf'sw\s+({REG}),\s*(.+)')


def _offset_initializer(rows):
    if any(not left or not right or re.sub(REG,'_',left)!=re.sub(REG,'_',right) for _,left,right in rows):
        return set()
    counts = set()
    for i,(_,line,_) in enumerate(rows):
        shift = THREE.fullmatch(line)
        if not shift or shift['op']!='sll' or not re.fullmatch(NUM,shift['b']):
            continue
        scale = any((op:=THREE.fullmatch(left)) and op['dst']==shift['a'] and op['op'] in {'addu','sll'}
                    for _,left,_ in rows[max(0,i-4):i])
        if not scale:
            continue
        for j in range(i+2,min(i+7,len(rows)-1)):
            _,sub,_ = rows[j]
            step = ADD.fullmatch(sub)
            load = next((m for _,left,_ in rows[i+1:j] if (m:=MEMORY.fullmatch(left))
                         and m['op']=='lw' and m['base']!='$sp'),None)
            address = THREE.fullmatch(rows[j+1][1])
            if (step and step[2]==shift['dst'] and _sext16(step[3])<0 and load and address
                    and address['op']=='addu' and {address['a'],address['b']}=={step[1],load['reg']}
                    and any(flag!=' ' for flag,_,_ in rows[i:j+2])):
                counts.add(int(shift['b'],0))
    return counts


def _clamp_fallback(rows):
    result = set()
    for i,(flag,left,right) in enumerate(rows):
        a,b = WORD_STORE.fullmatch(left),WORD_STORE.fullmatch(right)
        if flag==' ' or not a or not b or a[2]!=b[2] or a[1]==b[1]:
            continue
        region = rows[max(0,i-22):i]
        if not any(x.startswith(('bc1','bgez','bltz','beq','bne')) and x.split()[0]==y.split()[0]
                   for _,x,y in region if y):
            continue
        if not any((m:=MFC.fullmatch(x)) and m[1]==a[1] for _,x,_ in region):
            continue
        for marker,x,y in region:
            p,q = _value(x),_value(y)
            if marker!=' ' and p and q and p[0]==a[1] and q[0]==b[1] and p[1]==q[1] and p[1] in (0,1):
                result.add(p[1])
    return result


def _branch(text):
    op, _, args = text.partition(' ')
    operands = [part.strip() for part in args.split(',')]
    op = {'beqz':'beq','bnez':'bne','beqzl':'beql','bnezl':'bnel'}.get(op,op)
    if len(operands)==2 and op in {'beq','bne','beql','bnel'}:
        operands.insert(1,'$zero')
    return op,operands


def _shared_zero(rows):
    for i,(flag,a,b) in enumerate(rows[:-1]):
        if flag==' ':
            continue
        left, x = _branch(a)
        right, y = _branch(b)
        if len(x)!=3 or len(y)!=3 or not (left,right) in {
            ('beql','beq'),('beq','beql'),('bnel','bne'),('bne','bnel')
        } or set(x[:2])!=set(y[:2]):
            continue
        _,p,q = rows[i+1]
        value = _value(p)
        if not value or value!=_value(q) or value[1]!=0:
            continue
        before = rows[max(0,i-5):i]
        after = rows[i+2:i+12]
        if (any(line.startswith(('slt ','sltu ','slti ','sltiu ')) and _written(line) in x[:2]
                for _,line,_ in before)
                and any(_branch(line)[0] in {'beq','bne','beql','bnel'} and '$zero' in _branch(line)[1][:2]
                        and line!=a for _,line,_ in after)
                and (value[0]=='$v0' or any(line==f'move $v0, {value[0]}' for _,line,_ in after))):
            return True
    return False


def _selector_byte(target, candidate):
    def sites(code, wanted):
        for i,(changed,line) in enumerate(code):
            byte = GLOBAL_LOAD.fullmatch(line)
            if not byte or byte[1]!='lbu':
                continue
            for j in range(max(0,i-5),min(i+6,len(code))):
                flag,text = code[j]
                half = MEMORY.fullmatch(text)
                if not half or half['op'] not in {'lh','lhu'} or (i<j)!=wanted:
                    continue
                region = code[max(i,j)+1:max(i,j)+11]
                tests = [op for _,op in region if BRANCH.match(op) and byte[2] in re.findall(REG,op)]
                combines = [match for _,op in region if (match:=THREE.fullmatch(op))
                            and match['op'] in {'or','ori'} and half['reg'] in (match['a'],match['b'])]
                if tests and combines and (not wanted or any(m['dst']==byte[2] for m in combines)):
                    yield (byte[3],half['op'],half['offset']), changed or flag

    flipped = {site for site,_ in sites(candidate,False)}
    return {site[0] for site,changed in sites(target,True) if changed and site in flipped}


def _float_boundary(target,candidate):
    if len(target)!=len(candidate):
        return False
    def stores(code):
        return [(i,hit[1],hit[2]) for i,(_,line) in enumerate(code) if (hit:=FLOAT_STORE.fullmatch(line))]
    left,right = stores(target),stores(candidate)
    if len(left)<2 or Counter(m[2] for m in left)!=Counter(m[2] for m in right):
        return False
    return (left[0][2]==right[0][2] and left[0][0]>right[0][0] and left[0][1]!=right[0][1]
            and target[left[0][0]][0] and candidate[right[0][0]][0])


def _signed_mask(rows):
    for i,(flag,left,right) in enumerate(rows[:-1]):
        a,b = THREE.fullmatch(left),THREE.fullmatch(right)
        if (flag==' ' or not a or not b or (a['op'],b['op'])!=('slt','sltu')
                or [a[key] for key in ('dst','a','b')]!=[b[key] for key in ('dst','a','b')]):
            continue
        prefix = rows[max(0,i-8):i]
        mask = any((m:=THREE.fullmatch(x)) and m['op']=='andi' and m['dst']==a['a']
                   and re.fullmatch(NUM,m['b']) and int(m['b'],0)==65535
                   and (n:=THREE.fullmatch(y)) and n['op']=='andi' and n['b']==m['b']
                   for _,x,y in prefix)
        branch = rows[i+1][1]
        if mask and BRANCH.match(branch) and a['dst'] in re.findall(REG,branch):
            return True
    return False


def _previous_strides(target,candidate):
    def scales(code, source):
        values = {source:1}
        for flag,line in code:
            op = THREE.fullmatch(line)
            if not op:
                if out:=_written(line):
                    values.pop(out,None)
                continue
            dst,a,b = (op[k] for k in ('dst','a','b'))
            coefficient = None
            if op['op']=='sll' and a in values and re.fullmatch(NUM,b) and 0<int(b,0)<12:
                coefficient = values[a] << int(b,0)
            if op['op']=='addu' and a in values and b in values:
                coefficient = values[a]+values[b]
            values.pop(dst,None)
            if coefficient:
                values[dst] = coefficient
                yield dst,coefficient,flag

    candidate_sizes = set()
    for i,(_,line) in enumerate(candidate):
        step = ADD.fullmatch(line)
        if not step or not 2<=-_sext16(step[3])<=4096:
            continue
        prefix = candidate[max(0,i-7):i]
        for _,text in prefix:
            shift = THREE.fullmatch(text)
            if not shift or shift['op']!='sll':
                continue
            if any(dst==step[2] and n==-_sext16(step[3]) for dst,n,_ in scales(prefix,shift['a'])):
                candidate_sizes.add(-_sext16(step[3]))
    proven = set()
    for i,(changed,line) in enumerate(target):
        step = ADD.fullmatch(line)
        if not step or _sext16(step[3])!=-1:
            continue
        for dest,n,flag in scales(target[i+1:i+7],step[1]):
            if n in candidate_sizes and (flag or changed) and any(
                (op:=THREE.fullmatch(text)) and op['op']=='addu' and dest in (op['a'],op['b'])
                for _,text in target[i+2:i+10]
            ):
                proven.add(n)
    return proven


def _guarded_numerator(target,candidate):
    def sites(code, early):
        for i,(_,line) in enumerate(code):
            one = re.fullmatch(rf'lui\s+({REG}),\s*(?:0x3f80|16256)',line)
            if not one:
                continue
            window = code[max(0,i-9):i+16]
            transfer = next(((j,m[1]) for j,(_,text) in enumerate(window)
                            if (m:=re.fullmatch(rf'mtc1\s+{re.escape(one[1])},\s*({REG})',text))),None)
            if not transfer:
                continue
            pos,reg = transfer
            guards = [j for j,(_,text) in enumerate(window[:pos]) if text.startswith(('blez ','bgtz '))]
            if not guards:
                continue
            pointers = [j for j,(_,text) in enumerate(window) if j>guards[-1]
                        and text.startswith(('lw ','addu ','addiu '))]
            uses = [j for j,(_,text) in enumerate(window) if (m:=THREE.fullmatch(text))
                    and m['op']=='div.s' and m['a']==reg]
            if uses and any((pos<j)==early and j<uses[0] for j in pointers):
                yield code[i][0] or window[pos][0] or any(window[j][0] for j in pointers)
    return any(sites(target,True)) and any(sites(candidate,False))


def _float_word(rows):
    for i,(flag,left,right) in enumerate(rows):
        store = WORD_STORE.fullmatch(left)
        other = FLOAT_STORE.fullmatch(right)
        if flag==' ' or not store or not other or store[2]!=other[2]:
            continue
        before = rows[max(0,i-8):i]
        transfer = next((m for _,a,_ in reversed(before) if (m:=MFC.fullmatch(a)) and m[1]==store[1]),None)
        if transfer and any((p:=THREE.fullmatch(a)) and (q:=THREE.fullmatch(b))
                            and p['op']==q['op']=='mul.s' and p['dst']==transfer[2] and q['dst']==other[1]
                            for _,a,b in before):
            return True
    return False


def _volatile_record(rows,target,candidate):
    def sites(code):
        matches = []
        totals = Counter(m[3] for _,line in code if (m:=GLOBAL_LOAD.fullmatch(line)) and m[1]=='lw')
        for i,(changed,line) in enumerate(code):
            glob = GLOBAL_LOAD.fullmatch(line)
            if not glob or glob[1]!='lw' or totals[glob[3]]!=1:
                continue
            for flag,text in code[i+1:i+9]:
                op = THREE.fullmatch(text)
                if op and op['op']=='addu' and glob[2] in (op['a'],op['b']):
                    matches.append((glob[3],changed or flag,glob[2]==op['b']))
                    break
        return matches

    if not any(flag!=' ' and (a.startswith('sll ') or b.startswith('sll ')) for flag,a,b in rows):
        return set()
    peers = {symbol for symbol,_,_ in sites(candidate)}
    return {symbol for symbol,changed,offset_first in sites(target)
            if changed and offset_first and symbol in peers}


def proposals(source,name,header,rows,target,candidate,flags):
    evidence = []
    for rewrite,signature in (
        (source_repairs.offset_shift,_offset_initializer(rows)),
        (source_repairs.clamp_fallback,_clamp_fallback(rows)),
        (source_repairs.shared_zero,_shared_zero(rows)),
        (source_repairs.selector,_selector_byte(target,candidate)),
        (source_repairs.float_store_boundary,_float_boundary(target,candidate)),
        (source_repairs.signed_mask,_signed_mask(rows)),
        (source_repairs.previous_record,_previous_strides(target,candidate)),
        (source_repairs.numerator,_guarded_numerator(target,candidate)),
        (source_repairs.float_word,'-fstrict-aliasing' not in flags and _float_word(rows)),
        (source_repairs.volatile_record,_volatile_record(rows,target,candidate)),
    ):
        if signature:
            evidence.append((rewrite,() if signature is True else (signature,)))
    return source_repairs.variants(source,name,header,evidence)
