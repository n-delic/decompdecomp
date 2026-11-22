#!/usr/bin/env python3
"""How long this session has worked on its function, and how long since its score last improved: the hand-off clock.

The skill's hand-off rule ("only after 45 minutes with no improvement") is read off the app's own attempt history, not
judged by the agent: every candidate the app judged for this function is a row (score, time, agent). An improvement is a
judged score of this agent's below everything the function had before it (the saved best when the session began, and
every earlier attempt by anyone). Scores tried only in the isolated judge don't count: submit each new best to the app
(`compile_and_diff`), which also keeps it safe.

    clock.py HOLDER                         the session setup.py made (/tmp/dd-HOLDER/connection.json)
    clock.py --slug S --function F --agent NAME --since UNIX_TIME [--home DIR]

Prints the minutes worked, the minutes since the last improvement, the best, and the verdict: "keep going" or
"hand-off allowed". Exit status 0 either way.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import judge  # noqa: E402

HANDOFF_MINUTES = 45


def clock(scores, agent, started, now=None, minutes=HANDOFF_MINUTES):
    """The clock from the function's attempt rows ({score, t, agent}). `started`: when this session began on it."""
    now = time.time() if now is None else now
    rows = sorted((r for r in scores if r.get("score") is not None and r.get("t") is not None), key=lambda r: r["t"])
    before = [r["score"] for r in rows if r["t"] < started]
    best = min(before) if before else None
    start_best, last_improved, own = best, started, 0
    for row in rows:
        if row["t"] < started:
            continue
        mine = row.get("agent") == agent
        own += mine
        if best is None or row["score"] < best:
            best = row["score"]
            if mine:
                last_improved = row["t"]
    worked = (now - started) / 60
    stalled = (now - last_improved) / 60
    allowed = worked >= minutes and stalled >= minutes
    return {"worked_min": round(worked, 1), "since_improvement_min": round(stalled, 1), "best": best,
            "start_best": start_best, "own_attempts": own,
            "verdict": "hand-off allowed" if allowed else "keep going",
            "wait_min": 0.0 if allowed else round(max(minutes - worked, minutes - stalled), 1)}


def session(holder):
    info = json.loads((Path(f"/tmp/dd-{holder}") / "connection.json").read_text())
    return info["home"], info["slug"], info["function"], info.get("connection_name") or holder, info.get("started")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("holder", nargs="?")
    parser.add_argument("--slug")
    parser.add_argument("--function")
    parser.add_argument("--agent")
    parser.add_argument("--since", type=float)
    parser.add_argument("--home")
    args = parser.parse_args(argv)
    home, slug, function, agent, since = args.home, args.slug, args.function, args.agent, args.since
    if args.holder:
        home, slug, function, agent, started = session(args.holder)
        since = args.since or started
    if not (slug and function and agent and since):
        sys.exit("give a session HOLDER, or --slug, --function, --agent and --since")
    detail = judge.App(home).function(slug, function)
    result = clock(detail.get("scores") or [], agent, since)
    if detail.get("scores_sampled"):
        result["note"] = "the app sampled this function's history; the clock may miss rows"
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
