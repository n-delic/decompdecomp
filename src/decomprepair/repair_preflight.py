"""Conservative checks before generated repairs spend a compiler invocation.

This is not a C parser. Unknown declarations, macros, C++ and indirect calls stay with the compiler.
Only an unambiguous plain prototype can rule out a direct call's argument count.
"""
import re
from functools import lru_cache

from .csource import _COMMENT_OR_STRING, find

MAX_CACHED_HEADER_CHARS = 1024*1024
_IDENT = r'[A-Za-z_]\w*'
_TYPE = r'(?:void|char|short|int|long|float|double|signed|unsigned|const|volatile|struct\s+\w+|u(?:8|16|32|64)|s(?:8|16|32|64))'
_PROTO = re.compile(rf'(?:^|(?<=[;}}]))\s*(?:extern\s+|static\s+)?(?:{_TYPE}\s+|\*\s*)+({_IDENT})\s*\(([^(){{}};]*)\)\s*;', re.M)


def clean(text):
    return _COMMENT_OR_STRING.sub(lambda m: '0' if m.group().startswith(('"', "'")) else ' ', text)


def arguments(text):
    depth = 0
    count = 1 if text.strip() else 0
    for char in text:
        depth += (char in '([{') - (char in ')]}')
        if char == ',' and depth == 0:
            count += 1
    return count


def _prototypes(text):
    prototypes = {}
    for match in _PROTO.finditer(text):
        params = match[2].strip()
        if not params or '...' in params or '=' in params:
            prototypes.setdefault(match[1], set()).add(None)
            continue  # empty () is unspecified in C; default parameters/varargs are not fixed arity
        if params != 'void' and any(not re.match(rf'^{_TYPE}\b', p.strip()) for p in params.split(',')):
            prototypes.setdefault(match[1], set()).add(None)
            continue  # typedefs and old-style names may need macro expansion
        prototypes.setdefault(match[1], set()).add(0 if params == 'void' else arguments(params))
    return {name:frozenset(counts) for name,counts in prototypes.items()}


@lru_cache(maxsize=4)
def _header(header):
    text = clean(header)
    unknown = bool(re.search(r'^\s*#', text, re.M) or re.search(r'::|\b(?:class|template|namespace)\b', text))
    # Complete declarations cannot continue into the candidate. Preserve the old combined
    # scan for unusual fragments rather than guessing across the header/source boundary.
    complete = not text.strip() or text.rstrip().endswith((';', '}'))
    return text, unknown, complete, _prototypes(text) if complete and not unknown else {}


def invalid_call(source, name, header=''):
    """Explain a provable arity error, otherwise let the ordinary compiler decide."""
    # Bound retained headers independently of the number of concurrently searched functions.
    # Parsing is pure: cache keys are exact text, never a project or revision assumption.
    text, unknown, complete, declared = (_header(header) if len(header)<=MAX_CACHED_HEADER_CHARS else _header.__wrapped__(header))
    if unknown:return ''
    body_text = clean(source)
    if not complete:
        text = clean(header+'\n'+source)
    else:
        text = body_text
    if re.search(r'^\s*#', text, re.M) or re.search(r'::|\b(?:class|template|namespace)\b', text):
        return ''
    span = find(source, name)
    if not span:
        return ''
    body = clean(source[span[0]:span[1]])
    body = body[body.find('{')+1:]
    prototypes = dict(declared) if complete else {}
    for callee, counts in _prototypes(text).items():
        prototypes[callee] = prototypes.get(callee, frozenset()) | counts
    for call in re.finditer(rf'(?<![\w.>])({_IDENT})\s*\(', body):
        if call[1] not in prototypes or len(prototypes[call[1]]) != 1 or None in prototypes[call[1]]:
            continue
        # A local object/parameter with this name can shadow the declaration; leave it to the compiler.
        if re.search(rf'\b{re.escape(call[1])}\b(?!\s*\()', clean(source)):
            continue
        start = call.end()
        depth, end = 1, start
        while end < len(body) and depth:
            depth += (body[end] == '(') - (body[end] == ')')
            end += 1
        if depth:
            continue
        actual = arguments(body[start:end-1])
        expected = next(iter(prototypes[call[1]]))
        if actual != expected:
            return f'{call[1]} expects {expected} arguments; generated call has {actual}.'
    return ''


def drop_argument_allowed(source, callee, args, header=''):
    """Reject only a proven fixed-arity violation, allowing unrelated object macros.

    The generic preflight intentionally treats preprocessing as opaque. This narrower
    generator check reads one direct call/prototype only; macros touching either, included
    or conditional declarations, shadows and unfamiliar parameter types remain unknown.
    """
    text=clean(header+'\n'+source)
    directives=list(re.finditer(r'(?m)^[ \t]*#[^\n]*',text))
    macros=set()
    for directive in directives:
        match=re.match(r'\s*#\s*define\s+('+_IDENT+r')\b',directive[0])
        if not match or directive[0].rstrip().endswith('\\'):return True
        macros.add(match[1])
    for directive in reversed(directives):
        text=text[:directive.start()]+' '*(directive.end()-directive.start())+text[directive.end():]
    if re.search(r'::|\b(?:class|template|namespace)\b',text):return True
    prototypes=[m for m in _PROTO.finditer(text) if m[1]==callee]
    if not prototypes or re.search(r'\b'+re.escape(callee)+r'\b(?!\s*\()',clean(source)):return True
    counts=set()
    for proto in prototypes:
        params=proto[2].strip()
        words=set(re.findall(_IDENT,proto[0]+' '+args))
        if macros & words or not params or '...' in params or any(c in params for c in '=[]'):return True
        if params!='void' and any(not re.match(rf'^{_TYPE}\b',p.strip()) for p in params.split(',')):return True
        counts.add(0 if params=='void' else arguments(params))
    if len(counts)!=1:return True
    return arguments(args)-1==next(iter(counts))
