"""How the app's per-project work grows with the number of projects: run by hand, not collected by pytest.

    uv run python tests/bench_many_projects.py --projects 200 --functions 500

It makes the projects directly in a throwaway database (as tests/test_progress_speed.py does: no game, no
compiler), saves each once, then times what the app repeats on a timer for every project: one autosave pass and the
project list the sidebar asks for every 5 s. Memory is the process's peak resident size.
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import tempfile
import time
from pathlib import Path


def make(net, count: int, functions: int, root: Path) -> list[str]:
    slugs = []
    for p in range(count):
        slug = f"game{p}"
        cfg = {"kind": "rom", "platform": "n64", "rom": {"sha1": f"demo{p}", "game_code": "DEMO", "region": "us"},
               "compiler": {"toolchain": "ido-7.1"}}
        with net.store.tx() as db:
            pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)",
                             (slug, f"Game {p}", str(root / slug), json.dumps(cfg))).lastrowid
            db.execute("INSERT INTO context_versions(project_id,version,header,created_at,unit) VALUES(?,1,'',0,'')",
                       (pid,))
            db.executemany(
                "INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit,matched_source,best_score,"
                "matched_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                [(pid, f"f{i}", 0x1000 + i * 4, 4, 1, "matched" if i % 3 == 0 else "open", "",
                  f"int f{i}(void) {{ return {i}; }}" if i % 3 == 0 else None, 0 if i % 3 == 0 else 50, i)
                 for i in range(functions)])
        slugs.append(slug)
    return slugs


def timed(label: str, call, repeat: int = 3) -> float:
    best = min(_once(call) for _ in range(repeat))
    print(f"{label:<44} {best * 1000:9.1f} ms")
    return best


def _once(call) -> float:
    start = time.perf_counter()
    call()
    return time.perf_counter() - start


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects", type=int, default=100)
    parser.add_argument("--functions", type=int, default=500)
    args = parser.parse_args()
    from decompnet.app import saves
    from decompnet.app.engine import RomNetwork

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        net = RomNetwork(root / "app.db", root)
        net.apply_remote_layout = lambda *a: None
        start = time.perf_counter()
        slugs = make(net, args.projects, args.functions, root)
        for slug in slugs:
            net.progress.sync(slug, force=True)
        print(f"{args.projects} projects x {args.functions} functions made and saved in "
              f"{time.perf_counter() - start:.1f} s")
        net.progress.used.clear() if hasattr(net.progress, "used") else None  # steady state: no project open
        autosave = saves.Autosave(net)
        autosave.run_once()  # (the first pass after a start reads every folder once)
        timed("one autosave pass (every second)", autosave.run_once)
        timed("the project list (every 5 s)", lambda: [net.summary(s) for s in slugs])
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        print(f"{'peak memory':<44} {peak / (1 << 20 if sys.platform == 'darwin' else 1 << 10):9.1f} MB")


if __name__ == "__main__":
    main()
