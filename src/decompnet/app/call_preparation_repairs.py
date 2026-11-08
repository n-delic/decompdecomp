"""Typed aggregate dispatch and bounded, independent argument-preparation bundles.

No prototype is inferred or changed. Unknown macros, pointer aliasing, qualifiers and nested
calls fail closed. Direct record objects and unescaped scalar locals have distinct storage;
these modest cases can move assignments into arguments without unsequenced access.
"""
import itertools
import re

from ..forks.csource import _blank, find
from . import branch_call_support as support
from . import integer_abi, local_flow
from .integer_repair_support import IDENT, edit, view

FAMILIES = ('aggregate_pointer_dispatch', 'call_preparation_bundle', 'aggregate_call_preparation', 'direct_field_call_preparation')
PREFIX = '__repair_aggregate_call_'
LIMIT = 12


def records(context, arch=None):
    """Complete plain records only: no union overlap, bitfields, pointers or qualifiers."""
    found = {}
    pattern = r'\btypedef\s+struct(?:\s+'+IDENT+r')?\s*{([^{}]+)}\s*('+IDENT+r')\s*;'
    for hit in re.finditer(pattern, _blank(context)):
        fields = {}
        members = [m.strip() for m in hit[1].split(';') if m.strip()]
        for member in members:
            match = re.fullmatch(r'(.+?)\s+('+IDENT+r')(\s*\[\s*[1-9][0-9]*\s*\])?', member)
            if not match or integer_abi.scalar(match[1], context, arch) is None:
                break
            fields[match[2]] = match[1]
        else:
            if fields:
                found[hit[2]] = fields
    return found


def _calls(source, name, header):
    part = view(source, name)
    if not part or re.search(r'\b(?:volatile|restrict|asm|__asm__|__LINE__|setjmp|longjmp)\b', source+'\n'+header):
        return
    try:
        flow = local_flow.analyze(source, name)
    except (ValueError, RecursionError):
        return
    if not flow:
        return
    macros = set(re.findall(r'(?m)^\s*#\s*define\s+('+IDENT+r')', header+'\n'+source))
    for node in sorted(flow.nodes, key=lambda n:n.start):
        call = support.call(part, node)
        if not call or call[0] or call[1] in macros or call[1].startswith(PREFIX):
            continue
        if macros.intersection(re.findall(IDENT, part.clean[node.start:node.end])):
            continue
        signature = support.prototype(header+'\n'+source[:part.opening], call[1])
        if signature and signature[0] == 'void' and len(signature[1]) == len(call[2]) <= 6:
            yield part, flow, node, call[1], call[2], signature[1]


def dispatch(source, name, header='', arch=None):
    """Pass addressable aggregates through pointer formals; the actual callee keeps its ABI."""
    context = header+'\n'+source
    if PREFIX in context:
        return []
    known = records(context, arch)
    if not known:
        return []
    result = []
    for part, _, node, callee, args, types in _calls(source, name, header):
        aggregate = [i for i,t in enumerate(types) if t in known]
        if not aggregate or any(t not in known and integer_abi.scalar(t, context, arch) is None
                                and not re.fullmatch(IDENT+r'\s*\*', t) for t in types):
            continue
        # No effects/calls are moved across aggregate reads. Subsequent bundle proposals may
        # introduce independent direct-object stores after proving disjoint accesses below.
        if any(re.search(r'(?<![=!<>])=(?!=)|\+\+|--|[,{};?]', a) or re.search(IDENT+r'\s*\(', a)
               for a in args):
            continue
        if any(not re.fullmatch(IDENT+r'(?:\s*\[\s*'+IDENT+r'\s*\])?', args[i]) for i in aggregate):
            continue
        helper = PREFIX+name+'_'+callee
        if re.search(r'\b'+re.escape(helper)+r'\b', context):
            continue
        params = [t+(' *' if i in aggregate else ' ')+f'arg{i}' for i,t in enumerate(types)]
        forwarded = [('*' if i in aggregate else '')+f'arg{i}' for i in range(len(args))]
        passed = ['&('+a+')' if i in aggregate else a for i,a in enumerate(args)]
        definition = 'static __inline__ void '+helper+'('+', '.join(params)+') { '+callee+'('+', '.join(forwarded)+'); }\n'
        start = find(source, name)[0]
        candidate = edit(source, [(start, start, definition),
                                  (node.start, node.end, helper+'('+', '.join(passed)+');')])
        result.append((FAMILIES[0], candidate))
        if len(result) == 2:
            break
    return result


