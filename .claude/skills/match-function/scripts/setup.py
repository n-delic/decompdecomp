#!/usr/bin/env python3
"""One command to start a match-function session, and one to end it: a private worktree, venv, claim and renewal.

Parallel agents kept stepping on each other (a shared venv re-synced under a run, a claim that lapsed during a long
probe, two agents claiming one function). This makes every session self-contained under /tmp/dd-<holder>/ and
undoes all of it in one step. Claims go through the app's MCP gateway on a project-scoped connection of their own (the
skill's rule: never through the window API); the window API is used only to create and revoke that connection and to
read a function's status.

    setup.py SLUG [FUNC] [--holder NAME] [--home DIR] [--random] [--max-size N]
                                                        build the session, claim FUNC (with none: the nearest open
                                                        function, lowest saved score first, of at most N=800
                                                        instructions; --random a random one; asked for exactly once),
                                                        start the renewal
    setup.py sync HOLDER                                `uv sync` the private venv to the worktree's pyproject/uv.lock
                                                        (after `git pull` or a rebase there: the pins may have moved)
    setup.py stop HOLDER                                stop the renewal, revoke the connection (that frees its
                                                        claim, even with no compile yet), remove /tmp/dd-HOLDER/

What it makes in /tmp/dd-HOLDER/:
    repo/              a git worktree (detached) at the latest origin/main; `git switch -c <type>/<name>` there
    venv/              a private environment (UV_PROJECT_ENVIRONMENT), synced to the worktree's pyproject/uv.lock at
                       setup (and by `setup.py sync`), so `uv sync` never touches anyone else's
    connection.json    mode 0600: the project-scoped MCP connection (url, bearer token, ids), slug, function, paths.
                       The token is a secret: never print it. A client reads it from here, e.g.
                       claude mcp add --transport http dd-HOLDER URL --header "Authorization: Bearer $(jq -r .token connection.json)"
    renew.pid, renew.log   the background process that renews the claim every 5 minutes until the function matches
It prints the environment to use. Release with notes through MCP (`release_function`) first when you learned something;
`stop` frees whatever is left. Run `stop` from outside that folder; it works from the primary checkout too because it only
ever *adds* a worktree there (never pulls, checks out or syncs).
"""
import argparse
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import judge  # noqa: E402  (the app's connection file, and its window API for creating a connection)

RENEW_SECONDS = 300  # a claim lapses after about ten minutes; renewing at half that leaves room for one miss
HOLDER_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
HOLDER_MAX = 40
TMP = Path(os.environ.get("DD_SESSIONS_DIR", "/tmp"))
STILL_RUNNING = "still running"  # the gateway's answer while the app works: repeat the identical call
STILL_RUNNING_RETRIES = 60
CLAIMED = re.compile(r"^## CLAIMED: [^/\s]+/(\S+)", re.M)
SUPPORTED_MCP_VERSION = "2025-06-18"


class McpError(Exception):
    pass


class Gateway:
    """A minimal MCP client for the app's gateway: JSON-RPC over HTTP, one bearer token, a fresh request id each call."""

    def __init__(self, url, token):
        self.url, self.token, self.next_id = url, token, 1

    def rpc(self, method, params=None, notification=False, timeout=120):
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notification:
            body["id"], self.next_id = self.next_id, self.next_id + 1
        request = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST", headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as reply:
                raw, kind = reply.read().decode(), reply.headers.get("Content-Type", "")
        except urllib.error.HTTPError as error:
            raise McpError(f"{method}: HTTP {error.code} {error.read().decode()[:300]}") from None
        except OSError as error:
            raise McpError(f"{method}: the gateway isn't answering ({error})") from None
        if notification or not raw.strip():
            return {}
        if "event-stream" in kind:
            raw = next((line[5:].strip() for line in raw.splitlines() if line.startswith("data:")), "{}")
        message = json.loads(raw)
        if "error" in message:
            raise McpError(f"{method}: {message['error'].get('message') or message['error']}")
        return message.get("result") or {}

    def start(self):
        self.rpc("initialize", {"protocolVersion": SUPPORTED_MCP_VERSION, "capabilities": {},
                                "clientInfo": {"name": "match-function-setup", "version": "1"}})
        self.rpc("notifications/initialized", notification=True)

    def call(self, tool, arguments):
        """The tool's text. A "still running" answer means repeat the same call (it never runs twice); a tool error raises."""
        for _ in range(STILL_RUNNING_RETRIES):
            result = self.rpc("tools/call", {"name": tool, "arguments": arguments})
            text = "\n".join(part.get("text", "") for part in result.get("content", []))
            if result.get("isError"):
                raise McpError(f"{tool}: {text[:500]}")
            if STILL_RUNNING in text.lower() and "repeat" in text.lower():
                time.sleep(1)
                continue
            return text
        raise McpError(f"{tool} was still running after {STILL_RUNNING_RETRIES} repeats; check the app")


