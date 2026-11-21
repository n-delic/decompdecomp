"""A candidate that emits a fraction of the target is called a stub: found from the judge's own rows."""
import sqlite3
import types

from decompnet.app import stub_candidates
from decompnet.diff_summary import advice, stub, text


def rows(target, candidate):
    """Judge rows: `target` target instructions of which `candidate` are matched, the rest missing."""
    return [(' ', 'addiu $v0, $v0, 1', 'addiu $v0, $v0, 1')] * candidate + [('<', 'sw $v0, 0($a0)', '')] * (target - candidate)


def test_a_candidate_under_half_the_target_is_a_stub():
    assert stub(rows(2122, 146)) == (146, 2122)
    assert 'it is a stub' in advice(rows(2122, 146)) and 'rebuild' in text(rows(2122, 146))


def test_near_misses_and_small_functions_are_not_stubs():
    assert stub(rows(2122, 1200)) is None
    assert stub(rows(31, 4)) is None
    assert stub([('r', 'addu $v0, $a0, $a1', 'addu $v0, $a1, $a0')] * 100) is None
    assert 'stub' not in advice([('r', 'addu $v0, $a0, $a1', 'addu $v0, $a1, $a0')] * 100)


class Store:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)')

    def one(self, sql, *args):
        return self.db.execute(sql, args).fetchone()


def net_with(store):
    return types.SimpleNamespace(project_store=lambda _project: store)


def test_the_verdict_is_kept_for_the_measured_source_only():
    store = Store()
    fn = {'id': 7, 'project_id': 1, 'status': 'open'}
    stub_candidates.record(store.db, 7, 'int f(void){return 0;}', rows(2122, 146))
    net = net_with(store)
    assert 'it is a stub' in stub_candidates.hint(net, fn, 'int f(void){return 0;}')
    assert stub_candidates.hint(net, fn, 'int f(void){return 1;}') == ''
    assert stub_candidates.hint(net, dict(fn, status='matched'), 'int f(void){return 0;}') == ''


def test_a_later_full_size_attempt_clears_the_verdict():
    store = Store()
    stub_candidates.record(store.db, 7, 'a', rows(2122, 146))
    stub_candidates.record(store.db, 7, 'b', rows(2122, 2000))
    assert stub_candidates.hint(net_with(store), {'id': 7, 'project_id': 1, 'status': 'open'}, 'a') == ''