def bundles(source, name, header='', arch=None):
    """Fold at most three adjacent scalar assignments into independent argument expressions.

    Reads through pointers/arrays and global stores are intentionally unsupported: distinct
    spellings do not prove distinct storage. Keep explicit assignment conversions in each fold.
    """
    part = view(source, name)
    if not part:
        return []
    facts = support.facts(source, name, header, arch)
    if not facts:
        return []
    _, flow, scalars = facts
    nodes = sorted(flow.nodes, key=lambda n:n.start)
    context = header+'\n'+source
    macros = set(re.findall(r'(?m)^\s*#\s*define\s+('+IDENT+r')', context))
    result = []
    for pos, node in enumerate(nodes):
        call = support.call(part, node)
        if not call or call[0] or call[1] in macros:
            continue
        args = call[2]
        if not 2 <= len(args) <= 6 or any(not support.local_expression(a, part, scalars, context, arch) for a in args):
            continue
        if part.clean[:node.start].rstrip()[-1:] not in ';{}':
            continue
        prefix = []
        boundary = node.start
        for prev in reversed(nodes[max(0, pos-3):pos]):
            if part.clean[prev.end:boundary].strip():
                break
            match = re.fullmatch(r'\s*('+IDENT+r')\s*=(?!=)\s*(.+);\s*', part.clean[prev.start:prev.end], re.S)
            if not match or match[1] not in scalars or not support.local_expression(match[2], part, scalars, context, None, arithmetic=True):
                break
            if part.clean[:prev.start].rstrip()[-1:] not in ';{}':
                break
            prefix.insert(0, (prev, match[1], match[2].strip()))
            boundary = prev.start
        if not prefix:
            continue
        writes = {v for _,v,_ in prefix}
        if len(writes) != len(prefix):
            continue
        if any(writes.intersection(re.findall(IDENT, rhs)) for _,_,rhs in prefix):
            continue
        slots = []
        for _, var, _ in prefix:
            hits = [i for i,a in enumerate(args) if re.search(r'\b'+re.escape(var)+r'\b', a)]
            if len(hits) != 1 or args[hits[0]] != var:
                break
            slots.append(hits[0])
        if len(slots) != len(prefix) or len(set(slots)) != len(slots):
            continue
        for count in range(1, len(prefix)+1):
            for selected in itertools.combinations(range(len(prefix)), count):
                # Moving a non-terminal assignment crosses the remaining statements; their
                # RHS/local storage is proven independent by the checks above.
                updated = list(args)
                changes = []
                for i in selected:
                    prev,var,rhs = prefix[i]
                    updated[slots[i]] = f'({var} = ({scalars[var]})({rhs}))'
                    changes.append((prev.start, prev.end, ''))
                replacement = call[1]+'('+', '.join(updated)+');'
                result.append((FAMILIES[1], edit(source, changes+[(node.start, node.end, replacement)])))
                if len(result) == LIMIT:
                    return result
    return result


def field_bundles(source, name, header='', arch=None):
    """Move independent fields of direct records into call arguments; never pointer stores."""
    part = view(source, name)
    facts = support.facts(source, name, header, arch)
    if not part or not facts:
        return []
    _, flow, scalars = facts
    from .narrow_store_repairs import declared
    known = records(header+'\n'+source, arch)
    context = header+'\n'+source
    nodes = sorted(flow.nodes, key=lambda n:n.start)
    result = []
    for pos, node in enumerate(nodes):
        call = support.call(part, node)
        if not call or call[0] or not support.prototype(context[:len(header)+1+part.opening], call[1]):
            continue
        args = call[2]
        stores = []
        boundary = node.start
        for previous in reversed(nodes[max(0,pos-2):pos]):
            if part.clean[previous.end:boundary].strip():
                break
            match = re.fullmatch(r'\s*('+IDENT+r')\.('+IDENT+r')\s*=(?!=)\s*('+IDENT+r');\s*',
                                 part.clean[previous.start:previous.end])
            if not match:
                break
            obj, field, value = match.groups()
            record = declared(part, obj, previous.start, header)
            if not record or record[1] or record[0] not in known or value not in scalars:
                break
            if field not in known[record[0]] or re.search(r'&\s*\(*\s*'+re.escape(obj)+r'\b', part.body):
                break
            if part.clean[:previous.start].rstrip()[-1:] not in ';{}':
                break
            hits = [i for i,a in enumerate(args) if re.search(r'\b'+re.escape(value)+r'\b', a)]
            if len(hits) != 1 or args[hits[0]] != value:
                break
            if any(re.search(r'\b'+re.escape(obj)+r'\b', a) for a in args):
                break
            stores.insert(0, (previous, obj, field, hits[0]))
            boundary = previous.start
        if not stores or len({(o,f) for _,o,f,_ in stores}) != len(stores):
            continue
        # Other args are pure locals or distinct direct aggregate values. An arbitrary load
        # or pointer can alias a moved field even when its spelling differs.
        for arg in args:
            if support.local_expression(arg, part, scalars, context, arch):
                continue
            record = declared(part, arg, node.start, header) if re.fullmatch(IDENT,arg) else None
            if not record or record[1] or record[0] not in known:
                break
        else:
            for size in range(1, len(stores)+1):
                for selected in itertools.combinations(stores, size):
                    updated = list(args)
                    changes = []
                    for previous, obj, field, slot in reversed(selected):
                        statement = source[previous.start:previous.end].strip().rstrip(';')
                        updated[slot] = '('+statement+', '+updated[slot]+')'
                        changes.append((previous.start, previous.end, ''))
                    changes.append((node.start,node.end,call[1]+'('+', '.join(updated)+');'))
                    result.append((FAMILIES[3], edit(source, changes)))
        if len(result) >= 6:
            break
    return result[:6]


def variants(source, name, header='', arch=None):
    result = dispatch(source, name, header, arch)+bundles(source, name, header, arch)+field_bundles(source, name, header, arch)
    # Preserve combinations even when the first shape is numerically inert.
    for _, first in result[:2]:
        for _, combined in bundles(first, name, header, arch)[:3]:
            result.append((FAMILIES[2], combined))
    return list(dict.fromkeys(result))[:LIMIT]
