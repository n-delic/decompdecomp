"""Bounded decision, halfword and selector-address hypotheses for old compiler scheduling.

Edits retain existing loads and source guards. Directives remain opaque, in place; only
spans whose identifiers are not dangerous macros are touched. The exact judge accepts C.
"""
import itertools
import re

from . import local_flow
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, INTEGER, NUMBER, edit, integer, view
from .pattern_repairs import close

FAMILIES = ('halfword_arithmetic', 'single_case_switch', 'shared_flag_guard',
            'selector_address_staging', 'qualified_selector_load', 'decision_address_bundle')
MAX_SITES = 4
MAX_VARIANTS = 32


def context(source, name, header):
    """Keep directive bytes and offsets intact while inspecting ordinary statements."""
    # Do not hoist a define: even #line and continuation lines keep their original positions.
    masked=re.sub(r'^[ \t]*\#(?:[^\n]*\\\n)*[^\n]*',lambda m:re.sub(r'[^\n]',' ',m[0]),source,flags=re.M)
    part=view(masked,name)
    if not part or re.search(r'\b(?:asm|__asm__|class|template)\b',part.clean):
        return None
    if re.search(r'^[ \t]*#\s*(?!define\b|line\b)\w+',source[part.opening:part.end],re.M):return None
    macros=set()
    for directive in re.finditer(r'^[ \t]*\#(?:[^\n]*\\\n)*[^\n]*',header+'\n'+source,re.M):
        definition=re.match(r'\#\s*define\s+('+IDENT+r')(.*)',directive[0].lstrip(),re.S)
        if definition and re.search(r'\b(?:if|else|break|continue|goto|return|case|default)\b|[{};]',definition[2]):
            macros.add(definition[1])
    return part,macros


def safe(text, macros):
    return not macros.intersection(re.findall(IDENT,text))


def integer_type(typ, text):
    """Resolve common scalar aliases instead of guessing their width from their names."""
    if re.fullmatch(r'[su](8|16|32)',typ):
        scalar={'8':'char','16':'short','32':'int'}[typ[1:]]
        sign=r'unsigned\s+' if typ.startswith('u') else r'(?:signed\s+)?'
        return bool(re.search(r'\btypedef\s+'+sign+scalar+r'\s+'+typ+r'\s*;',text))
    return bool(re.fullmatch(INTEGER,typ))


def integer_condition(condition, part, source, header):
    """A switch needs a resolved integer expression, not an arbitrary comparison."""
    text=header+'\n'+source
    if '->' not in condition:
        return any(integer_type(m[1],text) for m in re.finditer(rf'\b({INTEGER})\s+{re.escape(condition)}\s*(?=[;=,)])',part.clean))
    pointer,field=re.split(r'\s*->\s*',condition)
    types=set(re.findall(rf'\b({IDENT})\s*\*\s*{pointer}\s*(?=[=;,)]|$)',part.clean))
    if len(types)!=1:return False
    typ=next(iter(types))
    records=list(re.finditer(rf'typedef\s+struct(?:\s+{IDENT})?\s*{{([^{{}}]*)}}\s*{typ}\s*;',text))
    return len(records)==1 and any(integer_type(m[1],text) for m in re.finditer(rf'\b({INTEGER})\s+{field}\s*;',records[0][1]))


def halfwords(source, name, header):
    """Try halfword arithmetic spelling without moving an access or changing a header."""
    ctx=context(source,name,header)
    if not ctx:return []
    part,macros=ctx
    narrow=set()
    for hit in re.finditer(r'\b(?:s16|short|signed\s+short)\s+('+IDENT+r'(?:\s*,\s*'+IDENT+r')*)\s*;',part.body):
        if 's16' not in hit[0] or integer_type('s16',header+'\n'+source):narrow.update(re.findall(IDENT,hit[1]))
    result=[]
    for hit in re.finditer(rf'\b({IDENT})\s*=\s*({IDENT})\s*-\s*({NUMBER})\s*;',part.body):
        if hit[1] not in narrow or not 0<integer(hit[3])<=32767 or not safe(hit[0],macros):continue
        start=part.opening+1+hit.start();end=part.opening+1+hit.end()
        result.append((start,end,f'{hit[1]} = (unsigned short){hit[2]} + (unsigned short)-{hit[3]};'))
    # Prefer a value already adjusted in its wide lifetime; casting an original load
    # needlessly can disrupt an otherwise matching region.
    result.sort(key=lambda change:0 if re.search(r'\b'+re.escape(re.search(r'unsigned short\)([A-Za-z_]\w*)',change[2])[1])+r'\s*=\s*[^;{}]+-\s*'+NUMBER,part.body) else 1)
    return result[:3]


