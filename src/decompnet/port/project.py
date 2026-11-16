"""Read a project's own C/C++ for port readiness, without building or changing its checkout.

Objdiff matches can come from assembly and can have no saved matched_source at all. Read the actual unit files,
keep missing bodies explicit, and never turn a 100% object report into a claim that the game has portable C.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..app.forkengine import ForkEngine
from ..forks import csource
from ..forks.project import ForkError, ForkProject

_SOURCE_INCLUDE = re.compile(r'^\s*#\s*include\s*"([^"\n]+\.(?:c|cc|cpp|cxx))"', re.M)
_ASSEMBLY = re.compile(r'\b(?:asm|__asm|__asm__|INCLUDE_ASM|GLOBAL_ASM)\b')


def _inside(root: Path, path: Path) -> Path | None:
    try:
        path = path.resolve()
        return path if path.is_relative_to(root.resolve()) and path.is_file() else None
    except OSError:
        return None


def _texts(root: Path, source: Path) -> list[str]:
    """Read included source as the ordinary project brief does, never following a path outside the checkout."""
    todo, seen, texts = [source], set(), []
    while todo and len(seen) < 6:
        path = todo.pop(0)
        if path in seen:
            continue
        seen.add(path)
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        texts.append(text)
        for include in _SOURCE_INCLUDE.findall(text):
            candidates = (path.parent / include, root / include, root / 'src' / include)
            if found := next((p for candidate in candidates if (p := _inside(root, candidate))), None):
                todo.append(found)
    return texts


def sources(proj, rows: list) -> dict:
    """Current function bodies by id, plus missing/assembly evidence and the source files inspected.

    Older public objdiff files often omit source_path. A unique file with exactly the unit's name is useful
    evidence, labelled as inferred; ambiguous files are left unread. No build commands or project code run.
    """
    root = Path(proj['root']).resolve()
    fp = ForkProject(root, create_state=False)
    try:
        units = {u.name: u for u in fp.units()}
        problem = ''
    except (ForkError, OSError, ValueError, TypeError, KeyError) as error:
        units, problem = {}, f"The project's source-unit list could not be read: {error}"
    bodies, assembly, files, inferred = {}, set(), set(), set()
    cached, paths = {}, {}
    for row in sorted(rows, key=lambda r: r['unit'] or ''):
        meta = json.loads(row['meta'] or '{}')
        unit = units.get(row['unit'])
        declared = (unit.source if unit else None) or meta.get('source')
        key = (row['unit'], declared)
        if key not in paths:
            source = _inside(root, root / declared) if declared else None
            if not declared and row['unit']:
                stem = root / row['unit']
                candidates = {p for ext in ('.c', '.cc', '.cpp', '.cxx')
                              if (p := _inside(root, Path(str(stem) + ext)))}
                if len(candidates) == 1:
                    source = candidates.pop()
                    inferred.add(str(source.relative_to(root)))
            paths[key] = source
        source = paths[key]
        if source is None:
            continue
        if source not in cached:
            cached[source] = _texts(root, source)
            files.add(str(source.relative_to(root.resolve())))
        for text in cached[source]:
            body = next((text[span[0]:span[1]] for name in ForkEngine._names(meta, row)
                         if (span := csource.find(text, name))), None)
            if body is None:
                continue
            if csource.is_stub(body) or _ASSEMBLY.search(csource._blank(body)):
                assembly.add(row['id'])
            else:
                bodies[row['id']] = body
            break
    return dict(bodies=bodies, assembly=assembly, files=sorted(files), inferred=sorted(inferred), problem=problem)
