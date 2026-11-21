"""Per-function workers overlap without multiplying the configured allowance."""
import threading
from types import SimpleNamespace

import pytest

from decompnet.app import deep_parallel, deep_permuter
from decompnet.network import NetworkError
from tests.test_deep_permuter import Log, setup


@pytest.mark.parametrize('imported', [False, True])
def test_paths_overlap_share_budget_deadline_and_aggregate(monkeypatch, imported):
    net = setup(monkeypatch, imported)
    barrier = threading.Barrier(3)
    seen = []
    def path(net, slug, name, minutes, allowance, log, profiles, *, lane):
        lane.prepared()
        barrier.wait(timeout=5)
        seen.append((lane.index, allowance, lane.deadline))
        log.progress({'tested': allowance, 'best_score': 40-lane.index, 'best_percent': 50+lane.index})
        return {'tested': allowance, 'best_score': 40-lane.index, 'skipped': lane.index}
    monkeypatch.setattr(deep_permuter, '_run', path)
    log = Log()
    result = deep_permuter.run(net, 'game', 'f', 1, 11, log, threads_per_function=3)
    assert sorted((i, n) for i,n,_ in seen) == [(0,4), (1,4), (2,3)]
    assert len({d for _,_,d in seen}) == 1
    assert result['tested'] == 11 and result['skipped'] == 3
    assert result['threads_per_function'] == 3
    assert result['reason'] == 'Variant limit reached'
    assert max(u.get('tested', 0) for u in log.updates) == 11
    assert all(u['variant_limit'] == 11 for u in log.updates)


def test_preparation_precedes_other_paths_and_credentials_are_distinct(monkeypatch):
    net = setup(monkeypatch)
    agents = []
    net.local_agent = lambda name: (agents.append(name) or SimpleNamespace(id=len(agents), name=name), None)
    preparation = []
    preflight = []
    net._comparison_digest = lambda *a: ''
    monkeypatch.setattr('decompnet.app.source_preparation.run', lambda *a, **k: preparation.append('done') or {'tested':0, 'improved':0})
    monkeypatch.setattr('decompnet.app.matching_preflight.run', lambda *a, **k: preflight.append('done') or {'tested':0})
    ready = threading.Barrier(2)
    def baseline(self, *args):
        assert preparation == ['done'] and preflight == ['done']
        ready.wait(timeout=5)
        return 40, 0
    monkeypatch.setattr(deep_permuter.Search, '_baseline', baseline)
    monkeypatch.setattr(deep_permuter.Search, 'local_repairs', lambda *a, **k: (40, 'x'))
    def permute(self, agent, proj, fn, state):
        # The random phase must not mutate compiler settings underneath another path.
        self._compiler_preflight(proj, fn, state, self.deadline)
        assert preflight == ['done']
        state.tried = 1
        self.tested = self.limit
    monkeypatch.setattr(deep_permuter.Search, '_permute', permute)
    result = deep_permuter.run(net, 'game', 'f', 1, 10, Log(), threads_per_function=2)
    assert len(set(agents)) == 2 and all('-lane-' in a for a in agents)
    assert result['tested'] == 10


def test_exact_match_supersedes_inflight_guard_failure(monkeypatch):
    net = setup(monkeypatch)
    barrier = threading.Barrier(2)
    matched = threading.Event()
    def path(net, slug, name, minutes, allowance, log, profiles, *, lane):
        lane.prepared()
        barrier.wait(timeout=5)
        if lane.index:
            assert matched.wait(5)
            raise NetworkError('Search inputs changed while compiling; the function is no longer open.')
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status='matched',best_score=0")
        matched.set()
        return {'tested':1}
    monkeypatch.setattr(deep_permuter, '_run', path)
    result = deep_permuter.run(net, 'game', 'f', 1, 10, Log(), threads_per_function=2)
    assert result['matched'] and result['reason'] == 'Matched'
    # Outer duplicate protection is released even when another worker is superseded.
    assert not net._deep_run_locks[net.function()['id']].locked()


