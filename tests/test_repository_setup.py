"""Git setup is local, scoped to the remembered save, and never stages game data."""
import shutil

import pytest

from decompnet.app import match_commits, repository_progress, repository_setup, saves
from decompnet.network import NetworkError

from . import test_progress

base_project = test_progress.project


@pytest.fixture(params=["n64", "ps1", "ps2", "psp"])
def project(base_project, request, monkeypatch):
    net, folder, judged = base_project
    net._update_cfg("game", lambda cfg: cfg.update(platform="ps" if request.param == "ps1" else request.param))
    net.progress.sync("game", force=True)
    chosen = folder.parent/'repository'
    saves.save(net, 'game', chosen)
    net._update_cfg('game', lambda cfg: cfg.update(progress_folder=str(chosen)))
    def export(net, slug, out, log):
        import json
        out.mkdir(parents=True)
        (out/'build.py').write_text('# verified test build\n')
        (out/'build.json').write_text(json.dumps({'platform':request.param, 'image':'baserom.bin'}))
        (out/'README.md').write_text('# Full source tree\n')
        (out/'src').mkdir()
        (out/'src/function.c').write_text('void function(void) {}\n')
        (out/'baserom.bin').write_bytes(b'PRIVATE ROM')
        (out/'assets').mkdir()
        (out/'assets/extracted.bin').write_bytes(b'PRIVATE DATA')
        return {'path':str(out), 'identical':True}
    monkeypatch.setattr(net.console(net.project_row('game')), 'export_project', export)
    return net, chosen, judged
from .test_repository_progress import git


def test_init_checkpoint_and_origin_allow_automatic_commits(project, tmp_path):
    net, folder, _ = project
    (folder/'private.rom').write_bytes(b'private game')
    (folder/'notes.txt').write_text('unrelated edits')
    (folder/'.gitignore').write_text('# my existing rules\n/src/private.c\n')
    assert not repository_setup.status(net, 'game')['repository']
    state = repository_setup.run(net, 'game', 'init', '', lambda line: None)
    assert state['repository'] and not state['checkpoint'] and not state['origin']
    assert git(folder, 'ls-files') == ''
    assert (folder/'.gitignore').read_text().endswith('# my existing rules\n/src/private.c\n')
    assert git(folder, 'check-ignore', 'private.rom') == 'private.rom'
    assert git(folder, 'check-ignore', 'src/private.c') == 'src/private.c'
    git(folder, 'config', 'user.name', 'Test')
    git(folder, 'config', 'user.email', 'test@example.invalid')
    state = repository_setup.run(net, 'game', 'checkpoint', '', lambda line: None)
    assert state['checkpoint']
    tracked = git(folder, 'ls-tree', '-r', '--name-only', 'HEAD').splitlines()
    assert 'progress/'+saves.MANIFEST in tracked and '.gitignore' in tracked
    assert {'build.py', 'build.json', 'src/function.c'} <= set(tracked)
    assert 'private.rom' not in tracked and 'notes.txt' not in tracked
    assert match_commits.configure(net, 'game', True, str(folder))['enabled']
    remote = tmp_path/'origin.git'
    git(tmp_path, 'init', '--bare', str(remote))
    state = repository_setup.run(net, 'game', 'origin', str(remote), lambda line: None)
    assert state['origin']
    assert git(folder, 'remote', 'get-url', 'origin') == str(remote)
    with pytest.raises(NetworkError, match='already configured'):
        repository_setup.run(net, 'game', 'origin', 'another', lambda line: None)


def test_staged_changes_and_links_are_preserved(project):
    net, folder, _ = project
    repository_setup.run(net, 'game', 'init', '', lambda line: None)
    git(folder, 'config', 'user.name', 'Test')
    git(folder, 'config', 'user.email', 'test@example.invalid')
    (folder/'src').mkdir(exist_ok=True)
    (folder/'src/personal.c').write_text('private')
    git(folder, 'add', 'src/personal.c')
    staged = git(folder, 'diff', '--cached', '--name-only')
    with pytest.raises(NetworkError, match='staged'):
        repository_setup.run(net, 'game', 'checkpoint', '', lambda line: None)
    assert git(folder, 'diff', '--cached', '--name-only') == staged


