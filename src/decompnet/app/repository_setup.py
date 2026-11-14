"""Create verified full source repositories with nested portable progress."""
import json
from pathlib import Path

from ..network import NetworkError
from . import repository_git, repository_operations, repository_progress, repository_sync, saves

git = repository_git.git
# An allowlist also protects game images with unfamiliar extensions or names.
IGNORE_BEFORE_SPLIT = '\n# decompdecomp portable progress: never add the game or local app state\n/*\n!/.gitignore\n!/decompdecomp.json\n!/README.md\n!/decompdecomp-progress.svg\n!/src/\n/src/*\n!/src/*.c\n!/best/\n/best/*\n!/best/*.c\n!/include/\n/include/*\n!/include/*.h\n'
# (format 2 saves keep their notes and verification records beside the manifest: saves.SIDE_FILES)
IGNORE = '\n# decompdecomp portable progress: never add the game or local app state\n/*\n!/.gitignore\n!/decompdecomp.json\n!/notes.json\n!/verification.json\n!/README.md\n!/decompdecomp-progress.svg\n!/src/\n/src/*\n!/src/*.c\n!/best/\n/best/*\n!/best/*.c\n!/include/\n/include/*\n!/include/*.h\n'


def folder(net, slug):
    proj = net.project_store(slug).one('SELECT * FROM projects WHERE slug=?', slug)
    if proj is None:raise NetworkError('Open a project before setting up Git.')
    if not net.is_rom(proj):
        return Path(proj['root']).resolve(), proj
    return saves.save_folder(net, slug).expanduser().resolve(), proj


def root_at(path):
    # Do not accidentally create a nested repository inside a parent checkout.
    for candidate in (path, *path.parents):
        if (candidate/'.git').exists():
            return repository_sync.checkout(candidate, clean=False)
    return None


def status(net, slug):
    path, proj = folder(net, slug)
    root = root_at(path)
    head = git(root, 'rev-parse', '--verify', '--quiet', 'HEAD', missing=True) if root else None
    origin = bool(root and 'origin' in git(root, 'remote').splitlines())
    checkpoint = bool(head)
    build = saves.build_checkout(path)
    remembered = net.cfg(proj).get('repository_sync_folder')
    if build is None and remembered:
        candidate = Path(remembered).expanduser().resolve()
        if saves.build_checkout(candidate) == candidate:
            build = candidate
    if head and net.is_rom(proj):
        manifest = (path.relative_to(root)/saves.MANIFEST).as_posix()
        checkpoint = manifest in git(root, 'ls-tree', '-r', '--name-only', 'HEAD', '--', manifest).splitlines()
    return {'slug':slug, 'folder': str(path), 'save_folder':str(saves.save_folder(net, slug).expanduser().resolve()),
            'root': str(root) if root else '', 'repository': bool(root),
            'github_create': True, 'origin': origin, 'checkpoint': checkpoint, 'portable': net.is_rom(proj),
            'linked': path == saves.default_folder(net, slug).resolve(),
            'build_folder': str(build or ''), 'rebuildable': bool(build and (build/'progress'/saves.MANIFEST).is_file())}



