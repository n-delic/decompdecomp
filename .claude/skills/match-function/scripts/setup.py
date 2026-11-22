#!/usr/bin/env python3
"""One command to start a match-function session, and one to end it: a private worktree, venv, claim and renewal.

Parallel agents kept stepping on each other (a shared venv re-synced under a run, a claim that lapsed during a long
probe, two agents claiming one function). This makes every session self-contained under /tmp/dd-<holder>/ and
undoes all of it in one step.

    setup.py SLUG [FUNC] [--holder NAME] [--home DIR]   build the session, claim FUNC (a random open function when
                                                        none is given: asked for exactly once), start the renewal
    setup.py stop HOLDER                                release the claim, stop the renewal, remove /tmp/dd-HOLDER/

What it makes in /tmp/dd-HOLDER/:
    repo/              a git worktree (detached) at the latest origin/main; `git switch -c <type>/<name>` there
    venv/              a private environment (UV_PROJECT_ENVIRONMENT), so `uv sync` never touches anyone else's
    connection.json    home, port, slug, holder, function, paths (no secret: judge.py reads it from the app's home)
    renew.pid, renew.log   the background process that renews the claim every 5 minutes until the function matches
It prints the environment to use. Run `stop` from anywhere outside that folder when the work is done; it works from
the primary checkout too because it only ever *adds* a worktree there (never pulls, checks out or syncs).
"""
import argparse
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import judge  # noqa: E402  (the claim and release calls, and the app's connection file)

RENEW_SECONDS = 300  # a claim lapses after about ten minutes; renewing at half that leaves room for one miss
HOLDER_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
HOLDER_MAX = 40
TMP = Path(os.environ.get("DD_SESSIONS_DIR", "/tmp"))


def holder_name(wanted=None):
    """A holder the app accepts (judge.py: up to 40 of [A-Za-z0-9._-]); a fresh `claude-xxxxxx` when none is given."""
    name = wanted or "claude-" + secrets.token_hex(3)
    if not name or len(name) > HOLDER_MAX or set(name) - HOLDER_CHARS:
        sys.exit(f"holder {name!r}: use up to {HOLDER_MAX} letters, digits, '.', '_' or '-'")
    return name


def session_dir(holder):
    return TMP / f"dd-{holder}"


def run(cmd, **kwargs):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


def checkout_of(script):
    """The repository this script lives in; a worktree reports its own folder, and `git worktree add` works from either."""
    return Path(run(["git", "-C", str(Path(script).resolve().parent), "rev-parse", "--show-toplevel"]).stdout.strip())


def make_worktree(repo, root, remote="origin"):
    run(["git", "-C", str(repo), "fetch", "-q", remote])
    run(["git", "-C", str(repo), "worktree", "add", "-q", "--detach", str(root / "repo"), f"{remote}/main"])


def make_venv(root, log):
    env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(root / "venv"))
    with open(log, "w") as out:
        subprocess.run(["uv", "sync"], cwd=root / "repo", env=env, stdout=out, stderr=subprocess.STDOUT, check=True)


def write_connection(root, data):
    path = root / "connection.json"
    path.write_text(json.dumps(data, indent=2))
    path.chmod(0o600)


def start_renewal(root, interval):
    """Detached, so it outlives this command; its pid file is what `stop` (and the loop itself) goes by."""
    log = open(root / "renew.log", "a")
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_renew", str(root), str(interval)],
                               stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    (root / "renew.pid").write_text(str(process.pid))
    return process.pid


def renew_loop(root, interval):
    """Claim the same function again every `interval` seconds (that is how a claim is renewed), until it matches,
    the session folder is removed, or this process is told to stop."""
    root = Path(root)
    info = json.loads((root / "connection.json").read_text())
    app = judge.App(info["home"], info["holder"])
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    while not stop and (root / "renew.pid").exists():
        state = app.function(info["slug"], info["function"])
        if state.get("status") == "matched":
            print(f"{info['function']} matched; the claim needs no renewing", flush=True)
            break
        result = app.claim(info["slug"], info["function"])
        print(time.strftime("%H:%M:%S"), "renewed" if result.get("name") else f"not renewed: {result.get('error') or result}",
              flush=True)
        for _ in range(int(interval)):  # short sleeps so a stop is prompt
            if stop or not (root / "renew.pid").exists():
                break
            time.sleep(1)
    (root / "renew.pid").unlink(missing_ok=True)


