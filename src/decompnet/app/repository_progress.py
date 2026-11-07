"""Commit a bound progress save without treating autosaved matches as unrelated Git edits."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from ..network import NetworkError
from . import repository_git, repository_sync, saves


def owned(manifest):
    """Only manifest-listed C, headers and portable metadata belong to a progress checkpoint."""
    if (not isinstance(manifest, dict) or not isinstance(manifest.get('functions'), dict)
            or not isinstance(manifest.get('headers'), dict)
            or any(not isinstance(name, str) or not isinstance(value, dict)
                   for name, value in manifest['functions'].items())
            or any(not isinstance(name, str) for name in manifest['headers'].values())):
        raise NetworkError('Fix the saved progress manifest before committing its files.')
    paths = {saves.MANIFEST, 'README.md', 'decompdecomp-progress.svg'}
    paths.update(('src/' if 'matched_by' in value else 'best/') + name + '.c'
                 for name, value in manifest['functions'].items())
    paths.update('include/' + name for name in manifest['headers'].values())
    return paths


def recover_missing_sources(net, slug, folder, manifest, missing, log):
    """An older partial save could acknowledge a manifest without materializing its C files.
    Restore only still-listed source from the current row, never overwrite a contributor's file."""
    store = net.project_store(slug)
    pid = store.one('SELECT id FROM projects WHERE slug=?', slug)['id']
    recovered = {}
    for path in sorted(missing):
        if not path.startswith(('src/', 'best/')) or not path.endswith('.c'):
            continue
        name = path.split('/', 1)[1][:-2]
        info = manifest['functions'].get(name)
        row = store.one('SELECT * FROM functions WHERE project_id=? AND name=?', pid, name)
        if not info or not row or row['vram'] != info.get('vram'):
            continue
        if path.startswith('src/'):
            source = row['matched_source'] if row['status'] == 'matched' else None
        else:
            source = row['best_source'] if row['status'] != 'matched' else None
        if source:
            recovered[path] = saves._source_text(source)
    if not recovered:
        return False
    if not saves._publish(folder, recovered, expected={name:None for name in recovered}, chosen=True):
        raise NetworkError('Progress changed while recovering missing source files. Retry after its edits finish.')
    for name in recovered:
        log(f'Recovered missing saved source {name} from the project. It will be checked before committing.')
    return True


def snapshot(net, slug, root, log, *, recover=True, allow_unrelated_edits=False):
    """Read contributions through the ordinary judge, then capture files under the autosave lock.

    Per-function commits may leave unrelated edits in place: their caller selects a strict
    subset of this snapshot for the private index. Whole-progress checkpoints still reject them.
    """
    store = net.project_store(slug)
    folder = saves.default_folder(net, slug).resolve()
    with net.progress._project_lock(slug):
        net.progress.sync(slug, force=True, logger=log)
        if net.progress.errors.get(slug):
            raise NetworkError(net.progress.errors[slug])
        removed = net.progress.prune_matched_drafts(slug)
        if removed:
            log(f'Removed {removed} redundant best drafts with verified matched sources.')
        saves._check_save_paths(folder, chosen=True)
        files = saves._read_owned(folder)
        from .progress import _Snapshot
        # project_row synchronizes saved files. A second sync here can rejudge recovered
        # sources and replace the baseline after files were read, producing a false race.
        proj = store.one('SELECT * FROM projects WHERE slug=?', slug)
        baseline = net.progress._baseline(proj['id'])
        if baseline is None or any(baseline.disk.files.get(name) != value
                                   for name, value in _Snapshot.of(files).files.items()):
            raise NetworkError('Progress changed while capturing it. Retry after its edits finish.')
        manifest = json.loads(files[saves.MANIFEST])
        if manifest.get('game', {}).get('sha1') != net.cfg(proj).get('rom', {}).get('sha1'):
            raise NetworkError('The saved progress belongs to another game. Choose this project’s progress checkout.')
        for name in ('README.md', 'decompdecomp-progress.svg'):
            if (folder/name).is_file():
                files[name] = (folder/name).read_bytes().decode('utf-8')
        missing = owned(manifest) - files.keys()
        if missing:
            if recover and recover_missing_sources(net, slug, folder, manifest, missing, log):
                return snapshot(net, slug, root, log, recover=False, allow_unrelated_edits=allow_unrelated_edits)
            names = ', '.join(sorted(missing)[:12])
            extra = f' (and {len(missing)-12} more)' if len(missing)>12 else ''
            raise NetworkError(f'Cannot commit: {len(missing)} saved progress file(s) are missing: {names}{extra}. '
                               'Restore these files from Git or save a recovered copy of the project, then retry.')
        prefix = folder.relative_to(root).as_posix()
        prefix = '' if prefix == '.' else prefix + '/'
        paths = {prefix + name for name in owned(manifest)}
        tracked = repository_sync.git(root, 'ls-files', '-z').decode().split('\0')
        if prefix + saves.MANIFEST in tracked:
            previous = json.loads(repository_sync.git(root, 'show', 'HEAD:' + prefix + saves.MANIFEST))
            paths.update(prefix + name for name in owned(previous))
        for name in paths:
            repository_sync.inside(root, name)
        # A tracked redundant draft must be deleted in the checkpoint too, even if an
        # older manifest selected only src/ and never listed the leftover best/ file.
        paths.update(prefix + f'best/{name}.c' for name, info in manifest['functions'].items()
                     if 'matched_by' in info and prefix + f'best/{name}.c' in tracked)
        changed = set(filter(None, repository_sync.git(root, 'diff', '--name-only', '--no-renames', '-z', 'HEAD').decode().split('\0')))
        changed.update(filter(None, repository_sync.git(root, 'ls-files', '--others', '--exclude-standard', '-z').decode().split('\0')))
        if changed - paths and not allow_unrelated_edits:
            repository_sync.checkout(root, blocking=changed - paths)  # Show only files that block this checkpoint.
        # Retired paths may be deleted, but never discard a file that survived a rename or layout change.
        if not allow_unrelated_edits and any(repository_sync.inside(root, name).exists() for name in paths - {prefix+n for n in files}):
            raise NetworkError('Retired progress files still contain edits. Move them before committing progress.')
        captured = {}
        for name, text in files.items():
            data = (folder/name).read_bytes()
            normalized = data.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
            if name not in {'README.md', 'decompdecomp-progress.svg'} and normalized != text:
                raise NetworkError('Progress changed while capturing it. Retry after its edits finish.')
            captured[prefix + name] = data
        return captured, paths, manifest


