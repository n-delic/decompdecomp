"""A library upgraded under the running app is reported as a restart, not as a bug partway through a job."""
import pytest

from decompnet.app import library_skew
from decompnet.network import NetworkError


def test_matching_versions_pass_and_missing_packages_are_ignored():
    library_skew.check({'decomprepair': '1.2.3', 'decompassets': None}, lambda package: {'decomprepair': '1.2.3'}.get(package))
    assert library_skew.changed({'decomprepair': '1.2.3'}, lambda package: None) == []


def test_an_upgraded_library_asks_for_a_restart_naming_both_versions():
    with pytest.raises(NetworkError, match=r'decomprepair 1\.2\.3 is loaded but 1\.2\.4 is installed.*start it again'):
        library_skew.check({'decomprepair': '1.2.3'}, lambda package: '1.2.4')


def test_the_running_process_has_noted_what_is_installed():
    assert library_skew.LOADED['decomprepair'] and library_skew.changed() == []


def test_a_deep_search_checks_before_preparing(monkeypatch):
    from decompnet.app import deep_permuter
    monkeypatch.setattr(library_skew, 'check', lambda *a, **k: (_ for _ in ()).throw(NetworkError('restart')))
    with pytest.raises(NetworkError, match='restart'):
        deep_permuter.validate(object(), 'twine', 'f', 10, 100)
