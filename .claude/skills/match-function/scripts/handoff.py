#!/usr/bin/env python3
"""Hand a stalled near-match off in one command: the note the next agent needs, written with add_note, then the
claim released with its token and the session's renewal told to let it go.

Agents handing off wrote notes without the rows or the register pairs, and add_note / release_function calls failed
for missing arguments (the function, the claim token), leaving the claim held and the note unwritten. This builds the
note from the session itself and makes both calls with every argument:

  - the best score and the remaining differing rows of the source (the judge's own rows);
  - the register pairs explain_regs.py reads for it (the swapped registers and their `cause:` lines);
  - every approach tried, with its score (`--tried`, one per approach: required);
  - the source's path and the agent's own findings (`--findings`).

    handoff.py HOLDER --tried "dropped the cast on the index: 40" [--tried ...] [--source BEST.c]
               [--findings TEXT] [--dry-run] [--no-release]

`--source` defaults to the session's saved best (`/tmp/dd-HOLDER/start/best.c`). `--dry-run` prints the note and calls
nothing. Without `--no-release` it then calls `release_function(function, notes, claim_token)` and `setup.py release
HOLDER`; run `setup.py stop HOLDER` after. The app accepts the release once `clock.py` allows a hand-off (SKILL.md 5c).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import setup  # noqa: E402  (the session's connection, Gateway and release)

MAX_ROWS = 40
PAIR = "where the target has"


def note_text(function, source_path, score, rows, explanation, tried, findings=""):
    """The note: score, rows, register pairs, tried approaches with scores, findings and the source's path."""
    differing = [row for row in rows if row and row[0] != " "]
    out = [f"Hand-off of {function}: best {score} ({len(differing)} differing rows) in {source_path}.", "",
           "Remaining rows (marker | target | candidate):"]
    out += [f"  {m} | {t} | {c}" for m, t, c in differing[:MAX_ROWS]]
    if len(differing) > MAX_ROWS:
        out.append(f"  ... {len(differing) - MAX_ROWS} more (fast_judge.py --diff --rows all)")
    pairs = [line for line in explanation.splitlines() if PAIR in line or line.strip().startswith(("cause:", "why not"))]
    out += ["", "Register pairs (explain_regs.py):"] + ([f"  {line.strip()}" for line in pairs[:24]] or ["  none"])
    out += ["", "Tried (approach: score):"] + [f"  - {item}" for item in tried]
    if findings:
        out += ["", "Findings:", findings.strip()]
    return "\n".join(out)


def claim_token(gateway, function):
    return next((c.get("claim_token") or "" for c in setup.claims(gateway) if c.get("function") == function), None)


def handoff(holder, tried, source="", findings="", dry_run=False, release=True, gateway=None, judge=None,
            write=print):
    root = setup.session_dir(setup.holder_name(holder))
    info = setup.read_session(root)
    function = info.get("function") or ""
    if not function:
        sys.exit(f"{holder} holds no function (already released)")
    if not tried:
        sys.exit("--tried is required: every approach you tried, with its score (`idea: score`), one per --tried")
    path = Path(source) if source else root / "start" / "best.c"
    if not path.is_file():
        sys.exit(f"no source at {path}: pass --source BEST.c")
    text = path.read_text()
    if judge is None:
        import fast_judge
        judge = fast_judge.Judge(info["slug"], function, home=info.get("home"))
    try:
        import explain_regs
        score, explanation, _ = explain_regs.explain(judge, text)
        _, rows = judge.rows(text)
    finally:
        judge.close()
    note = note_text(function, path, score, rows, explanation, tried, findings)
    write(note)
    if dry_run:
        return 0
    if gateway is None:
        gateway = setup.Gateway(info["gateway_url"], info["token"])
        gateway.start()
    try:
        write(gateway.call("add_note", {"function": function, "text": note}))
        if not release:
            return 0
        token = claim_token(gateway, function)
        if token is None:
            write(f"{function} is not claimed by this session any more: the note is written, nothing to release")
            return 1
        write(gateway.call("release_function", {"function": function, "claim_token": token,
                                                "notes": f"handed off at {score}: see the note ({len(tried)} approaches tried)"}))
    except setup.McpError as error:
        write(f"hand-off stopped: {error}")
        return 1
    return setup.release_session(setup.holder_name(holder))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("holder")
    ap.add_argument("--tried", action="append", default=[], help="one approach and its score; repeat for each")
    ap.add_argument("--source", default="")
    ap.add_argument("--findings", default="", help="compiler findings: dumps read, pass orders, scaffolding in the best")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-release", action="store_true", help="write the note only")
    a = ap.parse_args(argv)
    return handoff(a.holder, a.tried, a.source, a.findings, a.dry_run, not a.no_release)


if __name__ == "__main__":
    sys.exit(main())
