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
    tc = toolchains.get("msvc2015")
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
    assert not any(t.wine for tid, t in toolchains.registry().items() if tid != "msvc2015")  # (the rest: wibo)


def test_without_wine_the_pack_says_how_to_get_it_before_downloading(tc_root, monkeypatch):
    monkeypatch.setattr(wine, "find", lambda: None)
    monkeypatch.setattr(wine.sys, "platform", "linux")
    with pytest.raises(toolchains.ToolchainError, match="no wine.*apt install wine"):
        toolchains.install("msvc2015", accept_license=True, log=lambda *_: None)
    assert toolchains.installed("msvc2015") is None and not (toolchains.root() / ".downloads").exists()
    monkeypatch.setattr(wine.sys, "platform", "darwin")
    assert "Gcenx/macOS_Wine_builds" in toolchains.runtime_error("msvc2015")
    # each pack that needs wine names itself (a 2015 or 2019 pack would say its own name, not 2017's)
    assert toolchains.runtime_error("msvc2015").startswith(f"{toolchains.get('msvc2015').name} runs through wine")
    assert wine.problem("Visual C++ 2019").startswith("Visual C++ 2019 runs through wine")
    monkeypatch.setattr(wine.sys, "platform", "win32")  # Windows runs the compilers itself
    assert toolchains.runtime_error("msvc2015") is None and wine.problem() is None


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
    assert env["WINEPREFIX"] == str(wine.prefix()) and env["WINEDEBUG"] == "-all"
    assert "mscoree" in env["WINEDLLOVERRIDES"]


def test_a_development_copy_has_a_wine_prefix_of_its_own(tc_root, monkeypatch):
    """Review of #136: both copies share the toolchains folder, so a development copy kept the installed app's
    prefix and its quit stopped the installed app's compiles. A development copy uses another; a release keeps the
    prefix existing installs have, and a prefix named in the environment (tests, a scratch home) stays."""
    from decompnet.app import cli, edition

    monkeypatch.setenv("DECOMPNET_WINE_PREFIX", "")  # (unset for the app; the fixture undoes what the app sets)
    monkeypatch.setattr(edition, "DEV", False)
    cli.use_own_wine_prefix()
    assert wine.prefix() == toolchains.root() / ".wine"
    monkeypatch.setattr(edition, "DEV", True)
    cli.use_own_wine_prefix()
    assert wine.prefix() == toolchains.root() / ".wine-dev"
    monkeypatch.setenv("DECOMPNET_WINE_PREFIX", str(tc_root / "scratch"))
    cli.use_own_wine_prefix()
    assert wine.prefix() == tc_root / "scratch"


def test_quitting_one_copy_stops_only_its_own_wine_server(tc_root, monkeypatch):
    """Review of #136: one prefix served both copies, so `wineserver -k` at a development copy's quit killed a
    Visual C++ 2017 compile running in the installed app (and the other way around)."""
    from decompnet.app import cli, edition

    ran = []
    monkeypatch.setattr(wine, "needed", lambda: True)
    monkeypatch.setattr(wine, "find", lambda: "/opt/wine/bin/wine")
    monkeypatch.setattr(wine, "server", lambda w: "/opt/wine/bin/wineserver")
    monkeypatch.setattr(wine.subprocess, "run", lambda argv, **k: (ran.append((argv, k["env"]["WINEPREFIX"])),
                                                                  subprocess.CompletedProcess(argv, 0))[1])
    monkeypatch.setenv("DECOMPNET_WINE_PREFIX", "")  # (unset for the app; the fixture undoes what the app sets)
    monkeypatch.setattr(edition, "DEV", False)
    cli.use_own_wine_prefix()
    release = wine.prefix()
    release.mkdir(parents=True)
    monkeypatch.setattr(edition, "DEV", True)
    cli.use_own_wine_prefix()
    dev = wine.prefix()
    dev.mkdir(parents=True)
    assert dev == toolchains.root() / ".wine-dev" and dev != release
    wine.stop()
    assert ran == [(["/opt/wine/bin/wineserver", "-k"], str(dev))]
    monkeypatch.setenv("DECOMPNET_WINE_PREFIX", str(release))
    wine.stop()
    assert ran[-1] == (["/opt/wine/bin/wineserver", "-k"], str(release))


@pytest.mark.skipif(not wine.needed() or wine.problem() is not None, reason="needs wine (Linux, macOS)")
def test_the_prefix_is_made_once_and_leaves_no_server(tc_root):
    """wineboot once (its registry written: its own server stopped first); a second prepare finds it made. No pipe of
    ours is held (prepare returned at all), and no server is left: one no wine program has used never starts its idle
    countdown (wineserver -p600), so it would stay for good. The first compile starts one."""
    where = wine.prepare(log=lambda *_: None)
    ws = wine.server(wine.find())
    try:
        assert (where / "system.reg").is_file() and (where / "drive_c").is_dir()
        started = time.monotonic()
        assert wine.prepare(log=lambda *_: None) == where and time.monotonic() - started < 10
        assert subprocess.run([ws, "-k"], env=wine.environment(where)).returncode == 1  # (none to stop)
    finally:
        subprocess.run([ws, "-k"], env=wine.environment(where))


