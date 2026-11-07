"""Make a remembered portable save a Git checkout without adding unrelated files."""
import json
from pathlib import Path

from ..network import NetworkError
from . import repository_git, repository_operations, repository_progress, repository_sync, saves

git = repository_git.git
# An allowlist also protects game images with unfamiliar extensions or names.
IGNORE = '\n# decompdecomp portable progress: never add the game or local app state\n/*\n!/.gitignore\n!/decompdecomp.json\n!/README.md\n!/decompdecomp-progress.svg\n!/src/\n/src/*\n!/src/*.c\n!/best/\n/best/*\n!/best/*.c\n!/include/\n/include/*\n!/include/*.h\n'


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
    return {'folder': str(path), 'save_folder':str(saves.save_folder(net, slug).expanduser().resolve()),
            'root': str(root) if root else '', 'repository': bool(root),
            'origin': origin, 'checkpoint': checkpoint, 'portable': net.is_rom(proj),
            'linked': path == saves.default_folder(net, slug).resolve(),
            'build_folder': str(build or '')}


def bind(net, slug, path, proj):
    """Only adopt a remembered backup after it agrees with the authoritative save."""
    if path == saves.default_folder(net, slug).resolve():
        net._update_cfg(slug, lambda cfg: cfg.update(progress_folder=str(path)))
        return
    if net.cfg(proj).get('auto_commit_matches'):
        raise NetworkError('Turn off automatic commits before moving the progress folder.')
    net.progress.sync(slug, force=True)
    def comparable(folder):
        files = saves._read_owned(folder)
        manifest = json.loads(files[saves.MANIFEST])
        for key in ('revision', 'saved_at'):
            manifest.pop(key, None)
        return {**files, saves.MANIFEST: json.dumps(manifest, sort_keys=True)}
    if comparable(path) != comparable(saves.default_folder(net, slug)):
        raise NetworkError('This save differs from current progress. Review its edits and Save now before using it for automatic commits.')
    net._update_cfg(slug, lambda cfg: cfg.update(progress_folder=str(path)))


def run(net, slug, action, origin, log):
    path, proj = folder(net, slug)
    with net.progress._project_lock(slug):
        with repository_operations.operation(root_at(path) or path, slug, 'Setting up progress repository', log) as output:
            if output is None:
                raise NetworkError('Another operation or queued push is using this repository. Wait for it to finish.')
            log = output
            root = root_at(path)
            if action == 'init':
                if not net.is_rom(proj):raise NetworkError('Imported projects use their existing source checkout.')
                if root:raise NetworkError('This save already belongs to a Git repository. Refresh its settings.')
                saves._check_save_paths(path, chosen=True)
                manifest = json.loads((path/saves.MANIFEST).read_text(encoding='utf-8'))
                if manifest.get('game', {}).get('sha1') != net.cfg(proj).get('rom', {}).get('sha1'):
                    raise NetworkError('Save this project to the selected folder before creating its repository.')
                ignore = path/'.gitignore'
                if ignore.is_symlink():raise NetworkError('Replace the .gitignore symbolic link before setting up Git.')
                bind(net, slug, path, proj)
                git(path, 'init', '--initial-branch=main')
                # Existing rules stay last so a user's exclusions retain precedence.
                previous = ignore.read_bytes() if ignore.exists() else b''
                ignore.write_bytes(IGNORE.encode() + previous)
                log('Created Git repository and .gitignore. No files were staged or committed.')
            elif action == 'origin':
                if not root:raise NetworkError('Create a Git repository before adding origin.')
                if 'origin' in git(root, 'remote').splitlines():raise NetworkError('Origin is already configured. Refresh its settings.')
                if not origin or origin.startswith('-') or any(c in origin for c in '\r\n\0'):
                    raise NetworkError('Enter a repository URL or SSH address for origin.')
                git(root, 'remote', 'add', '--', 'origin', origin)
                log('Added origin. Nothing was fetched or pushed.')
            elif action == 'checkpoint':
                if not root or not net.is_rom(proj):raise NetworkError('Choose a portable Git progress save before committing.')
                bind(net, slug, path, proj)
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
                    names = repository_progress.owned(manifest)
                    files = {}
                    for name in names:
                        item = repository_sync.inside(path, name)
                        if not item.is_file():raise NetworkError('Save current progress before creating its first commit.')
                        files[(path.relative_to(root)/name).as_posix()] = item.read_bytes()
                    paths = set(files)
                if root == path and (root/'.gitignore').is_file():
                    files['.gitignore'] = repository_sync.inside(root, '.gitignore').read_bytes()
                    paths.add('.gitignore')
                commit = repository_progress.commit_snapshot(root, files, paths, log, expected_head=head, expected_branch=branch,
                    message='Save decompilation progress')
                log('Saved progress commit '+commit[:12]+'. Nothing was pushed.')
            else:raise NetworkError('Choose Create repository, Add origin or Commit saved progress.')
        return status(net, slug)
