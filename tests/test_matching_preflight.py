"""Preflight budgets and cached evidence never replace the normal profile application judge."""
import hashlib
from types import SimpleNamespace

import pytest

from decompnet.app import matching_preflight as preflight
from decompnet.store import IN_MEMORY, Store


@pytest.fixture(autouse=True)
def installed_current_toolchain(monkeypatch):
    monkeypatch.setattr(preflight.toolchains, 'installed', lambda tid:{'fingerprint':'pinned'})


def setup():
    state = {'header':'typedef int word;', 'auto':False, 'calls':[], 'applied':[], 'suggested':[]}
    fn = {'id':1, 'name':'f', 'status':'open', 'best_source':'int f(void){return value;}',
          'best_score':50, 'vram':4096, 'size':16, 'unit':''}
    cfg = {'compiler':{'toolchain':'example', 'flags':['-O2']}}
    proj = {'id':1, 'slug':'game'}
    def judge(proj, fn, toolchain, flags, source):
        state['calls'].append(flags)
        score = 0 if flags == ['-O3'] else 50
        return score == 0, SimpleNamespace(score=score), ''
    net = SimpleNamespace(store=Store(IN_MEMORY), fork_slug=lambda s:None, is_rom=lambda p:True,
                          cfg=lambda p:cfg, project_row=lambda s:proj, _function=lambda *a:fn,
                          flag_variants=lambda *a:[['-O0'],['-O1'],['-O2'],['-O3'],['-O2','-g']],
                          _comparison_digest=lambda p,f,s,c:hashlib.sha256(repr((s,c,state['header'])).encode()).hexdigest(),
                          _judge_with=judge, auto_adjusts=lambda p:state['auto'],
                          apply_function_compiler=lambda *a:state['applied'].append(a),
                          local_agent=lambda n:(object(),None), suggest=lambda *a,**k:state['suggested'].append(k))
    net.project_store = lambda _: net.store
    return net, proj, fn, state


def test_probe_order_is_bounded_cached_and_rechecks_changed_source_or_context():
    net, proj, fn, state = setup()
    result = preflight.run(net, proj, fn)
    assert result['tested'] == 4 and result['best']['exact']
    assert state['calls'] == [['-O2'],['-O0'],['-O1'],['-O3']]
    assert not state['applied'] and len(state['suggested']) == 1
    assert preflight.run(net,proj,fn)['cached']
    assert len(state['calls']) == 4 and len(state['suggested']) == 1
    fn['best_source'] += '\n'
    preflight.run(net,proj,fn)
    state['header'] += '\nextern int value;'
    preflight.run(net,proj,fn)
    assert len(state['calls']) == 12


def test_application_uses_existing_revalidation_gate_and_respects_the_budget():
    net, proj, fn, state = setup();state['auto'] = True
    preflight.run(net,proj,fn,max_profiles=1)
    assert len(state['calls']) == 1 and not state['applied']
    preflight.run(net,proj,fn)
    assert state['applied'] == [('game','f','example',['-O3'])]


def test_cancellation_is_not_cached_and_unresolved_symbols_skip_other_profiles():
    net, proj, fn, state = setup()
    def cancel():raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):
        preflight.run(net,proj,fn,check=cancel)
    assert not net.store.one('SELECT 1 FROM reuse_trials')
    net._judge_with = lambda *a:(False,SimpleNamespace(score=0),'`missing` is not a known symbol.')
    result = preflight.run(net,proj,fn)
    assert result['tested'] == 1 and result['link']['symbols'] == ['missing']
    assert not state['applied'] and not state['suggested']


def test_preflight_batches_completed_probes_and_retries_an_interrupted_probe(monkeypatch):
    from contextlib import contextmanager

    net, proj, fn, state = setup()
    original_tx = net.store.tx
    writes = []

    @contextmanager
    def count_tx(*args, **kwargs):
        writes.append(1)
        with original_tx(*args, **kwargs) as db:
            yield db

    monkeypatch.setattr(net.store, 'tx', count_tx)

    def cancel():
        if len(state['calls']) >= 3:
            raise RuntimeError('cancelled')

    with pytest.raises(RuntimeError, match='cancelled'):
        preflight.run(net, proj, fn, check=cancel)
    assert len(writes) == 1
    assert net.store.one("SELECT COUNT(*) AS n FROM reuse_trials WHERE outcome='profile'")['n'] == 2
    writes.clear()
    result = preflight.run(net, proj, fn, max_profiles=1)
    assert result['tested'] == 1
    assert state['calls'] == [['-O2'], ['-O0'], ['-O1'], ['-O1']]
    assert len(writes) == 1


