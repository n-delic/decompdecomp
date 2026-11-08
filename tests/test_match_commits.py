"""Automatic match commits exercise the ordinary record, notes, progress and local Git pipeline."""
import json
import subprocess

import pytest

from decompnet.app import match_commits, repository_progress, saves
from decompnet.network import NetworkError

from . import test_repository_progress

progress = test_repository_progress.progress
git = test_repository_progress.git


@pytest.fixture
def automatic(progress, monkeypatch):
    net, root, remote = progress
    monkeypatch.setattr(net, 'on_match', lambda *args: None)
    net._update_cfg('game', lambda cfg: cfg.update(compiler={'toolchain': 'gcc-2.7.2-kmc', 'flags': []}))
    pid = net.store.one("SELECT id FROM projects WHERE slug='game'")['id']
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open',matched_source=NULL,best_source='void f(void) {}',best_score=10 WHERE project_id=?", (pid,))
        db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit,best_source,best_score) VALUES(?,'g',4100,4,1,'open','','void g(void) {}',20)", (pid,))
    net.progress.sync('game', force=True)
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'checkpoint before automatic matches')
    agent, _ = net.local_agent('test helper')
    return net, root, remote, agent


def match(automatic, name='f', note=None):
    net, _, _, agent = automatic
    fn = net._function(name, 'game')
    net._record(fn, agent, 0, f'void {name}(void) {{ return; }}', 1, matched=True)
    if note is not None:net.add_note(agent, 'game', note, name)


def enable(automatic):
    net, root, _, _ = automatic
    return match_commits.configure(net, 'game', True, str(root))


def test_default_off_and_enabling_does_not_backfill(automatic):
    net, root, _, _ = automatic
    match(automatic, note='Existing match')
    before = git(root, 'rev-parse', 'HEAD')
    assert not match_commits.status(net, net.project_row('game'))['enabled']
    assert enable(automatic)['pending'] == 0
    match_commits.process(net, 'game')
    assert git(root, 'rev-parse', 'HEAD') == before


def test_one_commit_per_function_uses_notes_and_removes_only_its_draft(automatic):
    net, root, remote, _ = automatic
    remote_before = git(remote, 'rev-parse', 'HEAD')
    before = int(git(root, 'rev-list', '--count', 'HEAD'))
    enable(automatic)
    match(automatic, 'f', 'Use the signed comparison\n\nPreserve loop order and the literal `$(text)`.')
    match(automatic, 'g', 'Use an unsigned return value')
    match_commits.process(net, 'game')
    assert int(git(root, 'rev-list', '--count', 'HEAD')) == before + 2
    assert git(root, 'log', '-2', '--format=%s').splitlines() == ['Use an unsigned return value', 'Use the signed comparison']
    assert 'Preserve loop order and the literal `$(text)`.' in git(root, 'log', '-1', '--format=%B', 'HEAD^')
    first = set(git(root, 'show', '--format=', '--name-only', 'HEAD^').splitlines())
    second = set(git(root, 'show', '--format=', '--name-only', 'HEAD').splitlines())
    assert {'src/f.c', 'best/f.c'} <= first and 'src/g.c' not in first and 'best/g.c' not in first
    assert {'src/g.c', 'best/g.c'} <= second and 'src/f.c' not in second
    manifest = json.loads(git(root, 'show', 'HEAD^:decompdecomp.json'))
    assert 'matched_by' in manifest['functions']['f'] and 'matched_by' not in manifest['functions']['g']
    assert not (root/'best/f.c').exists() and not (root/'best/g.c').exists()
    assert git(remote, 'rev-parse', 'HEAD') == remote_before  # No automatic push.
    assert match_commits.status(net, net.project_row('game'))['pending'] == 0


def test_missing_notes_use_fallback_without_repeating_after_late_notes(automatic):
    net, root, _, agent = automatic
    enable(automatic)
    before = git(root, 'rev-parse', 'HEAD')
    match(automatic)
    match_commits.process(net, 'game')
    committed = git(root, 'rev-parse', 'HEAD')
    assert committed != before
    assert git(root, 'log', '-1', '--format=%s') == 'Match f'
    assert match_commits.status(net, net.project_row('game'))['pending'] == 0
    net.release_function(agent, 'f', 'Matching agent handoff notes', 'game')
    match(automatic, note='Polishing the same match does not add another match commit')
    match_commits.process(net, 'game')
    assert git(root, 'rev-parse', 'HEAD') == committed