def test_parent_repository_is_detected_without_nested_init(project):
    net, folder, _ = project
    git(folder.parent, 'init')
    assert repository_setup.status(net, 'game')['root'] == str(folder.parent)
    with pytest.raises(NetworkError, match='already belongs'):
        repository_setup.run(net, 'game', 'init', '', lambda line: None)
    assert not (folder/'.git').exists()


def test_remembered_save_can_become_live_progress(project, tmp_path):
    net, folder, _ = project
    target = tmp_path/'backup'
    saves.save(net, 'game', target)
    state = repository_setup.run(net, 'game', 'init', '', lambda line: None)
    assert state['linked'] and saves.default_folder(net, 'game') == target/'progress'
    assert folder.exists()
    assert (target/'.git').is_dir()
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='new draft',best_score=10 WHERE name='f'")
    net.progress.sync('game', force=True)
    assert (target/'progress/best/f.c').read_text() == 'new draft\n'
    assert not (folder/'best/f.c').exists()


def test_mismatched_save_is_not_adopted(project, tmp_path):
    net, folder, _ = project
    target = tmp_path/'backup'
    shutil.copytree(folder, target)
    net._update_cfg('game', lambda cfg: cfg.update(save_folder=str(target)))
    (target/'include/common.h').write_text('changed header')
    with pytest.raises(NetworkError, match='differs'):
        repository_setup.run(net, 'game', 'init', '', lambda line: None)
    assert saves.default_folder(net, 'game') == folder
    assert not (target/'.git').exists()


def test_origin_on_imported_project_uses_its_source_checkout(project, tmp_path):
    net, folder, _ = project
    root = tmp_path/'source'
    root.mkdir()
    git(root, 'init')
    with net.store.tx() as db:
        db.execute("UPDATE projects SET root=? WHERE slug='game'", (str(root),))
    net._update_cfg('game', lambda cfg: cfg.update(kind='fork', save_folder=str(tmp_path/'backup')))
    state = repository_setup.status(net, 'game')
    assert not state['portable'] and state['root'] == str(root)
    assert state['folder'] == str(root) and state['save_folder'] == str(tmp_path/'backup')
    assert repository_setup.run(net, 'game', 'origin', 'git@example.invalid:project.git', lambda line: None)['origin']
    assert git(root, 'remote', 'get-url', 'origin') == 'git@example.invalid:project.git'


def test_first_checkpoint_rejects_a_concurrent_initial_commit(project):
    net, folder, _ = project
    repository_setup.run(net, 'game', 'init', '', lambda line: None)
    git(folder, 'config', 'user.name', 'Test')
    git(folder, 'config', 'user.email', 'test@example.invalid')
    git(folder, 'add', '.gitignore')
    git(folder, 'commit', '-qm', 'Other Git operation')
    head = git(folder, 'rev-parse', 'HEAD')
    with pytest.raises(NetworkError, match='committed new work'):
        repository_progress.commit_snapshot(folder, {}, set(), lambda line: None, expected_head=None)
    assert git(folder, 'rev-parse', 'HEAD') == head


