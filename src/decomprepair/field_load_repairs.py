"""Stage ordinary field loads in an order actually present in the MIPS target.

Offset pairs only rank source hypotheses, not pointer identities. The linked judge still
checks registers, addresses, side effects and every byte before accepting a candidate.
"""
import re

from .inferred_layout_repairs import accesses
from .integer_repair_support import IDENT, edit, integer
from .pattern_repairs import close


def variants(source, name, arch, asm):
    from .target_local_repairs import _part
    part=_part(source,name)
    if not part or arch!='mips' or re.search(r'\b(?:volatile|restrict|asm|__asm__)\b',part.clean):return []
    insns=re.findall(r'/\*[^\n]*?\*/[ \t]*(\w+)[ \t]*([^\n]*)',asm or '')
    pairs=set()
    load=re.compile(r'\$\w+,\s*(0x[\dA-Fa-f]+|\d+)\(\$(\w+)\)')
    for left,right in zip(insns,insns[1:]):
        a=load.fullmatch(left[1].strip());b=load.fullmatch(right[1].strip())
        if left[0]==right[0]=='lw' and a and b and a[2]==b[2] and a[1]!=b[1]:pairs.add((integer(a[1]),integer(b[1])))
    result=[]
    hits=accesses(part.body)
    for cond in re.finditer(r'\bif\s*\(',part.body):
        opening=cond.end()-1;end=close(part.body,opening)
        if not end:continue
        # Calls, assignments and comma expressions would move effects across control flow.
        expression=part.body[opening+1:end-1]
        if re.search(r'\+\+|--|(?<![=!<>])=(?!=)|[,?]|\b'+IDENT+r'\s*\(',re.sub(r'\([^()]*\*[^()]*\)','',expression)):continue
        inside=[h for h in hits if opening<h[0]<end and h[3] in {'unsigned int','int'}]
        preceding=re.search(r'\b('+IDENT+r')\s*=\s*([^;{}]+);\s*$',part.body[:cond.start()])
        before=[h for h in hits if preceding and preceding.start()<h[0]<preceding.end()]
        for a,b,base,typ,offset in inside:
            others=[h for h in inside+before if h[2]==base and h[4]!=offset and (offset,h[4]) in pairs]
            if not others:continue
            fresh=f'__repair_load_{offset:X}'
            if re.search(r'\b'+fresh+r'\b',part.clean):continue
            original=source[part.opening+1+a:part.opening+1+b]
            # One variant immediately before the condition, another before its preceding
            # simple local assignment. Their C source shapes can schedule differently.
            positions=[cond.start()]
            if preceding and (before or preceding[2].strip()=='0'):
                positions.insert(0,preceding.start())
            for pos in positions:
                edits=[(part.opening+1+a,part.opening+1+b,fresh),
                       (part.opening+1+pos,part.opening+1+pos,f'{fresh} = {original};\n'),
                       (part.opening+1,part.opening+1,f'\n{typ} {fresh};')]
                result.append(('target_field_load_order',edit(source,edits)))
                if len(result)>=8:return result
    return result