def test_disable_cancels_pending_and_branch_or_index_changes_pause_commits(automatic):
    net, root, _, _ = automatic
    enable(automatic)
    match(automatic, note='Function notes')
    (root/'private.txt').write_text('unrelated staged work')
    git(root, 'add', 'private.txt')
    before = git(root, 'rev-parse', 'HEAD')
    match_commits.process(net, 'game')
    assert 'staged' in match_commits.status(net, net.project_row('game'))['error']
    assert git(root, 'rev-parse', 'HEAD') == before
    git(root, 'reset', '-q', 'HEAD', '--', 'private.txt')
    (root/'private.txt').unlink()
    with net.store.tx() as db:db.execute('UPDATE auto_match_commits SET retry_at=0')
    git(root, 'checkout', '-qb', 'different-branch')
    match_commits.process(net, 'game')
    assert 'branch changed' in match_commits.status(net, net.project_row('game'))['error']
    match_commits.configure(net, 'game', False)
    assert not match_commits.status(net, net.project_row('game'))['enabled']
    enable(automatic)
    match_commits.process(net, 'game')
    assert git(root, 'rev-parse', 'HEAD') == before


def test_retries_after_commit_recording_failure_without_a_duplicate(automatic, monkeypatch):
    net, root, _, _ = automatic
    enable(automatic)
    match(automatic, note='Durable function notes')
    original = repository_progress.commit_snapshot
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise NetworkError('Interrupted after creating the commit')
    monkeypatch.setattr(repository_progress, 'commit_snapshot', interrupted)
    match_commits.process(net, 'game')
    committed = git(root, 'rev-parse', 'HEAD')
    monkeypatch.setattr(repository_progress, 'commit_snapshot', original)
    with net.store.tx() as db:db.execute('UPDATE auto_match_commits SET retry_at=0')
    match_commits.process(net, 'game')
    assert git(root, 'rev-parse', 'HEAD') == committed
    assert match_commits.status(net, net.project_row('game'))['pending'] == 0


def test_other_agents_notes_are_ignored_and_unrelated_edits_stay_uncommitted(automatic):
    net, root, _, _ = automatic
    enable(automatic)
    match(automatic)
    other, _ = net.local_agent('different helper')
    net.add_note(other, 'game', 'Another agent’s older notes', 'f')
    (root/'private.rom').write_bytes(b'homemade private input')
    before = git(root, 'rev-parse', 'HEAD')
    match_commits.process(net, 'game')
    assert match_commits.status(net, net.project_row('game'))['pending'] == 0
    assert git(root, 'rev-parse', 'HEAD') != before
    assert git(root, 'log', '-1', '--format=%s') == 'Match f'
    assert 'private.rom' not in git(root, 'ls-files')


def test_checkbox_preflight_checks_folder_and_identity(automatic):
    net, root, _, _ = automatic
    with pytest.raises(NetworkError):match_commits.configure(net, 'game', 'yes', str(root))
    with pytest.raises(NetworkError):match_commits.configure(net, 'game', True, str(root.parent))
    git(root, 'checkout', '--detach')
    with pytest.raises(NetworkError, match='named Git branch'):enable(automatic)


def test_autosave_drains_enabled_matches(automatic):
    net, root, _, _ = automatic
    enable(automatic)
    match(automatic, note='Saved from the ordinary autosave worker')
    before = git(root, 'rev-parse', 'HEAD')
    saves.Autosave(net).run_once()
    assert git(root, 'rev-parse', 'HEAD') != before
    assert git(root, 'log', '-1', '--format=%s') == 'Saved from the ordinary autosave worker'


def test_gui_auto_commit_checkbox():
    from pathlib import Path
    subprocess.run(['node', str(Path(__file__).with_name('gui_match_commits.js'))], check=True, capture_output=True, timeout=30)


def test_pending_matches_survive_reopening_the_engine(automatic):
    from decompnet.app.engine import RomNetwork
    net, root, _, _ = automatic
    enable(automatic)
    match(automatic, note='Notes retained across restart')
    reopened = RomNetwork(net.store.path, root.parent)
    match_commits.process(reopened, 'game')
    assert git(root, 'log', '-1', '--format=%s') == 'Notes retained across restart'
    assert match_commits.status(reopened, reopened.project_row('game'))['pending'] == 0


