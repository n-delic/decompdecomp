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
    monkeypatch.setattr(project, 'build_all', lambda **kw: (calls.append(('build', None)), (True, ''))[1])
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


def test_check_does_not_measure_stale_objects_after_an_unattributed_build_failure(tmp_path, monkeypatch):
    (tmp_path / 'objdiff.json').write_text('{"units": []}')
    project = ForkProject(tmp_path)
    monkeypatch.setattr(project, 'build_all', lambda **kw: (False, 'toolchain failed before compiling'))
    monkeypatch.setattr(project, 'report', lambda: pytest.fail('must not report stale objects'))
    engine = SimpleNamespace(get=lambda _: ({'id': 1}, project, {}),
                             _tree=lambda _: SimpleNamespace(exclusive=contextlib.nullcontext),
                             _build_locks={1: contextlib.nullcontext()}, _save_cfg=lambda *_: None)
    with pytest.raises(NetworkError, match='toolchain failed before compiling'):
        ForkEngine.check(engine, 'demo', log=lambda *_: None, build=True)


def test_check_still_reports_partial_builds_with_identified_failed_units(tmp_path, monkeypatch):
    (tmp_path / 'objdiff.json').write_text('{"units": []}')
    project = ForkProject(tmp_path)
    def build(**kw):
        kw['failed'].append('build/broken.o')
        return False, 'a source did not compile'
    monkeypatch.setattr(project, 'build_all', build)
    monkeypatch.setattr(project, 'report', lambda: {'units': []})
    monkeypatch.setattr(project, 'failed_units', lambda targets: {'broken'} if targets else set())
    monkeypatch.setattr(project, 'linkable', lambda: False)
    engine = SimpleNamespace(get=lambda _: ({'id': 1}, project, {}),
                             _tree=lambda _: SimpleNamespace(exclusive=contextlib.nullcontext),
                             _build_locks={1: contextlib.nullcontext()}, _save_cfg=lambda *_: None,
                             sync=lambda *a, **kw: kw['broken'])
    assert ForkEngine.check(engine, 'demo', log=lambda *_: None, build=True) == {'broken'}