def alive(pid):
    """Is `pid` still our renewal? (a recycled pid must never be signalled)"""
    try:
        command = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
    except OSError:
        return False
    return "_renew" in command


def stop_session(holder, remote_ok=True):
    """Everything `start` made, undone: renewal killed, claim released, worktree and folder removed."""
    root = session_dir(holder)
    if not root.is_dir():
        sys.exit(f"no session folder {root}")
    info = json.loads((root / "connection.json").read_text()) if (root / "connection.json").exists() else {}
    pid_file = root / "renew.pid"
    if pid_file.exists():
        pid = int(pid_file.read_text() or 0)
        pid_file.unlink(missing_ok=True)  # the loop leaves on its own once this is gone, even if the signal is missed
        if pid and alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
    released = None
    if remote_ok and info.get("function"):
        try:
            released = judge.App(info["home"], info["holder"]).release(info["slug"], info["function"])
        except SystemExit as error:  # the app is gone: nothing to release to
            released = {"error": str(error)}
    primary = info.get("primary")
    if primary and (root / "repo").exists():
        subprocess.run(["git", "-C", primary, "worktree", "remove", "--force", str(root / "repo")], capture_output=True)
        subprocess.run(["git", "-C", primary, "worktree", "prune"], capture_output=True)
    shutil.rmtree(root, ignore_errors=True)
    print(f"stopped {holder}: claim release {released}; removed {root}" if released is not None
          else f"stopped {holder}; removed {root}", flush=True)
    return 0


def start(args):
    holder = holder_name(args.holder)
    root = session_dir(holder)
    if root.exists():
        sys.exit(f"{root} exists: pick another --holder, or `setup.py stop {holder}` first")
    app = judge.App(args.home, holder)  # fails early, before anything is built, when no app is running
    repo = Path(args.repo) if args.repo else checkout_of(__file__)
    root.mkdir(parents=True)
    root.chmod(0o700)
    info = {"holder": holder, "slug": args.slug, "function": args.function or "", "home": args.home or "",
            "port": app.c["port"], "primary": str(repo), "repo": str(root / "repo"),
            "venv": str(root / "venv"), "python": str(root / "venv" / "bin" / "python")}
    try:
        make_worktree(repo, root)
        if not args.no_sync:
            make_venv(root, root / "sync.log")
        # Claimed last so the claim's ten minutes are not spent on the build; exactly one claim call is made.
        result = app.claim(args.slug, args.function or "")
        if "name" not in result:
            raise SystemExit(f"no claim: {result.get('error') or result}")
        info["function"] = result["name"]
        write_connection(root, info)
        info["renew_pid"] = start_renewal(root, args.interval)
    except BaseException as error:
        info_text = f"setup failed ({error}); removing {root}"
        write_connection(root, info)
        stop_session(holder, remote_ok=False)
        sys.exit(info_text)
    print(f"holder   {holder}\nfunction {info['function']}\nworktree {root / 'repo'}\nvenv     {root / 'venv'}")
    print(f"export UV_PROJECT_ENVIRONMENT={root / 'venv'}")
    print(f"python   {info['python']}   (judge: {Path(__file__).parent}/judge.py --holder {holder} ...)")
    print(f"renewal  pid {info['renew_pid']} (every {args.interval // 60} min); end with: setup.py stop {holder}")
    return 0


def main(argv):
    if argv[:1] == ["_renew"]:
        renew_loop(argv[1], int(argv[2]))
        return 0
    if argv[:1] == ["stop"]:
        if len(argv) != 2:
            sys.exit(__doc__)
        return stop_session(argv[1])
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("slug")
    parser.add_argument("function", nargs="?")
    parser.add_argument("--holder")
    parser.add_argument("--home")
    parser.add_argument("--repo", help="the checkout to add the worktree to (default: the one this script is in)")
    parser.add_argument("--interval", type=int, default=RENEW_SECONDS)
    parser.add_argument("--no-sync", action="store_true", help="skip `uv sync` (tests)")
    return start(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
