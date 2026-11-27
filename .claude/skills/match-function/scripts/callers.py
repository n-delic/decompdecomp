#!/usr/bin/env python3
"""Where a function is called from, and what its callers say about its result: the app's own `get_callers` over the session's connection.

A function's own code can leave its return type (and what its arguments are) ambiguous: KMC GCC copies a flag into $v0
in void functions too. The callers settle it: a caller that reads $v0 or $f0 after the call proves the function returns a
value (a function returning nothing leaves it unread), and a matched caller's C (`x = g();`) is exact. This prints every
call site with the caller's name and the instructions around the call, and the verdict (value / void / unknown, with
the evidence and how strong it is).

    callers.py SLUG FUNC [--limit 8] [--holder H]

Read-only: it changes nothing and needs no claim, but it does use the MCP connection a `setup.py SLUG` session made (the
only one for SLUG; with several sessions open pass `--holder NAME`, your own session's).
ROM projects only (a project's callees are not stored yet). The verdict is a lead:
write the result type it names, judge, and trust the judge.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import setup  # noqa: E402  (the session's connection: setup.session_gateway, setup.McpError)

VERDICT = re.compile(r"^Result: (?P<verdict>value|void|unknown)(?: \((?P<type>[^,)]+), (?P<strength>strong|weak)\))?")
MAX_LIMIT = 20  # what get_callers returns at most


def parse(text):
    """{verdict, type, strength, callers: [name]} from get_callers' text (None fields when it has no verdict line)."""
    found = {"verdict": None, "type": None, "strength": None}
    callers = []
    for line in text.splitlines():
        hit = VERDICT.match(line)
        if hit:
            found = {"verdict": hit["verdict"], "type": hit["type"], "strength": hit["strength"]}
        elif line.startswith("## "):
            callers.append(line[3:].split(" (")[0])
    return {**found, "callers": callers}


def run(gateway, function, limit, write=print):
    text = gateway.call("get_callers", {"function": function, "limit": max(1, min(int(limit), MAX_LIMIT))})
    write(text.strip())
    return 0 if parse(text)["verdict"] else 1


def main(argv=None, gateway=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("function")
    ap.add_argument("--limit", type=int, default=8, help=f"callers to list (up to {MAX_LIMIT})")
    ap.add_argument("--holder", default="",
                    help="your setup.py session's holder name; needed when several sessions of SLUG are open")
    a = ap.parse_args(argv)
    if gateway is None:
        gateway, _ = setup.session_gateway(a.slug, a.holder)
    try:
        return run(gateway, a.function, a.limit)
    except setup.McpError as error:
        sys.exit(f"get_callers: {error}")


if __name__ == "__main__":
    sys.exit(main())
