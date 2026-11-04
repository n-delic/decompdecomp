"""MIPS operand evidence for bounded private source hypotheses, never a match verdict."""
import re

NUMBER = r'-?(?:0x[0-9a-fA-F]+|\d+)'


def operand_changes(rows):
    result=[]
    for i,(mark,target,own) in enumerate(rows):
        if mark==' ':continue
        a=re.fullmatch(r'((?:lb|lbu|lh|lhu|lw|lwc1|ldc1|sb|sh|sw|swc1|sdc1)\s+[^,]+,\s*)('+NUMBER+r')(\(\$[^)]+\))',target)
        b=re.fullmatch(r'((?:lb|lbu|lh|lhu|lw|lwc1|ldc1|sb|sh|sw|swc1|sdc1)\s+[^,]+,\s*)('+NUMBER+r')(\(\$[^)]+\))',own)
        if a and b and (a[1],a[3])==(b[1],b[3]) and '$sp' not in a[3]:
            result.append({'row':i,'kind':'field','old':int(b[2],0),'new':int(a[2],0),'store':target.startswith('s')})
            continue
        a=re.fullmatch(r'((?:sltiu?|addiu|ori|andi|xori)\s+[^,]+,\s*[^,]+,\s*)('+NUMBER+r')',target)
        b=re.fullmatch(r'((?:sltiu?|addiu|ori|andi|xori)\s+[^,]+,\s*[^,]+,\s*)('+NUMBER+r')',own)
        if a and b and a[1]==b[1] and '$sp' not in a[1]:
            result.append({'row':i,'kind':'constant','old':int(b[2],0),'new':int(a[2],0)})
    return result


def blocks(asm, starts, end):
    """Only real target instructions between known jump-table entry addresses."""
    instructions=[]
    for line in asm.splitlines():
        m=re.search(r'/\*\s*[0-9A-Fa-f]+\s+([0-9A-Fa-f]{8})\s+[0-9A-Fa-f]{8}\s*\*/\s*(.+)',line)
        if m:instructions.append((int(m[1],16),m[2].strip()))
    result={}
    boundaries=sorted(set(starts)|{end})
    for a,b in zip(boundaries,boundaries[1:]):
        ins=[text for address,text in instructions if a<=address<b]
        if not ins:continue
        symbols=re.findall(r'\bD_[0-9A-Fa-f]{8}\b','\n'.join(ins))
        # Structural cloning is restricted to terminating arms with the same instructions,
        # ABI registers and constants. Only symbolic data addresses may differ.
        if not any(re.match(r'b\s+|jr\s+\$ra',text) for text in ins):continue
        registers={}
        def reg(m, registers=registers):
            n=m[0]
            if not re.fullmatch(r'\$(?:t[0-9]|s[0-7]|at)',n):return n
            return registers.setdefault(n,'$tmp'+str(len(registers)))
        signature=re.sub(r'\$\w+',reg,'\n'.join(ins))
        signature=re.sub(r'\bD_[0-9A-Fa-f]{8}\b','DATA',signature)
        signature=re.sub(r'\.L[0-9A-Fa-f]+','EXIT',signature)
        result[a]={'symbols':list(dict.fromkeys(symbols)), 'signature':signature,
                   'calls':re.findall(r'\bjal\s+(\w+)','\n'.join(ins))}
    return result


def recovery_hints(asm, rows):
    """Classify evidence for review, not proof of a private ABI or a reason to alter boundaries."""
    result=[]
    text='\n'.join(line.split('*/',1)[-1].strip() for line in asm.splitlines())
    if re.search(r'\bjal\b',text) and re.search(r'\$(?:a[0-3]|t[0-9])',text):
        # A retained argument register after a call without a corresponding reload/copy.
        for m in re.finditer(r'\bjal\s+\w+\n[^\n]*\n',text):
            tail=text[m.end():]
            if re.search(r'\([^)]*\$a3\)',tail) and not re.search(r'\b(?:lw|or|move|addiu)\s+\$a3\b',tail):
                result.append('Possible internal-call ABI: target keeps an argument pointer in $a3 across a call. Try the original translation unit and internal helper definitions; isolated-source permutations may not reproduce compiler clobber knowledge.')
                break
    if '$fa0' in text or '$f12' in text:
        if re.search(r'\blw\s+\$\w+,\s*'+NUMBER+r'\(\$a0\)',text) and not re.search(r'\b(?:mtc1|jal)\b',text):
            result.append('Possible private pointer/float entry ABI: target consumes $a0 and incoming $f12 without a transfer. Check original static helper and translation-unit context before changing the signature.')
    marks={r[0] for r in rows if r[0]!=' '}
    if marks and marks<={'r','s'}:
        result.append('Register/frame-only mismatch: prioritize bounded scheduling/context trials; unrelated retail control-flow edits are unsupported.')
    return result


