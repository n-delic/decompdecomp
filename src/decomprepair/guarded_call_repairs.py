"""Reuse an equality's stable local value in calls inside its true arm.

Only unescaped automatic integers are replaced. Keep the condition, captures and
observable calls; ordinary compilation still decides whether a proposal helps.
"""
import re

from . import integer_abi
from .call_lifetime_repairs import block_path
from .csource import _blank
from .integer_repair_support import IDENT, INTEGER, edit, locals_for
from .pattern_repairs import close
from .repair_source_view import read

FAMILY = 'guarded_call_value'
MAX_GROUPS = 8
MAX_CALLS = 256
MAX_EDITS = 32


def _strip(text):
    text = text.strip()
    while text.startswith('(') and close(text, 0) == len(text):
        text = text[1:-1].strip()
    return text


def _last(text, operator):
    """Only the final comma/conjunction operand supplies a fact; earlier writes stay intact."""
    depth, last, pos = 0, 0, 0
    while pos < len(text):
        char = text[pos]
        if char in '([{':
            depth += 1
        elif char in ')]}':
            depth -= 1
        elif depth == 0 and text.startswith(operator, pos):
            last = pos+len(operator)
            pos += len(operator)-1
        pos += 1
    return text[last:]


def _fact(condition):
    old = None
    while condition != old:
        old = condition
        condition = _strip(_last(_strip(_last(_strip(condition), ',')), '&&'))
    hit = re.fullmatch(r'('+IDENT+r')\s*==\s*(.+)', condition, re.S)
    return hit.groups() if hit else None


def _writes(text, var):
    word = re.escape(var)
    return bool(re.search(r'\b'+word+r'(?:\s*\))*\s*(?:[+*/%&|^\-]?=(?!=)|<<=|>>=|\+\+|--)|(?:\+\+|--)\s*(?:\(\s*)*\b'+word+r'\b', text))


def _binding(part, var):
    # Also reject typedef/qualified/pointer shadows outside the scalar local parser.
    declarations = list(re.finditer(r'(?:^|[;{}])\s*((?:'+IDENT+r'\s+)+(?:\*\s*)*)'+re.escape(var)+r'\s*(?=[;=,\[])', part.body))
    declarations = [d for d in declarations if not re.match(r'(?:return|goto|break|continue)\b', d[1])]
    if (len(declarations) != 1 or re.search(r'\bfor\s*\([^;{}]*\b'+re.escape(var)+r'\s*[=;]', part.body)
            or re.search(r'\(\s*\*\s*'+re.escape(var)+r'\b', part.body)):
        return None
    if re.search(r'(?<!&)&(?!&)\s*(?:\(\s*)*'+re.escape(var)+r'\b', part.body):
        return None
    return declarations[0][1].strip()


def _operand(text, var):
    """A value or scalar cast chain, never arithmetic, sizeof or another evaluation."""
    text = _strip(text)
    while True:
        cast = re.match(r'^\(\s*'+INTEGER+r'\s*\)\s*', text)
        if not cast:
            return text == var
        text = _strip(text[cast.end():])


def _constant_capture(part, var, at, macros, context, arch):
    """Reuse the exact pure captured expression, never assume pointer casts preserve numbers."""
    if re.search(r'\b(?:for|while|do)\b', part.body):
        return None  # A back edge could carry a later write past the lexical test point.
    assignment = re.compile(r'\b'+re.escape(var)+r'\s*=(?!=)\s*([^;{}]+);')
    for hit in reversed(list(assignment.finditer(part.clean, part.opening+1, at))):
        before = part.clean[part.opening+1:hit.start()].rstrip()
        between = part.clean[hit.end():at]
        path = block_path(part, hit.start())
        if (before and before[-1] not in ';{}') or block_path(part, at)[:len(path)] != path:
            continue
        if (_writes(between, var) or re.search(r'\b(?:case|default)\b|(?:^|[;{}])\s*'+IDENT+r'\s*:(?!:)', between)
                or any(re.search(r'\b'+re.escape(m)+r'\b', between) for m in macros)):
            continue
        value = _strip(hit[1])
        while True:
            cast = re.match(r'^\(\s*(void\s*\*|'+INTEGER+r')\s*\)\s*', value)
            if not cast:
                break
            if cast[1].replace(' ', '') != 'void*' and not integer_abi.scalar(cast[1], context, arch):
                break
            value = _strip(value[cast.end():])
        if re.fullmatch(r'(?:0|[1-9][0-9]?)', value):
            return '('+_binding(part, var)+')('+' '.join(hit[1].split())+')'
    return None


