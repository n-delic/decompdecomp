"""The app builds the RT64 adapter in the background, once per change, and never installs Xcode for it."""
import json
import sys

import pytest

from decompnet.app import rt64_autobuild
from decompnet.consoles.n64 import rt64_build


@pytest.fixture
def mac(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(rt64_build, "folder", lambda: tmp_path)
    monkeypatch.setattr(rt64_build, "problem", lambda: "")
    for name in ("DECOMP_RT64_AUTOBUILD", "DECOMP_AUTO_TOOLS", "DECOMPNET_RT64_ADAPTER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "bin/python"))
    return tmp_path


def test_builds_when_missing_and_ready(mac):
    assert rt64_autobuild.unavailable() == ""


def test_stays_quiet_without_xcode(mac, monkeypatch):
    monkeypatch.setattr(rt64_build, "problem", lambda: "Install the Metal Toolchain")
    assert rt64_autobuild.unavailable() == "Install the Metal Toolchain"


@pytest.mark.parametrize("name,value", [("DECOMP_RT64_AUTOBUILD", "0"), ("DECOMP_AUTO_TOOLS", "off"),
                                        ("DECOMPNET_RT64_ADAPTER", "/x/lib.dylib")])
def test_the_user_can_turn_it_off(mac, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    assert rt64_autobuild.unavailable()


def test_not_on_other_systems(mac, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert "macOS" in rt64_autobuild.unavailable()


def test_current_build_is_kept_and_a_changed_stamp_rebuilds(mac, monkeypatch):
    (mac / rt64_build.LIBRARY).write_text("x")
    (mac / rt64_build.STAMP).write_text(json.dumps(rt64_build.stamp()))
    assert rt64_autobuild.unavailable() == "the adapter is current"
    (mac / rt64_build.STAMP).write_text(json.dumps({"sources": {}, "adapter": "old"}))
    assert rt64_autobuild.unavailable() == ""


def test_a_failed_build_is_not_retried_for_the_same_stamp(mac):
    (mac / rt64_autobuild.FAILED).write_text(json.dumps(rt64_build.stamp()))
    assert "already failed" in rt64_autobuild.unavailable()
    (mac / rt64_autobuild.FAILED).write_text(json.dumps({"sources": {}, "adapter": "old"}))
    assert rt64_autobuild.unavailable() == ""


def test_the_row_in_background_work(mac):
    build = rt64_autobuild.AutoBuild(delay=0)
    assert build.snapshot() is None
    build.process = object()
    build.phase = "Building rt64"
    assert build.snapshot()["detail"].startswith("Building rt64")
    build.stopping.set()
    assert build.snapshot()["state"] == "stopping"


def test_tool_script_and_app_share_one_build():
    from pathlib import Path
    tool = (Path(__file__).resolve().parents[1] / "tools/build_rt64_adapter.py").read_text()
    assert "rt64_build.main()" in tool
