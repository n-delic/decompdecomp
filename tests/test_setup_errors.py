"""Build failures must not be presented as successful setup with missing measurement configuration."""

import pytest

from decompnet.forks.project import ForkError, ForkProject


def test_a_failed_make_without_objdiff_reports_the_build_error_and_stops(tmp_path, monkeypatch):
    project = ForkProject(tmp_path)
    monkeypatch.setattr(project, "unpack_disc", lambda *a, **k: None)
    monkeypatch.setattr(project, "setup_commands", lambda *a: [["make", "extract"], ["make"]])
    called = []
    def run(cmd, **kwargs):
        called.append(cmd)
        return False, "required-assembler: command not found"
    monkeypatch.setattr(project, "run", run)
    with pytest.raises(ForkError, match="required-assembler: command not found"):
        project.setup(log=lambda *a: None)
    assert called == [["make", "extract"]]


def test_an_incomplete_make_can_still_measure_units_with_existing_objdiff(tmp_path, monkeypatch):
    project = ForkProject(tmp_path)
    (tmp_path / "objdiff.json").write_text('{"units": []}')
    monkeypatch.setattr(project, "unpack_disc", lambda *a, **k: None)
    monkeypatch.setattr(project, "setup_commands", lambda *a: [["make"]])
    monkeypatch.setattr(project, "run", lambda *a, **k: (False, "unfinished source"))
    monkeypatch.setattr(project, "_all_source", lambda: False)
    project.setup(log=lambda *a: None)


def test_a_successful_build_missing_objdiff_reports_only_the_configuration_gap(tmp_path, monkeypatch):
    project = ForkProject(tmp_path)
    monkeypatch.setattr(project, "unpack_disc", lambda *a, **k: None)
    monkeypatch.setattr(project, "setup_commands", lambda *a: [["make"]])
    monkeypatch.setattr(project, "run", lambda *a, **k: (True, "built"))
    with pytest.raises(ForkError, match="built, but has no objdiff"):
        project.setup(log=lambda *a: None)
