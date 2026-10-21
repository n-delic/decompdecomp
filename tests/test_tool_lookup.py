"""decompdecomp's own build tools are found where its installs put them, also when they aren't on PATH."""

import os

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


def test_without_a_bundle_only_pythons_own_folder_is_looked_in(tmp_path, monkeypatch):
    (tmp_path / "decompnet" / "forks").mkdir(parents=True)
    monkeypatch.setattr(project, "__file__", str(tmp_path / "decompnet" / "forks" / "project.py"))
    assert len(project._tool_dirs()) == 1
