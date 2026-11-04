"""Share scratch storage across disjoint pointer/integer phases; never reinterpret a stored value."""
import re

from . import integer_abi, local_flow, pointer_pair_repairs
from .allocator_call_guards import dominators
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, edit, locals_for, refs, view

FAMILIES = ('pointer_integer_phase_union',)
LIMIT = 8


def variants(source,name,arch,flags=(),header=''):
    # This first adapter requires explicit 32-bit code-generation metadata. Do not
    # assume that an x86/ARM family label alone proves pointer width.
    if arch not in {'mips','ppc','x86','arm','thumb'} or not set(flags)&{'-mgp32','-m32','-mabi=32'}:
        return []
    if set(flags)&{'-m64','-mgp64','-mabi=64','-mabi=n32'}:
        return []
    part = view(source,name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__|setjmp|longjmp)\b',part.clean):
        return []
    qualified = pointer_pair_repairs.qualified_types(header+'\n'+source)
    pointers = [d for d in pointer_pair_repairs.declarations(part) if not set(re.findall(IDENT,d['type'])) & qualified]
    scalars = [d for d in locals_for(part) if block_path(part,d.start())==[part.opening]]
    declarations = pointers+scalars
    flow = local_flow.analyze(source,name,declarations=declarations)
    if not flow:
        return []
    dom = dominators(flow)
    macros = re.findall(r'^\s*#\s*define\s+[^\n]+',source+'\n'+header,re.M)
    result = []
    for scalar in scalars:
        typ = scalar['type']
        if typ not in {'u8','unsigned char','u32','s32','int','unsigned int'}:
            continue
        width = integer_abi.scalar(typ,header+'\n'+source,arch)
        if width not in {(8,False),(32,False),(32,True)}:
            continue
        a = scalar['name']
        for pointer in pointers:
            b = pointer['name']
            if any(set(re.findall(IDENT,m)) & {a,b} for m in macros):
                continue
            if not local_flow.disjoint(flow,scalar,pointer):
                continue
            sr,pr = refs(part,scalar),refs(part,pointer)
            if not sr or not pr or pr[-1].end()>=sr[0].start():
                continue  # one pointer phase followed by one integer phase, including loop back edges
            safe = True
            for decl in (scalar,pointer):
                var = decl['name']
                definitions = [i for i,n in enumerate(flow.nodes) if var in n.defines and not n.start<=decl.start()<n.end]
                for i,n in enumerate(flow.nodes):
                    if var in n.uses and not any(j!=i and j in dom.get(i,set()) for j in definitions):
                        safe = False
                if any(re.search(r'\b'+var+r'\s*(?:[+\-*/%&|^]=|\+\+|--)|(?:\+\+|--)\s*\b'+var+r'\b',part.clean[n.start:n.end]) for n in flow.nodes):
                    safe = False
            if not safe:
                continue
            fresh = '__repair_phase_storage'
            if fresh in source+'\n'+header:
                continue
            narrow = typ in {'u8','unsigned char'}
            word = 'unsigned int' if narrow else typ
            replacement = f'union {{ {pointer["type"]} pointer; {word} value; }} {fresh};'
            edits = [(pointer.start(),pointer.end(),replacement),(scalar.start(),scalar.end(),'')]
            edits += [(h.start(),h.end(),fresh+'.pointer') for h in pr]
            covered = set()
            for h in sr:
                assignment = re.match(r'\s*=(?!=)([^;{}]+);',part.clean[h.end():])
                if assignment:
                    node = flow.at(h.start())
                    if not node or part.clean[node.start:h.start()].strip():
                        safe = False;break
                    end = h.end()+assignment.end()
                    rhs_start = h.end()+assignment.start(1)
                    rhs_end = h.end()+assignment.end(1)
                    rhs = source[rhs_start:rhs_end]
                    # Assignment expressions or references to this scalar in its own RHS
                    # need a separate use/definition proof; leave them to existing families.
                    if re.search(r'\b'+a+r'\b|=(?!=)',assignment[1]):
                        safe = False;break
                    value = f'({typ})({rhs})' if narrow else rhs
                    edits.append((h.start(),end,fresh+'.value = '+value+';'))
                    covered.add(h.start())
            if not safe:
                continue
            for h in sr:
                if h.start() not in covered:
                    value = f'(({typ}){fresh}.value)' if narrow else fresh+'.value'
                    edits.append((h.start(),h.end(),value))
            candidate = edit(source,edits)
            result.append((FAMILIES[0],candidate))
            if len(result)==LIMIT:
                return result
    return list(dict.fromkeys(result))
