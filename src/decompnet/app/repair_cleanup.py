"""Revisit removable lifetime scaffolding after a structural repair, within ordinary budgets.

Removing a wrapper can change old GCC scheduling even when adding it helped an earlier source.
Only a sole unescaped scalar assignment is unwrapped; declarations and loop control stay scoped.
"""
import re

from .. import compile_timing
from . import branch_call_support
from .integer_repair_support import IDENT, edit, view

FAMILIES = ('revisit_one_shot_scope',)
LIMIT = 8


@compile_timing.timed('cleanup_generation', unless=('proposal_generation',))
def variants(source, name, header='', arch=None):
    part = view(source, name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__)\b', part.body) or '__LINE__' in source+'\n'+header:
        return []
    macros = set(re.findall(r'(?m)^\s*#\s*define\s+('+IDENT+r')', header+'\n'+source))
    pattern = re.compile(r'\bdo\s*{\s*('+IDENT+r')\s*=(?!=)([^;{}]+);\s*}\s*while\s*\(\s*0\s*\)\s*;')
    result = []
    for hit in pattern.finditer(part.clean, part.opening+1, part.end-1):
        variable, expression = hit.groups()
        if macros.intersection(re.findall(IDENT, hit[0])) or not branch_call_support.pure(expression):
            continue
        # The wrapper must be a complete statement, never an arm with a dangling else.
        if part.clean[:hit.start()].rstrip()[-1:] not in ';{}':
            continue
        from .narrow_store_repairs import declared
        kind = declared(part, variable, hit.start(), header)
        from .integer_abi import scalar
        if not kind or kind[1] or scalar(kind[0], header+'\n'+source, arch) is None:
            continue
        if re.search(r'&\s*\(*\s*'+re.escape(variable)+r'\b', part.body):
            continue
        # Preserve the exact inner spelling, including line breaks and comments.
        left = source.find('{', hit.start(), hit.end())+1
        right = source.rfind('}', hit.start(), hit.end())
        result.append((FAMILIES[0], edit(source, [(hit.start(), hit.end(), source[left:right])])) )
        if len(result) == LIMIT:
            break
    return result
