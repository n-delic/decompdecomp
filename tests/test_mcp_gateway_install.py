"""`decomp setup` gives an install from source the release's MCP gateway, checked against its .sha256."""
import hashlib
import io
import os
import urllib.error

import pytest

from decompnet.app import mcp_gateway

PROGRAM = b"#!/bin/sh\necho gateway\n"


@pytest.fixture
def release(tmp_path, monkeypatch):
    """A fake GitHub: `assets` maps a release URL to its bytes; anything else is a 404."""
    monkeypatch.setenv("DECOMPNET_TOOLCHAINS", str(tmp_path / "toolchains"))
    monkeypatch.setattr(mcp_gateway, "_local", lambda: (tmp_path / "pkg" / "none", tmp_path / "checkout" / "none"))
    assets, fetched = {}, []

    def urlopen(request, timeout=None):
        fetched.append(request.full_url)
        if request.full_url not in assets:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
        return io.BytesIO(assets[request.full_url])

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    def publish(tag, data=PROGRAM, digest=None):
        base = f"https://github.com/o/r/releases/{tag}/{mcp_gateway.asset_name()}"
        assets[base] = data
        assets[base + ".sha256"] = f"{digest or hashlib.sha256(data).hexdigest()}  {mcp_gateway.asset_name()}\n".encode()
    return publish, fetched


def test_installs_this_versions_release_build(release):
    publish, _ = release
    publish("download/v9.9.9")
    path = mcp_gateway.install(log=lambda _: None, repo="o/r", version="9.9.9")
    assert path == mcp_gateway.downloaded() and path.read_bytes() == PROGRAM
    assert os.access(path, os.X_OK) or os.name == "nt"
    assert mcp_gateway.built() == path


def test_an_unreleased_version_takes_the_latest(release):
    publish, fetched = release
    publish("latest/download")
    assert mcp_gateway.install(log=lambda _: None, repo="o/r", version="9.9.10") == mcp_gateway.downloaded()
    assert any("/download/v9.9.10/" in url for url in fetched)


def test_a_wrong_checksum_installs_nothing(release):
    publish, _ = release
    publish("download/v9.9.9", digest="0" * 64)
    lines = []
    assert mcp_gateway.install(log=lines.append, repo="o/r", version="9.9.9") is None
    assert not mcp_gateway.downloaded().exists() and "checksum" in lines[-1]


def test_up_to_date_downloads_only_the_checksum(release):
    publish, fetched = release
    publish("download/v9.9.9")
    mcp_gateway.install(log=lambda _: None, repo="o/r", version="9.9.9")
    fetched.clear()
    mcp_gateway.install(log=lambda _: None, repo="o/r", version="9.9.9")
    assert fetched and all(url.endswith(".sha256") for url in fetched)


def test_no_release_build_reports_unavailable(release):
    lines = []
    assert mcp_gateway.install(log=lines.append, repo="o/r", version="9.9.9") is None
    assert "no release build available" in lines[-1]


def test_a_packaged_gateway_needs_no_download(release, tmp_path, monkeypatch):
    _, fetched = release
    packaged = tmp_path / "pkg" / mcp_gateway.NAME
    packaged.parent.mkdir(parents=True)
    packaged.write_bytes(PROGRAM)
    packaged.chmod(0o755)
    monkeypatch.setattr(mcp_gateway, "_local", lambda: (packaged, tmp_path / "none"))
    assert mcp_gateway.install(log=lambda _: None, repo="o/r", version="9.9.9") == packaged
    assert fetched == []


def test_startup_provisions_a_missing_gateway(release, monkeypatch):
    publish, fetched = release
    publish("latest/download")
    monkeypatch.delenv("DECOMP_MCP_GATEWAY")
    from decompnet.app import updates
    monkeypatch.setattr(updates, "installed", lambda: {"repo": "o/r"})
    assert mcp_gateway.startup_program() == mcp_gateway.downloaded()
    assert fetched
    fetched.clear()
    assert mcp_gateway.startup_program() == mcp_gateway.downloaded()
    assert fetched == []


def test_startup_requires_a_gateway_when_no_build_is_available(release, monkeypatch):
    monkeypatch.delenv("DECOMP_MCP_GATEWAY")
    with pytest.raises(RuntimeError, match="No MCP gateway build is available"):
        mcp_gateway.start(1234)


def test_startup_respects_an_explicit_gateway_path(release, monkeypatch, tmp_path):
    _, fetched = release
    executable = tmp_path / "custom-gateway"
    monkeypatch.setenv("DECOMP_MCP_GATEWAY", str(executable))
    with pytest.raises(RuntimeError, match="Correct DECOMP_MCP_GATEWAY"):
        mcp_gateway.startup_program()
    executable.write_bytes(PROGRAM)
    assert mcp_gateway.startup_program() == executable
    assert fetched == []


def test_failed_gateway_launch_stops_the_gateway(monkeypatch, tmp_path):
    from unittest.mock import Mock

    gateway = Mock()
    gateway.start.side_effect = OSError("cannot execute")
    monkeypatch.setattr(mcp_gateway, "Gateway", Mock(return_value=gateway))
    with pytest.raises(RuntimeError, match="MCP gateway could not start: cannot execute"):
        mcp_gateway.start(1234, executable=tmp_path / "gateway")
    gateway.stop.assert_called_once()
