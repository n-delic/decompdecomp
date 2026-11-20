"""Verified source repositories with portable app progress; game-derived files stay local."""
import json
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from ..network import NetworkError
from ..rom.export_process import progress_step
from . import repository_sync, saves

MARKER = 'decompdecomp-repository.json'
# A saved repository's own words at its root (a provenance notice, its agents' rules, a license) are public and stay
# as written when it's upgraded: an N64 save tracking NOTICE.md was refused ("Untrack game/private files"), and its
# hand-written AGENTS.md would then have collided with the export's own.
KEPT_DOCS = ('AGENTS.md', 'NOTICE.md', 'CONTRIBUTING.md', 'LICENSE', 'LICENSE.md', 'LICENSE.txt')
BUILD_FILES = frozenset({'build.py', 'build.json', 'export_entry.py', 'splat.yaml', 'symbol_addrs.txt',
                         'reloc_addrs.txt', 'objdiff.json', 'functions.cfg', 'README.md', 'requirements.txt',
                         repository_sync.STATE, MARKER})
ROOT_FILES = BUILD_FILES | frozenset({'.gitignore', *KEPT_DOCS})


def full(root):
    return bool(root and (root/'build.json').is_file() and (root/'build.py').is_file()
                and (root/'progress'/saves.MANIFEST).is_file())


def moved_config(root) -> dict:
    """A project's settings once its progress lives in the full repository at `root`."""
    target = Path(root)/'progress'
    return {'progress_folder': str(target), 'save_folder': str(target), 'repository_sync_folder': str(root)}


def interrupted(folder):
    """The repository root, when `folder` was a flat progress save upgraded in place whose project still points at
    it: its files moved under progress/ but the setting that follows them was never written (an upgrade that stopped
    between the two). None otherwise."""
    folder = Path(folder)
    if (folder/saves.MANIFEST).is_file() or not (folder/MARKER).is_file():return None
    return folder if full(folder) else None


def follow(net, slug, path, log=lambda line:None):
    """The progress folder to use from `path`: where an interrupted upgrade moved it (pointing the project there
    first), else `path` itself. Commit used to work this out from the old folder and put it back in the settings
    before a sync moved it again."""
    if (root := interrupted(path)) is None:return path
    moved = moved_config(root)
    net._update_cfg(slug, lambda cfg: cfg.update(**moved), synchronize=False)
    log(f'Saved progress moved to {moved["progress_folder"]} with its repository; following it.')
    return Path(moved['progress_folder'])


def public(name):
    path = Path(name)
    if path.is_absolute() or '..' in path.parts:return False
    if len(path.parts) == 1:return name in ROOT_FILES
    if path.parts[0] == 'src':return path.suffix in ('.c', '.cpp', '.h')
    if path.parts[0] == 'include':return path.suffix in ('.h', '.inc')
    if path.parts[0] == 'progress':
        rest = path.relative_to('progress')
        # data.json and data/: the game's data in C (app/data_saves.py)
        return (str(rest) in (*saves.TOP_FILES, 'README.md', 'decompdecomp-progress.svg', 'data.json') or
                len(rest.parts) == 2 and rest.parts[0] in ('src', 'best', 'include', 'data')
                and rest.suffix == ('.h' if rest.parts[0] == 'include' else '.c'))
    return False


def ignore():
    # An allowlist protects unrecognized ROM extensions and arbitrary extraction folders too.
    root = '\n'.join('!/'+name for name in sorted(ROOT_FILES) if name != '.gitignore')
    return ('# Full decompilation repository: game images and extracted data stay local.\n/*\n!/.gitignore\n'+root+
            '\n!/src/\n/src/**\n!/src/**/\n!/src/**/*.c\n!/src/**/*.cpp\n!/src/**/*.h\n'
            '!/include/\n/include/**\n!/include/**/\n!/include/**/*.h\n!/include/**/*.inc\n'
            '!/progress/\n/progress/*\n!/progress/decompdecomp.json\n!/progress/notes.json\n!/progress/verification.json\n!/progress/README.md\n'
            '!/progress/decompdecomp-progress.svg\n!/progress/src/\n/progress/src/*\n!/progress/src/*.c\n'
            '!/progress/best/\n/progress/best/*\n!/progress/best/*.c\n!/progress/include/\n'
            '/progress/include/*\n!/progress/include/*.h\n!/progress/data.json\n!/progress/data/\n'
            '/progress/data/*\n!/progress/data/*.c\n')


