#!/usr/bin/env python3
"""The app's own judge, standalone, for fast and parallel candidate scoring: `decomp judge-worker`.

It returns exactly the score the app gives (a candidate judged 1090 in the app judged 1090 here), in about 0.1 s,
needs no claim, and opens the project attach-only so it runs beside the app without changing its state.

    fast_judge.py FILE.c --slug twine --function func_8002D240 --rom ROM --offset 187968 --size 1408 --vram 2147668544
    # as a library in a search loop (one worker process per search process; run several in parallel):
    from fast_judge import Judge
    j = Judge(slug, name, vram, target_hex, toolchain, flags); j.score(source)

Needs the repository's .venv (the `decomp` command) and DECOMP_HOME (default ~/Decomp-RetroReversing-dev).
offset, size and vram come from GET /api/function (rom_offset, size, vram). Reads the ROM only; never copy it.
Score 0 is a match; None means it did not compile.
"""
import argparse
import json
import os
import subprocess


class Judge:
    def __init__(self, slug, name, vram, target_hex, toolchain="gcc-2.7.2-kmc",
                 flags=("-G0", "-mips3", "-mgp32", "-mfp32", "-O2"), decomp=None, home=None):
        self.request = {"op": "judge", "slug": slug,
                        "function": {"name": name, "vram": vram, "target": target_hex},
                        "compiler": {"toolchain": toolchain, "flags": list(flags)}}
        repo = os.path.abspath(os.path.join(os.path.dirname(__file__), *[".."] * 4))
        self.cmd = [decomp or os.path.join(repo, ".venv", "bin", "decomp"), "judge-worker"]
        self.env = dict(os.environ, DECOMP_HOME=home or os.environ.get("DECOMP_HOME")
                        or os.path.expanduser("~/Decomp-RetroReversing-dev"), DECOMP_WINDOW="0", DECOMP_TRAY="0")
        self.proc = None

    def score(self, source):
        if self.proc is None or self.proc.poll() is not None:
            self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, text=True, env=self.env)
        self.proc.stdin.write(json.dumps(dict(self.request, source=source)) + "\n")
        self.proc.stdin.flush()
        reply = json.loads(self.proc.stdout.readline())
        if "error" in reply:
            raise SystemExit(reply["error"])
        return reply.get("score")

    def close(self):
        if self.proc:
            self.proc.kill()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    for name in ("slug", "function", "rom"):
        ap.add_argument("--" + name, required=True)
    for name in ("offset", "size", "vram"):
        ap.add_argument("--" + name, type=int, required=True)
    a = ap.parse_args()
    target = open(a.rom, "rb").read()[a.offset:a.offset + a.size].hex()
    print(Judge(a.slug, a.function, a.vram, target).score(open(a.source).read()))
