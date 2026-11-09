"""Check again recovers missing references after an external or previously interrupted setup."""

import contextlib
from types import SimpleNamespace

import pytest

from decompnet.app.forkengine import ForkEngine
from decompnet.forks.project import ForkProject
from decompnet.network import NetworkError


@pytest.mark.parametrize('build', [True, False])
def test_check_generates_missing_references_with_the_selected_version(tmp_path, monkeypatch, build):
    project = ForkProject(tmp_path)
    calls = []
    def generate(**kw):
        calls.append(('generate', kw['version']))
        (tmp_path / 'objdiff.json').write_text('{"units": []}')
        return True
    monkeypatch.setattr(project, 'generate_objdiff', generate)
    monkeypatch.setattr(project, 'build_all', lambda **kw: calls.append(('build', None)))
    monkeypatch.setattr(project, 'report', lambda: {})
    monkeypatch.setattr(project, 'linkable', lambda: False)
    cfg = {'version': 'jp'}
    engine = SimpleNamespace(get=lambda _: ({'id': 1}, project, cfg),
                             _tree=lambda _: SimpleNamespace(exclusive=contextlib.nullcontext),
                             _build_locks={1: contextlib.nullcontext()}, _save_cfg=lambda *_: None,
                             sync=lambda *a, **kw: {'functions': 2})
    assert ForkEngine.check(engine, 'demo', log=lambda *_: None, build=build) == {'functions': 2}
    assert calls[0] == ('generate', 'jp')
    assert calls.count(('build', None)) == int(build)
    assert ('generate', 'jp') in calls
    assert cfg['ready']


def test_unsupported_missing_references_do_not_mark_the_project_ready(tmp_path, monkeypatch):
    project = ForkProject(tmp_path)
    monkeypatch.setattr(project, 'generate_objdiff', lambda **kw: False)
    cfg = {}
    engine = SimpleNamespace(get=lambda _: ({'id': 1}, project, cfg))
    with pytest.raises(NetworkError, match='supported Splat layout'):
        ForkEngine.check(engine, 'demo', log=lambda *_: None)
    assert not cfg.get('ready')