def test_imported_projects_own_their_flags_and_stopped_searches_are_retryable():
    net, proj, fn, state = setup()
    assert preflight.run(net,proj,fn,check=lambda:False)['tested'] == 0
    assert not net.store.one('SELECT 1 FROM reuse_trials')
    net.fork_slug = lambda s:'imported'
    assert preflight.run(net,proj,fn)['tested'] == 0 and not state['calls']


def test_missing_compilers_are_not_installed_and_current_exact_profiles_are_preserved(monkeypatch):
    net, proj, fn, state = setup()
    monkeypatch.setattr(preflight.toolchains, 'installed', lambda tid:None)
    assert preflight.run(net,proj,fn)['tested'] == 0 and not state['calls']
    monkeypatch.setattr(preflight.toolchains, 'installed', lambda tid:{'fingerprint':'pinned'})
    net._judge_with = lambda *a:(True,SimpleNamespace(score=0),'')
    recovered = []

    def save(agent, project, candidate, source, **kwargs):
        recovered.append((source, kwargs))
        candidate['status'] = 'matched'

    net.try_quietly = save
    result = preflight.run(net, proj, fn)
    assert result['tested'] == 1 and result['applied']
    assert recovered == [(fn['best_source'], {'synchronize':False, 'exact_only':True})]
    assert not state['applied'] and not state['suggested']


def test_similarity_alone_does_not_automatically_change_a_compiler_profile():
    net, proj, fn, state = setup();state['auto'] = True
    net._judge_with = lambda p,f,t,flags,s:(False,SimpleNamespace(score=20 if flags==['-O3'] else 50),'')
    result=preflight.run(net,proj,fn)
    assert result['best']['score']==20 and not result['best']['exact']
    assert not state['applied'] and len(state['suggested'])==1


def test_rejected_partial_suggestion_keeps_working_profile_and_returns_to_search():
    from decompnet.network import NetworkError
    net,proj,fn,state=setup()
    net._judge_with=lambda p,f,t,flags,s:(False,SimpleNamespace(score=20 if flags==['-O3'] else 50),'')
    def reject(*args,**kwargs):raise NetworkError('not suggested: the C does not match')
    net.suggest=reject;logs=[]
    result=preflight.run(net,proj,fn,log=logs.append)
    assert 'not suggested' in result['suggestion_rejected']
    assert net.cfg(proj)['compiler']['flags']==['-O2'] and not state['applied']
    assert any('continue source search' in line for line in logs)
    assert preflight.run(net,proj,fn)['tested']==1  # Continue the remaining debug profile.
    assert preflight.run(net,proj,fn)['cached']


def test_unexpected_suggestion_failure_is_not_swallowed():
    from decompnet.network import NetworkError
    net,proj,fn,state=setup()
    def fail(*args,**kwargs):raise NetworkError('project was removed')
    net.suggest=fail
    with pytest.raises(NetworkError,match='project was removed'):preflight.run(net,proj,fn)


def test_grouped_word_transfer_prioritizes_existing_copy_repairs():
    from decompnet.app.repair_ranking import matching_topics, order
    rows = [(' ', f'lw $t{i}, {i*4}($a0)', f'lw $t{i}, {i*4}($a0)') for i in range(3)]
    rows += [('o', f'sw $t{i}, {i*4}($a1)', f'sw $t{i}, {i*4}($a1)') for i in range(3)]
    assert matching_topics(rows) == ['Grouped copies']
    assert order(['local_width', 'global_record_copy'], rows) == ['global_record_copy', 'local_width']


