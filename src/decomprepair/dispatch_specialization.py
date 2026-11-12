"""Specialize a genuine selector/call/store through independent inline mapping parameters.

Only exhaustive literal switches and an adjacent, explicitly prototyped call qualify.
The old selector value must be dead on every outgoing CFG edge. No call, reload or
store is moved past another operation. The existing generated-helper emission guard
requires the helper to disappear; the ordinary judge still decides exactness.
"""
import re

from . import branch_call_support, integer_abi, local_flow
from .csource import _blank, find
from .integer_repair_support import IDENT, NUMBER, edit, integer, locals_for, view
from .pattern_repairs import close

FAMILY = 'effectful_dispatch_specialization'
PREFIX = '__repair_aggregate_call_'


def variants(source, name, header='', arch=None):
    part = view(source, name)
    context = _blank(header+'\n'+source)
    if (not part or PREFIX in context or re.search(r'\b(?:volatile|restrict|asm|__asm__|setjmp|longjmp)\b', source)
            or re.search(r'\b(?:__LINE__|__FILE__|__COUNTER__)\b',context)):
        return []
    # Comma locals can be analyzed on a private view without reprinting the source.
    from .integer_repair_support import INTEGER, split_plain_declarations
    analyzed = split_plain_declarations(source, name, header)
    if analyzed != source:
        choices = variants(analyzed, name, header, arch)
        restorations = []
        for hit in re.finditer(r'\b('+INTEGER+r')\s+('+IDENT+r'(?:\s*,\s*'+IDENT+r')+)\s*;', part.body):
            original = source[part.opening+1+hit.start():part.opening+1+hit.end()]
            expanded = re.sub(r',(\s*)', lambda m, kind=hit[1]: ';'+m[1]+kind+' ', hit[2])
            restorations.append((hit[1]+' '+expanded+';', original))
        restored = []
        for family, candidate in choices:
            for expanded, original in restorations:
                candidate = candidate.replace(expanded, original, 1)
            restored.append((family,candidate))
        return restored
    declarations = {d['name']: d for d in locals_for(part)}
    try:
        flow = local_flow.analyze(source, name)
    except (ValueError, RecursionError):
        return []
    if not flow:
        return []
    macros = set(re.findall(r'(?m)^\s*#\s*define\s+('+IDENT+r')', context))
    used_macros=macros.intersection(re.findall(IDENT,part.body))
    # An opaque macro elsewhere can read the supposedly dead selector implicitly.
    # Permit only a literal NULL definition; all other used macros need expansion evidence.
    for macro in used_macros:
        definitions=re.findall(r'(?m)^\s*#\s*define\s+'+re.escape(macro)+r'[ \t]+([^\n]+)',context)
        if macro!='NULL' or not definitions or any(re.sub(r'\s+','',d) not in {'0','((void*)0)'} for d in definitions):return []
    result = []
    for switch in re.finditer(r'\bswitch\s*\(\s*('+IDENT+r')\s*\)\s*{', part.body):
        start = part.opening+1+switch.start()
        opening = part.opening+switch.end()
        end = close(part.clean, opening, '{', '}')
        index = switch[1]
        if not end or index not in declarations:
            continue
        index_type = declarations[index]['type']
        if integer_abi.scalar(index_type, context, arch) is None:
            continue
        body = part.clean[opening+1:end-1]
        arm = re.compile(r'\s*(case\s+('+NUMBER+r')|default)\s*:\s*('+IDENT+r')\s*=\s*('+NUMBER+r')\s*;\s*break\s*;')
        arms = list(arm.finditer(body))
        if not 2 <= len(arms) <= 4 or ''.join(m[0] for m in arms).strip() != body.strip():
            continue
        selectors = {m[3] for m in arms}
        if len(selectors) != 1 or sum(m[2] is None for m in arms) != 1:
            continue
        selector = next(iter(selectors))
        if selector == index or selector not in declarations:
            continue
        value_type = declarations[selector]['type']
        value_kind = integer_abi.scalar(value_type, context, arch)
        if value_kind not in {(32, True), (32, False)}:
            continue
        cases = [(integer(m[2]), integer(m[4])) for m in arms if m[2] is not None]
        fallback = next(integer(m[4]) for m in arms if m[2] is None)
        index_kind = integer_abi.scalar(index_type, context, arch)
        # Only representable positive literals; no signed conversion hypotheses here.
        if len({k for k,_ in cases}) != len(cases) or any(k >= 2**(index_kind[0]-int(index_kind[1])) for k,_ in cases):
            continue
        if any(v >= 2**(value_kind[0]-int(value_kind[1])) for v in [fallback]+[v for _,v in cases]):
            continue
        call = re.match(r'\s*('+IDENT+r')\s*=\s*('+IDENT+r')\s*\(\s*'+re.escape(selector)+r'\s*\)\s*;', part.clean[end:])
        if not call:
            continue
        destination, callee = call[1], call[2]
        stop = end+call.end()
        formals={'idx','last','other','map0','map1','map2','map3'}
        if (formals | {'if','else','static','__inline__','void'}).intersection(macros) or {destination,callee}.intersection(formals):continue
        if (re.search(r'\b'+IDENT+r'\s+\**\s*'+re.escape(callee)+r'\s*(?=[;=,\[])|\*\s*'+re.escape(callee)+r'\b',part.body)
                or re.search(r'\b'+re.escape(callee)+r'\b',part.clean[find(source,name)[0]:part.opening])):continue
        signature = branch_call_support.prototype(header+'\n'+source[:part.opening], callee, arch)
        if not signature or len(signature[1]) != 1 or integer_abi.scalar(signature[1][0], context, arch) != value_kind:
            continue
        if signature[0] == 'void' or destination in declarations or re.search(r'\b'+IDENT+r'\s*\*?\s*'+re.escape(destination)+r'\b', part.body):
            continue
        # The helper must resolve the same global, not a parameter/local shadow.
        declaration = re.search(r'(?:^|[;}])\s*(?:extern\s+)?((?:struct\s+)?'+IDENT+r')\s*\**\s*'+re.escape(destination)+r'\s*;', context[:len(header)+1+part.opening])
        if not declaration:
            continue
        from .pointer_pair_repairs import qualified_types
        if declaration[1] in qualified_types(context) or declaration[1] in macros:continue
        if re.search(r'\b'+re.escape(destination)+r'\b', part.clean[find(source,name)[0]:part.opening]):
            continue
        if macros.intersection(re.findall(IDENT, part.clean[start:stop]) + [index_type, value_type]):
            continue
        node = flow.at(end+call.start(1))
        if not node or node.end != stop or any(selector in flow.nodes[n].live for n in node.successors):
            continue
        # A real loop boundary supplies one additional fallback group. Its formal is
        # independent in the generic helper, even when this caller passes fallback twice.
        bounds = re.findall(r'\b'+re.escape(index)+r'\s*(<|<=|!=)\s*('+NUMBER+r')\b', part.body)
        splits = []
        for operator, value in bounds:
            last = integer(value)-(operator != '<=')
            if 0 <= last < 2**(index_kind[0]-int(index_kind[1])) and last not in {k for k,_ in cases}:
                splits.append(last)
        for split in list(dict.fromkeys(splits))[:2] + [None]:
            groups = list(dict.fromkeys(v for _,v in cases))
            params = [index_type+' idx']+[value_type+f' map{i}' for i in range(len(groups))]
            passed = [index]+[str(v) for v in groups]
            branches = [(k, 'map'+str(groups.index(v))) for k,v in cases]
            if split is not None:
                params.append(value_type+' last'); passed.append(str(fallback)); branches.append((split,'last'))
            params.append(value_type+' other'); passed.append(str(fallback))
            helper = PREFIX+name+'_dispatch'
            operation = lambda value, destination=destination, callee=callee: destination+' = '+callee+'('+value+');'
            definition = 'static __inline__ void '+helper+'('+', '.join(params)+') {\n'
            for pos,(key,value) in enumerate(branches):
                definition += ('    if' if pos == 0 else '    else if')+' (idx == '+str(key)+') { '+operation(value)+' }\n'
            definition += '    else { '+operation('other')+' }\n}\n'
            candidate = edit(source, [(find(source,name)[0],find(source,name)[0],definition),
                                      (start,stop,helper+'('+', '.join(passed)+');')])
            result.append((FAMILY,candidate))
        if result:
            break
    return result[:3]