def _parameters(context, callee, arch):
    """Visible, consistent by-value prototypes rule out references and overload selection."""
    pattern = (r'(?:^|(?<=[;{}]))\s*(?:(?:extern|static|inline)\s+)*'
               r'(?:'+INTEGER+r'|void|float|double|'+IDENT+r')(?:(?:\s*\*)+\s*|\s+)'+re.escape(callee)
               +r'\s*\(([^()]*)\)\s*(?:;|{)')
    signatures = []
    for match in re.finditer(pattern, context, re.M):
        if re.match(r'\s*(?:return|else|goto)\b', match[0].lstrip(';{}')):
            continue
        params = []
        for spelling in match[1].split(','):
            spelling = spelling.strip()
            if not integer_abi.scalar(spelling, context, arch) and not spelling.endswith('*'):
                named = re.fullmatch(r'(.+[\s*])'+IDENT, spelling)
                if named:
                    spelling = named[1].strip()
            params.append(' '.join(spelling.split()))
        signatures.append(tuple(params))
    return signatures[0] if signatures and len(set(signatures)) == 1 else ()


def _by_value(spelling, context, arch):
    return bool(integer_abi.scalar(spelling, context, arch) or
                re.fullmatch(r'(?:'+IDENT+r'\s+)*'+IDENT+r'\s*\*+(?:\s*\*)*', spelling))


def _c_subset(source):
    """Do not interpret unknown C++ type/constructor syntax as ordinary C locals."""
    try:
        from pycparser import CParser, c_parser
    except ImportError:
        return False  # Minimal runtimes stay conservative; never install a parser while searching.
    tokens = r'/\*[\s\S]*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''
    clean = re.sub(tokens, lambda m: re.sub(r'[^\n]', ' ', m[0])
                   if m[0].startswith(('/*','//')) else m[0], source)
    clean = re.sub(r'(?m)^[^\S\n]*#[^\n]*', lambda m: ' '*len(m[0]), clean)
    clean = re.sub(r'\b__inline__?\b', 'inline', clean)
    try:
        CParser().parse(clean)
        return True
    except (c_parser.ParseError, ValueError, RecursionError):
        return False


