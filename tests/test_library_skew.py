"""A library upgraded under the running app is reloaded in place, not reported as a bug partway through a job."""
import importlib
import sys
import textwrap

import pytest

from decompnet.app import library_skew
from decompnet.network import NetworkError


def _library(root, version_two):
    package = root / 'skewlib'
    package.mkdir(exist_ok=True)
    (package / '__init__.py').write_text('')
    text = 'def f():\n    return 2\n\n\ndef added():\n    return "new"\n' if version_two else 'def f():\n    return 1\n'
    (package / 'a.py').write_text(text)
    (package / 'b.py').write_text(textwrap.dedent('''
        from . import a
        from .a import f

        def through_module():
            return a.f()

        def through_name():
            return f()
    '''))
    importlib.invalidate_caches()


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    _library(tmp_path, False)
    for name in [m for m in sys.modules if m.startswith('skewlib')]:
        del sys.modules[name]
    b = importlib.import_module('skewlib.b')
    versions = {'skewlib': '1'}
    monkeypatch.setitem(library_skew.LOADED, 'skewlib', '1')
    yield tmp_path, b, versions
    for name in [m for m in sys.modules if m.startswith('skewlib')]:
        del sys.modules[name]
    library_skew.LOADED.pop('skewlib', None)


def test_an_upgraded_library_is_reloaded_in_place_dependencies_first(library):
    root, b, versions = library
    assert b.through_module() == b.through_name() == 1
    _library(root, True)
    versions['skewlib'] = '2'
    epoch = library_skew.EPOCH
    assert library_skew.ensure(force=True, installed=versions.get, packages=('skewlib',))
    assert library_skew.EPOCH == epoch + 1 and library_skew.LOADED['skewlib'] == '2'
    assert b.through_module() == 2 and b.through_name() == 2  # `from .a import f` follows because a reloads first
    assert importlib.import_module('skewlib.a').added() == 'new'  # a module object held earlier sees new names


def test_nothing_happens_when_the_versions_match_and_checks_are_throttled(library):
    _, _, versions = library
    versions['skewlib'] = '1'
    assert library_skew.ensure(force=True, installed=versions.get, packages=('skewlib',)) is False
    versions['skewlib'] = '9'
    assert library_skew.ensure(installed=versions.get, packages=('skewlib',)) is False  # inside the throttle window


def test_a_failed_reload_leaves_the_old_code_and_asks_for_a_restart(library):
    root, b, versions = library
    (root / 'skewlib' / 'a.py').write_text('def f(:\n')
    importlib.invalidate_caches()
    versions['skewlib'] = '2'
    with pytest.raises(NetworkError, match=r'skewlib 1 is loaded but 2 is installed.*quit and start'):
        library_skew.ensure(force=True, installed=versions.get, packages=('skewlib',))
    assert b.through_module() in (1, 2) and library_skew.LOADED['skewlib'] == '1'


def test_the_running_process_has_noted_what_is_installed():
    assert library_skew.LOADED['decomprepair'] and library_skew.changed() == []
    assert dict(library_skew.installed_versions())['decomprepair'] == library_skew.LOADED['decomprepair']


def test_a_deep_search_reloads_before_preparing(monkeypatch):
    from decompnet.app import deep_permuter
    calls = []
    monkeypatch.setattr(library_skew, 'ensure', lambda **k: calls.append(k) or (_ for _ in ()).throw(NetworkError('stop here')))
    with pytest.raises(NetworkError, match='stop here'):
        deep_permuter.validate(object(), 'twine', 'f', 10, 100)
    assert calls == [{'force': True}]
