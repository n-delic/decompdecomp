"""Autosaved progress commits use the ordinary progress engine and a local bare Git remote."""
import json
import shutil
import subprocess

import pytest

from decompnet.app import repository_git, repository_progress, repository_sync, saves
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args]).decode().strip()


@pytest.fixture(params=['n64', 'ps1', 'ps2', 'psp'])
def progress(tmp_path, request):
    if not shutil.which('git'):pytest.skip('needs git')
    remote = tmp_path/'remote.git'
    subprocess.run(['git', 'init', '--bare', '-q', str(remote)], check=True)
    root = tmp_path/'checkout'
    subprocess.run(['git', 'clone', '-q', str(remote), str(root)], check=True)
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'user.email', 'test@example.invalid')
    net = RomNetwork(tmp_path/'app.db', tmp_path)
    cfg = {'kind': 'rom', 'platform': request.param, 'rom': {'sha1': 'homemade'},
           'instructions': 'Keep these notes'}
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                         ('game', 'Homemade game', str(tmp_path/'game'), json.dumps(cfg))).lastrowid
        db.execute("INSERT INTO context_versions(project_id,version,header,created_at,unit) VALUES(?,1,'typedef int s32;',0,'')", (pid,))
        db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit,best_source,best_score) "
                   "VALUES(?,'f',4096,4,1,'open','','void f(void) {}',10)", (pid,))
    net.progress.sync('game', force=True)
    shutil.copytree(saves.default_folder(net, 'game'), root, dirs_exist_ok=True)
    net._update_cfg('game', lambda cfg: cfg.update(progress_folder=str(root)))
    net.progress.sync('game', force=True)
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'initial progress')
    git(root, 'push', '-u', 'origin', 'HEAD')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source='void f(void) { return; }',best_score=0 WHERE project_id=?", (pid,))
    net.progress.sync('game', force=True)
    return net, root, remote


def test_autosaved_matches_promotions_and_metadata_can_be_committed(progress):
    net, root, remote = progress
    assert not (root/'best/f.c').exists() and (root/'src/f.c').exists()
    result = repository_sync.run(net, 'game', root, commit_push=True)
    assert result['pushed'] and result['mode'] == 'progress'
    assert result['matched'] == 1 and result['best'] == 0
    assert git(remote, 'rev-parse', 'HEAD') == result['commit']
    assert git(root, 'status', '--porcelain') == ''
    assert json.loads(git(root, 'show', 'HEAD:decompdecomp.json'))['instructions'] == 'Keep these notes'
    assert 'best/f.c' not in git(root, 'ls-files')
    repeated = repository_sync.run(net, 'game', root, commit_push=True)
    assert repeated['commit'] == result['commit']


def test_review_progress_needs_no_upstream_or_build(progress):
    net, root, _ = progress
    git(root, 'branch', '--unset-upstream')
    before = git(root, 'rev-parse', 'HEAD')
    assert repository_sync.status(net, 'game')['mode'] == 'progress'
    assert repository_sync.run(net, 'game', root)['matched'] == 1
    assert git(root, 'rev-parse', 'HEAD') == before


def test_unrelated_and_staged_edits_are_protected(progress):
    net, root, _ = progress
    (root/'private.rom').write_bytes(b'private homemade input')
    with pytest.raises(NetworkError, match='existing changes'):
        repository_sync.run(net, 'game', root, commit_push=True)
    assert git(root, 'log', '-1', '--format=%s') == 'initial progress'
    (root/'private.rom').unlink()
    git(root, 'add', 'README.md')
    before = git(root, 'diff', '--cached')
    with pytest.raises(NetworkError, match='staged changes'):
        repository_sync.run(net, 'game', root, commit_push=True)
    assert git(root, 'diff', '--cached') == before


def test_helper_writes_after_capture_remain_uncommitted(progress, monkeypatch):
    net, root, _ = progress
    original = repository_progress.commit_snapshot
    def advance(root, files, paths, log, **kwargs):
        (root/'src/f.c').write_text('new helper work\n')
        return original(root, files, paths, log, **kwargs)
    monkeypatch.setattr(repository_progress, 'commit_snapshot', advance)
    result = repository_sync.run(net, 'game', root, commit_push=True)
    assert result['pushed']
    assert git(root, 'show', 'HEAD:src/f.c') == 'void f(void) { return; }'
    assert (root/'src/f.c').read_text() == 'new helper work\n'
    assert git(root, 'status', '--porcelain') == 'M src/f.c'


