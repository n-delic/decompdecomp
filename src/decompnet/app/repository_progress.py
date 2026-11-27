"""Commit a bound progress save without treating autosaved matches as unrelated Git edits."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from contextlib import nullcontext
from pathlib import Path

from ..network import NetworkError
from . import repository_git, repository_sync, saves

CAPTURE_ATTEMPTS = 3


def linked_checkout(net, slug, root):
    """A build repository can also version its bound progress/ subfolder.

    Build sync still treats the root as a build; automatic commits only own the
    manifest-listed files under the linked save, never standalone source files.
    """
    folder = saves.default_folder(net, slug).resolve()
    root = root.resolve()
    if folder == root and ((root / 'build.json').is_file() or (root / 'build.py').is_file()):
        return False
    return folder.is_relative_to(root) and (folder / saves.MANIFEST).is_file()


def owned(manifest, folder=None):
    """Only manifest-listed C, headers and portable metadata belong to a progress checkpoint, and the data's C its
    index lists (app/data_saves.py) when `folder` is given."""
    if (not isinstance(manifest, dict) or not isinstance(manifest.get('functions'), dict)
            or not isinstance(manifest.get('headers'), dict)
            or any(not isinstance(name, str) or not isinstance(value, dict)
                   for name, value in manifest['functions'].items())
            or any(not isinstance(name, str) for name in manifest['headers'].values())):
        raise NetworkError('Fix the saved progress manifest before committing its files.')
    paths = {saves.MANIFEST, 'README.md', 'decompdecomp-progress.svg'}
    if manifest.get('format') in saves.SPLIT_FORMATS:  # (format 1 holds them inside its manifest)
        paths.update(saves.SIDE_FILES.values())
    paths.update(('src/' if 'matched_by' in value else 'best/') + saves._source_file(name)
                 for name, value in manifest['functions'].items())
    paths.update('include/' + name for name in manifest['headers'].values())
    if folder is not None:
        from . import data_saves
        paths |= data_saves.owned(Path(folder))
    return paths


def committed_manifest_text(root, manifest_path, rev='HEAD'):
    """A commit's joined progress manifest: a format 2 save's notes and verification records are files beside it."""
    text = repository_git.git(root, 'show', f'{rev}:{manifest_path}')
    try:
        split = json.loads(text).get('format') in saves.SPLIT_FORMATS
    except (ValueError, AttributeError):
        split = False
    if not split:
        return text
    prefix = manifest_path[:-len(saves.MANIFEST)]
    present = set(filter(None, repository_git.git(
        root, '--literal-pathspecs', 'ls-tree', '--name-only', '-z', rev, '--',
        *(prefix + name for name in saves.SIDE_FILES.values())).split('\0')))
    side = {name: repository_git.git(root, 'show', f'{rev}:{prefix}{name}') if prefix + name in present else None
            for name in saves.SIDE_FILES.values()}
    return saves.join_manifest(text, side)


def manifest_blobs(prefix, manifest):
    """A manifest as the files a commit holds (split as the folder keeps it), by path in the repository."""
    return {prefix + name: text.encode() for name, text in saves.split_manifest(saves.manifest_text(manifest)).items()}


def data_saves_owned(folder):
    from . import data_saves
    return data_saves.owned(Path(folder))


def recover_missing_sources(net, slug, folder, manifest, missing, log):
    """An older partial save could acknowledge a manifest without materializing its C files.
    Restore only still-listed source from the current row, never overwrite a contributor's file."""
    store = net.project_store(slug)
    pid = store.one('SELECT id FROM projects WHERE slug=?', slug)['id']
    recovered = {}
    names = {saves._source_file(name): name for name in manifest['functions']}
    for path in sorted(missing):
        if not path.startswith(('src/', 'best/')) or not path.endswith('.c'):
            continue
        name = names.get(path.split('/', 1)[1])
        if name is None:
            continue
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


