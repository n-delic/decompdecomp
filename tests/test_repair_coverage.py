import hashlib
import json
from types import SimpleNamespace

from decompnet.app import repair_coverage
from tests.test_repair_feedback import fn, match, setup  # noqa: F401


def test_requires_same_frozen_baseline_and_exact_verdict(setup, monkeypatch):  # noqa: F811
    net, _, _, pid, fid = setup
    cfg = json.loads(net.store.one('SELECT config FROM projects WHERE id=?', pid)['config'])
    cfg['rom'] = {'sha1': 'a'*40, 'byte_order': 'big'}
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?,arch=? WHERE id=?', (json.dumps(cfg), 'mips', pid))
    example = net.repair_feedback.detail('demo', match(setup))
    before, winner = (example['evidence'][key] for key in ('before', 'after'))
    settings = dict(winner['settings'], settings_source='compile invocation snapshot', language='c',
                    scoring_revision='0'*64, effective_command=['compiler'])
    winner['settings'] = settings
    before['settings'] = dict(settings)
    candidate = 'int target(void) { return 3; }\n'
    seen = []
    verdict = False

    def compile_source(_config, header, source):
        assert header == before['header']
        seen.append(source)
        return SimpleNamespace(ok=True, language='c', obj=source)

    def judge(_fn, _proj, source):
        return object(), verdict and source == candidate, []

    monkeypatch.setattr(repair_coverage.toolchains, 'installed', lambda _: True)
    monkeypatch.setattr(repair_coverage, 'compiler_config', lambda *_: SimpleNamespace(cmd=['compiler'], timeout=1))
    monkeypatch.setattr(repair_coverage, 'compile_source', compile_source)
    monkeypatch.setattr(repair_coverage.scoring, 'diff', lambda *_: SimpleNamespace(rows=[]))
    monkeypatch.setattr(net, '_evaluate', judge)
    monkeypatch.setattr(net, '_target_obj', lambda *_: object())
    monkeypatch.setattr(repair_coverage, 'proposals', lambda *_: iter([('installed_rule', candidate)]))
    before['settings']['language'] = 'c++'
    assert repair_coverage.verify(net.repair_feedback, example) is None
    before['settings']['language'] = 'c'
    before['source_sha256'] = hashlib.sha256(b'wrong').hexdigest()
    assert repair_coverage.verify(net.repair_feedback, example) is None
    assert not seen
    before['source_sha256'] = hashlib.sha256(before['source'].encode()).hexdigest()
    assert repair_coverage.verify(net.repair_feedback, example) is None
    assert seen == [before['source'], candidate]
    verdict = True
    assert repair_coverage.verify(net.repair_feedback, example) == 'installed_rule'
    assert fn(net, fid)['status'] == 'matched'