def test_one_profile_rounds_resume_durably_instead_of_retesting_first_choices():
    net,proj,fn,state=setup()
    assert preflight.run(net,proj,fn,max_profiles=1)['pending']==4
    assert preflight.run(net,proj,fn,max_profiles=1)['pending']==3
    assert preflight.run(net,proj,fn,max_profiles=1)['pending']==2
    last=preflight.run(net,proj,fn,max_profiles=1)
    assert last['best']['exact'] and last['pending']==0
    assert state['calls']==[['-O2'],['-O0'],['-O1'],['-O3']]
    assert len(state['suggested'])==1
    assert preflight.run(net,proj,fn,max_profiles=1)['cached']
    fn['best_source']+=' /* improved source */'
    assert preflight.run(net,proj,fn,max_profiles=1)['tested']==1
    assert state['calls'][-1]==['-O2']


def test_completed_profiles_survive_a_stopped_later_probe():
    net,proj,fn,state=setup();checks=[]
    def check():
        checks.append(True)
        return len(checks)<=2
    assert preflight.run(net,proj,fn,check=check)['tested']==1
    result=preflight.run(net,proj,fn)
    assert result['best']['exact'] and result['tested']==3
    assert state['calls']==[['-O2'],['-O0'],['-O1'],['-O3']]


def test_promising_candidates_test_debug_profiles_before_optimization():
    net,proj,fn,state=setup()
    cfg=net.cfg(proj);cfg['compiler']={'toolchain':'gcc-2.7.2-kmc','flags':['-O2','-g3']}
    fn['n_insns']=100;fn['best_score']=500
    net.debug_flag_variants=lambda tid,flags:[flags,['-O2'],['-O2','-g']]
    net._judge_with=lambda p,f,t,flags,s:(flags==['-O2'],SimpleNamespace(score=0 if flags==['-O2']else 500),'')
    state['auto']=True;seen=[]
    judge=net._judge_with
    def record(*args):seen.append(args[3]);return judge(*args)
    net._judge_with=record
    result=preflight.run(net,proj,fn,max_profiles=2)
    assert result['best']['exact'] and result['applied']
    assert seen==[['-O2','-g3'],['-O2']]


def test_captured_profiles_are_ranked_but_rejudged_with_current_inputs():
    import json

    from decompnet.app.profile_hypotheses import captured
    net,proj,fn,state=setup();net.repair_feedback=object()
    with net.store.tx() as db:
        db.execute('CREATE TABLE repair_profile_evidence(example_id INTEGER PRIMARY KEY, project_id INTEGER, profile TEXT, before_hash TEXT, after_hash TEXT)')
        for flags in (['-O1'],['-O3'],['-O3']):
            profile={'toolchain':'example','flags':flags}
            db.execute('INSERT INTO repair_profile_evidence(project_id,profile,before_hash,after_hash) VALUES(?,?,?,?)',
                       (proj['id'],json.dumps(profile),'same','same'))
    assert captured(net,proj,net.cfg(proj)['compiler'])==[['-O3'],['-O1']]
    assert preflight.run(net,proj,fn,max_profiles=2)['best']['exact']
    assert state['calls']==[['-O2'],['-O3']]
    assert not state['applied']


