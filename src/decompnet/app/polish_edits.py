"""Small polish plans keep compiler-sensitive C in place instead of asking a model to copy it.

This deliberately accepts simple C declarations only. Ambiguous scopes, macros and C++
need the ordinary make_readable tool; exact recompilation remains the final authority.
"""
import hashlib
import json
import re

from ..forks.csource import _blank, find
from ..network import NetworkError

IDENT = re.compile(r'\b[A-Za-z_]\w*\b')
KEYWORDS = set('auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while _Bool class namespace template public private protected virtual new delete this true false'.split())
TYPE_WORDS = set('char double float int long short signed unsigned void _Bool'.split())
MAX_PLAN_BYTES = 32_000


def mapping(value):
    if isinstance(value, str):
        try:value = json.loads(value or '{}')
        except ValueError as error:raise NetworkError('Use a JSON object mapping old names to new names.') from error
    if not isinstance(value, dict) or len(value) > 100 or any(
            not isinstance(k, str) or not isinstance(v, str) or not IDENT.fullmatch(k) or not IDENT.fullmatch(v)
            or v in KEYWORDS for k, v in value.items()):
        raise NetworkError('Use at most 100 identifier pairs, with C identifiers as names.')
    return {k:v for k,v in value.items() if k != v}


def type_names(header):
    from ..seeds import declarations
    names = set()
    for declaration in declarations(header):
        text = _blank(declaration.text).strip()
        if text.startswith('typedef '):
            final = re.search(r'\b([A-Za-z_]\w*)\s*;$',text)
            if final:names.add(final[1])
    return names


DIRECTIVE = re.compile(r'^[ \t]*#(?:[^\n]*\\\n)*[^\n]*', re.M)  # one preprocessor line, with its continuations


def scope(source, function):
    """The function's span in `source`, or None when a small edit can't be confined to it.

    A machine draft keeps its preamble (M2C_DEFINES, extern declarations) around the function. Refusing
    every rename there sent agents back to retyping whole files with make_readable. Edits stay inside this
    span, and locals_in leaves out any name also used outside it (a #define or prototype naming it).
    #define and #undef lines inside it stay as they are, as outside text (a 529-instruction function of an
    N64 game defines its field accessors there, and no helper would retype it whole); a conditional or an
    include inside it still needs make_readable.
    """
    clean = _blank(source)
    span = find(source, function)
    if span is None or '::' in function:return None
    if any(not re.match(r'[ \t]*#[ \t]*(?:define|undef)\b', m[0])
           for m in DIRECTIVE.finditer(clean, span[0], span[1])):return None
    return span


def _code(clean, span):
    """`clean` with the #define/#undef lines inside the function blanked (edits never touch them), and their names."""
    names = set()
    for m in DIRECTIVE.finditer(clean, span[0], span[1]):
        names.update(IDENT.findall(m[0]))
        clean = clean[:m.start()] + re.sub(r'[^\n]', ' ', m[0]) + clean[m.end():]
    return clean, names


def locals_in(source, function, header=''):
    """Recognize unambiguous parameters and standalone local declarations, in source order."""
    clean = _blank(source)
    span = scope(source, function)
    if span is None:return []
    types = TYPE_WORDS | set(re.findall(r'\b(?:s|u)(?:8|16|32|64)\b|\bf(?:32|64)\b', header+' '+clean))
    # Typedef names from a declaration index are supplied in the header. The common final name is enough
    # here: unsupported function-pointer typedefs simply require a full rewrite.
    types.update(type_names(header))
    types.update(type_names(source[:span[0]]))  # a draft's own typedefs (M2C_UNK) declare its locals too
    type_pat = '(?:'+'|'.join(re.escape(t) for t in sorted(types))+r'|(?:struct|union|enum)\s+[A-Za-z_]\w*)'
    decl = re.compile(r'(?:\b(?:const|volatile|register)\s+)*\b'+type_pat+
                      r'(?:\s+(?:const|volatile|signed|unsigned|long|short|int))*\s*\**\s*([A-Za-z_]\w*)\s*(?=[,)=;\[])')
    code, in_directives = _code(clean, span)
    matches = list(decl.finditer(code, span[0], span[1]))
    # Static/extern locals have linkage or storage-sensitive names, so leave them to a full rewrite.
    names = [m[1] for m in matches if m[1] != function and m[1] not in KEYWORDS
             and not re.search(r'\b(?:extern|static)\s*$',clean[max(0,m.start()-16):m.start()])]
    if len(names) != len(set(names)):return []  # shadowed locals need a scope-aware edit
    outside = set(IDENT.findall(clean[:span[0]]+' '+clean[span[1]:])) | in_directives
    return [name for name in names if name not in outside]