def test_an_editor_change_during_capture_is_not_committed(progress, monkeypatch):
    net, root, _ = progress
    original = saves._read_owned
    def edited(folder):
        (root/'src/f.c').write_text('unjudged editor change\n')
        return original(folder)
    monkeypatch.setattr(saves, '_read_owned', edited)
    with pytest.raises(NetworkError, match='changed while capturing'):
        repository_sync.run(net, 'game', root, commit_push=True)
    assert git(root, 'log', '-1', '--format=%s') == 'initial progress'


def test_index_lock_and_branch_change_preserve_checkpoint(progress, monkeypatch):
    _, root, _ = progress
    files = {'src/f.c': b'captured\n'}
    lock = root/'.git/index.lock'
    lock.write_text('another Git process')
    with pytest.raises(NetworkError, match='Another Git operation'):
        repository_progress.commit_snapshot(root, files, set(files), print)
    assert lock.read_text() == 'another Git process'
    lock.unlink()
    original = repository_git.git
    def staged(root, *args, **kwargs):
        if args[0] == 'write-tree':
            git(root, 'add', 'src/f.c')
        return original(root, *args, **kwargs)
    monkeypatch.setattr(repository_git, 'git', staged)
    with pytest.raises(NetworkError, match='staged files changed'):
        repository_progress.commit_snapshot(root, files, set(files), print)
    assert git(root, 'log', '-1', '--format=%s') == 'initial progress'
    assert git(root, 'diff', '--cached', '--name-only') == 'src/f.c'
    assert not lock.exists()


def test_nested_progress_excludes_other_repository_files(progress):
    net, root, _ = progress
    repository_sync.run(net, 'game', root, commit_push=True)
    folder = root/'progress'
    folder.mkdir()
    for name in ('README.md', 'decompdecomp.json', 'decompdecomp-progress.svg', 'src', 'best', 'include'):
        if (root/name).exists():shutil.move(root/name, folder/name)
    (root/'README.md').write_text('Repository guide\n')
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'nest progress')
    cfg = json.loads(net.store.one("SELECT config FROM projects WHERE slug='game'")['config'])
    cfg['progress_folder'] = str(folder)
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=?,name='Renamed homemade project' WHERE slug='game'", (json.dumps(cfg),))
    result = repository_sync.run(net, 'game', root, commit_push=True)
    assert result['pushed']
    assert git(root, 'show', 'HEAD:README.md') == 'Repository guide'
    assert json.loads(git(root, 'show', 'HEAD:progress/decompdecomp.json'))['title'] == 'Renamed homemade project'


def test_failed_progress_push_can_be_retried(progress):
    net, root, remote = progress
    hook = remote/'hooks/pre-receive'
    hook.write_text('#!/bin/sh\nexit 1\n')
    hook.chmod(0o755)
    failed = repository_sync.run(net, 'game', root, commit_push=True)
    assert not failed['pushed'] and failed['push_error']
    assert git(root, 'rev-parse', 'HEAD') == failed['commit']
    hook.unlink()
    retried = repository_sync.run(net, 'game', root, commit_push=True)
    assert retried['pushed'] and retried['commit'] == failed['commit']


def test_symbolic_link_and_conflicts_are_not_committed(progress, tmp_path):
    net, root, _ = progress
    outside = tmp_path/'outside'
    outside.write_text('private content')
    path = root/'src/f.c'
    path.unlink()
    try:path.symlink_to(outside)
    except OSError:pytest.skip('needs symlinks')
    with pytest.raises(NetworkError, match='symbolic'):
        repository_sync.run(net, 'game', root, commit_push=True)
    assert outside.read_text() == 'private content'
    path.unlink()
    path.write_text('<<<<<<< HEAD\nconflict\n=======\nother\n>>>>>>> branch\n')
    with pytest.raises(NetworkError, match='conflicts'):
        repository_sync.run(net, 'game', root, commit_push=True)
    assert git(root, 'log', '-1', '--format=%s') == 'initial progress'


