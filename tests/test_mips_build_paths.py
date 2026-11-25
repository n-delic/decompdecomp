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
        wrapper = (paths[0] / (prefix + "ld")).read_text()  # (a script: it renames the emulation, then runs ld)
        assert str(ld.resolve()) in wrapper and "elf32btsmip) a=elf32ebmip" in wrapper
        assert (paths[0] / (prefix + "ld")).stat().st_mode & 0o111
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


def test_n64_redirects_a_cc_variable_and_builds_the_crc_tool(tmp_path, monkeypatch):
    """A community decomp that picks its compiler with an indented `CC :=` under an else (after a qemu line that
    also assigns CC) still gets the installed IDO, and tools/n64crc is compiled for its checksum step."""
    from decompnet.consoles.n64 import buildtools

    source = tmp_path / "ido"
    source.mkdir()
    (source / "cc").write_text("compiler")
    monkeypatch.setattr(buildtools.toolchains, "installed", lambda tid: {"id": tid})
    monkeypatch.setattr(buildtools.toolchains, "require", lambda tid: source)
    ran = []
    monkeypatch.setattr(buildtools.subprocess, "run", lambda argv, **kw: ran.append(argv))
    monkeypatch.setattr(buildtools.shutil, "which", lambda name: "/usr/bin/cc" if name == "cc" else None)
    root = tmp_path / "project"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / "n64crc.c").write_text("int main(void){return 0;}")
    make = ("TOOLS_DIR = tools\nifeq ($(USE_QEMU_IRIX),1)\n\tCC := $(QEMU_IRIX) -silent $(TOOLS_DIR)/x/cc\n"
            "\telse\n\tCC\t := $(TOOLS_DIR)/ido-static-recomp/build/5.3/out/cc\n\tendif\n")
    (root / "Makefile").write_text(make)
    prepared = consoles.get("n64").project_build_command(SimpleNamespace(root=root), ["make", "-j4"])
    assert prepared == ["make", "-j4", "CC=.decomp-wtf/build-tools/ido-5.3/cc"]
    assert ran and ran[0][0] == "/usr/bin/cc" and ran[0][-1].endswith("tools/n64crc")


def test_decomp_yaml_names_the_rom_and_place_game_aliases_the_build_scripts_name(tmp_path):
    """A community decomp whose decomp.yaml says paperboy.z64 beside a configure.py that reads baserom.z64: setup
    knows where the ROM goes (and which version a hash is) and leaves it under both names."""
    from decompnet.forks.project import ForkProject

    root = tmp_path / "p"
    root.mkdir()
    (root / "decomp.yaml").write_text(
        "versions:\n- name: us\n  sha1: " + "a" * 40 + "\n  paths:\n    target: game.z64\n"
        "- name: eu\n  paths:\n    baserom: ver/eu/baserom.z64\n")
    (root / "configure.py").write_text('rom = open("baserom.z64")\n')
    (root / "eu.sha1").write_text("b" * 40 + "  baserom.z64\n")
    fp = ForkProject(root)
    assert fp.decomp_yaml_roms() == {"us": ("game.z64", "a" * 40), "eu": ("ver/eu/baserom.z64", "b" * 40)}
    assert fp.rom_originals() == {"us": "a" * 40, "eu": "b" * 40}
    assert fp.needs("eu")["path"] == "ver/eu/baserom.z64"


def test_n64_wraps_a_linux_ido_driver_on_a_mac(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import buildtools

    source = tmp_path / "installed"
    source.mkdir()
    (source / "cc").write_text("compiler")
    monkeypatch.setattr(buildtools.sys, "platform", "darwin")
    monkeypatch.setattr(buildtools.toolchains, "installed", lambda tid: {"id": tid})
    monkeypatch.setattr(buildtools.toolchains, "require", lambda tid: source)
    root = tmp_path / "project"
    driver = root / "tools" / "ido_5.3" / "usr" / "lib" / "cc"
    driver.parent.mkdir(parents=True)
    driver.write_bytes(b"\x7fELF....")
    buildtools._wrap_foreign_ido(SimpleNamespace(root=root))
    assert driver.read_text().startswith("#!/bin/sh") and str(source / "cc") in driver.read_text()
    assert driver.with_name("cc.linux").read_bytes().startswith(b"\x7fELF")


def test_a_makefile_or_readme_names_the_rom_when_nothing_else_does(tmp_path):
    from decompnet.forks.project import ForkProject

    root = tmp_path / "p"
    root.mkdir()
    (root / "Makefile").write_text("VERSION ?= us\nBASEROM := baserom.$(VERSION).z64\nsetup:\n\ttrue\n")
    fp = ForkProject(root)
    assert fp.needs(None)["path"] == "baserom.us.z64"
    assert fp.setup_commands(None)[0][-1] == "setup"
    (root / "Makefile").write_text("all:\n\ttrue\n")
    (root / "README.md").write_text("Copy your rom to roms/baserom.us.z64 and run ./setup\n")
    assert ForkProject(root).needs(None)["path"] == "roms/baserom.us.z64"


def test_a_missing_python_tool_is_installed_into_the_project_environment(tmp_path, monkeypatch):
    from decompnet.forks import project as project_module
    from decompnet.forks.project import ForkProject

    root = tmp_path / "p"
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "pyvenv.cfg").write_text("")
    (root / ".venv" / "bin" / "python3").write_text("")
    fp = ForkProject(root)
    ran = []
    monkeypatch.setattr(project_module, "_tool", lambda name: "/bin/uv")
    monkeypatch.setattr(fp, "run", lambda cmd, log=print, **kw: (ran.append(cmd) or (True, "")))
    assert fp._install_missing_tools("FAILED: [code=127] x\npigment64 to-bin a b\n/bin/sh: pigment64: command not found", print)
    assert ran[0][:3] == ["/bin/uv", "pip", "install"] and ran[0][-1] == "pigment64"
    assert not fp._install_missing_tools("make: *** Error 1", print)