def apply(source, function, renames, comment='', types=None, header=''):
    """Apply simultaneous local/type substitutions outside literals and members, without adding lines."""
    renames, types = mapping(renames), mapping(types or {})
    if len(source) > 2_000_000 or len(comment) > MAX_PLAN_BYTES:
        raise NetworkError('Keep the polish plan small; use make_readable for a larger rewrite.')
    local = locals_in(source, function, header)
    if set(renames)-set(local):
        raise NetworkError('Rename only simple parameters and locals here. Use rename_symbol for shared names, or make_readable for complex declarations.')
    clean = _blank(source)
    words = set(IDENT.findall(clean))
    if len(set(renames.values())) != len(renames) or any(v in words and v not in renames for v in renames.values()):
        raise NetworkError('A new local name already exists in this function. Choose distinct names.')
    if set(types) & (set(local) | {function}) or set(types) & set(renames.values()):
        raise NetworkError('Type edits must name types, separately from parameters and locals.')
    # Both spellings must be known types; never turn a global, callee or literal into an alias by accident.
    known_types = TYPE_WORDS | type_names(header)
    known_types |= set(re.findall(r'\b(?:[su](?:8|16|32|64)|f(?:32|64))\b', header))
    if set(types)-words or (set(types) | set(types.values()))-known_types:
        raise NetworkError('Type edits need existing type names from the header. Add declarations with edit_context or edit_file first.')
    span = scope(source, function)
    if span is None:raise NetworkError('Polish this function with make_readable: it has a preprocessor conditional or include inside.')
    code, _ = _code(clean, span)
    edits = []
    for m in IDENT.finditer(code, span[0], span[1]):
        before = clean[:m.start()].rstrip()
        if before.endswith(('.', '->', '::')):continue
        if m[0] in renames or m[0] in types:
            if clean[m.end():].lstrip().startswith(('(', ':')):
                raise NetworkError('A renamed identifier is also a call or label; use make_readable to edit its scope.')
            edits.append((m.start(), m.end(), (renames | types)[m[0]]))
    for start, end, value in reversed(edits):source = source[:start]+value+source[end:]
    if comment.strip():
        # Prefix on the function's first line. No newline is introduced, even for debug-line-sensitive compilers.
        if '*/' in comment or '\n' in comment or '\r' in comment or '\\' in comment:
            raise NetworkError('Use a one-line comment without a comment terminator or backslash.')
        line = source.rfind('\n', 0, span[0])+1
        source = source[:line]+'/* '+comment.strip()+' */ '+source[line:]
    if not edits and not comment.strip():raise NetworkError('Give local names, type aliases or a short comment to apply.')
    return source


def signature(source, function, header=''):
    """Identical C tokens, retaining types, literals, operators and shared names; only locals and self vary."""
    if scope(source, function) is None:return None, []
    local = locals_in(source, function, header)
    names = {name:f'@local{i}' for i,name in enumerate(local)} | {function:'@self'}
    # Strings and chars must stay in the signature: getters of two different strings have different meanings.
    from ..forks.csource import _COMMENT_OR_STRING
    clean = _COMMENT_OR_STRING.sub(lambda m: ' ' if m[0].startswith(('//','/*')) else m[0], source)
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_]\w*|\d+(?:\.\d*)?|[^\s]', clean, re.S)
    normalized = []
    for index, token in enumerate(tokens):
        member = index and (tokens[index-1] == '.' or tokens[max(0,index-2):index] in [['-','>'],[':',':']])
        normalized.append(token if member else names.get(token,token))
    return hashlib.sha256(json.dumps(normalized).encode()).hexdigest(), local


def guarded_header(source):
    """Only unconditional pragma-once or a guard enclosing the entire header is safe to deduplicate."""
    clean = _blank(source).strip()
    if re.match(r'#\s*pragma\s+once\b',clean):return True
    guard = re.match(r'#\s*ifndef\s+([A-Za-z_]\w*)\s*\n\s*#\s*define\s+\1\b',clean)
    if not guard or re.search(r'^\s*#\s*undef\s+'+re.escape(guard[1])+r'\b',clean,re.M):return False
    depth = 0
    for directive in re.finditer(r'^\s*#\s*(if|ifdef|ifndef|endif)\b[^\n]*',clean,re.M):
        depth += -1 if directive[1] == 'endif' else 1
        if depth == 0:return not clean[directive.end():].strip()
    return False


def cleanup(source, guarded_includes=()):
    """Remove exact repeated includes/extern declarations within a preprocessor region, keeping physical lines.

    Type extraction and unused declarations need semantic analysis and stay with the agent.
    Any directive or definition ends a region: conditionals and order-sensitive macros cannot leak across it.
    """
    seen, out, depth = set(), [], 0
    clean_lines = _blank(source).splitlines(True)
    for line, clean in zip(source.splitlines(True), clean_lines, strict=True):
        include = re.fullmatch(r'[ \t]*#[ \t]*include[ \t]+([<"][^>"\n]+[>"])[ \t]*(?:\r?\n)?', line)
        extern = re.fullmatch(r'[ \t]*extern[ \t]+[A-Za-z_][\w \t*]*(?:\([^;{}]*\)|(?:\[[^;{}]*\])*)?[ \t]*;[ \t]*(?:\r?\n)?', clean)
        candidate = depth == 0 and ((include and include[1] in guarded_includes) or (extern and clean == line)) and not line.rstrip('\r\n').endswith('\\')
        key = ('include', include[1]) if include else ('extern', clean.strip())
        if candidate and key in seen:
            out.append(re.sub(r'[^\r\n]', ' ', line))
        else:
            out.append(line)
            if candidate:seen.add(key)
            else:
                if clean.strip():seen.clear()
        depth += clean.count('{')-clean.count('}')
    return ''.join(out)
