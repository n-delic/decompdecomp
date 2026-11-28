"""Stage an exported ROM analysis beside a public checkout without changing it."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import limits
from ..forks.project import STATE, ForkError, ForkProject
from ..forks.rom_judge import plan_export, publish_export
from ..hostenv import NO_WINDOW

_EXCLUDE = {'.git', STATE, 'build', 'progress', '__pycache__', 'decompdecomp-sync.json',
            'decompdecomp-repository.json', '.gitattributes'}
_REFERENCE_SUFFIXES = {'.c', '.cc', '.cpp', '.h', '.hpp', '.md', '.txt', '.yaml', '.yml', '.json'}


def _inside(root: Path, name: str) -> Path:
    path = Path(name)
    result = (root/path).resolve()
    if path.is_absolute() or '..' in path.parts or root not in result.parents:
        raise ForkError('The exported ROM path must stay inside the selected folder.')
    current = root
    for part in path.parts:
        current = current/part
        if current.is_symlink():
            raise ForkError('The exported ROM path cannot contain a symbolic link.')
    return result


def _copy_file(source: str, destination: str, *, check_cancel, reuse: bool = False) -> str:
    """Copy atomically; a resumed copy reuses only byte-identical regular files."""
    source_path, dest_path = Path(source), Path(destination)
    if dest_path.is_symlink():
        raise ForkError('The staged analysis contains a symbolic link.')
    if reuse and dest_path.is_file() and source_path.stat().st_size == dest_path.stat().st_size:
        def digest(path):
            value = hashlib.sha256()
            with path.open('rb') as stream:
                while block := stream.read(4 * 1024 * 1024):
                    check_cancel()
                    value.update(block)
            return value.digest()
        if digest(source_path) == digest(dest_path):
            return destination
    fd, temp = tempfile.mkstemp(prefix='.copy-', dir=dest_path.parent)
    temporary = Path(temp)
    try:
        with os.fdopen(fd, 'wb') as dest, source_path.open('rb') as src:
            while block := src.read(4 * 1024 * 1024):
                check_cancel()
                dest.write(block)
        shutil.copystat(source, temporary, follow_symlinks=False)
        check_cancel()
        os.replace(temporary, dest_path)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _git(root: Path, args: list[str]) -> None:
    env = {**os.environ, 'GIT_CONFIG_NOSYSTEM':'1', 'GIT_CONFIG_GLOBAL':os.devnull,
           'GIT_ATTR_NOSYSTEM':'1', 'GIT_AUTHOR_NAME':'decompdecomp',
           'GIT_AUTHOR_EMAIL':'noreply@decompdecomp.invalid', 'GIT_COMMITTER_NAME':'decompdecomp',
           'GIT_COMMITTER_EMAIL':'noreply@decompdecomp.invalid'}
    result = subprocess.run(limits.capped(['git', *args]), cwd=root, env=env, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=120, creationflags=NO_WINDOW)
    if result.returncode:
        raise ForkError('Could not initialize the local analysis repository: ' + (result.stderr or result.stdout)[-1000:])


def _reference_sources(checkout: Path, destination: Path, log, check_cancel) -> None:
    """Copy small text sources for context; the public checkout remains authoritative."""
    total = 0
    skipped = False
    for folder, dirs, files in os.walk(checkout, followlinks=False):
        check_cancel()
        base = Path(folder)
        dirs[:] = [name for name in dirs if name not in _EXCLUDE and name not in ('assets', 'bin')
                   and not (base/name).is_symlink()]
        for name in files:
            path = base/name
            if name in _EXCLUDE or path.is_symlink() or path.suffix.lower() not in _REFERENCE_SUFFIXES:
                continue
            size = path.stat().st_size
            if size > 2 * 1024 * 1024 or total + size > 64 * 1024 * 1024:
                skipped = True
                continue
            dest = destination/path.relative_to(checkout)
            dest.parent.mkdir(parents=True, exist_ok=True)
            _copy_file(str(path), str(dest), check_cancel=check_cancel)
            total += size
    if skipped:
        log('Some large reference files were not copied; the original checkout is unchanged.')


def prepare(checkout: Path, export: Path, expected_sha1: str, platform: str, log=print, *,
            checkpoint=None, resume_stage: Path | None = None) -> Path:
    """Prepare and verify a private workspace; no project row changes until the caller adopts it."""
    checkout, export = checkout.resolve(), export.expanduser().resolve()
    check_cancel = getattr(log, 'check_cancel', lambda:None)
    if not checkout.is_dir() or not export.is_dir() or export == checkout or export in checkout.parents:
        raise ForkError('Choose a separate exported ROM project folder.')
    if not re.fullmatch(r'[0-9a-fA-F]{40}', expected_sha1 or ''):
        raise ForkError('Add your game to the public project before choosing an exported analysis.')
    try:
        original = json.loads((export/'build.json').read_text(encoding='utf-8'))
        if (not isinstance(original.get('sha1'), str) or original['sha1'].lower() != expected_sha1.lower()
                or original.get('platform') != platform):
            raise ForkError('The exported analysis does not match the game and platform selected for this project.')
        image_name = original['image']
        if (not isinstance(image_name, str) or not re.fullmatch(r'[A-Za-z0-9._/-]+', image_name)
                or Path(image_name).parts[0] in ('src', 'include')):
            raise ForkError('The exported game image has an unsupported path.')
        image = _inside(export, image_name)
        digest = hashlib.sha1()
        with image.open('rb') as stream:
            while block := stream.read(4 * 1024 * 1024):
                check_cancel()
                digest.update(block)
        if digest.hexdigest() != expected_sha1.lower():
            raise ForkError('The exported game image does not match the game selected for this project.')
        if not (export/'objdiff.json').is_file():
            raise ForkError('The selected folder is not an exported ROM project with objdiff.json.')
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise ForkError(f'Could not read the exported ROM project: {error}') from error
    if not shutil.which('git'):
        raise ForkError('Install git to keep the generated analysis and its matches locally.')
    state = checkout/STATE
    for directory in (state, state/'rom-reference-workspaces'):
        if directory.is_symlink():
            raise ForkError('The project state folder cannot be a symbolic link.')
        directory.mkdir(exist_ok=True)
    parent = state/'rom-reference-workspaces'
    if export == parent or export in parent.parents:
        raise ForkError('Choose an export outside this project\'s analysis workspace.')
    if resume_stage is not None:
        stage = Path(resume_stage)
        if (stage.parent != parent or not stage.name.startswith('.preparing-') or stage.is_symlink()
                or not stage.is_dir() or stage.resolve().parent != parent.resolve()):
            raise ForkError('The saved setup checkpoint is unavailable or outside this project.')
        # A checkpoint contains only files the app created. Never follow a link
        # substituted into it while setup was stopped.
        for folder, dirs, files in os.walk(stage, followlinks=False):
            for name in (*dirs, *files):
                item = Path(folder)/name
                if item.is_symlink() or not (item.is_dir() or item.is_file()):
                    raise ForkError('The saved setup checkpoint contains a symbolic link or special file.')
    else:
        stage = Path(tempfile.mkdtemp(prefix='.preparing-', dir=parent))
    final = parent/secrets.token_hex(16)
    workspace, snapshot = stage/'workspace', stage/'snapshot'
    retained = resume_stage is not None
    try:
        if checkpoint is not None and not retained:
            checkpoint(stage)
            retained = True
        if workspace.exists():
            shutil.rmtree(workspace)
        snapshot.mkdir(exist_ok=True)
        log('Copying the exported analysis; its original folder will not be changed')
        def ignore(folder, names):
            check_cancel()
            base = Path(folder)
            for name in names:
                if name not in _EXCLUDE:
                    item = base/name
                    if item.is_symlink():
                        raise ForkError('The exported analysis contains a symbolic link; copy its files into the folder first.')
                    if not item.is_file() and not item.is_dir():
                        raise ForkError('The exported analysis contains a non-regular file.')
            return {name for name in names if name in _EXCLUDE}
        def copy_cached(source, destination):
            cached = snapshot/Path(source).relative_to(export)
            cached.parent.mkdir(parents=True, exist_ok=True)
            _copy_file(source, str(cached), check_cancel=check_cancel, reuse=True)
            return _copy_file(str(cached), destination, check_cancel=check_cancel)
        shutil.copytree(export, workspace, symlinks=True, ignore=ignore, copy_function=copy_cached)
        copied = json.loads((workspace/'build.json').read_text(encoding='utf-8'))
        if (not isinstance(copied.get('sha1'), str) or copied['sha1'].lower() != expected_sha1.lower()
                or copied.get('platform') != platform
                or copied.get('image') != image_name):
            raise ForkError('The exported game information changed during setup; try again.')
        log('Keeping a read-only reference copy of the public project sources')
        _reference_sources(checkout, workspace/'reference', log, check_cancel)
        ignore_file = workspace/'.gitignore'
        previous = ignore_file.read_text(encoding='utf-8') if ignore_file.exists() else ''
        ignore_file.write_text(previous.rstrip() + f'\n/{STATE}/\n/reference/\n/asm/\n/assets/\n/build/\n/{image_name}\n',
                               encoding='utf-8')
        (workspace/'ROM_REFERENCE.md').write_text(
            'This local analysis uses the selected original game and the ROM judge. '
            'The public checkout is unchanged; reference/ is a copy for investigation. '
            'New matches are kept in this local analysis, not applied to the public project build.\n', encoding='utf-8')
        project = ForkProject(workspace, create_state=False)
        log('Preparing original-code references and editable candidate files')
        plan = plan_export(project, 'build.json', log=log)
        publish_export(project, plan, log=log)
        check_cancel()
        log('Creating the local analysis repository')
        _git(workspace, ['init', '-q'])
        _git(workspace, ['symbolic-ref', 'HEAD', 'refs/heads/decomp-wtf'])
        paths = [p.relative_to(workspace).as_posix() for folder in ('src', 'include')
                 for p in (workspace/folder).rglob('*') if p.is_file() and not p.is_symlink()
                 and p.suffix.lower() in ('.c', '.cc', '.cpp', '.h', '.hpp', '.inc')]
        paths.extend(['.gitignore', 'ROM_REFERENCE.md'])
        for start in range(0, len(paths), 128):
            check_cancel()
            _git(workspace, ['add', '-f', '--', *paths[start:start+128]])
        _git(workspace, ['commit', '-q', '-m', 'Initialize ROM-backed analysis'])
        check_cancel()
        if final.exists() or final.is_symlink():
            raise ForkError('The analysis destination already exists; try again.')
        os.rename(workspace, final)
        return final
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError) as error:
        raise ForkError(f'Could not prepare the ROM-backed analysis: {error}') from error
    finally:
        if not retained:
            shutil.rmtree(stage, ignore_errors=True)