def test_select_build_root_saves_progress_and_enables_existing_repository(project, tmp_path):
    from decompnet.app import repository_sync

    net, old, _ = project
    root = tmp_path / 'build-checkout'
    root.mkdir()
    git(root, 'init', '--initial-branch=main')
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'user.email', 'test@example.invalid')
    (root / 'build.json').write_text('{}')
    (root / 'build.py').write_text('# build tools')
    (root / 'src').mkdir()
    (root / 'src' / 'handwritten.c').write_text('int preserved;')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'build checkout')
    (root / 'src' / 'handwritten.c').write_text('int newer_hand_written;')
    result = saves.save(net, 'game', root)
    assert result['folder'] == str(root / 'progress')
    assert (root / 'progress' / saves.MANIFEST).is_file()
    assert not (root / saves.MANIFEST).exists()
    assert (root / 'src' / 'handwritten.c').read_text() == 'int newer_hand_written;'
    state = repository_setup.status(net, 'game')
    assert state['root'] == str(root) and state['build_folder'] == str(root)
    assert not state['linked']
    repository_setup.run(net, 'game', 'checkpoint', '', lambda _: None)
    assert saves.default_folder(net, 'game') == root / 'progress'
    assert match_commits.configure(net, 'game', True, str(root))['enabled']
    assert not repository_sync.progress_checkout(net, 'game', root), 'build sync must still use the build route'
    assert repository_progress.linked_checkout(net, 'game', root)
    assert git(root, 'show', 'HEAD:src/handwritten.c') == 'int preserved;'
    assert 'progress/decompdecomp.json' in git(root, 'ls-files')
    assert not (root / 'progress' / '.git').exists()
    assert (root / 'src' / 'handwritten.c').read_text() == 'int newer_hand_written;'
    assert 'src/handwritten.c' in git(root, 'diff', '--name-only')


def test_detected_progress_does_not_follow_a_child_link(project, tmp_path):
    net, _, _ = project
    root = tmp_path / 'build-checkout'
    root.mkdir()
    (root / 'build.json').write_text('{}')
    (root / 'build.py').write_text('# build')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'progress').symlink_to(outside, target_is_directory=True)
    with pytest.raises(NetworkError, match='symbolic link'):
        saves.save(net, 'game', root)
    assert list(outside.iterdir()) == []


def test_detected_progress_rejects_a_different_game(project, tmp_path):
    import json

    net, _, _ = project
    root = tmp_path / 'build-checkout'
    root.mkdir()
    (root / 'build.json').write_text('{}')
    (root / 'build.py').write_text('# build')
    (root / 'progress').mkdir()
    manifest = root / 'progress' / saves.MANIFEST
    text = json.dumps({'game': {'sha1': 'another-game'}})
    manifest.write_text(text)
    with pytest.raises(NetworkError, match='another game'):
        saves.save(net, 'game', root)
    assert manifest.read_text() == text


def test_remembered_separate_build_checkout_stays_available(project, tmp_path):
    net, _, _ = project
    root = tmp_path / 'separate-build'
    root.mkdir()
    (root / 'build.json').write_text('{}')
    (root / 'build.py').write_text('# build')
    net._update_cfg('game', lambda cfg: cfg.update(repository_sync_folder=str(root)))
    assert repository_setup.status(net, 'game')['build_folder'] == str(root)


@pytest.mark.parametrize('linked', [False, True])
def test_detected_progress_rejects_bad_manifest_without_replacing_it(project, tmp_path, linked):
    net, _, _ = project
    root = tmp_path / 'build-checkout'
    root.mkdir()
    (root / 'build.json').write_text('{}')
    (root / 'build.py').write_text('# build')
    (root / 'progress').mkdir()
    manifest = root / 'progress' / saves.MANIFEST
    outside = tmp_path / 'outside.json'
    outside.write_text('broken JSON')
    if linked:manifest.symlink_to(outside)
    else:manifest.write_text('broken JSON')
    with pytest.raises(NetworkError, match='manifest'):
        saves.save(net, 'game', root)
    assert outside.read_text() == 'broken JSON'
    assert manifest.read_text() == 'broken JSON'


