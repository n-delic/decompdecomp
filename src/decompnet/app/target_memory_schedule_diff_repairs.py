"""Route measured MIPS memory and scheduling mismatches to source candidates."""

import re

from decomprepair import balanced_switch_tail
from decomprepair import target_memory_schedule_repairs as repairs

from .measured_mips_repairs import BRANCH, MEMORY, NUM, REG, SYMBOL, TRANSFER, _value, labelled_code
from .target_schedule_diff_repairs import THREE

GLOBAL = re.compile(rf'lw\s+({REG}),\s*%lo\(({SYMBOL})\)\(({REG})\)')
HALF_MASK = re.compile(rf'andi\s+({REG}),\s*\$zero,\s*(?:65535|0xffff)')


def _offset(memory):
    return int(memory['offset'] or '0',0)


def _middle_reload(rows,target,candidate):
    differences = [i for i,(mark,_,_) in enumerate(rows) if mark!=' ']
    if not differences or any(not a or not b or re.sub(REG,'_',a)!=re.sub(REG,'_',b)
                              for mark,a,b in rows if mark!=' '):
        return set()
    if len(target)!=len(candidate):
        return set()
    loads = {}
    for i,(_,line) in enumerate(target):
        if hit:=GLOBAL.fullmatch(line):
            loads.setdefault(hit[2],[]).append((i,hit[1]))
    result = set()
    for symbol,sites in loads.items():
        if len(sites)!=3:
            continue
        (first,_),(middle,reg),(last,_) = sites
        if not all(first<=i<middle for i in differences):
            continue
        region = [line for _,line in target[middle+1:last]]
        for j,text in enumerate(region):
            word = MEMORY.fullmatch(text)
            if not word or word['op']!='lw' or word['base']!=reg:
                continue
            for k,after in enumerate(region[j+1:j+4],j+1):
                mask = THREE.fullmatch(after)
                if not mask or mask['op']!='ori' or mask['a']!=word['reg']:
                    continue
                if any((m:=MEMORY.fullmatch(op)) and m['op']=='sw' and m['base']==reg and
                       m['reg']==mask['dst'] and _offset(m)==_offset(word) for op in region[k+1:k+4]):
                    result.add((symbol,_offset(word)))
    return result


def _zero_chain(target,candidate):
    def runs(code):
        stores = [(i,flag,m) for i,(flag,line) in enumerate(code) if (m:=MEMORY.fullmatch(line)) and m['op'] in {'sh','sw'}]
        for a,b,c in zip(stores,stores[1:],stores[2:]):
            ops = (a[2],b[2],c[2])
            if (tuple(m['op'] for m in ops)!=('sh','sw','sh') or len({m['base'] for m in ops})!=1
                    or c[0]-a[0]>12):
                continue
            ranges = sorted((_offset(m),_offset(m)+n) for m,n in zip(ops,(2,4,2)))
            if any(x[1]>y[0] for x,y in zip(ranges,ranges[1:])):
                continue
            yield tuple(map(_offset,ops)),a,b,c

    restored = set()
    for key,a,b,c in runs(target):
        zero = next((m[1] for flag,line in target[max(0,a[0]-3):c[0]]
                     if (m:=HALF_MASK.fullmatch(line)) and m[1]==c[2]['reg'] and flag),None)
        if not zero or any(m['reg'] not in {zero,'$zero'} for _,_,m in (a,b,c)):
            continue
        for peer,x,y,z in runs(candidate):
            if peer==key and any(flag and (m:=MEMORY.fullmatch(line)) and m['op']=='lh'
                                 and m['base']==x[2]['base'] and _offset(m)==peer[0] and
                                 m['reg'] in (y[2]['reg'],z[2]['reg'])
                                 for flag,line in candidate[x[0]+1:z[0]]):
                restored.add(key)
    return restored


