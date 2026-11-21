"""Push-only actions use local bare remotes and preserve every uncommitted byte."""
import subprocess

import pytest

from decompnet.app import repository_push
from decompnet.network import NetworkError

from . import test_repository_progress

progress = test_repository_progress.progress
git = test_repository_progress.git


def commit(root, name='local.txt', text='local progress'):
    (root/name).write_text(text)
    git(root, 'add', name)
    git(root, 'commit', '-qm', text)
    return git(root, 'rev-parse', 'HEAD')


def test_status_and_push_only_existing_commits_preserves_dirty_and_staged_files(progress):
    net, root, remote = progress
    head = commit(root)
    (root/'private.txt').write_text('unfinished private work')
    git(root, 'add', 'private.txt')
    dirty = git(root, 'status', '--porcelain')
    state = repository_push.status(net, 'game', str(root))
    assert state['unpushed'] == 1 and state['can_push']
    assert state['commits'][0]['sha'] == head
    result = repository_push.run(net, 'game', str(root), lambda line: None)
    assert result['pushed'] and result['status']['unpushed'] == 0
    assert git(remote, 'rev-parse', 'HEAD') == head == git(root, 'rev-parse', 'HEAD')
    assert git(root, 'status', '--porcelain') == dirty
    assert 'private.txt' not in git(remote, 'ls-tree', '-r', '--name-only', 'HEAD')


def test_failed_push_keeps_local_commits(progress):
    net, root, remote = progress
    before = git(remote, 'rev-parse', 'HEAD')
    head = commit(root)
    hook = remote/'hooks/pre-receive'
    hook.write_text('#!/bin/sh\nexit 1\n');hook.chmod(0o755)
    with pytest.raises(NetworkError, match='remain local'):
        repository_push.run(net, 'game', str(root), lambda line: None)
    assert git(remote, 'rev-parse', 'HEAD') == before
    assert git(root, 'rev-parse', 'HEAD') == head
    assert repository_push.status(net, 'game', str(root))['unpushed'] == 1


def test_remote_divergence_is_not_force_pushed(progress, tmp_path):
    net, root, remote = progress
    other = tmp_path/'other'
    subprocess.run(['git', 'clone', '-q', str(remote), str(other)], check=True)
    git(other, 'config', 'user.name', 'Other');git(other, 'config', 'user.email', 'other@example.invalid')
    remote_head = commit(other, 'remote.txt', 'remote progress')
    git(other, 'push', 'origin', 'HEAD')
    local = commit(root)
    with pytest.raises(NetworkError, match='ahead or has diverged'):
        repository_push.run(net, 'game', str(root), lambda line: None)
    assert git(remote, 'rev-parse', 'HEAD') == remote_head
    assert git(root, 'rev-parse', 'HEAD') == local
    state = repository_push.status(net, 'game', str(root))
    assert state['behind'] == 1 and not state['can_push']


def test_new_branch_is_pushed_to_origin_without_changing_other_upstream(progress):
    net, root, remote = progress
    git(root, 'checkout', '-qb', 'work/matches')
    git(root, 'remote', 'add', 'other', str(root.parent/'unused.git'))
    git(root, 'config', 'branch.work/matches.remote', 'other')
    git(root, 'config', 'branch.work/matches.merge', 'refs/heads/elsewhere')
    head = commit(root)
    state = repository_push.status(net, 'game', str(root))
    assert state['new_branch'] and state['unpushed'] == 1 and state['destination'] == 'origin/work/matches'
    repository_push.run(net, 'game', str(root), lambda line: None)
    assert git(remote, 'rev-parse', 'refs/heads/work/matches') == head
    assert git(root, 'config', '--get', 'branch.work/matches.remote') == 'other'


def test_detached_missing_origin_and_unlinked_paths_have_actionable_errors(progress, tmp_path):
    net, root, _ = progress
    with pytest.raises(NetworkError, match='linked checkout'):
        repository_push.status(net, 'game', str(tmp_path))
    git(root, 'checkout', '--detach')
    with pytest.raises(NetworkError, match='named Git branch'):
        repository_push.status(net, 'game', str(root))
    git(root, 'checkout', '-b', 'work/test')
    git(root, 'remote', 'rename', 'origin', 'other')
    with pytest.raises(NetworkError, match='origin remote'):
        repository_push.status(net, 'game', str(root))


