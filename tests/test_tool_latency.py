"""Slow tool calls are kept with where their time went (app/tool_latency.py), and the gateway reports what helpers
waited per tool."""
import json
from types import SimpleNamespace

from decompnet.app import tool_latency


def net_at(tmp_path):
    (tmp_path / ".decomp").mkdir()
    return SimpleNamespace(store=SimpleNamespace(path=tmp_path / ".decomp" / "decomp.db"))


def test_keeps_slow_calls_with_their_phases_and_counts_every_call(tmp_path):
    net = net_at(tmp_path)
    log = tmp_path / ".decomp" / "logs" / tool_latency.NAME
    tool_latency.record(net, "helper", "game", "latency_quick", {"queue_ms": 1, "dispatch_ms": 20})
    assert not log.exists()
    tool_latency.record(net, "helper", "game", "latency_slow", {"queue_ms": 4000, "dispatch_ms": 2500,
                                                                "write_lock_wait_ms": 2000, "build_ms": 0})
    kept = json.loads(log.read_text())
    assert (kept["tool"], kept["total_ms"], kept["project"]) == ("latency_slow", 6500, "game")
    assert kept["timings"] == {"queue_ms": 4000, "dispatch_ms": 2500, "write_lock_wait_ms": 2000}
    totals = tool_latency.totals()
    assert totals["latency_quick"]["calls"] == 1 and totals["latency_quick"]["slow"] == 0
    assert totals["latency_slow"]["slow"] == 1 and totals["latency_slow"]["max_ms"] == 6500
    text = tool_latency.summary(log.read_text().splitlines())
    assert text.startswith("latency_slow: 1 slow calls, median 6.5 s") and "queue 4.0 s" in text


def test_a_record_never_fails_the_call(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("")
    net = SimpleNamespace(store=SimpleNamespace(path=blocked / "decomp.db"))  # (its folder is a file)
    tool_latency.record(net, "helper", "game", "latency_broken", {"dispatch_ms": 5000})
