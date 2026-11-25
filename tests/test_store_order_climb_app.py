"""The store-order climb's app half (app/store_order_climb.py): when it runs, how it is bounded, and that it resumes."""
import json
import re
from types import SimpleNamespace

import pytest

from decompnet.app import deep_permuter, project_activity, store_order_climb

LINES = [f'    p->f{i:X} = {v};' for i, v in zip(range(0, 40, 4), (5, 9, 1, 7, 3, 8, 2, 6, 4, 10))]
SOURCE = 'void init(Rec *p) {\n' + '\n'.join(LINES) + '\n}\n'
WANT = sorted(LINES, key=lambda line: int(re.search(r'= (\d+);', line)[1]))
PROJ = {'slug': 'p', 'id': 1}


def distance(text):
    got = [WANT.index(line) for line in text.splitlines() if line.strip().startswith('p->')]
    return sum(got[a] > got[b] for a in range(len(got)) for b in range(a + 1, len(got)))


class Store:
    def __init__(self):
        self.rows = {}

    def one(self, sql, fid, signature):
        return {'feedback': self.rows[signature]} if signature in self.rows else None

    def tx(self):
        rows = self.rows

        class Db:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def execute(self_, sql, params):
                rows[params[1]] = params[4]
        return Db()


class FakeSearch:
    def __init__(self, limit=1000, scorer=distance):
        self.store, self.limit, self.tested, self.phase, self.lines, self.stopped = Store(), limit, 0, 'x', [], False
        self.net = SimpleNamespace(project_store=lambda pid: self.store)
        self.scorer, self.seen = scorer, []

    def log(self, text):
        self.lines.append(text)

    def check(self):
        if self.stopped:
            raise deep_permuter.SearchLimit('Stopped')

    def _score(self, agent, proj, fn, text, best, fork):
        self.tested += 1
        self.seen.append(text)
        return self.scorer(text)


@pytest.fixture(autouse=True)
def arrange(monkeypatch):
    from decompnet.app import agent_tokens
    monkeypatch.setattr(project_activity, 'active', lambda net, slug: True)
    monkeypatch.setattr(agent_tokens, 'target_scope', lambda net, fn, extra: 'scope')


def fn(score=45, **kwargs):
    return {'id': 1, 'name': 'init', 'status': 'open', 'best_score': score, 'unit': 'u', **kwargs}


def test_only_a_close_open_function_with_a_long_block_is_a_candidate():
    net = SimpleNamespace()
    assert store_order_climb.eligible(net, PROJ, fn(40), SOURCE, '')
    assert not store_order_climb.eligible(net, PROJ, fn(store_order_climb.CLOSE + 1), SOURCE, '')
    assert not store_order_climb.eligible(net, PROJ, fn(0), SOURCE, '')
    assert not store_order_climb.eligible(net, PROJ, fn(None), SOURCE, '')
    assert not store_order_climb.eligible(net, PROJ, fn(40, status='matched'), SOURCE, '')
    short = 'void init(Rec *p) {\n' + '\n'.join(LINES[:4]) + '\n}\n'
    assert not store_order_climb.eligible(net, PROJ, fn(40), short, '')


def test_the_climb_walks_to_the_best_order_and_records_that_it_finished():
    search = FakeSearch()
    assert store_order_climb.run(search, None, PROJ, fn(), SOURCE, '') == 0
    assert search.phase == 'x' and any('reached 0' in line for line in search.lines)
    assert store_order_climb.SIGNATURE + store_order_climb._scope(search.net, fn(), SOURCE) in search.store.rows


def test_a_climbed_saved_c_is_not_climbed_again():
    search = FakeSearch()
    store_order_climb.run(search, None, PROJ, fn(), SOURCE, '')
    before = search.tested
    assert store_order_climb.run(search, None, PROJ, fn(), SOURCE, '') is None
    assert search.tested == before


def test_a_spent_allowance_stops_it_and_the_next_run_resumes_from_the_steps_it_used():
    search = FakeSearch(limit=6)
    store_order_climb.run(search, None, PROJ, fn(), SOURCE, '')
    assert search.tested <= 6
    rows = search.store.rows
    assert not any(key.startswith(store_order_climb.SIGNATURE) for key in rows)  # cut short: not finished
    progress = json.loads(next(v for k, v in rows.items() if k.startswith(store_order_climb.PROGRESS)))
    assert progress['calls'] >= 1
    search.limit = 1000
    assert store_order_climb.run(search, None, PROJ, fn(), SOURCE, '') == 0


def test_a_stop_raises_past_the_climb_and_keeps_the_steps_used():
    search = FakeSearch()
    seen = []

    def stopping(text):
        seen.append(text)
        if len(seen) == 4:
            search.stopped = True
        return distance(text)
    search.scorer = stopping
    with pytest.raises(deep_permuter.SearchLimit):
        store_order_climb.run(search, None, PROJ, fn(), SOURCE, '')
    assert search.phase == 'x'
    assert any(k.startswith(store_order_climb.PROGRESS) for k in search.store.rows)
    assert not any(k.startswith(store_order_climb.SIGNATURE) for k in search.store.rows)


def test_an_idle_project_is_left_alone(monkeypatch):
    monkeypatch.setattr(project_activity, 'active', lambda net, slug: False)
    search = FakeSearch()
    assert store_order_climb.run(search, None, PROJ, fn(), SOURCE, '') is None and not search.seen


def test_the_deep_permuter_and_the_autopilots_saved_source_pass_call_it():
    from pathlib import Path
    root = Path(deep_permuter.__file__).parent
    assert 'store_order_pass' in (root / 'deep_permuter.py').read_text()
    assert 'store_order_autopilot' in (root / 'engine' / 'autopilot.py').read_text()


def test_the_deep_permuters_pass_reads_the_header_from_the_context_row(monkeypatch):
    """The header comes from a database row (no `.get`): every function failed with "'sqlite3.Row' object has no
    attribute 'get'" until it was read by key, and a function with no context at all gets an empty header."""
    import sqlite3
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT 'typedef int s32;' AS header").fetchone()
    seen = []
    monkeypatch.setattr(store_order_climb, 'run', lambda s, a, p, f, src, header, fork: seen.append(header))
    monkeypatch.setattr(deep_permuter, '_candidate_source', lambda fn, best: 'int f(void){return 1;}')
    for context, want in ((row, 'typedef int s32;'), (None, '')):
        search = SimpleNamespace(net=SimpleNamespace(_context=lambda pid, unit, c=context: c))
        deep_permuter.Search.store_order_pass(search, None, PROJ, {'unit': 'u', 'name': 'f'}, 5, None)
        assert seen[-1] == want