@pytest.mark.parametrize("where", ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin",
                                   *(f"/Applications/Wine {kind}.app/Contents/Resources/wine/bin"
                                     for kind in ("Stable", "Staging", "Devel"))])
def test_wine_is_looked_for_where_each_mac_install_puts_it(where):
    """Homebrew (Apple Silicon, Intel), MacPorts (Gcenx's ports, under /opt/local) and Gcenx's app bundles, whichever
    of wine's releases (Stable, Staging, Devel): each in Applications, the system's or the user's own."""
    assert where in wine.PLACES
    if where.startswith("/Applications/"):
        assert where.replace("/Applications", str(wine.Path.home() / "Applications"), 1) in wine.PLACES


def test_wineboot_runs_under_the_memory_cap(tc_root, monkeypatch, tmp_path):
    """Making the prefix starts wineboot and wine's services: a heavy tool, run through limits.capped like the
    others."""
    ran = []
    monkeypatch.setattr(wine, "problem", lambda what="": None)
    monkeypatch.setattr(wine, "find", lambda: "/opt/wine/bin/wine")
    monkeypatch.setattr(wine, "server", lambda w: None)
    monkeypatch.setattr(wine.limits, "capped", lambda argv, *a, **k: ["capped", *argv])

    def run(argv, **kwargs):
        ran.append(argv)
        (tmp_path / "prefix").mkdir(exist_ok=True)
        (tmp_path / "prefix" / "system.reg").write_text("")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(wine.subprocess, "run", run)
    assert wine.prepare(log=lambda *_: None, where=tmp_path / "prefix") == tmp_path / "prefix"
    assert ran == [["capped", "/opt/wine/bin/wine", "wineboot", "--init"]]


def test_the_server_leaves_by_itself_and_when_the_app_quits(tc_root, monkeypatch, tmp_path):
    """Review of #136: `wineserver -p` stayed forever. The build starts it for SERVER_IDLE_SECONDS after its last
    compile (tools/compile.py: wineserver -p600, the routing test in test_pc.py), so it leaves however the app ended
    (quit, Ctrl-C, killed), and stop() ends it at once when the app quits."""
    ran = []
    monkeypatch.setattr(wine, "needed", lambda: True)
    monkeypatch.setattr(wine, "find", lambda: "/opt/wine/bin/wine")
    monkeypatch.setattr(wine, "server", lambda w: "/opt/wine/bin/wineserver")
    monkeypatch.setattr(wine.subprocess, "run", lambda argv, **k: (ran.append((argv, k["env"]["WINEPREFIX"])),
                                                                  subprocess.CompletedProcess(argv, 0))[1])
    wine.stop()  # (no prefix made yet: nothing to stop)
    assert ran == []
    wine.prefix().mkdir(parents=True)
    wine.stop()
    assert ran == [(["/opt/wine/bin/wineserver", "-k"], str(wine.prefix()))]


def test_a_prefix_can_be_a_scratch_one(tc_root, monkeypatch, tmp_path):
    monkeypatch.setenv("DECOMPNET_WINE_PREFIX", str(tmp_path / "scratch"))
    assert wine.prefix() == tmp_path / "scratch" and wine.environment()["WINEPREFIX"] == str(tmp_path / "scratch")


def test_a_prefix_that_fails_fails_the_install_with_its_own_reason(tc_root, monkeypatch):
    """Review of #136: a prefix wineboot couldn't make was only logged, and the build later said "install wine"
    though wine was there. The install fails with what went wrong, and isn't kept."""
    import hashlib
    import tarfile

    src = tc_root / "src"
    src.mkdir()
    (src / "cl.exe").write_text("")
    archive = tc_root / "pack.tar.gz"
    with tarfile.open(archive, "w:gz") as t:
        t.add(src / "cl.exe", arcname="cl.exe")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    pack = toolchains.Toolchain(id="pack", name="A Visual C++ pack", license="MIT", source="test", wine=True,
                                parts=[{"url": archive.as_uri(), "sha256": digest}])
    monkeypatch.setattr(toolchains, "registry", lambda: {"pack": pack})
    monkeypatch.setattr(wine, "needed", lambda: True)
    monkeypatch.setattr(wine, "problem", lambda what="": None)

    def fails(log=print, where=None):
        raise RuntimeError("wine couldn't prepare its prefix in /x (wineboot exited with 1): out of disk")

    monkeypatch.setattr(wine, "prepare", fails)
    with pytest.raises(toolchains.ToolchainError,
                       match="A Visual C\\+\\+ pack downloaded, but wine couldn't prepare its prefix in /x "
                             ".*out of disk"):
        toolchains.install("pack", log=lambda *_: None)
    assert toolchains.installed("pack") is None and not toolchains.path("pack").exists()