def known_older_save(net, proj, saved, latest):
    """Prove an unreceipted backup contains older app attempts, not contributor edits.

    Settings, headers and matched C must agree. Only appended notes and improved
    drafts already present in compiled attempt history may supersede this save.
    """
    from .function_names import canonical_source

    old = json.loads(saved[saves.MANIFEST])
    new = json.loads(latest[saves.MANIFEST])
    old_functions = old.pop('functions', {})
    new_functions = new.pop('functions', {})
    old_notes = old.pop('notes', [])
    new_notes = new.pop('notes', [])
    if not isinstance(old_notes, list) or new_notes[:len(old_notes)] != old_notes:
        return False
    if old != new or old_functions.keys() != new_functions.keys():
        return False
    changed = {name for name in saved.keys() | latest.keys()
               if name != saves.MANIFEST and saved.get(name) != latest.get(name)}
    store = net.project_store(proj['id'])
    for name, info in old_functions.items():
        current = new_functions[name]
        relative = 'best/' + name + '.c'
        if info == current and relative not in changed:
            continue
        if ({k:v for k,v in info.items() if k not in ('best_score', 'best_by')}
                != {k:v for k,v in current.items() if k not in ('best_score', 'best_by')}
                or 'matched_by' in info or 'matched_by' in current
                or not isinstance(info.get('best_score'), (int, float))
                or not isinstance(current.get('best_score'), (int, float))
                or current['best_score'] >= info['best_score']
                or relative not in saved or relative not in latest):
            return False
        row = store.one('SELECT id,name,vram,meta FROM functions WHERE project_id=? AND name=?', proj['id'], name)
        if row is None:
            return False
        attempts = store.all('SELECT source FROM attempts WHERE function_id=? AND compiled=1 AND score=?',
                             row['id'], info['best_score'])
        if not any(attempt['source'] is not None and
                   saves._source_text(canonical_source(row, attempt['source'])) == saved[relative]
                   for attempt in attempts):
            return False
        changed.discard(relative)
    return not changed


def bind(net, slug, path, proj, log=lambda line:None):
    """Refresh an untouched app backup, preserving external edits before adoption."""
    if path == saves.default_folder(net, slug).resolve():
        net._update_cfg(slug, lambda cfg: cfg.update(progress_folder=str(path)))
        return
    if net.cfg(proj).get('auto_commit_matches') or net.cfg(proj).get('auto_commit_polished'):
        raise NetworkError('Turn off automatic commits before moving the progress folder.')
    previous = saves._read_owned(path)
    receipt = net.cfg(proj).get('progress_save_receipt') or {}
    untouched = receipt.get('folder') == str(path.resolve()) and receipt.get('digest') == saves.owned_digest(previous)
    net.progress.sync(slug, force=True, logger=log)
    if net.progress.errors.get(slug):raise NetworkError(net.progress.errors[slug])
    def comparable(files):
        # Save now adds a final newline to C; an imported authoritative save may
        # retain the exact source without it. Use the save writer's normalization,
        # preserving leading/internal lines and headers (compiler inputs).
        files = {name:saves._source_text(text) if name.startswith(('src/', 'best/')) and name.endswith('.c') else text
                 for name, text in files.items()}
        manifest = json.loads(files[saves.MANIFEST])
        for key in ('revision', 'saved_at'):
            manifest.pop(key, None)
        return {**files, saves.MANIFEST: json.dumps(manifest, sort_keys=True)}
    current = saves._read_owned(saves.default_folder(net, slug))
    saved, latest = comparable(previous), comparable(current)
    if saved != latest:
        differences = sorted(name for name in saved.keys() | latest.keys() if saved.get(name) != latest.get(name))
        if not untouched and not known_older_save(net, proj, saved, latest):
            detail = ', '.join(differences[:20])
            if len(differences) > 20:detail += f' (and {len(differences)-20} more)'
            raise NetworkError('This save differs from current progress and has external edits or no app save receipt. '
                               'Files that differ: '+detail+'. Review these files before saving current progress to this folder. Nothing was overwritten.')
        log(f'Refreshing {len(differences)} outdated saved progress file(s) before committing…')
        if not saves._publish(path, current, previous, expected=previous, chosen=True):
            raise NetworkError('The selected save changed while preparing the commit. Its external edits were preserved; review it and retry.')
        saves._remember_revision(net, slug, json.loads(current[saves.MANIFEST]))
    net._update_cfg(slug, lambda cfg: cfg.update(progress_folder=str(path)))


