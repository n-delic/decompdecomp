"""Bounded callee experiments retain useful non-best sources without relaxing either verdict."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from decompnet.app import callee_probes as probes
from decompnet.scoring import DiffResult

from .test_permute import AGENT, FakeNet, batches

BASE = 'extern void adjust(void *, float, float); void f(void *p) { adjust(p, 35.0f, -51.0f); }'
CORRECTED = BASE.replace('35.0f, -51.0f', '-51.0f, 35.0f')
HELPER = 'unsigned int adjust(float *p, float goal, float speed) { *p = goal + speed; return 0; }'


class Net(FakeNet):
    def __init__(self):
        super().__init__()
        self.compiles, self.records, self.evaluations = [], [], []
        self.header, self.helper_valid, self.matches, self.errors = '', True, True, []
        self.markers = ['r']
        with self.store.tx() as db:
            db.execute("UPDATE functions SET best_source=?,best_score=10,target=?,relocs='[]' WHERE id=1", (BASE, b'caller'))
            db.execute("INSERT INTO functions(id,project_id,name,vram,n_insns,size,status,matched_source,target,relocs) "
                       "VALUES(2,1,'adjust',2147483904,5,20,'matched',?,?,'[]')", (HELPER, b'helper'))
            db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,source,created_at,context_version) '
                       'VALUES(1,1,30,1,?,0,1)', (CORRECTED,))

    def xrefs(self, proj):
        return {'calls': {2147483648: {2147483904}}}

    def _context(self, project, unit):
        return {'header': self.header, 'version': 1}

    def _compile(self, proj, header, source, fn):
        self.compiles.append(source)
        return SimpleNamespace(ok=True, obj=source)

    def _target_obj(self, fn, proj):
        return None

    def _evaluate(self, fn, proj, source):
        self.evaluations.append(fn['name'])
        if fn['name'] == 'adjust':
            return DiffResult(0, 500, [], True), self.helper_valid, []
        exact = self.matches and 'static unsigned int adjust' in source and 'adjust(p, -51.0f, 35.0f)' in source
        return DiffResult(0 if exact else 30, 1000, [(m, 'target', 'candidate') for m in self.markers], exact), exact, self.errors

    def _record(self, fn, agent, score, source, version, matched, via):
        self.records.append((score, matched, source))
        with self.store.tx() as db:
            db.execute('UPDATE functions SET best_score=?,best_source=?,status=? WHERE id=?',
                       (score, source, 'matched' if matched else 'open', fn['id']))


def run(net, **kwargs):
    return probes.probe(net, AGENT, net.project_row('game'), net.function(),
                        kwargs.get('stop', threading.Event()), kwargs.get('deadline', time.monotonic() + 10))


@pytest.fixture(autouse=True)
def fake_diff(monkeypatch):
    monkeypatch.setattr(probes.scoring, 'diff', lambda target, candidate: candidate)


def test_corrected_but_worse_attempt_matches_with_verified_callee():
    net = Net()
    matched, alternatives, summary = run(net)
    assert matched and not alternatives and 'adjust' in summary
    assert net.records[-1][:2] == (0, True)
    assert 'void *decomp_callee_arg_0' in net.records[-1][2]
    assert 'float * p = (float *)decomp_callee_arg_0' in net.records[-1][2]
    assert net.evaluations.count('adjust') == 2 and len(net.compiles) == 4


def test_matching_caller_cannot_hide_wrong_helper():
    net = Net()
    net.helper_valid = False
    matched, _, summary = run(net)
    assert not matched and not net.records and 'validation failed' in summary


def test_failed_probes_persist_and_context_changes_retry():
    net = Net()
    net.matches = False
    first = run(net)
    n = len(net.compiles)
    assert n == 4 and first[1]  # retains the helper context even when its score is worse
    assert '4 cached results' in run(net)[2] and len(net.compiles) == n
    net.header = 'typedef int NewType;'
    run(net)
    assert len(net.compiles) == n + 4
    with net.store.tx() as db:
        cfg = json.loads(net.project_row('game')['config'])
        cfg['compiler']['flags'] = ['-O3']
        db.execute('UPDATE projects SET config=? WHERE id=1', (json.dumps(cfg),))
    run(net)
    assert len(net.compiles) == n + 8
    with net.store.tx() as db:
        db.execute("UPDATE functions SET target=? WHERE id=2", (b'changed helper',))
    run(net)
    assert len(net.compiles) > n + 8


def test_link_errors_are_reported_instead_of_randomly_including_helpers():
    net = Net()
    net.errors = ['unresolved helper relocation']
    matched, alternatives, summary = run(net)
    assert not matched and not alternatives and len(net.compiles) == 2
    assert 'relocations' in summary and 'adjust' not in net.evaluations


@pytest.mark.parametrize('markers', [['<', '>'], ['|'], []])
def test_structural_diffs_do_not_trigger_callee_search(markers):
    net = Net()
    net.markers = markers
    assert not run(net)[0] and len(net.compiles) == 2


def test_budget_cancellation_and_project_mode(monkeypatch):
    net = Net()
    monkeypatch.setattr(probes, 'MAX_PROBES', 1)
    assert not run(net)[0] and len(net.compiles) == 3
    net.compiles.clear()
    stop = threading.Event(); stop.set()
    assert not run(net, stop=stop)[0] and not net.compiles
    assert not run(net, deadline=0)[0] and not net.compiles
    net.is_rom = lambda proj: False
    assert 'ROM' in run(net)[2] and not net.compiles


def test_mutation_of_a_verified_helper_is_checked_again():
    net = Net()
    source = probes.combine(CORRECTED, HELPER, 'adjust', '')
    assert probes.helpers_match(net, net.project_row('game'), net.function(), source, source)
    net.helper_valid = False
    assert not probes.helpers_match(net, net.project_row('game'), net.function(), source, source)


@pytest.mark.parametrize('source,helper,header', [
    (BASE, HELPER + '\nint other(void){return 0;}', ''),
    (BASE, HELPER, 'extern unsigned int adjust(float *,float,float);'),
    (BASE.replace('extern void adjust(void *, float, float);', HELPER), HELPER, ''),
    (BASE.replace('void *, float, float', 'void *, ...'), HELPER, ''),
    (BASE, HELPER.replace('float *p', 'float (*p)(int)'), ''),
])
def test_unsupported_declarations_are_left_to_agents(source, helper, header):
    assert probes.combine(source, helper, 'adjust', header) is None


def test_permuter_runs_diagnostic_before_random_rewrites(monkeypatch):
    from decompnet.app import permute

    net = Net()
    jobs = batches(monkeypatch, ['should not run'])
    state = permute.State()
    permute.Permuter(net)._permute(AGENT, net.project_row('game'), net.function(), state)
    assert state.matched == 1 and state.variants == 4 and jobs == []
    assert not net.store.all('SELECT * FROM claims')


def test_permuter_respects_existing_claim(monkeypatch):
    from decompnet.app import permute

    net = Net()
    with net.store.tx() as db:
        db.execute('INSERT INTO claims VALUES(1,1,0,99999999999)')
    batches(monkeypatch)
    permute.Permuter(net)._permute(AGENT, net.project_row('game'), net.function(), permute.State())
    assert net.compiles == [] and len(net.store.all('SELECT * FROM claims')) == 1


def test_alternative_contexts_reach_generator(monkeypatch):
    from decompnet.app import permute

    net = Net()
    net.matches = False
    jobs = batches(monkeypatch, ['unchanged'] , ['unchanged'], ['unchanged'], [])
    monkeypatch.setattr(permute.Permuter, '_score', lambda *args: 1000)
    permute.Permuter(net)._permute(AGENT, net.project_row('game'), net.function(), permute.State())
    assert jobs[0]['source'] == BASE
    assert 'static unsigned int adjust' in jobs[2]['source']


def test_cached_probes_are_shared_by_another_engine_instance():
    net = Net()
    net.matches = False
    run(net)
    other = Net()
    other.store = net.store
    other.matches = False
    run(other)
    assert other.compiles == []


def test_random_variant_cannot_save_a_match_with_a_changed_helper():
    from decompnet.app.permute import Permuter

    net = Net()
    net.helper_valid = False
    source = probes.combine(CORRECTED, HELPER, 'adjust', '')
    assert Permuter(net)._score(AGENT, net.project_row('game'), net.function(), source, 10, None) is None
    assert not net.records


def test_probe_cache_is_bounded(monkeypatch):
    net = Net()
    net.matches = False
    monkeypatch.setattr(probes, 'CACHE_ROWS', 2)
    run(net)
    assert len(net.store.all('SELECT * FROM callee_probes')) == 2


def test_symbol_changes_invalidate_link_results():
    net = Net()
    net.matches = False
    run(net)
    n = len(net.compiles)
    with net.store.tx() as db:
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,'data',4096,4,'data')")
    run(net)
    assert len(net.compiles) == n + 4


def test_fallback_visits_all_retained_contexts(monkeypatch):
    from decompnet.app import permute

    net = Net()
    monkeypatch.setattr(permute.callee_probes, 'probe', lambda *a, **kw: (False, ['one', 'two', 'three'], ''))
    monkeypatch.setattr(permute, 'available', lambda: False)
    seen = []
    monkeypatch.setattr(permute, 'mutate', lambda source, rng: seen.append(source))
    permute.Permuter(net)._permute(AGENT, net.project_row('game'), net.function(), permute.State())
    assert {'one', 'two', 'three'} <= set(seen)


def test_empty_generator_batch_does_not_discard_other_contexts(monkeypatch):
    from decompnet.app import permute

    net = Net()
    monkeypatch.setattr(permute.callee_probes, 'probe', lambda *a, **kw: (False, ['alternative'], ''))
    jobs = batches(monkeypatch, [], [], ['variant'], [])
    monkeypatch.setattr(permute.Permuter, '_score', lambda *a: 0)
    state = permute.State()
    permute.Permuter(net)._permute(AGENT, net.project_row('game'), net.function(), state)
    assert jobs[2]['source'] == 'alternative' and state.matched == 1


def test_brief_history_distinguishes_helper_failures_and_old_inputs():
    net = Net()
    assert probes.history(net, 1) == ''
    net.helper_valid = False
    run(net)
    summary = probes.history(net, 1)
    assert 'historical; inputs may have changed' in summary
    assert 'adjust' in summary and 'helper does not match' in summary


def test_compiler_failure_is_not_cached(monkeypatch):
    net = Net()
    compile_source = net._compile
    monkeypatch.setattr(net, '_compile', lambda *args: SimpleNamespace(ok=False))
    assert not run(net)[0] and not net.store.all('SELECT * FROM callee_probes')
    monkeypatch.setattr(net, '_compile', compile_source)
    assert run(net)[0]
