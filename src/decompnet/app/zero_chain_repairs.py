"""Split standalone zero assignment chains into independent real local stores.

No observer can see their order: only ordinary unescaped C32 integer locals
with visible bindings and no macro/type ambiguity are eligible.
"""
import re

from ..forks.csource import _blank
from .integer_repair_support import IDENT, edit, view
from .pattern_repairs import close

FAMILY = 'split_local_zero_chain'
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
    aliases = {'int', 'unsigned int'}
    for alias, canonical in (('s32','int'),('u32','unsigned int')):
        definitions = re.findall(r'\btypedef\s+([^;{}]+)\s+'+alias+r'\s*;', context)
        if definitions and all(' '.join(d.split()).removeprefix('signed ') == canonical for d in definitions):
            aliases.add(alias)
    if set(macros) & {'unsigned', 'signed', 'int', 'u32', 's32', name}:
        return []
    declaration = re.compile(r'(?<=[;{}])\s*('+'|'.join(sorted(aliases))+r')\s+('+IDENT+r'(?:\s*,\s*'+IDENT+r')*)\s*;')
    bindings = {}
    for hit in declaration.finditer(part.clean, part.opening, part.end):
        prefix = part.clean[part.opening+1:hit.start()]
        if prefix.count('{') != prefix.count('}') or prefix.count('(') != prefix.count(')'):
            continue
        for variable in re.findall(IDENT, hit[2]):
            bindings.setdefault(variable, []).append((hit.start(),hit.end(),hit[1]))
    eligible = {}
    for variable, spans in bindings.items():
        if len(spans) != 1 or variable in macros or re.search(r'&\s*\(*\s*\b'+variable+r'\b', part.body):
            continue
        # Catch qualified/pointer/unknown-type shadows, including later comma
        # declarators. Ambiguous comma uses are deliberately left to other rules.
        others = part.clean[part.opening+1:spans[0][0]] + ' '*(spans[0][1]-spans[0][0]) + part.clean[spans[0][1]:part.end]
        if re.search(r'(?:\b(?!(?:return|if|while|for|switch|sizeof)\b)'+IDENT+r'(?:\s+|(?=[(*]))|,)\s*[*()\s]*\b'+variable+r'\b', others):
            continue
        # Expression macros elsewhere are allowed, but cannot hide bindings,
        # scope changes or indirect references to the rewritten local.
        if any(re.search(r'\b'+variable+r'\b|[{};#]', replacement)
               for replacement in macros.values()):
            continue
        if any(re.search(r'\b'+macro+r'\s*\([^;{}]*\b'+variable+r'\b', part.body) for macro in macros):
            continue
        eligible[variable] = spans[0][2]
    pattern = re.compile(r'(?<=[;{}])\s*('+IDENT+r')\s*=\s*(?:\(\s*('+IDENT+r')\s*=\s*0\s*\)|('+IDENT+r')\s*=\s*0)\s*;')
    changes = []
    for hit in pattern.finditer(part.clean,part.opening+1,part.end):
        left,right = hit[1],hit[2] or hit[3]
        if (left == right or left not in eligible or right not in eligible
                or eligible[left] != eligible[right]):
            continue
        prefix = part.clean[part.opening+1:hit.start()]
        if prefix.count('(') != prefix.count(')'):
            continue  # Not a standalone statement (for headers/expressions).
        original = source[hit.start():hit.end()]
        if '/*' in original or '//' in original:
            continue
        leading = source[hit.start():hit.start(1)]
        indent = leading.rsplit('\n',1)[-1]
        changes.append((hit.start(),hit.end(),leading+left+' = 0;\n'+indent+right+' = 0;'))
        if len(changes) == 16:
            break
    groups = [changes]+[[c]for c in changes] if len(changes)>1 else [changes]
    return list(dict.fromkeys((FAMILY,edit(source,group))for group in groups if group))[:LIMIT]
