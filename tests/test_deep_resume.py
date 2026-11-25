from types import SimpleNamespace

from decompnet.app import deep_resume
from decompnet.store import IN_MEMORY, Store


def test_retained_roots_keep_distinct_outputs_and_invalidate_changed_inputs(monkeypatch):
    store = Store(IN_MEMORY)
    net = SimpleNamespace(project_store=lambda _: store)
    proj = {'id': 1}
    fn = {'id': 2, 'best_source': 'current'}
    header = ['old']
    monkeypatch.setattr(deep_resume.candidate_scope, 'key',
                        lambda net, proj, fn: header[0] + fn['best_source'])
    for score, gain in [(20, 0), (30, 3), (40, 2), (50, 1)]:
        deep_resume.keep(net, proj, fn, str(score), score, [('r', 'a', 'b')], str(score), ['repair'], gain)
    roots = deep_resume.roots(net, proj, fn)
    assert [node['source'] for node in roots] == ['20', '30', '40']
    assert roots[0]['rows'] == [['r', 'a', 'b']]
    deep_resume.keep(net, proj, fn, 'another', 35, [], '30', ['repair'], 3)
    roots = deep_resume.roots(net, proj, dict(fn, best_source='20'))
    assert [node['source'] for node in roots] == ['another', '40']
    header[0] = 'changed'
    assert deep_resume.roots(net, proj, fn) == []


def test_near_register_focus_excludes_opcode_and_large_differences():
    from decompnet.app.allocation_search import near_registers

    row = ('r', 'lw $a1, 4($a0)', 'lw $v1, 4($a0)')
    assert near_registers([row], 'mips')
    assert not near_registers([row]*9, 'mips')
    assert not near_registers([('|', 'lw $a1, 4($a0)', 'lh $v1, 4($a0)')], 'mips')


def test_second_search_rejudges_and_expands_a_persisted_root(monkeypatch):
    from decompnet.app import deep_permuter, draft_repairs
    from tests.test_deep_permuter import Log, setup
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    proj = net.project_row('game')
    fn = net.function()
    net._comparison_digest = lambda *args: ''
    monkeypatch.setattr(deep_resume.candidate_scope, 'key', lambda *_: 'same inputs')
    rows = [('r', 'move $a0, $a1', 'move $v0, $a1')]
    deep_resume.keep(net, proj, fn, 'retained root', 45, rows, 'different output', ['first repair'], 1)
    search = deep_permuter.Search(net, 1, 30, Log(), fn)
    search.winner_diff = (rows, '')
    search._repair_preflight = lambda *args: 40
    attempted = []

    def candidates(source, *args, labels, **kwargs):
        if source != 'retained root':
            return []
        labels['exact child'] = 'second repair'
        return ['exact child']

    def measure(agent, project, current, source, best, fork):
        attempted.append(source)
        search._trial_clean = True
        search._trial_reused = False
        search._trial_output = source
        net._last_diffs = {(agent.id, current['id']): (rows, '')}
        if source == 'retained root':
            assert best == 0
            return 45
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status='matched',matched_source=?,best_score=0 WHERE id=?",
                       (source, current['id']))
        return 0

    monkeypatch.setattr(draft_repairs, 'variants', candidates)
    monkeypatch.setattr(search, '_score', measure)
    assert search.local_repairs(AGENT, proj, fn, 40, None, max_trials=15)[0] == 0
    assert attempted[:2] == ['retained root', 'exact child']


def test_scopes_are_computed_before_the_writer_is_taken(monkeypatch):
    """`keep` hashed every saved node's build inputs inside its write transaction, holding the project's single
    writer for most of a second per call."""
    import json
    from contextlib import contextmanager
    from types import SimpleNamespace
    state = {'open': False, 'inside': 0}

    class Store:
        row = {'feedback': json.dumps([dict(source='a', score=5, rows=[], output='o1', path='p', recovered=0, scope='s')])}

        def one(self, sql, *args):
            return self.row

        @contextmanager
        def tx(self):
            state['open'] = True
            try:
                yield SimpleNamespace(execute=lambda sql, params: Store.row.update(feedback=params[4]))
            finally:
                state['open'] = False

    def scope(net, proj, fn, source):
        state['inside'] += state['open']
        return 's'
    monkeypatch.setattr(deep_resume, '_scope', scope)
    net = SimpleNamespace(project_store=lambda pid: Store())
    deep_resume.keep(net, {'id': 1}, {'id': 2, 'best_source': ''}, 'b', 3, [], 'o2', 'path', 1)
    assert state['inside'] == 0
    assert [node['source'] for node in json.loads(Store.row['feedback'])] == ['b', 'a']
