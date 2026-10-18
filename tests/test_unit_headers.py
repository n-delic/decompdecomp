"""Per-unit headers: each part of a library compiles with the header of its own unit."""

import pytest

from decompnet import toolchains
from decompnet.network import Network, NetworkError

from .handmade import import_project
from .test_network import DEMO, HOST_GCC, needs_host_gcc

PROJECTS = DEMO.parent


@pytest.fixture
def net(tmp_path):
    if not HOST_GCC:
        pytest.skip(needs_host_gcc.kwargs["reason"])
    n = Network(tmp_path / "t.db")
    import_project(n.store, DEMO, log=lambda *_: None)
    return n


needs_n64 = pytest.mark.skipif(toolchains.installed("gcc-2.7.2-kmc") is None, reason="gcc-2.7.2-kmc not installed")


zlib_ready = (PROJECTS / "zlib-n64" / "original" / "zlib.h").exists()


@needs_n64
@pytest.mark.skipif(not zlib_ready, reason="run projects/zlib-n64/prepare.py first")
def test_zlib_uses_per_unit_headers(tmp_path):
    net = Network(tmp_path / "z.db")
    import_project(net.store, PROJECTS / "zlib-n64", log=lambda *_: None)
    assert net.store.one("SELECT COUNT(*) AS n FROM functions")["n"] == 71
    assert net._unit_contexts(net._project("zlib-n64")["id"]) >= {"deflate", "inflate", "trees"}
    with pytest.raises(NetworkError, match="pass `function`"):
        net.get_context("zlib-n64")
    assert "deflate_state" in net.get_context("zlib-n64", "fill_window")
    agent = net.authenticate(net.register_agent("z"))
    src = (PROJECTS / "zlib-n64" / "original" / "adler32.c").read_text()
    body = src[src.index("uLong ZEXPORT adler32("):]
    assert "MATCH" in net.compile_and_diff(agent, "adler32", body[: body.index("\n}\n") + 3], project="zlib-n64")
