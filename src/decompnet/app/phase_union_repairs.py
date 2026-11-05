"""Share scratch storage across disjoint pointer/integer phases; never reinterpret a stored value."""
import re

from . import integer_abi, local_flow, pointer_pair_repairs
from .allocator_call_guards import dominators
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, edit, locals_for, refs, view


def pointer_declarations(part):
    """Recognize plain pointer initializers using the existing escape/shadow guards."""
    from dataclasses import replace

    initializers = {}
    clean = part.clean
    pattern = re.compile(rf"\b(?:struct\s+)?{IDENT}\s*\*+\s*({IDENT})\s*=([^;{{}}]+);")
    for hit in pattern.finditer(part.clean, part.opening+1, part.end):
        # Self-initializers and comma declarators need a richer declaration parser.
        if re.search(rf"\b{hit[1]}\b|,",hit[2]):
            continue
        start = hit.start(2)-1
        while clean[start] != "=":
            start -= 1
        initializers[hit.start()] = (hit.start(2),hit.end(2))
        clean = clean[:start]+"".join("\n" if c=="\n" else " " for c in clean[start:hit.end()-1])+clean[hit.end()-1:]
    return pointer_pair_repairs.declarations(replace(part,clean=clean)), initializers


def separated(flow, scalar, pointer, transition):
    """A final pointer read may feed the assignment that starts the scalar phase."""
    a,b=scalar["name"],pointer["name"]
    for node in flow.nodes:
        if not {a,b} <= node.live | node.defines:
            continue
        if node.start<=scalar.start()<node.end:
            continue  # An uninitialized declaration does not write the shared storage.
        if node is not transition or a not in node.defines or a in node.uses or b not in node.uses:
            return False
        if any(b in flow.nodes[i].live for i in node.successors):
            return False
    return True


def keep_lines(text, replacement=""):
    return replacement+"\n"*max(0,text.count("\n")-replacement.count("\n"))


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
    pointer_rows, initializers = pointer_declarations(part)
    pointers = [d for d in pointer_rows if not set(re.findall(IDENT,d['type'])) & qualified]
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
            sr,pr = refs(part,scalar),refs(part,pointer)
            if not sr or not pr:
                continue
            transition = flow.at(sr[0].start())
            if not transition or not re.fullmatch(r'\s*'+a+r'\s*=(?!=)[^;{}]+;\s*',part.clean[transition.start:transition.end]):
                continue
            # The only shared-storage write in this statement is its final LHS store.
            rhs = part.clean[sr[0].end():transition.end].split('=',1)[-1]
            if re.search(r'\b(?:'+a+'|'+b+r')\s*(?:[+\-*/%&|^]?=(?!=)|\+\+|--)|(?:\+\+|--)\s*\b(?:'+a+'|'+b+r')\b',rhs):
                continue
            if pr[-1].end()>=sr[0].start() and not (transition.start<=pr[-1].start()<transition.end):
                continue
            if not separated(flow,scalar,pointer,transition):
                continue
            safe = True
            for decl in (scalar,pointer):
                var = decl['name']
                definitions = [i for i,n in enumerate(flow.nodes) if var in n.defines and
                               (not n.start<=decl.start()<n.end or decl.start() in initializers)]
                for i,n in enumerate(flow.nodes):
                    if var in n.uses and not any(j!=i and j in dom.get(i,set()) for j in definitions):
                        safe = False
                if (decl is pointer or width[0]!=32) and any(re.search(r'\b'+var+r'\s*(?:[+\-*/%&|^]=|\+\+|--)|(?:\+\+|--)\s*\b'+var+r'\b',part.clean[n.start:n.end]) for n in flow.nodes):
                    safe = False
            if not safe:
                continue
            fresh = '__repair_phase_storage'
            if fresh in source+'\n'+header:
                continue
            narrow = typ in {'u8','unsigned char'}
            word = 'unsigned int' if narrow else typ
            replacement = f'union {{ {pointer["type"]} pointer; {word} value; }} {fresh}'
            if pointer.start() in initializers:
                begin,end=initializers[pointer.start()]
                replacement += ' = {'+source[begin:end]+' }'
            replacement += ';'
            replacement = keep_lines(source[pointer.start():pointer.end()],replacement)
            edits = [(pointer.start(),pointer.end(),replacement),(scalar.start(),scalar.end(),keep_lines(source[scalar.start():scalar.end()]))]
            edits += [(h.start(),h.end(),fresh+'.pointer') for h in pr]
            covered = set()
            for h in sr if narrow else ():
                assignment = re.match(r'\s*=(?!=)([^;{}]+);',part.clean[h.end():])
                if assignment:
                    node = flow.at(h.start())
                    if not node or part.clean[node.start:h.start()].strip():
                        safe = False;break
                    end = h.end()+assignment.end()
                    rhs_start = h.end()+assignment.start(1)
                    rhs_end = h.end()+assignment.end(1)
                    rhs = source[rhs_start:rhs_end]
                    # Narrow values need an explicit conversion at each assignment; leave
                    # embedded assignments and self-reads to the existing narrow families.
                    if re.search(r'=(?!=)|\b'+a+r'\b',assignment[1]):
                        safe = False;break
                    for hit in reversed(pr):
                        if rhs_start<=hit.start()<hit.end()<=rhs_end:
                            lo,hi=hit.start()-rhs_start,hit.end()-rhs_start
                            rhs=rhs[:lo]+fresh+'.pointer'+rhs[hi:]
                    value = f'({typ})({rhs})' if narrow else rhs
                    edits.append((h.start(),end,keep_lines(source[h.start():end],fresh+'.value = '+value+';')))
                    covered.add(h.start())
            if not safe:
                continue
            for h in sr:
                if h.start() not in covered:
                    value = f'(({typ}){fresh}.value)' if narrow else fresh+'.value'
                    edits.append((h.start(),h.end(),value))
            assignments = [(start,end) for start,end,value in edits if value.startswith(fresh+'.value =')]
            edits = [(start,end,value) for start,end,value in edits if not any(lo<=start<end<=hi and (start,end)!=(lo,hi) for lo,hi in assignments)]
            candidate = edit(source,edits)
            result.append((FAMILIES[0],candidate))
            if len(result)==LIMIT:
                return result
    return list(dict.fromkeys(result))
