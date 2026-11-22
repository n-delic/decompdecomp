"""Saved candidate resets are transient search invalidations, never a zero score or completed pass."""
import pytest

from decompnet.app import deep_permuter, draft_repairs
from decompnet.app.progress import ProgressMoved
from tests import test_deep_sweep as sweep_tests
from tests.test_deep_permuter import Log, setup
from tests.test_permute import AGENT

sweep = sweep_tests.sweep


@pytest.mark.parametrize('missing', ['source', 'score', 'both'])
@pytest.mark.parametrize('when', ['entry', 'preflight', 'winner', 'return'])
def test_reset_defers_before_using_missing_candidate(monkeypatch, missing, when):
    net = setup(monkeypatch)
    fn = net.function()
    search = deep_permuter.Search(net, 1, 20, Log(), fn)
    scored = []
    def clear():
        with net.store.tx() as db:
            db.execute('UPDATE functions SET '+(','.join(
                field+'=NULL' for field in ('best_source', 'best_score')
                if missing == 'both' or field == ('best_source' if missing == 'source' else 'best_score'))))
    def preflight(agent, proj, current, best, fork):
        if when == 'preflight' or when == 'winner' and current['best_source'] == 'winner':clear()
        return net.function()['best_score']
    monkeypatch.setattr(search, '_repair_preflight', preflight)
    def score(agent, proj, current, source, best, fork):
        scored.append(source)
        with net.store.tx() as db:db.execute("UPDATE functions SET best_source='winner',best_score=20")
        return 20
    monkeypatch.setattr(search, '_score', score)
    monkeypatch.setattr(draft_repairs, 'variants', lambda *args, **kwargs: ['winner'])
    if when == 'entry':clear()
    if when == 'return':
        clear()
        run = lambda: search._saved_result(net.project_row('game'), fn, 40)
    else:
        run = lambda: search.local_repairs(AGENT, net.project_row('game'), net.function(), 40, None, max_trials=1)
    with pytest.raises(ProgressMoved, match='Saved candidate changed'):
        run()
    assert scored == (['winner'] if when == 'winner' else [])
    assert net.function()['status'] == 'open'


def test_matched_database_candidate_stops_without_parsing_missing_best(monkeypatch):
    net = setup(monkeypatch)
    search = deep_permuter.Search(net, 1, 20, Log(), net.function())
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source='int f(void){return 1;}',best_source=NULL,best_score=NULL")
    monkeypatch.setattr(search, '_repair_preflight', lambda *a: pytest.fail('A matched function needs no flag probe'))
    assert search.local_repairs(AGENT, net.project_row('game'), net.function(), 40, None) == (0, 'int f(void){return 1;}')


def test_sweep_keeps_input_reset_pending_without_completing(sweep, monkeypatch):
    worker, net, _, _ = sweep
    def moved(net, slug, name, *args, **kwargs):
        with net.store.tx() as db:
            db.execute('UPDATE functions SET best_source=NULL,best_score=NULL WHERE name=?', (name,))
        raise ProgressMoved('Saved candidate changed during deep search')
    monkeypatch.setattr(deep_permuter, 'run', moved)
    class StopLog(Log):
        def check_cancel(self):
            if net.store.one("SELECT 1 FROM deep_permutation_passes WHERE state='pending' LIMIT 1"):
                raise RuntimeError('cancelled while waiting')
    with pytest.raises(RuntimeError, match='cancelled while waiting'):
        worker.run('game', 1, 10, StopLog())
    rows = net.store.all('SELECT state,completed_at,error FROM deep_permutation_passes')
    assert rows and all(row['state'] == 'pending' and row['completed_at'] is None for row in rows)
    assert all('Saved candidate changed' in row['error'] for row in rows)


def test_local_repairs_parse_saved_source_under_the_current_function_name(monkeypatch):
    net = setup(monkeypatch)
    old = 'int old(void) { /* old */ return old(); }'
    current = 'int f(void) { /* old */ return f(); }'
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?,meta=?',
                   (old, '{"naming":{"previous_name":"old"}}'))
    fn = net.function()
    search = deep_permuter.Search(net, 1, 20, Log(), fn)
    monkeypatch.setattr(search, '_repair_preflight', lambda *args: 40)
    seen = []

    def variants(source, name, *args, **kwargs):
        seen.append((source, name))
        return []

    monkeypatch.setattr(draft_repairs, 'variants', variants)
    assert search.local_repairs(AGENT, net.project_row('game'), fn, 40, None, max_trials=1) == (40, current)
    assert seen and all(item == (current, 'f') for item in seen)
    assert net.function()['best_source'] == old


def test_renamed_candidate_uses_its_measured_diff_only_in_the_same_scope(monkeypatch):
    net = setup(monkeypatch)
    fn = dict(net.function(), vram=0x80000000)
    search = deep_permuter.Search(net, 1, 20, Log(), fn)
    old = 'int func_80000000(void) { return 1; }'
    current = 'int f(void) { return 1; }'
    search.winner_source = old
    search.winner_diff = ([], '')
    search.winner_scope = deep_permuter.agent_tokens.target_scope(net, dict(fn, best_source=old), '')
    assert search.measured_winner(fn, current)
    assert not search.measured_winner(fn, current.replace('1', '2'))
    assert not search.measured_winner(dict(fn, target=b'changed'), current)