_ANY_HEAD = object()


def commit_snapshot(root, files, paths, log, *, expected_head=_ANY_HEAD, expected_branch=None, message='Save matches and all decompilation progress', allow_empty=False):
    """Use a private index; helper writes after capture remain uncommitted in the working tree."""
    git = repository_git.git
    head = git(root, 'rev-parse', '--verify', '--quiet', 'HEAD', missing=True)
    if expected_head is not _ANY_HEAD and head != expected_head:
        raise NetworkError('The checkout committed new work during capture. Review Git and retry.')
    branch = git(root, 'symbolic-ref', '--quiet', 'HEAD')
    if expected_branch is not None and branch != expected_branch:
        raise NetworkError('The checkout switched branches during capture. Review Git and retry.')
    if git(root, 'diff', '--cached', '--name-only'):
        raise NetworkError('Review or commit staged changes before committing progress from the app.')
    with tempfile.TemporaryDirectory(prefix='decomp-progress-commit-') as temp:
        index = Path(temp)/'index'
        env = {'GIT_INDEX_FILE': str(index)}
        git(root, 'read-tree', *([head] if head else ['--empty']), env=env)
        names = sorted(files)
        # Feed blobs and index records in batches: large saves contain thousands of C files.
        blobs = []
        for number, name in enumerate(names):
            check = getattr(log, 'check_cancel', None)
            if check:check()
            blob = Path(temp)/f'blob-{number}'
            blob.write_bytes(files[name])
            blobs.append(str(blob))
            progress = getattr(log, 'progress', None)
            if progress:progress({'phase': 'Capturing progress files', 'finished': number+1, 'total': len(names)})
        hashes = git(root, 'hash-object', '-w', '--no-filters', '--stdin-paths',
                     input=('\n'.join(blobs)+'\n').encode()).splitlines()
        records = [f'100644 {oid}\t{name}\0' for name, oid in zip(names, hashes)]
        zero = '0' * len(git(root, 'hash-object', '--stdin', input=b''))
        records.extend(f'0 {zero}\t{name}\0' for name in sorted(paths - files.keys()))
        git(root, 'update-index', '-z', '--index-info', env=env, input=''.join(records).encode())
        tree = git(root, 'write-tree', env=env)
        if head and not allow_empty and tree == git(root, 'rev-parse', head+'^{tree}'):
            return head
        check = getattr(log, 'check_cancel', None)
        if check:check()
        # Hold Git's actual index lock only for publication, never during compilation or networking.
        index_path = Path(git(root, 'rev-parse', '--path-format=absolute', '--git-path', 'index'))
        lock = Path(str(index_path)+'.lock')
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise NetworkError('Another Git operation is running. Wait for it to finish, then commit progress again.') from None
        try:
            with os.fdopen(fd, 'wb') as output, index.open('rb') as source:
                shutil.copyfileobj(source, output)
            if git(root, 'symbolic-ref', '--quiet', 'HEAD') != branch or git(root, 'diff', '--cached', '--name-only'):
                raise NetworkError('The branch or staged files changed. Review Git before committing progress again.')
            commit = git(root, 'commit-tree', tree, *(['-p', head] if head else []), '-m', message)
            git(root, 'update-ref', '-m', 'Save decompilation progress', branch, commit, head or zero)
            os.replace(lock, index_path)
            return commit
        finally:
            lock.unlink(missing_ok=True)


def run(net, slug, root, log, *, commit_push):
    """A portable progress save is already judged by the app; it is not a standalone rebuild."""
    if repository_git.git(root, 'diff', '--cached', '--name-only'):
        raise NetworkError('Review or commit staged changes before committing progress from the app.')
    upstream = repository_git.prepare(root, log) if commit_push else None
    head = repository_git.git(root, 'rev-parse', 'HEAD')
    branch = repository_git.git(root, 'symbolic-ref', '--quiet', 'HEAD') if commit_push else None
    log('Reading the project’s saved progress and checking contributions…')
    files, paths, manifest = snapshot(net, slug, root, log)
    matched = sum('matched_by' in item for item in manifest['functions'].values())
    result = {'mode': 'progress', 'folder': str(root), 'matched': matched,
              'best': len(manifest['functions']) - matched, 'accepted': [], 'skipped': [],
              'verified_build': False, 'changes': repository_sync.git(root, 'status', '--short').decode()}
    if commit_push:
        log('Committing the captured progress snapshot; newer helper edits stay local…')
        commit = commit_snapshot(root, files, paths, log, expected_head=head, expected_branch=branch)
        result.update(repository_git.push(root, commit, upstream, log))
    else:
        log(f'Progress ready to review: {matched} matches and {result["best"]} best drafts. Use Commit and push to publish it.')
    return result
