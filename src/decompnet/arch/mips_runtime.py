"""Instruction evidence for small O32 runtime helpers and compiler-specialized MIPS calls."""
import re


def instructions(asm):
    return [line.split('*/',1)[-1].strip() for line in (asm or '').splitlines()
            if re.match(r'^(?:/\*.*?\*/\s*)?[a-z][a-z0-9.]*(?:\s|$)',line.strip())]


def helper(asm):
    code=instructions(asm)
    ops={line.split()[0] for line in code}
    if len(code)>64 or ops & {'jal','jalr','bal'}:
        return None
    if {'ddiv','mfhi','daddu','bgez','bgtz','blez','ld','sd'} <= ops:
        # O32 arguments are paired 32-bit words in the home slots, not N32/N64 registers.
        if all(re.search(r'\bsw\s+\$'+reg+r',',asm) for reg in ('a0','a1','a2','a3')):
            return 'signed_mod64'
    if {'cvt.l.d','ctc1','cfc1','dmfc1','dsll32','dsra32'} <= ops:
        if any(re.match(r'dsra32\s+\$v0,',line) for line in code) and any(re.match(r'dsra32\s+\$v1,',line) for line in code):
            return 'double_to_unsigned64'
    return None


def specialized_calls(asm):
    """Advisory only: a temporary live across a call may reflect whole-unit clobber analysis."""
    code=instructions(asm)
    for i,line in enumerate(code):
        if not re.match(r'jal\s+',line):
            continue
        # Require a prior definition and a later read before any redefinition, with no intervening
        # call or explicit spill/reload. Delay-slot definitions do not count as preserved values.
        for reg in (f'$t{n}' for n in range(10)):
            before=code[max(0,i-32):i]
            last_call=next((j for j in range(len(before)-1,-1,-1) if before[j].startswith(('jal ','jalr '))),-1)
            before=before[last_call+1:]
            if any(re.match(r's[wd]\s+'+re.escape(reg)+r',',x) for x in before):
                continue
            if not any(re.match(r'[a-z][a-z0-9.]*\s+'+re.escape(reg)+r',',x) and not x.startswith(('sw ','sd ','sb ','sh ')) for x in before):
                continue
            if re.search(re.escape(reg)+r'\b',code[i+1] if i+1<len(code) else ''):
                continue
            for after in code[i+2:i+12]:
                if after.startswith(('jal ','jalr ','jr ')):
                    break
                if re.match(r'[a-z][a-z0-9.]*\s+'+re.escape(reg)+r',',after) and not after.startswith(('sw ','sd ','sb ','sh ')):
                    break
                if re.search(re.escape(reg)+r'\b',after):
                    return True
    return False


def candidate(asm, name):
    """Small generic arithmetic hypotheses; no SDK/game source and no match verdict."""
    if not re.fullmatch(r'[A-Za-z_]\w*',name):
        return None
    kind=helper(asm)
    if kind=='signed_mod64':
        return ('long long '+name+'(long long a, long long b)\n{\n long long r;\n r=a%b;\n'
                ' if((r<0 && b>0)||(r>0 && b<0)) r+=b;\n return r;\n}\n')
    if kind=='double_to_unsigned64':
        return 'unsigned long long '+name+'(double value)\n{\n return (unsigned long long)value;\n}\n'
    return None


def entry_context(asm):
    """Conservative read-before-write leads, not a calling-convention verdict."""
    defined=set();saved=set();floating=False;pointer=False
    for line in instructions(asm)[:48]:
        if line.startswith(('jal ','jalr ','jr ')):break
        match=re.match(r'(\w+(?:\.\w+)?)\s+(.+)',line)
        if not match:continue
        op,args=match.groups();regs=re.findall(r'\$[a-z][a-z0-9]*',args)
        if not regs:continue
        store=op in {'sw','sd','sh','sb','swc1','sdc1'}
        branch=op.startswith('b')
        reads=regs if store or branch else regs[1:]
        # Saving a caller's saved register is normal; using it as an address is evidence.
        if store and len(regs)>1 and regs[-1]=='$sp':reads=[]
        saved.update(r for r in reads if re.fullmatch(r'\$s[0-7]',r) and r not in defined)
        floating |= '$f12' in reads and '$f12' not in defined
        pointer |= '$a0' in reads and '$a0' not in defined and '(' in args
        if not store and not branch:defined.add(regs[0])
    return saved, floating and pointer


