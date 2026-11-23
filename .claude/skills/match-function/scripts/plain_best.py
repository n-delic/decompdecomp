#!/usr/bin/env python3
"""The saved best and the same C with its scaffolding stripped, side by side with their isolated scores.

A saved best that a search built is full of scaffolding (do { } while (0) fences, `if (x) { S } else { S }` guards, union
views of one register, identity inline functions, phantom locals); prove_chain cannot step from it, and agents spent
half an hour finding that out. setup.py runs this right after a claim, so the session starts with both: decomprepair
`scaffold_strip.plain` (every fence without its own break opened, then `strip`), judged by the isolated judge.

    plain_best.py --slug SLUG --function FUNC [--out-dir DIR] [SOURCE.c]

It writes DIR/best.c and DIR/plain.c (when stripping changed anything) and prints one line each with the score. The
plain form usually scores worse (the fences are often what the score rests on: worse in 23 of 30 pre-match bests of an N64 game,
closer to the final match in 11): it is a second place to start a chain from, not a replacement. Read-only, no claim
needed. Run it with a session's python (it imports decompnet and decomprepair).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fast_judge  # noqa: E402


def plain_form(source, name, header=""):
    """The stripped source, or None when the library's version has no `plain` or nothing changed."""
    try:
        from decomprepair import scaffold_strip
    except ImportError:
        return None
    plain = getattr(scaffold_strip, "plain", None)
    try:
        return plain(source, name, header) if plain else None
    except Exception as error:  # a rewrite's bug must not end a claim's summary
        print(f"note: scaffold_strip.plain failed ({error})", file=sys.stderr)
        return None


def report(source, name, score, header="", out_dir=None, write=print):
    """Score the best and its plain form; write them to `out_dir`; return {'best': score, 'plain': score or None}."""
    found = {"best": score(source)}
    plain = plain_form(source, name, header)
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / "best.c").write_text(source)
    write(f"saved best   score {found['best']}" + (f"   -> {Path(out_dir) / 'best.c'}" if out_dir else ""))
    if plain is None:
        write("plain form   (no scaffolding to strip)")
        return found
    found["plain"] = score(plain)
    if out_dir:
        (Path(out_dir) / "plain.c").write_text(plain)
    shown = "did not compile" if found["plain"] is None else f"score {found['plain']}"
    write(f"plain form   {shown}" + (f"   -> {Path(out_dir) / 'plain.c'}" if out_dir else "")
          + "   (fences opened, scaffolding stripped: prove_chain can step from here when it can't from the best)")
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="C to start from (default: the app's saved best)")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--function", required=True)
    ap.add_argument("--out-dir")
    ap.add_argument("--home")
    a = ap.parse_args(argv)
    if a.source:
        source = Path(a.source).read_text()
    else:
        import judge
        source = judge.App(a.home).function(a.slug, a.function).get("source") or ""
    if not source.strip():
        print("saved best   (none yet: nothing to strip)")
        return 0
    j = fast_judge.Judge(a.slug, a.function, home=a.home)
    try:
        report(source, a.function, j.score, j.header, a.out_dir)
    finally:
        j.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