def switches(source, name, header):
    """Copy both arms verbatim; a new switch must not capture existing control transfers."""
    ctx=context(source,name,header)
    if not ctx:return []
    part,macros=ctx;result=[]
    for hit in re.finditer(rf'\bif\s*\(\s*({IDENT}(?:\s*->\s*{IDENT})?)\s*==\s*({NUMBER})\s*\)\s*{{',part.clean[part.opening+1:part.end-1]):
        start=part.opening+1+hit.start();opening=part.opening+hit.end();end=close(part.clean,opening,'{','}')
        if not end:continue
        other=re.match(r'\s*else\s*{',part.clean[end:])
        if not other:continue
        second=end+other.end()-1;stop=close(part.clean,second,'{','}')
        if not stop:continue
        body=part.clean[opening+1:end-1]+part.clean[second+1:stop-1]
        # A new switch must not capture an existing break, case or jump entry.
        if re.search(r'\b(?:break|continue|goto|case|default)\b|(?<![?:]):(?!:)',body) or not safe(part.clean[start:stop],macros):continue
        condition=hit[1].strip();constant=hit[2]
        if not integer_condition(condition,part,source,header):continue
        replacement=('switch ('+condition+'){case '+constant+':{'+source[opening+1:end-1]+
                     '}break;default:{'+source[second+1:stop-1]+'}break;}')
        # A repeated comparison also used for a scalar value is particularly sensitive to allocation.
        preceding=part.clean[max(part.opening,start-160):start]
        priority=0 if re.search(re.escape(condition)+r'\s*==\s*'+re.escape(constant)+r'\s*\)\s*\?',preceding) else 1
        result.append((priority,start,stop,replacement))
    return [(a,b,c) for _,a,b,c in sorted(result)[:3]]


def shared_guard(source, name, header):
    """Share a failed flag edge only across ordinary reloads of the same pointer."""
    ctx=context(source,name,header)
    if not ctx or '__repair_flag_join' in source:return None
    part,macros=ctx
    pattern=rf'\bif\s*\(\s*({IDENT})->({IDENT})\[\s*\1->({IDENT})\[\s*([0-9]+)\s*\]\s*\]\.({IDENT})\s*&\s*({NUMBER})\s*\)\s*{{'
    hits=list(re.finditer(pattern,part.clean))
    for first,second,third in zip(hits,hits[1:],hits[2:]):
        if first.groups()[:5]!=second.groups()[:5] or first[6]!=second[6] or first.groups()[:5]!=third.groups()[:5]:continue
        a=close(part.clean,first.end()-1,'{','}');b=close(part.clean,second.end()-1,'{','}')
        if not a or not b:continue
        reload=rf'{first[1]}\s*=\s*({IDENT})\s*;'
        between=part.clean[a:second.start()]
        last=part.clean[b:third.start()]
        one=re.fullmatch(r'\s*}\s*'+reload+r'\s*',between)
        two=re.fullmatch(r'\s*'+reload+r'\s*',last)
        if not one or not two or one[1]!=two[1] or not safe(part.clean[first.start():third.end()],macros):continue
        # Only an ordinary nonvolatile pointer reload is skipped on the failed first guard.
        if re.search(r'(?:^|[;{}])[^;{}]*\bvolatile\b[^;{}]*\b(?:'+re.escape(one[1])+'|'+re.escape(first[1])+r')\b',header+'\n'+source):continue
        old=source[first.start():first.end()-1].strip()
        condition=old[old.find('(')+1:old.rfind(')')]
        return edit(source,[(first.start(),first.end()-1,'if (!('+condition+')) goto __repair_flag_join; '),
                            (b,b,'\n__repair_flag_join:;\n')])
    return None