def _stack_masks(target,candidate):
    def sites(code,loads_first):
        for i,(flag,line) in enumerate(code):
            high = re.fullmatch(rf'lui\s+({REG}),\s*({NUM})',line)
            if not high:
                continue
            for j in range(i+1,min(i+4,len(code))):
                low = THREE.fullmatch(code[j][1])
                if not low or low['op']!='ori' or low['a']!=high[1] or not re.fullmatch(NUM,low['b']):
                    continue
                value = ((int(high[2],0)&65535)<<16)|(int(low['b'],0)&65535)
                if value<=65535:
                    continue
                for k in (range(max(i-4,0),i) if loads_first else range(j+1,min(j+5,len(code)))):
                    load = MEMORY.fullmatch(code[k][1])
                    if not load or load['op']!='lw' or load['base']!='$sp':
                        continue
                    for changed,text in code[max(j,k)+1:max(j,k)+5]:
                        m = THREE.fullmatch(text)
                        if m and m['op']=='and' and {m['a'],m['b']}=={low['dst'],load['reg']}:
                            yield (value,_offset(load)),flag or changed or code[k][0]
    peer = {key for key,_ in sites(candidate,False)}
    return {key[0] for key,changed in sites(target,True) if key in peer and changed}


def _float_loads(target,candidate):
    def sites(code,first):
        for i,(flag,line) in enumerate(code):
            load = MEMORY.fullmatch(line)
            if not load or load['op']!='lwc1':
                continue
            region = code[i+1:i+9]
            other = next(((j,m) for j,(_,text) in enumerate(region) if (m:=MEMORY.fullmatch(text))
                          and m['op']=='lwc1' and m['base']==load['base'] and _offset(m)!=_offset(load)),None)
            if not other:
                continue
            j,second = other
            adds = [(k,m) for k,(_,text) in enumerate(region) if (m:=THREE.fullmatch(text)) and m['op']=='add.s']
            for k,a in adds:
                if (k>j)!=first or load['reg'] not in (a['a'],a['b']):
                    continue
                remaining = [(z,b) for z,b in adds if z>max(k,j) and second['reg'] in (b['a'],b['b'])]
                for z,b in remaining:
                    operands = (set((a['a'],a['b']))-{load['reg']},set((b['a'],b['b']))-{second['reg']})
                    if not all(len(item)==1 for item in operands):
                        continue
                    constants = tuple(next(iter(item)) for item in operands)
                    if first or any(text=='nop' for _,text in region[:j+1]):
                        key = (load['base'],_offset(load),_offset(second),constants)
                        yield key,flag or any(changed for changed,_ in region[:z+1])
    return bool({key for key,changed in sites(target,True) if changed} & {key for key,_ in sites(candidate,False)})


def _separate_stores(target,candidate):
    def sites(code,ordered):
        for i,(flag,line) in enumerate(code):
            first = MEMORY.fullmatch(line)
            if not first or first['op'] not in {'sb','sh','sw'} or first['base']=='$sp':
                continue
            items = code[max(0,i-2):i+10]
            stores = [(j,m) for j,(_,text) in enumerate(items) if (m:=MEMORY.fullmatch(text))
                      and m['op'] in {'sb','sh','sw'} and m['base']==first['base']]
            if len(stores)!=3 or _offset(stores[0][1])!=_offset(first) or len({_offset(m) for _,m in stores})!=3:
                continue
            math = [j for j,(_,text) in enumerate(items) if text.startswith(('addiu ','addu ','subu '))]
            interior = [j for j in math if stores[0][0]<j<stores[1][0]]
            if (ordered and interior and stores[2][0]==stores[1][0]+1) or (
                not ordered and any(j<stores[0][0] or stores[1][0]<j<stores[2][0] for j in math)
            ):
                yield tuple((m['op'],_offset(m),m['reg']) for _,m in stores),flag or any(x for x,_ in items)
    return bool({key for key,changed in sites(target,True) if changed} & {key for key,_ in sites(candidate,False)})