def pending(root):
    from .repository_git import git
    head = git(root, 'rev-parse', '--verify', '--quiet', 'HEAD', missing=True)
    return not head or MARKER not in git(root, 'ls-tree', '-r', '--name-only', 'HEAD', '--', MARKER).splitlines()


def files(root):
    """Only declared source/configuration and manifest-owned progress may enter a checkpoint."""
    marker = root/MARKER
    if not marker.is_file() or not pending(root):return {}
    record = json.loads(marker.read_text(encoding='utf-8'))
    names = record.get('files', [])
    if not isinstance(names, list) or any(not isinstance(name, str) or not public(name) for name in names):
        raise NetworkError('Invalid rebuildable repository file list. No files were committed.')
    captured = {}
    for name in names:
        if name.startswith('progress/'):continue
        path = repository_sync.inside(root, name)
        if not path.is_file() or (name != MARKER and repository_sync.digest(path) != record.get('hashes', {}).get(name)):
            raise NetworkError('Generated build file changed before its first checkpoint: '+name+'. Review it before retrying.')
        captured[name] = path.read_bytes()
    return captured


def retired(root):
    marker = root/MARKER
    if not marker.is_file() or not pending(root):return set()
    names = json.loads(marker.read_text(encoding='utf-8')).get('retired', [])
    # Only paths formerly owned by a flat portable progress save can be retired.
    if any(not isinstance(name, str) or not public('progress/'+name) for name in names):
        raise NetworkError('Invalid retired progress file list. No files were committed.')
    return set(names)


