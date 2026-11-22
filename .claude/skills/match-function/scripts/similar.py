#!/usr/bin/env python3
"""Matched functions that look like this one, with the saved C of the best: the app's own `find_similar` over the session's connection.

A look-alike that already matches is the best starting point (copy-pasted code, the same inlined helper, template
instances): its source shows the spelling the compiler needed. This asks the app (the instruction-pattern similarity the
`find_similar` tool uses, which for a ROM project also lists other games' twins) and prints each look-alike with its
similarity and status, then the saved source of the top matched ones.

    similar.py SLUG FUNC [--limit 10] [--sources 3] [--all] [--out DIR] [--max-chars 6000] [--holder H]

Read-only: it changes nothing and needs no claim, but it does use the MCP connection a `setup.py SLUG` session made (the
only one for SLUG, or `--holder`). Matched look-alikes come first; `--all` also lists unmatched ones (best score so far).
`--sources N` prints the saved source (`get_function section=matched`) of the best N matched ones; `--out DIR` writes
each to DIR/NAME.c and prints the path instead. A look-alike is a lead, not a proof: judge any C you adapt.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import setup  # noqa: E402  (the session's connection: setup.session_gateway, setup.McpError)

HIT = re.compile(r"^- (?P<name>\S+)\s+(?P<percent>\d+)% similar\s+(?P<state>matched|best -?\d+|not started)\s*(?P<rest>.*)$")
MAX_LIMIT = 15  # what find_similar returns at most
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.$:~<>-]+$")


def parse(text):
    """[{name, percent, matched, score, rest}] from find_similar's lines, in the app's order (matched first, then by
    similarity); the "other games" suggestions and the source block are not look-alikes in this project and are left out."""
    hits = []
    for line in text.splitlines():
        if line.startswith("Other games"):
            break
        found = HIT.match(line)
        if found:
            state = found["state"]
            hits.append({"name": found["name"], "percent": int(found["percent"]), "matched": state == "matched",
                         "score": int(state.split()[1]) if state.startswith("best") else None,
                         "rest": found["rest"].strip()})
    return hits


def describe(hit):
    state = "matched" if hit["matched"] else f"best {hit['score']}" if hit["score"] is not None else "not started"
    return f"{hit['percent']:>3}%  {state:<12} {hit['name']}  {hit['rest']}".rstrip()


def source_of(gateway, name, max_chars):
    """The saved matched C of `name` (the app's `get_function section=matched`), or the app's refusal as text."""
    try:
        return gateway.call("get_function", {"function": name, "section": "matched", "limit": max_chars})
    except setup.McpError as error:
        return f"(no saved source: {error})"


def run(gateway, slug, function, limit, sources, show_all, out, max_chars, write=print):
    text = gateway.call("find_similar", {"function": function, "limit": max(1, min(int(limit), MAX_LIMIT))})
    hits = parse(text)
    if not hits:
        write(text.strip() or f"No look-alikes found for {function}.")
        return 1
    shown = hits if show_all else [h for h in hits if h["matched"]]
    if not shown:
        write(f"{len(hits)} look-alike(s) for {function} but none matched yet (--all lists them):")
        shown = hits
    write(f"Look-alikes of {function} in {slug}:")
    for hit in shown:
        write("  " + describe(hit))
    for hit in [h for h in shown if h["matched"]][:max(0, sources)]:
        body = source_of(gateway, hit["name"], max_chars)
        if out:
            if not SAFE_NAME.match(hit["name"]):
                write(f"\n{hit['name']}: not written (the name isn't a plain file name)")
                continue
            folder = Path(out)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{hit['name']}.c").write_text(body)
            write(f"\n{hit['name']} -> {folder / (hit['name'] + '.c')}")
        else:
            write(f"\n--- {hit['name']} ({hit['percent']}% similar, matched) ---\n{body.strip()}")
    return 0


def main(argv=None, gateway=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("function")
    ap.add_argument("--limit", type=int, default=10, help=f"look-alikes to ask for (up to {MAX_LIMIT})")
    ap.add_argument("--sources", type=int, default=3, help="print the saved source of the best N matched ones (default 3)")
    ap.add_argument("--all", action="store_true", help="also list look-alikes that are not matched")
    ap.add_argument("--out", help="write each source to DIR/NAME.c instead of printing it")
    ap.add_argument("--max-chars", type=int, default=6000)
    ap.add_argument("--holder", default="")
    a = ap.parse_args(argv)
    if gateway is None:
        gateway, _ = setup.session_gateway(a.slug, a.holder)
    try:
        return run(gateway, a.slug, a.function, a.limit, a.sources, a.all, a.out, a.max_chars)
    except setup.McpError as error:
        sys.exit(f"find_similar: {error}")


if __name__ == "__main__":
    sys.exit(main())
