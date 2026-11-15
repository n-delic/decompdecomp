"""The throughput benchmark (decompnet/bench.py): it runs the machine-only pipeline on a demo game and measures the
outcome, and --compare reads two runs side by side.

The run itself needs a demo's compiler and m2c; it skips without them (CI has none). The comparison and the
skipping need nothing.
"""

import json

import pytest

from decompnet import bench, consoles, seeds


def _report(revision: str, matched: int, cpu: float, **timing) -> dict:
    demo = {"console": "ps", "demo": "demo-ps1", "compiler": "gcc-2.7.2-psx", "wall_seconds": 10.0,
            "cpu_seconds": {"self": 1.0, "children": cpu - 1.0, "total": cpu}, "before": {"matched": 0},
            "after": {"matched": matched, "functions": 101}, "new_matches": matched,
            "matches_per_cpu_minute": matched / (cpu / 60), "matches_per_wall_minute": matched * 6.0,
            "cache_hit_rate": 0.0, "compile_timing": timing, "phases": {}}
    return {"format": bench.FORMAT, "revision": revision, "machine": {"cpus": 8}, "settings": {"seconds": 10},
            "demos": {"ps": demo}, "skipped": {"gc": "needs mwcc"}}


def test_compare_shows_each_demos_change_and_what_was_skipped():
    text = bench.compare(_report("aaa", 20, 40.0, execute=30.0), _report("bbb", 22, 20.0, execute=12.0))
    assert "aaa" in text and "bbb" in text
    per_cpu = next(line for line in text.splitlines() if "matches per CPU-minute" in line)
    assert "30" in per_cpu and "66" in per_cpu and "+120.0%" in per_cpu
    assert any("timing: execute" in line and "-18" in line for line in text.splitlines())
    assert "gc: skipped in the before run, needs mwcc" in text and "VM, WSL" not in text


def test_compare_warns_when_a_compilers_cpu_ran_where_it_isnt_counted():
    """The PS2's EE-GCC runs in the Linux VM on a Mac: its CPU isn't this process's children's."""
    after = _report("bbb", 22, 20.0)
    after["demos"]["ps"]["cpu_seconds"]["complete"] = False
    assert "compare the wall-minute figures" in bench.compare(_report("aaa", 20, 40.0), after)


def test_compare_refuses_files_that_arent_benchmark_runs():
    with pytest.raises(SystemExit, match="format"):
        bench.compare({"demos": {}}, _report("bbb", 1, 1.0))


def test_a_demo_without_its_tools_is_skipped_with_what_it_needs(monkeypatch):
    monkeypatch.setattr(bench, "missing_tools", lambda console: ["example-gcc"])
    report = bench.run(["ps"], seconds=1, log=lambda *_: None)
    assert report["demos"] == {} and report["skipped"] == {"ps": "needs example-gcc"}


def test_an_unknown_demo_says_which_there_are():
    with pytest.raises(SystemExit, match="ps2"):
        bench.demos(["dreamcast"])


PS1 = consoles.get("ps")


@pytest.mark.skipif(bool(bench.missing_tools(PS1)) or not seeds.available(),
                    reason="needs the PS1 demo's tools (gcc-2.7.2-psx, maspsx, binutils-mips) and m2c")
def test_a_short_run_on_the_ps1_demo_stops_at_its_budget_and_measures_it(tmp_path):
    """The smallest demo whose drafts match (PS1: about 100 functions), with a budget too small to finish it: the
    run stops at the budget and still reports what it tried and what the compiles cost. (Whether the first few
    seconds already match a function depends on the machine's speed: the count is checked, not its size.)"""
    report = bench.run(["ps"], seconds=5, home=tmp_path, log=lambda *_: None)
    result = report["demos"]["ps"]
    assert result["before"]["matched"] == 0 and result["after"]["functions"] == result["before"]["functions"] > 50
    assert result["new_matches"] == result["after"]["matched"] - result["before"]["matched"] >= 0
    assert result["cpu_seconds"]["total"] > 0 and result["matches_per_cpu_minute"] >= 0
    assert result["phases"]["autopilot"]["tried"] > 0
    assert result["compile_timing"]["requests"] > 0 and 0 <= result["cache_hit_rate"] <= 1
    assert result["wall_seconds"] < 5 + bench.STOP_GRACE_SECONDS
    json.dumps(report)  # what --out writes
