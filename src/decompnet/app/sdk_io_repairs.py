"""Target-supported I/O source hypotheses. No guessed addresses, fields or match verdicts."""
import re

from ..forks.csource import _blank
from .integer_repair_support import IDENT, edit, view

FAMILIES=('proven_mmio_forms','io_local_storage','byte_record_copy')
WIDTH={32:1,36:1,40:1,33:2,37:2,41:2,35:4,43:4}


def instructions(asm):
    # Read machine words, not user-controlled numeric-looking symbol names.
    return [int(m[1],16) for m in re.finditer(r'/\*\s*[0-9a-fA-F]+\s+[0-9a-fA-F]+\s+([0-9a-fA-F]{8})\s*\*/',asm or '')]


def accesses(asm):
    words=instructions(asm);out=[]
    for i,word in enumerate(words):
        if word>>26!=15:continue
        register=(word>>16)&31;base=(word&65535)<<16
        for following in words[i+1:i+5]:
            op=following>>26;rs=(following>>21)&31;rt=(following>>16)&31
            if op in WIDTH and rs==register:
                offset=following&65535;offset=offset-65536 if offset>=32768 else offset
                address=(base+offset)&0xffffffff
                if 0xa0000000<=address<0xc0000000:out.append((address,WIDTH[op],op,rt))
            # Stop across control flow, or any possible definition of the base.
            if op in {0,1,2,3,4,5,6,7} or (rt==register and op not in {40,41,43}):break
    return out


def io_target(asm):
    found=accesses(asm)
    return len(found)>=2


