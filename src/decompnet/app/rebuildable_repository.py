"""Verified source repositories with portable app progress; game-derived files stay local."""
import json
import tempfile
from pathlib import Path

from ..network import NetworkError
from . import repository_sync, saves

MARKER = 'decompdecomp-repository.json'
ROOT_FILES = frozenset({'build.py', 'build.json', 'splat.yaml', 'symbol_addrs.txt', 'reloc_addrs.txt',
                        'objdiff.json', 'functions.cfg', 'README.md', 'AGENTS.md', 'requirements.txt',
                        'decompdecomp-sync.json', MARKER, '.gitignore'})


def full(root):
    return bool(root and (root/'build.json').is_file() and (root/'build.py').is_file()
                and (root/'progress'/saves.MANIFEST).is_file())


def public(name):
    path = Path(name)
    if path.is_absolute() or '..' in path.parts:return False
    if len(path.parts) == 1:return name in ROOT_FILES
    if path.parts[0] == 'src':return path.suffix in ('.c', '.cpp', '.h')
    if path.parts[0] == 'include':return path.suffix in ('.h', '.inc')
    if path.parts[0] == 'progress':
        rest = path.relative_to('progress')
        # data.json and data/: the game's data in C (app/data_saves.py)
        return (str(rest) in (saves.MANIFEST, 'README.md', 'decompdecomp-progress.svg', 'data.json') or
                len(rest.parts) == 2 and rest.parts[0] in ('src', 'best', 'include', 'data')
                and rest.suffix == ('.h' if rest.parts[0] == 'include' else '.c'))
    return False


def ignore():
    # An allowlist protects unrecognized ROM extensions and arbitrary extraction folders too.
    root = '\n'.join('!/'+name for name in sorted(ROOT_FILES) if name != '.gitignore')
    return ('# Full decompilation repository: game images and extracted data stay local.\n/*\n!/.gitignore\n'+root+
            '\n!/src/\n/src/**\n!/src/**/\n!/src/**/*.c\n!/src/**/*.cpp\n!/src/**/*.h\n'
            '!/include/\n/include/**\n!/include/**/\n!/include/**/*.h\n!/include/**/*.inc\n'
            '!/progress/\n/progress/*\n!/progress/decompdecomp.json\n!/progress/README.md\n'
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
    old = saves._read_owned(destination) if (destination/saves.MANIFEST).is_file() else {}
    if old:
        for name in ('README.md', 'decompdecomp-progress.svg'):
            if (destination/name).is_file():old[name] = (destination/name).read_text(encoding='utf-8')
    baseline = {name: repository_sync.digest(repository_sync.inside(destination, name)) for name in old}
    with tempfile.TemporaryDirectory(prefix='.decomp-repository-', dir=destination.parent) as temporary:
        stage = Path(temporary)/'tree'
        log('Generating the complete source tree and verifying its rebuild against your game…')
        result = net.console(proj).export_project(net, slug, stage, log=log)
        if not result.get('identical'):
            raise NetworkError('The rebuildable export did not reproduce the original game bytes. No repository was created or upgraded. '+str(result.get('log', ''))[-1200:])
        getattr(log, 'check_cancel', lambda: None)()
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
        from .repository_setup import IGNORE
        if previous_ignore.startswith(IGNORE):previous_ignore = previous_ignore[len(IGNORE):]
        exclusions = '\n'.join(line for line in previous_ignore.splitlines() if not line.startswith('!') and line not in ('/*', '/src/*', '/best/*', '/include/*'))
        (stage/'.gitignore').write_text(ignore()+exclusions+'\n', encoding='utf-8')
        names = sorted(str(p.relative_to(stage)) for p in stage.rglob('*') if p.is_file() and public(str(p.relative_to(stage))))
        (stage/MARKER).write_text(json.dumps({'format': 1, 'files': sorted(set(names)|{MARKER}), 'hashes': {name:repository_sync.digest(stage/name) for name in names if not name.startswith('progress/')}, 'retired': sorted(set(old)-set(names))}, indent=1)+'\n', encoding='utf-8')
        if any(p.is_symlink() for p in stage.rglob('*')):
            raise NetworkError('Generated repository contains symbolic links. No files were published.')
        generated = {str(p.relative_to(stage)) for p in stage.rglob('*') if p.is_file()}
        # Refuse collisions with files not owned by the old portable save.
        for name in generated:
            target = repository_sync.inside(destination, name)
            if target.exists() and name not in old and name != '.gitignore' and repository_sync.digest(target) != repository_sync.digest(stage/name):
                raise NetworkError('Repository generation would replace an existing file: '+name+'. Its contents were preserved.')
            baseline.setdefault(name, repository_sync.digest(target))
        destination.mkdir(parents=True, exist_ok=True)
        # Old flat progress paths move under progress/, while .git and every unrelated file remain.
        repository_sync.publish(destination, stage, generated | old.keys(), baseline, log)
    target = destination/'progress'
    net._update_cfg(slug, lambda current: current.update(progress_folder=str(target), save_folder=str(target),
                                                       repository_sync_folder=str(destination)))
    log('Full rebuildable repository prepared. Game images and extracted data remain ignored.')
    return target