def test_github_creation_is_scoped_and_does_not_repeat(project, monkeypatch):
    from decompnet.app import repository_github
    net, folder, _ = project
    repository_setup.run(net, 'game', 'init', '', print)
    calls = []
    def create(root, name, visibility, log):
        calls.append((root, name, visibility))
        git(root, 'remote', 'add', 'origin', 'https://github.com/person/progress.git')
    monkeypatch.setattr(repository_github, 'create', create)
    state = repository_setup.run(net, 'game', 'github', '', print, github_name='person/progress')
    assert state['origin']
    assert calls == [(folder, 'person/progress', 'private')]
    with pytest.raises(NetworkError, match='already configured'):
        repository_setup.run(net, 'game', 'github', '', print, github_name='person/another')
    assert len(calls) == 1


@pytest.mark.parametrize('kind', ['matched', 'best'])
@pytest.mark.parametrize('newline_in_backup', [True, False])
def test_checkpoint_accepts_save_writer_final_newline(project, tmp_path, monkeypatch, kind, newline_in_backup):
    net, folder, _ = project
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status=?,matched_source=?,best_source=?,best_score=? WHERE name='f'",
                   ('matched' if kind == 'matched' else 'open', 'good source' if kind == 'matched' else None,
                    'good source', 0 if kind == 'matched' else 10))
    net.progress.sync('game', force=True)
    target = tmp_path/'backup'
    saves.save(net, 'game', target)
    relative = ('src' if kind == 'matched' else 'best')+'/f.c'
    # Isolate precisely the two exporter representations; all other bytes agree.
    (folder/relative).write_text('good source'+('' if newline_in_backup else '\n'))
    (target/relative).write_text('good source'+('\n' if newline_in_backup else ''))
    monkeypatch.setattr(net.progress, 'sync', lambda *args, **kwargs:None)
    git(target, 'init', '--initial-branch=main')
    git(target, 'config', 'user.name', 'Test')
    git(target, 'config', 'user.email', 'test@example.invalid')
    state = repository_setup.run(net, 'game', 'checkpoint', '', lambda line:None)
    assert state['checkpoint'] and state['linked']
    assert saves.default_folder(net, 'game') == target/'progress'
    assert git(target, 'show', 'HEAD:progress/'+relative).strip() == 'good source'



@pytest.mark.parametrize('changed', ['changed source\n', '\ngood source\n', 'good\nsource\n'])
def test_newline_comparison_still_rejects_source_or_line_changes(project, tmp_path, monkeypatch, changed):
    net, folder, _ = project
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='good source',best_score=10 WHERE name='f'")
    net.progress.sync('game', force=True)
    target = tmp_path/'backup'
    saves.save(net, 'game', target)
    (target/'best/f.c').write_text(changed)
    monkeypatch.setattr(net.progress, 'sync', lambda *args, **kwargs:None)
    with pytest.raises(NetworkError, match='differs'):
        repository_setup.run(net, 'game', 'init', '', lambda line:None)
    assert saves.default_folder(net, 'game') == folder
    assert not (target/'.git').exists()


def test_checkpoint_refreshes_untouched_app_backup_after_new_progress(project,tmp_path):
    net,folder,_=project
    target=tmp_path/'backup'
    saves.save(net,'game',target)
    git(target,'init','--initial-branch=main')
    git(target,'config','user.name','Test');git(target,'config','user.email','test@example.invalid')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='new agent draft',best_score=10 WHERE name='f'")
    messages=[]
    state=repository_setup.run(net,'game','checkpoint','',messages.append)
    assert state['checkpoint'] and state['linked']
    assert git(target,'show','HEAD:progress/best/f.c')=='new agent draft'
    assert any('outdated' in message for message in messages)


def test_checkpoint_lists_external_edits_and_preserves_them(project,tmp_path):
    net,folder,_=project
    target=tmp_path/'backup'
    saves.save(net,'game',target)
    git(target,'init','--initial-branch=main')
    (target/'include/common.h').write_text('external contribution')
    with pytest.raises(NetworkError,match=r'Files that differ:.*include/common.h'):
        repository_setup.run(net,'game','checkpoint','',lambda line:None)
    assert (target/'include/common.h').read_text()=='external contribution'
    assert saves.default_folder(net,'game')==folder
    assert not repository_setup.git(target,'rev-parse','--verify','--quiet','HEAD',missing=True)