def test_failure_cancels_other_paths_and_does_not_record_completion(monkeypatch):
    net = setup(monkeypatch)
    barrier = threading.Barrier(2)
    ended = threading.Event()
    def path(net, slug, name, minutes, allowance, log, profiles, *, lane):
        lane.prepared()
        barrier.wait(timeout=5)
        if lane.index == 0:
            raise NetworkError('Compiler unavailable')
        try:
            while True:
                log.check_cancel()
        finally:
            ended.set()
    monkeypatch.setattr(deep_permuter, '_run', path)
    with pytest.raises(NetworkError, match='Compiler unavailable'):
        deep_permuter.run(net, 'game', 'f', 1, 10, Log(), threads_per_function=2)
    assert ended.is_set()


@pytest.mark.parametrize('value', [0, -1, True, 1.5, '2', 257])
def test_invalid_thread_counts(value):
    with pytest.raises(NetworkError, match='threads per function'):
        deep_parallel.validate_threads(value)


def test_cancellation_reaches_all_function_workers(monkeypatch):
    net = setup(monkeypatch)
    barrier = threading.Barrier(2)
    log = Log()
    ended = []
    def path(net, slug, name, minutes, allowance, output, profiles, *, lane):
        lane.prepared()
        barrier.wait(timeout=5)
        log.cancelled = True
        try:
            output.check_cancel()
        finally:
            ended.append(lane.index)
    monkeypatch.setattr(deep_permuter, '_run', path)
    with pytest.raises(RuntimeError, match='cancelled'):
        deep_permuter.run(net, 'game', 'f', 1, 10, log, threads_per_function=2)
    assert sorted(ended) == [0,1]


def test_one_completed_record_uses_whole_function_budget(monkeypatch):
    net = setup(monkeypatch)
    records = []
    net.deep_sweeps = SimpleNamespace(completed=lambda *a: records.append(a), improvement=lambda *a: None)
    monkeypatch.setattr('decompnet.candidate_scope.key', lambda *a: 'scope')
    def path(net, slug, name, minutes, allowance, log, profiles, *, lane):
        lane.prepared()
        return {'tested':allowance}
    monkeypatch.setattr(deep_permuter, '_run', path)
    deep_permuter.run(net, 'game', 'f', 5, 11, Log(), threads_per_function=3)
    assert len(records) == 1
    assert records[0][2:4] == (5,11)
    assert records[0][4]['tested'] == 11


def test_wrapped_deadline_is_not_multiplied_by_worker_count(monkeypatch):
    net = setup(monkeypatch)
    log = Log()
    log.maximum_seconds = 0
    monkeypatch.setattr(deep_permuter, '_run', lambda *a, **k: pytest.fail('expired budget'))
    result = deep_permuter.run(net, 'game', 'f', 1, 10, log, threads_per_function=3)
    assert result['reason'] == 'Time limit reached' and result['tested'] == 0


def test_maximum_mode_uses_detected_cores_without_multiplying_budget(monkeypatch):
    from decompnet import compiler
    from decompnet.app import deep_throughput
    net = setup(monkeypatch)
    monkeypatch.setattr(deep_throughput.os,'cpu_count',lambda:4)
    paths=[]
    def path(net,slug,name,minutes,allowance,log,profiles,*,lane):
        paths.append((lane.index,allowance,log.compile_pool))
        lane.prepared()
        return {'tested':allowance}
    monkeypatch.setattr(deep_permuter,'_run',path)
    initial=compiler._SLOTS.limit
    log=Log()
    result=deep_permuter.run(net,'game','f',1,11,log,maximum_throughput=True)
    assert len(paths)==4 and result['tested']==11
    assert sum(n for _,n,_ in paths)==11
    assert len({id(pool) for _,_,pool in paths})==1
    assert paths[0][2].count==4 and paths[0][2].maximum
    assert compiler._SLOTS.limit==initial and log.compile_pool is None