def variants(source,name,arch,asm,header=''):
    if arch!='mips':return []
    part=view(source,name)
    if not part:return []
    result=[];proof={(a,w) for a,w,_,_ in accesses(asm)}
    context=_blank(header+'\n'+source[:part.opening])
    types=r'(?:volatile\s+(?:u32|s32|unsigned\s+int|int|u16|s16|unsigned\s+short|short|u8|s8|unsigned\s+char|char)|vu(?:8|16|32))'
    combined=[]
    for decl in re.finditer(rf'\bextern\s+({types})\s+(D_([ABab][0-9a-fA-F]{{7}}))\s*;',context):
        typ,symbol,encoded=decl.groups();address=int(encoded,16)
        width=1 if re.search(r'char|[svu]8\b',typ) else 2 if re.search(r'short|[svu]16\b',typ) else 4
        if (address,width) not in proof:continue
        if re.search(r'\b(?:u32|s32|u16|s16|u8|s8|int|char|short|void)\s*\**\s*'+re.escape(symbol)+r'\b',part.clean[part.clean.rfind(name,0,part.opening):part.end]):continue
        typ={'vu8':'volatile unsigned char','vu16':'volatile unsigned short','vu32':'volatile unsigned int'}.get(typ,typ)
        changes=[]
        for hit in re.finditer(r'(?<![\w.>])\b'+re.escape(symbol)+r'\b',part.clean[part.opening+1:part.end-1]):
            start=part.opening+1+hit.start();end=part.opening+1+hit.end()
            before=part.clean[:start].rstrip()
            if before.endswith('&'):
                start=part.clean.rfind('&',0,start)
                replacement=f'(({typ} *)0x{address:08X})'
            else:replacement=f'(*({typ} *)0x{address:08X})'
            changes.append((start,end,replacement))
        if changes:
            result.append(('proven_mmio_forms',edit(source,changes)));combined.extend(changes)
        # Reverse form only when this same symbol/type/address is independently present.
        numeric=re.compile(r'\*\s*\(\s*'+re.escape(typ)+r'\s*\*\s*\)\s*0[xX]'+f'{address:08x}'+r'(?![\w])',re.I)
        for hit in list(numeric.finditer(part.clean,part.opening,part.end))[:2]:
            result.append(('proven_mmio_forms',edit(source,[(hit.start(),hit.end(),symbol)])))
    if combined:result.insert(0,('proven_mmio_forms',edit(source,combined)))
    if proof:
        # Candidate-local scalar storage only; never remove volatile from hardware accesses.
        declaration=re.compile(rf'\b(?P<qual>volatile\s+|register\s+)?(?P<type>u32|s32|unsigned\s+int|int)\s+(?P<name>{IDENT})\s*;')
        for decl in declaration.finditer(part.clean,part.opening+1,part.end):
            var=decl['name']
            if re.search(r'&\s*\b'+var+r'\b',part.body):continue
            if len(list(declaration.finditer(part.body)))>32:break
            assignment=re.search(r'\b'+var+r'\s*=\s*(IO_READ\s*\(|\*\s*\(\s*volatile\b|D_[ABab][0-9a-fA-F]{7}\b)',part.body)
            if not assignment:continue
            has_saved=any(16<=rt<=23 and op in {32,33,35,36,37} for _,_,op,rt in accesses(asm))
            words=instructions(asm)
            stack=any(w>>26==43 and (w>>21)&31==29 for w in words)
            # Existing qualifiers can be reconsidered, but no parameter/address-taking or shared type changes.
            choices=[''] if decl['qual'] else []
            if has_saved:choices.append('register ')
            if stack:choices.append('volatile ')
            for qualifier in dict.fromkeys(choices):
                replacement=qualifier+decl['type']+' '+var+';'
                if replacement!=source[decl.start():decl.end()]:
                    result.append(('io_local_storage',edit(source,[(decl.start(),decl.end(),replacement)])))
            if len(result)>=10:break
    # A real all-byte record and observed unaligned word-copy instructions justify an aggregate-copy hypothesis.
    word_ops=[w>>26 for w in instructions(asm)]
    if 34 in word_ops and 38 in word_ops and not re.search(r'\bvolatile\b',part.clean):
        for record in re.finditer(rf'\btypedef\s+struct\s*{{([^{{}}]*)}}\s*({IDENT})\s*;',context):
            fields=re.findall(rf'\b(?:u8|s8|unsigned\s+char|signed\s+char|char)\s+({IDENT})\s*;',record[1])
            rest=re.sub(rf'\b(?:u8|s8|unsigned\s+char|signed\s+char|char)\s+{IDENT}\s*;','',record[1])
            if rest.strip() or not 4<=len(fields)<=16 or len(fields)%4 or min(word_ops.count(34),word_ops.count(38))*4<len(fields):continue
            local=re.search(r'\b'+record[2]+rf'\s+({IDENT})\s*;',part.body)
            if not local:continue
            pattern=r'\s*'.join(re.escape(local[1])+r'\s*\.\s*'+re.escape(field)+rf'\s*=\s*({IDENT})\s*\[\s*{i}\s*\]\s*;' for i,field in enumerate(fields))
            for hit in re.finditer(pattern,part.body):
                if len(set(hit.groups()))!=1:continue
                pointer=hit[1]
                if not re.search(rf'\b(?:u8|s8|char|unsigned\s+char)\s*\*\s*{pointer}\b',part.clean):continue
                result.append(('byte_record_copy',edit(source,[(part.opening+1+hit.start(),part.opening+1+hit.end(),f'{local[1]} = *({record[2]} *){pointer};')])))
    unique=[];seen=set()
    for family,text in result:
        if text!=source and text not in seen:seen.add(text);unique.append((family,text))
    return unique[:12]


def hints(source,asm,rows=None):
    result=[]
    ops=[w>>26 for w in instructions(asm)]
    if 34 in ops and 38 in ops:
        result.append('Target unaligned word copies warrant checking the original byte-aligned response record and aggregate-copy shape. Keep only evidenced fields and alignment; do not invent padding or packed attributes.')
    if not io_target(asm):return result
    result+=['Target uncached I/O accesses support a bounded -O1 SDK hypothesis. Check original register locals, volatile accesses and byte-record alignment before broad permutation; identity still requires evidence.']
    immediate=re.compile(r'addiu\s+(\$\w+),\s*\$zero,\s*(-?(?:0x[0-9a-fA-F]+|\d+))')
    for row in rows or []:
        if len(row)!=3 or row[0]==' ':continue
        a=immediate.fullmatch(row[1]);b=immediate.fullmatch(row[2])
        if a and b and a[1]==b[1] and a[2]!=b[2]:
            result.append(f'I/O reference constant differs: target {a[2]}, candidate {b[2]}. Inspect the SDK revision, error codes and polling masks; no constant was replaced.')
        if len(result)>=4:break
    if any(len(r)==3 and r[0] in {'<','>'} and re.match(r'b(?:eq|ne|gez|gtz|lez|ltz|\s)',r[1] or r[2]) for r in rows or []):
        result.append('I/O control flow has an added/missing branch. Check reference recovery paths against retail before permutation; no path was removed automatically.')
    return result
