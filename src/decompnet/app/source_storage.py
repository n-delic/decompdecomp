"""Storage/scope and address-load evidence. Never replace an initializer or infer missing definitions."""
import re
from collections import Counter

from ..forks.csource import _blank

IDENT = r'[A-Za-z_]\w*'


def local_statics(source):
    """Simple single-object local statics only; function definitions and ambiguous declarators are excluded."""
    clean=_blank(source);result=[]
    for match in re.finditer(r'\bstatic\s+[^;{}()]+;',clean):
        text=match[0]
        declaration=text.split('=',1)[0].rstrip(';').strip()
        if ',' in declaration:
            continue
        name=re.search(r'\b('+IDENT+r')\s*(?:\[[^\]]*\])?\s*$',declaration)
        if name:result.append((name[1],text))
    return result


def scope_hints(reference, source, header, entry):
    from ..forks.csource import find
    span=find(reference,entry)
    if not span:return []
    original=reference[span[0]:span[1]]
    current=find(source,entry)
    body=source[current[0]:current[1]] if current else source
    retained={name for name,_ in local_statics(body)}
    return [f'Local static {name} is absent from the candidate body or changed storage/scope. Keep its original declaration in the function; global/extern substitutes can alter loads and register reuse. No declaration was moved.'
            for name,_ in local_statics(original) if name not in retained and re.search(r'\b'+re.escape(name)+r'\b',_blank(body+header))][:8]


def object_hints(rows, header):
    """Extra candidate address-high loads suggest reviewing authorized original object definitions."""
    target=Counter();candidate=Counter()
    for row in rows or []:
        if not isinstance(row,(tuple,list)) or len(row)!=3:continue
        for text,counts in ((row[1],target),(row[2],candidate)):
            match=re.search(r'\blui\s+[^,]+,\s*%hi\(('+IDENT+r')\)',str(text))
            if match:counts[match[1]]+=1
    externs=[]
    wide=set()
    for match in re.finditer(r'\bextern\s+([^;{}()]+);',_blank(header)):
        names=re.findall(r'\b('+IDENT+r')\s*(?=,|\[|$)',match[1])
        externs.extend(names)
        if re.search(r'\b(?:u64|s64|uint64_t|int64_t|OSTime|double)\b|\blong\s+long\b',match[1]):wide.update(names)
    result=[]
    for name in sorted(set(externs),key=lambda n:-(candidate[n]-target[n])):
        aliases={name}
        # An externally declared wide object can appear as base and base+4 assembler symbols.
        # This only groups diagnostic loads; it does not assert an object's size or placement.
        if name in wide and re.fullmatch(r'D_[0-9A-Fa-f]{8}',name):
            aliases.add('D_'+format(int(name[2:],16)+4,'08X'))
        expected=sum(target[n] for n in aliases);actual=sum(candidate[n] for n in aliases)
        if actual>expected and expected:
            result.append(f'{name}: candidate has {actual} address-high loads versus {expected} in the target. Review its authorized original object definition instead of an extern-only substitute; preserve its initializer, storage and scope. This is advisory, not permission to invent zero data.')
    return list(dict.fromkeys(result))[:4]



def wide_argument_hints(asm, header):
    """MIPS call evidence plus a narrow prototype suggests review, not an inferred width change."""
    from ..arch.mips_runtime import word_argument_pairs
    from .source_next_steps import signatures
    pairs=word_argument_pairs(asm)
    declarations=signatures(header,{pair['function'] for pair in pairs});result=[]
    narrow={'u32','s32','int','unsigned','unsigned int','long','unsigned long','signed int','signed long'}
    for pair in pairs:
        declaration=declarations.get(pair['function'])
        if not declaration:continue
        params=[p.strip() for p in declaration.split('(',1)[1].rstrip(')').split(',')]
        registers=pair['registers']
        if ((len(params)==1 and params[0] in narrow and registers==('$a0','$a1')) or
                (len(params)==2 and params[1] in narrow and registers==('$a2','$a3'))):
            result.append(f"{pair['function']} is declared {declaration}, but the target loads adjacent words into {registers[0]}/{registers[1]} at its call. Check a 64-bit or aggregate argument and original ABI/prototype before body permutation; no width was inferred or changed.")
    return list(dict.fromkeys(result))[:4]


def typed_object_hints(rows, source, header):
    """Different symbolic high bases around raw byte access are an object-identity review lead."""
    clean=_blank(source+'\n'+header)
    if not re.search(r'\b(?:char|[su]8)\s*\*|\bextern\s+(?:unsigned\s+|signed\s+)?char\b[^;]*\[',clean):return []
    target=set();candidate=set()
    for row in rows or []:
        if not isinstance(row,(list,tuple)) or len(row)!=3:continue
        for text,names in ((row[1],target),(row[2],candidate)):
            names.update(re.findall(r'%hi\((D_[0-9A-Fa-f]{8})\)',str(text)))
    pairs=[(a,b) for a in sorted(target-candidate) for b in sorted(candidate-target)
           if 0<abs(int(a[2:],16)-int(b[2:],16))<=0x20000]
    return [f'Object identity: target uses {a}, candidate uses {b} around raw byte access. Check an authorized typed view of the original object and its field offsets; equivalent address arithmetic can change compiler base construction. No type or layout was invented.' for a,b in pairs[:3]]