def test_commits_created_during_fetch_remain_unpushed(progress, monkeypatch):
    net, root, remote = progress
    head = commit(root)
    original = repository_push.git
    later = []
    def changed(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if args[0] == 'fetch':later.append(commit(root, 'later.txt', 'newer automatic match'))
        return result
    monkeypatch.setattr(repository_push, 'git', changed)
    result = repository_push.run(net, 'game', str(root), lambda line: None)
    assert git(remote, 'rev-parse', 'HEAD') == head
    assert git(root, 'rev-parse', 'HEAD') == later[0]
    assert result['status']['unpushed'] == 1


def test_imported_project_checkout_can_push_its_existing_commits(progress):
    net, root, _ = progress
    with net.store.tx() as db:
        db.execute("UPDATE projects SET root=?,config=json_set(config,'$.kind','fork') WHERE slug='game'", (str(root),))
    commit(root)
    assert repository_push.status(net, 'game')['unpushed'] == 1
    assert repository_push.run(net, 'game', '', lambda line: None)['pushed']


@pytest.mark.parametrize('change', ['branch', 'rewrite'])
def test_branch_change_during_fetch_aborts_without_pushing(progress, monkeypatch, change):
    net, root, remote = progress
    before = git(remote, 'rev-parse', 'HEAD')
    commit(root)
    original = repository_push.git
    def changed(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if args[0] == 'fetch':
            if change == 'branch':git(root, 'checkout', '-qb', 'work/changed')
            else:git(root, 'reset', '--soft', 'HEAD^')
        return result
    monkeypatch.setattr(repository_push, 'git', changed)
    with pytest.raises(NetworkError, match='changed while checking'):
        repository_push.run(net, 'game', str(root), lambda line: None)
    assert git(remote, 'rev-parse', 'HEAD') == before


class QueueLog:
    def __init__(self):
        import threading
        self.queued = threading.Event()
        self.cancel = threading.Event()
        self.updates = []
    def __call__(self, line):
        self.check_cancel()
    def check_cancel(self):
        if self.cancel.is_set():raise RuntimeError('Push cancelled')
    def progress(self, value):
        self.updates.append(value)
        if value.get('queued'):self.queued.set()


def test_push_waits_for_repository_operation_then_pushes_current_commits(progress):
    from concurrent.futures import ThreadPoolExecutor

    from decompnet.app import repository_operations
    net, root, remote = progress
    commit(root)
    log = QueueLog()
    with ThreadPoolExecutor(1) as pool:
        with repository_operations.operation(root, 'game', 'Saving checkpoint', lambda _: None) as held:
            assert held is not None
            future = pool.submit(repository_push.run, net, 'game', str(root), log)
            assert log.queued.wait(5) and not future.done()
            # A preceding checkpoint can add commits while the push waits.
            latest = commit(root, 'queued.txt', 'checkpoint completed before push')
        result = future.result(timeout=10)
    assert result['commit'] == latest == git(remote, 'rev-parse', 'HEAD')
    assert any(u.get('queued') for u in log.updates)
    assert log.updates[-1]['queued'] is False


def test_cancelled_queued_push_does_not_touch_origin_or_release_other_operation(progress):
    from concurrent.futures import ThreadPoolExecutor

    from decompnet.app import repository_operations
    net, root, remote = progress
    original = git(remote, 'rev-parse', 'HEAD')
    commit(root)
    log = QueueLog()
    with ThreadPoolExecutor(1) as pool:
        with repository_operations.operation(root, 'game', 'Saving checkpoint', lambda _: None) as held:
            assert held is not None
            future = pool.submit(repository_push.run, net, 'game', str(root), log)
            assert log.queued.wait(5)
            log.cancel.set()
            with pytest.raises(RuntimeError, match='Push cancelled'):future.result(timeout=5)
            with repository_operations.operation(root, 'game', 'Other work', lambda _: None) as other:
                assert other is None
            assert not repository_operations._STATES[repository_operations.key(root)]['waiters']
    assert git(remote, 'rev-parse', 'HEAD') == original
    assert repository_push.run(net, 'game', str(root), lambda _:None)['pushed']


@pytest.mark.parametrize('change', ['branch', 'origin'])
def test_destination_changes_while_queued_are_not_pushed(progress, change):
    from concurrent.futures import ThreadPoolExecutor

    from decompnet.app import repository_operations
    net, root, remote = progress
    original = git(remote, 'rev-parse', 'HEAD')
    commit(root)
    log = QueueLog()
    with ThreadPoolExecutor(1) as pool:
        with repository_operations.operation(root, 'game', 'Saving checkpoint', lambda _: None) as held:
            assert held is not None
            future = pool.submit(repository_push.run, net, 'game', str(root), log)
            assert log.queued.wait(5)
            if change == 'branch':git(root, 'checkout', '-qb', 'work/different')
            else:git(root, 'remote', 'set-url', 'origin', str(remote.parent/'different.git'))
        with pytest.raises(NetworkError, match='changed while this push was queued'):future.result(timeout=5)
    assert git(remote, 'rev-parse', 'HEAD') == original


def test_push_queue_preserves_order_and_recovers_after_failure(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from decompnet.app import repository_operations, repository_push_queue
    root = tmp_path
    logs = [QueueLog(), QueueLog(), QueueLog()]
    order = []
    def work(index):
        with repository_push_queue.turn(root, 'game', logs[index], logs[index].check_cancel):
            order.append(index)
            if index == 0:raise ValueError('earlier push failed')
    with ThreadPoolExecutor(3) as pool:
        with repository_operations.operation(root, 'game', 'Saving checkpoint', lambda _: None) as held:
            assert held is not None
            futures = []
            for index, log in enumerate(logs):
                futures.append(pool.submit(work, index))
                assert log.queued.wait(5)
        with pytest.raises(ValueError, match='earlier push failed'):futures[0].result(timeout=5)
        for future in futures[1:]:future.result(timeout=5)
    assert order == [0,1,2] and not repository_operations._STATES


def incoming(remote, tmp_path):
    other = tmp_path/'other'
    subprocess.run(['git', 'clone', '-q', str(remote), str(other)], check=True)
    git(other, 'config', 'user.name', 'Other')
    git(other, 'config', 'user.email', 'other@example.invalid')
    return other


def test_pull_reviews_fresh_count_and_applies_only_reviewed_commits(progress, tmp_path):
    from decompnet.app import repository_pull

    net, root, remote = progress
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'save fixture progress')
    git(root, 'push', 'origin', 'HEAD')
    before = git(root, 'rev-parse', 'HEAD')
    other = incoming(remote, tmp_path)
    commit(other, 'first.txt')
    commit(other, 'second.txt')
    git(other, 'push', 'origin', 'HEAD')
    log = []
    assert repository_push.status(net, 'game', str(root))['behind'] == 0
    review = repository_pull.run(net, 'game', str(root), log.append)['pull_review']
    assert review['count'] == 2
    assert git(root, 'rev-parse', 'HEAD') == before
    latest = commit(other, 'third.txt')
    git(other, 'push', 'origin', 'HEAD')
    with pytest.raises(NetworkError, match='changed since your review'):
        repository_pull.run(net, 'game', str(root), log.append, review=review['token'])
    assert git(root, 'rev-parse', 'HEAD') == before
    review = repository_pull.run(net, 'game', str(root), log.append)['pull_review']
    assert review['count'] == 3
    (root/'untracked-notes.txt').write_text('local notes')
    state = repository_pull.run(net, 'game', str(root), log.append, review=review['token'])['status']
    assert state['head'] == latest and state['behind'] == 0
    assert (root/'untracked-notes.txt').read_text() == 'local notes'
    assert 'Pulled 3 commits from ' in log[-1]
    assert repository_pull.run(net, 'game', str(root), log.append)['pull_review'] is None


def test_pull_preserves_dirty_work_and_reports_divergence(progress, tmp_path):
    from decompnet.app import repository_pull

    net, root, remote = progress
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'save fixture progress')
    git(root, 'push', 'origin', 'HEAD')
    other = incoming(remote, tmp_path)
    commit(other, 'upstream.txt')
    git(other, 'push', 'origin', 'HEAD')
    review = repository_pull.run(net, 'game', str(root), print)['pull_review']
    (root/'staged.txt').write_text('staged edits')
    git(root, 'add', 'staged.txt')
    with pytest.raises(NetworkError, match='stash tracked changes'):
        repository_pull.run(net, 'game', str(root), print, review=review['token'])
    git(root, 'commit', '-qm', 'local edits')
    local = git(root, 'rev-parse', 'HEAD')
    result = repository_pull.run(net, 'game', str(root), print)
    review = result['pull_review']
    assert review['count'] == 1 and review['merge']
    assert '1 local-only commit' in result['message']
    assert not (root/'upstream.txt').exists()
    assert git(root, 'rev-parse', 'HEAD') == local

    result = repository_pull.run(net, 'game', str(root), print, review=review['token'])
    assert result['status']['behind'] == 0
    assert git(root, 'rev-parse', 'HEAD^1') == local
    assert git(root, 'rev-parse', 'HEAD^2') == git(other, 'rev-parse', 'HEAD')
    assert git(remote, 'rev-parse', 'HEAD') == git(other, 'rev-parse', 'HEAD')
    assert (root/'upstream.txt').exists()
    assert (root/'staged.txt').read_text() == 'staged edits'


def test_pull_merge_conflict_leaves_checkout_untouched(progress, tmp_path):
    from decompnet.app import repository_pull

    net, root, remote = progress
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'save fixture progress')
    git(root, 'push', 'origin', 'HEAD')
    other = incoming(remote, tmp_path)
    (other/'new.txt').write_text('remote version')
    git(other, 'add', 'new.txt')
    git(other, 'commit', '-qm', 'remote version')
    git(other, 'push', 'origin', 'HEAD')
    (root/'new.txt').write_text('local version')
    git(root, 'add', 'new.txt')
    git(root, 'commit', '-qm', 'local version')
    local = git(root, 'rev-parse', 'HEAD')
    before = git(root, 'status', '--porcelain')

    review = repository_pull.run(net, 'game', str(root), print)['pull_review']
    with pytest.raises(NetworkError, match='changes need manual review'):
        repository_pull.run(net, 'game', str(root), print, review=review['token'])

    assert git(root, 'rev-parse', 'HEAD') == local
    assert git(root, 'status', '--porcelain') == before
    assert (root/'new.txt').read_text() == 'local version'


