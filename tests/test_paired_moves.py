"""The two-move search: pool judging, the beam, and the Deep Permuter pass that adopts its winner."""
import pytest

from decompnet.app import deep_permuter, judge_pool, paired_moves
from tests.test_deep_permuter import Log, setup
from tests.test_permute import AGENT

pytest.importorskip('decomprepair.paired_statement_moves')

SOURCE = '''extern int a, b, c, d;
void f(void) {
    int i;
    a = 1;
    b = 2;
    c = 3;
    d = 4;
    i = 0;
}'''


def order(source):
    return [line.strip() for line in source.splitlines() if line.startswith('    ') and '=' in line]


def target_after_two_moves():
    from decomprepair import paired_statement_moves as moves
    return moves.variants(SOURCE, 'f', limit=1000, seed=3)[17][1]


def test_the_beam_reaches_what_no_single_move_does():
    target = target_after_two_moves()
    singles_only = set()
    from decomprepair import paired_statement_moves as moves
    singles_only = set(moves.single_moves(SOURCE, 'f'))
    assert target not in singles_only

    def judge(candidates, check=lambda: None):
        return [50 if c == target else 120 for c in candidates]  # every single move is worse; the pair is better

    score, winner, judged = paired_moves.search(judge, SOURCE, 'f', '', 100)
    assert (score, winner) == (50, target) and judged > len(singles_only)


def test_nothing_better_returns_the_base_and_the_count():
    lines = []
    score, winner, judged = paired_moves.search(lambda cs, check=lambda: None: [150 for _ in cs], SOURCE, 'f', '', 100,
                                                log=lines.append)
    assert (score, winner) == (100, None) and judged > 0 and lines


def test_moves_that_change_nothing_are_not_expanded():
    expanded = []

    def judge(candidates, check=lambda: None):
        expanded.append(len(candidates))
        return [100 for _ in candidates]  # all neutral: the output did not change

    paired_moves.search(judge, SOURCE, 'f', '', 100)
    assert len(expanded) == 1  # only the single moves were judged; a neutral move leads nowhere new


def test_the_budget_and_seen_sources_are_respected():
    from decomprepair import paired_statement_moves as moves
    singles = moves.single_moves(SOURCE, 'f')
    batches = []

    def judge(candidates, check=lambda: None):
        batches.append(list(candidates))
        return [120 for _ in candidates]

    _, _, judged = paired_moves.search(judge, SOURCE, 'f', '', 100, budget=len(singles) + 5, seen=singles[:2])
    assert judged <= len(singles) + 5
    assert not set(singles[:2]) & set(batches[0])


def test_the_gate_wants_registers_and_order_not_a_missing_body():
    registers = [('r', f'addu $s{n}, $v0, $a1', f'addu $s{n + 1}, $v0, $a1') for n in range(6)]
    assert paired_moves.worth(registers + [('o', 'a', 'b'), ('o', 'c', 'd')])
    assert not paired_moves.worth([])
    assert not paired_moves.worth([('>', '', f'addu $t{n}, $v0, $a1') for n in range(8)])


class FakeWorker:
    def __init__(self, replies):
        self.replies = replies

    def ask(self, request):
        source = request['source']
        reply = self.replies(source)
        return reply

    def close(self):
        pass


def pool_with(replies, size=3):
    fn = {'name': 'f', 'vram': 0x80001000, 'target': b'\x00\x01'}
    pool = judge_pool.JudgePool('game', fn, 'gcc-2.7.2-kmc', ['-O2'], size=size)
    pool.all = [FakeWorker(replies) for _ in range(size)]
    pool.workers = judge_pool.queue.Queue()
    for worker in pool.all:
        pool.workers.put(worker)
    return pool


def test_the_pool_keeps_order_and_asks_with_the_functions_own_target():
    asked = []

    def replies(source):
        asked.append(source)
        return {'score': int(source)}

    pool = pool_with(replies)
    assert pool.request['function'] == {'name': 'f', 'vram': 0x80001000, 'target': '0001'}
    assert pool.request['compiler'] == {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}
    assert pool.judge_many([str(n) for n in range(200)], chunk=16) == list(range(200))
    assert sorted(asked) == sorted(str(n) for n in range(200))


def test_the_pool_reports_failures_as_none_and_keeps_the_error():
    def replies(source):
        return {'error': 'no project'} if source == 'bad' else None if source == 'dead' else {'score': 7}

    pool = pool_with(replies)
    assert pool.judge_many(['ok', 'bad', 'dead']) == [7, None, None]
    assert pool.errors == ['no project']


