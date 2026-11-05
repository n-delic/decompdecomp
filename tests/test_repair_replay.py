from copy import deepcopy

from decompnet.app import repair_replay as replay
from decompnet.elfobj import FunctionObj


def case():
    settings = {'mode':'rom','platform':'n64','compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}}
    before = 'int f(){return 1;}'
    after = 'int f(){return 2;}'
    return {'id':7,'evidence':{'function':'f','comparable_settings':True,
            'before':{'source':before,'source_sha256':replay.digest(before),'header':'','settings':settings},
            'after':{'source':after,'source_sha256':replay.digest(after),'header':'','settings':settings}}}


def obj(number):
    return FunctionObj('f','mips',True,bytes.fromhex(f'03e000082402000{number}'))


def test_replay_round_robins_reports_failures_and_never_changes_evidence(monkeypatch):
    example = case(); original = deepcopy(example)
    def variants(source,name,arch,asm,flags,labels,**kwargs):
        labels.update(bad='long_family',neutral='long_family',winner='small_family')
        return ['bad','neutral','winner']
    monkeypatch.setattr(replay.draft_repairs,'variants',variants)
    seen = []
    def measure(source):
        seen.append(source)
        if source=='bad': return {'error':'invalid C'}
        return {'object':obj(2 if source in ('winner','int f(){return 2;}') else 1)}
    result = replay.replay(example,trials=2,measure=measure)
    assert seen[-2:]==['bad','winner']
    assert result['summary']=={'compile_error':1,'control_output_recovered':1}
    assert result['remaining']==1 and example==original
    assert 'no linked/objdiff match adoption' in result['verdict']


def test_missing_or_mixed_context_is_skipped_before_compiling():
    for change in ('source','header','settings'):
        example = case()
        if change=='source':example['evidence']['before']['source']='changed'
        elif change=='header':example['evidence']['before']['header']='changed'
        else:example['evidence']['comparable_settings']=False
        assert 'skipped' in replay.replay(example,measure=lambda source: (_ for _ in ()).throw(AssertionError()))


def test_combination_benchmark_reports_path_cost_and_stops_at_recovered_control(monkeypatch):
    example = case()
    original = deepcopy(example)
    before = example['evidence']['before']['source']
    def variants(source, name, arch, asm, flags, labels, **kwargs):
        pool = {before:[('scope','bad'), ('scope','step')], 'step':[('order','winner'), ('order','unused')]}.get(source, [])
        labels.update({s:f for f,s in pool})
        return [s for _,s in pool]
    monkeypatch.setattr(replay.draft_repairs, 'variants', variants)
    calls = []
    def measure(source):
        calls.append(source)
        if source == 'bad':return {'error':'failed compile'}
        return {'object':obj(2 if source in ('winner',example['evidence']['after']['source']) else 1)}
    result = replay.replay(example, measure=measure, combinations=True, trials=8)
    assert result['winning_path'] == ['scope','order']
    assert result['first_exact_trial'] == result['candidate_compiles'] == 3
    assert result['control_compiles'] == 2 and result['elapsed_seconds'] >= 0
    assert result['reason'] == 'control_output_recovered'
    assert calls == [before,example['evidence']['after']['source'],'bad','step','winner']
    assert example == original


def test_combination_benchmark_budget_failure_and_data_mismatch(monkeypatch):
    def variants(source, name, arch, asm, flags, labels, **kwargs):
        labels.update(winner='scope', untried='scope')
        return ['winner','untried']
    monkeypatch.setattr(replay.draft_repairs, 'variants', variants)
    def measure(source):
        return {'object':obj(1 if 'return 1' in source else 2),
                'data':'different' if source == 'winner' else 'control'}
    result = replay.replay(case(), measure=measure, combinations=True, trials=1)
    assert result['reason'] == 'trial_budget' and result['first_exact_trial'] is None
    assert result['trials'][0]['score'] == 0 and result['trials'][0]['outcome'] == 'improved'


def test_imported_build_missing_inputs_and_uninstalled_tools_are_explicit_skips(monkeypatch):
    example = case()
    monkeypatch.setattr(replay.toolchains,'installed',lambda name:None)
    assert 'already be installed' in replay.replay(example)['skipped']
    for stage in ('before','after'):example['evidence'][stage]['settings']['mode']='project'
    assert 'copied build' in replay.replay(example)['skipped']


def test_recorded_control_must_reproduce_a_difference():
    result = replay.replay(case(),measure=lambda source:{'object':obj(1)})
    assert result['trials']==[] and 'no isolated regression' in result['skipped']


def test_equal_instructions_with_different_constant_data_do_not_recover_control(monkeypatch):
    def variants(source,name,arch,asm,flags,labels,**kwargs):
        labels['candidate']='test'
        return ['candidate']
    monkeypatch.setattr(replay.draft_repairs,'variants',variants)
    def measure(source):
        return {'object':obj(1 if source=='int f(){return 1;}' else 2),
                'data':'different' if source=='candidate' else 'control'}
    result = replay.replay(case(),measure=measure)
    assert result['summary']=={'improved':1} and result['trials'][0]['score']==0


def test_inherited_or_different_base_random_labels_are_not_direct_evidence():
    example=case();e=example['evidence']
    e['provenance']={'kind':'random rewrite','family':'perm_duplicate_assignment',
                     'source_sha256':e['after']['source_sha256'],'base_source_sha256':e['before']['source_sha256']}
    assert replay.rewrite_evidence(e)['direct']
    e['provenance']['base_source_sha256']='other'
    assert not replay.rewrite_evidence(e)['direct']
    e['provenance']['source_sha256']='other'
    assert replay.rewrite_evidence(e)['kind']=='inherited'


def test_cancellation_stops_before_a_compiler_call():
    import pytest
    def cancelled():raise RuntimeError('stopped')
    with pytest.raises(RuntimeError,match='stopped'):
        replay.replay(case(),check_cancel=cancelled,measure=lambda s: (_ for _ in ()).throw(AssertionError()))


def test_edit_minimization_shares_the_replay_budget_and_preserves_evidence(monkeypatch):
    example=case();before='int f(){return 1;}\n\nint unrelated=1;\n'
    after='int f(){return 2;}\n\nint unrelated=2;\n'
    for key,source in [('before',before),('after',after)]:
        example['evidence'][key].update(source=source,source_sha256=replay.digest(source))
    original=deepcopy(example)
    monkeypatch.setattr(replay.draft_repairs,'variants',lambda *a,**kw:[])
    result=replay.replay(example,trials=6,minimize=True,measure=lambda source:{'object':obj(2 if 'return 2' in source else 1)})
    assert result['minimization']['reverted']==1
    assert len(result['trials'])<=3 and example==original
    assert 'unrelated=1' in result['minimization']['source']


def test_analysis_options_require_booleans():
    import pytest
    with pytest.raises(ValueError,match='booleans'):
        replay.replay(case(),minimize='yes')


def test_judge_revision_only_change_can_be_recompiled_without_trusting_old_scores(monkeypatch):
    example=case()
    example['evidence']['before']['settings']=deepcopy(example['evidence']['before']['settings'])
    example['evidence']['before']['settings']['scoring_revision']='old'
    example['evidence']['after']['settings']['scoring_revision']='new'
    example['evidence']['comparable_settings']=False
    original=deepcopy(example)
    monkeypatch.setattr(replay.draft_repairs,'variants',lambda *args,**kwargs:[])
    report=replay.replay(example,measure=lambda source:{'object':obj(1 if 'return 1' in source else 2)})
    assert 'skipped' not in report and 'Both sources are recompiled' in report['context_note']
    assert example==original and 'no linked/objdiff match adoption' in report['verdict']
    example['evidence']['before']['settings']['compiler']={'toolchain':'different'}
    assert 'skipped' in replay.replay(example,measure=lambda s:1/0)


def test_original_container_order_does_not_block_frozen_source_replay(monkeypatch):
    from tests.test_repair_comparison import snapshot
    example = case()
    settings = snapshot()
    settings['context_sha256'] = replay.digest('')
    example['evidence']['before']['settings'] = settings
    example['evidence']['after']['settings'] = deepcopy(settings)
    example['evidence']['after']['settings']['rom']['byte_order'] = 'z64'
    example['evidence']['after']['settings']['platform'] = 'n64'
    example['evidence']['before']['settings']['platform'] = 'n64'
    example['evidence']['comparable_settings'] = False  # Historical flags stay frozen.
    frozen = deepcopy(example)
    monkeypatch.setattr(replay.draft_repairs, 'variants', lambda *args, **kw: [])
    seen = []
    def measure(source):
        seen.append(source)
        return {'object':obj(1 if 'return 1' in source else 2)}
    result = replay.replay(example, measure=measure)
    assert 'skipped' not in result and len(seen) == 2
    assert 'container' in result['comparison_note']
    assert example == frozen
    example['evidence']['after']['settings']['big_endian'] = False
    assert 'skipped' in replay.replay(example, measure=lambda source: (_ for _ in ()).throw(AssertionError()))
