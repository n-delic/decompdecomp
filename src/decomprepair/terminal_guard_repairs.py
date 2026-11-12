"""Equivalent terminal branch layouts, including candidate-owned inline helpers.

Keep conditions and observable operations, not a guessed inverse comparison.
Every proposal still needs the ordinary compiler and exact byte judge.
"""
import re

from .csource import _blank
from .integer_repair_support import IDENT, edit, locals_for, view
from .pattern_repairs import close

FAMILY = 'terminal_guard_layout'
LIMIT = 6


def _parts(source, name):
    names = [name]
    target = view(source, name)
    if not target:
        return []
    # Helpers are owned by this candidate, not inferred definitions from a shared header.
    for hit in re.finditer(r'\bstatic\s+(?:inline|__inline__|__inline)\s+void\s+('+IDENT+r')\s*\(', _blank(source)):
        if hit[1] != name and re.search(r'\b'+re.escape(hit[1])+r'\s*\(', target.body):
            names.append(hit[1])
        if len(names) == 3:
            break
    return [(n, part) for n in dict.fromkeys(names) if (part := view(source, n))]


def _allowed(part, source, header, name):
    raw = source+'\n'+header
    if re.search(r'\\(?:\r\n?|\n)', raw) or '??' in raw or '%:' in raw:
        return False
    context = _blank(raw)
    if re.search(r'\b(?:operator|class|template|namespace)\b', context):
        return False  # Logical negation must be the built-in scalar operation.
    if any(d != 'define' for d in re.findall(r'^\s*#\s*(\w+)', context, re.M)):
        return False  # Hidden/conditional macros require preprocessed input.
    if re.search(r'\b(?:goto|case|default|for|while|do|break|continue|asm|__asm|__asm__|setjmp|longjmp|_Pragma|__LINE__|__COUNTER__|__FILE__|__label__|class|template|namespace|try|catch|throw|co_await|co_return|co_yield)\b', part.body):
        return False
    if re.search(r'(?:^|[;{}])\s*'+IDENT+r'\s*:(?!:)', part.body):
        return False
    for hit in re.finditer(r'^\s*#\s*define\s+('+IDENT+r')\b([^\n]*)', context, re.M):
        if hit[1] in {name, 'void', 'return', 'static', 'inline', '__inline__', '__inline'}:
            return False
        if not re.search(r'\b'+re.escape(hit[1])+r'\b', part.body):
            continue
        # The ordinary null constant is safe; unknown macros can hide declarations/transfers.
        null = re.sub(r'\s+', '', hit[2])
        if hit[1] != 'NULL' or null not in {'0', '0L', '(0)', '((void*)0)', '(void*)0', '__null'}:
            return False
    return True


def _declarations(part, start, end):
    if any(start <= d.start() < end for d in locals_for(part)):
        return True
    # Fail closed for declarations outside the simple local parser's vocabulary.
    text = part.clean[start:end]
    if re.search(r'\b(?:_Alignas|alignas|__attribute__|__declspec|typeof|__typeof__|decltype|auto|struct|union|enum|typedef)\b', text):
        return True
    return bool(re.search(r'(?:^|[;{}])\s*(?!return\b|if\b|else\b)(?:'+IDENT+r'\s+)+(?:[&*]\s*)*'+IDENT+r'\s*(?:[=;,\[])', text))


def variants(source, name, header=''):
    if max(len(source), len(header)) > 128000:
        return []
    result = []
    for selected, part in _parts(source, name):
        # find() can include whitespace after the definition; use the actual brace.
        part.end = close(part.clean, part.opening, '{', '}')
        if not part.end or not _allowed(part, source, header, selected):
            continue
        for hit in re.finditer(r'\bif\s*\(', part.body):
            start = part.opening+1+hit.start()
            prefix = part.clean[part.opening+1:start]
            if re.search(r'\b(?:if|else|return)\b|[{}]', prefix):
                continue  # First control statement, never a nested/unbraced arm.
            a = part.opening+1+hit.end()-1
            condition_end = close(part.clean, a)
            left = re.match(r'\s*{', part.clean[condition_end:]) if condition_end else None
            if not left:
                continue
            opening = condition_end+left.end()-1
            end = close(part.clean, opening, '{', '}')
            if not end:
                continue
            condition = source[a+1:condition_end-1]
            arm = source[opening+1:end-1]
            other = re.match(r'\s*else\s*{', part.clean[end:])
            if other:
                right = end+other.end()-1
                right_end = close(part.clean, right, '{', '}')
                if (not right_end or part.clean[right_end:part.end-1].strip()
                        or _declarations(part, opening+1, right_end-1)
                        or re.search(r'\breturn\b', part.clean[opening+1:right_end-1])):
                    continue
                # Only a void definition can replace implicit function exit with return;.
                if not re.search(r'\bvoid\s+'+re.escape(selected)+r'\s*\([^;{}]*\)\s*$', part.clean[:part.opening]):
                    continue
                second = source[right+1:right_end-1]
                choices = ['if ('+condition+') {'+arm+'return;}'+second,
                           'if (!('+condition+')) {'+second+'return;}'+arm]
                result.extend((FAMILY, edit(source, [(start, right_end, text)])) for text in choices)
            else:
                returned = re.fullmatch(r'\s*return\s+[^;{}]+;\s*', part.clean[opening+1:end-1])
                tail = source[end:part.end-1]
                if (not returned or not re.search(r'\breturn\s+[^;{}]+;\s*$', part.clean[end:part.end-1])
                        or re.search(r'\b(?:if|else)\b|[{}]', part.clean[end:part.end-1])
                        or _declarations(part, end, part.end-1)):
                    continue
                text = 'if (!('+condition+')) {'+tail+'} else {'+arm+'}'
                result.append((FAMILY, edit(source, [(start, part.end-1, text)])))
            if len(result) >= LIMIT:
                return list(dict.fromkeys(result))[:LIMIT]
    return list(dict.fromkeys(result))[:LIMIT]
