"""Select conversion and scheduling hypotheses from measured MIPS differences."""

import re
import struct
from collections import Counter

from decomprepair import target_conversion_order_repairs as repairs

from .measured_mips_repairs import BRANCH, CALL, MEMORY, NUM, REG, SYMBOL, TRANSFER, _value, labelled_code
from .operand_and_edge_search import FPR

XOR = re.compile(rf'xori\s+({REG}),\s*({REG}),\s*({NUM})')
NONZERO = re.compile(rf'sltu\s+({REG}),\s*\$zero,\s*({REG})')
CONVERT = re.compile(rf'trunc\.w\.s\s+({FPR}),\s*({FPR})')
MFC = re.compile(rf'mfc1\s+({REG}),\s*({FPR})')
FOP = re.compile(rf'(?:mul|div)\.([sd])\s+({FPR}),\s*({FPR}),\s*({FPR})')
SAVED_FP = re.compile(r'\$(?:fs\d+|f(?:2[0-9]|3[01]))')
SHIFT = re.compile(rf'srl\s+({REG}),\s*({REG}),\s*({NUM})')
RELOC = re.compile(rf'(?:ldc1|lb|lbu|addiu)\s+({REG}),\s*%lo\(({SYMBOL})\)\(({REG})\)')


def _inequalities(target, candidate, asm):
    if not asm or not any(changed and (XOR.fullmatch(line) or NONZERO.fullmatch(line) or _value(line))
               for changed,line in candidate):
        return set()
    instructions, labels = labelled_code(asm)
    code = [' '.join(item).strip() for item in instructions]
    changed = {line for flag,line in target if flag}

    def boolean(start, end):
        for index in range(start,end-1):
            xor = XOR.fullmatch(code[index])
            test_index = index+1
            if code[test_index].startswith(('b ','j ')):
                test_index += 1
            if test_index>=end:
                continue
            test = NONZERO.fullmatch(code[test_index])
            if xor and test and xor[1]==test[2]:
                yield (xor[2],int(xor[3],0)), bool({code[index],code[test_index]}&changed)

    result = set()
    for i,line in enumerate(code):
        if not BRANCH.match(line):
            continue
        alternative = labels.get(line.rsplit(',',1)[-1].strip(),-1)
        if not i+3<alternative<=min(i+20,len(code)):
            continue
        for j in range(i+2,alternative):
            callee = CALL.fullmatch(code[j])
            if not callee or not any(line==code[j] for _,line in candidate):
                continue
            join = next((labels.get(dest,-1) for op,dest in instructions[j+2:alternative] if op in {'b','j'}),-1)
            if not alternative+1<join<=min(alternative+12,len(code)):
                continue
            first = dict(boolean(i+2,alternative))
            other = dict(boolean(alternative,join))
            for key in first.keys() & other.keys():
                if first[key] or other[key]:
                    result.add((callee[1],key[1]))
    return result


def _argument_positions(rows):
    positions = set()
    conversions = set()
    for marker,left,right in rows:
        first,second = CONVERT.fullmatch(left),CONVERT.fullmatch(right)
        if first and second:
            conversions.add((first[1],second[1]))
        a,b = MFC.fullmatch(left),MFC.fullmatch(right)
        if not (a and b and (a[2],b[2]) in conversions):
            continue
        conversions.remove((a[2],b[2]))
        if marker!=' ' and re.fullmatch(r'\$a[0-3]',a[1]) and not re.fullmatch(r'\$a[0-3]',b[1]):
            positions.add(int(a[1][-1]))
    if len(positions)>=2 and any(marker!=' ' and a.startswith(('sll ','or ')) and a.split()[0]==b.split()[0]
                                 for marker,a,b in rows if b):
        return tuple(sorted(positions))
    return ()


def _product_precision(rows):
    # A closed transposition must explain every operand difference, including
    # all use sites. Identical opcodes alone also allow unrelated live ranges.
    pairs = set()
    products = []
    for marker,left,right in rows:
        if marker==' ':
            continue
        x,y = re.findall(REG,left), re.findall(REG,right)
        if len(x)!=len(y) or not left or not right or re.sub(REG,'_',left)!=re.sub(REG,'_',right):
            return None
        pairs.update((a,b) for a,b in zip(x,y) if a!=b)
        if left.startswith('mul.') and (match:=FOP.fullmatch(left)) and x[0]!=y[0]:
            products.append(match)
    regs = {x for pair in pairs for x in pair}
    if len(regs)!=2 or pairs!={(a,b) for a in regs for b in regs if a!=b} or not all(
        re.fullmatch(FPR,r) for r in regs
    ):
        return None
    if len(products)!=2:
        return None
    first,second = products
    if first[1]==second[1] and {first[2],second[2]}==regs and {first[3],first[4]}&{second[3],second[4]}:
        return first[1]
    return None


