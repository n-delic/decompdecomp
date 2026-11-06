"""A bounded alternative m2c context from a saved candidate's reachable declarations."""
import re

from .. import seeds
from ..forks.csource import _blank


def candidate_context(header, source, roots, name=''):
    """Overlay declarations, never function bodies or macros, without changing the shared header.

    Local prototypes can retain return widths that the shared header has not learned. m2c must parse
    this alternative strictly: silently dropping it would merely repeat the normal draft.
    """
    if not source or len(source) > 128_000:
        return None
    source = re.sub(r'#ifndef M2C_DEFINES.*?#endif\n?', '', source, flags=re.S)
    declarations = []
    for decl in seeds.declarations(seeds.c_only(source)):
        clean = _blank(decl.text)
        if decl.text.startswith('#') or re.search(r'\b(?:class|namespace|template)\b|::', clean):
            continue
        definition = re.search(r'\)\s*{', clean)
        if definition:
            if not name or not re.search(r'\b' + re.escape(name) + r'\s*\(', clean[:definition.end()]):
                continue
            text = decl.text[:definition.end()-1].strip() + ';'
        elif '=' in clean:
            continue
        else:
            text = decl.text
        declarations.append(text)
        if len(declarations) > 256:
            return None
    mine = seeds.HeaderIndex('\n'.join(declarations))
    shared = seeds.HeaderIndex(seeds.c_only(header))
    roots = set(roots) | ({name} if name else set())
    names = set(roots)
    # Cross-index dependencies: a local prototype may use a shared typedef, whose fields in turn
    # use a locally defined tag. Bound the closure and reject unusually tangled contexts.
    for _ in range(16):
        local = mine.used(names)
        theirs = shared.used(names)
        more = names | set().union(*(d.uses for d in local + theirs))
        if more == names:
            break
        names = more
    else:
        return None
    if not local:
        return None
    declared = set().union(*(d.names for d in local))
    # A shared declaration of multiple globals cannot be partially replaced safely.
    if any(d.names & declared and not d.names <= declared for d in theirs):
        return None
    context = '\n'.join(d.text for d in theirs if not d.text.startswith('#') and not d.names & declared)
    context += '\n' + '\n'.join(d.text for d in local)
    normal = '\n'.join(d.text for d in shared.used(roots) if not d.text.startswith('#'))
    if len(context) > 32_000 or _blank(context).split() == _blank(normal).split():
        return None
    return context