def test_backup_refresh_detects_edits_during_capture(project,tmp_path,monkeypatch):
    net,folder,_=project
    target=tmp_path/'backup'
    saves.save(net,'game',target)
    git(target,'init','--initial-branch=main')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='new draft',best_score=10 WHERE name='f'")
    publish=saves._publish
    def concurrent(path,*args,**kwargs):
        if path==target:(target/'include/common.h').write_text('concurrent edit')
        return publish(path,*args,**kwargs)
    monkeypatch.setattr(saves,'_publish',concurrent)
    with pytest.raises(NetworkError,match='changed while preparing'):
        repository_setup.run(net,'game','checkpoint','',lambda line:None)
    assert (target/'include/common.h').read_text()=='concurrent edit'
    assert saves.default_folder(net,'game')==folder


@pytest.mark.parametrize('external', [False, True, 'uncompiled'])
def test_unreceipted_old_attempt_refreshes_but_unknown_edits_survive(project, tmp_path, external):
    net, folder, _ = project
    agent, _ = net.local_agent('helper')
    with net.store.tx() as db:
        fid = db.execute("SELECT id FROM functions WHERE name='f'").fetchone()['id']
        db.execute("UPDATE functions SET best_source='old draft',best_score=30 WHERE id=?", (fid,))
        db.execute("INSERT INTO attempts(function_id,agent_id,score,compiled,context_version,source,created_at) "
                   "VALUES(?,?,30,1,1,'old draft',1)", (fid, agent.id))
        if external == 'uncompiled':db.execute('UPDATE attempts SET compiled=0 WHERE function_id=?', (fid,))
    net.progress.sync('game', force=True)
    target = tmp_path/'backup'
    # Reproduce a Git/manual copy, which has no app Save now receipt.
    shutil.copytree(folder, target)
    net._update_cfg('game', lambda cfg: cfg.update(save_folder=str(target)))
    git(target, 'init', '--initial-branch=main')
    git(target, 'config', 'user.name', 'Test')
    git(target, 'config', 'user.email', 'test@example.invalid')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='new draft',best_score=10 WHERE id=?", (fid,))
    if external:
        if external is True:(target/'best/f.c').write_text('unknown contributor draft\n')
        with pytest.raises(NetworkError, match='Files that differ'):
            repository_setup.run(net, 'game', 'checkpoint', '', lambda line:None)
        assert (target/'best/f.c').read_text() == ('unknown contributor draft\n' if external is True else 'old draft\n')
        assert saves.default_folder(net, 'game') == folder
    else:
        state = repository_setup.run(net, 'game', 'checkpoint', '', lambda line:None)
        assert state['checkpoint'] and state['linked']
        assert git(target, 'show', 'HEAD:progress/best/f.c') == 'new draft'
        assert match_commits.configure(net, 'game', True, str(target))['enabled']


def test_upgrade_keeps_history_and_origin_and_ignores_all_game_data(project, tmp_path):
    net, root, _ = project
    git(root, 'init', '--initial-branch=main')
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'user.email', 'test@example.invalid')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'existing portable history')
    old = git(root, 'rev-parse', 'HEAD')
    git(root, 'remote', 'add', 'origin', str(tmp_path/'remote.git'))
    state = repository_setup.run(net, 'game', 'init', '', lambda _:None)
    assert state['rebuildable'] and git(root, 'rev-parse', 'HEAD') == old
    assert git(root, 'remote', 'get-url', 'origin') == str(tmp_path/'remote.git')
    repository_setup.run(net, 'game', 'checkpoint', '', lambda _:None)
    assert git(root, 'rev-parse', 'HEAD^') == old
    tracked = git(root, 'ls-files').splitlines()
    assert 'decompdecomp.json' not in tracked and 'progress/decompdecomp.json' in tracked
    for name in ('baserom.bin', 'assets/extracted.bin', 'odd-game.unknown', 'asm/raw.s', 'build/output.bin'):
        assert git(root, 'check-ignore', '--no-index', name) == name
    (root/'src/function.c').write_text('a manual source edit\n')
    repository_setup.run(net, 'game', 'checkpoint', '', lambda _:None)
    assert git(root, 'show', 'HEAD:src/function.c') == 'void function(void) {}'
    assert (root/'src/function.c').read_text() == 'a manual source edit\n'


