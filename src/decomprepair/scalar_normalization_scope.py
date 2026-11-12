"""Keep a scalar normalization copy distinct during old-compiler CSE.

The original is overwritten only inside a straight-line expression replacement and
restored before the next statement. No whole-function macro liveness is assumed.
"""
import ast
import re

from .integer_repair_support import IDENT, edit, locals_for, refs, view
from .pattern_repairs import close
from .pointer_pair_repairs import qualified_types

FAMILY = 'scalar_normalization_scope'
LIMIT = 4


def variants(source, name, header=''):
    part = view(source, name)
    if not part:
        return []
    context = header+'\n'+source
    qualified = qualified_types(re.sub(r'\bconst\b', 'volatile', context))
    declarations = {d['name']: d for d in locals_for(part)
                    if d['type'] in {'int', 'signed int', 's32'} and d['type'] not in qualified}
    def available(variable, start):
        decl = declarations[variable]
        scopes = [a for a in range(part.opening, decl.start()) if part.clean[a] == '{'
                  and (close(part.clean, a, '{', '}') or 0) > decl.end()]
        end = close(part.clean, max(scopes), '{', '}') if scopes else None
        return (decl.end() < start < (end or 0)
                and not re.search(rf'(?<!&)&(?!&)\s*\(*\s*{variable}\b', part.body))

    result = []
    for hit in re.finditer(rf'\b({IDENT})\s*=\s*([^;{{}}]+);', part.body):
        origin = hit[1]
        if origin not in declarations:
            continue
        # Python's expression parser agrees with C for this tiny name/zero/minus
        # grammar and preserves grouping; deleting parentheses would change semantics.
        text = hit[2].strip()
        while text.startswith('(') and text.endswith(')'):
            depth = 0
            for pos, char in enumerate(text):
                depth += (char == '(')-(char == ')')
                if depth == 0:
                    break
            if pos != len(text)-1:
                break
            text = text[1:-1].strip()
        pieces = re.split(r'[?:]', text)
        if len(pieces) != 3:
            continue
        try:
            condition, negative, positive = [ast.parse(t.strip(), mode='eval').body for t in pieces]
        except (SyntaxError, ValueError):
            continue
        negated=negative.left if isinstance(negative,ast.BinOp) else None
        unary=(isinstance(negated,ast.UnaryOp) and isinstance(negated.op,ast.USub)
               and isinstance(negated.operand,ast.Name) and negated.operand.id==origin)
        zero_sub=(isinstance(negated,ast.BinOp) and isinstance(negated.op,ast.Sub)
                  and isinstance(negated.left,ast.Constant) and type(negated.left.value) is int and negated.left.value==0
                  and isinstance(negated.right,ast.Name) and negated.right.id==origin)
        if not (isinstance(condition, ast.Compare) and isinstance(condition.left, ast.Name)
                and condition.left.id == origin and len(condition.ops) == 1
                and isinstance(condition.ops[0], ast.Lt) and len(condition.comparators) == 1
                and isinstance(condition.comparators[0], ast.Constant)
                and type(condition.comparators[0].value) is int and condition.comparators[0].value == 0
                and isinstance(negative, ast.BinOp) and isinstance(negative.op, ast.Sub)
                and (unary or zero_sub)
                and isinstance(negative.right, ast.Name)
                and isinstance(positive, ast.Name) and positive.id == origin):
            continue
        operand = negative.right.id
        if operand == origin or operand not in declarations:
            continue
        start, stop = part.opening+1+hit.start(), part.opening+1+hit.end()
        if part.clean[part.opening+1:start].rstrip()[-1:] not in {';', '{', '}'}:
            continue
        if any(not available(v, start) for v in (origin, operand)):
            continue
        # A directive can alias or qualify one of the tracked locals outside the span.
        # Unrelated helper macros retain their original bytes and need no CFG expansion.
        directives = re.findall(r'^\s*#.*$', context, re.M)
        types = '|'.join(re.escape(declarations[v]['type']) for v in (origin, operand))
        if any(line.rstrip().endswith('\\') or re.search(rf'\b(?:{origin}|{operand})\b', line)
               or re.match(rf'\s*#\s*define\s+(?:{types})\b', line)
               for line in directives):
            continue
        saved = [d for v, d in declarations.items()
                 if v not in {origin, operand} and d['type'] == declarations[origin]['type']
                 and available(v, start) and not refs(part, d)]
        for decl in saved[:LIMIT]:
            variable = decl['name']
            if any(re.search(rf'\b{variable}\b', line) for line in directives):
                continue
            replacement = (f'do {{{variable}={origin}; if({origin}<0){{{origin}=0;'
                           f'{variable}=-{variable}-{operand};}}}} while(0); {origin}={variable};')
            result.append((FAMILY, edit(source, [(start, stop, replacement)])))
            if len(result) >= LIMIT:
                return result
    return result