def incoming_arguments(asm):
    """Read-before-write incoming argument evidence, including spimdisasm's floating aliases."""
    aliases={'fa0':'f12','fa1':'f14','fv0':'f0','fv1':'f2',
             **{f'ft{i}':f'f{n}' for i,n in enumerate((4,6,8,10,16,18))},
             **{f'fs{i}':f'f{20+2*i}' for i in range(6)}}
    seen=set();integer=set();floating=set();frame=0;stack_slots=set();written=set()
    for original in instructions(asm)[:64]:
        line=re.sub(r'\$(\w+)',lambda m:'$'+aliases.get(m[1],m[1]),original)
        if line.startswith(('jal ','jalr ','jr ')):break
        match=re.match(r'(\w+(?:\.\w+)?)\s+(.+)',line)
        if not match:continue
        op,args=match.groups();regs=re.findall(r'\$[a-z][a-z0-9]*',args)
        if not regs:continue
        if m:=re.fullmatch(r'addiu\s+\$sp,\s*\$sp,\s*(-0x[\da-fA-F]+|-\d+)',line):frame=-int(m[1],0)
        slot=re.search(r'(-?0x[\da-fA-F]+|-?\d+)\(\$sp\)',args)
        store=op in {'sw','sd','sh','sb','swc1','sdc1'};branch=op.startswith(('b','c.'))
        to_float=op in {'mtc1','dmtc1','ctc1'}
        reads=regs[:1] if to_float else regs if store or branch else regs[1:]
        for reg in reads:
            if reg in seen:continue
            if re.fullmatch(r'\$a[0-3]',reg):integer.add(reg)
            if reg in {'$f12','$f14'}:floating.add(reg)
        if slot:
            offset=int(slot[1],0)
            if store:written.add(offset)
            elif offset>=frame+16 and offset not in written:stack_slots.add((offset-frame)//4+1)
        if not store and not branch:seen.add(regs[-1] if to_float else regs[0])
    return {'integer':sorted(integer),'floating':sorted(floating),'mixed':bool(floating and '$a0' in integer),
            'incoming_stack_slots':sorted(stack_slots)}


def temporary_arguments(asm):
    """Incoming temporary-register reads are leads for same-unit specialization, not ABI rewrites."""
    defined=set();incoming=set()
    for line in instructions(asm)[:64]:
        if line.startswith(('jal ', 'jalr ', 'jr ', 'b ', 'j ')):
            break
        match=re.match(r'([a-z][a-z0-9.]*)\s+(.+)',line)
        if not match:
            continue
        op,args=match.groups();regs=re.findall(r'\$[a-z][a-z0-9]*',args)
        if not regs:
            continue
        store=op in {'sw','sd','sh','sb','swc1','sdc1'}
        reads=regs if store or op.startswith(('b','c.')) else regs[1:]
        # A plain spill is preservation, whereas reading through a temporary pointer is evidence.
        if store and len(regs)>1 and regs[-1]=='$sp':
            reads=[]
        incoming.update(r for r in reads if re.fullmatch(r'\$t[0-8]',r) and r not in defined)
        if not store and not op.startswith(('b','c.')):
            defined.add(regs[0])
    return sorted(incoming)


def specialized_argument_setup(callee, caller, name):
    """Require callee read-before-write plus caller definitions immediately around its direct call."""
    incoming=temporary_arguments(callee)
    if len(incoming)<2:
        return None
    code=instructions(caller)
    for index,line in enumerate(code):
        if not re.fullmatch(r'jal\s+'+re.escape(name),line):
            continue
        before=code[max(0,index-12):index]
        last=next((i for i in range(len(before)-1,-1,-1) if before[i].startswith(('jal ','jalr ','b ','j '))),-1)
        window=before[last+1:]+code[index+1:index+2]
        setup=[text for text in window if any(re.match(r'(?:or|addu|addiu|move|lw|li|lui)\s+'+re.escape(reg)+r',',text) for reg in incoming)]
        proven=[reg for reg in incoming if any(re.match(r'\w+\s+'+re.escape(reg)+r',',text) for text in setup)]
        if len(proven)>=2:
            return {'registers':proven,'setup':setup,'call':line}
    return None



def word_argument_pairs(asm):
    """Adjacent loads into ABI word pairs at a direct call, including the delay slot."""
    code=instructions(asm);result=[]
    for index,line in enumerate(code):
        match=re.fullmatch(r'jal\s+([A-Za-z_]\w*)',line)
        if not match:continue
        before=code[max(0,index-12):index]
        last=next((i for i in range(len(before)-1,-1,-1) if before[i].startswith(('jal ','jalr '))),-1)
        window=before[last+1:]+code[index+1:index+2];loads={}
        for text in window:
            write=re.match(r'([a-z][a-z0-9.]*)\s+(\$a[0-3]),',text)
            if not write or write[1] in {'sw','sd','sh','sb'}:continue
            loads.pop(write[2],None)
            load=re.fullmatch(r'lw\s+(\$a[0-3]),\s*(-?0x[0-9a-fA-F]+|-?\d+)\((\$\w+)\)',text)
            if load:loads[load[1]]=(int(load[2],0),load[3])
        for pair in [('$a0','$a1'),('$a2','$a3')]:
            if all(reg in loads for reg in pair) and loads[pair[0]][1]==loads[pair[1]][1] and loads[pair[1]][0]==loads[pair[0]][0]+4:
                result.append({'function':match[1],'registers':pair,'loads':[loads[r] for r in pair]})
    return result
