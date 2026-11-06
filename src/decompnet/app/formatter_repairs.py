"""Bounded source-shape repairs observed in a large compiler-library formatter.

Proposals preserve evaluation order; only the ordinary compiler/link judge accepts C.
No SDK source is bundled or fetched. These rules apply to ordinary C on every processor.
"""
import re

from ..forks.csource import _blank, find
from .pattern_repairs import close

FAMILIES = ('condition_assignment', 'conditional_output', 'switch_flag_loop', 'shared_tail_copy', 'scalar_initialization_placement', 'scalar_declaration_order', 'scalar_initialization_order', 'formatter_bundle')
ID = r'[A-Za-z_]\w*'


def variants(source, name, *, _bundles=True):
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    opening = clean.find('{', *span)
    if opening < 0 or '#' in clean[opening:span[1]] or re.search(r'::|\b(?:class|template|namespace)\b',clean):
        return []
    result = []
    # Combining adjacent statements keeps the load/increment before the same comparison.
    # Require an existing scalar and a simple load, with no qualifier, shadow or macro call.
    declared = {m[1] for m in re.finditer(r'\b(?:char|int|[su](?:8|16|32))\s+('+ID+r')\s*;', clean[opening:span[1]])
                if clean[opening:opening+m.start()].count('{')-clean[opening:opening+m.start()].count('}')==1}
    for hit in re.finditer(r'\b('+ID+r')\s*=\s*(\*\s*'+ID+r'\s*(?:\+\+)?);\s*if\s*\(\s*\1\s*(==|!=)', clean[opening:span[1]]):
        if hit[1] not in declared or re.search(r'\bvolatile\b[^;{}]*\b'+re.escape(hit[1])+r'\b', clean[opening:span[1]]):
            continue
        a, b = opening+hit.start(), opening+hit.end()
        replacement = f'if (({hit[1]}={hit[2]}){hit[3]}'
        result.append(('condition_assignment', source[:a]+replacement+source[b:]))
    # A do/while(0) macro whose two branches are expression statements can use ?:.
    # All invocations must discard the result; reject token/stringification and multiline macros.
    macro = re.compile(r'^#define\s+('+ID+r')\(([^)]+)\)\s+do\s*{\s*if\s*\(([^{};]+)\)\s*([^{};]+);\s*else\s+([^{};]+);\s*}\s*while\s*\(0\)\s*$', re.M)
    for hit in macro.finditer(source[:opening]):
        if any(x in hit[0] for x in ('\\', '##')):
            continue
        uses = list(re.finditer(r'\b'+re.escape(hit[1])+r'\s*\(', clean[opening:span[1]]))
        if not uses:
            continue
        safe = True
        for use in uses:
            at = opening+use.start()
            ending = close(clean, opening+use.end()-1)
            if not ending or not re.match(r'\s*;', clean[ending:]) or clean[opening:at].rstrip()[-1:] not in ';{}':
                safe = False
                break
            prefix = clean[max(opening,at-15):at]
            if re.search(r'\b(?:return|sizeof)\s*$', prefix) or re.search(r'[=+*/?:]\s*$', prefix):
                safe = False
                break
        if safe:
            text = f'#define {hit[1]}({hit[2]}) (({hit[3]}) ? ({hit[4]}) : ({hit[5]}))'
            result.append(('conditional_output', source[:hit.start()]+text+source[hit.end():]))
    # A switch-only infinite flag loop. All continues target this loop; nested loops are excluded.
    for loop in re.finditer(r'\bfor\s*\(\s*;\s*;\s*\)\s*{', clean[opening:span[1]]):
        a = opening+loop.start(); brace = opening+loop.end()-1
        end = close(clean, brace, '{', '}')
        if not end:
            continue
        body = clean[brace+1:end-1]
        head = re.match(r'\s*('+ID+r')\s*=\s*(\*\s*'+ID+r'\+\+)\s*;\s*switch\s*\(\s*(\(\s*signed\s+char\s*\)\s*)?\1\s*\)\s*{', body)
        if not head or re.search(r'\b(?:for|while|do|goto)\b', body):
            continue
        switch_brace = brace+1+head.end()-1
        switch_end = close(clean, switch_brace, '{', '}')
        if not switch_end or not re.fullmatch(r'\s*break\s*;\s*', clean[switch_end:end-1]):
            continue
        label = 'decomp_flag_loop'
        if re.search(r'\b'+label+r'\b', clean):
            continue
        inner = source[switch_brace+1:switch_end-1]
        # case/default bodies must terminate in continue; a break would target a different construct.
        if re.search(r'\bbreak\b', _blank(inner)):
            continue
        inner = re.sub(r'\bcontinue\s*;', 'goto '+label+';', inner)
        assignment=f'({head[1]}={head[2]})' if head[3] else f'{head[1]}={head[2]}'
        text = f"{label}: switch ({head[3] or ''}{assignment}) {{"+inner+'}'
        result.append(('switch_flag_loop', source[:a]+text+source[end:]))
    # Duplicate a short shared tail at its goto sites. No declarations, escaping labels or updates
    # to scope are introduced; the same statements still execute exactly once on each path.
    for tail in re.finditer(r'\b('+ID+r')\s*:\s*((?:'+ID+r'\s*=\s*[^;{}]+;\s*){1,4}goto\s+'+ID+r'\s*;)', clean[opening:span[1]]):
        label = tail[1]
        jumps = list(re.finditer(r'\bgoto\s+'+re.escape(label)+r'\s*;', clean[opening:span[1]]))
        if not 1 <= len(jumps) <= 3 or len(tail[2]) > 300:
            continue
        block = source[opening+tail.start(2):opening+tail.end(2)]
        edited = source
        for jump in reversed(jumps):
            a,b=opening+jump.start(),opening+jump.end()
            edited=edited[:a]+'{'+block+'}'+edited[b:]
        result.append(('shared_tail_copy', edited))
    # Ordinary unescaped scalar initializations may move across constant byte stores.
    # This changes old-GCC scheduling without changing a pointer's side effects or reads.
    body=clean[opening:span[1]]
    scalar=set()
    for declaration in re.finditer(r'\b(?:int|[su](?:16|32))\s+([^;{}()]+);',body):
        if body[:declaration.start()].count('{')-body[:declaration.start()].count('}')!=1:continue
        prefix=body[max(0,declaration.start()-12):declaration.start()]
        if re.search(r'\b(?:volatile|const)\b',prefix):continue
        for item in declaration[1].split(','):
            match=re.fullmatch(r'\s*('+ID+r')(?:\s*=\s*[01])?\s*',item)
            if match:scalar.add(match[1])
    for hit in re.finditer(r'\b('+ID+r')\s*=\s*(0|1);\s*((?:\*\s*'+ID+r'\+\+\s*=\s*[^;{}]*;\s*){1,2})',body):
        if hit[1] not in scalar or re.search(r'&\s*'+re.escape(hit[1])+r'\b',body) or re.search(r'\b'+re.escape(hit[1])+r'\b',hit[3]):
            continue
        a,b=opening+hit.start(),opening+hit.end()
        stores=source[opening+hit.start(3):opening+hit.end(3)]
        result.append(('scalar_initialization_placement',source[:a]+stores+hit[1]+'='+hit[2]+';'+source[b:]))
    # Declaration-only permutations are scoped to the first three unqualified local scalars.
    for hit in list(re.finditer(r'\bint\s+('+ID+r')\s*,\s*('+ID+r')\s*,\s*('+ID+r')(?=\s*[,;])',body))[:3]:
        if body[:hit.start()].count('{')-body[:hit.start()].count('}')!=1:
            continue
        if re.search(r'\b(?:volatile|const)\b',body[max(0,hit.start()-12):hit.start()]):continue
        a,b=opening+hit.start(),opening+hit.end()
        names=list(hit.groups())
        for order in ((1,2,0),(2,0,1),(1,0,2)):
            result.append(('scalar_declaration_order',source[:a]+'int '+', '.join(names[i] for i in order)+source[b:]))
    ordinary=set()
    for declaration in re.finditer(r'\bint\s+([^;{}()]+);',body):
        if body[:declaration.start()].count('{')-body[:declaration.start()].count('}')!=1:continue
        if re.search(r'\b(?:volatile|const)\b',body[max(0,declaration.start()-12):declaration.start()]):
            continue
        for item in declaration[1].split(','):
            match=re.fullmatch(r'\s*('+ID+r')(?:\s*=\s*[01])?\s*',item)
            if match:ordinary.add(match[1])
    for hit in re.finditer(r'\b('+ID+r')\s*=\s*[01];\s*('+ID+r')\s*=\s*[01];',body):
        if hit[1]==hit[2] or not {hit[1],hit[2]}<=ordinary or any(re.search(r'&\s*'+re.escape(n)+r'\b',body) for n in hit.groups()):
            continue
        a,b=opening+hit.start(),opening+hit.end()
        text=source[a:b];first,second=text.split(';',1)
        result.append(('scalar_initialization_order',source[:a]+second.strip()+' '+first+';'+source[b:]))
    # A small bundle tests interacting changes without opening an unbounded cross product.
    first = list(result)
    for family, candidate in (first[:6] if _bundles else []):
        for other, combined in variants(candidate, name, _bundles=False):
            if other != family and combined != source:
                result.append(('formatter_bundle', combined))
                break
    if _bundles:
        # Stack declaration choice plus two independent initialization choices interacted in
        # the measured formatter. Keep this bounded even when a single step scores worse.
        for family, candidate in first:
            if family!='scalar_declaration_order':continue
            single=candidate
            for wanted in ('scalar_initialization_placement','scalar_initialization_order'):
                candidate=next((s for f,s in variants(candidate,name,_bundles=False) if f==wanted),candidate)
            if candidate!=single:result.insert(0,('formatter_bundle',candidate))
    return list(dict.fromkeys(result))[:32]

