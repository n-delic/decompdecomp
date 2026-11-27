"""Build app-created C candidates with a project's existing Ninja compiler rule."""

from __future__ import annotations

import re
import tempfile
from collections import Counter
from pathlib import Path


def _escape(path: str) -> str:
    if '\n' in path or '\r' in path:
        raise ValueError('Ninja paths cannot contain newlines')
    return path.replace('$', '$$').replace(' ', '$ ').replace(':', '$:')


def correct_generated_make(root: Path, config: dict) -> dict:
    """Repair an automatic reference config that selected make for a Ninja-only project."""
    if (config.get('custom_make') != 'make' or config.get('custom_args')
            or not (root / 'build.ninja').is_file()
            or root.resolve() not in (root / 'build.ninja').resolve().parents
            or any((root / name).is_file() for name in ('Makefile', 'makefile', 'GNUmakefile'))):
        return config
    targets = [u.get('target_path', '') for u in config.get('units', [])]
    if not targets or not all(isinstance(t, str) and t.startswith('.decomp-wtf/objdiff-references/targets/')
                              for t in targets):
        return config
    return {**config, 'custom_make': 'ninja'}


def _common_bindings(text: str, rule: str) -> dict[str, str]:
    """Reuse dominant per-edge variables the project's own C rule actually references."""
    lines = re.sub(r'\$\r?\n[ \t]*', '', text).splitlines()
    referenced = set()
    for index, line in enumerate(lines):
        if line.strip() != f'rule {rule}':
            continue
        for item in lines[index + 1:]:
            if not item.startswith((' ', '\t')):
                break
            if item.lstrip().startswith('command ='):
                referenced.update(a or b for a, b in re.findall(r'(?<!\$)\$(?:\{([\w.-]+)\}|([\w.-]+))', item))
        break
    referenced -= {'in', 'out', 'in_newline'}
    counts: dict[str, Counter] = {}
    edges = 0
    for index, line in enumerate(lines):
        if not re.match(rf'^build\s+.+:\s*{re.escape(rule)}\s+.+\.c(?:\s|$)', line):
            continue
        edges += 1
        for item in lines[index + 1:]:
            if not item.startswith((' ', '\t')):
                break
            match = re.match(r'^\s+([\w.-]+)\s*=\s*(.*)$', item)
            if match and match[1] in referenced:
                counts.setdefault(match[1], Counter())[match[2]] += 1
    result = {}
    for name, values in counts.items():
        value, count = values.most_common(1)[0]
        # A strong project-wide default, not an arbitrary source file's special flags.
        if count * 5 >= edges * 4:
            result[name] = value
    return result


def build(project, unit, tool, args, log, timeout):
    """Use the project's existing C rule in a temporary manifest, without editing its build."""
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
    rule = next((name for name in ('compile_c', 'cc')
                 if re.search(rf'^rule\s+{name}\s*$', text, re.M)
                 and re.search(rf'^build\s+.+:\s*{name}\s+.+\.c(?:\s|$)', text, re.M)), None)
    if rule is None:
        return None
    relative_base = unit.base.relative_to(project.root).as_posix()
    relative_source = source.relative_to(project.root).as_posix()
    state = project.root / '.decomp-wtf'
    if root not in state.resolve().parents:
        return None
    state.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='candidate-build-', dir=state) as temporary:
        manifest_path = Path(temporary) / 'candidate.ninja'
        bindings = _common_bindings(text, rule)
        variables = ''.join(f'  {name} = {value}\n' for name, value in sorted(bindings.items()))
        manifest_path.write_text(f'include build.ninja\nbuild {_escape(relative_base)}: {rule} {_escape(relative_source)}\n'
                                 + variables, encoding='utf-8')
        return project.run([tool, '-f', str(manifest_path), relative_base], log=log, timeout=timeout)