def test_stale_candidate_is_discarded_without_caching_and_can_be_rejudged(monkeypatch):
    from decompnet.app.permute import Permuter, StaleProjectGeneration
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    search = deep_permuter.Search(net, 1, 10, Log())
    search.parallel_lane = SimpleNamespace(index=1)
    proj, fn = net.project_row('game'), net.function()
    outcomes = [StaleProjectGeneration('stale symbols'), 42]
    def judge(*args):
        result = outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(Permuter, '_score', judge)
    assert search._score_current(AGENT, proj, fn, 'child', 40, None) is None
    assert not net.store.one('SELECT 1 FROM reuse_trials')
    assert search._score_current(AGENT, proj, fn, 'child', 40, None) == 42
    assert search.tested == 2


def test_call_result_pass_regenerates_from_saved_best_and_obeys_budget(monkeypatch):
    from decompnet.app.permute import Permuter
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    proj, fn = net.project_row('game'), net.function()
    first = fn['best_source']
    monkeypatch.setattr('decomprepair.target_local_repairs.call_result_pass',
                        lambda source, name: [('call_result_reuse', source + 'x')])
    def judge(self, agent, proj, fn, source, best, fork):
        with net.store.tx() as db:
            db.execute('UPDATE functions SET best_score=?,best_source=?', (best-1, source))
        return best-1
    monkeypatch.setattr(Permuter, '_score', judge)
    search = deep_permuter.Search(net, 1, 6, Log())
    assert search.call_result_pass(AGENT, proj, fn, 40, None) == 37
    assert search.tested == 3 and net.function()['best_source'] == first + 'xxx'


def test_parallel_baseline_refreshes_after_generation_change(monkeypatch):
    from decompnet.app.permute import StaleProjectGeneration
    from tests.test_permute import AGENT

    baseline = deep_permuter.Search._baseline
    net = setup(monkeypatch)
    search = deep_permuter.Search(net, 1, 10, Log())
    search.parallel_lane = SimpleNamespace(index=0)
    seen = []
    def compile_baseline(self, agent, proj, fn, state, fork):
        seen.append(fn['best_source'])
        if len(seen) == 1:
            with net.store.tx() as db:
                db.execute("UPDATE functions SET best_source='new'")
            raise StaleProjectGeneration('stale baseline')
        return 39, 1
    monkeypatch.setattr(deep_permuter.Search, '_baseline_work', compile_baseline)
    original = net.function()
    assert baseline(search, AGENT, net.project_row('game'), original, None, None) == (39, 1)
    assert seen == [original['best_source'], 'new']