def checkpoint(net, slug, root, path, log, *, message='Save decompilation progress'):
    """Commit all saved progress on the checked-out branch: Commit saved progress, and the automatic commits'
    own checkpoint after functions were renamed (match_commits.process). Returns the commit."""
    proj = net.project_store(slug).one('SELECT * FROM projects WHERE slug=?', slug)
    bind(net, slug, path, proj, log)
    for identity in ('GIT_AUTHOR_IDENT', 'GIT_COMMITTER_IDENT'):
        git(root, 'var', identity, error='Set Git user.name and user.email before committing saved progress.')
    head = git(root, 'rev-parse', '--verify', '--quiet', 'HEAD', missing=True)
    branch = git(root, 'symbolic-ref', '--quiet', 'HEAD', error='Switch to a named branch before committing saved progress.')
    if head:
        files, paths, _ = repository_progress.snapshot(net, slug, root, log, allow_unrelated_edits=True)
    else:
        net.progress.sync(slug, force=True, logger=log)
        if net.progress.errors.get(slug):raise NetworkError(net.progress.errors[slug])
        net.progress.prune_matched_drafts(slug)
        saves._check_save_paths(path, chosen=True)
        manifest = json.loads((path/saves.MANIFEST).read_text(encoding='utf-8'))
        names = repository_progress.owned(manifest, path)
        files = {}
        for name in names:
            item = repository_sync.inside(path, name)
            if not item.is_file():raise NetworkError('Save current progress before creating its first commit.')
            files[(path.relative_to(root)/name).as_posix()] = item.read_bytes()
        paths = set(files)
    from . import rebuildable_repository
    scaffold = rebuildable_repository.files(root)
    files.update(scaffold)
    paths.update(scaffold)
    paths.update(rebuildable_repository.retired(root))
    if root == path and (root/'.gitignore').is_file():
        files['.gitignore'] = repository_sync.inside(root, '.gitignore').read_bytes()
        paths.add('.gitignore')
    commit = repository_progress.commit_snapshot(root, files, paths, log, expected_head=head, expected_branch=branch,
        message=message)
    return commit


def run(net, slug, action, origin, log, *, github_name="", visibility="private"):
    path, proj = folder(net, slug)
    # The user clicked this: wait in the repository's queue (cancellable, showing what holds it) rather than fail
    # because an automatic commit or a push has the repository right now. The repository turn comes before the
    # project lock, so a background commit holding the project never waits on us while we wait on it.
    check = getattr(log, 'check_cancel', None) or (lambda: None)
    with repository_operations.push(root_at(path) or path, slug, log, check, label='Setting up progress repository') as output:
        log = output
        with net.progress._project_lock(slug):
            root = root_at(path)
            if action in ('init', 'checkpoint', 'github') and net.is_rom(proj):
                from . import rebuildable_repository
                if not rebuildable_repository.full(root):
                    if (path/saves.MANIFEST).is_file():bind(net, slug, path, proj, log)
                    path = rebuildable_repository.prepare(net, slug, path, root, log)
                    root = root_at(path)
                    proj = net.project_row(slug)
            if action == 'init':
                if not net.is_rom(proj):raise NetworkError('Imported projects use their existing source checkout.')
                root = root or path.parent
                if not (root/'.git').exists():git(root, 'init', '--initial-branch=main')
                log('Created full rebuildable Git repository. Game images and extracted data are ignored; no files were staged or committed.')
            elif action == 'github':
                if not root:raise NetworkError('Create a Git repository before connecting GitHub.')
                if 'origin' in git(root, 'remote').splitlines():raise NetworkError('Origin is already configured. Refresh its settings.')
                from .repository_github import create
                create(root, github_name, visibility, log)
            elif action == 'origin':
                if not root:raise NetworkError('Create a Git repository before adding origin.')
                if 'origin' in git(root, 'remote').splitlines():raise NetworkError('Origin is already configured. Refresh its settings.')
                if not origin or origin.startswith('-') or any(c in origin for c in '\r\n\0'):
                    raise NetworkError('Enter a repository URL or SSH address for origin.')
                git(root, 'remote', 'add', '--', 'origin', origin)
                log('Added origin. Nothing was fetched or pushed.')
            elif action == 'checkpoint':
                if not root or not net.is_rom(proj):raise NetworkError('Choose a portable Git progress save before committing.')
                commit = checkpoint(net, slug, root, path, log)
                log('Saved progress commit '+commit[:12]+'. Nothing was pushed.')
            else:raise NetworkError('This repository action is not supported. Close and reopen decompdecomp after updating, then reload Settings.')
        return status(net, slug)