@pytest.mark.parametrize('different_headers', [False, True])
def test_pull_keeps_matches_and_lower_scores_in_diverged_progress(progress, tmp_path, different_headers):
    import json

    from decompnet.app import repository_pull, repository_pull_progress, save_readme, saves

    net, root, remote = progress
    # Use a clean checkpoint with four open functions. The two branches independently
    # promote one function, improve another, and add separate attempts.
    manifest_path = root/saves.MANIFEST
    manifest = json.loads(manifest_path.read_text())
    manifest['functions'] = {}
    manifest['layout'] = [{'name': name, 'vram': addr, 'size': 4, 'status': 'open'}
                          for addr, name in enumerate(('f', 'g', 'h', 'i'), 4096)]
    (root/'src/f.c').unlink()
    progress_text, card = repository_pull_progress._report(manifest)
    (root/'README.md').write_text('My project notes\n\n' + save_readme.block('progress', progress_text) + '\n')
    (root/save_readme.CARD).write_text(card)
    manifest_path.write_text(json.dumps(manifest, indent=1) + '\n')
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'shared progress checkpoint')
    git(root, 'push', 'origin', 'HEAD')
    other = incoming(remote, tmp_path)

    def progress_commit(checkout, title, records):
        manifest_file = checkout/saves.MANIFEST
        data = json.loads(manifest_file.read_text())
        for name, verdict in records.items():
            matched = verdict is None
            entry = {'matched_by': title} if matched else {'best_score': verdict, 'best_by': title}
            data['functions'][name] = {'vram': next(row['vram'] for row in data['layout'] if row['name'] == name), **entry}
            source = checkout/('src' if matched else 'best')/f'{name}.c'
            source.parent.mkdir(exist_ok=True)
            source.write_text(f'void {name}(void) {{}} /* {title} */\n')
        manifest_file.write_text(json.dumps(data, indent=1) + '\n')
        progress_text, card = repository_pull_progress._report(data)
        readme = checkout/'README.md'
        readme.write_text(save_readme._replace(readme.read_text(), 'progress', progress_text))
        (checkout/save_readme.CARD).write_text(card)
        notes = checkout/saves.NOTES_FILE
        notes.write_text(json.dumps([{'function': 'f', 'text': title}], indent=1) + '\n')
        if title == 'remote progress':
            evidence = {'matches': {'f': {'source_sha256': 'remote-source'}},
                        'sources': {'remote-source': 'source snapshot'}}
            (checkout/saves.VERIFICATION_FILE).write_text(json.dumps(evidence) + '\n')
            if different_headers:
                (checkout/'include/common.h').write_text('typedef long s32;\n')
        git(checkout, 'add', '-A')
        git(checkout, 'commit', '-qm', title)
        return git(checkout, 'rev-parse', 'HEAD')

    local = progress_commit(root, 'local progress', {'f': 1, 'g': 12, 'h': 2})
    upstream = progress_commit(other, 'remote progress', {'f': None, 'g': 3, 'i': 9})
    git(other, 'push', 'origin', 'HEAD')
    review = repository_pull.run(net, 'game', str(root), print)['pull_review']
    if different_headers:
        with pytest.raises(NetworkError, match='changes need manual review'):
            repository_pull.run(net, 'game', str(root), print, review=review['token'])
        assert git(root, 'rev-parse', 'HEAD') == local
        assert git(root, 'status', '--porcelain') == ''
        assert 'local progress' in (root/'best/f.c').read_text()
        return
    result = repository_pull.run(net, 'game', str(root), print, review=review['token'])

    assert 'best saved progress for 2 functions' in result['message']
    assert git(root, 'rev-parse', 'HEAD^1') == local
    assert git(root, 'rev-parse', 'HEAD^2') == upstream
    assert git(root, 'status', '--porcelain') == ''
    data = json.loads(manifest_path.read_text())['functions']
    assert data['f']['matched_by'] == 'remote progress'
    assert not (root/'best/f.c').exists()
    assert 'remote progress' in (root/'src/f.c').read_text()
    assert data['g']['best_score'] == 3
    assert 'remote progress' in (root/'best/g.c').read_text()
    assert set(data) == {'f', 'g', 'h', 'i'}
    readme = (root/'README.md').read_text()
    assert readme.startswith('My project notes\n')
    assert '1 matching function(s), 3 best attempt(s)' in readme
    assert '1 / 4 functions matched' in (root/save_readme.CARD).read_text()
    assert [item['text'] for item in json.loads((root/saves.NOTES_FILE).read_text())] == ['local progress', 'remote progress']
    proof = json.loads((root/saves.VERIFICATION_FILE).read_text())
    assert proof['matches']['f']['source_sha256'] == 'remote-source'
    assert proof['sources']['remote-source'] == 'source snapshot'