def test_failed_full_rebuild_never_falls_back_to_a_progress_repository(project, monkeypatch):
    net, root, _ = project
    original = {p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    cfg = net.cfg(net.project_row('game'))
    monkeypatch.setattr(net.console(net.project_row('game')), 'export_project', lambda *a, **kw:{'identical':False})
    with pytest.raises(NetworkError, match='did not reproduce'):
        repository_setup.run(net, 'game', 'init', '', lambda _:None)
    assert not (root/'.git').exists()
    assert all((root/name).read_bytes() == data for name, data in original.items())
    assert net.cfg(net.project_row('game')).get('progress_folder') == cfg.get('progress_folder')



def test_export_does_not_regenerate_the_linked_source_repository(project):
    from pathlib import Path
    net, root, _ = project
    # Use the normal default repository location for this regression.
    net._update_cfg('game', lambda cfg: cfg.update(save_folder='', progress_folder=''))
    net.progress.sync('game', force=True)
    state = repository_setup.run(net, 'game', 'init', '', lambda _:None)
    source = Path(state['root'])
    assert source.name == 'source'
    saved = (source/'progress/decompdecomp.json').read_bytes()
    (source/'src/manual.c').write_text('curated source')
    result = net.export_rom('game', lambda _:None)
    assert Path(result['path']).name == 'source-export'
    assert (source/'progress/decompdecomp.json').read_bytes() == saved
    assert (source/'src/manual.c').read_text() == 'curated source'
    assert (source/'.git').exists()
    (Path(result['path'])/'.git').mkdir()
    with pytest.raises(NetworkError, match='Update build files'):
        net.export_rom('game', lambda _:None)


def test_commit_waits_for_a_busy_repository_instead_of_failing(project):
    """A click on Commit saved progress while an automatic commit or push holds the repository queues behind it."""
    from concurrent.futures import ThreadPoolExecutor

    from decompnet.app import repository_operations

    from .test_repository_push import QueueLog
    net, folder, _ = project
    repository_setup.run(net, 'game', 'init', '', lambda line: None)
    git(folder, 'config', 'user.name', 'Test')
    git(folder, 'config', 'user.email', 'test@example.invalid')
    log = QueueLog()
    with ThreadPoolExecutor(1) as pool:
        with repository_operations.operation(folder, 'game', 'Committing a verified match', lambda _: None):
            future = pool.submit(repository_setup.run, net, 'game', 'checkpoint', '', log)
            assert log.queued.wait(5)
            assert not future.done()
        assert future.result(timeout=60)['checkpoint']
    assert any('Committing a verified match' in update['phase'] for update in log.updates if update.get('queued'))


def test_queued_commit_can_be_stopped(project):
    from concurrent.futures import ThreadPoolExecutor

    from decompnet.app import repository_operations

    from .test_repository_push import QueueLog
    net, folder, _ = project
    repository_setup.run(net, 'game', 'init', '', lambda line: None)
    log = QueueLog()
    with ThreadPoolExecutor(1) as pool:
        with repository_operations.operation(folder, 'game', 'Committing a verified match', lambda _: None):
            future = pool.submit(repository_setup.run, net, 'game', 'checkpoint', '', log)
            assert log.queued.wait(5)
            log.cancel.set()
            with pytest.raises(RuntimeError, match='cancelled'):
                future.result(timeout=5)
    assert not repository_operations._STATES
