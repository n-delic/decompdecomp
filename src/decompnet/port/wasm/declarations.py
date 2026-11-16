"""Narrow clang compatibility for the generated port build, never for saved or judged source.

Old compilers accepted a redundant plain extern beside a qualified shared declaration. Clang rejects it. Only
clang's own canonical scalar types can prove the two differ solely by added const/volatile on the shared type;
keep that stronger declaration, and remove the redundant line from the temporary compiler input alone.
"""
from __future__ import annotations

import re
from pathlib import Path

from ...forks.csource import _blank

_REDECLARED = re.compile(r"^(.*?):(\d+):\d+: error: redeclaration of '(\w+)' with a different type: "
                         r"'([^']+)'(?: \(aka '([^']+)'\))? vs '([^']+)'(?: \(aka '([^']+)'\))?", re.M)
_SCALAR = frozenset('const volatile signed unsigned char short int long float double _Bool'.split())
_EXTERN = re.compile(r'\s*extern\s+[A-Za-z_][\w \t]*?\s+([A-Za-z_]\w*)\s*;\s*')


def _scalar(text: str) -> tuple[str, set[str]] | None:
    words = text.split()
    if not words or not set(words) <= _SCALAR:
        return None
    return ' '.join(w for w in words if w not in ('const', 'volatile')), set(words) & {'const', 'volatile'}


def qualified_externs(source: str, diagnostics: str, file: Path) -> str:
    """Remove only unconditional file-scope scalar extern lines clang proves redundant with a stronger header.

    Pointer/array/function types, width changes, declarations inside conditionals and a qualifier present only
    in the source are left alone. Their errors still stop the build. Line numbers stay unchanged on a retry.
    """
    allowed = {}
    depth = conditional = 0
    continued = False
    for number, line in enumerate(_blank(source).splitlines(), 1):
        stripped = line.strip()
        if continued:
            continued = stripped.endswith('\\')
            continue
        if stripped.startswith('#'):
            continued = stripped.endswith('\\')
        if re.match(r'#\s*(?:if|ifdef|ifndef)\b', stripped):
            conditional += 1
        elif re.match(r'#\s*endif\b', stripped):
            conditional = max(0, conditional - 1)
        elif not depth and not conditional and (match := _EXTERN.fullmatch(line)):
            allowed[number] = match[1]
        if not stripped.startswith('#'):
            depth += line.count('{') - line.count('}')
    remove = set()
    for match in _REDECLARED.finditer(diagnostics):
        number = int(match[2])
        if Path(match[1]).resolve() != file.resolve() or allowed.get(number) != match[3]:
            continue
        own, shared = _scalar(match[5] or match[4]), _scalar(match[7] or match[6])
        if own and shared and own[0] == shared[0] and own[1] < shared[1]:
            remove.add(number)
    return ''.join(re.sub(r'[^\r\n]', ' ', line) if number in remove else line
                   for number, line in enumerate(source.splitlines(keepends=True), 1))
