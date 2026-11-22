#!/usr/bin/env python3
"""Comment-free C for the pinned classic cpp (gcc 2.7.2 KMC and its relatives), so a candidate preprocesses reliably.

The classic cpp is not a modern one: it has no `//` comments in C mode, so an apostrophe or quote inside one reads as
an unterminated character or string constant, and the app itself blanks `//` comments before it preprocesses a donor
unit (compiler.py, donor preprocessing). Agents who ran the bundled `cpp` by hand on a candidate full of comments (the
m2c-style `/* ... */` notes and `// TODO don't ...` lines the drafts carry) got it aborting, exit 134, and stripped the
comments by hand. This does that, once and the same way every time: every comment becomes blanks, strings and
character constants stay, and every newline stays, so line numbers in the compiler's messages and dumps still match.

    preprocess.py CAND.c [-o CAND.nocomment.c]       the stripped source (stdout without -o)

passes.py sends its candidate through `strip_comments` too. To dump cc1 passes by hand, strip first, then preprocess;
for a score or a diff, use the judge (fast_judge.py), which compiles the way the app does and needs none of this.
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
    a = ap.parse_args(argv)
    text = strip_comments(Path(a.source).read_text())
    if a.out:
        Path(a.out).write_text(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