def _scale_constants(region, source):
    """Read the low-word scaled quotient built from the result of mfhi."""
    values = {source:1}
    literals = {}
    for _,line in region:
        op, _, operands = line.partition(' ')
        args = [v.strip() for v in operands.split(',')]
        dst = args[0]
        value = None
        if TRANSFER.match(line):
            break
        if immediate := _value(line):
            literals[immediate[0]] = immediate[1]
        if len(args)==3:
            _, a,b = args
            if op=='subu' and b in values and a not in values:
                yield dst,values[b]
            if op=='sll' and a in values and re.fullmatch(NUM,b) and 0<=int(b,0)<16:
                value = values[a] << int(b,0)
            if op in {'addu','subu'} and a in values and b in values:
                value = values[a] + (values[b] if op=='addu' else -values[b])
            if op=='mul' and a in values and b in literals:
                value = values[a] * literals[b]
        values.pop(dst,None)
        if value is not None:
            values[dst] = value


def _switch_remainder(target, candidate):
    def sites(code):
        for i,(_,line) in enumerate(code):
            high = re.fullmatch(rf'mfhi\s+({REG})',line)
            if not high:
                continue
            early = code[max(0,i-8):i]
            if not any(text.startswith(('mult ','multu ')) for _,text in early):
                continue
            region = code[i+1:i+14]
            divisors = dict(_scale_constants(region,high[1]))
            for j,(changed,text) in enumerate(region):
                sub = re.fullmatch(rf'subu\s+({REG}),\s*({REG}),\s*({REG})',text)
                if not sub or sub[1] not in divisors:
                    continue
                rem = sub[1]
                bound = next(((flag,match[1]) for flag,op in region[j+1:j+4]
                              if (match:=re.fullmatch(rf'sltiu\s+{REG},\s*{re.escape(rem)},\s*({NUM})',op))),None)
                index = any(re.fullmatch(rf'sll\s+{REG},\s*{re.escape(rem)},\s*(?:2|0x2)',op)
                            for _,op in region[j+2:j+8])
                if bound and index:
                    yield divisors[rem],int(bound[1],0),rem,changed or bound[0]

    other = {(d,n):reg for d,n,reg,_ in sites(candidate)}
    return {d for d,n,reg,changed in sites(target) if changed and 1<d<=65535
            and (d,n) in other and reg!=other[d,n]}


def _ido_reload(target, candidate):
    def sites(code, target_order):
        for i,(_,line) in enumerate(code):
            call = CALL.fullmatch(line)
            if not call:
                continue
            if target_order:
                if i+1>=len(code):
                    continue
                dependent = MEMORY.fullmatch(code[i+1][1])
                if not dependent or dependent['op']!='sw':
                    continue
            else:
                dependent = next((m for _,text in code[max(0,i-4):i+2] if (m:=MEMORY.fullmatch(text))
                                  and m['op']=='sw' and m['reg']!='$zero' and m['base']!='$sp'),None)
                if not dependent:
                    continue
            region = code[max(0,i-7):i]
            for j,(_,text) in enumerate(region):
                reload = MEMORY.fullmatch(text)
                if not reload or (reload['op'],reload['reg'],reload['base'])!=('lw',dependent['reg'],'$sp'):
                    continue
                for flag,op in (region[j+1:] if target_order else region[:j]):
                    clear = MEMORY.fullmatch(op)
                    if clear and clear['op']=='sw' and clear['reg']=='$zero' and (
                        clear['base']==dependent['base'] and clear['offset']!=dependent['offset']
                    ):
                        yield (call[1],clear['offset'],dependent['offset'],reload['offset']),flag or region[j][0]
    peers = {key for key,_ in sites(candidate,False)}
    return {key[0] for key,changed in sites(target,True) if changed and key in peers}


def _reciprocal(rows):
    for i,(marker,left,right) in enumerate(rows):
        a,b = FOP.fullmatch(left),FOP.fullmatch(right)
        if not a or not b or marker==' ' or not left.startswith('div.s ') or not right.startswith('div.s '):
            continue
        if a[1]=='s' and a[2]==a[4] and SAVED_FP.fullmatch(a[2]) and not SAVED_FP.fullmatch(b[2]):
            if any(x.startswith('mul.s ') and a[2] in re.findall(FPR,x)[1:]
                   and y.startswith('mul.s ') and b[2] in re.findall(FPR,y)[1:]
                   for _,x,y in rows[i+1:i+35]):
                return True
    return False


