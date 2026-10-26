"""Updates: decompdecomp finds a newer version (a release, or new commits on the branch it was installed from), installs
it with uv and restarts in place. Offline: GitHub's answers are stand-ins."""

import json
import os
import socket
import stat
import sys
import urllib.error
from pathlib import Path

import pytest

from decompnet import __version__
from decompnet.app import updates


@pytest.fixture(autouse=True)
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "data"))
    monkeypatch.setattr(updates, "info_file", lambda: tmp_path / "data" / "install.json")
    return tmp_path / "data"


def test_install_info_round_trip():
    assert updates.installed()["channel"] == "release"  # nothing recorded: a release install
    updates.record("branch", "cross-platform", "a" * 40)
    cur = updates.installed()
    assert (cur["channel"], cur["branch"], cur["commit"], cur["version"]) == ("branch", "cross-platform", "a" * 40, __version__)
    updates.record("release", "ignored", "ignored")
    assert updates.installed()["branch"] == "" and updates.installed()["commit"] == ""


def test_release_update(monkeypatch):
    newer = ".".join(str(x + (1 if i == 2 else 0)) for i, x in enumerate(updates.version_key(__version__)[:3]))
    def get(url, timeout=15):
        assert url == "https://raw.githubusercontent.com/n-delic/decompdecomp/main/site/public/install.sh"
        return f'#!/bin/sh\nVERSION="{newer}"\n'.encode()
    monkeypatch.setattr(updates, "_get", get)
    found = updates.check()
    assert found["available"] and found["latest"]["version"] == newer and found["error"] is None
    assert found["spec"] == f"decompnet @ https://github.com/n-delic/decompdecomp/archive/refs/tags/v{newer}.tar.gz"
    monkeypatch.setattr(updates, "_get", lambda url, timeout=15: f'VERSION="{__version__}"\n'.encode())
    assert not updates.check()["available"]


def test_branch_update(monkeypatch):
    have, head = "1" * 40, "2" * 40
    updates.record("branch", "cross-platform", have)
    answers = {
        "commits/cross-platform": {"sha": head, "commit": {"message": "Newest\n\nbody", "committer": {"date": "2026-10-14"}}},
        f"compare/{have}...{head}": {"status": "ahead", "commits": [{"commit": {"message": "Older one"}},
                                                                   {"commit": {"message": "Newest\n\nbody"}}]},
    }
    monkeypatch.setattr(updates, "_json", lambda url: next(v for k, v in answers.items() if url.endswith(k)))
    monkeypatch.setattr(updates, "_branch_version", lambda repo, sha: "0.10.0")
    found = updates.check()
    assert found["available"] and found["latest"]["commit"] == head and found["notes"] == ["Newest", "Older one"]
    assert found["spec"] == f"decompnet @ https://github.com/n-delic/decompdecomp/archive/{head}.tar.gz"

    # installed from a commit newer than the branch (pushed elsewhere): not an "update"
    answers[f"compare/{have}...{head}"] = {"status": "behind", "commits": []}
    assert not updates.check()["available"]

    # a commit GitHub doesn't know (never pushed): the branch's newest is offered
    def unknown(url):
        if "compare" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return answers["commits/cross-platform"]
    monkeypatch.setattr(updates, "_json", unknown)
    found = updates.check()
    assert found["available"] and found["notes"] == ["Newest"]

    updates.record("branch", "cross-platform", head)
    assert not updates.check()["available"]


def test_offline_check_says_why(monkeypatch):
    def offline(url, timeout=15):
        raise urllib.error.URLError("no network")
    monkeypatch.setattr(updates, "_get", offline)
    found = updates.check()
    assert not found["available"] and "couldn't check" in found["error"]


