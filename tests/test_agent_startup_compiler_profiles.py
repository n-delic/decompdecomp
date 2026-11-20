"""Agent startup metadata accepts compiler IDs as well as structured profiles."""

from types import SimpleNamespace

import pytest

from decompnet.app import prompt_settings, session_efficiency, tools


@pytest.mark.parametrize(
    ("compiler", "session_name", "project_name", "flags"),
    [
        ("msvc", "msvc", "msvc", ""),
        ({"toolchain": "gcc-2.7.2-kmc", "flags": ["-O2", "-g0"]},
         "gcc-2.7.2-kmc", "gcc-2.7.2-kmc", "-O2 -g0"),
        (None, "project build", "set by the project build", ""),
    ],
)
def test_agent_startup_supports_string_and_mapping_compiler_settings(
    monkeypatch, compiler, session_name, project_name, flags
):
    """Both the session snapshot and compact prompt must accept fork compiler IDs."""
    project = {"id": 1, "name": "Example", "slug": "example"}
    cfg = {"kind": "fork", "platform": "pc", "compiler": compiler}
    store = SimpleNamespace(one=lambda *args: {"total": 1, "matched": 0, "attempts": 0})
    net = SimpleNamespace(
        project_row=lambda slug: project,
        _project=lambda slug: project,
        cfg=lambda row: cfg,
        fork_slug=lambda slug: "example",
        project_store=lambda slug: store,
    )
    monkeypatch.setattr(
        prompt_settings, "effective",
        lambda net, options: {"initial_project": "Start", "instructions": "Work carefully"},
    )

    snapshot = session_efficiency.context(net, "example", {})
    assert snapshot["compiler"] == session_name
    assert snapshot["prompt_profile"]

    info = tools._project_info(net, "example", detail=False)
    assert f"compiler: {project_name}" in info
    assert f"compiler: {project_name} {flags}\n" in info
    assert "progress: 0/1 functions matched" in info