def test_snapshot_preserves_line_endings_and_rejects_a_new_head(progress):
    _, root, _ = progress
    head = git(root, 'rev-parse', 'HEAD')
    payload = b'void f(void) { return; }\r\n'
    commit = repository_progress.commit_snapshot(root, {'src/f.c': payload}, {'src/f.c'}, print, expected_head=head)
    assert subprocess.check_output(['git', '-C', str(root), 'show', 'HEAD:src/f.c']) == payload
    assert commit != head
    with pytest.raises(NetworkError, match='committed new work'):
        repository_progress.commit_snapshot(root, {'src/f.c': b'old'}, {'src/f.c'}, print, expected_head=head)
    assert git(root, 'rev-parse', 'HEAD') == commit


def test_progress_commit_recovers_missing_upstream(progress):
    net,root,remote=progress
    git(root,'branch','--unset-upstream')
    branch=git(root,'branch','--show-current')
    result=repository_sync.run(net,'game',root,commit_push=True)
    assert result['pushed'] and result['matched']==1
    assert git(remote,'rev-parse','refs/heads/'+branch)==result['commit']
    assert git(root,'rev-parse','--abbrev-ref','@{upstream}')=='origin/'+branch
    assert git(root,'status','--porcelain')==''


def test_commit_recovers_acknowledged_missing_sources_from_database(progress,monkeypatch):
    net,root,_=progress
    source=(root/'src/f.c').read_text()
    (root/'src/f.c').unlink()
    # An older autosave already acknowledged the incomplete snapshot, so sync considers it current.
    monkeypatch.setattr(net.progress,'sync',lambda *args,**kwargs:False)
    lines=[]
    files,_,_=repository_progress.snapshot(net,'game',root,lines.append)
    assert (root/'src/f.c').read_text()==source
    assert files['src/f.c']==source.encode()
    assert any('Recovered missing saved source src/f.c' in line for line in lines)


def test_commit_lists_unrecoverable_missing_sources(progress,monkeypatch):
    net,root,_=progress
    (root/'src/f.c').unlink()
    monkeypatch.setattr(net.progress,'sync',lambda *args,**kwargs:False)
    with net.store.tx() as db:db.execute("UPDATE functions SET matched_source=NULL WHERE name='f'")
    with pytest.raises(NetworkError,match=r'1 saved progress file.*src/f.c'):
        repository_progress.snapshot(net,'game',root,print)
    assert not (root/'src/f.c').exists()


def test_commit_does_not_restore_a_deletion_processed_by_progress(progress):
    net,root,_=progress
    (root/'src/f.c').unlink()
    with pytest.raises(NetworkError,match='src/f.c'):
        repository_progress.snapshot(net,'game',root,print)
    assert not (root/'src/f.c').exists()
    assert net._function('f','game')['status'] != 'matched'


def test_missing_source_recovery_never_overwrites_a_concurrent_edit(progress):
    net,root,_=progress
    path=root/'src/f.c'
    path.write_text('contributor edit')
    manifest=json.loads((root/saves.MANIFEST).read_text())
    with pytest.raises(NetworkError,match='Progress changed while recovering'):
        repository_progress.recover_missing_sources(net,'game',root,manifest,{'src/f.c'},print)
    assert path.read_text()=='contributor edit'


def test_capture_does_not_resynchronize_through_project_reads(progress, monkeypatch):
    net, root, _ = progress
    original_read = saves._read_owned
    original_project = net.project_row
    reading = False
    def read(folder):
        nonlocal reading
        files = original_read(folder)
        reading = True
        return files
    def project_row(slug):
        if reading:
            raise AssertionError('A project lookup must not synchronize after reading the snapshot')
        return original_project(slug)
    monkeypatch.setattr(saves, '_read_owned', read)
    monkeypatch.setattr(net, 'project_row', project_row)
    files, _, manifest = repository_progress.snapshot(net, 'game', root, print)
    assert files['src/f.c'] == (root/'src/f.c').read_bytes()
    assert 'matched_by' in manifest['functions']['f']


