#!/usr/bin/env python3
"""The app's own judge, standalone, for fast and parallel candidate scoring: `decomp judge-worker`.

It returns exactly the score the app gives (a candidate judged 1090 in the app judged 1090 here), in about 0.1 s,
needs no claim, and opens the project attach-only so it runs beside the app without changing its state.

    fast_judge.py FILE.c [FILE.c ...] --slug twine --function func_8002D240 [--diff [--rows N|all]] [--header]
                  [--toolchain ID] [--flags='-mfp32' | --replace-flags='-O2 ...']

The profile is the function's own unless told otherwise: `--toolchain` names another compiler, `--flags` ADDS to the
function's flags, `--replace-flags` judges with exactly those flags (use it with another compiler or optimisation level,
or once a match has changed the function's own profile). stderr says which compiler and flags were judged with.

Only the project and the function are needed: the worker's `function` op supplies the target bytes, the function's
own compiler profile and the project's header (typedefs and externs, which the judge prepends to every candidate:
a source needs none of its own). `--diff` prints the rows that differ, the app's own normalisation (the same text
`compile_and_diff` shows), after the score: the first 60, then a "... (N more rows not shown ...)" line; `--diff --rows all` (or N) changes that. `--header` prints the header, for compiling a candidate outside the judge. With several files, three or more that compile
to identical instructions get a hint (invariant.py): their difference is not what decides the rows.

    # as a library in a search loop (one worker process per search process; run several in parallel):
    from fast_judge import Judge
    j = Judge(slug, name); j.score(source); j.judge(source, diff=True)["diff"]; j.header

Needs a `decomp` command: $DECOMP_BIN, else $UV_PROJECT_ENVIRONMENT/bin/decomp (a setup.py session exports it), else
the venv running this script, else the checkout's .venv. DECOMP_HOME names the app's data home (default ~/Decomp-RetroReversing-dev). Reads the project
only; never copies a ROM. Score 0 is a match; None means it did not compile (test `is None`, never `score or BIG`).
The old explicit form still works: Judge(slug, name, vram, target_hex, toolchain, flags).
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claimlost  # noqa: E402

DEFAULT_HOME = "~/Decomp-RetroReversing-dev"
DIFF_ROWS = 60  # rows --diff prints unless told otherwise; the worker counts what it leaves out
ALL_ROWS = 10**9


def rows_wanted(rows):
    """`--rows N|all` as the row count the worker renders (`all`: no cap)."""
    if rows == "all":
        return ALL_ROWS
    if not str(rows).isdigit() or int(rows) < 1:
        raise SystemExit("--rows takes a positive number or `all`")
    return int(rows)


def find_decomp(explicit=None):
    here = os.path.abspath(os.path.join(os.path.dirname(__file__), *[".."] * 4))
    private = os.environ.get("UV_PROJECT_ENVIRONMENT")
    candidates = [explicit, os.environ.get("DECOMP_BIN"), private and os.path.join(private, "bin", "decomp"),
                  # a copy of the scripts outside a checkout, run with the session's python: that venv's decomp
                  sys.prefix != sys.base_prefix and os.path.join(sys.prefix, "bin", "decomp"),
                  os.path.join(here, ".venv", "bin", "decomp")]
    return next((c for c in candidates if c and os.path.exists(c)), candidates[-1])


class Judge:
    def __init__(self, slug, name, vram=None, target_hex=None, toolchain=None, flags=None, decomp=None, home=None,
                 extra_flags=None):
        self.slug, self.name = slug, name
        self.cmd = [find_decomp(decomp), "judge-worker"]
        self.env = dict(os.environ, DECOMP_HOME=home or os.environ.get("DECOMP_HOME") or os.path.expanduser(DEFAULT_HOME),
                        DECOMP_WINDOW="0", DECOMP_TRAY="0")
        self.proc = None
        self.header, self.arch, self.asm = "", "mips", ""
        self.big_endian = True
        self.tables, self.callees, self.entry_scope = [], "", None
        self.words = {}
        self.jump_tables = []  # set by rows(): the linked jump-table differences of the last candidate (switch_tables.differences)
        if vram is None or target_hex is None or toolchain is None:
            info = self.ask({"op": "function", "slug": slug, "name": name, **({"vram": vram} if vram is not None else {})})
            vram, target_hex = info["vram"], info["target"]
            toolchain = toolchain or info["compiler"]["toolchain"]
            flags = flags if flags is not None else info["compiler"]["flags"]
            self.header, self.arch, self.asm = info.get("header") or "", info.get("arch") or "mips", info.get("asm") or ""
            self.big_endian = bool(info.get("big_endian", True))
            self.tables = [(low, list(targets)) for low, targets in info.get("tables") or []]
            self.callees, self.entry_scope = info.get("callees") or "", info.get("entry_scope") or None
            self.words = {str(k): int(v) for k, v in (info.get("words") or {}).items()}  # float constants' words
        # the flags added to the function's own (--flags, else $DD_EXTRA_FLAGS): the diagnostics (passes.py,
        # explain_regs.py, dumps.py, alloc_roles.py) send them too, so a probe's dumps are of the compile it scored
        self.extra_flags = list(extra_flags if extra_flags is not None else os.environ.get("DD_EXTRA_FLAGS", "").split())
        self.compiler = {"toolchain": toolchain, "flags": list(flags if flags is not None else
                                                              ("-G0", "-mips3", "-mgp32", "-mfp32", "-O2"))
                                                          + self.extra_flags}
        self.request = {"op": "judge", "slug": slug, "function": {"name": name, "vram": vram, "target": target_hex},
                        "compiler": self.compiler}

    def ask(self, request):
        if self.proc is None or self.proc.poll() is not None:
            self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, text=True, env=self.env)
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()
        reply = json.loads(self.proc.stdout.readline())
        if "error" in reply:
            raise SystemExit(reply["error"])
        return reply

    def judge(self, source, diff=False, rows=None):
        """The worker's whole reply: score, detail and (with diff) the app's rows for this candidate: the first `rows`
        (default 60; ALL_ROWS for every one), then a line counting the rows left out."""
        extra = {"diff": True, **({"rows": rows} if rows else {})} if diff else {}
        return self.ask(dict(self.request, source=source, **extra))

    def score(self, source):
        return self.judge(source).get("score")

    def rows(self, source):
        """(score, rows): the exact score and every row of the judge's diff, (marker, target, candidate) each; the
        rows say which of the target's instructions differ (prove_chain.py's row-coverage combinations)."""
        reply = self.ask(dict(self.request, source=source, raw_rows=True))
        self.jump_tables = reply.get("jump_tables") or []  # the cases whose table entry differs (instructions exact)
        return reply.get("score"), [tuple(row) for row in reply.get("rows") or []]

    def close(self):
        if self.proc:
            self.proc.kill()


def profile_line(judge, toolchain=None, replaced=None):
    """Which compiler and flags the scores that follow are of, and where they came from."""
    came = [f"compiler {'from --toolchain' if toolchain else 'of the function'}",
            f"flags {'from --replace-flags' if replaced is not None else 'of the function'}"]
    if judge.extra_flags:
        came.append(f"added: {' '.join(judge.extra_flags)}")
    return f"judged with {judge.compiler['toolchain']} {' '.join(judge.compiler['flags'])}  ({', '.join(came)})"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="+", help="candidate C files, each scored in turn")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--function", required=True)
    ap.add_argument("--diff", action="store_true", help="print the differing rows after each score")
    ap.add_argument("--rows", default=str(DIFF_ROWS), metavar="N|all",
                    help=f"with --diff: print the first N rows (default {DIFF_ROWS}) or `all`; the rest is counted")
    ap.add_argument("--header", action="store_true", help="print the project header the judge prepends, and exit")
    ap.add_argument("--toolchain", help="judge with this compiler instead of the function's own")
    ap.add_argument("--replace-flags", metavar="'-G0 -mips3 -O2 ...'",
                    help="judge with exactly these flags instead of the function's own ($DD_EXTRA_FLAGS is ignored)")
    ap.add_argument("--flags", metavar="'-fno-strength-reduce ...'",
                    help="extra compiler flags added to the function's own, to probe another profile without changing "
                         "it (default $DD_EXTRA_FLAGS; a diagnostic: the app records nothing; the function's profile is "
                         "changed with `suggest`)")
    ap.add_argument("--vram", type=int, help="the address, when the name is shared by overlays")
    ap.add_argument("--home")
    a = ap.parse_args(argv)
    claimlost.warn()  # a lost claim is said first, before minutes of scoring
    limit = rows_wanted(a.rows)
    replaced = a.replace_flags.split() if a.replace_flags is not None else None
    added = a.flags.split() if a.flags is not None else ([] if replaced is not None else None)  # None: $DD_EXTRA_FLAGS
    # vram and target stay unset: the worker's `function` op supplies them, with the header
    judge = Judge(a.slug, a.function, vram=a.vram, home=a.home, extra_flags=added, toolchain=a.toolchain,
                  flags=replaced)
    try:
        if a.header:
            print(judge.header)
            return
        print(profile_line(judge, a.toolchain, replaced), file=sys.stderr, flush=True)
        codes = []
        for path in a.source:
            with open(path) as f:
                text = f.read()
            reply = judge.judge(text, diff=a.diff, rows=limit)
            print(reply.get("score") if len(a.source) == 1 else f"{path}: {reply.get('score')}")
            if a.diff:
                print(reply.get("diff") or reply.get("detail") or "")
            if reply.get("score") and text not in {t for t, _ in codes}:
                codes.append((text, reply.get("code")))
        import invariant
        labels = {text: path for path in reversed(a.source) for text in [open(path).read()]}
        for line in invariant.hint([(labels[t], c) for t, c in codes]):
            print(line, file=sys.stderr)
    finally:
        judge.close()


if __name__ == "__main__":
    main()