def test_disabling_during_snapshot_cancels_publication(automatic, monkeypatch):
    net, root, _, _ = automatic
    enable(automatic)
    match(automatic, note='Pending notes')
    before = git(root, 'rev-parse', 'HEAD')
    original = match_commits.scoped_snapshot
    def disabled(*args):
        result = original(*args)
        match_commits.configure(net, 'game', False)
        return result
    monkeypatch.setattr(match_commits, 'scoped_snapshot', disabled)
    match_commits.process(net, 'game')
    assert git(root, 'rev-parse', 'HEAD') == before


def test_layout_changes_require_a_manual_checkpoint(automatic):
    net, root, _, _ = automatic
    with net.store.tx() as db:db.execute("UPDATE functions SET size=8 WHERE name='g'")
    with pytest.raises(NetworkError, match='Commit current progress once'):
        enable(automatic)


def test_later_edits_do_not_replace_the_queued_verified_source(automatic):
    net, root, _, agent = automatic
    enable(automatic)
    match(automatic)
    original = 'void f(void) { return; }'
    # A later exact polish keeps matched status and is not another new match.
    fn = net._function('f', 'game')
    net._record(fn, agent, 0, 'void f(void) { /* polished */ return; }', 1, matched=True)
    net.add_note(agent, 'game', 'Original matching notes', 'f')
    match_commits.process(net, 'game')
    assert git(root, 'show', 'HEAD:src/f.c') == original
    assert 'polished' in (root/'src/f.c').read_text()
    assert 'src/f.c' in git(root, 'diff', '--name-only')


def test_pre_match_notes_are_ignored_in_favor_of_fallback(automatic):
    net, root, _, agent = automatic
    enable(automatic)
    net.add_note(agent, 'game', 'Older unsuccessful experiment', 'f')
    match(automatic)
    match_commits.process(net, 'game')
    assert git(root, 'log', '-1', '--format=%s') == 'Match f'


def test_notes_written_by_the_match_callback_are_available(automatic, monkeypatch):
    net, root, _, agent = automatic
    enable(automatic)
    monkeypatch.setattr(net, 'on_match', lambda *args: net.add_note(agent, 'game', 'Notes recorded with the matching verdict', 'f'))
    match(automatic)
    match_commits.process(net, 'game')
    assert git(root, 'log', '-1', '--format=%s') == 'Notes recorded with the matching verdict'


def test_new_saved_contributions_use_the_contributing_agents_notes(automatic, monkeypatch):
    from types import SimpleNamespace
    net, root, _, agent = automatic
    enable(automatic)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_agent_id=? WHERE name='f'", (agent.id,))
    net.progress.sync('game', force=True)
    net.add_note(agent, 'game', 'The contributing agent found this match', 'f')
    monkeypatch.setattr(net, 'apply_remote_layout', lambda *args: None)
    monkeypatch.setattr(net, '_compile', lambda *args: SimpleNamespace(ok=True, obj=b'fake', log=''))
    def attempt(worker, name, source, slug):
        net._record(net._function(name, slug), worker, 0, source, 1, matched=True)
        return {'matched': True}
    monkeypatch.setattr(net, 'attempt', attempt)
    (root/'best/f.c').write_text('void f(void) { return; }\n')
    net.progress.sync('game', force=True)
    assert net.store.one("SELECT COUNT(*) n FROM auto_match_commits")['n'] == 1
    match_commits.process(net, 'game')
    assert git(root, 'log', '-1', '--format=%s') == 'The contributing agent found this match'
    # Rechecking an existing saved match must not enqueue it again.
    net.progress.sync('game', force=True)
    assert net.store.one("SELECT COUNT(*) n FROM auto_match_commits")['n'] == 1


def test_auto_commit_leaves_unrelated_tracked_edits_and_deleted_drafts_uncommitted(automatic):
    net, root, _, _ = automatic
    (root/'review.txt').write_text('original review')
    (root/'best/retired.c').write_text('old unrelated draft')
    git(root, 'add', 'review.txt', 'best/retired.c')
    git(root, 'commit', '-qm', 'unrelated baseline')
    (root/'review.txt').write_text('unfinished user review')
    (root/'best/retired.c').unlink()
    enable(automatic)
    match(automatic, note='Exact function notes')
    match_commits.process(net, 'game')
    assert match_commits.status(net, net.project_row('game'))['pending'] == 0
    assert git(root, 'show', 'HEAD:review.txt') == 'original review'
    assert git(root, 'show', 'HEAD:best/retired.c') == 'old unrelated draft'
    assert (root/'review.txt').read_text() == 'unfinished user review'
    assert not (root/'best/retired.c').exists()
    assert {'src/f.c', 'best/f.c'} <= set(git(root, 'show', '--format=', '--name-only', 'HEAD').splitlines())


