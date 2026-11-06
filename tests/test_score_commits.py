"""Distinct-function score batches use synthetic scores and real local Git history."""
import json

import pytest

from decompnet.app import auto_push, match_commits, repository_push, score_commits
from decompnet.network import NetworkError

from . import test_match_commits, test_repository_progress

progress = test_repository_progress.progress
automatic = test_match_commits.automatic
git = test_repository_progress.git


def improve(automatic, name='f', score=9):
    net, _, _, agent = automatic
    fn = net._function(name, 'game')
    net._record(fn, agent, score, f'void {name}(void) {{ /* distance {score} */ }}', 1)


def state(net):
    return score_commits.status(net, net.store.one("SELECT * FROM projects WHERE slug='game'"))


def enable(automatic, threshold=2):
    net, root, _, _ = automatic
    return score_commits.configure(net, 'game', True, threshold, str(root))


def test_default_off_counts_distinct_functions_and_commits_latest_drafts(automatic):
    net, root, remote, _ = automatic
    assert state(net) == dict(enabled=False, threshold=10, pending=0, error='', last_commit='')
    assert enable(automatic)['pending'] == 0
    before = git(root, 'rev-parse', 'HEAD')
    remote_before = git(remote, 'rev-parse', 'HEAD')
    improve(automatic, score=9)
    improve(automatic, score=8)
    assert state(net)['pending'] == 1
    assert state(net)['items'] == [{'name':'f', 'before':10, 'score':8}]
    score_commits.process(net, 'game')
    assert git(root, 'rev-parse', 'HEAD') == before
    improve(automatic, 'g', 19)
    assert state(net)['pending'] == 2
    score_commits.process(net, 'game')
    assert state(net)['pending'] == 0 and state(net)['last_commit']
    assert git(root, 'log', '-1', '--format=%s') == 'Improve drafts for 2 functions'
    assert 'distance 8' in git(root, 'show', 'HEAD:best/f.c')
    manifest = json.loads(git(root, 'show', 'HEAD:decompdecomp.json'))
    assert manifest['functions']['f']['best_score'] == 8 and manifest['functions']['g']['best_score'] == 19
    assert git(remote, 'rev-parse', 'HEAD') == remote_before
    improve(automatic, score=7)
    assert state(net)['pending'] == 1


def test_no_backfill_equal_or_worse_scores_and_threshold_changes_keep_pending(automatic):
    net, root, _, _ = automatic
    improve(automatic, score=9)
    enable(automatic)
    assert state(net)['pending'] == 0
    improve(automatic, score=9)
    improve(automatic, score=10)
    assert state(net)['pending'] == 0
    improve(automatic, score=8)
    score_commits.configure(net, 'game', True, 1, str(root))
    assert state(net)['pending'] == 1
    score_commits.process(net, 'game')
    assert state(net)['pending'] == 0


def test_matching_functions_leave_batch_and_drafts_are_not_resurrected(automatic):
    net, root, _, _ = automatic
    test_match_commits.enable(automatic)
    enable(automatic)
    improve(automatic)
    improve(automatic, 'g', 19)
    test_match_commits.match(automatic, 'f', 'Exact function notes')
    assert state(net)['pending'] == 1
    match_commits.process(net, 'game')
    head = git(root, 'rev-parse', 'HEAD')
    score_commits.process(net, 'game')
    assert git(root, 'rev-parse', 'HEAD') == head
    assert not (root/'best/f.c').exists()


def test_scoped_batch_preserves_other_work_and_staged_changes_pause(automatic):
    net, root, _, _ = automatic
    enable(automatic, 1)
    (root/'personal.txt').write_text('unrelated')
    git(root, 'add', 'personal.txt')
    improve(automatic)
    before = git(root, 'rev-parse', 'HEAD')
    score_commits.process(net, 'game')
    assert 'staged' in state(net)['error'] and git(root, 'rev-parse', 'HEAD') == before
    git(root, 'reset', '--', 'personal.txt')
    with net.store.tx() as db:
        row = db.execute("SELECT config FROM projects WHERE slug='game'").fetchone()
        cfg = json.loads(row['config']);cfg['score_commits']['retry_at'] = 0
        db.execute("UPDATE projects SET config=? WHERE slug='game'", (json.dumps(cfg),))
    score_commits.process(net, 'game')
    paths = git(root, 'show', '--format=', '--name-only', 'HEAD').splitlines()
    assert 'best/f.c' in paths and 'best/g.c' not in paths and 'personal.txt' not in paths
    assert (root/'personal.txt').read_text() == 'unrelated'