def _post_copy(target,candidate):
    def sites(code,after):
        for i,(flag,line) in enumerate(code):
            m = THREE.fullmatch(line)
            if not m or m['op']!='andi' or m['b'] not in {'0xffff','65535'}:
                continue
            region = code[max(0,i-16):i] if after else code[i+1:i+17]
            copies = [(j,l) for j,(_,text) in enumerate(region) if (l:=MEMORY.fullmatch(text))
                      and l['op'] in {'lw','sw'}]
            for trip in (copies[j:j+4] for j in range(len(copies)-3)):
                a,b,c,d = (record for _,record in trip)
                if (a['op']==b['op']=='lw' and c['op']==d['op']=='sw' and
                    {a['reg'],b['reg']}=={c['reg'],d['reg']} and a['base']==b['base'] and c['base']==d['base'] and
                    abs(_offset(a)-_offset(b))==abs(_offset(c)-_offset(d))==4):
                    yield (a['base'],c['base'],_offset(a),_offset(c)),flag
    return bool({key for key,changed in sites(target,True) if changed} & {key for key,_ in sites(candidate,False)})


def _default_shift(rows,asm):
    code,labels = labelled_code(asm or '')
    choices = set()
    for i,(flag,first,second) in enumerate(rows[:-1]):
        m = re.fullmatch(r'(?:j|b)\s+(\S+)',first)
        if not m or not second.startswith(('j ','b ')) or m[1] not in labels:
            continue
        changed,delay,other = rows[i+1]
        shift = THREE.fullmatch(delay)
        if (changed==' ' or not shift or shift['op']!='sll' or not re.fullmatch(NUM,shift['b'])
                or not 0<int(shift['b'],0)<32 or other.startswith('sll ')):
            continue
        actual = next((j for j,(op,args) in enumerate(code[:-1])
                       if f'{op} {args}'==first and ' '.join(code[j+1])==delay),None)
        if actual is None or not any((v:=_value(' '.join(pair))) and v==(shift['a'],0)
                                    for pair in code[max(0,actual-4):actual]):
            continue
        if any(line==delay for _,line,_ in rows[:i]+rows[i+2:]):
            choices.add(int(shift['b'],0))
    return choices


def _packed_word(target,candidate,big_endian):
    groups = []
    for i in range(len(target)-3):
        quad = [MEMORY.fullmatch(text) for _,text in target[i:i+4]]
        if not all(quad) or tuple(m['op'] for m in quad)!=('lwl','lwr','swl','swr'):
            continue
        a,b,c,d = quad
        delta = 3 if big_endian else -3
        if (len({m['reg'] for m in quad})!=1 or a['base']!=b['base'] or c['base']!=d['base']
                or _offset(b)-_offset(a)!=delta or _offset(d)-_offset(c)!=delta
                or not any(flag for flag,_ in target[i:i+4])):
            continue
        groups.append((a['base'],min(_offset(a),_offset(b)),c['base'],min(_offset(c),_offset(d))))
    # Adjacent 12-byte copies already have an aggregate repair.
    result = set()
    for a,x,b,y in groups:
        if any((a,x+step,b,y+step) in groups for step in (-4,4)):
            continue
        for i,(_,text) in enumerate(candidate[:-1]):
            left,right = MEMORY.fullmatch(text),MEMORY.fullmatch(candidate[i+1][1])
            if (left and right and left['op']=='lw' and right['op']=='sw' and left['reg']==right['reg'] and
                (left['base'],_offset(left),right['base'],_offset(right))==(a,x,b,y)):
                result.add((x,y))
    return result