def _capture_bytes(path, text):
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        # A logical side file can come from the manifest itself, or from the
        # save reader's default for missing verification records.
        if path.name in saves.SIDE_FILES.values():
            return text.encode('utf-8')
        raise
    normalized = data.decode('utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
    if path.name not in {'README.md', 'decompdecomp-progress.svg'} and normalized != text:
        # Commit the judged text if an editor changed the file after capture.
        return text.encode('utf-8')
    return data


def snapshot(net, slug, root, log, *, recover=True, allow_unrelated_edits=False, directories=None):
    """Read contributions through the ordinary judge, then capture files under the autosave lock.

    Per-function commits request only the saved directories they read. Their caller selects
    the actual paths to commit, so only checkpoints compare the entire checkout with Git.
    """
    store = net.project_store(slug)
    folder = saves.default_folder(net, slug).resolve()
    progress = getattr(log, 'progress', None) or (lambda _: None)
    with net.progress._project_lock(slug):
        from .progress import _Snapshot
        # Edits that land between the sync and the read (a helper's contribution, an editor) are not an error:
        # sync again and capture the newer state. Only a folder that keeps changing gives up.
        for attempt in range(CAPTURE_ATTEMPTS):
            progress({'phase': 'Checking saved progress', 'finished': 0, 'total': 3})
            net.progress.sync(slug, force=True, logger=log)
            if net.progress.errors.get(slug):
                raise NetworkError(net.progress.errors[slug])
            removed = net.progress.prune_matched_drafts(slug)
            if removed:
                log(f'Removed {removed} redundant best drafts with verified matched sources.')
            progress({'phase': 'Reading saved progress', 'finished': 1, 'total': 3})
            saves._check_save_paths(folder, chosen=True)
            files = saves._read_owned(folder)
            # project_row synchronizes saved files. A second sync here can rejudge recovered
            # sources and replace the baseline after files were read, producing a false race.
            proj = store.one('SELECT * FROM projects WHERE slug=?', slug)
            baseline = net.progress._baseline(proj['id'])
            changed = sorted(name for name, value in _Snapshot.of(files).files.items()
                             if baseline is None or baseline.disk.files.get(name) != value)
            if not changed:
                break
            log(f'{changed[0]}' + (f' and {len(changed)-1} more file(s)' if len(changed) > 1 else '')
                + ' changed while capturing progress; checking again…')
        else:
            raise NetworkError(f'Progress kept changing while capturing it ({", ".join(changed[:3])}). '
                               'Retry after its edits finish.')
        progress({'phase': 'Comparing with Git', 'finished': 2, 'total': 3})
        manifest = json.loads(files[saves.MANIFEST])
        if manifest.get('game', {}).get('sha1') != net.cfg(proj).get('rom', {}).get('sha1'):
            raise NetworkError('The saved progress belongs to another game. Choose this project’s progress checkout.')
        # The commit holds the folder's own files: a format 2 save's notes and verification records apart.
        files = saves.split_files(files)
        visuals = {'README.md', 'decompdecomp-progress.svg'}
        required = owned(manifest) - visuals
        if directories is None:
            for name in visuals:
                if (folder/name).is_file():
                    files[name] = (folder/name).read_bytes().decode('utf-8')
            data_paths = data_saves_owned(folder)
            required.update(data_paths)
            for name in data_paths:
                item = repository_sync.inside(folder, name)
                if item.is_file():
                    files[name] = item.read_text(encoding='utf-8')
        missing = required - files.keys()
        if missing:
            if recover and recover_missing_sources(net, slug, folder, manifest, missing, log):
                return snapshot(net, slug, root, log, recover=False, allow_unrelated_edits=allow_unrelated_edits,
                                directories=directories)
            names = ', '.join(sorted(missing)[:12])
            extra = f' (and {len(missing)-12} more)' if len(missing)>12 else ''
            raise NetworkError(f'Cannot commit: {len(missing)} saved progress file(s) are missing: {names}{extra}. '
                               'Restore these files from Git or save a recovered copy of the project, then retry.')
        prefix = folder.relative_to(root).as_posix()
        prefix = '' if prefix == '.' else prefix + '/'
        if directories is not None:
            captured = {prefix + name: _capture_bytes(repository_sync.inside(root, prefix + name), text)
                        for name, text in files.items() if name.partition('/')[0] in directories}
            return captured, set(captured), manifest
        paths = {prefix + name for name in owned(manifest, folder)}
        tracked = repository_sync.git(root, 'ls-files', '-z').decode().split('\0')
        head = repository_git.git(root, 'rev-parse', '--verify', '--quiet', 'HEAD', missing=True)
        if head and prefix + saves.MANIFEST in tracked:
            previous = json.loads(committed_manifest_text(root, prefix + saves.MANIFEST, head))
            paths.update(prefix + name for name in owned(previous))
        for name in paths:
            repository_sync.inside(root, name)
        # A tracked redundant draft must be deleted in the checkpoint too, even if an
        # older manifest selected only src/ and never listed the leftover best/ file.
        matched_drafts = {prefix + 'best/' + saves._source_file(name)
                          for name, info in manifest['functions'].items() if 'matched_by' in info}
        paths.update(matched_drafts.intersection(tracked))
        from . import rebuildable_repository
        scaffold = rebuildable_repository.files(root)
        paths.update(scaffold)
        paths.update(rebuildable_repository.retired(root))
        # The root README shows the progress map and compiler (save_readme._update_root), so each checkpoint keeps it current.
        readme = root/'README.md'
        if prefix == 'progress/' and rebuildable_repository.full(root) and readme.is_file() and not readme.is_symlink():
            paths.add('README.md')
            scaffold.setdefault('README.md', readme.read_bytes())
        changed = set(filter(None, repository_sync.git(root, 'diff', '--name-only', '--no-renames', '-z', *([head] if head else [])).decode().split('\0')))
        changed.update(filter(None, repository_sync.git(root, 'ls-files', '--others', '--exclude-standard', '-z').decode().split('\0')))
        if changed - paths and not allow_unrelated_edits:
            repository_sync.checkout(root, blocking=changed - paths)  # Show only files that block this checkpoint.
        # Retired paths may be deleted, but never discard a file that survived a rename or layout change.
        if not allow_unrelated_edits and any(repository_sync.inside(root, name).exists() for name in paths - {prefix+n for n in files}):
            raise NetworkError('Retired progress files still contain edits. Move them before committing progress.')
        captured = {prefix + name: _capture_bytes(folder / name, text) for name, text in files.items()}
        captured.update(scaffold)
        return captured, paths, manifest


_ANY_HEAD = object()



def commit_snapshot(root, files, paths, log, *, expected_head=_ANY_HEAD, expected_branch=None, message='Save matches and all decompilation progress', allow_empty=False,
                    publication=None):
    """Use a private index so later helper writes stay uncommitted. A publication context can veto the commit
    or protect project state while Git publishes the prepared tree."""
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
        with publication if publication is not None else nullcontext(True) as allowed:
            if not allowed:
                return None
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
