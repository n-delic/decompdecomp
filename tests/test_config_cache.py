"""A project config is parsed once per text and shared, never re-parsed on the hot compile/brief paths.

an overlay-heavy PS2 project carries a 2 MB config blob; json.loads of it costs ~8 ms. Re-parsing it on
every compile and every brief, across many agents, was the dominant CPU cost and the ceiling on concurrency.
"""
import inspect
import json

import decompnet.network as network
from decompnet.network import Network


def test_network_cfg_parses_each_config_text_only_once(monkeypatch):
    network._settings.clear()
    calls = {"n": 0}
    real_loads = json.loads

    def counting_loads(text, *a, **k):
        calls["n"] += 1
        return real_loads(text, *a, **k)

    monkeypatch.setattr(network.json, "loads", counting_loads)
    proj = {"config": '{"compiler": {"toolchain": "mw"}, "pad": "' + "y" * 5000 + '"}'}
    first = Network.cfg(proj)
    second = Network.cfg(proj)
    assert first == {"compiler": {"toolchain": "mw"}, "pad": "y" * 5000}
    assert first is second      # the same parsed dict handed back
    assert calls["n"] == 1      # parsed once, not on the second read


def test_compile_reads_config_through_the_cfg_cache(monkeypatch):
    captured = {}

    def fake_cfg(proj):
        captured["proj"] = proj
        return {"compiler_ranges": None}

    monkeypatch.setattr(Network, "cfg", staticmethod(fake_cfg))
    monkeypatch.setattr(network, "compiler_config", lambda root, cfg: ("CC", cfg))
    recorded = {}

    def fake_compile_source(config, header, source):
        recorded["config"] = config
        return b"obj"

    monkeypatch.setattr(network, "compile_source", fake_compile_source)
    net = Network.__new__(Network)
    proj = {"config": "NOT-JSON-MUST-NOT-BE-PARSED", "root": "/tmp/x"}
    out = net._compile(proj, "header", "source")
    assert captured["proj"] is proj     # config read through the cache, not json.loads
    assert out == b"obj"


def test_network_hot_paths_do_not_parse_config_directly():
    for method in (Network._compile, Network._brief):
        body = inspect.getsource(method)
        assert 'json.loads(proj["config"])' not in body, (
            f"{method.__name__} must read config through the cfg cache, not re-parse the blob each call")
        assert "self.cfg(proj)" in body, f"{method.__name__} should read config via self.cfg(proj)"