def test_missing_source_recovery_does_not_resynchronize_after_capture(progress, monkeypatch):
    net, root, _ = progress
    source = (root/'src/f.c').read_bytes()
    (root/'src/f.c').unlink()
    # Keep the old incomplete save acknowledged, as in the historical partial-save bug.
    calls = []
    monkeypatch.setattr(net.progress, 'sync', lambda *args, **kwargs: calls.append(kwargs.get('force')))
    def unexpected_project_read(slug):
        raise AssertionError('Recovery must not start an implicit saved-file synchronization')
    monkeypatch.setattr(net, 'project_row', unexpected_project_read)
    files, _, _ = repository_progress.snapshot(net, 'game', root, print)
    assert files['src/f.c'] == source
    assert calls == [True, True]


@pytest.mark.parametrize('matched', [True, False])
def test_recovered_source_is_rejudged_before_the_snapshot_is_captured(progress, monkeypatch, matched):
    net, root, _ = progress
    proj = net.store.one("SELECT * FROM projects WHERE slug='game'")
    source = (root/'src/f.c').read_text()
    (root/'src/f.c').unlink()
    # Reproduce an old partial save whose baseline recorded the manifest but not its source.
    baseline = net.progress._baseline(proj['id'])
    baseline.disk.files.pop('src/f.c')
    net.progress.signatures['game'] = net.progress._signature(root)
    monkeypatch.setattr(net, 'apply_remote_layout', lambda *args: None)
    judged = []
    def attempt(agent, name, text, slug):
        judged.append(name)
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status=?,matched_source=?,best_source=?,best_score=? WHERE project_id=? AND name=?",
                       ('matched' if matched else 'open', text if matched else None, text, 0 if matched else 10, proj['id'], name))
        return {'matched': matched}
    monkeypatch.setattr(net, 'attempt', attempt)
    files, _, manifest = repository_progress.snapshot(net, 'game', root, print)
    assert judged == ['f']
    assert files['src/f.c' if matched else 'best/f.c'] == source.encode()
    assert ('matched_by' in manifest['functions']['f']) == matched


def test_acknowledged_draft_alongside_a_match_is_removed(progress):
    net, root, remote = progress
    draft = root/'best/f.c'
    draft.parent.mkdir(exist_ok=True)
    draft.write_text('void f(void) { /* earlier draft */ }\n')
    from decompnet.app.progress import _Snapshot
    pid = net.store.one("SELECT id FROM projects WHERE slug='game'")['id']
    net.progress._baseline(pid).disk.files.update(_Snapshot.of({'best/f.c': draft.read_text()}).files)
    net.progress.signatures['game'] = net.progress._signature(root)
    result = repository_sync.run(net, 'game', root, commit_push=True)
    assert result['pushed']
    assert not draft.exists()
    assert 'best/f.c' not in git(remote, 'ls-tree', '-r', '--name-only', 'HEAD')
    assert git(remote, 'show', 'HEAD:src/f.c') == 'void f(void) { return; }'
    assert 'matched_by' in json.loads(git(remote, 'show', 'HEAD:decompdecomp.json'))['functions']['f']
    assert git(root, 'status', '--porcelain') == ''


def test_only_unrelated_paths_are_reported_as_blockers(progress):
    net, root, _ = progress
    (root/'private.rom').write_text('private homemade input')
    with pytest.raises(NetworkError) as error:
        repository_sync.run(net, 'game', root, commit_push=True)
    message = str(error.value)
    assert '(1 path)' in message and 'private.rom' in message
    assert 'README.md' not in message and 'src/f.c' not in message


def test_unacknowledged_draft_changes_are_preserved(progress, monkeypatch):
    net, root, _ = progress
    original = saves._read_owned
    def edited(folder):
        result = original(folder)
        (root/'best').mkdir(exist_ok=True)
        (root/'best/f.c').write_text('concurrent draft\n')
        return result
    monkeypatch.setattr(saves, '_read_owned', edited)
    with pytest.raises(NetworkError, match='Retired progress files'):
        repository_sync.run(net, 'game', root, commit_push=True)
    assert (root/'best/f.c').read_text() == 'concurrent draft\n'
    assert git(root, 'log', '-1', '--format=%s') == 'initial progress'


