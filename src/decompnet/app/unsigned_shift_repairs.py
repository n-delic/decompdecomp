"""Stage unsigned shift/add packing without moving neighboring statements.

The extra assignment can change local/global register allocation in old GCC.
Only unescaped ordinary unsigned-int locals with visible declarations qualify.
"""
import re

from ..forks.csource import _blank
from .integer_repair_support import IDENT, NUMBER, edit, view
from .pattern_repairs import close

FAMILY = 'unsigned_shift_staging'
LIMIT = 5


def variants(source, name, header='', *, language=None, int_bits=None):
    if language != 'c' or int_bits != 32 or max(len(source), len(header)) > 128000:
        return []
    context = _blank(source+'\n'+header)
    if (re.search(r'\\(?:\r\n?|\n)|\?\?|%:', source+'\n'+header)
            or any(d != 'define' for d in re.findall(r'^\s*#\s*(\w+)', context, re.M))
            or re.search(r'\b(?:asm|__asm|__asm__|setjmp|longjmp|_Pragma|class|namespace|template|__attribute__|__declspec|typeof|__typeof__|__auto_type|_Generic|__LINE__|__COUNTER__)\b', context)):
        return []
    part = view(source, name)
    if not part:
        return []
    part.end = close(part.clean, part.opening, '{', '}')
    if not part.end:
        return []
    macros = dict(re.findall(r'^\s*#\s*define\s+('+IDENT+r')([^\n]*)', context, re.M))
    aliases = {'unsigned int'}
    for alias in ('u32',):
        definitions = re.findall(r'\btypedef\s+([^;{}]+)\s+'+alias+r'\s*;', context)
        if definitions and all(' '.join(d.split()) == 'unsigned int' for d in definitions):
            aliases.add(alias)
    if set(macros) & {'unsigned', 'int', 'u32', name}:
        return []
    declaration = re.compile(r'(?<=[;{}])\s*('+'|'.join(sorted(aliases))+r')\s+('+IDENT+r'(?:\s*,\s*'+IDENT+r')*)\s*;')
    bindings = {}
    for hit in declaration.finditer(part.clean, part.opening, part.end):
        prefix = part.clean[part.opening+1:hit.start()]
        if prefix.count('{') != prefix.count('}') or prefix.count('(') != prefix.count(')'):
            continue
        for variable in re.findall(IDENT, hit[2]):
            bindings.setdefault(variable, []).append(hit.span())
    eligible = set()
    for variable, spans in bindings.items():
        if len(spans) != 1 or variable in macros or re.search(r'&\s*\(*\s*\b'+variable+r'\b', part.body):
            continue
        # Catch qualified/pointer/unknown-type shadows, including later comma
        # declarators. Ambiguous comma uses are deliberately left to other rules.
        others = part.clean[part.opening+1:spans[0][0]] + ' '*(spans[0][1]-spans[0][0]) + part.clean[spans[0][1]:part.end]
        if re.search(r'(?:\b(?!return\b)'+IDENT+r'(?:\s+|(?=[(*]))|,)\s*[*()\s]*\b'+variable+r'\b', others):
            continue
        # Expression macros elsewhere are allowed, but cannot hide bindings,
        # scope changes or indirect references to the rewritten local.
        if any(re.search(r'\b'+variable+r'\b|[{};#]', replacement)
               for replacement in macros.values()):
            continue
        if any(re.search(r'\b'+macro+r'\s*\([^;{}]*\b'+variable+r'\b', part.body) for macro in macros):
            continue
        eligible.add(variable)
    pattern = re.compile(r'(?<=[;{}])\s*('+IDENT+r')\s*=\s*\(\s*('+IDENT+r')\s*<<\s*('+NUMBER+r')\s*\)\s*\+\s*('+NUMBER+r')\s*;')
    changes = []
    for hit in pattern.finditer(part.clean, part.opening+1, part.end):
        if not {hit[1], hit[2]} <= eligible or hit[1] == hit[2]:
            continue
        prefix = part.clean[part.opening+1:hit.start()]
        if prefix.count('(') != prefix.count(')'):
            continue
        def number(text):
            value = text.rstrip('uUlL')
            return int(value, 16 if value.lower().startswith('0x') else 8 if value.startswith('0') else 10)
        try:
            shift, tag = number(hit[3]), number(hit[4])
        except ValueError:
            continue
        if not 1 <= shift <= 31 or not 1 <= tag <= 0xffffffff:
            continue
        # Both are ordinary local stores; no observer can run between them.
        changes.append((hit.start(), hit.end(), '\n'+hit[1]+' = '+hit[2]+' << '+hit[3]+'; '+hit[1]+' += '+hit[4]+';'))
        if len(changes) == 32:
            break
    if not changes:
        return []
    groups = [changes] + [[c] for c in changes] if len(changes) > 1 else [changes]
    return list(dict.fromkeys((FAMILY, edit(source, group)) for group in groups))[:LIMIT]
