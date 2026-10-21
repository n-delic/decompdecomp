"""decompdecomp's own build tools are found where its installs put them, also when they aren't on PATH."""

import os
from pathlib import Path

from decompnet.forks import project


def test_the_windows_bundles_ninja_is_found_beside_its_packages(tmp_path, monkeypatch):
    """The Windows setup installs the packages with --target, which puts ninja in packages/bin, and its launcher
    left that folder off PATH: every ninja project failed to build in the native app."""
    packages = tmp_path / "app" / "packages"
    (packages / "decompnet" / "forks").mkdir(parents=True)
    (packages / "bin").mkdir()
    ninja = packages / "bin" / ("ninja.exe" if os.name == "nt" else "ninja")
    ninja.write_bytes(b"")
    monkeypatch.setattr(project, "__file__", str(packages / "decompnet" / "forks" / "project.py"))
    monkeypatch.setattr(project.shutil, "which", lambda *a, **k: None)
    monkeypatch.setattr(project.sys, "executable", str(tmp_path / "app" / "python" / "python.exe"))  # no ninja there

    assert project._tool("ninja") == str(ninja)
    assert packages / "bin" in project._tool_dirs()


def test_build_steps_run_with_the_console_python_not_pythonw(tmp_path):
    """The Windows app runs as pythonw.exe, which has no console: a build step under it (configure, the compile
    wrapper) started every compiler and linker in a console window of its own, flashing on screen."""
    from decompnet.hostenv import console_python

    (tmp_path / "pythonw.exe").write_bytes(b"")
    assert console_python(str(tmp_path / "pythonw.exe")) == str(tmp_path / "pythonw.exe")  # no python.exe beside it
    (tmp_path / "python.exe").write_bytes(b"")
    assert console_python(str(tmp_path / "pythonw.exe")) == str(tmp_path / "python.exe")
    assert console_python(str(tmp_path / "PythonW.EXE")) == str(tmp_path / "python.exe")
    assert console_python("/usr/bin/python3") == "/usr/bin/python3"


def test_the_templates_configure_writes_the_console_python_into_the_build():
    root = Path(__file__).resolve().parent.parent / "src" / "decompnet" / "consoles"
    for template in ("pc", "nds"):
        text = (root / template / "project_template" / "tools" / "configure.py").read_text(encoding="utf-8")
        assert 'with_name("python.exe")' in text and "sys.executable}" not in text, template


def test_without_a_bundle_only_pythons_own_folder_is_looked_in(tmp_path, monkeypatch):
    (tmp_path / "decompnet" / "forks").mkdir(parents=True)
    monkeypatch.setattr(project, "__file__", str(tmp_path / "decompnet" / "forks" / "project.py"))
    assert len(project._tool_dirs()) == 1
