#!/usr/bin/env python3
"""Preprocess a candidate the way the app does, for running cc1's dumps by hand.

Do not run the bundled `cpp` or `cccp` yourself. GCC 2.7.2's (KMC) own list of predefined macros is malformed (the MIPS
config runs `-Amachine(mips)-Dunix` together), so the bare program aborts, exit 134, on ANY file, even
`int f(void){return 1;}`; only the `gcc` driver works, because it passes `-undef` and its own -D/-A list. Hand-expanding
macros to get past it is never needed. This script has the app's judge worker do what the compile does (the function's
own profile, flags, include folders and the project's header as context.h) and gives you the result:

    preprocess.py CAND.c --slug SLUG --function FUNC [-o CAND.i]    # the preprocessed C, plus the cc1 command to run

It prints (to stderr, so `-o` or a redirect keeps the C clean) the cc1 command line with the compile's own flags, e.g.
`.../cc1 CAND.i -mfp64 -mgp64 -G0 -quiet -dumpbase CAND.c -mips3 -mgp32 -mfp32 -O2 -version -o CAND.s`: add `-dl -dg`
(allocation), `-dS -dR` (schedulers) and so on to it, and run it on the `.i`. For a prepared dump with the priorities and
orders already read out, `passes.py` does the whole thing. Diagnostic only: nothing is judged or recorded. Needs the
same `decomp` and DECOMP_HOME as fast_judge.py. GCC-style drivers only (not IDO, Metrowerks or Visual C++).

The older mode, for a compiler whose cpp can't read comments (the classic cpp has no `//` comments in C mode, so an
apostrophe or quote in one reads as an unterminated constant, and the app blanks `//` comments before it preprocesses
a donor unit, compiler.py): `--strip-only` (or no --slug) writes the source with every comment blanked, strings and
character constants kept, and every newline kept so line numbers in the compiler's messages and dumps still match.

    preprocess.py CAND.c --strip-only [-o CAND.nocomment.c]

passes.py sends its candidate through `strip_comments` too. For a score or a diff, use the judge (fast_judge.py).
"""
import argparse
import re
import sys
from pathlib import Path

# A string or character constant is matched first so a comment marker inside one is left alone; a `//` comment runs to
# its newline, through a backslash-continued line too (also the `??/` trigraph backslash).
TOKENS = re.compile(r'//(?:\\\r?\n|\?\?/\r?\n|[^\n])*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.S)


def strip_comments(source):
    """`source` with every comment blanked to spaces, keeping newlines, strings and character constants."""
    return TOKENS.sub(lambda m: re.sub(r'[^\r\n]', ' ', m[0]) if m[0][:2] in ('//', '/*') else m[0], source)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("-o", "--out")
    ap.add_argument("--slug")
    ap.add_argument("--function")
    ap.add_argument("--strip-only", action="store_true", help="only blank the comments, no compiler involved")
    ap.add_argument("--home")
    a = ap.parse_args(argv)
    text = strip_comments(Path(a.source).read_text())
    if not a.strip_only and (a.slug or a.function):
        if not (a.slug and a.function):
            raise SystemExit("--slug and --function go together")
        text, cc1 = preprocess_like_app(a.slug, a.function, text, a.home)
        stem = Path(a.out).with_suffix("") if a.out else Path(a.source).with_suffix("")
        print(cc1_line(cc1, a.out or f"{stem}.i", f"{stem}.s"), file=sys.stderr)
    if a.out:
        Path(a.out).write_text(text)
    else:
        sys.stdout.write(text)


def preprocess_like_app(slug, function, source, home=None):
    """(preprocessed C, cc1 words) from the judge worker, which runs compiler.preprocess_candidate."""
    import fast_judge
    judge = fast_judge.Judge(slug, function, home=home)
    try:
        reply = judge.ask({"op": "preprocess", "slug": slug, "name": function, "source": source})
    finally:
        judge.close()
    return reply["text"], reply.get("cc1")


def cc1_line(words, infile, outfile):
    """The cc1 command, with the file names filled in and the paths quoted (the toolchains folder can hold spaces)."""
    if not words:
        return "(no cc1 command found for this compiler: its driver didn't report one; use passes.py)"
    import shlex
    fill = {"IN.i": infile, "OUT.s": outfile}
    return "cc1 command (add -dl -dg for allocation, -dS -dR for scheduling): " + shlex.join(fill.get(w, w) for w in words)


if __name__ == "__main__":
    main()
