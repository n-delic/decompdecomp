"""Share scratch storage across disjoint pointer/integer phases; never reinterpret a stored value."""
import re

from . import integer_abi, local_flow, pointer_pair_repairs
from .allocator_call_guards import dominators
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, edit, locals_for, refs, split_args, view
from .pattern_repairs import close


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


FAMILIES = ('pointer_integer_phase_union', 'integer_array_pointer_phase')
LIMIT = 8


def array_variants(source, name, arch, flags=(), header=''):
    """Reuse an ended integer index for a later local-array call address.

    Each active union member is assigned before reading it. Only bare array call
    arguments change; array elements, their stores and the calls keep their order.
    An unrelated volatile result does not disqualify these unescaped local phases.
    """
    if arch not in {'mips','ppc','x86','arm','thumb'} or not set(flags)&{'-mgp32','-m32','-mabi=32'}:
        return []
    if set(flags)&{'-m64','-mgp64','-mabi=64','-mabi=n32'}:
        return []
    part = view(source,name)
    if not part or '__repair_array_phase' in source or re.search(r'\b(?:asm|__asm__|setjmp|longjmp|goto)\b',part.body):
        return []
    from .equal_value_flow import macros_safe
    qualified = pointer_pair_repairs.qualified_types(header+'\n'+source)
    scalars = [d for d in locals_for(part) if not set(re.findall(IDENT,d['type'])) & qualified
               and block_path(part,d.start())==[part.opening]
               and integer_abi.scalar(d['type'],header+'\n'+source,arch) in {(32,True),(32,False)}]
    declarations = {d['name']:d['type'] for d in scalars}
    if not macros_safe(source,header,part.body,declarations):
        return []
    macro_names = re.findall(r'^\s*#\s*define\s+('+IDENT+r')\b',source+'\n'+header,re.M)
    if {'value','pointer'} & set(macro_names) or any(re.search(r'\b'+re.escape(m)+r'\b',part.body) for m in macro_names):
        return []  # Expansion, stringizing and token pasting are not modeled here.
    flow = local_flow.analyze(source,name,declarations=scalars,allow_unrelated_volatile=True)
    if not flow:
        return []
    scalars = flow.locals  # Flow also rejects parenthesized address escapes.
    dom = dominators(flow)
    arrays = list(re.finditer(rf'\b(?P<type>s32|u32|int|unsigned\s+int)\s+(?P<name>{IDENT})\s*\[\s*(?:[1-9][0-9]?)\s*\]\s*;',part.body))
    calls = []
    for hit in re.finditer(rf'(?m)^[ \t]*(?P<callee>{IDENT})\s*\(',part.clean[part.opening+1:part.end-1]):
        start = part.opening+1+hit.start()
        opening = part.opening+hit.end()
        end = close(part.clean,opening)
        if end and re.match(r'\s*;',part.clean[end:]):
            calls.append((start,opening+1,end-1))
    out = []
    for array in arrays:
        buffer = array['name']
        # A unique ordinary local array, never a qualified/shadowed declaration.
        array_pos = part.opening+1+array.start()
        if block_path(part,array_pos)!=[part.opening] or len(re.findall(rf'\b{IDENT}\s+\**\s*{buffer}\s*(?=[;=,\[])',part.body))!=1:
            continue
        if set(re.findall(IDENT,array['type'])) & qualified or re.search(rf'\b(?:volatile|const)\s+[^;{{}}]*\b{buffer}\b',part.body):
            continue
        for scalar in scalars:
            integer = scalar['name']
            if re.search(rf'^\s*#\s*define\b[^\n]*\b(?:{integer}|{buffer})\b',source+'\n'+header,re.M):
                continue
            writes = re.finditer(rf'\b{buffer}\s*\[[^;{{}}]*?\]\s*=(?!=)(?P<rhs>[^;{{}}]+);',part.body)
            if not any(re.search(rf'\[\s*{integer}\s*\]',w['rhs']) for w in writes):
                continue
            sr = refs(part,scalar)
            if not sr:
                continue
            # Every scalar read must have a dominating definition. Address escapes
            # and qualified/shadowed scalars were rejected by locals_for.
            definitions = [i for i,n in enumerate(flow.nodes) if integer in n.defines and not n.start<=scalar.start()<n.end]
            if any(integer in n.uses and not any(j!=i and j in dom.get(i,set()) for j in definitions) for i,n in enumerate(flow.nodes)):
                continue
            uses = []
            for call_start,begin,end in calls:
                if call_start<=sr[-1].end() or block_path(part,call_start)!=[part.opening]:
                    continue
                for lo,hi in split_args(part.clean[begin:end]):
                    if part.clean[begin+lo:begin+hi].strip()==buffer:
                        uses.append((call_start,begin+lo,begin+hi))
            if len(uses)<2:
                continue
            # Do not enter a later branch/loop or cross a label to begin the new
            # phase. Straight-line stores and calls between the phases are fine.
            between = part.clean[sr[-1].end():uses[0][0]]
            if re.search(r'\b(?:if|else|for|while|do|switch|case|return|break|continue)\b|[{}:]|\?',between):
                continue
            replacement = f'union {{ {scalar["type"]} value; {array["type"]} *pointer; }} {integer};'
            edits = [(scalar.start(),scalar.end(),replacement)]
            edits.extend((r.start(),r.end(),integer+'.value') for r in sr)
            edits.extend((lo,hi,integer+'.pointer') for _,lo,hi in uses)
            edits.append((uses[0][0],uses[0][0],f'  /*__repair_array_phase*/\n  {integer}.pointer = {buffer};\n'))
            candidate = edit(source,edits)
            out.append((FAMILIES[1],candidate))
            # A pure local copy immediately following a call can be captured
            # just before it: neither unescaped value is writable by that call.
            changed = view(candidate,name)
            copy = re.compile(rf'(?P<call>^[ \t]*(?P<callee>{IDENT})\s*\([^;{{}}]*\);)\s*\n(?P<copy>[ \t]*(?P<dest>{IDENT})\s*=\s*{integer}\.value\s*;)',re.M)
            for hit in copy.finditer(changed.clean,changed.opening+1,changed.end):
                dest = next((d for d in scalars if d['name']==hit['dest'] and d['type']==scalar['type']),None)
                if hit['callee'] in {'if','while','for','switch','sizeof'} or re.search(r'\+\+|--|<<=|>>=|(?<![=!<>])=(?!=)',hit['call']):
                    continue
                if not dest or re.search(rf'\b{hit["dest"]}\b',hit['call']):
                    continue
                before = candidate[hit.start('copy'):hit.end('copy')]
                call = candidate[hit.start('call'):hit.end('call')]
                out.append((FAMILIES[1],edit(candidate,[(hit.start(),hit.end(),before+'\n'+call)])))
                break
            if len(out)>=LIMIT:
                return list(dict.fromkeys(out))[:LIMIT]
    return list(dict.fromkeys(out))[:LIMIT]


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
