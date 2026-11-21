#!/usr/bin/env python3
"""Prove the Deep Permuter finds a match itself: a fresh scratch app, a fresh import, the stock search.

A repair that only works when you call it by hand has not been proven (the first version of the stack-unpack repair
passed its own tests and never fired in a real run: the search saves sources in a printed dialect, not m2c's). This
script is that real run, on a throwaway copy, so the user's own app and projects stay untouched:

    uv sync under a scratch environment (never the one the user's app runs from):
        UV_PROJECT_ENVIRONMENT=/tmp/dd-scratch-venv uv sync        # in the worktree holding your change
    python3 scratch_run.py --python /tmp/dd-scratch-venv/bin/python --checkout WORKTREE \\
        --rom /path/to/the/users/own/game.z64 --function func_80123456 [--minutes 20] [--keep]

It starts an app with a throwaway DECOMP_HOME/HOME and port 6484 (no window, no tray; toolchains shared read only
through DECOMPNET_TOOLCHAINS), imports the ROM (read only, from where it is: never copied into the repo), judges the
app's untouched draft as the baseline (judge.py seed), runs the Deep Permuter with stock settings (one compiler
profile), and reports. Exit status 0 only when the search ended "Matched" and the function is matched in the app.
It removes the scratch home afterwards unless --keep.
"""
import argparse
import os
import re
import socket
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from judge import App, improvements  # noqa: E402


def toolchains_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "decompnet" / "toolchains"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "decompnet" / "toolchains"


def wait_for(predicate, seconds, every=3):
    deadline = time.time() + seconds
    while time.time() < deadline:
        found = predicate()
        if found:
            return found
        time.sleep(every)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--python", required=True, help="python of the scratch environment that has decompnet (the change)")
    ap.add_argument("--checkout", required=True, help="the worktree the change is in (the app's working directory)")
    ap.add_argument("--rom", required=True, help="the user's own game file, read where it is")
    ap.add_argument("--function", required=True)
    ap.add_argument("--minutes", type=int, default=20)
    ap.add_argument("--variants", type=int, default=10000)
    ap.add_argument("--port", type=int, default=6484)
    ap.add_argument("--keep", action="store_true", help="keep the scratch home to look at the result")
    a = ap.parse_args()

    # Two mistakes this script now refuses, each of which cost a whole run: an environment that does not hold the
    # change (a `uv run` in another state of the worktree, `git stash` included, re-syncs it to the old pin), and a
    # port another session's scratch app already holds (every session defaults to 6484).
    pin = re.search(r"decomp-repair/archive/refs/tags/v([\d.]+)\.tar", (Path(a.checkout) / "pyproject.toml").read_text())
    have = subprocess.run([a.python, "-c", "import importlib.metadata as m; print(m.version('decomprepair'))"],
                          capture_output=True, text=True).stdout.strip()
    if pin and have != pin[1]:
        sys.exit(f"the scratch environment has decomprepair {have or 'none'} but the checkout pins {pin[1]}: "
                 f"run UV_PROJECT_ENVIRONMENT=<venv> uv sync in {a.checkout} first")
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", a.port)) == 0:
            sys.exit(f"port {a.port} is taken (another session's scratch app?): pass a free --port")

    scratch = Path(tempfile.mkdtemp(prefix="dd-scratch-"))
    home, fake_home = scratch / "home", scratch / "hm"
    home.mkdir()
    fake_home.mkdir()
    env = {**os.environ, "HOME": str(fake_home), "DECOMP_HOME": str(home), "DECOMP_PORT": str(a.port),
           "DECOMP_WINDOW": "0", "DECOMP_TRAY": "0", "DECOMPNET_TOOLCHAINS": str(toolchains_dir())}
    decomp = Path(a.python).parent / "decomp"
    log = open(scratch / "app.log", "w")
    server = subprocess.Popen([str(decomp)], cwd=a.checkout, env=env, stdout=log, stderr=subprocess.STDOUT)
    code = 1
    try:
        if not wait_for(lambda: (home / ".decomp" / "server.json").exists(), 90):
            sys.exit(f"the scratch app did not start; see {scratch / 'app.log'}")
        time.sleep(2)
        app = App(str(home))
        query = urllib.parse.urlencode({"path": str(Path(a.rom).expanduser()), "confirmed": "1"})
        job = app.call("/api/import?" + query, {}).get("job")
        done = wait_for(lambda: (j := app.job(job)).get("state") != "running" and j, 1800, every=5)
        if not done or done.get("state") != "done":
            sys.exit(f"import failed: {(done or {}).get('error')}")
        slug = (done.get("result") or {}).get("slug")
        print(f"imported as {slug} in the scratch app (port {a.port}); the function's own draft is the starting point")
        time.sleep(10)  # (the project finishes its first synchronisation)
        function = app.function(slug, a.function)
        print("starting state:", function.get("status"), function.get("best"))
        seeded = app.try_source(slug, a.function, function.get("source") or "")
        print("baseline score", seeded.get("score"))
        job = app.call("/api/deep-permuter", {"slug": slug, "function": a.function, "minutes": a.minutes,
                                               "max_variants": a.variants, "compiler_profiles": 1}).get("job")
        print("deep permuter job", job)
        end = time.time() + a.minutes * 60 + 120
        while time.time() < end:
            state = app.job(job)
            progress = state.get("progress") or {}
            print(f"  {state.get('state')} {progress.get('phase')} best {progress.get('best_score')} "
                  f"tested {progress.get('tested')} {progress.get('current_family')}", flush=True)
            if state.get("state") != "running":
                if state.get("error"):
                    print("job error:", state["error"])  # e.g. a family's module missing from the environment
                break
            time.sleep(20)
        state = app.job(job)
        for line in improvements(state)[-12:]:
            print("  ", line)
        final = app.function(slug, a.function)
        result = state.get("result") or {}
        print("result:", result.get("reason"), "| function status:", final.get("status"), final.get("best"))
        code = 0 if result.get("matched") and final.get("status") == "matched" else 1
        print("PROVEN: the stock Deep Permuter matched it" if code == 0 else "NOT proven: the search did not match it")
    finally:
        server.terminate()
        try:
            server.wait(30)
        except subprocess.TimeoutExpired:
            server.kill()
        if a.keep:
            print("scratch home kept:", scratch)
        else:
            shutil.rmtree(scratch, ignore_errors=True)
    sys.exit(code)


if __name__ == "__main__":
    main()