def _conversion_index(target, candidate):
    def sites(code, before):
        for i,(_,line) in enumerate(code):
            conversion = re.fullmatch(rf'cvt\.s\.w\s+({FPR}),\s*{FPR}',line)
            if not conversion:
                continue
            region = code[max(0,i-4):i+9]
            correction = next((j for j,(flag,text) in enumerate(region) if text.startswith('add.s ')
                               and conversion[1] in re.findall(FPR,text)),None)
            if correction is None:
                continue
            for j in range(1,len(region)):
                flag,text = region[j]
                shift = SHIFT.fullmatch(text)
                if not shift or not ((j<correction and BRANCH.match(region[j-1][1])) if before else j>correction):
                    continue
                if any(next_.startswith(('sll ','addu ')) and shift[1] in re.findall(REG,next_)[1:]
                       for _,next_ in region[j+1:]+code[i+9:i+20]):
                    yield int(shift[3],0), flag or region[j-1][0]
    late = {n for n,_ in sites(candidate,False)}
    return {n for n,changed in sites(target,True) if changed and n in late}


def _strided_table(target,candidate):
    saved = lambda code:{m['reg'] for _,text in code if (m:=MEMORY.fullmatch(text)) and
                         m['op']=='sw' and m['base']=='$sp' and re.fullmatch(r'\$s[0-7]',m['reg'])}
    extra = saved(candidate)-saved(target)
    if not extra:
        return set()
    indexed = Counter()
    carried = set()
    for code in (target,candidate):
        for i,(changed,line) in enumerate(code):
            load = RELOC.fullmatch(line)
            if load and line.startswith('lb ') and any((jump:=re.fullmatch(rf'addu\s+({REG}),\s*{REG},\s*{REG}',text))
                                                       and jump[1]==load[3] for _,text in code[max(0,i-4):i]):
                if code is target:
                    indexed[load[2]] += 1
            elif load and line.startswith('addiu ') and load[1] in extra and code is candidate:
                if any((m:=MEMORY.fullmatch(text)) and m['op']=='lb' and m['base']==load[1]
                       for _,text in code[i+1:i+40]) and any(flag and BRANCH.match(op) for flag,op in code[i+1:i+40]):
                    carried.add(load[2])
    return {s for s,n in indexed.items() if n==2 and s in carried}


def _signed_high_word(rows):
    for i,(marker,left,right) in enumerate(rows):
        if marker==' ' or not left.startswith('slt ') or not right.startswith('sltu '):
            continue
        reg = re.findall(REG,left)
        if len(reg)!=3 or reg!=re.findall(REG,right):
            continue
        before = [line for _,line,_ in rows[max(0,i-9):i]]
        after = [line for _,line,_ in rows[i+1:i+8]]
        if (sum(op.startswith('subu ') for op in before)>=2 and any(op.startswith('sltu ') for op in before)
                and any(op.startswith('sltu ') and op.split()[1].rstrip(',')==reg[0] for op in after)
                and any(BRANCH.match(op) and set(reg[1:])<=set(re.findall(REG,op)) for op in after)):
            return True
    return False


def _bias_symbols(rows, read_data, big_endian):
    if read_data is None:
        return set()
    result = set()
    for i,(marker,left,right) in enumerate(rows):
        load = RELOC.fullmatch(left)
        if marker==' ' or not load or not left.startswith('ldc1 ') or not re.search(
            r'%lo\(\.',right
        ):
            continue
        before = [op for _,op,_ in rows[max(0,i-7):i]]
        after = [op for _,op,_ in rows[i+1:i+5]]
        if (any(op.startswith('cvt.d.w ') for op in before) and any(BRANCH.match(op) for op in before)
                and any(op.startswith('add.d ') and load[1] in re.findall(FPR,op)[1:] for op in after)):
            raw = read_data(load[2],8)
            if raw == struct.pack('>d' if big_endian else '<d',4294967296.0):
                result.add(load[2])
    return result


def proposals(source, name, header, toolchain, rows, target, candidate, asm, *, read_data=None, big_endian=False):
    if toolchain.startswith('ido-5.3'):
        callees = _ido_reload(target,candidate)
        evidence = [(repairs.ido_store,(callees,))] if callees else []
    else:
        evidence = []
        for repair, signature in (
            (repairs.inequality,_inequalities(target,candidate,asm)),
            (repairs.argument_carriers,_argument_positions(rows)),
            (repairs.float_products,_product_precision(rows)),
            (repairs.modulo_selector,_switch_remainder(target,candidate)),
            (repairs.early_index,_conversion_index(target,candidate)),
            (repairs.table_offset,_strided_table(target,candidate)),
            (repairs.named_bias,_bias_symbols(rows,read_data,big_endian)),
        ):
            if signature:
                evidence.append((repair,(signature,)))
        if _reciprocal(rows):
            evidence.append((repairs.reciprocal,()))
        if _signed_high_word(rows):
            evidence.append((repairs.signed_bound,()))
    return repairs.variants(source,name,header,evidence)