def test_kmc_scheduler_orders_single_and_paired_passes_by_evidence(monkeypatch):
    from decompnet.app import allocation_evidence
    from decompnet.app.profile_hypotheses import kmc_priorities

    own = {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O1']}
    rows = [('o', 'sw $ra, 24($sp)', 'sw $ra, 24($sp)')]
    diagnosis = {'scheduling': True, 'delay_slot': False}
    monkeypatch.setattr(allocation_evidence, 'diagnose', lambda rows: diagnosis)
    single = ('-O1', '-fschedule-insns2')
    combined = ('-O1', '-fschedule-insns', '-fschedule-insns2')
    assert kmc_priorities(own, '', rows) == [single, combined]
    diagnosis['delay_slot'] = True
    assert kmc_priorities(own, '', rows) == [combined, single]


def test_kmc_sqrt_trial_stays_review_only_even_with_auto_adjust(monkeypatch):
    from decompnet.app import repair_ranking

    net, proj, fn, state = setup()
    net.cfg(proj)['compiler']['toolchain'] = 'gcc-2.7.2-kmc'
    fn['asm'] = 'sqrt.s $f0, $f12'
    state['auto'] = True
    monkeypatch.setattr(repair_ranking, 'evidence', lambda *a: [
        ('>', '', 'c.eq.s $f0, $f0'), ('>', '', 'jal sqrtf'),
    ])
    net._judge_with = lambda p,f,t,flags,s: (
        '-ffast-math' in flags, SimpleNamespace(score=1120), '',
    )
    assert preflight.run(net, proj, fn)['best']['exact']
    assert not state['applied']
    assert state['suggested'][0]['review_only']
    assert state['suggested'][0]['flags'] == '-O2 -ffast-math'


def test_four_word_copy_ranking_requires_contiguous_transfers():
    from decompnet.app.repair_ranking import matching_topics, order

    rows = [(' ', f'lw $t{i}, {i*4}($a0)', f'lw $t{i}, {i*4}($a0)') for i in range(4)]
    rows += [('o', f'sw $t{i}, {i*4}($a1)', f'sw $t{i}, {i*4}($a1)') for i in range(4)]
    assert order(['local_width', 'word_block_copy'], rows)[0] == 'word_block_copy'
    rows[-1] = ('o', 'sw $t3, 20($a1)', '')
    assert 'Grouped copies' not in matching_topics(rows)


@pytest.mark.parametrize("case", ["sqrt", "prologue", "delay-slot"])
def test_kmc_fresh_baseline_prioritizes_targeted_profiles_on_resume(case):
    net, proj, fn, state = setup()
    base, trial, assembly, rows, problem = {
        "sqrt": (
            ["-O2"], ["-O2", "-ffast-math"], "sqrt.s $f0, $f12",
            [('>', '', 'c.eq.s $f0, $f0'), ('>', '', 'jal sqrtf')],
            "`sqrtf` is not a known symbol.",
        ),
        "prologue": (
            ["-O1"], ["-O1", "-fschedule-insns2"], "",
            [('o', 'sw $ra, 20($sp)', 'sw $ra, 20($sp)')], "",
        ),
        "delay-slot": (
            ["-O2", "-g3"], ["-O2"], "",
            [(' ', 'jr $ra', 'jr $ra'), ('|', 'addiu $sp, $sp, 16', 'nop')], "",
        ),
    }[case]
    net.cfg(proj)['compiler'] = {'toolchain': 'gcc-2.7.2-kmc', 'flags': base}
    fn['asm'] = assembly
    state['auto'] = True

    def judge(project, function, toolchain, flags, source):
        assert source == fn['best_source']
        state['calls'].append(flags)
        if flags == base:
            return False, SimpleNamespace(score=1120, rows=rows), problem
        assert flags == trial
        return True, SimpleNamespace(score=0), ''

    net._judge_with = judge
    assert preflight.run(net, proj, fn, max_profiles=1)['pending']
    result = preflight.run(net, proj, fn, max_profiles=1)
    assert result['best']['exact'] and 'link' not in result
    assert state['calls'] == [base, trial]
    if case == 'sqrt':
        assert state['suggested'][0]['review_only'] and not state['applied']
    else:
        assert result['applied']


def test_kmc_float_sqrt_repairs_source_and_flags_together(monkeypatch):
    from decompnet.app import repair_ranking

    net, proj, fn, state = setup()
    net.cfg(proj)['compiler']['toolchain'] = 'gcc-2.7.2-kmc'
    fn.update(asm='sqrt.s $f0, $f0', best_source='extern float sqrt(float);\nfloat f(float x) { return sqrt(x); }')
    state['auto'] = True
    monkeypatch.setattr(repair_ranking, 'evidence', lambda *a: [])

    def judge(proj, fn, toolchain, flags, source):
        state['calls'].append((flags, source))
        if 'sqrtf(' in source and '-ffast-math' in flags:
            return True, SimpleNamespace(score=0), ''
        return False, SimpleNamespace(score=1120), '`sqrtf` is not a known symbol.' if 'sqrtf(' in source else ''

    net._judge_with = judge
    result = preflight.run(net, proj, fn)
    assert result['best']['exact'] and result['review_only']
    assert len(state['calls']) == 3
    assert state['calls'][1][0] == ['-O2']
    assert not state['applied']
    assert state['suggested'][0]['review_only'] and 'sqrtf(' in state['suggested'][0]['source']


def test_signed_halfword_word_store_pair_runs_before_isolated_flags(monkeypatch):
    from decompnet.app import profile_hypotheses

    net, proj, fn, state = setup()
    proj['arch'] = 'mips'
    net.cfg(proj)['compiler']['toolchain'] = 'gcc-2.7.2-kmc'
    fn.update(best_source='void f(void) { v = *(volatile s32 *)(in + i); *(s32 *)(out + j) = v; }',
              asm='lh $v0, %lo(in)($v1)\nsw $v0, %lo(out)($at)')
    monkeypatch.setattr(profile_hypotheses, 'kmc_priorities', lambda *args: [['-O1']])

    def judge(proj, fn, toolchain, flags, source):
        state['calls'].append((flags, source))
        exact = ('-fno-schedule-insns' in flags and '*(s16 *)' in source and '*(volatile s32 *)(out' in source)
        return exact, SimpleNamespace(score=0 if exact else 200, rows=[]), ''

    net._judge_with = judge
    result = preflight.run(net, proj, fn, max_profiles=2)
    assert result['tested'] == 2 and result['best']['exact']
    assert [flags for flags, _ in state['calls']] == [['-O2'], ['-O2', '-fno-schedule-insns']]
    assert state['suggested'][0]['source'] == result['best']['source']
    assert not state['applied']


def test_relocation_recovery_resumes_and_rejudges_the_source_before_clearing_the_link_blocker():
    net, proj, fn, state = setup()
    proj['arch'] = 'mips'
    fn['best_source'] = 'extern int stale; int f(void){return stale;}'
    rows = [('i', 'lui $v0, %hi(current)', 'lui $v0, %hi(?stale)'),
            ('i', 'lw $v0, %lo(current)($v0)', 'lw $v0, %lo(?stale)($v0)')]

    def judge(project, function, toolchain, flags, source):
        state['calls'].append((flags, source))
        if 'stale' in source:
            return False, SimpleNamespace(score=70, rows=rows), '`stale` is not a known symbol.'
        return False, SimpleNamespace(score=50), ''

    net._judge_with = judge
    first = preflight.run(net, proj, fn, max_profiles=1)
    assert first['tested'] == 1 and first['link']['symbols'] == ['stale']
    result = preflight.run(net, proj, fn, max_profiles=1)
    assert result['tested'] == 1 and 'link' not in result
    assert result['relinked']['source'] == 'extern int current; int f(void){return current;}'
    assert result['relinked']['baseline'] == fn['best_source']
    assert [flags for flags, _ in state['calls']] == [['-O2'], ['-O2']]
    assert not state['suggested']


def test_displaced_jump_table_keeps_a_clean_compiler_profile_as_a_search_lead(monkeypatch):
    from decompnet.app import switch_tables

    net, proj, fn, state = setup()
    proj['arch'] = 'mips'
    net.cfg(proj)['compiler']['toolchain'] = 'gcc-2.7.2-kmc'
    monkeypatch.setattr(switch_tables, 'read', lambda *args: [(0, [4100, 4104])])
    logs = []

    def judge(project, function, toolchain, flags, source):
        state['calls'].append(flags)
        if '-fno-schedule-insns' in flags:
            return False, SimpleNamespace(score=90), ''
        return False, SimpleNamespace(score=50), "your code refers to .rodata+0x0 where the ROM's code has no address"

    net._judge_with = judge
    assert not preflight.run(net, proj, fn, log=logs.append, max_profiles=1).get('jump_table_profile')
    result = preflight.run(net, proj, fn, log=logs.append, max_profiles=2)
    assert state['calls'][:3] == [['-O2'], ['-O2', '-fno-schedule-insns'], ['-O2', '-fno-schedule-insns2']]
    assert result['jump_table_profile']['score'] == 90
    assert result['jump_table_profile']['flags'] == ['-O2', '-fno-schedule-insns']
    assert 'Jump-table relocation cleared' in '\n'.join(logs)
    assert not state['applied'] and not state['suggested']
    assert fn['best_score'] == 50


def test_jump_table_search_scores_only_clean_candidates():
    from decompnet.app import profile_climb

    net, proj, fn, _ = setup()
    search = SimpleNamespace(net=net, tested=0, report=lambda: None)
    profile = net.cfg(proj)['compiler']
    source = fn['best_source']
    net._judge_with = lambda *args: (False, SimpleNamespace(score=20), 'unplaced table')
    assert profile_climb._judge(search, proj, fn, profile, source, require_clean=True) is None
    net._judge_with = lambda *args: (False, SimpleNamespace(score=30), '')
    assert profile_climb._judge(search, proj, fn, profile, source, require_clean=True) == 30
    assert search.tested == 2


def test_displaced_jump_table_can_recover_with_delay_slot_scheduling_disabled(monkeypatch):
    from decompnet.app import switch_tables

    net, proj, fn, state = setup()
    proj['arch'] = 'mips'
    net.cfg(proj)['compiler']['toolchain'] = 'gcc-2.7.2-kmc'
    monkeypatch.setattr(switch_tables, 'read', lambda *args: [(0, [4100, 4104])])
    logs = []

    def judge(project, function, toolchain, flags, source):
        state['calls'].append(flags)
        if '-fno-delayed-branch' in flags:
            return False, SimpleNamespace(score=90), ''
        return False, SimpleNamespace(score=50), "your code refers to .rodata+0x0 where the ROM's code has no address"

    net._judge_with = judge
    assert not preflight.run(net, proj, fn, log=logs.append, max_profiles=1).get('jump_table_profile')
    result = preflight.run(net, proj, fn, log=logs.append, max_profiles=3)
    assert state['calls'][:4] == [
        ['-O2'], ['-O2', '-fno-schedule-insns'], ['-O2', '-fno-schedule-insns2'], ['-O2', '-fno-delayed-branch'],
    ]
    assert result['jump_table_profile']['score'] == 90
    assert result['jump_table_profile']['flags'] == ['-O2', '-fno-delayed-branch']
    assert 'Jump-table relocation cleared' in '\n'.join(logs)
    assert not state['applied'] and not state['suggested']
    assert fn['best_score'] == 50


def test_jump_table_preflight_combines_case_order_with_scheduling(monkeypatch):
    from decompnet.app import switch_tables

    net, proj, fn, state = setup()
    proj['arch'] = 'mips'
    net.cfg(proj)['compiler']['toolchain'] = 'gcc-2.7.2-kmc'
    child = 'int f(void) { return 2; }'
    monkeypatch.setattr(switch_tables, 'read', lambda *args: [(0, [4100, 4104])])
    monkeypatch.setattr(switch_tables, 'variants', lambda *args: [('target_switch_order', child)])
    calls = []

    def judge(project, function, compiler, flags, source):
        calls.append((flags, source))
        if source == child and '-fno-schedule-insns' in flags:
            return False, SimpleNamespace(score=90), ''
        return False, SimpleNamespace(score=50), "your code refers to .rodata+0x0 where the ROM's code has no address"

    net._judge_with = judge
    assert not preflight.run(net, proj, fn).get('jump_table_profile')
    assert preflight.run(net, proj, fn)['jump_table_profile']['source'] == child
    assert (['-O2', '-fno-schedule-insns'], child) in calls
    assert not state['applied']


def test_poll_loop_without_strength_reduction_is_probed_with_its_spellings(monkeypatch):
    """The profile alone ties while the source indexes by element, so it is judged joined with the byte offsets."""
    from decompnet.app import repair_ranking

    net, proj, fn, state = setup()
    proj['arch'] = 'mips'
    net.cfg(proj)['compiler'] = {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}
    source = ('extern s16 D_80100004;\nextern s16 D_80100006;\nvoid f(void)\n{\n  s32 off;\n  s32 i;\n  off = 0;\n  i = 0;\n'
              '  do\n  {\n    *((&D_80100004) + off) = 1;\n    *((&D_80100006) + off) = 2;\n    off += 8;\n    i++;\n  }\n'
              '  while (i < 4);\n}\n')
    one = ('lui $at, %hi(D_80100004)\naddu $at, $at, $s4\nsh $v0, %lo(D_80100004)($at)\n'
           'lui $at, %hi(D_80100006)\naddu $at, $at, $s4\nsh $v0, %lo(D_80100006)($at)\naddiu $s4, $s4, 0x8\n')
    fn.update(asm=one + one, best_source=source, name='f', n_insns=20)
    monkeypatch.setattr(repair_ranking, 'evidence', lambda *a: [])

    def judge(proj, fn, toolchain, flags, source):
        state['calls'].append((flags, source))
        exact = '-fno-strength-reduce' in flags and '(s8 *) (&D_80100004)) + off' in source
        return exact, SimpleNamespace(score=0 if exact else 400, rows=[]), ''

    net._judge_with = judge
    result = preflight.run(net, proj, fn, max_profiles=3)
    assert result['best']['exact'] and '-fno-strength-reduce' in result['best']['flags']
    assert state['calls'][0] == (['-O2'], source)
    assert all(flags == ['-O2', '-fno-strength-reduce'] for flags, _ in state['calls'][1:3])


def test_goto_table_loop_with_the_table_idiom_is_probed_as_a_real_loop_with_the_unreduced_profile(monkeypatch):
    """The real do/while, counter-compare guard and pointer-local store only pay off with the profile: judged joined."""
    from decompnet.app import repair_ranking

    net, proj, fn, state = setup()
    proj['arch'] = 'mips'
    net.cfg(proj)['compiler'] = {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}
    source = ('extern u8 gTable[];\nextern u8 gCount;\ns32 f(s32 a)\n{\n  u32 i;\n  s32 prev;\n  i = 0;\n  if (gCount != 0)\n  {\n'
              '    loop:\n    {\n      *(s32 *)(gTable + ((prev & 0xFF) * 0x1A0)) = a;\n      prev = i;\n      i += 1;\n    }\n'
              '    if (i < (u8) gCount) goto loop;\n  }\n  return prev;\n}\n')
    fn.update(asm='/* 1 */  lui        $at, %hi(gTable)\n/* 2 */  addu       $at, $at, $v1\n', best_source=source, name='f', n_insns=20)
    monkeypatch.setattr(repair_ranking, 'evidence', lambda *a: [])

    def judge(proj, fn, toolchain, flags, source):
        state['calls'].append((flags, source))
        exact = ('-fno-strength-reduce' in flags and 'goto' not in source and 'store_ptr' in source
                 and 'if (i < gCount)' in source)
        return exact, SimpleNamespace(score=0 if exact else 400, rows=[]), ''

    net._judge_with = judge
    result = preflight.run(net, proj, fn, max_profiles=3)
    assert result['best']['exact'] and '-fno-strength-reduce' in result['best']['flags']
    assert state['calls'][0] == (['-O2'], source)


def test_stepped_induction_loop_is_probed_as_counter_multiples_without_the_flag(monkeypatch):
    """A profile carrying -fno-strength-reduce and m2c's stepped pointers: the rewrite and the profile are judged together."""
    from decompnet.app import repair_ranking

    net, proj, fn, state = setup()
    proj['arch'] = 'mips'
    net.cfg(proj)['compiler'] = {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2', '-fno-strength-reduce']}
    source = ('void f(char *list, s32 *out)\n{\n  s32 i;\n  s32 off;\n  char *cursor;\n  i = 0;\n  off = 0;\n'
              '  cursor = list;\n  do\n  {\n    out[i] = *(s32 *) cursor + off;\n    i += 1;\n    cursor += 0x20;\n'
              '    off += 0xC;\n  }\n  while (*(s16 *) cursor >= 0);\n}\n')
    fn.update(asm='', best_source=source, name='f', n_insns=20)
    monkeypatch.setattr(repair_ranking, 'evidence', lambda *a: [])

    def judge(proj, fn, toolchain, flags, source):
        state['calls'].append((flags, source))
        exact = '-fno-strength-reduce' not in flags and 'i * 0x20' in source and 'cursor +=' not in source
        return exact, SimpleNamespace(score=0 if exact else 400, rows=[]), ''

    net._judge_with = judge
    result = preflight.run(net, proj, fn, max_profiles=3)
    assert result['best']['exact'] and '-fno-strength-reduce' not in result['best']['flags']
    assert state['calls'][0] == (['-O2', '-fno-strength-reduce'], source)
    assert state['calls'][1][0] == ['-O2'] and 'i * 0x20' in state['calls'][1][1]


def test_nearby_match_proposes_its_entry_specific_compiler_flags():
    net, proj, fn, state = setup()
    address = fn['vram'] + 4
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name,vram,size,rom_offset,status,best_score)"
                   " VALUES(1,'neighbor',?,16,200,'matched',0)", (address,))
    net.cfg(proj)['function_compilers'] = {
        f'100:{address}': {'vram': address, 'toolchain': 'example', 'flags': ['-O1']},
        f'200:{address}': {'vram': address, 'toolchain': 'example', 'flags': ['-O3']},
    }

    result = preflight.run(net, proj, fn, max_profiles=2)

    assert state['calls'] == [['-O2'], ['-O3']]
    assert result['best']['exact']


