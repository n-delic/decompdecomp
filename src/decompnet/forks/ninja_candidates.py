"""Build app-created C candidates with a project's existing Ninja compiler rule."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path


def _escape(path: str) -> str:
    if '\n' in path or '\r' in path:
        raise ValueError('Ninja paths cannot contain newlines')
    return path.replace('$', '$$').replace(' ', '$ ').replace(':', '$:')


def build(project, unit, tool, args, log, timeout):
    """Use the existing compile_c rule in a temporary manifest, without editing the project build."""
    if args or not unit.source or unit.base is None:
        return None
    root = project.root.resolve()
    manifest = project.root / 'build.ninja'
    source = project.root / unit.source
    if root not in manifest.resolve().parents or root not in source.resolve().parents or root not in unit.base.resolve().parents:
        return None
    try:
        text = manifest.read_text(encoding='utf-8')
    except OSError:
        return None
    # Only use a rule the actual project already applies to C inputs.
    if not re.search(r'^rule\s+compile_c\s*$', text, re.M) or not re.search(r'^build\s+.+:\s*compile_c\s+.+\.c(?:\s|$)', text, re.M):
        return None
    relative_base = unit.base.relative_to(project.root).as_posix()
    relative_source = source.relative_to(project.root).as_posix()
    state = project.root / '.decomp-wtf'
    if root not in state.resolve().parents:
        return None
    state.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='candidate-build-', dir=state) as temporary:
        manifest_path = Path(temporary) / 'candidate.ninja'
        manifest_path.write_text(f'include build.ninja\nbuild {_escape(relative_base)}: compile_c {_escape(relative_source)}\n',
                                 encoding='utf-8')
        return project.run([tool, '-f', str(manifest_path), relative_base], log=log, timeout=timeout)
