from copy import deepcopy

from decompnet.app.repair_context_replay import replay
from tests.test_repair_replay import case, obj


def test_joint_context_changes_are_isolated_without_mutating_capture():
    evidence=case()['evidence'];before=evidence['before'];after=evidence['after']
    before['source']=after['source'];before['source_sha256']=after['source_sha256']
    before['settings']=deepcopy(after['settings']);after['settings']['compiler']['flags']=['-O2','-mcpu=r4600']
    before['header']='old';after['header']='new'
    original=deepcopy(evidence)
    def measure(source,header,profile):
        return {'object':obj(2 if header=='new' and '-mcpu=r4600' in profile['flags'] else 1)}
    result=replay(evidence,measure=measure)
    rows={r['combination']:r for r in result['trials']}
    assert rows['after']['control_output_recovered']
    assert not rows['compiler only']['control_output_recovered']
    assert not rows['header only']['control_output_recovered']
    assert evidence==original


def test_context_replay_checks_hashes_before_compile():
    evidence=case()['evidence'];evidence['after']['source']='changed'
    assert 'skipped' in replay(evidence,measure=lambda *args:(_ for _ in ()).throw(AssertionError()))
