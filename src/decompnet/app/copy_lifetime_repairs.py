"""Bounded scalar copy/flag hypotheses, checked by the unchanged ordinary judge.

Old allocators can substitute a copied-from register even after an explicit C copy. An assignment
whose value is dead on every CFG edge can end that lifetime without modifying the saved copy.
"""
import re
from dataclasses import dataclass

from . import local_flow
from .integer_repair_support import IDENT, INTEGER, edit, view
from .pattern_repairs import close

FAMILIES = ('dead_copy_source', 'divisor_flag_split', 'regional_load_reuse', 'divisor_value_scope', 'divisor_normalization_copy')
LIMIT = 16


@dataclass
class Declaration:
    type: str
    name: str
    begin: int
    stop: int
    scope_stop: int

    def __getitem__(self, key):
        return getattr(self, key)

    def start(self):
        return self.begin

    def end(self):
        return self.stop


def context(source, name):
    # Object-like aliases for local record fields need not hide scalar liveness. Leave their
    # bytes untouched, and reject directives with control flow, token pasting or continuations.
    directives = list(re.finditer(r'^[ \t]*#.*$', source, re.M))
    if any(h[0].rstrip().endswith('\\') for h in directives):
        return None
    if any(re.search(r'\b(?:if|else|for|while|do|goto|return|break|continue|setjmp|longjmp|asm|__asm__|volatile)\b', h[0])
           for h in directives if re.match(r'\s*#\s*define\b', h[0])):
        return None
    masked = source
    for hit in reversed(directives):
        masked = masked[:hit.start()]+' '*(hit.end()-hit.start())+masked[hit.end():]
    part = view(masked, name)
    if not part:
        return None
    for hit in directives:
        if part.opening < hit.start() < part.end and not re.fullmatch(
                rf'\s*#define\s+{IDENT}\s+{IDENT}(?:\.{IDENT})*\s*', hit[0]):
            return None
    declarations = []
    scopes = []
    for hit in re.finditer(r'\{', part.clean[part.opening:part.end]):
        start = part.opening+hit.start()
        stop = close(part.clean,start,'{','}')
        if stop:
            scopes.append((start,stop))
    pattern = rf'\b(?P<type>{INTEGER})\s+(?P<items>{IDENT}(?:\s*=\s*{IDENT})?(?:\s*,\s*{IDENT})*)\s*;'
    for decl in re.finditer(pattern, part.clean):
        if not part.opening < decl.start() < part.end:
            continue
        if part.clean[part.opening+1:decl.start()].rstrip()[-1:] not in {'', ';', '{', '}'}:
            continue
        scope = max((s for s in scopes if s[0] < decl.start() < s[1]), default=None)
        if not scope or re.search(r'\b(?:struct|union)(?:\s+'+IDENT+r')?\s*$', part.clean[:scope[0]]):
            continue
        for variable in re.findall(IDENT, decl['items'].split('=')[0]):
            if any(re.search(rf'\b{variable}\b', h[0]) for h in directives):
                continue
            if re.search(rf'(?<!&)&(?!&)\s*\(*\s*{variable}\b', part.body):
                continue
            shadows = re.findall(rf'\b(?:{INTEGER}|void|float|double)\s*\**\s*{variable}\b', part.body)
            if (len(shadows) > 1 or re.search(rf'\b(?:{INTEGER}|void|float|double)\s*\*+\s*{variable}\b', part.body)
                    or re.search(rf'\b(?:const|volatile)\s+(?:{INTEGER})\s+{variable}\b', part.body)):
                continue
            declarations.append(Declaration(decl['type'], variable, decl.start(), decl.end(), scope[1]))
    counts = {d.name: sum(other.name == d.name for other in declarations) for d in declarations}
    declarations = [d for d in declarations if counts[d.name] == 1]
    flow = local_flow.analyze(masked, name, declarations=declarations)
    return (part, flow, {d.name: d for d in declarations}) if flow else None