def tail_branch(asm):
    """One exact terminal conditional-clear/flag-set shape; no arbitrary instruction lifting."""
    lines=[line.split('*/',1)[-1].strip() for line in asm.splitlines() if '*/' in line]
    reg=r'(\$\w+)';off='('+NUMBER+')'
    for i in range(len(lines)-7):
        a=re.fullmatch(r'lw\s+'+reg+r',\s*'+off+r'\('+reg+r'\)',lines[i])
        if not a:continue
        branch=re.fullmatch(r'beql\s+'+re.escape(a[1])+r',\s*\$zero,\s*(\.L[0-9A-Fa-f]+)',lines[i+1])
        if not branch or not re.fullmatch(r'lw\s+\$ra,\s*'+NUMBER+r'\(\$sp\)',lines[i+2]):continue
        clear=re.fullmatch(r'sw\s+\$zero,\s*'+off+r'\('+re.escape(a[3])+r'\)',lines[i+3])
        load=re.fullmatch(r'lw\s+'+reg+r',\s*'+off+r'\('+reg+r'\)',lines[i+4])
        flag=re.fullmatch(r'ori\s+'+reg+r',\s*'+reg+r',\s*'+off,lines[i+5])
        if not clear or not load or not flag or flag[2]!=load[1]:continue
        if not re.fullmatch(r'sw\s+'+re.escape(flag[1])+r',\s*'+re.escape(load[2])+r'\('+re.escape(load[3])+r'\)',lines[i+6]):continue
        # The taken path must be precisely the function epilogue, not another body.
        label=asm.find(branch[1]+':')
        if label<0:continue
        tail=[s.split('*/',1)[-1].strip() for s in asm[label:].splitlines() if '*/' in s]
        if len(tail)!=3 or not re.fullmatch(r'addiu\s+\$sp,\s*\$sp,\s*'+NUMBER,tail[0]) or not re.fullmatch(r'jr\s+\$ra',tail[1]) or tail[2]!='nop':continue
        # Trace only direct argument copies and explicit stack saves/restores through calls.
        origins={f'$a{n}':n for n in range(4)};slots={};pending=False
        for line in lines[:i]:
            prior=pending;pending=False
            move=re.fullmatch(r'or\s+'+reg+r',\s*'+reg+r',\s*\$zero',line)
            save=re.fullmatch(r'sw\s+'+reg+r',\s*'+off+r'\(\$sp\)',line)
            restore=re.fullmatch(r'lw\s+'+reg+r',\s*'+off+r'\(\$sp\)',line)
            if move:origins[move[1]]=origins.get(move[2])
            elif save:slots[int(save[2],0)]=origins.get(save[1])
            elif restore:origins[restore[1]]=slots.get(int(restore[2],0))
            elif re.match(r'jal\s+',line):pending=True
            elif re.match(r'(?:l\w+|addiu|addu|ori|andi|xori|sll)\s+',line):
                dst=re.search(r'\$\w+',line)
                if dst:origins.pop(dst[0],None)
            if prior:
                origins={k:v for k,v in origins.items() if not re.fullmatch(r'\$(?:a[0-3]|t[0-9]|v[01]|at)',k)}
        p,q=origins.get(a[3]),origins.get(load[3])
        values=[int(a[2],0),int(clear[1],0),int(load[2],0),int(flag[3],0)]
        if p is None or q is None or p==q or any(v<0 or v>65535 for v in values) or not values[-1]:continue
        return {'condition_arg':p,'object_arg':q,'test':values[0],'clear':values[1],'flags':values[2],'mask':values[3],
                'instructions':lines[i:i+7]}
    return None


def extra_narrowing(rows):
    """Candidate-only integer normalization; not a claim about the variable's intended type."""
    result=[];extra=[(i,own) for i,(mark,target,own) in enumerate(rows) if mark=='>' and own]
    for i,own in extra:
        m=re.fullmatch(r'andi\s+\$\w+,\s*\$\w+,\s*('+NUMBER+r')',own)
        if m and int(m[1],0) in (255,65535):result.append({'row':i,'width':8 if int(m[1],0)==255 else 16,'unsigned':True})
    for (i,a),(j,b) in zip(extra,extra[1:]):
        left=re.fullmatch(r'sll\s+(\$\w+),\s*\$\w+,\s*('+NUMBER+r')',a)
        right=re.fullmatch(r'(sra|srl)\s+\$\w+,\s*(\$\w+),\s*('+NUMBER+r')',b)
        if left and right and j==i+1 and left[1]==right[2] and left[2]==right[3] and int(left[2],0) in (16,24):
            result.append({'row':i,'width':32-int(left[2],0),'unsigned':right[1]=='srl'})
    return result[:4]


