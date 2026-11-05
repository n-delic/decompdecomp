"""Bounded permutations of independent, pure local setup before a loop or label.

Only integer/function-address expressions are accepted. No operation crosses a
call, memory access or control edge; the destination judge still decides matches.
"""
import itertools
import re

from ..forks.csource import _blank
from .integer_repair_support import IDENT, NUMBER, edit, view
from .pattern_repairs import close

FAMILY = 'pure_preheader_order'
LIMIT = 6
INTEGER = r'(?:unsigned(?:\s+(?:int|long|short|char))?|signed(?:\s+(?:int|long|short|char))?|int|long|short|char|[su](?:8|16|32|64))'


def variants(source, name, header='', *, language=None, int_bits=None):
    # Reuse the current macro-free direct-C invocation proof; source text alone
    # cannot reveal -D/-include or a custom compiler command's hidden qualifiers.
    if language != 'c' or int_bits != 32:
        return []
    if max(len(source), len(header)) > 128000:
        return []
    part = view(source, name)
    if not part:
        return []
    part.end = close(part.clean, part.opening, '{', '}')
    context = _blank(source+'\n'+header)
    if (not part.end or re.search(r'\\(?:\r\n?|\n)|\?\?|%:', source+'\n'+header)
            or re.search(r'\b(?:class|template|namespace|operator|asm|__asm|__asm__|setjmp|longjmp|_Pragma|__LINE__|__COUNTER__|__FILE__)\b', context)
            or any(d != 'define' for d in re.findall(r'^\s*#\s*(\w+)', context, re.M))):
        return []
    if re.search(r'\b(?:struct|union|enum|typedef|typeof|__typeof__|__auto_type|__attribute__|__declspec|_Generic|_Atomic)\b',part.body):
        return []
    macros = set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')\b', context, re.M))
    if macros & {'void', 'static', 'extern', 'typedef', 'int', 'unsigned', 'signed', 'long', 'short', 'char', name}:
        return []
    # Explicit callback typedefs are scalar objects; their argument types are not rewritten.
    callbacks = set(re.findall(r'\btypedef\s+void\s*\(\s*\*\s*('+IDENT+r')\s*\)\s*\([^();{}]*\)\s*;', context))
    aliases = set(re.findall(r'\btypedef\s+(?:(?:unsigned|signed)\s+)?(?:int|long|short|char)(?:\s+(?:int|long))?\s+([su](?:8|16|32|64))\s*;',context))
    types = callbacks | set(re.findall(r'\b'+INTEGER+r'\b', context))
    types = {t for t in types if not re.fullmatch(r'[su]\d+',t) or t in aliases}
    declarations = {}
    pattern = r'\b('+INTEGER+('|'+'|'.join(re.escape(t) for t in callbacks) if callbacks else '')+r')\s+('+IDENT+r')\s*(?=[;,=)])'
    # Include parameters and local declarations, not globals or struct fields.
    signature = re.search(r'\b'+re.escape(name)+r'\s*\([^{};]*\)\s*$',part.clean[:part.opening])
    local_context = (signature[0] if signature else '')+'\n'+part.body
    if macros & set(re.findall(IDENT,local_context)):
        return []  # A declaration macro can hide volatile/atomic qualification.
    for hit in re.finditer(pattern, local_context):
        variable = hit[2]
        if signature and hit.start() < len(signature[0]):
            prefix = local_context[:hit.start()]
            own_scope = prefix.count('(')-prefix.count(')') == 1 and prefix.rstrip()[-1:] in {'(', ','}
        else:
            prefix = part.body[:hit.start()-(len(signature[0]) if signature else 0)-1]
            own_scope = (prefix.count('{') == prefix.count('}') and prefix.count('(') == prefix.count(')')
                         and prefix.rstrip()[-1:] in {'', ';', '{', '}'})
        declarations.setdefault(variable, []).append((hit[1],own_scope))
    # Unparsed pointer/array declarators, ambiguous shadows and escapes are ineligible.
    eligible = {}
    for variable, definitions in declarations.items():
        kinds = [kind for kind,_ in definitions]
        if (len(definitions) == 1 and definitions[0][1] and kinds[0] in types and kinds[0] not in macros and variable not in macros
                and len(re.findall(r'\b(?:'+INTEGER+r'|'+IDENT+r')\s+\**\s*'+re.escape(variable)+r'\s*(?=[;,=)\[])',local_context)) == 1
                and not re.search(r'&\s*\b'+re.escape(variable)+r'\b|\b(?:volatile|_Atomic|static|extern|register)\b[^;{}]*\b'+re.escape(variable)+r'\b',local_context)):
            eligible[variable] = kinds[0]
    functions = set(re.findall(r'(?:^|[;{}])\s*(?:extern\s+)?void\s+('+IDENT+r')\s*\([^();{}]*\)\s*[;{]',context,re.M))
    statement = re.compile(r'(?<=[;{}])\s*('+IDENT+r')\s*=\s*([^;{}]+);')
    statements = list(statement.finditer(part.clean,part.opening+1,part.end-1))
    result = []
    for end_index, last in enumerate(statements):
        tail = part.clean[last.end():part.end-1]
        if not re.match(r'\s*(?:'+IDENT+r'\s*:(?!:)|(?:for|while)\s*\(|do\s*{)',tail):
            continue
        group = [last]
        for previous in reversed(statements[max(0,end_index-2):end_index]):
            if part.clean[previous.end():group[0].start()].strip():
                break
            group.insert(0,previous)
        if len(group) < 2:
            continue
        # An unbraced conditional or for header must not become a setup sequence.
        prefix = part.clean[part.opening+1:group[0].start()]
        if prefix.count('(') != prefix.count(')'):
            continue
        destinations = {h[1] for h in group}
        if len(destinations) != len(group) or not destinations <= eligible.keys():
            continue
        valid = True
        for hit in group:
            expr = hit[2]
            words = set(re.findall(IDENT,re.sub(r'(?<![\w.])'+NUMBER+r'(?![\w.])',' ',expr)))
            raw_expression = source[hit.start(2):hit.end(2)]
            if (not expr.strip() or any(q in raw_expression for q in (chr(34),chr(39))) or words & macros or words & destinations
                    or not words <= (eligible.keys() | functions | types)
                    or re.search(r'[^\w\s()+\-|^~<>]',expr)
                    or re.search(r'\+\+|--|->|(?<!<)<(?!<)|(?<!>)>(?!>)',expr)
                    or re.search(r'\b'+IDENT+r'\s*\(',expr)
                    or any(eligible.get(w) in callbacks and w not in destinations for w in words)
                    or expr.count('(') != expr.count(')')):
                valid = False
                break
        if not valid:
            continue
        texts = [source[h.start():h.end()].strip() for h in group]
        original = tuple(range(len(texts)))
        for order in itertools.permutations(original):
            if order != original:
                result.append((FAMILY,edit(source,[(group[0].start(),group[-1].end(),'\n'+'\n'.join(texts[i] for i in order))])))
            if len(result) >= LIMIT:
                return list(dict.fromkeys(result))
    return list(dict.fromkeys(result))