def test_tracked_redundant_draft_is_deleted_in_the_commit(progress):
    net, root, remote = progress
    from decompnet.app.progress import _Snapshot
    draft = root/'best/f.c'
    draft.parent.mkdir(exist_ok=True)
    draft.write_text('old draft\n')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'old duplicate draft')
    pid = net.store.one("SELECT id FROM projects WHERE slug='game'")['id']
    net.progress._baseline(pid).disk.files.update(_Snapshot.of({'best/f.c': draft.read_text()}).files)
    net.progress.signatures['game'] = net.progress._signature(root)
    result = repository_sync.run(net, 'game', root, commit_push=True)
    assert result['pushed'] and not draft.exists()
    assert 'best/f.c' not in git(remote, 'ls-tree', '-r', '--name-only', 'HEAD')


@pytest.mark.parametrize('change', ['draft', 'source', 'unmatched'])
def test_draft_cleanup_preserves_unverified_or_edited_work(progress, change):
    net, root, _ = progress
    from decompnet.app.progress import _Snapshot
    draft = root/'best/f.c'
    draft.parent.mkdir(exist_ok=True)
    draft.write_text('old draft\n')
    pid = net.store.one("SELECT id FROM projects WHERE slug='game'")['id']
    net.progress._baseline(pid).disk.files.update(_Snapshot.of({'best/f.c': draft.read_text()}).files)
    if change == 'draft':draft.write_text('new edit\n')
    elif change == 'source':(root/'src/f.c').write_text('new selected source\n')
    else:
        with net.store.tx() as db:db.execute("UPDATE functions SET status='open' WHERE project_id=?", (pid,))
    assert net.progress.prune_matched_drafts('game') == 0
    assert draft.exists()


@pytest.mark.parametrize('action', ['save', 'match'])
def test_saving_or_publishing_a_match_removes_its_acknowledged_old_draft(progress, action):
    net, root, _ = progress
    from decompnet.app.progress import _Snapshot
    draft = root/'best/f.c'
    draft.parent.mkdir(exist_ok=True)
    draft.write_text('old draft\n')
    pid = net.store.one("SELECT id FROM projects WHERE slug='game'")['id']
    net.progress._baseline(pid).disk.files.update(_Snapshot.of({'best/f.c': draft.read_text()}).files)
    net.progress.signatures['game'] = net.progress._signature(root)
    if action == 'save':saves.save(net, 'game')
    else:
        with net.store.tx() as db:
            db.execute("UPDATE functions SET matched_source='void f(void) { /* new match */ }' WHERE project_id=?", (pid,))
        net.progress.sync('game', force=True)
    assert not draft.exists()
    assert (root/'src/f.c').is_file()


def test_draft_cleanup_batches_names_without_reading_unrelated_sources(progress, monkeypatch):
    net, root, _ = progress
    store = net.project_store('game')
    pid = net._project('game')['id']
    with store.tx() as db:
        db.executemany("""INSERT INTO functions(project_id,name,status,matched_source)
            VALUES(?,?,'matched',?)""",
            [(pid, f'no_draft_{i:04d}<&é', '/* synthetic body */' * 100) for i in range(513)])
    # The one acknowledged draft must still be rechecked against its selected
    # source; all the other matches have no draft in the saved receipt.
    from decompnet.app.progress import _Snapshot
    draft = root / 'best/f.c'
    draft.parent.mkdir(exist_ok=True)
    draft.write_text('old draft\n')
    net.progress._baseline(pid).disk.files.update(_Snapshot.of({'best/f.c': draft.read_text()}).files)
    native_rows = []
    original = store._read_rows
    def read(sql, args, single):
        result = original(sql, args, single)
        native_rows.append(1 if single else len(result))
        return result
    monkeypatch.setattr(store, '_read_rows', read)
    from decompnet import payload_storage
    unpack = payload_storage.unpack
    def selected_only(key, blob):
        text = unpack(key, blob)
        assert text != '/* synthetic body */' * 100
        return text
    monkeypatch.setattr(payload_storage, 'unpack', selected_only)
    assert net.progress.prune_matched_drafts('game') == 1
    assert not draft.exists()
    assert (root / 'src/f.c').read_text() == 'void f(void) { return; }\n'
    assert max(native_rows) <= 1
    # A newly edited draft must still pass the original receipt checks on the
    # next invocation; batching names cannot authorize its removal.
    draft.write_text('new unacknowledged draft\n')
    assert net.progress.prune_matched_drafts('game') == 0
    assert draft.read_text() == 'new unacknowledged draft\n'