def _inline_order(rows,asm):
    code,labels = labelled_code(asm or '')
    if not any(BRANCH.match(op+' ') and labels.get(args.rsplit(',',1)[-1].strip(),i)<i for i,(op,args) in enumerate(code)):
        return set()
    if any(line.startswith(('jal ','jalr ')) for _,line,_ in rows):
        return set()
    changes = [i for i,(mark,_,_) in enumerate(rows) if mark!=' ']
    if not changes or changes[-1]>18:
        return set()
    stop = min((i for i,(_,a,_) in enumerate(rows) if MEMORY.fullmatch(a)),default=len(rows))
    if changes[-1]>=stop:
        return set()

    def preheader(side):
        values = []
        addr = None
        for row in rows[:stop]:
            text = row[side]
            if re.fullmatch(rf'lui\s+{REG},\s*%hi\({SYMBOL}\)',text):
                continue
            if m:=re.fullmatch(rf'addiu\s+{REG},\s*{REG},\s*%lo\(({SYMBOL})\)',text):
                if addr:
                    return None,()
                addr = m[1]
                values.append(None)
            elif literal:=_value(text):
                values.append(literal[1])
            elif text and text!='nop':
                return None,()
        return addr,tuple(values)
    a,x = preheader(1)
    b,y = preheader(2)
    if a and a==b and len(x)==4 and x!=y and len(set(x))==4 and set(x)==set(y):
        return {x}
    return set()


def _null_epilogue(rows,asm):
    code,labels = labelled_code(asm or '')
    for i,(flag,left,right) in enumerate(rows):
        if flag==' ' or not left.startswith('bnez ') or not right.startswith('beql '):
            continue
        branch = left.rsplit(',',1)[-1].strip()
        success = labels.get(branch,-1)
        if success<0:
            continue
        assembly_row = next((j for j,(op,args) in enumerate(code) if f'{op} {args}'==left),None)
        if assembly_row is None or not assembly_row+3<success<=assembly_row+8:
            continue
        for j in range(assembly_row+2,success-1):
            op,label = code[j]
            exit_ = labels.get(label,-1)
            if (op not in {'b','j'} or exit_<=success or not i<len(rows)-1
                    or _value(' '.join(code[j+1]))!=('$v0',0)):
                continue
            if any(name=='jr' and args=='$ra' for name,args in code[exit_:exit_+7]) and not any(
                TRANSFER.match(f'{name} {args}') for name,args in code[success:exit_]
            ):
                return True
    return False


def _indexed_load_order(rows):
    """Look for the same indexed word load moving across arithmetic."""
    left = [(i,MEMORY.fullmatch(a)) for i,(mark,a,_) in enumerate(rows) if mark!=' ']
    right = [(i,GLOBAL.fullmatch(b)) for i,(mark,_,b) in enumerate(rows) if mark!=' ']
    for i,a in left:
        if not a or a['op']!='lw' or a['base']=='$sp':
            continue
        for j,b in right:
            if not b or (a['reg'],a['base'])!=(b[1],b[3]) or i>=j:
                continue
            if any(mark==' ' and re.match(r'(?:sll|addu|subu)\s',x)
                   for mark,x,_ in rows[i+1:j]):
                return True
    return False


def proposals(source,name,header,toolchain,rows,target,candidate,asm,*,big_endian=False,flags=()):
    if toolchain.startswith('ido-5.3'):
        evidence = [(repairs.zero_chain,_zero_chain(target,candidate))]
    else:
        evidence = [
            (repairs.middle_reload,_middle_reload(rows,target,candidate)),
            (repairs.stack_mask,_stack_masks(target,candidate)),
            (repairs.float_loads,_float_loads(target,candidate)),
            (repairs.store_scopes,_separate_stores(target,candidate)),
            (repairs.post_copy_narrow,_post_copy(target,candidate)),
            (repairs.default_scale,_default_shift(rows,asm)),
            (repairs.packed_word,_packed_word(target,candidate,big_endian) if '-fstrict-aliasing' not in flags else set()),
            (repairs.inline_invariants,_inline_order(rows,asm)),
            (repairs.null_epilogue,_null_epilogue(rows,asm)),
            (repairs.indexed_load_before_arithmetic,_indexed_load_order(rows) if toolchain.startswith('gcc-2.7.2-kmc') else False),
        ]
    result = repairs.variants(source,name,header,[(method,witness) for method,witness in evidence if witness])
    if toolchain.startswith('gcc-2.7.2-kmc'):
        result += balanced_switch_tail.variants(source, name, rows, header=header)
    return result