def test_address_split_rows_probe_another_small_data_threshold_with_the_externs_widened(monkeypatch):
    """The target keeps a float global in `$at` and splits an integer one: `-G4` plus a two-word array declaration matches
    (small_data_fingerprints); the exact pair is only ever a review-only suggestion, even with automatic changes on."""
    from decompnet.app import repair_ranking

    net, proj, fn, state = setup()
    proj['arch'] = 'mips'
    state['auto'] = True
    net.cfg(proj)['compiler'] = {'toolchain': 'sn-n64-281', 'flags': ['-G0', '-mips2', '-O2']}
    source = 'extern f32 F;\nextern s32 I;\nf32 f(f32 a) {\n    return (f32) I * a + F;\n}\n'
    asm = ('lui $v0, %hi(I)\nlw $v1, %lo(I)($v0)\nlui $at, %hi(F)\nlwc1 $f2, %lo(F)($at)\n')
    fn.update(asm=asm, best_source=source, name='f', n_insns=4)
    rows = [('>', '', 'lui $v0, %hi(F)'), ('<', 'lui $at, %hi(F)', ''), ('r', 'lwc1 $f2, %lo(F)($at)', 'lwc1 $f2, %lo(F)($v0)')]
    monkeypatch.setattr(repair_ranking, 'evidence', lambda *a: rows)
    monkeypatch.setattr(preflight.toolchains, 'compiler_family', lambda tid: 'gcc')
    net.flag_variants = lambda *a: []

    def judge(proj, fn, toolchain, flags, source):
        state['calls'].append((flags, source))
        exact = '-G4' in flags and 'I[2]' in source
        return exact, SimpleNamespace(score=0 if exact else 110 if '-G4' in flags else 100, rows=rows), ''

    net._judge_with = judge
    result = preflight.run(net, proj, fn, max_profiles=4)
    assert result['best']['exact'] and result['best']['flags'][0] == '-G4' and 'I[2]' in result['best']['source']
    assert result.get('review_only') is True and not state['applied']
    assert state['suggested'] and state['suggested'][0].get('review_only') is True