@pytest.mark.skipif(sys.platform == "win32", reason="a shell script stands in for uv")
def test_install_runs_uv_then_the_new_setup(tmp_path, monkeypatch):
    calls = tmp_path / "calls"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "decomp").write_text(f'#!/bin/sh\necho "decomp $*" >> "{calls}"\necho "tools ok"\n')
    uv = tmp_path / "uv"
    uv.write_text(f'#!/bin/sh\nif [ "$1 $2" = "tool dir" ]; then echo "{bindir}"; exit 0; fi\n'
                  f'echo "uv $*" >> "{calls}"\necho "Installed 1 executable: decomp"\n')
    for f in (uv, bindir / "decomp"):
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(updates, "uv", lambda: str(uv))
    monkeypatch.setattr(updates.edition, "DEV", False)  # (the tests run from a checkout: a development copy)
    updates.record("branch", "cross-platform", "1" * 40)
    found = {"current": updates.installed(), "available": True, "latest": {"version": "0.10.0", "commit": "2" * 40},
             "spec": "decompnet @ https://github.com/n-delic/decompdecomp/archive/" + "2" * 40 + ".tar.gz"}
    said = []
    updates.install(found, log=said.append)
    ran = calls.read_text().splitlines()
    assert ran[0] == f"uv tool install --force --python 3.12 {found['spec']}" and ran[1] == "decomp setup"
    assert any("tools ok" in s for s in said)
    assert json.loads(updates.info_file().read_text())["commit"] == "2" * 40  # the next check starts from here

    (bindir / "decomp").write_text("#!/bin/sh\necho broken >&2\nexit 3\n")
    with pytest.raises(RuntimeError, match="exit code 3"):
        updates.install(found, log=said.append)


def test_windows_update_runs_the_new_exe(tmp_path, monkeypatch):
    import subprocess

    bindir = tmp_path / "Tool bin ü"
    bindir.mkdir()
    executable = bindir / "decomp.exe"
    executable.write_bytes(b"stand-in")
    monkeypatch.setattr(updates.sys, "platform", "win32")
    monkeypatch.setattr(updates, "uv", lambda: "uv.exe")
    monkeypatch.setattr(updates, "can_apply", lambda: (True, ""))
    calls = []
    monkeypatch.setattr(updates, "_stream", lambda argv, log, failure: calls.append(argv))
    monkeypatch.setattr(updates.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(
        argv, 0, str(bindir), ""))
    found = {"current": updates.installed(), "latest": {"version": "1.0.4"}, "spec": "test-wheel.whl"}
    updates.install(found, log=lambda _: None)
    assert calls[0] == ["uv.exe", "pip", "install", "--python", sys.executable,
                        "--reinstall-package", "decompnet", found["spec"]]
    assert calls[1] == [str(executable), "setup"]
    assert json.loads(updates.info_file().read_text())["version"] == "1.0.4"


def test_restart_keeps_port_and_secret(monkeypatch):
    from decompnet.app import cli

    monkeypatch.setenv("DECOMP_RESTART", json.dumps({"port": 6501, "secret": "abc"}))
    assert cli._restart_state() == {"port": 6501, "secret": "abc"}
    assert "DECOMP_RESTART" not in os.environ  # a later restart (or a child) doesn't inherit it
    monkeypatch.setenv("DECOMP_RESTART", "garbage")
    assert cli._restart_state() is None

    # the port the old process had is taken back exactly, also while connections from before linger
    first = cli._listen(0 + 6502, exact=True)
    port = first.getsockname()[1]
    peer = socket.create_connection(("127.0.0.1", port))
    conn, _ = first.accept()
    conn.close()  # the server side closes first: TIME_WAIT on the server's port
    peer.close()
    first.close()
    again = cli._listen(port, exact=True)
    assert again.getsockname()[1] == port
    again.close()