NEAREST_MAX_INSNS = 800  # the default pick's size cap: near-misses a hand session can finish, not 3,000-line giants


def claim(gateway, function="", strategy="random", max_insns=0):
    """Claim FUNC by name (never swapped for another), or, when none is named, an open function nobody holds picked by
    `strategy` (nearest: the lowest saved score first; random) of at most `max_insns` instructions: one call."""
    if function:
        arguments = {"function": function, "fallback": False}
    else:
        arguments = {"strategy": strategy, **({"max_insns": int(max_insns)} if max_insns else {})}
    text = gateway.call("claim_function", arguments)
    found = CLAIMED.search(text)
    if not found:
        raise McpError(f"claim_function didn't claim anything: {text[:400]}")
    return found[1]


def holder_name(wanted=None):
    """A holder label for the folder and the log (up to 40 of [A-Za-z0-9._-]); a fresh `claude-xxxxxx` when none is given."""
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
    """`uv sync` inside the session's worktree into the session's own venv, so the pins (decomprepair above all) are the
    worktree's, never an older checkout's. The environment is always the private one: the primary's .venv is never synced."""
    venv, repo = (root / "venv").resolve(), (root / "repo").resolve()
    if not repo.is_dir():
        sys.exit(f"no worktree at {repo}")
    env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(venv))
    with open(log, "w") as out:
        subprocess.run(["uv", "sync"], cwd=repo, env=env, stdout=out, stderr=subprocess.STDOUT, check=True)


def sync_session(holder):
    """Re-sync the private venv to the worktree as it is now (after `git pull`/rebase there)."""
    root = session_dir(holder)
    if not (root / "repo").is_dir():
        sys.exit(f"no session worktree {root / 'repo'}")
    try:
        make_venv(root, root / "sync.log")
    except subprocess.CalledProcessError:
        sys.exit(f"uv sync failed; see {root / 'sync.log'}")
    print(f"synced {root / 'venv'} to {root / 'repo'}", flush=True)
    return 0


def write_connection(root, data):
    path = root / "connection.json"
    path.write_text(json.dumps(data, indent=2))
    path.chmod(0o600)


def gateway_url(home):
    """The Rust gateway's MCP address: the port the app keeps in its data home (the app's own address is not for helpers)."""
    port_file = Path(home or "").expanduser() / ".decomp" / "mcp-gateway.port"
    if not home or not port_file.exists():
        sys.exit("the app's MCP gateway isn't running (no .decomp/mcp-gateway.port in its data home): start it from the app "
                 "and try again; read-only work can go on meanwhile")
    return f"http://127.0.0.1:{port_file.read_text().strip()}/mcp"


def create_connection(app, slug):
    """A project-scoped MCP connection through the app's local configuration endpoint (the one use of the window API)."""
    reply = app.call("/api/manual-mcp", {"action": "create", "slug": slug})
    if "token" not in reply:
        sys.exit(f"the app wouldn't make a connection for {slug!r}: {reply.get('error') or reply}")
    return reply


def revoke_connection(app, connection_id):
    return app.call("/api/manual-mcp", {"action": "revoke", "id": connection_id})


def start_renewal(root, interval):
    """Detached, so it outlives this command; its pid file is what `stop` (and the loop itself) goes by."""
    log = open(root / "renew.log", "a")
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_renew", str(root), str(interval)],
                               stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    (root / "renew.pid").write_text(str(process.pid))
    return process.pid


