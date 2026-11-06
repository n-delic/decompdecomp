"""Capture a real component index shared by a load macro and an array read.

Keep values/types/evaluation order; never insert a dummy operation or guard.
Only direct C32 builds and expression-only visible macros are eligible.
"""
import re

from ..forks.csource import _blank
from . import local_flow
from .allocator_call_guards import dominators
from .integer_repair_support import IDENT, edit, split_args, view
from .pattern_repairs import close
from .permuter_macro_effects import DEFINE

FAMILY = 'component_index_capture'
LIMIT = 8


def variants(source, name, header='', *, language=None, int_bits=None):
    if language != 'c' or int_bits != 32 or max(len(source), len(header)) > 64000:
        return []
    context = _blank(source+'\n'+header)
    if (re.search(r'\\(?:\r\n?|\n)|\?\?|%:',source+'\n'+header)
            or any(d != 'define' for d in re.findall(r'^\s*#\s*(\w+)',context,re.M))
            or re.search(r'\b(?:asm|__asm|__asm__|goto|setjmp|longjmp|_Pragma|typeof|__typeof__|_Generic|__LINE__|__COUNTER__|__attribute__|__declspec|class|template|namespace)\b|\b__builtin_(?!sqrtf\b)\w+',context)):
        return []
    part = view(source,name)
    flow = local_flow.analyze(source,name)
    if not part or not flow or re.search(r'\bsizeof\b',part.body):
        return []
    part.end = close(part.clean,part.opening,'{','}')
    if not part.end:
        return []
    dominance = dominators(flow)
    definitions = {}
    for hit in DEFINE.finditer(context):
        if hit[1] in definitions:
            return []  # Redefinition/conditional preprocessing needs invocation evidence.
        definitions[hit[1]] = (hit[2],hit[3])
    if definitions.keys() & {'int',name}:
        return []
    def balanced(text):
        stack = []
        for char in text:
            if char in '([':
                stack.append(char)
            elif char in ')]':
                if not stack or stack.pop() != {')':'(',']':'['}[char]:
                    return False
        return not stack

    safe = set()
    for _ in range(16):
        before = set(safe)
        for macro,(parameters,body) in definitions.items():
            words = set(re.findall(IDENT,body))
            params = set(re.findall(IDENT,parameters or ''))
            if (not body.strip() or not balanced(body) or re.search(r'[{};#=?:]|\+\+|--|&&|\|\||\b(?:if|else|for|while|do|switch|case|return|break|continue|typedef|sizeof)\b',body)
                    or any(c in body for c in '\"\'\\')
                    or (words-params) & (definitions.keys()-safe)):
                continue
            safe.add(macro)
        if before == safe:
            break
    used = set(re.findall(IDENT,part.body)) & definitions.keys()
    if not used <= safe:
        return []  # Hidden control/scope changes cannot bypass the initialization.
    fresh = '__repair_component_index'
    names = set(re.findall(IDENT,context))
    while fresh in names:
        fresh += '_'

    def assignment_at(position):
        node = flow.at(position)
        if node is None:
            return None
        text = part.clean[node.start:node.end]
        # Only RHS reads of ordinary assignment statements, not declarators,
        # control conditions, labels, or assignment expressions in for headers.
        hit = re.match(r'\s*'+IDENT+r'\s*(?:\[[^;{}]*?\]\s*)?=(?!=)',text)
        if not hit or position < node.start+hit.end():
            return None
        return node

    def outer(node):
        prefix = part.clean[part.opening+1:node.start]
        return prefix.count('{') == prefix.count('}') and prefix.count('(') == prefix.count(')')

    # A declaration before the first ordinary top-level assignment fits the
    # old compilers' declaration block in the measured source. Unsupported
    # declaration placement still has to compile in the unchanged judge.
    declarations = [node for node in flow.nodes if outer(node)
                    and re.match(r'\s*'+IDENT+r'\s*=(?!=)',part.clean[node.start:node.end])]
    if not declarations:
        return []
    declaration_at = min(node.start for node in declarations)
    arrays = []
    for hit in re.compile(r'\b'+IDENT+r'\s*\[\s*([12])\s*\]').finditer(part.clean,part.opening+1,part.end):
        node = assignment_at(hit.start(1))
        if node:
            arrays.append((hit[1],hit.start(1),hit.end(1),node))
    macros = []
    for hit in re.compile(r'\b('+IDENT+r')\s*\(').finditer(part.clean,part.opening+1,part.end):
        if hit[1] not in safe or definitions[hit[1]][0] is None:
            continue
        end = close(part.clean,hit.end()-1)
        if not end:
            continue
        text = part.clean[hit.end():end-1]
        for a,b in split_args(text):
            value = text[a:b].strip()
            if value not in {'1','2'}:
                continue
            start = hit.end()+a+len(text[a:b])-len(text[a:b].lstrip())
            node = assignment_at(start)
            if node and outer(node) and node.start >= declaration_at:
                macros.append((value,start,start+1,node))
    result = []
    for value,a,b,first in macros:
        for other,c,d,last in arrays:
            if (value != other or c <= b or first.start >= last.start
                    or flow.nodes.index(first) not in dominance.get(flow.nodes.index(last),set())):
                continue
            # CFG dominance also covers unbraced conditionals and zero-iteration
            # loops: every selected read must pass the initialization.
            changes = [(declaration_at,declaration_at,'int '+fresh+';\n'),
                       (first.start,first.start,fresh+' = '+value+';\n'),
                       (a,b,fresh),(c,d,fresh)]
            result.append((FAMILY,edit(source,changes)))
            if len(result) >= LIMIT:
                return list(dict.fromkeys(result))
    return list(dict.fromkeys(result))