def test_the_pool_checks_for_cancellation_between_chunks():
    pool = pool_with(lambda source: {'score': 1})
    seen = []

    def check():
        seen.append(1)
        if len(seen) == 2:
            raise deep_permuter.SearchLimit('stop')

    with pytest.raises(deep_permuter.SearchLimit):
        pool.judge_many(['x'] * 200, check=check, chunk=16)


class FakePool:
    created = []

    def __init__(self, slug, fn, toolchain, flags, **kw):
        self.args = (slug, fn['name'], toolchain, flags)
        self.errors = []
        FakePool.created.append(self)

    def judge_many(self, sources, check=lambda: None):
        check()
        return [120 for _ in sources]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def search_for(monkeypatch, source=SOURCE, minutes=5, limit=50):
    net = setup(monkeypatch)
    proj = net.project_row('game')
    fn = {**net.function(), 'name': 'f', 'asm': '', 'best_source': source}
    net._function = lambda name, slug: fn  # the pass reads the saved source again, as the real search does
    search = deep_permuter.Search(net, minutes, limit, Log())
    rows = [('r', f'addu $s{n}, $v0, $a1', f'addu $s{n + 1}, $v0, $a1') for n in range(6)] + [('o', 'a', 'b')]
    search.winner_diff = (rows, None)
    monkeypatch.setattr(deep_permuter, 'compiler_for', lambda cfg, vram, f: {'toolchain': 'gcc-2.7.2-kmc',
                                                                             'flags': '-O2 -G0'})
    monkeypatch.setattr(judge_pool, 'JudgePool', FakePool)
    FakePool.created.clear()
    return net, proj, fn, search


def test_the_pass_adopts_a_winner_through_the_ordinary_judge(monkeypatch):
    net, proj, fn, search = search_for(monkeypatch)
    target = target_after_two_moves()
    monkeypatch.setattr(FakePool, 'judge_many',
                        lambda self, sources, check=lambda: None: [50 if s == target else 120 for s in sources])
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, p, f, child, best, fork: tried.append(child) or 50)
    result = search.paired_move_pass(AGENT, proj, fn, None, 100, None)
    assert tried and tried[0] == target and result <= 50
    assert FakePool.created[0].args == ('game', 'f', 'gcc-2.7.2-kmc', ['-O2', '-G0'])
    assert search.tested >= 1
    assert any('improved' in line for line in search.log.lines)


def test_the_pass_keeps_the_saved_source_when_the_ordinary_judge_disagrees(monkeypatch):
    net, proj, fn, search = search_for(monkeypatch)
    target = target_after_two_moves()
    monkeypatch.setattr(FakePool, 'judge_many',
                        lambda self, sources, check=lambda: None: [50 if s == target else 120 for s in sources])
    monkeypatch.setattr(search, '_score', lambda agent, p, f, child, best, fork: 100)
    assert search.paired_move_pass(AGENT, proj, fn, None, 100, None) == 100
    assert any('ordinary judge' in line for line in search.log.lines)


@pytest.mark.parametrize('why', ['fork', 'no rows', 'not register', 'out of time', 'out of trials'])
def test_the_pass_skips_what_it_cannot_help(monkeypatch, why):
    net, proj, fn, search = search_for(monkeypatch)
    fork = 'fork' if why == 'fork' else None
    if why == 'no rows':
        search.winner_diff = None
    if why == 'not register':
        search.winner_diff = ([('>', '', 'addu $t0, $v0, $a1')] * 8, None)
    if why == 'out of time':
        search.deadline = deep_permuter.time.monotonic() + 10
    if why == 'out of trials':
        search.tested = search.limit
    monkeypatch.setattr(search, '_score', lambda *a: pytest.fail('nothing to adopt'))
    assert search.paired_move_pass(AGENT, proj, fn, None, 100, fork) == 100
    assert not FakePool.created


def test_a_function_without_movable_statements_costs_one_cheap_look(monkeypatch):
    net, proj, fn, search = search_for(monkeypatch, source='void f(void) { a = 1; }')
    monkeypatch.setattr(search, '_score', lambda *a: pytest.fail('nothing to adopt'))
    assert search.paired_move_pass(AGENT, proj, fn, None, 100, None) == 100
