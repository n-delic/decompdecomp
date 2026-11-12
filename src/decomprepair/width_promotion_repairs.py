"""A narrow return type and the locals that feed it, written as `int`.

m2c takes the width from the last load or store (`u8 var_a0`, `u8` return). The original returned `int`, so
the compiler no longer narrows the value after each update: the `& 0xFF` after a shift-and-add disappears.
Three alternatives per narrow type: the return type alone, the same-typed locals alone, and both.
"""
import re

from .integer_repair_support import edit, view

FAMILY = 'width_promotion'
FAMILIES = (FAMILY,)
_NARROW = r'(?:u8|s8|u16|s16|unsigned\s+char|signed\s+char|char|unsigned\s+short|short)'


def variants(source, name):
    part = view(source, name)
    if not part:
        return []
    clean = part.clean
    definition = re.search(rf'(?m)^({_NARROW})(\s+\**\s*{re.escape(name.split("::")[-1])}\s*\()', clean[:part.opening])
    if not definition or '*' in definition[2]:
        return []
    narrow = re.sub(r'\s+', ' ', definition[1])
    returned = (definition.start(1), definition.end(1), 'int')
    locals_ = [(part.opening + 1 + d.start(1), part.opening + 1 + d.end(1), 'int')
               for d in re.finditer(rf'(?m)^[ \t]+({_NARROW})\s+\w+\s*(?:=[^;]*)?;', clean[part.opening+1:part.end])
               if re.sub(r'\s+', ' ', d[1]) == narrow]
    result = [(FAMILY, edit(source, [returned]))]
    if locals_:
        result += [(FAMILY, edit(source, locals_)), (FAMILY, edit(source, [returned] + locals_))]
    return result