def test_two_additions_that_each_come_closer_are_probed_together():
    net, proj, fn, state = setup()
    off = ['-fno-schedule-insns', '-fno-schedule-insns2']
    net.flag_variants = lambda *a: [['-O2', '-fno-strength-reduce'], ['-O2', *off]]

    def judge(proj, fn, toolchain, flags, source):
        state['calls'].append(flags)
        score = {1: 50, 2: 30, 3: 20, 4: 0}[len(flags)]
        return score == 0, SimpleNamespace(score=score), ''
    net._judge_with = judge
    result = preflight.run(net, proj, fn)
    assert state['calls'][-1] == ['-O2', '-fno-strength-reduce', *off]
    assert result['best']['exact'] and len(state['suggested']) == 1


def test_an_addition_that_does_not_help_is_not_combined():
    net, proj, fn, state = setup()
    net.flag_variants = lambda *a: [['-O2', '-fno-strength-reduce'], ['-O2', '-fno-common']]

    def judge(proj, fn, toolchain, flags, source):
        state['calls'].append(flags)
        return False, SimpleNamespace(score=30 if '-fno-common' in flags else 50), ''
    net._judge_with = judge
    preflight.run(net, proj, fn)
    assert state['calls'] == [['-O2'], ['-O2', '-fno-strength-reduce'], ['-O2', '-fno-common']]