def variants(source, name, header='', arch=None):
    """Propose bounded, type-preserving call values from dominating narrow-local equalities."""
    if len(source)+len(header) > 256000:
        return []
    part = read(source, name)
    if not part or re.search(r'\b(?:volatile|_Atomic|asm|__asm__|setjmp|longjmp|__builtin_\w+|__auto_type|sizeof|typeof|__typeof__|decltype|_Generic)\b', part.body):
        return []
    if any(m[1] not in {'else','return','case','goto'} for m in re.finditer(r'(?:^|[;{}])\s*('+IDENT+r')\s+'+IDENT+r'\s*\([^;{}]*\)\s*{', part.body)):
        return []
    raw = header+'\n'+source
    if re.search(r'\bR"', raw):
        return []
    context = _blank(raw)
    if (re.search(r'\b(?:class|template|namespace|operator)\b|::', context)
            or re.search(r'\(\s*{|\[[^]]*\]\s*(?:\([^;{}]*\)\s*)?(?:mutable\s*)?{', part.body)
            or re.search(r'\b'+IDENT+r'\s*\**\s*&{1,2}\s*(?:'+IDENT+r'\b|[),;])', context)):
        return []
    if re.search(r'\\(?:\r\n?|\n)|\?\?|%:', context):
        return []
    directives = re.findall(r'^\s*#\s*(\w+)', context, re.M)
    if any(d != 'define' for d in directives):
        return []
    definitions = re.findall(r'^\s*#\s*define\s+('+IDENT+r')\b([^\n]*)', context, re.M)
    macros = dict(definitions)
    if (set(macros) & {'if','else','case','default','goto','switch','do','for','while','return','void'}
            or any('#' in body for _, body in definitions)):
        return []
    locals_ = {d['name']: d for d in locals_for(part)}
    calls = list(re.finditer(r'\b('+IDENT+r')\s*\(', part.body))
    if len(calls) > MAX_CALLS:
        return []
    groups = []
    parameter_cache = {}
    for hit in re.finditer(r'\bif\s*\(', part.body):
        at = part.opening+1+hit.end()-1
        stop = close(part.clean, at)
        arm = re.match(r'\s*{', part.clean[stop:]) if stop else None
        if not arm:
            continue
        opening = stop+arm.end()-1
        end = close(part.clean, opening, '{', '}')
        fact = _fact(part.clean[at+1:stop-1])
        if not end or not fact:
            continue
        var, rhs = fact
        decl = locals_.get(var)
        abi = integer_abi.scalar(decl['type'], context, arch) if decl else None
        body = part.clean[opening+1:end-1]
        if any(re.search(r'\b'+re.escape(m)+r'\b', body+part.clean[at+1:stop-1]) for m in macros):
            continue
        if (not abi or abi[0] not in (8, 16) or not _binding(part, var)
                or _writes(body, var) or re.search(r'\b(?:case|default)\b|(?:^|[;{}])\s*'+IDENT+r'\s*:(?!:)', body)):
            continue  # No label may bypass the equality on entry to this arm.
        rhs = _strip(rhs)
        dependencies = set()
        if not re.fullmatch(r'(?:0|[1-9][0-9]?)', rhs):
            cast = re.fullmatch(r'\(\s*('+INTEGER+r')\s*\)\s*\(*\s*('+IDENT+r')\s*\)*', rhs)
            rhs_abi = integer_abi.scalar(cast[1], context, arch) if cast else None
            if not cast or rhs_abi != (32, True) or abi[1]:
                continue  # Unsigned narrow == signed32 has no lossy comparison conversion.
            bound = _binding(part, cast[2])
            if not bound or (not re.fullmatch(r'void\s*\*', bound) and not integer_abi.scalar(bound, context, arch)):
                continue
            dependencies.add(cast[2])
            captured = _constant_capture(part, cast[2], stop, macros, context, arch)
            if captured is not None:
                rhs = '('+cast[1]+')('+captured+')'
        if any(_writes(body, word) for word in dependencies):
            continue
        values = {var: (decl['type'], 0)}
        # A leading same-width copy is dominated by the guard and dominates later calls.
        copy = re.match(r'\s*('+IDENT+r')\s*=\s*'+re.escape(var)+r'\s*;', body)
        if copy and copy[1] in locals_:
            dest = locals_[copy[1]]
            if (_binding(part, copy[1]) and integer_abi.scalar(dest['type'], context, arch) == abi
                    and not _writes(body[copy.end():], copy[1])):
                values[copy[1]] = (dest['type'], copy.end())
        protected = set(values) | dependencies
        if any(set(re.findall(IDENT, macro)) & protected for _, macro in definitions):
            continue
        if any(re.search(r'\b'+re.escape(m)+r'\s*\([^;{}]*\b'+re.escape(v)+r'\b', part.body) for m in macros for v in protected):
            continue  # An earlier macro invocation could have escaped this local without a visible &.
        edits = []
        uncertain_call = False
        for call in calls:
            if call[1] in macros or call[1] in {'if','while','for','switch','sizeof'}:
                continue
            begin = part.opening+1+call.end()
            finish = close(part.clean, begin-1)
            if not finish or finish > part.end:
                continue
            # Parenthesis depth is enough here: eligible operands contain only casts/identifiers.
            args, start, depth = [], begin, 0
            for pos in range(begin, finish-1):
                char = part.clean[pos]
                depth += (char in '([{')-(char in ')]}')
                if char == ',' and depth == 0:
                    args.append((start, pos));start = pos+1
            args.append((start, finish-1))
            if call[1] not in parameter_cache:
                parameter_cache[call[1]] = _parameters(context, call[1], arch)
            parameters = parameter_cache[call[1]]
            for index, (a, b) in enumerate(args):
                if any(re.search(r'\b'+re.escape(v)+r'\b', part.clean[a:b]) for v in protected):
                    if index >= len(parameters) or not _by_value(parameters[index], context, arch):
                        uncertain_call = True
                if not opening < a < b < end:
                    continue
                for value, (typ, after) in values.items():
                    if a < opening+1+after or not _operand(part.clean[a:b], value):
                        continue
                    token = next(re.finditer(r'\b'+re.escape(value)+r'\b', part.clean[a:b]))
                    replacement = rhs if rhs.isdecimal() else '(('+typ+')('+ ' '.join(rhs.split())+'))'
                    edits.append((a+token.start(), a+token.end(), replacement))
        if edits and not uncertain_call and len(edits) <= MAX_EDITS:
            groups.append(sorted(set(edits)))
            if len(groups) == MAX_GROUPS:
                break
    if not groups or not _c_subset(raw):
        return []
    combined = {a: (a,b,text) for group in groups for a,b,text in group}
    choices = [list(combined.values())]+groups
    return list(dict.fromkeys((FAMILY, edit(source, changes)) for changes in choices if changes))[:4]