def renew_loop(root, interval):
    """Claim the same function again every `interval` seconds (an own claim claimed again is renewed), until it
    matches, the session folder is removed, or this process is told to stop. Errors are logged and retried."""
    root = Path(root)
    info = json.loads((root / "connection.json").read_text())
    app = judge.App(info["home"])
    gateway = Gateway(info["gateway_url"], info["token"])
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    while not stop and (root / "renew.pid").exists():
        try:
            if app.function(info["slug"], info["function"]).get("status") == "matched":
                print(f"{info['function']} matched; the claim needs no renewing", flush=True)
                break
            claim(gateway, info["function"])
            print(time.strftime("%H:%M:%S"), "renewed", flush=True)
        except (McpError, SystemExit) as error:
            print(time.strftime("%H:%M:%S"), f"not renewed: {error}", flush=True)
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
    """Everything `start` made, undone: renewal killed, connection revoked (its claim freed), worktree and folder removed."""
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
    revoked = None
    if remote_ok and info.get("connection_id") is not None:
        try:
            revoked = revoke_connection(judge.App(info["home"]), info["connection_id"])
        except SystemExit as error:  # the app is gone: nothing to revoke
            revoked = {"error": str(error)}
    primary = info.get("primary")
    if primary and (root / "repo").exists():
        subprocess.run(["git", "-C", primary, "worktree", "remove", "--force", str(root / "repo")], capture_output=True)
        subprocess.run(["git", "-C", primary, "worktree", "prune"], capture_output=True)
    shutil.rmtree(root, ignore_errors=True)
    print(f"stopped {holder}: connection revoke {revoked}; removed {root}" if revoked is not None
          else f"stopped {holder}; removed {root}", flush=True)
    return 0


def start(args):
    holder = holder_name(args.holder)
    root = session_dir(holder)
    if root.exists():
        sys.exit(f"{root} exists: pick another --holder, or `setup.py stop {holder}` first")
    home = str(Path(args.home).expanduser()) if args.home else next(
        (str(Path(h).expanduser()) for h in judge.HOMES if (Path(h).expanduser() / ".decomp" / "server.json").exists()), "")
    app = judge.App(home or None)  # fails early, before anything is built, when no app is running
    url = gateway_url(home)
    repo = Path(args.repo) if args.repo else checkout_of(__file__)
    root.mkdir(parents=True)
    root.chmod(0o700)
    info = {"holder": holder, "slug": args.slug, "function": args.function or "", "home": home,
            "port": app.c["port"], "gateway_url": url, "primary": str(repo), "repo": str(root / "repo"),
            "venv": str(root / "venv"), "python": str(root / "venv" / "bin" / "python")}
    try:
        make_worktree(repo, root)
        if not args.no_sync:
            make_venv(root, root / "sync.log")
        connection = create_connection(app, args.slug)
        info.update(connection_id=connection["id"], connection_name=connection["name"], token=connection["token"])
        write_connection(root, info)  # at once: a failure below must still be able to revoke it
        # Claimed last so the claim's ten minutes are not spent on the build; exactly one claim call is made.
        gateway = Gateway(url, connection["token"])
        gateway.start()
        strategy = "random" if args.random else "nearest"
        info["function"] = claim(gateway, args.function or "", strategy, args.max_size)
        write_connection(root, info)
        info["renew_pid"] = start_renewal(root, args.interval)
    except BaseException as error:
        message = f"setup failed ({error}); removing {root}"
        write_connection(root, info)
        stop_session(holder)
        sys.exit(message)
    print(f"holder   {holder}\nfunction {info['function']}\nworktree {root / 'repo'}\nvenv     {root / 'venv'}")
    print(f"export UV_PROJECT_ENVIRONMENT={root / 'venv'}")
    print(f"mcp      {url} (connection {info['connection_name']}, scoped to {args.slug}; token in {root / 'connection.json'}, mode 0600)")
    print(f"python   {info['python']}   (fast_judge.py, passes.py and prove_chain.py: {Path(__file__).parent})")
    print(f"renewal  pid {info['renew_pid']} (every {args.interval // 60} min); end with: setup.py stop {holder}")
    return 0


def main(argv):
    if argv[:1] == ["_renew"]:
        renew_loop(argv[1], int(argv[2]))
        return 0
    if argv[:1] == ["sync"]:
        if len(argv) != 2:
            sys.exit(__doc__)
        return sync_session(argv[1])
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
    parser.add_argument("--random", action="store_true",
                        help="with no FUNC: a random open function instead of the nearest (lowest saved score) one")
    parser.add_argument("--max-size", type=int, default=NEAREST_MAX_INSNS, metavar="N",
                        help=f"with no FUNC: only functions of at most N instructions (default {NEAREST_MAX_INSNS}; 0: any)")
    parser.add_argument("--no-sync", action="store_true", help="skip the venv's `uv sync` (tests)")
    return start(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
