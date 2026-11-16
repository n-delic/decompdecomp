"""Where a slow tool call's time went, kept so it can be read later.

Every tool call the app runs measures its phases (tools.build_mcp run: the wait for a thread in its lane, the
dispatch, SQL reads and the writer's lock, the build, the candidate lock, the saved-progress steps) and only showed
them in the live transcript. Nothing kept them, so "why are helpers waiting on compiles" had no answer: the
compiler, the interpreter lock, a database lock or a lane full of other calls look the same from outside. Calls
that take SLOW_MS or more are now appended, with every phase, to `.decomp/logs/slow-tool-calls.log` (one JSON
object a line, rotated at MAX_BYTES), and every call counts towards per-tool totals in memory.

    python -m decompnet.app.tool_latency ~/Decomp-RetroReversing/.decomp/logs/slow-tool-calls.log

prints, per tool, how many slow calls there were, their median and 90th percentile, and the mean of each phase.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

SLOW_MS = 1000.0
MAX_BYTES = 20 * 1024 * 1024
NAME = "slow-tool-calls.log"

_lock = threading.Lock()
_totals: dict[str, dict] = defaultdict(lambda: {"calls": 0, "slow": 0, "ms": 0.0, "max_ms": 0.0})


def record(net, agent_name: str, slug: str, tool: str, timings: dict, error: bool = False) -> None:
    """Count the call; append it with its phases when it took SLOW_MS or more. Never raises: it's a record."""
    try:
        total = float(timings.get("queue_ms", 0)) + float(timings.get("dispatch_ms", 0))
        with _lock:
            kept = _totals[tool]
            kept["calls"] += 1
            kept["ms"] += total
            kept["max_ms"] = max(kept["max_ms"], total)
            if total >= SLOW_MS:
                kept["slow"] += 1
        if total < SLOW_MS:
            return
        path = Path(net.store.path).parent / "logs" / NAME
        line = json.dumps({"at": round(time.time(), 3), "tool": tool, "agent": agent_name, "project": slug,
                           "total_ms": round(total, 2), "error": error,
                           "timings": {k: v for k, v in timings.items() if v}}) + "\n"
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_file() and path.stat().st_size > MAX_BYTES:
                os.replace(path, path.with_name(NAME + ".1"))
            with open(path, "a", encoding="utf-8") as out:
                out.write(line)
    except Exception:  # (a full disk or a read-only home must not fail the tool call)
        pass


def totals() -> dict:
    """Per tool since the app started: calls, slow calls, mean and longest milliseconds."""
    with _lock:
        return {tool: {"calls": t["calls"], "slow": t["slow"], "mean_ms": round(t["ms"] / t["calls"], 1),
                       "max_ms": round(t["max_ms"], 1)} for tool, t in _totals.items() if t["calls"]}


def summary(lines) -> str:
    """The log's slow calls per tool: count, median and p90 total, and the mean of each phase (largest first)."""
    calls: dict[str, list[dict]] = defaultdict(list)
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        calls[item.get("tool", "?")].append(item)
    out = []
    for tool, items in sorted(calls.items(), key=lambda kv: -len(kv[1])):
        totals_ms = sorted(i["total_ms"] for i in items)
        phases: dict[str, float] = defaultdict(float)
        for item in items:
            for phase, value in item.get("timings", {}).items():
                if isinstance(value, (int, float)) and phase.endswith("_ms"):
                    phases[phase] += value
        top = sorted(phases.items(), key=lambda kv: -kv[1])[:6]
        out.append(f"{tool}: {len(items)} slow calls, median {totals_ms[len(totals_ms) // 2] / 1000:.1f} s, "
                   f"p90 {totals_ms[int(len(totals_ms) * 0.9)] / 1000:.1f} s; mean "
                   + ", ".join(f"{phase.removesuffix('_ms')} {value / len(items) / 1000:.1f} s" for phase, value in top))
    return "\n".join(out) or "No slow calls recorded."


if __name__ == "__main__":
    paths = [Path(p) for p in sys.argv[1:]] or [Path.home() / "Decomp-RetroReversing" / ".decomp" / "logs" / NAME]
    print(summary(line for path in paths if path.is_file() for line in path.read_text(encoding="utf-8").splitlines()))
