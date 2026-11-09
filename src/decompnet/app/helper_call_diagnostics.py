"""Suggest runtime-helper aliases only when every other compared instruction already agrees."""
import re

HELPERS = {'__ull_div','__ll_div','__ull_rem','__ll_rem','__ll_mul','__ll_lshift','__ull_rshift','__ll_rshift'}


MATH = {'sqrtf':'float', 'sinf':'float', 'cosf':'float', 'tanf':'float', 'fabsf':'float',
        'sqrt':'double', 'sin':'double', 'cos':'double', 'tan':'double', 'fabs':'double'}


def suggestions(rows):
    mappings={};count=0
    for row in rows or []:
        if len(row)!=3:return []
        kind,target,candidate=row
        if kind==' ':continue
        left=re.fullmatch(r'(jal|bl|call)\s+([A-Za-z_]\w*)',target.strip())
        right=re.fullmatch(r'(jal|bl|call)\s+\??([A-Za-z_]\w*)',candidate.strip())
        if not left or not right or left[1]!=right[1] or right[2] not in HELPERS | MATH.keys() or left[2]==right[2]:return []
        if right[2] in mappings and mappings[right[2]]!=left[2]:return []
        mappings[right[2]]=left[2];count+=1
    if not count:return []
    runtime_types={a: ('long long' if a.startswith('__ll_') else 'unsigned long long') for a in HELPERS}
    runtime_prototypes=' '.join(f'{runtime_types[a]} {b}({runtime_types[a]}, '+('int' if 'shift' in a else runtime_types[a])+');' for a,b in sorted(mappings.items()) if a in HELPERS and 'shift' not in a)
    prototypes=' '.join(f'{MATH[a]} {b}({MATH[a]});' for a,b in sorted(mappings.items()) if a in MATH)
    return ['Only runtime-helper call targets differ: '+', '.join(f'{a} → {b}' for a,b in sorted(mappings.items()))+
            '. Confirm the helper ABI and each target call site, then use an explicit typed call or a verified symbol alias. '+
            (' Integer helper prototype candidates (confirm signedness and shift ABI): '+runtime_prototypes if runtime_prototypes else '')+
            ' No addresses or symbols were changed; recompile and pass the byte judge before accepting it.'+
            (' Expected SDK math prototypes: '+prototypes+' An alias alone does not declare its return/argument types; an implicit int return changes the ABI.' if prototypes else '')]