def addresses(source, name, header):
    """Stage existing selector loads only when CFG liveness permits reusing their base."""
    ctx=context(source,name,header)
    if not ctx or '__repair_selector' in source:return []
    part,macros=ctx
    pattern=rf'\bif\s*\(\s*({IDENT})->({IDENT})\[\s*\1->({IDENT})\[\s*([0-9]+)\s*\]\s*\]\.({IDENT})\s*&\s*({NUMBER})\s*\)\s*{{'
    groups={}
    for hit in re.finditer(pattern,part.clean):
        if safe(hit[0],macros):groups.setdefault(hit.groups()[:4],[]).append(hit)
    result=[]
    for group,hits in list(groups.items())[:2]:
        hits=hits[:MAX_SITES];pointer,entries,selector,index=group
        # Overwriting the address temporary is safe only when neither arm nor its
        # continuation needs the old pointer. Unknown CFGs and hidden macro uses stop us.
        declaration=list(re.finditer(rf'\b(?P<type>{IDENT})\s*\*\s*(?P<name>{pointer})\s*(?:=\s*[^;{{}}]+)?;',part.clean[part.opening+1:part.end]))
        if len(declaration)!=1:continue
        if re.search(r'^\s*#\s*define[^\n]*\b'+pointer+r'\b',header+'\n'+source,re.M):continue
        # Flow uses absolute source spans for declarations.
        declaration=list(re.finditer(rf'\b(?P<type>{IDENT})\s*\*\s*(?P<name>{pointer})\s*(?:=\s*[^;{{}}]+)?;',part.clean))
        if len(declaration)!=1:continue
        try:flow=local_flow.Flow(part,declaration)
        except (ValueError,RecursionError):continue
        if pointer not in flow.names:continue
        eligible=[]
        for hit in hits:
            nodes=[node for node in flow.nodes if hit.start()<=node.start<hit.end()]
            if len(nodes)==1 and all(pointer not in flow.nodes[n].live for n in nodes[0].successors):eligible.append(hit)
        hits=eligible
        types=set(re.findall(rf'\b({IDENT})\s*\*\s*{pointer}\s*(?=[=;,)]|$)',part.clean))
        if len(types)!=1:continue
        typ=next(iter(types))
        if re.search(rf'\bstruct\s+{typ}\s*\*\s*{pointer}\b',part.clean):continue
        selections=list(itertools.combinations(hits,2))+[(h,)for h in hits]
        selections.sort(key=lambda selected:(0 if len(selected)==2 and block_path(part,selected[0].start())==block_path(part,selected[1].start()) else 1,-len(selected)))
        for selected in selections:
            for qualified in (True,False):
                edits=[]
                for hit in selected:
                    end=close(part.clean,hit.end()-1,'{','}')
                    if not end:break
                    load=(f'((volatile {typ}*){pointer})->{selector}[{index}]' if qualified else f'{pointer}->{selector}[{index}]')
                    # sizeof uses the existing element type. Reject incompatible strides at compile time.
                    size=f'sizeof({pointer}->{entries}[0])'
                    prefix=('{typedef char __repair_stride_contract[('+size+'%8==0 && '+size+'<=4096)?1:-1];'
                            'int __repair_selector='+load+';'
                            '__repair_selector=__repair_selector*(int)('+size+'/8);__repair_selector<<=3;'
                            f'{pointer}=({typ}*)((unsigned char*){pointer}+__repair_selector);'
                            f'if({pointer}->{entries}[0].{hit[5]} & {hit[6]})')
                    edits.extend([(hit.start(),hit.end()-1,prefix),(end,end,'}')])
                else:
                    result.append(('qualified_selector_load'if qualified else 'selector_address_staging',edit(source,edits)))
        break
    return result[:20]


def variants(source, name, arch, header=''):
    """Compose a short deterministic lane, retaining single edits for independent exploration."""
    if arch!='mips':return []  # Other ABIs need measured adapters; see ROADMAP.
    narrow=halfwords(source,name,header)
    base=edit(source,narrow[:1]) if narrow else source
    joined=shared_guard(base,name,header)
    if joined:base=joined
    result=[]
    decisions=switches(base,name,header)
    for start,end,replacement in decisions[:1]:
        decision=edit(base,[(start,end,replacement)])
        result.extend(('decision_address_bundle',candidate)for _,candidate in addresses(decision,name,header))
        result.append(('decision_address_bundle',decision))
    result.extend(('halfword_arithmetic',edit(source,[change]))for change in narrow)
    result.extend(('single_case_switch',edit(source,[change]))for change in switches(source,name,header))
    if joined:result.append(('shared_flag_guard',joined))
    result.extend(addresses(source,name,header))
    seen=set();return [(family,text)for family,text in result if text!=source and not(text in seen or seen.add(text))][:MAX_VARIANTS]
