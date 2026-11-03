import hashlib
import json
from types import SimpleNamespace

from decompnet.app import adaptive_repairs
from decompnet.app import experiment_evidence as evidence
from decompnet.repair_feedback import trial
from tests.test_permute import AGENT, FakeNet


def test_no_edit_proposals_never_invoke_a_compiler_and_record_empty_spans():
    net=FakeNet();fn=net.function();source=fn['best_source']
    owner=SimpleNamespace(net=net,_score_work=lambda *a: (_ for _ in ()).throw(AssertionError('compiled no edit')))
    with trial({'family':'test','base_source_sha256':hashlib.sha256(source.encode()).hexdigest()}):
        assert evidence.measured(owner,AGENT,net.project_row('game'),fn,source,40,None) is None
    row=net.store.one('SELECT * FROM repair_input_effects')
    assert row['attempted']==1 and row['no_edit']==1 and row['last_edits']=='[]'
    assert owner._trial_outcome=='no_edit'


def test_identical_function_outputs_regressions_and_real_edits_are_distinct():
    net=FakeNet();fn=net.function();source=fn['best_source'];base=hashlib.sha256(source.encode()).hexdigest()
    net._repair_compile_outputs={(fn['id'],evidence.build_scope(net,net.project_row('game'),fn),base):(source,'same-code')}
    owner=SimpleNamespace(net=net)
    def compile(*args):owner._trial_output='same-code';return 40
    owner._score_work=compile
    with trial({'family':'test','base_source_sha256':base}):
        evidence.measured(owner,AGENT,net.project_row('game'),fn,source+'\n',40,None)
    row=net.store.one('SELECT * FROM repair_input_effects')
    assert row['same_output']==1 and row['no_edit']==0 and json.loads(row['last_edits'])
    assert evidence.edits(source,source)==[]


def test_ineffective_input_priority_keeps_exploration_and_resets_when_source_changes():
    net=FakeNet();fn=net.function();proj=net.project_row('game');key=evidence.scope(net,proj,fn)
    for _ in range(8):evidence.record(net,fn,key,'no_win','old','changed',40,45,None,None,3)
    assert evidence.factors(net,proj,fn)=={'no_win':0.25}
    assert set(adaptive_repairs.order(net,proj,fn,[],['no_win','fresh']))=={'no_win','fresh'}
    changed={**dict(fn),'best_source':'new source'}
    assert evidence.factors(net,proj,changed)=={}
    evidence.record(net,fn,key,'no_win','old','winner',40,30,None,None,3)
    assert evidence.factors(net,proj,fn)=={}


def test_output_cache_is_scoped_to_build_inputs():
    net=FakeNet();fn=net.function();proj=net.project_row('game');source=fn['best_source']
    base=hashlib.sha256(source.encode()).hexdigest()
    net._repair_compile_outputs={(fn['id'],evidence.build_scope(net,proj,fn),base):(source,'same-code')}
    net._context=lambda *_:{'header':'typedef long s32;','version':2}
    owner=SimpleNamespace(net=net)
    def compile(*args):owner._trial_output='same-code';return 40
    owner._score_work=compile
    with trial({'family':'test','base_source_sha256':base}):
        evidence.measured(owner,AGENT,proj,fn,source+'\n',40,None)
    assert net.store.one('SELECT * FROM repair_input_effects')['same_output']==0


def test_imported_compiler_label_does_not_enter_rom_identity(monkeypatch):
    from decompnet import match_bundle
    from decompnet.app import agent_tokens
    net=FakeNet();fn=net.function();proj=net.project_row('game')
    net.cfg=lambda _: {'kind':'fork','compiler':'GCC 2.95.3'}
    net.fork_slug=lambda _: 'game'
    build=['project-unit-v1']
    monkeypatch.setattr(agent_tokens,'target_scope',lambda *args: build[0])
    original=match_bundle.compiler_identity
    seen=[]
    def identity(settings):
        seen.append(settings)
        return original(settings)
    monkeypatch.setattr(match_bundle,'compiler_identity',identity)
    before=evidence.build_scope(net,proj,fn)
    build[0]='project-unit-v2'
    assert evidence.build_scope(net,proj,fn) != before
    assert seen == [{},{}]
    owner=SimpleNamespace(net=net,_score_work=lambda *args: 35)
    with trial({'family':'test'}):
        assert evidence.measured(owner,AGENT,proj,fn,fn['best_source']+'\n',40,object()) == 35


def test_rom_compiler_identity_still_scopes_evidence(monkeypatch):
    from decompnet import match_bundle
    from decompnet.app import agent_tokens
    net=FakeNet();fn=net.function();proj=net.project_row('game')
    monkeypatch.setattr(agent_tokens,'target_scope',lambda *args: 'same-target')
    identity=['installed-v1']
    monkeypatch.setattr(match_bundle,'compiler_identity',lambda settings: (settings['toolchain'],identity[0]))
    before=evidence.build_scope(net,proj,fn)
    identity[0]='installed-v2'
    assert evidence.build_scope(net,proj,fn) != before
