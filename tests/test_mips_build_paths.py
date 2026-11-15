"""Public MIPS builds find installed pinned binutils without global links or project-source changes."""

import json
import os
from types import SimpleNamespace

import pytest

from decompnet import consoles
from decompnet.arch import mips_tools
from decompnet.forks.project import ForkProject

pytestmark = pytest.mark.skipif(os.name == "nt", reason="pinned MIPS binaries run inside WSL on Windows")


def pinned(tmp_path, monkeypatch):
    tools = tmp_path / "pinned"
    tools.mkdir()
    ld = tools / "mips-ps2-decompals-ld"
    ld.write_text("#!/bin/sh\nprintf 'pinned MIPS linker\\n'\n")
    ld.chmod(0o755)
    monkeypatch.setattr(mips_tools.toolchains, "installed", lambda tid: {"id": tid})
    monkeypatch.setattr(mips_tools.toolchains, "require", lambda tid: tools)
    return tools, ld


@pytest.mark.parametrize("console", ["n64", "ps", "ps2"])
def test_all_mips_consoles_expose_the_same_installed_tools(tmp_path, monkeypatch, console):
    tools, ld = pinned(tmp_path, monkeypatch)
    project = SimpleNamespace(root=tmp_path / "project")
    paths = consoles.get(console).project_tool_paths(project)
    assert paths[-1] == tools
    for prefix in ("mips-linux-gnu-", "mips64-linux-gnu-", "mips64-elf-"):
        assert (paths[0] / (prefix + "ld")).resolve() == ld
    assert consoles.get(console).project_tool_paths(project) == paths  # repeated builds reuse the links
    assert consoles.get("gc").project_tool_paths(project) == []


def test_missing_toolchain_creates_nothing_and_downloads_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(mips_tools.toolchains, "installed", lambda tid: None)
    project = SimpleNamespace(root=tmp_path)
    assert mips_tools.build_paths(project) == []
    assert not (tmp_path / ".decomp-wtf").exists()


def test_make_detects_pinned_tools_in_its_process_path(tmp_path, monkeypatch):
    import shutil

    if not shutil.which("make"):
        pytest.skip("needs make")
    pinned(tmp_path, monkeypatch)
    root = tmp_path / "project"
    root.mkdir()
    (root / ".decomp.wtf.json").write_text(json.dumps({"platform": "n64"}))
    makefile = root / "Makefile"
    contents = ("ifeq ($(shell which mips-linux-gnu-ld 2>/dev/null),)\n"
                "$(error Unable to detect a suitable MIPS toolchain installed)\nendif\n"
                "all:\n\t@mips-linux-gnu-ld --version\n")
    makefile.write_text(contents)
    path = os.environ.get("PATH")
    ok, log = ForkProject(root).run(["make"], log=lambda *_: None)
    assert ok and "pinned MIPS linker" in log
    assert os.environ.get("PATH") == path and makefile.read_text() == contents


def test_n64_reuses_only_the_declared_missing_ido_version(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import buildtools

    source = tmp_path / "installed compiler with spaces"
    source.mkdir()
    (source / "cc").write_text("compiler")
    (source / "cfe").write_text("compiler sibling")
    monkeypatch.setattr(buildtools.toolchains, "installed", lambda tid: {"id": tid})
    monkeypatch.setattr(buildtools.toolchains, "require", lambda tid: source)
    root = tmp_path / "project"
    root.mkdir()
    make = "TOOLS := tools\nIDO := $(TOOLS)/ido-static-recomp/build/5.3/out/cc\n"
    (root / "Makefile").write_text(make)
    project = SimpleNamespace(root=root)
    prepared = consoles.get("n64").project_build_command(project, ["make", "extract"])
    assert prepared == ["make", "extract", "IDO=.decomp-wtf/build-tools/ido-5.3/cc"]
    assert (root / ".decomp-wtf/build-tools/ido-5.3/cfe").read_text() == "compiler sibling"
    assert (root / "Makefile").read_text() == make
    explicit = ["make", "IDO=my-own-compiler"]
    assert consoles.get("n64").project_build_command(project, explicit) == explicit
    assert consoles.get("ps2").project_build_command(project, ["make"]) == ["make"]
    local = root / "tools/ido-static-recomp/build/5.3/out/cc"
    local.parent.mkdir(parents=True)
    local.write_text("existing local compiler")
    assert consoles.get("n64").project_build_command(project, ["make"]) == ["make"]
    local.unlink()
    monkeypatch.setattr(buildtools.toolchains, "installed", lambda tid: None)
    assert consoles.get("n64").project_build_command(project, ["make"]) == ["make"]