def test_blank_notes_use_fallback_and_remove_draft(automatic):
    net, root, _, agent = automatic
    enable(automatic)
    match(automatic, note='Temporary note')
    with net.store.tx() as db:
        db.execute("UPDATE notes SET text=' \n\t ' WHERE agent_id=?", (agent.id,))
    match_commits.process(net, 'game')
    assert git(root, 'log', '-1', '--format=%s') == 'Match f'
    assert not (root/'best/f.c').exists()
    assert 'best/f.c' in git(root, 'show', '--format=', '--name-only', 'HEAD')


def test_waiting_push_interrupts_match_batch_after_current_commit(automatic, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from decompnet.app import repository_push

    from .test_repository_push import QueueLog
    net, root, remote, _ = automatic
    enable(automatic)
    match(automatic, 'f', 'first match')
    match(automatic, 'g', 'second match')
    output = QueueLog()
    pushing, release = threading.Event(), threading.Event()
    original_commit = repository_progress.commit_snapshot
    original_git = repository_push.git
    future = []
    def git_push(path, *args, **kwargs):
        if args[0] == 'push':
            pushing.set()
            assert release.wait(10)
        return original_git(path, *args, **kwargs)
    monkeypatch.setattr(repository_push, 'git', git_push)
    with ThreadPoolExecutor(1) as pool:
        def commit_and_queue(*args, **kwargs):
            result = original_commit(*args, **kwargs)
            if not future:
                future.append(pool.submit(repository_push.run, net, 'game', str(root), output))
                assert output.queued.wait(5)
            return result
        monkeypatch.setattr(repository_progress, 'commit_snapshot', commit_and_queue)
        try:
            match_commits.process(net, 'game')
            assert pushing.wait(5)
            assert match_commits.status(net, net.project_row('game'))['pending'] == 1
        finally:
            release.set()
        assert future[0].result(timeout=10)['pushed']
    pushed = git(remote, 'rev-parse', 'HEAD')
    manifest = json.loads(git(remote, 'show', 'HEAD:decompdecomp.json'))
    assert 'matched_by' in manifest['functions']['f']
    assert 'matched_by' not in manifest['functions']['g']
    match_commits.process(net, 'game')
    assert match_commits.status(net, net.project_row('game'))['pending'] == 0
    assert git(root, 'rev-parse', 'HEAD^') == pushed


def test_revoked_match_job_is_retired_and_a_later_exact_verdict_can_commit(automatic):
    net, root, _, _ = automatic
    enable(automatic)
    match(automatic, note='Initial exact verdict')
    store = net.project_store('game')
    with store.tx() as db:
        db.execute("UPDATE functions SET status='open',matched_source=NULL WHERE name='f'")
        db.execute("UPDATE auto_match_commits SET error='The queued function no longer matches.' WHERE state='pending'")
    before = git(root, 'rev-parse', 'HEAD')
    match_commits.process(net, 'game')
    assert git(root, 'rev-parse', 'HEAD') == before
    assert match_commits.status(net, net.project_row('game'))['pending'] == 0
    retired = store.one("SELECT state,error FROM auto_match_commits WHERE state='cancelled'")
    assert retired is not None and retired['error'] is None
    match(automatic, note='Fresh exact verdict')
    match_commits.process(net, 'game')
    assert git(root, 'log', '-1', '--format=%s') == 'Fresh exact verdict'
    assert match_commits.status(net, net.project_row('game'))['pending'] == 0


def test_automatic_match_in_build_progress_child_never_changes_build_sources(automatic):
    from decompnet.app import repository_setup, repository_sync

    net, root, _, _ = automatic
    child = root / 'progress'
    child.mkdir()
    for name in ('src', 'best', 'include', 'decompdecomp.json', 'README.md', 'decompdecomp-progress.svg'):
        path = root / name
        if path.exists():path.rename(child / name)
    (root / 'src').mkdir()
    (root / 'src' / 'manual.c').write_text('int hand_written;')
    (root / 'build.json').write_text('{}')
    (root / 'build.py').write_text('# do not execute')
    net._update_cfg('game', lambda cfg: cfg.update(progress_folder=str(child), save_folder=str(child)))
    net.progress.sync('game', force=True)
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'combined build and progress checkpoint')
    before = git(root, 'rev-parse', 'HEAD')
    assert repository_setup.status(net, 'game')['build_folder'] == str(root)
    assert not repository_sync.progress_checkout(net, 'game', root)
    assert enable(automatic)['enabled']
    match(automatic, 'f')
    match_commits.process(net, 'game')
    assert git(root, 'rev-parse', 'HEAD') != before
    assert git(root, 'show', 'HEAD:src/manual.c') == 'int hand_written;'
    assert git(root, 'show', 'HEAD:build.json') == '{}'
    changed = git(root, 'diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD').splitlines()
    assert changed and all(path.startswith('progress/') for path in changed)
    assert 'progress/src/f.c' in changed
    assert match_commits.status(net, net.project_row('game'))['pending'] == 0


