"""Bounded address, literal and floating-load temporaries learned from verified repairs.

These are scheduling hypotheses, never equivalence claims; the ordinary exact judge accepts them.
"""
import re

from .. import m2cfix
from ..forks.csource import _blank, find

IDENT = r'[A-Za-z_]\w*'


def close(text, start):
    depth = 0
    for i in range(start,len(text)):
        depth += (text[i]=='(')-(text[i]==')')
        if not depth:
            return i+1
    return None


def statement_start(body, end):
    depth, start = 0, 1
    for i,ch in enumerate(body[:end]):
        depth += (ch=='(')-(ch==')')
        if depth==0 and ch in ';{}':
            start=i+1
    return start


def declaration_prefix(text):
    # Conservatively identify a declaration initializer. Never insert a statement ahead of it:
    # that would put the existing declaration after a statement in C89. Inline alternatives stay valid.
    return bool(re.match(r'\s*(?:(?:const|volatile|unsigned|signed|struct)\s+)*[A-Za-z_]\w*(?:\s+(?:\*\s*)*|\s*\*+\s*)[A-Za-z_]\w*\s*(?:\[[^]]*\])?\s*=',text))


def variants(source, name):
    span = find(source,name)
    if not span:
        return []
    clean = _blank(source)
    opening = clean.find('{',*span)
    body = clean[opening:span[1]]
    if '#' in body or re.search(r'\bvolatile\b|__repair_(?:addr|literal|cmp)',clean):
        return []
    result = []

    def raw(a,b):
        return source[opening+a:opening+b]

    def add(family, edits):
        merged={}
        for a,b,replacement in edits:
            merged[a,b]=merged.get((a,b),'')+replacement
        candidate=source
        for (a,b),text in sorted(merged.items(),reverse=True):
            candidate=candidate[:opening+a]+text+candidate[opening+b:]
        if candidate!=source and candidate not in [s for _,s in result]:
            result.append((family,candidate))

    # Keep the field macro, including its type/offset; only split its effective address.
    for m in list(re.finditer(r'\bM2C_FIELD\s*\(',body))[:16]:
        end=close(body,m.end()-1)
        if not end:
            continue
        args=m2cfix._split_top_level(body[m.end():end-1])
        if len(args)!=3 or not re.search(r'[+*]',args[0]) or not re.fullmatch(r'\s*'+IDENT+r'\s*\*\s*',args[1]):
            continue
        base=args[0].strip()
        # Nested field macros are loads; arbitrary function calls and writes must not move.
        calls=re.findall(r'\b('+IDENT+r')\s*\(',base)
        if any(c!='M2C_FIELD' for c in calls) or re.search(r'\+\+|--|[=;{}]',base):
            continue
        at=statement_start(body,m.start())
        prefix=body[at:m.start()]
        if re.search(r'\b(?:for|while|do|return|goto|else)\b',prefix):
            continue
        expr=raw(m.end(),m.end()+len(args[0])).strip()
        for inline in (False,True):
            if not inline and declaration_prefix(prefix):
                continue
            replacement='__repair_addr' if not inline else f'(__repair_addr = (char *)({expr}))'
            edits=[(1,1,'\nchar *__repair_addr;\n'),(m.end(),m.end()+len(args[0]),replacement)]
            if not inline:
                edits.append((at,at,f'\n__repair_addr = (char *)({expr});\n'))
            add('address_temporary',edits)
        if sum(f=='address_temporary' for f,_ in result)>=6:
            break

    # Plain casts in imported sources deserve the same address experiment as m2c macros.
    for m in list(re.finditer(r'\*\s*\(\s*(?:s16|u16|s32|u32|f32|f64|short|int|float|double)\s*\*\s*\)\s*\(',body))[:6]:
        end=close(body,m.end()-1)
        if not end:
            continue
        expr=raw(m.end(),end-1)
        if not re.search(r'[+*]',expr) or re.search(r'\b'+IDENT+r'\s*\(|\+\+|--|[=;{}]',expr):
            continue
        at=statement_start(body,m.start())
        if declaration_prefix(body[at:m.start()]) or re.search(r'\b(?:for|while|do|return|goto|else)\b',body[at:m.start()]):
            continue
        add('address_temporary',[(1,1,'\nchar *__repair_addr;\n'),
                                 (at,at,f'\n__repair_addr = (char *)({expr});\n'),
                                 (m.end(),end-1,'__repair_addr')])

    # Only small literal arithmetic/comparison operands, not field offsets, array bounds or masks.
    for m in list(re.finditer(r'(?P<op>\*|>=|<=|>|<)\s*(?P<n>0x[0-9a-fA-F]+|\d+)(?P<u>[uU]?)(?![\w.])',body))[:12]:
        number=int(m['n'],0) if m['n'].startswith('0x') else int(m['n'])
        if number not in (0,1,2,4,8,16,32):
            continue
        at=statement_start(body,m.start())
        prefix=body[at:m.start()]
        if re.search(r'\b(?:for|while|do|sizeof|else)\b',prefix) or re.search(r'<<|>>',body[max(0,m.start()-1):m.start()+2]):
            continue
        # A '*' can be a declaration/pointer cast; require a value immediately to its left.
        if m['op']=='*' and not re.search(r'[\w)\]]\s*$',prefix):
            continue
        if declaration_prefix(prefix):
            continue
        typ='unsigned int' if m['u'] else 'int'
        add('constant_local',[(1,1,f'\n{typ} __repair_literal;\n'),
                              (at,at,f'\n__repair_literal = {m["n"]}{m["u"]};\n'),
                              (m.start('n'),m.end(),'__repair_literal')])
        if sum(f=='constant_local' for f,_ in result)>=4:
            break

    # Materialize the two float loads without negating/swapping the comparison (NaN behavior stays).
    field=r'M2C_FIELD\([^;{}]*?,\s*(f32|f64)\s*\*,\s*(?:0x[0-9a-fA-F]+|\d+)\s*\)'
    for m in list(re.finditer(r'\bif\s*\(',body))[:12]:
        end=close(body,m.end()-1)
        if not end:
            continue
        if body[statement_start(body,m.start()):m.start()].strip():
            continue
        cond=body[m.end():end-1]
        match=re.fullmatch(r'\s*('+field+r')\s*(<|>|<=|>=)\s*('+field+r')\s*',cond)
        if not match or match[2]!=match[5]:
            continue
        left,right,typ=match[1],match[4],match[2]
        for reverse in (False,True):
            for copy in (False,True):
                assigns=[f'__repair_cmp_l = {left};',f'__repair_cmp_r = {right};']
                if reverse:
                    assigns.reverse()
                if copy:
                    assigns.append('__repair_cmp_c = __repair_cmp_r;')
                right_name='__repair_cmp_c' if copy else '__repair_cmp_r'
                decl=f'\n{typ} __repair_cmp_l; {typ} __repair_cmp_r;'+(f' {typ} __repair_cmp_c;' if copy else '')+'\n'
                add('comparison_load',[(1,1,decl),(m.start(),m.start(),'\n'.join(assigns)+'\n'),
                                       (m.end(),end-1,f'__repair_cmp_l {match[3]} {right_name}')])
        break
    return result