def test_disable_resets_baseline_and_branch_changes_pause(automatic):
    net, root, _, _ = automatic
    enable(automatic, 1)
    improve(automatic)
    git(root, 'switch', '-c', 'other')
    head = git(root, 'rev-parse', 'HEAD')
    score_commits.process(net, 'game')
    assert 'branch changed' in state(net)['error']
    assert git(root, 'rev-parse', 'HEAD') == head
    score_commits.configure(net, 'game', False)
    assert state(net)['pending'] == 0
    enable(automatic, 1)
    assert state(net)['pending'] == 0


def test_acknowledges_checkpoint_after_crash_without_duplicate_commit(automatic, monkeypatch):
    net, root, _, _ = automatic
    enable(automatic, 1)
    improve(automatic)
    real = score_commits._acknowledge
    def crash(net, rows):
        if rows:raise RuntimeError('simulated interruption after Git commit')
        return real(net, rows)
    monkeypatch.setattr(score_commits, '_acknowledge', crash)
    with pytest.raises(RuntimeError):score_commits.process(net, 'game')
    head = git(root, 'rev-parse', 'HEAD')
    monkeypatch.setattr(score_commits, '_acknowledge', real)
    score_commits.process(net, 'game')
    assert git(root, 'rev-parse', 'HEAD') == head and state(net)['pending'] == 0


def test_improvement_commit_counts_toward_automatic_push(automatic):
    net, root, remote, _ = automatic
    repository_push.run(net, 'game', str(root), lambda line: None)
    enable(automatic, 2)
    auto_push.configure(net, 'game', True, 1, str(root))
    improve(automatic)
    improve(automatic, 'g', 19)
    score_commits.process(net, 'game')
    auto_push.process(net, 'game')
    assert git(remote, 'rev-parse', 'HEAD') == git(root, 'rev-parse', 'HEAD')
    assert 'Improve drafts for 2 functions' in git(remote, 'log', '-1', '--format=%s')


@pytest.mark.parametrize('value', [0, -1, True, 1.5, '10', 10001])
def test_invalid_thresholds(automatic, value):
    net, root, _, _ = automatic
    with pytest.raises(NetworkError, match='batch size'):
        score_commits.configure(net, 'game', True, value, str(root))


def test_disable_during_capture_prevents_publication_without_waiting_for_git(automatic, monkeypatch):
    net, root, _, _ = automatic
    enable(automatic, 1)
    improve(automatic)
    before = git(root, 'rev-parse', 'HEAD')
    capture = score_commits.scoped_snapshot
    def disable(*args):
        result = capture(*args)
        score_commits.configure(net, 'game', False)
        return result
    monkeypatch.setattr(score_commits, 'scoped_snapshot', disable)
    score_commits.process(net, 'game')
    assert not state(net)['enabled'] and git(root, 'rev-parse', 'HEAD') == before


def test_default_ten_and_subthreshold_checks_do_not_synchronize_progress(automatic, monkeypatch):
    net, root, _, _ = automatic
    assert score_commits.configure(net, 'game', True, folder=str(root))['threshold'] == 10
    improve(automatic)
    monkeypatch.setattr(net.progress, 'sync', lambda *args, **kwargs: pytest.fail('A partial batch must not recheck saved code'))
    score_commits.process(net, 'game')
    assert state(net)['pending'] == 1
    score_commits.configure(net, 'game', False)


def test_batch_preserves_the_compiler_profile_captured_with_its_saved_sources(automatic, monkeypatch):
    net, root, _, _ = automatic
    enable(automatic, 1)
    improve(automatic)
    real = score_commits.repository_progress.snapshot
    def changed_profile(*args, **kwargs):
        auto_push._change(net, 'game', lambda cfg: cfg['compiler'].update(flags=['-O2']))
        return real(*args, **kwargs)
    monkeypatch.setattr(score_commits.repository_progress, 'snapshot', changed_profile)
    score_commits.process(net, 'game')
    manifest = json.loads(git(root, 'show', 'HEAD:decompdecomp.json'))
    profile = next(value for value in manifest['function_compilers'].values() if value['function'] == 'f')
    assert profile['flags'] == ['-O2']


def test_zero_distance_without_an_exact_verdict_is_still_a_draft(automatic):
    net, root, _, _ = automatic
    enable(automatic, 1)
    improve(automatic, score=0)
    assert state(net)['pending'] == 1
    score_commits.process(net, 'game')
    manifest = json.loads(git(root, 'show', 'HEAD:decompdecomp.json'))
    assert manifest['functions']['f']['best_score'] == 0
    assert 'matched_by' not in manifest['functions']['f']
    assert 'best/f.c' in git(root, 'show', '--format=', '--name-only', 'HEAD')
