"""Variants of one function's C, made by decomp-permuter's randomizer (MIT, a pinned tool: toolchains.toml).

A program of its own, not a module of decompdecomp: app/permute.py runs it with the Python of decomp-permuter's
environment, `python -I permuter_gen.py <decomp-permuter's folder>`, and it imports nothing of decompdecomp. Our
own judge scores what it writes; this only makes candidates, so a variant that doesn't compile costs one compile.

In, on stdin, one job per line, each a JSON object:
    header    the shared header, as the compiler gets it
    source    the near miss's C
    function  its name
    family    base, ido, gcc or mwcc: the weights decomp-permuter tuned per compiler (default_weights.toml)
    seed      any number: the same job gives the same variants
    count     how many variants to write
Out, on stdout, one JSON object per line: {"variant": C} for each variant, distinct and none the source itself,
each the function alone plus whatever the randomizer added beside it (a helper it inlined code into); or
{"error": why} when the C can't be read; then {"done": true}, and the next job. It runs for as long as its stdin
is open, so a header is parsed once per C a turn starts from: a second for a header of 300 KB.

decomp-permuter reads C through pycparser, which knows neither comments nor the preprocessor: comments go, and so
do the header's preprocessor lines (as for m2c's context, seeds.py). Its macros stay undefined here, which is fine
while the code only names them (GRAVITY): the compiler still gets the header whole. A source with macro calls of
its own doesn't parse, and the permuter falls back to its simple rewrites (permuter.py).

The walk is decomp-permuter's own: a variant is the last one rewritten once more (KEEP of the time) or the source
rewritten afresh, so variants drift a few rewrites away from the source and come back.
"""

import json
import random
import re
import sys

KEEP = 0.6  # decomp-permuter's default chance of building on the last variant instead of starting again
SOURCE_STARTS = "DECOMPDECOMP_SOURCE_STARTS"  # a typedef put between the header and the source, to find the seam
TRIES_PER_VARIANT = 4  # rewrites can repeat or fail: give up on a job that yields nothing new this many times over
MAX_SEED = 10**20
# a comment, or a string or character literal (group 1), which keeps any /* inside it
_COMMENT = re.compile(r'//[^\n]*|/\*.*?\*/|("(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\')', re.S)


def plain_c(text: str, directives: bool = True) -> str:
    """`text` without comments and continued lines, and without preprocessor lines unless `directives`."""
    text = _COMMENT.sub(lambda m: m.group(1) or " ", text.replace("\\\n", ""))
    return text if directives else "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def main() -> int:
    sys.path.insert(0, sys.argv[1])  # decomp-permuter's folder: its `src` package and its own copy of pycparser
    from perm_pycparser import c_ast
    from src.ast_util import to_c
    from src.candidate import Candidate
    from src.helpers import get_default_randomization_weights
    from src.perm.perm import EvalState

    def variants(job: dict):
        name, count = job["function"], int(job["count"])
        unit = f"{plain_c(job['header'], directives=False)}\ntypedef int {SOURCE_STARTS};\n{plain_c(job['source'])}\n"
        rng = random.Random(job["seed"])
        try:
            weights = get_default_randomization_weights(job.get("family") or "base")
            first = Candidate.from_source(unit, EvalState(), name, weights, rng.randrange(1, MAX_SEED))
        except Exception as e:  # the C or the header isn't C pycparser reads (a macro call, a compiler extension)
            yield {"error": f"{type(e).__name__}: {e}"}
            return
        seam = next(node for node in first.ast.ext if isinstance(node, c_ast.Typedef) and node.name == SOURCE_STARTS)

        def own_part(ast) -> str:
            """The C after the seam: the function and its neighbours from the source, without the header. A header
            declaration the randomizer retyped is left out: the header isn't the permuter's to change, and a
            second, different declaration would only fail to compile."""
            return to_c(c_ast.FileAST(ast.ext[ast.ext.index(seam) + 1:]))

        seen = {own_part(first.ast)}  # the source as decomp-permuter prints it: not a variant
        cand = first
        for _ in range(count * TRIES_PER_VARIANT):
            if len(seen) > count:
                break
            try:
                if cand is None or rng.random() >= KEEP:
                    cand = Candidate.from_source(unit, EvalState(), name, weights, rng.randrange(1, MAX_SEED))
                cand.randomize_ast()
                variant = own_part(cand.ast)
            except Exception:  # a rewrite that trips over this code: start again from the source
                cand = None
                continue
            if variant not in seen:
                seen.add(variant)
                yield {"variant": variant}

    for line in sys.stdin:
        if line.strip():
            for answer in variants(json.loads(line)):
                print(json.dumps(answer))
            print(json.dumps({"done": True}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