def test_update_api(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from decompnet.app.engine import RomNetwork
    from decompnet.app.server import build

    monkeypatch.setattr(updates, "check", lambda: {"current": updates.installed(), "available": False, "latest": None,
                                                   "notes": [], "link": None, "error": None, "checked": 1e12,
                                                   "can_apply": True, "why_not": ""})
    home = tmp_path / "home"
    (home / ".decomp").mkdir(parents=True)
    net = RomNetwork(home / ".decomp" / "decomp.db", home)
    from decompnet.app import window

    mcp, manager, security, _ = build(net, home / ".decomp", 6498, "sec")
    client = TestClient(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                        base_url="http://127.0.0.1:6498")
    assert client.get("/api/update").status_code == 401  # the window's secret, as everywhere
    client.headers["User-Agent"] = f"{window.TOKEN_AGENT}sec"
    monkeypatch.setenv("DECOMP_UPDATES", "on")
    st = client.get("/api/update").json()
    assert st["available"] is False and st["job"]["state"] == "idle"
    r = client.post("/api/update", json={"apply": True}, headers={"X-Decomp": "1"})
    assert r.status_code == 400 and "up to date" in r.json()["error"]
    assert client.post("/api/settings", json={"check_updates": False}, headers={"X-Decomp": "1"}).json()["check_updates"] is False
    assert client.get("/api/update").json()["disabled"] is True
    assert "build" in client.get("/health").json()


def test_apply_installs_what_a_fresh_check_finds(monkeypatch):
    """The app's cached check can be minutes old: installing from it recorded an older commit than `decomp update`
    had just found, so the same update was offered again."""
    old, new = {"available": True, "latest": {"commit": "1" * 40}}, {"available": True, "latest": {"commit": "2" * 40}}
    monkeypatch.setattr(updates, "check", lambda: new)
    monkeypatch.setattr(updates, "can_apply", lambda: (True, ""))
    installed = []
    monkeypatch.setattr(updates, "install", lambda found, log=print: installed.append(found))
    up = updates.Updater(lambda: None, 0, "")
    monkeypatch.setattr(updates, "restart", lambda port, secret, window: None)
    up.last = old
    up.apply()
    import threading
    for t in threading.enumerate():  # its install thread must end while restart is still the stand-in
        if t.name == "update":
            t.join(10)
    assert installed == [new]


def test_a_pre_release_comes_before_its_release():
    """An alpha must update to its beta and to the release (comparing only the numbers, 1.0.0-alpha == 1.0.0)."""
    order = ["0.11.5", "1.0.0-alpha", "1.0.0a1", "1.0.0-beta.2", "1.0.0rc1", "1.0.0", "1.0.1", "1.1.0"]
    keys = [updates.version_key(v) for v in order]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    assert updates.version_key("v1.0.0-alpha") == updates.version_key("1.0.0a0")  # a tag and Python's spelling


def test_a_packaged_install_points_at_the_releases(monkeypatch):
    """A .deb or an AppImage isn't a uv tool: installing over it with uv would leave the package behind."""
    monkeypatch.setenv("DECOMP_PACKAGE", "appimage")
    ok, why = updates.can_apply()
    assert not ok and "AppImage" in why and f"github.com/{updates.REPO}/releases" in why


def test_the_windows_setup_can_update_itself(monkeypatch):
    from decompnet.app import edition

    monkeypatch.setenv("DECOMP_PACKAGE", "windows")
    monkeypatch.setattr(edition, "DEV", False)
    monkeypatch.setattr(updates, "uv", lambda: "uv.exe")
    ok, why = updates.can_apply()
    assert ok and not why


def test_a_windows_setup_reinstalls_its_bundle_and_runs_setup(monkeypatch, tmp_path):
    from decompnet.app import edition

    monkeypatch.setenv("DECOMP_PACKAGE", "windows")
    monkeypatch.setattr(updates.sys, "platform", "win32")
    monkeypatch.setattr(edition, "DEV", False)
    monkeypatch.setattr(updates, "uv", lambda: "uv.exe")
    monkeypatch.setattr(updates, "_stream", lambda cmd, _log, _failure: calls.append(cmd))
    monkeypatch.setattr(updates, "info_file", lambda: tmp_path / "update.json")
    calls = []
    found = {"current": {"channel": "release"}, "latest": {"version": "1.2.3"}, "spec": "new-wheel"}

    updates.install(found, log=lambda _line: None)

    assert calls[0][:5] == ["uv.exe", "pip", "install", "--python", updates.sys.executable]
    assert calls[0][5:7] == ["--target", str(Path(updates.sys.executable).resolve().parent.parent / "packages")]
    assert calls[1] == [updates.sys.executable,
                        str(Path(updates.sys.executable).resolve().parent.parent / "decomp-launch.pyw"), "setup"]


def test_an_install_from_main_updates_on_releases_only():
    """Installed from a checkout of main, every commit used to be offered as an update (a website edit too)."""
    updates.record("branch", branch="main", commit="abc123")
    assert updates.installed()["channel"] == "release"
    updates.record("branch", branch="window-fix", commit="abc123")
    assert updates.installed()["channel"] == "branch"


def test_an_update_from_1_0_2_restarts_into_the_new_version_with_a_window(monkeypatch):
    """1.0.2's updater restarts into the new version with `--no-browser --port N` and no word about a window (it had
    a browser tab): the new `decomp` must take those arguments, or the update ends the app, and open its window."""
    import json

    from decompnet.app import cli

    ran = {}
    monkeypatch.setattr(cli, "run", lambda args: ran.update(port=args.port) or 0)
    assert cli.main(["--no-browser", "--port", "6464"]) == 0 and ran == {"port": 6464}
    monkeypatch.setenv("DECOMP_RESTART", json.dumps({"port": 6464, "secret": "s"}))
    restarted = cli._restart_state()
    assert restarted.get("window", True) is True  # how run() reads it: open the window


def test_a_development_copy_never_updates_itself(monkeypatch):
    """A checkout's Settings said updates came from GitHub's releases, and "Update now" would have installed a release
    beside it: a second app. It updates with git; the app says so."""
    from decompnet.app import edition
    monkeypatch.delenv("DECOMP_PACKAGE", raising=False)
    monkeypatch.setattr(edition, "DEV", True)
    ok, why = updates.can_apply()
    assert not ok and "development copy" in why


def test_settings_switch_between_releases_and_a_branch(monkeypatch):
    """Users pointed at a branch for support reported issues from it without saying so; the switch was the command
    line only. Settings switches now: a branch's newest commit, or back to releases (whatever their version), and
    updates follow the new source from then on."""
    head = "3" * 40
    monkeypatch.setattr(updates, "_json", lambda url: {"sha": head, "commit": {"message": "Newest", "committer": {}}}
                        if url.endswith("commits/polish-everywhere") else (_ for _ in ()).throw(
                            urllib.error.HTTPError(url, 404, "Not Found", {}, None)))
    monkeypatch.setattr(updates, "_branch_version", lambda repo, sha: "1.0.6a0")
    found = updates.switch_to("branch", "polish-everywhere")
    assert found["available"] and found["current"]["branch"] == "polish-everywhere"
    assert found["spec"] == f"decompnet @ https://github.com/n-delic/decompdecomp/archive/{head}.tar.gz"
    with pytest.raises(ValueError, match="no branch gone-branch on GitHub"):
        updates.switch_to("branch", "gone-branch")
    with pytest.raises(ValueError, match="isn't a branch name"):
        updates.switch_to("branch", "a b")
    updates.record("branch", "polish-everywhere", head)
    monkeypatch.setattr(updates, "_get", lambda url, timeout=15: b'VERSION="0.0.1"\n')  # older than the branch
    back = updates.switch_to("branch", "main")  # main is what releases are made from
    assert back["available"] and back["current"]["channel"] == "release" and back["latest"]["version"] == "0.0.1"
    monkeypatch.setattr(updates, "can_apply", lambda: (True, ""))
    installed = []
    monkeypatch.setattr(updates, "install", lambda found, log=print: installed.append(found["current"]["channel"]))
    monkeypatch.setattr(updates, "restart", lambda port, secret, window: None)
    updates.Updater(lambda: None, 0, "").apply("release")
    import threading
    for t in threading.enumerate():
        if t.name == "update":
            t.join(10)
    assert installed == ["release"]