def test_background_commit_does_not_wait_for_shared_autosave_lane(automatic):
    import time
    net, root, _, _ = automatic
    enable(automatic)
    match(automatic, note='Independent automatic commit')
    before = git(root, 'rev-parse', 'HEAD')
    auto = saves.Autosave(net)
    auto.lane.acquire()  # A different project's expensive save owns this slot.
    try:
        auto._reconcile()
        until = time.monotonic() + 8
        while match_commits.status(net, net.project_row('game'))['pending']:
            assert time.monotonic() < until, 'Commit waited for the occupied autosave lane'
            time.sleep(.02)
        assert git(root, 'rev-parse', 'HEAD') != before
        assert git(root, 'log', '-1', '--format=%s') == 'Independent automatic commit'
        assert auto.lane.acquire(blocking=False) is False
    finally:
        auto.close()
        auto.lane.release()
        for worker in auto.workers.values():
            worker['thread'].join(3)
            if worker.get('commit_thread'):worker['commit_thread'].join(3)


def test_polished_commits_are_independent_and_preserve_frozen_source(automatic):
    net, root, _, agent = automatic
    match(automatic)
    net.progress.sync('game', force=True)
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'matched before polish')
    state = match_commits.configure(net, 'game', True, str(root), kind='polished')
    assert state['polished_enabled'] and not state['enabled']
    fn = net._function('f', 'game')
    source = '/* Readable purpose. ' + 'Detailed explanation. ' * 40 + '*/ void f(void) { return; }'
    context = net._context(fn['project_id'], fn['unit'])
    net._record(fn, agent, 0, source, context['version'])
    net.readability._readable(net.project_row('game'), fn, agent, source)
    state = match_commits.status(net, net.project_row('game'))
    assert state['pending'] == 1
    job = net.store.one("SELECT * FROM auto_match_commits WHERE state='pending'")
    captured = json.loads(job['snapshot'])
    assert captured['kind'] == 'polished'
    assert captured['source'] == source
    assert captured['details']['source_sha256'] == __import__('hashlib').sha256(source.encode()).hexdigest()
    # Disabling new-match commits must leave the independent polish queue intact.
    match_commits.configure(net, 'game', False)
    assert match_commits.status(net, net.project_row('game'))['pending'] == 1
    match_commits.process(net, 'game', lambda *_: None)
    assert match_commits.status(net, net.project_row('game'))['pending'] == 0
    assert git(root, 'log', '-1', '--format=%s') == 'Polish f'
    assert 'Polished by: '+agent.name in git(root, 'log', '-1', '--format=%B')
    assert git(root, 'show', 'HEAD:src/f.c').strip() == source
    # A later verified polish has its own queue entry, and can be cancelled independently.
    fresh = net._function('f', 'game')
    later = source + ' /* Further explanation. */'
    net._record(fresh, agent, 0, later, context['version'])
    net.readability._readable(net.project_row('game'), fresh, agent, later)
    assert match_commits.status(net, net.project_row('game'))['pending'] == 1
    match_commits.configure(net, 'game', False, kind='polished')
    assert match_commits.status(net, net.project_row('game'))['pending'] == 0
    assert net.store.one("SELECT COUNT(*) n FROM auto_match_commits WHERE state='cancelled'")['n'] == 1


def test_polish_queue_is_opt_in_and_disabling_only_cancels_polish(automatic):
    net, root, _, agent = automatic
    enable(automatic)
    match(automatic)
    fn = net._function('f', 'game')
    match_commits.enqueue(net, net.project_row('game'), fn['id'], polished=True)
    assert net.store.one('SELECT COUNT(*) n FROM auto_match_commits')['n'] == 1
    match_commits.configure(net, 'game', True, str(root), kind='polished')
    # Enabling is future-only and does not backfill the old match.
    assert net.store.one('SELECT COUNT(*) n FROM auto_match_commits')['n'] == 1
    match_commits.configure(net, 'game', False, kind='polished')
    assert match_commits.status(net, net.project_row('game'))['pending'] == 1
