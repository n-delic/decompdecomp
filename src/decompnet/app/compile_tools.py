"""Revision-checked edits and reversible recipe trials for source-tree projects.

Previews build and compare through the project's ordinary tools. Only an applying
attempt may update claims, attempts, matches or git. ROM transactions remain planned.
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path

from ..forks import csource
from ..forks.objdiff import ObjdiffError
from ..forks.project import STATE, ForkError
from ..network import NetworkError
from ..scoring import percent_text

MAX_SOURCE = 64 * 1024
MAX_INPUTS = 32 * 1024 * 1024
MAX_FILES = 4096
MAX_TRIALS = 8
EXCLUDED = {'.git', '.venv', 'build', 'orig', 'extract', 'node_modules', '__pycache__', '.pytest_cache', '.ruff_cache', STATE}


def patch(source: str, edits: list[dict]) -> str:
    """Apply bounded exact replacements; ambiguity is an error rather than a guessed edit."""
    if not isinstance(edits, list) or not 1 <= len(edits) <= 16:
        raise NetworkError('Send between 1 and 16 exact replacements.')
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {'old', 'new'}:
            raise NetworkError('Each edit must contain only old and new strings.')
        old, new = edit['old'], edit['new']
        if not isinstance(old, str) or not isinstance(new, str) or not old or source.count(old) != 1:
            raise NetworkError('Each old string must occur exactly once in the current definition.')
        if len(new.encode()) > MAX_SOURCE:
            raise NetworkError('Patched definition is too large.')
        source = source.replace(old, new, 1)
        if len(source.encode()) > MAX_SOURCE:
            raise NetworkError('Patched definition is too large.')
    return source


def _read(path, limit=MAX_INPUTS):
    """Bound reads too: an external writer may grow a file after its size was checked."""
    try:
        if not path.is_file() or path.stat().st_size > limit:
            raise NetworkError('Project inputs are too large or not regular files; use compile_and_diff.')
        with path.open('rb') as file:
            data = file.read(limit + 1)
        if len(data) > limit:
            raise NetworkError('Project inputs are too large; use compile_and_diff.')
        return data
    except OSError as exc:
        raise NetworkError('Cannot read project inputs; fix their paths and permissions before using patches.') from exc


def snapshot(engine, slug, function):
    """The current definition and a content revision of its unit, headers and local build inputs."""
    proj, fp, cfg = engine.get(slug)
    fn = engine._fn(slug, function)
    unit = fp.unit(fn['unit'])
    if not unit.source:
        raise NetworkError('This function needs a unit source file before using patches or recipes.')
    try:
        text = _read(fp.safe_path(unit.source)).decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
    except (OSError, UnicodeError) as exc:
        raise NetworkError('Cannot read the unit source; fix its path and encoding before using patches.') from exc
    _, span = engine._find(text, engine._names(engine._meta(fn), fn))
    if span is None:
        raise NetworkError('Add the definition with compile_and_diff first.')
    source = text[span[0]:span[1]]
    if len(source.encode()) > MAX_SOURCE:
        raise NetworkError('This definition is too large for a compact patch request.')
    inputs = {unit.source, 'objdiff.json', 'build.ninja', 'Makefile', 'CMakeLists.txt', '.decomp.wtf.json'}
    visited = 0
    for directory, dirs, files in os.walk(fp.root):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED)
        visited += len(dirs) + len(files)
        if visited > MAX_FILES:
            raise NetworkError('Project input scan is too large; use compile_and_diff for this project.')
        for name in files:
            rel = (Path(directory) / name).relative_to(fp.root)
            if (rel.suffix.lower() in {'.h', '.hpp', '.hh', '.hxx', '.inc'}
                    or rel.parts[0] in {'tools', 'config', 'harness'}):
                inputs.add(rel.as_posix())
    # UI counters and the progress-save revision do not change the compiler's inputs.
    settings = {k: v for k, v in cfg.items() if k not in {
        'measures', 'uncovered', 'set_aside', 'link_failed', 'instructions', 'spend',
        'suggestions', 'knowledge_auto', 'autopilot', 'checked', 'legal', 'ready', 'progress_revision'}}
    digest = hashlib.sha256(json.dumps([fn['id'], fn['unit'], settings], sort_keys=True).encode())
    total = 0
    for name, path in [(name, fp.safe_path(name)) for name in sorted(inputs)] + [('target', unit.target)]:
        if path is None or not path.exists():
            digest.update(name.encode() + b'\0missing\0')
            continue
        data = _read(path, MAX_INPUTS - total)
        total += len(data)
        digest.update(name.encode() + b'\0' + data + b'\0')
    return fn, source, digest.hexdigest()


def definition(engine, fn, source):
    """Accept only this complete definition: declarations and preprocessing belong in edit_file."""
    names = engine._names(engine._meta(fn), fn)
    clean = csource._blank(source)
    _, span = engine._find(source, names)
    head = next((m for name in names if (m := re.search(r'(?<![\w:])' + re.escape(name) + r'\s*\(', clean))), None)
    if (span is None or head is None or re.search(r'[;{}#]', clean[:head.start()])
            or '#' in clean or source[:span[0]].strip() or source[span[1]:].strip()):
        raise NetworkError('Patches and recipes may change only the complete function definition; use edit_file for shared code.')
    return source


def _claim(engine, agent, fn):
    owner = engine.net.store.one('SELECT agent_id FROM claims WHERE function_id=? AND expires_at>?',
                                fn['id'], time.time())
    if owner is not None and owner['agent_id'] != agent.id:
        raise NetworkError('This function is claimed by another helper; wait for it to be released.')


def _fresh_build(engine, proj, fp, unit):
    """Force a real build, even when a custom build omits a header dependency; never unlink an original."""
    base = unit.base
    root = fp.root.resolve()
    if base is None or base.suffix.lower() not in {'.o', '.obj'}:
        raise NetworkError('Preview requires a generated .o or .obj candidate output.')
    resolved = base.resolve()
    if (root not in resolved.parents or any(p.is_symlink() for p in (base, *base.parents) if p != fp.root and fp.root in p.parents)
            or any(p in {'orig', 'extract', '.git'} for p in resolved.relative_to(root).parts)
            or any(u.target is not None and u.target.resolve() == resolved for u in fp.units())
            or resolved == (fp.root / unit.source).resolve() or base.exists() and base.stat().st_nlink != 1):
        raise NetworkError('Candidate output overlaps protected files or is outside the project; fix its build configuration.')
    if base.exists() and base.stat().st_size > MAX_INPUTS:
        raise NetworkError('Candidate output is too large for a reversible preview.')
    old = _read(base) if base.exists() else None
    base.unlink(missing_ok=True)
    try:
        ok, log = engine._build_unit(proj, fp, unit)
        if ok and base.is_file() and not base.is_symlink():
            return True, log
    except BaseException:
        if old is not None:
            base.unlink(missing_ok=True)
            base.write_bytes(old)
        raise
    if old is not None:
        base.unlink(missing_ok=True)
        base.write_bytes(old)
    return False, log


def preview(engine, agent, slug, function, source):
    """Measure without bookkeeping or publication, restoring source and outputs on every exit."""
    from .forkengine import _CHEAT, _strip_comments, scaled

    proj, fp, _ = engine.get(slug)
    fn = engine._fn(slug, function)
    definition(engine, fn, source)
    if _CHEAT.search(_strip_comments(source)):
        raise NetworkError('Inline assembly and binary includes are not allowed.')
    unit = fp.unit(fn['unit'])
    symbol = engine._meta(fn).get('symbol') or fn['name']
    with engine._tree(proj).exclusive(), engine._unit_lock(proj, unit.name):
        _claim(engine, agent, fn)
        _, after, path, before = engine._place(proj, fp, unit, fn, source,
                                                engine._names(engine._meta(fn), fn))
        raw = path.read_bytes()
        ok, log = _fresh_build(engine, proj, fp, unit)
        if not ok:
            return {'text': 'Baseline build failed; no trial applied.\n' + log[-1500:], 'score': None, 'matched': False}
        try:
            baseline = fp.diff(unit, symbol)
        except (ObjdiffError, ForkError) as exc:
            return {"text": f"Baseline comparison failed: {exc}", "score": None, "matched": False}
        held = engine._held(proj, unit.name, before)
        protected = {n for n, pct in baseline.unit_scores.items() if pct is not None and pct >= 100 and n not in held}
        try:
            _claim(engine, agent, fn)
            path.write_text(after, encoding='utf-8')
            ok, log = _fresh_build(engine, proj, fp, unit)
            if not ok:
                return {'text': 'BUILD ERROR: ' + log[-1500:], 'score': None, 'matched': False}
            diff = fp.diff(unit, symbol)
            broken = sorted(n for n in protected if (diff.unit_scores.get(n) or 0) < 100)
            if broken or baseline.matched and not diff.matched:
                return {'text': 'Rejected: breaks existing matches.', 'score': None, 'matched': False, 'broken': broken}
            if unit.complete and fp.linkable() and diff.matched and not engine._verify(proj, fp)[0]:
                return {'text': 'Rejected: linked game differs.', 'score': None, 'matched': False}
            if diff.match_percent is None:
                return {'text': 'The compiled object does not define this function.', 'score': None, 'matched': False}
            score = max(1, len(diff.literals)) if diff.literals else scaled(diff.match_percent, fn['n_insns'])
            return {'text': f'{percent_text(diff.match_percent)} (score {score})\n' + diff.render(),
                    'score': score, 'matched': diff.matched}
        except (ObjdiffError, ForkError) as exc:
            return {'text': f'Comparison failed: {exc}', 'score': None, 'matched': False}
        finally:
            path.write_bytes(raw)
            restored, log = _fresh_build(engine, proj, fp, unit)
            if unit.complete and fp.linkable() and restored:
                restored, log = engine._build_all(proj, fp)
            if not restored:
                raise NetworkError('Preview restored the source but rebuilding it failed: ' + log[-1500:])


def _compact(result, preview_only=False):
    text = result['text']
    return text if len(text) <= 2400 else text[:2400] + ('\nPreview comparison truncated.' if preview_only else '\nUse last_diff for the full comparison.')


def brief(net, agent, slug, function):
    engine = net.forks
    proj, _, _ = engine.get(slug)
    with engine._tree(proj).exclusive():
        fn, source, revision = snapshot(engine, slug, function)
        return (f"{fn['name']}: {fn['status']}, best score {fn['best_score']} (0 = identical)\n"
                f'revision: {revision}\n```c\n{source.strip()}\n```\n'
                'Use this revision with compile_patch or try_recipes. get_function has assembly and notes.')


def compile_patch(net, agent, slug, function, revision, edits):
    engine = net.forks
    proj, _, _ = engine.get(slug)
    with engine._tree(proj).exclusive():
        fn, source, current = snapshot(engine, slug, function)
        if revision != current:
            raise NetworkError('Stale revision: get function_brief again before editing.')
        source = definition(engine, fn, patch(source, edits))
        result = engine.attempt(agent, slug, function, source)
        return _compact(result) + '\nrevision: ' + snapshot(engine, slug, function)[2]


def try_recipes(net, agent, slug, function, revision, recipes, apply=False):
    """Preview up to eight explicit patch sets from one baseline; publish only through the ordinary judge."""
    if not isinstance(apply, bool):
        raise NetworkError('apply must be true or false.')
    if not isinstance(recipes, list) or not 1 <= len(recipes) <= MAX_TRIALS:
        raise NetworkError('Send between 1 and 8 recipes.')
    engine = net.forks
    proj, _, _ = engine.get(slug)
    with engine._tree(proj).exclusive():
        fn, source, current = snapshot(engine, slug, function)
        if revision != current:
            raise NetworkError('Stale revision: get function_brief again before trying recipes.')
        variants, names, seen = [], set(), {source}
        for recipe in recipes:
            if not isinstance(recipe, dict) or set(recipe) != {'name', 'edits'}:
                raise NetworkError('Each recipe must contain name and edits.')
            name = recipe['name']
            if not isinstance(name, str) or not name.strip() or len(name) > 80 or name in names:
                raise NetworkError('Recipe names must be unique nonempty strings of at most 80 characters.')
            names.add(name)
            candidate = definition(engine, fn, patch(source, recipe['edits']))
            if candidate in seen:
                raise NetworkError('Each recipe must produce a distinct, changed definition.')
            seen.add(candidate)
            variants.append((name, candidate))
        baseline = preview(engine, agent, slug, function, source)
        if baseline['score'] is None:
            return 'Baseline could not be judged; no recipes applied.\n' + _compact(baseline, True)
        best, winner = baseline['score'], None
        rows = ['Baseline: ' + _compact(baseline, True)]
        for name, candidate in variants:
            if snapshot(engine, slug, function)[2] != revision:
                raise NetworkError('Project inputs changed during trials; no winner applied. Refresh function_brief.')
            result = preview(engine, agent, slug, function, candidate)
            rows.append(name + ': ' + _compact(result, True))
            if result['score'] is not None and result['score'] < best:
                best, winner = result['score'], (name, candidate)
            if result['matched']:
                break
        if snapshot(engine, slug, function)[2] != revision:
            raise NetworkError('Project inputs changed during trials; no winner applied. Refresh function_brief.')
        if winner is None:
            rows.append('No strictly better candidate; source unchanged.')
        elif apply:
            # A fresh baseline is required after trials, even if ordinary compiles cached one before them.
            engine._scores.pop((proj['id'], fn['unit']), None)
            rows.append('Applying ' + winner[0] + ' through the normal judge:\n' + _compact(
                engine.attempt(agent, slug, function, winner[1])))
        else:
            rows.append('Best: ' + winner[0] + '. Preview only; repeat with apply=true to judge and keep it.')
        return '\n'.join(rows) + '\nrevision: ' + snapshot(engine, slug, function)[2]