def variants(source, name):
    ctx = context(source, name)
    if not ctx:
        return []
    part, flow, declarations = ctx
    result = []
    for node in sorted(flow.nodes, key=lambda n: n.start):
        text = part.clean[node.start:node.end]
        copy = re.fullmatch(rf'\s*(?:{INTEGER}\s+)?({IDENT})\s*=\s*({IDENT})\s*;\s*', text)
        if not copy:
            continue
        destination, origin = copy.groups()
        if destination == origin or origin not in declarations or destination not in declarations:
            continue
        if not (declarations[origin].end() <= node.start < declarations[origin].scope_stop
                and declarations[destination].end() <= node.end <= declarations[destination].scope_stop):
            continue
        if declarations[origin].type != declarations[destination].type:
            continue
        # Do not turn an unbraced conditional copy into an unconditional mutation.
        if part.clean[part.opening+1:node.start].rstrip()[-1:] not in {'', ';', '{', '}'}:
            continue
        if any(origin in flow.nodes[n].live for n in node.successors):
            continue
        result.append(('dead_copy_source', edit(source, [(node.end, node.end, f'{origin}=0;')])))
        if len(result) == LIMIT:
            break
    # Separate an in-place divisor from the boolean adjustment assigned to it. No new
    # arithmetic identity or signed shift is invented: preserve the original expression.
    for hit in re.finditer(rf'\b({IDENT})\s*\+=\s*\(?\s*\1\s*==\s*0\s*\)?\s*;', part.body):
        variable = hit[1]
        if variable not in declarations:
            continue
        start, stop = part.opening+1+hit.start(), part.opening+1+hit.end()
        if part.clean[part.opening+1:start].rstrip()[-1:] not in {';', '{', '}'}:
            continue
        if not declarations[variable].end() <= start < declarations[variable].scope_stop:
            continue
        fresh = '__repair_divisor_zero'
        if re.search(rf'\b{fresh}\b', part.clean):
            continue
        result.append(('divisor_flag_split', edit(source, [(start, stop,
                       f'{{int {fresh}=({variable}==0);{variable}+={fresh};}}')])))
    pattern = (rf'\b({IDENT})\s*=\s*({IDENT})\s*/\s*(2|4|8|16|32|64|128|256)\s*;'
               rf'\s*\1\s*\+=\s*\(?\s*\1\s*==\s*0\s*\)?\s*;'
               rf'\s*({IDENT})\s*/=\s*\1\s*;')
    for hit in re.finditer(pattern, part.body):
        divisor, origin, scale, quotient = hit.groups()
        if (divisor == quotient or divisor not in declarations
                or declarations[divisor].type not in {'int', 's32', 'signed int'}):
            continue
        start, stop = part.opening+1+hit.start(), part.opening+1+hit.end()
        node = flow.at(stop-1)
        if (not node or any(divisor in flow.nodes[n].live for n in node.successors)
                or part.clean[part.opening+1:start].rstrip()[-1:] not in {';', '{', '}'}):
            continue
        if not declarations[divisor].end() <= start < stop <= declarations[divisor].scope_stop:
            continue
        fresh = '__repair_divisor_value'
        if re.search(rf'\b{fresh}\b', part.clean):
            continue
        result.append(('divisor_value_scope', edit(source, [(start, stop,
                       f'{{int {fresh}={origin}/{scale};{fresh}+=({fresh}==0);{quotient}/={fresh};}}')])))
    # Move an existing signed rounding sequence into the saved divisor, separating the
    # zero flag in the original scratch local. The scratch is immediately redefined;
    # this is copy/flag staging, not permission to invent signed-shift arithmetic.
    pattern = (rf'\b(?P<scratch>{IDENT})\s*=\s*(?P<origin>{IDENT})\s*;'
               rf'\s*if\s*\(\s*(?P=scratch)\s*<\s*0\s*\)\s*(?P=scratch)\s*\+=\s*(?P<bias>[0-9]+)\s*;'
               rf'\s*(?P=scratch)\s*>>=\s*(?P<shift>[1-9])\s*;'
               rf'\s*(?P=scratch)\s*\+=\s*\(\s*(?P=scratch)\s*==\s*0\s*\)\s*;'
               rf'\s*{{\s*int\s+(?P<saved>{IDENT})\s*=\s*(?P=scratch)\s*;'
               rf'(?=\s*(?P=scratch)\s*=(?!=))')
    for hit in re.finditer(pattern, part.body):
        scratch, origin, bias, shift, saved = (hit[g] for g in ('scratch', 'origin', 'bias', 'shift', 'saved'))
        if (scratch not in declarations or saved not in declarations or origin not in declarations
                or declarations[scratch].type != 'int' or declarations[origin].type != 'int'
                or int(bias) != (1 << int(shift))-1 or len({scratch,origin,saved}) != 3):
            continue
        start, stop = part.opening+1+hit.start(), part.opening+1+hit.end()
        if part.clean[part.opening+1:start].rstrip()[-1:] not in {';', '{', '}'}:
            continue
        if any(not declarations[v].end() <= start < stop <= declarations[v].scope_stop for v in (scratch,origin)):
            continue
        redefine = re.match(rf'\s*{scratch}\s*=(?!=)([^;{{}}]+);', part.clean[stop:])
        if not redefine or re.search(rf'\b{scratch}\b', redefine[1]):
            continue
        text = (f'{{int {saved}={origin};if({saved}<0){saved}+={bias};{saved}>>={shift};'
                f'{scratch}=({saved}==0);{saved}+={scratch};')
        result.append(('divisor_normalization_copy', edit(source, [(start, stop, text)])))
    # Reuse a proven equal, nonvolatile scalar load in a small straight-line region. The
    # selected expression has already been evaluated, with no intervening writes/calls.
    for hit in re.finditer(rf'\b({IDENT})\s*=\s*({IDENT}\s*\[\s*{IDENT}\s*\])\s*;', part.body):
        variable, expression = hit.groups()
        array = re.match(IDENT, expression)[0]
        if variable not in declarations or not re.search(rf'\bint\s+{array}\s*\[\s*[1-9][0-9]*\s*\]', part.body):
            continue
        start = part.opening+1+hit.end()
        rest = part.clean[start:part.end-1]
        use = re.match(rf'\s*if\s*\(\s*({re.escape(expression)})\s*(?:<|>|==|!=)', rest)
        if use:
            a, b = start+use.start(1), start+use.end(1)
            result.append(('regional_load_reuse', edit(source, [(a, b, variable)])))
    return list(dict.fromkeys(result))[:LIMIT]


def hint(source, name):
    choices = variants(source, name)
    if any(f == 'dead_copy_source' for f, _ in choices):
        return ('A scalar copy has a copied-from local dead on every outgoing CFG edge. If ordered '
                'operations agree but registers differ, test dead_copy_source through local variants: '
                'ending the original lifetime may stop copy propagation. Keep the saved value, compiler '
                'and header fixed; only the ordinary judge establishes a match.')
    return ''