def prepare(net, slug, path, root, log):
    """Build privately, then publish without replacing Git history or unrelated local files."""
    if full(root):return root/'progress'
    if root and path.resolve() != root.resolve():
        raise NetworkError('This folder already belongs to another repository. Choose its root or a separate destination.')
    proj = net.project_row(slug)
    cfg = net.cfg(proj)
    local_progress = (Path(proj['root'])/saves.SAVE_FOLDER).resolve()
    destination = root or (path if path.resolve() != local_progress and (cfg.get('save_folder') or cfg.get('progress_folder')) else Path(proj['root'])/'source')
    destination = destination.expanduser().resolve()
    if root and repository_sync.git(root, 'diff', '--cached', '--name-only').strip():
        raise NetworkError('Commit or unstage existing staged files before upgrading this repository.')
    if root:
        tracked = repository_sync.git(root, 'ls-files', '-z').decode().split('\0')
        private = [name for name in tracked if name and not public(name) and not public('progress/'+name)]
        if private:
            raise NetworkError('Untrack game/private files before upgrading this repository: '+', '.join(private[:8])+'. No files were changed.')
    # Read external contributions through the normal verifier before exporting them.
    net.progress.sync(slug, force=True, logger=log)
    if net.progress.errors.get(slug):raise NetworkError(net.progress.errors[slug])
    destination.parent.mkdir(parents=True, exist_ok=True)
    old = saves.split_files(saves._read_owned(destination)) if (destination/saves.MANIFEST).is_file() else {}
    if old and (destination/'decompdecomp-progress.svg').is_file():
        old['decompdecomp-progress.svg'] = (destination/'decompdecomp-progress.svg').read_text(encoding='utf-8')
    baseline = {name: repository_sync.digest(repository_sync.inside(destination, name)) for name in old}
    with tempfile.TemporaryDirectory(prefix='.decomp-repository-', dir=destination.parent) as temporary:
        stage = Path(temporary)/'tree'
        log('Generating the complete source tree and verifying its rebuild against your game…')
        # The project lock and explicit sync above cover this stage. Avoid implicit
        # saved-folder scans from project lookups while producing the staged tree.
        proj = net.project_row(slug)
        with net.progress.view():
            result = net.console(proj).export_project(net, slug, stage, log=log)
        if not result.get('identical'):
            raise NetworkError('The rebuildable export did not reproduce the original game bytes. No repository was created or upgraded. '+str(result.get('log', ''))[-1200:])
        getattr(log, 'check_cancel', lambda: None)()
        progress_step(log, 5, "Saving the verified repository")
        progress = stage/'progress'
        saved, _ = saves._render(net, slug, progress, chosen=True)
        if not saves._publish(progress, saved, chosen=True):
            raise NetworkError('Progress changed while preparing its source repository. Retry.')
        managed = {str(p.relative_to(stage)): repository_sync.digest(p) for p in (stage/'src').rglob('*.c')}
        (stage/repository_sync.STATE).write_text(json.dumps({'format':1, 'owned':managed, 'accepted':[], 'verified_build':True}, indent=1)+'\n', encoding='utf-8')
        readme = stage/'README.md'
        readme.write_text(readme.read_text(encoding='utf-8')+'\n## Open in decompdecomp\n\nOpen this repository with **New project → Open a saved project** and supply your own game copy. '
                          'The `progress/` folder retains matches, drafts, names, notes, headers and compiler settings. '
                          'Build files and sources live at the repository root; ROMs and extracted files are excluded by `.gitignore`.\n', encoding='utf-8')
        ignore_path = repository_sync.inside(destination, '.gitignore')
        previous_ignore = ignore_path.read_text(encoding='utf-8') if ignore_path.is_file() else ''
        from .repository_setup import IGNORE, IGNORE_BEFORE_SPLIT
        for block in (IGNORE, IGNORE_BEFORE_SPLIT):
            if previous_ignore.startswith(block):previous_ignore = previous_ignore[len(block):]
        exclusions = '\n'.join(line for line in previous_ignore.splitlines() if not line.startswith('!') and line not in ('/*', '/src/*', '/best/*', '/include/*'))
        (stage/'.gitignore').write_text(ignore()+exclusions+'\n', encoding='utf-8')
        for name in KEPT_DOCS:
            mine = destination/name
            if mine.is_file() and not mine.is_symlink():
                shutil.copyfile(mine, stage/name)
        names = sorted(str(p.relative_to(stage)) for p in stage.rglob('*') if p.is_file() and public(str(p.relative_to(stage))))
        (stage/MARKER).write_text(json.dumps({'format': 1, 'files': sorted(set(names)|{MARKER}), 'hashes': {name:repository_sync.digest(stage/name) for name in names if not name.startswith('progress/')}, 'retired': sorted(set(old)-set(names))}, indent=1)+'\n', encoding='utf-8')
        if any(p.is_symlink() for p in stage.rglob('*')):
            raise NetworkError('Generated repository contains symbolic links. No files were published.')
        # Verification leaves disposable objects and binaries in build/. They are not
        # repository inputs, and may differ from an earlier export's build cache.
        generated = {str(p.relative_to(stage)) for p in stage.rglob('*')
                     if p.is_file() and p.relative_to(stage).parts[0] != 'build'}
        # The export supplies its own build instructions and verified extraction.
        # Keep previous copies outside the Git allowlist and publish the backups
        # in the same transaction so cancellation restores the original layout.
        backup_id = uuid4().hex
        backups = set()
        backup_locations = set()
        for name in sorted(generated):
            target = repository_sync.inside(destination, name)
            previous = repository_sync.digest(target)
            if target.exists() and name not in old and name != '.gitignore' and previous != repository_sync.digest(stage/name):
                if name in BUILD_FILES:
                    backup_folder = '.decomp-repository-backups'
                elif Path(name).parts[0] in ('asm', 'assets'):
                    backup_folder = '.decomp-extraction-backups'
                else:
                    backup_folder = None
                if previous is None or backup_folder is None:
                    raise NetworkError('Repository generation would replace an existing file: '+name+'. Its contents were preserved.')
                location = Path(backup_folder)/backup_id
                backup = (location/name).as_posix()
                copy = stage/backup
                copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(target, copy)
                if repository_sync.digest(copy) != previous:
                    raise NetworkError('A file changed while saving its local copy: '+name+'. Retry.')
                backups.add(backup)
                backup_locations.add(location.as_posix())
            baseline.setdefault(name, previous)
        destination.mkdir(parents=True, exist_ok=True)
        target = destination/'progress'
        # Old flat progress paths move under progress/, while .git and every unrelated file remain. Move them and
        # point the project at the new place as one step: a sync between the two (the config update's own, or an
        # autosave) read the old folder without its manifest and failed with "Restore progress/decompdecomp.json"
        # for good. The batch defers every sync of this project, then syncs once, in the new folder.
        with net.progress.batch(slug, logger=log):
            repository_sync.publish(
                destination, stage, generated | backups | old.keys(), baseline, log,
                progress=lambda done, total: progress_step(log, 5, "Publishing repository files", done=done, total=total),
            )
            net._update_cfg(slug, lambda current: current.update(**moved_config(destination)), synchronize=False)
    for location in sorted(backup_locations):
        log(f'Previous files saved locally in {location}.')
    log('Full rebuildable repository prepared. Game images and extracted data remain ignored.')
    return target
