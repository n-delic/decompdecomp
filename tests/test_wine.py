"""wine: how Linux and macOS run Visual C++ 2017 (wine.py). The parts that need wine itself skip without it."""

import subprocess
import sys
import time

import pytest

from decompnet import toolchains, wine


@pytest.fixture
def tc_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DECOMPNET_TOOLCHAINS", str(tmp_path / "toolchains"))
    return tmp_path


def test_the_2017_pack_is_pinned_and_runs_through_wine():
    """Your verified downloads, each version a folder with Bin and Include (msvc2005's layout), the Windows 10 SDK's
    headers a toolchain of their own the pack names (shared by packs that use that SDK, one copy on disk), and the
    64-bit compiler with the host's program database DLLs beside it."""
    tc = toolchains.get("msvc2017")
    assert tc.wine and tc.proprietary and tc.kind == "compiler-pack" and tc.platform == "PC"
    assert all(len(p["sha256"]) == 64 for p in tc.parts)
    assert [p["into"] for p in tc.parts] == ["14.16", "14.16-x64"]
    assert tc.headers == tc.requires == ["winsdk-10.0.17763"]
    for part in tc.parts:
        assert part["keep"]["VC/Tools/MSVC/14.16.27023/include"] == "Include" and "sdk10" not in part["url"]
    sdk = toolchains.get("winsdk-10.0.17763")
    assert sdk.proprietary and sdk.platform == "PC" and [len(p["sha256"]) for p in sdk.parts] == [64]
    assert "sdk10_17763" in sdk.parts[0]["url"] and set(sdk.parts[0]["keep"].values()) == {"Include"}
    assert list(sdk.parts[0]["keep"]) == [f"Include/10.0.17763.0/{d}" for d in ("shared", "um", "ucrt")]
    x64 = tc.parts[1]["keep"]
    assert x64["VC/Tools/MSVC/14.16.27023/bin/Hostx86/x64"] == "Bin"
    assert {f"Bin/{dll}" for dll in ("mspdb140.dll", "mspdbcore.dll", "msobj140.dll")} <= set(x64.values())
    assert not any(t.wine for tid, t in toolchains.registry().items() if tid != "msvc2017")  # (the rest: wibo)


def test_without_wine_the_pack_says_how_to_get_it_before_downloading(tc_root, monkeypatch):
    monkeypatch.setattr(wine, "find", lambda: None)
    monkeypatch.setattr(wine.sys, "platform", "linux")
    with pytest.raises(toolchains.ToolchainError, match="no wine.*apt install wine"):
        toolchains.install("msvc2017", accept_license=True, log=lambda *_: None)
    assert toolchains.installed("msvc2017") is None and not (toolchains.root() / ".downloads").exists()
    monkeypatch.setattr(wine.sys, "platform", "darwin")
    assert "Gcenx/macOS_Wine_builds" in toolchains.runtime_error("msvc2017")
    monkeypatch.setattr(wine.sys, "platform", "win32")  # Windows runs the compilers itself
    assert toolchains.runtime_error("msvc2017") is None and wine.problem() is None


def test_wine_on_apple_silicon_needs_rosetta(tc_root, monkeypatch, tmp_path):
    (tmp_path / "wine").write_text("")
    (tmp_path / "wineserver").write_text("")
    monkeypatch.setattr(wine, "find", lambda: str(tmp_path / "wine"))
    monkeypatch.setattr(wine.sys, "platform", "darwin")
    monkeypatch.setattr(toolchains, "current_platform", lambda: "macos-arm64")
    monkeypatch.setattr(toolchains, "_intel_mac_runtime_ready", type("R", (), {
        "__call__": lambda self: False, "cache_clear": lambda self: None})())
    assert "Rosetta 2" in wine.problem()


@pytest.mark.skipif(sys.platform == "win32", reason="Wine is not required for Windows")
def test_wine_is_found_where_a_mac_app_has_none_on_its_path(tmp_path, monkeypatch):
    """A Mac app starts with a PATH without Homebrew: wine is looked for where Homebrew and Gcenx's app put it, and
    its wineserver beside it."""
    bin_ = tmp_path / "Wine Stable.app/Contents/Resources/wine/bin"
    bin_.mkdir(parents=True)
    for name in ("wine", "wineserver"):
        (bin_ / name).write_text("#!/bin/sh\n")
        (bin_ / name).chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    monkeypatch.setattr(wine, "PLACES", (str(bin_),))
    assert wine.find() == str(bin_ / "wine") and wine.server(wine.find()) == str(bin_ / "wineserver")
    monkeypatch.setenv("DECOMPNET_WINE", str(bin_ / "wine"))
    assert wine.find() == str(bin_ / "wine")


def test_the_prefix_is_the_apps_own(tc_root):
    """Never ~/.wine: with the toolchains (a scratch home's toolchains have their own)."""
    assert wine.prefix() == toolchains.root() / ".wine"
    env = wine.environment()
    assert env["WINEPREFIX"] == str(wine.prefix()) and env["WINEDEBUG"] == "-all" and "mscoree" in env["WINEDLLOVERRIDES"]


@pytest.mark.skipif(not wine.needed() or wine.problem() is not None, reason="needs wine (Linux, macOS)")
def test_the_prefix_is_made_once_and_its_server_stays(tc_root):
    """wineboot once (its registry written: its own server stopped first), then a server that stays; a second
    prepare finds both. No pipe of ours is held: prepare returned at all."""
    where = wine.prepare(log=lambda *_: None)
    try:
        assert (where / "system.reg").is_file() and (where / "drive_c").is_dir()
        started = time.monotonic()
        assert wine.prepare(log=lambda *_: None) == where and time.monotonic() - started < 10
        ws = wine.server(wine.find())
        assert subprocess.run([ws, "-p"], env=wine.environment(where)).returncode == 2  # (already running)
    finally:
        subprocess.run([wine.server(wine.find()), "-k"], env=wine.environment(where))