def private_abi(asm, rows):
    """A measured extra transfer plus target incoming pointer/float use warrants context-first search."""
    text='\n'.join(line.split('*/',1)[-1].strip() for line in asm.splitlines())
    # This narrow leaf pattern cannot be repaired by permuting ordinary O32 argument widths.
    transfer=any(mark=='>' and re.fullmatch(r'mtc1\s+\$a[0-3],\s*\$(?:f12|fa0)',own) for mark,_,own in rows)
    from .mips_runtime import incoming_arguments
    incoming=incoming_arguments(asm)
    return_float=re.search(r'\bjr\s+\$ra\n\s*mov\.s\s+\$\w+,\s*\$(?:f12|fa0)\b',text)
    mixed=incoming['mixed'] or ('$a0' in incoming['integer'] and bool(return_float))
    displaced=any(mark!=' ' and '$a0' in target and '$a1' in own and re.match(r'(?:sra|andi|addiu)\s+',target)
                  and target.split()[0]==own.split()[0] for mark,target,own in rows if own and target)
    if (transfer or displaced) and mixed and not re.search(r'\b(?:mtc1|jal|jalr)\b',text):
        return {'kind':'mixed-pointer-float-entry','message':'Measured mixed integer/float entry: prioritize original inline/translation-unit context. Ordinary signature-width/storage trials are deferred; no register binding was invented.'}
    # The target explicitly retains the incoming object in a caller-saved register across
    # multiple calls; a measured mismatch makes this a context lead, not a new ABI definition.
    captured=re.search(r'\bor\s+\$a3,\s*\$a0,\s*\$zero',text)
    if captured and rows:
        tail=text[captured.end():]
        if (len(re.findall(r'\bjal\b',tail))>=2 and re.search(r'\([^)]*\$a3\)',tail)
                and not re.search(r'\b(?:lw|ld|or|move|addiu|addu)\s+\$a3\b',tail)):
            return {'kind':'retained-caller-saved-argument','message':'Target retains an argument in $a3 across multiple calls. Prioritize actual callee definitions and translation-unit clobber context before ordinary signature changes.'}
    if rows:
        # A load through a temporary before any definition, paired with a measured
        # ordinary argument load at the same offset, supports private entry context.
        # Stop at control flow; later paths need a CFG reader, not a lexical guess.
        defined=set()
        for line in text.splitlines():
            if re.match(r'(?:b\w*|j\w*)\s',line):break
            load=re.fullmatch(r'(l[bhw]u?)\s+\$\w+,\s*('+NUMBER+r')\((\$t[0-9])\)',line)
            if load and load[3] not in defined:
                measured=any(mark!=' ' and target.strip()==line and
                             re.fullmatch(re.escape(load[1])+r'\s+\$\w+,\s*'+re.escape(load[2])+r'\(\$a[0-3]\)',own)
                             for mark,target,own in rows)
                if measured:
                    return {'kind':'temporary-register-entry','message':'Target reads an incoming pointer through an undefined temporary register while the candidate uses an ordinary argument at the same offset. Prioritize original whole-unit call/entry context; do not invent a register binding.'}
            if not re.match(r'(?:s[bhdw]|swc1|sdc1)\s',line):
                destination=re.match(r'\w+\s+(\$\w+),',line)
                if destination:defined.add(destination[1])
    if rows:
        for capture in re.finditer(r'\b(?:move|or)\s+(\$t[0-9]),\s*\$a[0-3](?:,\s*\$zero)?\n',text):
            register=capture[1];tail=text[capture.end():]
            call=re.search(r'\bjal\s+\w+\n',tail)
            if not call:continue
            use=re.search(r'\('+re.escape(register)+r'\)',tail[call.end():])
            if not use:continue
            live=tail[:call.end()+use.end()]
            if re.search(r'\b(?:sw|sd)\s+'+re.escape(register)+r',[^\n]*\(\$sp\)',live):continue
            if re.search(r'\b\w+\s+'+re.escape(register)+r',',live):continue
            return {'kind':'retained-caller-saved-temporary','message':'Target keeps an incoming pointer in a caller-saved temporary across a call without a spill/reload. Try actual same-unit callee/clobber context; this does not invent a register binding.'}
    return None


def pointer_snapshot_order(asm):
    """Require the named target pointer load followed by byte stores in the same straight-line region."""
    # The source retains its existing global expression. A target's canonical relocation may
    # render the same load as another symbol plus an addend; this is load-order evidence only.
    for hit in re.finditer(r'\blw\s+[^\n]*%lo\([^)]+\)',asm):
        tail=[]
        for line in asm[hit.end():].splitlines()[1:18]:
            if re.search(r'\b(?:b\w*|j\w*)\s',line):break
            tail.append(line)
        if sum(bool(re.search(r'\bsb\b',line)) for line in tail)>=2:return True
    return False