def test_empty_call_result_pass_keeps_remeasured_baseline(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    search = deep_permuter.Search(net, 1, 10, Log())
    fn = net.function()
    assert search.call_result_pass(AGENT, net.project_row('game'), fn, 115, None) == 115
    assert search.tested == 0


def test_indexed_loop_frame_pass_tries_combined_source_first(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    source = '''extern u32 count;
extern void *items[];
void consume(void *);

void f(void) {
    u8 __repair_frame[8];
    u32 i;
    void **cursor;

    i = 0;
    if (count != 0) {
        cursor = items;
        do {
            consume(*cursor);
            i++;
            cursor++;
        } while (i < count);
    }
}
'''
    fn = {**net.function(), 'name': 'f', 'best_source': source}
    search = deep_permuter.Search(net, 1, 1, Log())
    tried = []

    def judge(agent, proj, fn, candidate, best, fork):
        tried.append(candidate)
        search.tested += 1
        return 0

    monkeypatch.setattr(search, '_score', judge)
    assert search.indexed_loop_frame_pass(AGENT, net.project_row('game'), fn, 35, None) == 0
    assert len(tried) == 1
    assert 'consume(items[i]);' in tried[0]
    assert '__repair_frame' not in tried[0]


def test_packed_word_store_scope_runs_with_mixed_operand_evidence(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    proj = net.project_row('game')
    fn = {**net.function(),
          'name': 'f',
          'asm': 'sll $v1, $a1, 16\nsll $v0, $a1, 8\n',
          'best_source': ('void f(Entry *entry, s16 value) {\n'
                          '    u32 shade = (s32)value;\n'
                          '    entry->color.word = (((0xff000000 | (shade << 8)) | (shade << 16)) | shade);\n'
                          '}')}
    search = deep_permuter.Search(net, 1, 6, Log())
    monkeypatch.setattr(deep_permuter.permuter_focus, 'classify', lambda *args: 'operand')
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, project, function, child, best, fork: tried.append(child) or 30)

    assert search.operand_order_pass(AGENT, proj, fn, 95, None) == 30
    assert len(tried) == 1
    assert 'do { entry->color.word = (((0xff000000 | (shade << 16)) | (shade << 8)) | shade); }' in tried[0]


def test_float_stack_call_reuse_runs_when_the_diff_has_extra_loads(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    proj = net.project_row('game')
    fn = {**net.function(), 'asm': 'lwc1 $fa0, 0($s0)'}
    search = deep_permuter.Search(net, 1, 6, Log())
    monkeypatch.setattr(deep_permuter.permuter_focus, 'classify', lambda *args: 'instruction')
    monkeypatch.setattr('decomprepair.float_stack_call_reuse.variants',
                        lambda *args: [('float_stack_call_reuse', 'shared float loads')])
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 0)

    assert search.operand_order_pass(AGENT, proj, fn, 235, None) == 0
    assert tried == ['shared float loads']


def test_scaled_local_trial_runs_on_shift_size_mismatch(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    proj = net.project_row('game')
    fn = {**net.function(), 'name': 'f',
          'asm': 'addiu $s0, $zero, 0x4000\n.Ljoin:\nsll $s0, $s0, 3\n',
          'best_source': ('void f(void) {\n'
                          '    short size;\n'
                          '    size = 0x800;\n'
                          '    if (expanded != 0) { size = 0x4000; }\n'
                          '    size *= 8;\n'
                          '    buffer = allocate(size, 0);\n'
                          '}')}
    search = deep_permuter.Search(net, 1, 6, Log())
    monkeypatch.setattr(deep_permuter.permuter_focus, 'classify', lambda *args: 'operand')
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 0)

    assert search.operand_order_pass(AGENT, proj, fn, 130, None) == 0
    assert len(tried) == 1
    assert '__repair_scaled_size = size * 8;' in tried[0]


def test_inline_float_scale_runs_before_broad_rewriting(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    proj = net.project_row('game')
    fn = {**net.function(), 'name': 'f',
          'asm': ('lwc1 $f2, 0($a0)\nmul.s $f2, $f2, $f0\n'
                  'lwc1 $f8, 4($a0)\nmul.s $f8, $f8, $f0\n'
                  'lwc1 $f4, 8($a0)\nmul.s $f4, $f4, $f0\n'),
          'best_source': ('void f(f32 *v) {\n'
                          '    f32 x = v[0] * 0.5f;\n'
                          '    f32 y = v[1] * 0.5f;\n'
                          '    f32 z = v[2] * 0.5f;\n'
                          '    use(x, y, z);\n'
                          '}')}
    search = deep_permuter.Search(net, 1, 6, Log())
    monkeypatch.setattr(deep_permuter.permuter_focus, 'classify', lambda *args: 'register')
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 0)

    assert search.operand_order_pass(AGENT, proj, fn, 35, None) == 0
    assert len(tried) == 1
    assert '__repair_float_mul(v[2], 0.5f)' in tried[0]


def test_guard_xor_trial_runs_on_an_opcode_near_miss(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    proj = net.project_row('game')
    fn = {**net.function(), 'name': 'f',
          'asm': 'xori $v0, $s1, 0x20\nsltu $v0, $zero, $v0\n',
          'best_source': ('void f(void) {\n'
                          '    s32 key;\n'
                          '    s32 changed;\n'
                          '    key = read_key();\n'
                          '    changed = key ^ 0x20;\n'
                          '    if (key == 0x26) { different = key != 0x20; }\n'
                          '}')}
    search = deep_permuter.Search(net, 1, 6, Log())
    monkeypatch.setattr(deep_permuter.permuter_focus, 'classify', lambda *args: 'instruction')
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 215)

    assert search.operand_order_pass(AGENT, proj, fn, 410, None) == 215
    assert len(tried) == 1
    assert 'different = changed != 0;' in tried[0]


def test_scoped_byte_read_trial_recognizes_a_saved_address_named_draft(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    proj = net.project_row('game')
    fn = {**net.function(), 'name': 'renamed', 'vram': 0x80001000,
          'asm': ('lui $s2, %hi(options)\naddiu $s2, $s2, %lo(options)\n'
                  'lbu $v0, 0($s2)\nbeqz $v0, .Lskip\nmove $a0, $s1\n'
                  'jal send\nnop\nlbu $v0, 0($s2)\n'),
          'best_source': ('extern u8 options[];\nvoid func_80001000(int channel) {\n'
                          '    if (options[0] != 0) {\n'
                          '        send(channel);\n'
                          '        if (options[0] >= 2U) { reset(channel); }\n'
                          '    }\n}\n')}
    search = deep_permuter.Search(net, 1, 6, Log())
    monkeypatch.setattr(deep_permuter.permuter_focus, 'classify', lambda *args: 'operand')
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 35)

    assert search.operand_order_pass(AGENT, proj, fn, 75, None) == 35
    assert len(tried) == 1
    assert tried[0].count('__repair_byte_table[0]') == 2


def test_scoped_cursor_entry_runs_before_broad_rewriting(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    proj = net.project_row('game')
    fn = {**net.function(), 'name': 'f', 'asm': 'addiu $s2, $s7, 0xc\n.Lscan:\nlw $s1, 0($s2)\n',
          'best_source': ('void f(char *base) {\n'
                          '    u16 **cursor;\n'
                          '    char *item;\n'
                          '    cursor = (u16 **) (base + 0xC);\n'
                          'scan:\n'
                          '    item = (char *) (*cursor);\n'
                          '    if (accept(item)) return;\n'
                          '    cursor += 3;\n'
                          '    goto scan;\n}\n')}
    search = deep_permuter.Search(net, 1, 6, Log())
    monkeypatch.setattr(deep_permuter.permuter_focus, 'classify', lambda *args: 'operand')
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 110)

    assert search.operand_order_pass(AGENT, proj, fn, 130, None) == 110
    assert len(tried) == 1
    assert 'do { cursor = (u16 **) (base + 0xC); } while (0);' in tried[0]


def test_global_reset_trials_try_the_combination_before_the_scope_only_variant(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    proj = net.project_row('game')
    fn = {**net.function(), 'asm': ''}
    search = deep_permuter.Search(net, 1, 6, Log())
    monkeypatch.setattr(deep_permuter.permuter_focus, 'classify', lambda *args: 'operand')
    monkeypatch.setattr('decomprepair.scoped_global_reset.variants', lambda *args: [
        ('scoped_global_reset', 'combined reset'), ('scoped_global_reset', 'scoped reset'),
    ])
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 395)

    assert search.operand_order_pass(AGENT, proj, fn, 855, None) == 395
    assert tried == ['combined reset']


def test_guarded_indexed_load_uses_current_relocation_name(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    proj = net.project_row('game')
    fn = {**net.function(), 'name': 'f', 'asm': 'lbu $v0, %lo(D_801001A1)($v0)',
          'best_source': ('extern s32 slots;\n'
                          'int f(int i) { return *((s32 *) (((s8 *) (&slots)) + i * 24)) == 0; }')}
    monkeypatch.setattr(net, 'rename_tokens', lambda project, asm: asm.replace('D_801001A1', 'slots'), raising=False)
    search = deep_permuter.Search(net, 1, 6, Log())
    monkeypatch.setattr(deep_permuter.permuter_focus, 'classify', lambda *args: 'instruction')
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 1110)

    assert search.operand_order_pass(AGENT, proj, fn, 1310, None) == 1110
    assert len(tried) == 1
    assert '*((u8 *)' in tried[0]


def test_counter_derived_offset_trial_runs_on_schedule_differences(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    proj = net.project_row('game')
    fn = {**net.function(), 'name': 'f',
          'asm': ('sw $a0, %lo(table)($at)\naddiu $a1, $a1, 1\nslti $v0, $a1, 32\n'
                  'bnez $v0, .Lloop\naddiu $v1, $v1, 40\n'),
          'best_source': ('void f(void) { s32 i; s32 offset; i = 0; offset = 0;\n'
                          '    do { *(s32 *)(table + offset) = 0; i += 1; offset += 40; } while (i < 32);\n'
                          '}')}
    search = deep_permuter.Search(net, 1, 6, Log())
    monkeypatch.setattr(deep_permuter.permuter_focus, 'classify', lambda *args: 'order')
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 0)

    assert search.operand_order_pass(AGENT, proj, fn, 60, None) == 0
    assert len(tried) == 1
    assert 'offset = (i << 5) + (i << 3);' in tried[0]


def test_word_byte_loop_carrier_runs_before_random_rewrites(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    proj = net.project_row('game')
    fn = {**net.function(), 'name': 'f',
          'asm': ('addu $t0, $a1, $zero\nandi $a3, $t0, 255\n'
                  'addu $v1, $a3, $zero\nlbu $v0, 8($a2)\nbeq $v0, $v1, .Lfound\n'),
          'best_source': ('int f(u8 *items, u8 key) { s16 i; u8 probe;\n'
                          'if (items != 0) { probe = key; if (probe == 255) return -1; i = 0;\n'
                          'do { if (items[i] == probe) return i; i++; } while (i < 16); } return -1; }')}
    search = deep_permuter.Search(net, 1, 6, Log())
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 0)

    assert search.operand_order_pass(AGENT, proj, fn, 60, None) == 0
    assert len(tried) == 1
    assert 's32 probe;' in tried[0]
    assert 'items[i] == (u8)key' in tried[0]


def test_scoped_byte_global_update_uses_current_relocation_names(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    proj = net.project_row('game')
    net.rename_tokens = lambda project, text: text.replace('D_80001234', 'state')
    fn = {**net.function(), 'name': 'f',
          'asm': ('lui $v1, %hi(D_80001234)\naddiu $v1, $v1, %lo(D_80001234)\n'
                  'lbu $v0, 0($v1)\naddiu $v0, $v0, -1\nsb $v0, 0($v1)\n'),
          'best_source': 'extern u8 state; void f(void) { state -= 1; }'}
    search = deep_permuter.Search(net, 1, 6, Log())
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 30)

    assert search.operand_order_pass(AGENT, proj, fn, 255, None) == 30
    assert len(tried) == 1
    assert '= &state; *__repair_byte_address -= 1;' in tried[0]


def test_stack_byte_loop_base_runs_in_the_bounded_lane(monkeypatch):
    from decomprepair import stack_byte_loop_base
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    proj = net.project_row('game')
    fn = {**net.function(), 'asm': ''}
    child = fn['best_source'] + '\n/* staged byte buffer */'
    monkeypatch.setattr(stack_byte_loop_base, 'variants', lambda *args: [('stack_byte_loop_base', child)])
    search = deep_permuter.Search(net, 1, 6, Log())
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 630)

    assert search.operand_order_pass(AGENT, proj, fn, 705, None) == 630
    assert tried == [child]
