"""Keep the consumed old value of an unsigned postdecrement loop guard explicit.

Old compilers can turn while (count--) into an end-value comparison. A separate
old-value test exposes the original latch without changing the zero-test decrement.
Only simple unsigned bindings and braced bodies without control transfers qualify.
"""
import re

from .csource import _blank
from .integer_repair_support import IDENT, edit, view
from .pattern_repairs import close

FAMILY = 'unsigned_countdown_guard'


def variants(source, name, header=''):
    part = view(source, name)
    if not part:
        return []
    context = _blank(source+'\n'+header)
    # Unknown scopes, qualified counters, preprocessor branches and incoming jumps
    # need a CFG/type analysis; do not infer them from this small lexical recognizer.
    if re.search(r'\b(?:volatile|typedef|asm|__asm__|goto|break|continue|switch|do|for)\b|(?<!:):(?!:)', part.body):
        return []
    if re.search(r'(?m)^\s*#\s*(?:if|ifdef|ifndef|else|elif|endif)\b', context):
        return []
    loops = list(re.finditer(rf'\bwhile\s*\(\s*(?P<count>{IDENT})\s*--\s*\)\s*{{', part.clean[part.opening:part.end]))
    if len(loops) != 1 or len(re.findall(r'\bwhile\b', part.body)) != 1:
        return []
    hit = loops[0]
    start, opening = part.opening+hit.start(), part.opening+hit.end()-1
    end = close(part.clean, opening, '{', '}')
    if not end or part.clean[part.opening+1:start].rstrip()[-1:] not in {'', ';', '{', '}'}:
        return []
    if re.match(r'\s*else\b', part.clean[end:]):
        return []
    count = hit['count']
    definition = re.search(r'\b'+re.escape(name)+r'\s*\(([^{};]*)\)\s*{', part.clean[:part.opening+1])
    if not definition:
        return []
    params = definition[1].split(',')
    scalar = rf'(?P<type>unsigned\s+int|u32)\s+{count}'
    param = [m for text in params if (m := re.fullmatch(r'\s*'+scalar+r'\s*', text))]
    local = list(re.finditer(rf'(?:^|(?<=[;{{}}]))\s*{scalar}\s*;', part.body))
    # Exactly one plain binding, with no shadows (including pointers/qualified types).
    declarations = re.findall(rf'\b(?:{IDENT}\s+)+\**\s*{count}\s*(?=[;=,\[)])', part.body)
    if len(param)+len(local) != 1 or len(declarations) != len(local):
        return []
    binding = (param+local)[0]
    kind = binding['type']
    if kind == 'u32':
        typedefs = re.findall(r'\btypedef\s+([^;{}]+)\s+u32\s*;', context)
        if typedefs != ['unsigned int']:
            return []
    # A macro or escaped/shadowed name could change the meaning of the copied guard.
    if re.search(rf'&\s*(?:\(\s*)*{count}\b|,\s*\**\s*{count}\s*(?=[;=\[])', part.body):
        return []
    if re.search(rf'(?m)^\s*#\s*define\s+(?:{count}|u32|unsigned|int|while|if|goto)\b', context):
        return []
    old, loop, check = 'repair_old_count', 'repair_count_body', 'repair_count_check'
    while any(re.search(rf'\b{token}\b', context) for token in (old, loop, check)):
        old += '_'; loop += '_'; check += '_'
    # Keep the original body scope and sequence every read/store. The old value is
    # consumed by the branch; it is not an unused allocator hint or a fake operation.
    replacement = f'{{ {kind} {old}; goto {check}; {loop}: '+source[opening:end]+f' {check}: {old}={count}; {count}--; if({old}) goto {loop}; }}'
    return [(FAMILY, edit(source, [(start, end, replacement)]))]