def test_pull_checkpoints_autosaved_match_and_keeps_it_over_upstream_draft(progress, tmp_path):
    import json

    from decompnet.app import repository_pull, saves

    net, root, remote = progress
    base = commit(root, 'retired.txt', 'Old tracked file')
    git(root, 'push', 'origin', 'HEAD')
    other = incoming(remote, tmp_path)
    (root/'retired.txt').unlink()
    (other/'retired.txt').unlink()
    data = json.loads((other/saves.MANIFEST).read_text())
    data['functions']['f']['best_score'] = 1
    (other/saves.MANIFEST).write_text(json.dumps(data) + '\n')
    (other/'best/f.c').write_text('void f(void) { /* improved draft */ }\n')
    git(other, 'add', '-A')
    git(other, 'commit', '-qm', 'Improve upstream draft')
    git(other, 'push', 'origin', 'HEAD')
    upstream = git(other, 'rev-parse', 'HEAD')

    (root/'private-notes.txt').write_text('Keep local\n')
    review = repository_pull.run(net, 'game', str(root), print)['pull_review']
    assert review['merge']
    assert git(root, 'rev-parse', 'HEAD') == base
    result = repository_pull.run(net, 'game', str(root), print, review=review['token'])

    assert 'best saved progress for 1 function.' in result['message']
    assert git(root, 'rev-parse', 'HEAD^1^') == base
    assert git(root, 'rev-parse', 'HEAD^2') == upstream
    assert git(remote, 'rev-parse', 'HEAD') == upstream
    assert 'matched_by' in json.loads((root/saves.MANIFEST).read_text())['functions']['f']
    assert (root/'src/f.c').read_text() == 'void f(void) { return; }\n'
    assert not (root/'best/f.c').exists()
    assert not (root/'retired.txt').exists()
    assert git(root, 'status', '--porcelain') == '?? private-notes.txt'


def test_pull_keeps_unrelated_tracked_edits_out_of_the_checkpoint(progress, tmp_path):
    from decompnet.app import repository_pull

    net, root, remote = progress
    base = commit(root, 'local-notes.txt', 'Original notes')
    git(root, 'push', 'origin', 'HEAD')
    (root/'local-notes.txt').write_text('Edited notes')
    other = incoming(remote, tmp_path)
    commit(other, 'upstream.txt')
    git(other, 'push', 'origin', 'HEAD')

    review = repository_pull.run(net, 'game', str(root), print)['pull_review']
    with pytest.raises(NetworkError, match='local-notes.txt'):
        repository_pull.run(net, 'game', str(root), print, review=review['token'])

    assert git(root, 'rev-parse', 'HEAD') == base
    assert (root/'local-notes.txt').read_text() == 'Edited notes'
    assert (root/'src/f.c').is_file()
