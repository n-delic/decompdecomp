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
    setup.py try HOLDER CAND.c [FUNC]                   submit a candidate to the app as this session (compile_and_diff over
                                                        the session's own MCP connection, which holds the claim) and print
                                                        the app's verdict; FUNC defaults to the claimed function
    setup.py check HOLDER                               ask the app now whether this session still holds its function (also
                                                        `status`); exit 1 and a loud warning when not. Run it every 20 minutes
                                                        and before long isolated work. Every command here (and fast_judge,
                                                        prove_chain, grid) prints the same warning while a LOST file exists
    setup.py env HOLDER                                 print `export UV_PROJECT_ENVIRONMENT=... DECOMP_HOME=... DECOMP_PORT=...`
                                                        for the session (eval it; the token is never printed)
    mcp.py HOLDER TOOL [JSON]                           (its own script) call any app MCP tool over this session's connection
    setup.py release HOLDER                             after `release_function`: the renewal stops renewing the released
                                                        function and never claims it again
    setup.py reclaim HOLDER [FUNC] [--random] [--max-size N]
                                                        claim another function (FUNC, or the nearest/random one, as at
                                                        start) and renew that one from now on; a released function is
                                                        refused
    setup.py stop HOLDER                                stop the renewal (SIGTERM, then SIGKILL, checked gone), revoke the connection (that frees its
                                                        claim, even with no compile yet), remove /tmp/dd-HOLDER/

What it makes in /tmp/dd-HOLDER/:
    repo/              a git worktree (detached) at the latest origin/main; `git switch -c <type>/<name>` there
    venv/              a private environment (UV_PROJECT_ENVIRONMENT), synced to the worktree's pyproject/uv.lock at
                       setup (and by `setup.py sync`), so `uv sync` never touches anyone else's
    connection.json    mode 0600: the project-scoped MCP connection (url, bearer token, ids), slug, function, paths.
                       The token is a secret: never print it. A client reads it from here, e.g.
                       claude mcp add --transport http dd-HOLDER URL --header "Authorization: Bearer $(jq -r .token connection.json)"
    LOST               written by the renewal when the claim is lost (someone else holds the function, or it is matched)
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
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claimlost  # noqa: E402
import judge  # noqa: E402  (the app's connection file, and its window API for creating a connection)

RENEW_SECONDS = 300  # a claim lapses after about ten minutes; renewing at half that leaves room for one miss
HOLDER_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
HOLDER_MAX = 40
TMP = Path(os.environ.get("DD_SESSIONS_DIR", "/tmp"))
STILL_RUNNING = "still running"  # the gateway's answer while the app works: repeat the identical call
CALL_WAIT_SECONDS = int(os.environ.get("DD_CALL_WAIT_SECONDS", "1800"))  # how long one call may wait for the app (30 min)
TRANSIENT_PAUSE = 5  # between repeats of a call the app or gateway dropped (overloaded, restarting)
STOP_GRACE_SECONDS = 5  # `stop`: SIGTERM, this long, then SIGKILL
CLAIMED = re.compile(r"^## CLAIMED: [^/\s]+/(\S+)", re.M)
UNCHANGED = re.compile(r"You still hold the unchanged claim: [^/\s]+/(\S+?)\.?(?:\s|$)")
CLAIMED_BY = re.compile(r"is claimed by (.+?)(?: for another|$)", re.M)
RENEW_CALL_SECONDS = 120  # one renewal attempt waits this long for the app; the loop tries again
RENEW_RETRIES = 4  # a failed renewal is tried again at once this many times, RETRY_SECONDS apart
RETRY_SECONDS = 10
CLAIM_LAPSE_SECONDS = 600  # without a renewal this long the claim has lapsed whatever the app says now
SUPPORTED_MCP_VERSION = "2025-06-18"


class McpError(Exception):
    transient = False  # a dropped connection, timeout or 5xx: repeating the identical call is safe and may succeed


class AppSlow(McpError):
    """The app kept answering "still running" (or not at all) until the wait ran out. The work continues server-side:
    repeating the identical call collects it, so nothing built for the session is torn down because of this."""


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
            problem = McpError(f"{method}: HTTP {error.code} {error.read().decode()[:300]}")
            problem.transient = error.code in (408, 425, 429) or error.code >= 500
            raise problem from None
        except OSError as error:
            problem = McpError(f"{method}: the gateway isn't answering ({error})")
            problem.transient = True
            raise problem from None
        if notification or not raw.strip():
            return {}
        if "event-stream" in kind:
            raw = next((line[5:].strip() for line in raw.splitlines() if line.startswith("data:")), "{}")
        message = json.loads(raw)
        if "error" in message:
            raise McpError(f"{method}: {message['error'].get('message') or message['error']}")
        return message.get("result") or {}

    def patient(self, method, params=None, notification=False, wait=None):
        """`rpc`, repeated while the app or gateway drops it (overloaded, restarting) until `wait` seconds are up."""
        deadline = time.time() + (CALL_WAIT_SECONDS if wait is None else wait)
        while True:
            try:
                return self.rpc(method, params, notification)
            except McpError as error:
                if not error.transient:
                    raise
                if time.time() >= deadline:
                    raise AppSlow(f"{error} (still failing after waiting; the app looks overloaded)") from None
                print(f"{method}: {error}; trying again", file=sys.stderr, flush=True)
                time.sleep(TRANSIENT_PAUSE)

    def start(self):
        self.patient("initialize", {"protocolVersion": SUPPORTED_MCP_VERSION, "capabilities": {},
                                    "clientInfo": {"name": "match-function-setup", "version": "1"}})
        self.patient("notifications/initialized", notification=True)

    def call(self, tool, arguments, wait=None):
        """The tool's text. A "still running" answer means repeat the same call (the gateway never runs it twice and
        keeps its result 15 minutes), so it is repeated, printing progress to stderr, for up to `wait` seconds (default
        CALL_WAIT_SECONDS) before AppSlow; a dropped connection or 5xx is repeated the same way. A tool error raises."""
        began = time.time()
        deadline = began + (CALL_WAIT_SECONDS if wait is None else wait)
        while True:
            result = self.patient("tools/call", {"name": tool, "arguments": arguments},
                                  wait=max(0, deadline - time.time()))
            text = "\n".join(part.get("text", "") for part in result.get("content", []))
            lowered = text.lower()
            # The gateway marks its "still running ... repeat the same call" answer as an error result: repeat it, don't raise.
            if result.get("isError") and not (STILL_RUNNING in lowered and "repeat" in lowered):
                raise McpError(f"{tool}: {text[:500]}")
            if "accepted" in lowered and "not repeat" in lowered:
                return text  # the app took the work and will finish it: repeating would only queue it twice
            if STILL_RUNNING in lowered and "repeat" in lowered:
                if time.time() >= deadline:
                    raise AppSlow(f"{tool} was still running after {int(time.time() - began)} s; the app is overloaded. "
                                  f"It keeps working: repeat the identical call to collect the result")
                print(f"{tool}: still running in the app ({int(time.time() - began)} s so far); waiting for it",
                      file=sys.stderr, flush=True)
                time.sleep(1)
                continue
            return text


NEAREST_MAX_INSNS = 800  # the default pick's size cap: near-misses a hand session can finish, not 3,000-line giants


def claim(gateway, function="", strategy="random", max_insns=0, wait=None):
    """Claim FUNC by name (never swapped for another), or, when none is named, an open function nobody holds picked by
    `strategy` (nearest: the lowest saved score first; random) of at most `max_insns` instructions: one call."""
    waiting = {} if wait is None else {"wait": wait}
    if function:
        text = gateway.call("claim_function", {"function": function, "fallback": False}, **waiting)
    else:
        text = None
        for wanted, cap in older_orders(strategy, max_insns):
            arguments = {"strategy": wanted, **({"max_insns": int(cap)} if cap else {})}
            try:
                text = gateway.call("claim_function", arguments, **waiting)
                break
            except McpError as error:
                if not unknown_option(str(error)):
                    raise
        if text is None:
            raise McpError("claim_function refused every claim order this script knows")
    found = CLAIMED.search(text)
    if not found and function:
        # the app answers a renewal that changes nothing "You still hold the unchanged claim: slug/FUNC": still held
        found = UNCHANGED.search(text)
    if not found:
        raise McpError(f"claim_function didn't claim anything: {text[:400]}")
    return found[1]


def older_orders(strategy, max_insns):
    """The claim to ask for, then what an app started before `nearest` and `max_insns` existed understands: closest
    (near-misses relative to size) with the cap, closest alone, random. Refused calls claim nothing, so the one claim
    that succeeds is still the only one."""
    orders = [(strategy, max_insns)]
    if strategy == "nearest":
        orders += [("closest", max_insns), ("closest", 0)]
    orders += [("random", 0)]
    return list(dict.fromkeys((s, c or 0) for s, c in orders))


def unknown_option(message):
    """An older app's refusal of an order or argument it doesn't know (not a refusal of the claim itself)."""
    return any(word in message for word in ("strategy must be one of", "max_insns", "unexpected keyword",
                                             "Unexpected keyword", "additional properties", "Invalid arguments"))


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
    """Replaced whole in one step, so the renewal loop reading it never sees half a file."""
    path = root / "connection.json"
    temporary = root / "connection.json.tmp"
    temporary.write_text(json.dumps(data, indent=2))
    temporary.chmod(0o600)
    os.replace(temporary, path)


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
    """Claim the session's current function again every `interval` seconds (an own claim claimed again is renewed),
    until it matches, the session folder is removed, or this process is told to stop. The function is read from
    connection.json each cycle: `setup.py release` empties it and `reclaim` changes it, so a released function is
    never claimed again. Errors are logged and retried."""
    root = Path(root)
    info = json.loads((root / "connection.json").read_text())
    app = judge.App(info["home"])
    gateway = Gateway(info["gateway_url"], info["token"])
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    last_good = time.time()
    while not stop and (root / "renew.pid").exists():
        pause = interval
        try:
            current = json.loads((root / "connection.json").read_text())
            function = current.get("function") or ""
            if not function or function in current.get("released", []):
                print(time.strftime("%H:%M:%S"), "no claimed function; nothing to renew", flush=True)
            else:
                outcome = renew_once(root, current, app, gateway, last_good, stop)
                if outcome == "matched":
                    break
                if outcome == "renewed":
                    last_good = time.time()
                elif outcome == "failed":
                    pause = min(interval, RETRY_SECONDS * 3)  # try again soon, not after a whole interval
        except (SystemExit, OSError, ValueError) as error:
            print(time.strftime("%H:%M:%S"), f"not renewed: {error}", flush=True)
        for _ in range(int(pause)):  # short sleeps so a stop is prompt
            if stop or not (root / "renew.pid").exists():
                break
            time.sleep(1)
    (root / "renew.pid").unlink(missing_ok=True)


def renew_once(root, current, app, gateway, last_good, stop=(), pause=None):
    """One renewal of the session's function, retried promptly when the app is slow or errors. Returns "renewed",
    "matched", "lost" (someone else holds it: recorded in LOST, never claimed again by retrying) or "failed" (the app
    never answered; LOST is written once the claim must have lapsed). A claim that comes back after a lapse clears LOST.
    A renewal never takes a released function: the caller only passes the current one (#2758)."""
    function, pause = current["function"], RETRY_SECONDS if pause is None else pause
    error = None
    for attempt in range(RENEW_RETRIES):
        try:
            if app.function(current["slug"], function).get("status") == "matched":
                claimlost.write(root, function, "matched", matched=True)
                print(f"{function} matched; the claim needs no renewing", flush=True)
                return "matched"
            claim(gateway, function, wait=RENEW_CALL_SECONDS)
            claimlost.clear(root)
            print(time.strftime("%H:%M:%S"), "renewed", function, flush=True)
            return "renewed"
        except McpError as problem:
            error = str(problem)
            holder = CLAIMED_BY.search(error)
            if holder:
                owner = holder[1].strip()[:80]
                claimlost.write(root, function, error[:300], holder=owner)
                print(time.strftime("%H:%M:%S"), f"CLAIM LOST: {function} is held by {owner}", flush=True)
                return "lost"
            if "already matched" in error:
                claimlost.write(root, function, error[:300], matched=True)
                print(f"{function} is matched; the claim needs no renewing", flush=True)
                return "matched"
        except (SystemExit, OSError, ValueError) as problem:
            error = str(problem)
        print(time.strftime("%H:%M:%S"), f"not renewed (try {attempt + 1}/{RENEW_RETRIES}): {error}", flush=True)
        for _ in range(int(pause)):
            if stop:
                return "failed"
            time.sleep(1)
    if time.time() - last_good >= CLAIM_LAPSE_SECONDS:
        claimlost.write(root, function, f"not renewed for {int((time.time() - last_good) // 60)} min: {error}"[:300])
    return "failed"


def release_session(holder):
    """Record that the session let its function go (after `release_function`): the renewal stops claiming it."""
    root = session_dir(holder)
    info = read_session(root)
    function = info.get("function") or ""
    if function:
        info["released"] = sorted({*info.get("released", []), function})
    info["function"] = ""
    write_connection(root, info)
    claimlost.clear(root)  # nothing is claimed any more, so nothing is lost
    print(f"{holder}: {function or 'no function'} released; the renewal no longer renews it", flush=True)
    return 0


def reclaim_session(holder, function="", strategy="nearest", max_insns=NEAREST_MAX_INSNS):
    """Claim another function for a session that released its first, and make it the one the renewal renews."""
    root = session_dir(holder)
    info = read_session(root)
    if function and function in info.get("released", []):
        sys.exit(f"{function} was released by this session; claim another function")
    gateway = Gateway(info["gateway_url"], info["token"])
    try:
        gateway.start()
        claimed = claim(gateway, function, strategy, max_insns)
    except McpError as error:
        sys.exit(f"nothing reclaimed: {error}")
    if claimed in info.get("released", []):  # the app handed back the function this session let go
        sys.exit(f"{claimed} was released by this session and was claimed again; release it with its claim token and "
                 f"ask for another function")
    info = read_session(root)  # fresh: the claim took a while
    info["function"], info["started"] = claimed, time.time()
    write_connection(root, info)
    claimlost.clear(root)
    print(f"{holder}: claimed {claimed}; the renewal now renews it", flush=True)
    return 0


def read_session(root):
    try:
        return json.loads((root / "connection.json").read_text())
    except (OSError, ValueError):
        sys.exit(f"no session in {root}: start one with `setup.py SLUG [FUNC]`")


def try_candidate(holder, path, function=""):
    """Submit CAND.c as the session: `compile_and_diff` over the connection that holds the claim, and print the app's
    verdict text. The window API's /api/try judges as another identity and is refused while this connection holds the
    claim (HTTP 400), so a session never goes through it; nor does anyone need their own gateway client."""
    root = session_dir(holder)
    if not (root / "connection.json").exists():
        sys.exit(f"no session {holder!r} ({root / 'connection.json'} is missing): start one with setup.py SLUG [FUNC]")
    info = json.loads((root / "connection.json").read_text())
    name = function or info.get("function")
    if not name:
        sys.exit("the session has no claimed function; name it: setup.py try HOLDER CAND.c FUNC")
    marker = claimlost.read(root)
    if claimlost.taken(marker) and marker.get("function") == name:
        sys.exit(claimlost.warning(root) + "not submitted: someone else holds this function now.")
    try:
        source = Path(path).read_text()
    except OSError as error:
        sys.exit(f"can't read {path}: {error}")
    gateway = Gateway(info["gateway_url"], info["token"])
    pending_file = root / "try-pending.json"
    try:
        gateway.start()
        # One submission in flight at a time: a slow app keeps compiling a source after its `try` gave up waiting, and a
        # second, different source would run behind it (a late stale submit once un-matched a function). The earlier one
        # is repeated (the same call, so the gateway hands back its result) and finished before this one goes out.
        earlier = read_pending(pending_file)
        if earlier and (earlier["function"], earlier["source"]) != (name, source):
            print(f"an earlier submission for {earlier['function']} was still in flight; collecting its verdict first "
                  f"(a second source is never sent while one is running)", flush=True)
            print(gateway.call("compile_and_diff", {"function": earlier["function"], "source": earlier["source"]}))
            pending_file.unlink(missing_ok=True)
            print("--- the earlier submission's verdict is above; now your new candidate ---", flush=True)
        pending_file.write_text(json.dumps({"function": name, "source": source}))
        text = gateway.call("compile_and_diff", {"function": name, "source": source})
        pending_file.unlink(missing_ok=True)  # answered: nothing is in flight any more
        print(text)
    except AppSlow as error:
        sys.exit(f"{error}\nThe submission is still in flight and is kept: `setup.py try {holder} {path}` again collects its "
                 f"verdict (a different candidate is held back until then).")
    except McpError as error:
        pending_file.unlink(missing_ok=True)  # the app answered (a refusal): nothing in flight
        sys.exit(f"not submitted: {error}")
    return 0


def read_pending(path):
    """The submission a `try` left in flight ({function, source}), or None."""
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data.get("function"), str) and isinstance(data.get("source"), str) else None
    except (OSError, ValueError, AttributeError):
        return None


def check_claim(holder):
    """Ask the app now (not the marker) whether this session holds its function: `get_claims` over the session's
    connection, which claims nothing. Exit status 0 held, 1 not held (and LOST is written, so the other commands warn)."""
    root = session_dir(holder)
    info = read_session(root)
    function = info.get("function") or ""
    if not function:
        print(f"{holder}: no function is claimed (released); `setup.py reclaim {holder}` takes the next one")
        return 1
    try:
        status = judge.App(info["home"]).function(info["slug"], function)
        gateway = Gateway(info["gateway_url"], info["token"])
        gateway.start()
        held = json.loads(gateway.call("get_claims", {})).get("claims", [])
    except (McpError, SystemExit, OSError, ValueError) as error:
        print(f"{holder}: could not ask the app whether {function} is still claimed ({error}); try again in a minute")
        return 1
    if any(entry.get("function") == function for entry in held):
        claimlost.clear(root)
        print(f"{holder}: {function} is claimed by this session (until {time.strftime('%H:%M', time.localtime(max(e.get('expires_at') or 0 for e in held)))})"
              if any(e.get("expires_at") for e in held) else f"{holder}: {function} is claimed by this session")
        return 0
    if status.get("status") == "matched":
        claimlost.write(root, function, "matched", matched=True)
    else:
        owner = next((status[key] for key in ("claimed_by", "claim_holder", "holder") if isinstance(status.get(key), str)), "")
        claimlost.write(root, function, "the app no longer lists the claim as this session's", holder=owner)
    print(claimlost.warning(root) + f"{holder}: {function} is NOT claimed by this session.")
    return 1


def session_gateway(slug="", holder=""):
    """(a started Gateway, the session's connection info) for a read or a write made as a session of this project: the
    named `holder`'s, or the only session under TMP whose project is `slug`. Several candidates are never guessed
    between (a session's token is its owner's): name one with --holder."""
    if holder:
        roots = [session_dir(holder_name(holder))]
    else:
        roots = sorted(path.parent for path in TMP.glob("dd-*/connection.json"))
    found = []
    for root in roots:
        try:
            info = json.loads((root / "connection.json").read_text())
        except (OSError, ValueError):
            continue
        if not slug or info.get("slug") == slug:
            found.append(info)
    if not found:
        sys.exit(f"no setup.py session for {slug or 'any project'}{f' ({holder})' if holder else ''}: start one with "
                 f"`setup.py SLUG [FUNC]`; the app's MCP tools need that connection")
    if len(found) > 1:
        sys.exit("several sessions match (" + ", ".join(i.get("holder", "?") for i in found) + "): pass --holder NAME")
    info = found[0]
    gateway = Gateway(info["gateway_url"], info["token"])
    try:
        gateway.start()
    except McpError as error:
        sys.exit(f"the session's connection isn't answering: {error}")
    return gateway, info


def env_lines(holder):
    """The lines a shell evals to work as the session: its private venv, and the app's data home and window port (never
    the token). Values come from connection.json, shell-quoted."""
    root = session_dir(holder_name(holder))
    info = read_session(root)
    values = {"UV_PROJECT_ENVIRONMENT": info.get("venv") or str(root / "venv"), "DECOMP_HOME": info.get("home") or "",
              "DECOMP_PORT": str(info.get("port") or "")}
    return "export " + " ".join(f"{name}={shlex.quote(value)}" for name, value in values.items() if value)


def alive(pid):
    """Is `pid` still our renewal? (a recycled pid must never be signalled). A zombie (exited, not yet reaped) is gone."""
    try:
        os.waitpid(pid, os.WNOHANG)  # reaps it when it is our own child (tests, or a `setup.py` that never exited)
    except (ChildProcessError, OSError):
        pass
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "stat=,command="], capture_output=True, text=True).stdout.strip()
    except OSError:
        return False
    return "_renew" in out and not out.startswith("Z")


def renewers(root, pid=0):
    """Pids of this session's renewal: the one in renew.pid and any other `_renew <root>` process (an orphan whose pid
    file is gone). Only processes whose command line says `_renew` and names this folder are ever returned."""
    found = {pid} if pid and alive(pid) else set()
    try:
        listing = subprocess.run(["ps", "-axo", "pid=,stat=,command="], capture_output=True, text=True).stdout
    except OSError:
        return found
    for line in listing.splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3 and not fields[1].startswith("Z") and f"_renew {root} " in fields[2] + " ":
            found.add(int(fields[0]))
    return found


def terminate_renewers(root, pid=0):
    """SIGTERM the renewal (its process group too: it was started as a session of its own), wait STOP_GRACE_SECONDS,
    SIGKILL what is left, and check. Returns the pids that survived (none: it is gone)."""
    def send(pids, sig):
        for target in pids:
            for call in (lambda: os.killpg(target, sig), lambda: os.kill(target, sig)):
                try:
                    call()
                    break
                except OSError:
                    continue
    pids = renewers(root, pid)
    send(pids, signal.SIGTERM)
    deadline = time.time() + STOP_GRACE_SECONDS
    while time.time() < deadline and renewers(root, pid):
        time.sleep(0.1)
    left = renewers(root, pid)
    if left:
        send(left, signal.SIGKILL)  # a renewal inside a long call never sees SIGTERM's flag: end it for good
        deadline = time.time() + STOP_GRACE_SECONDS
        while time.time() < deadline and renewers(root, pid):
            time.sleep(0.1)
    return renewers(root, pid)


def stop_session(holder, remote_ok=True):
    """Everything `start` made, undone: renewal killed, connection revoked (its claim freed), worktree and folder removed."""
    root = session_dir(holder)
    if not root.is_dir():
        sys.exit(f"no session folder {root}")
    info = json.loads((root / "connection.json").read_text()) if (root / "connection.json").exists() else {}
    pid_file = root / "renew.pid"
    pid = 0
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text() or 0)
        except ValueError:
            pid = 0
    survivors = terminate_renewers(root, pid)  # before the pid file goes: the loop also leaves once it is gone
    pid_file.unlink(missing_ok=True)
    if survivors:
        print(f"WARNING: the renewal (pid {', '.join(map(str, sorted(survivors)))}) is still alive after SIGKILL; "
              f"kill it by hand", flush=True)
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
    return 1 if survivors else 0


def resumable(root, args):
    """The info of a session whose claim was still pending when the app was too slow (kept on purpose: its worktree, venv
    and connection are built), or None. Such a session is finished by running the same command again."""
    try:
        info = json.loads((root / "connection.json").read_text())
    except (OSError, ValueError):
        return None
    if not info.get("claim_pending") or info.get("slug") != args.slug or not (root / "repo").is_dir():
        return None
    return info


def start(args):
    holder = holder_name(args.holder)
    root = session_dir(holder)
    info = resumable(root, args) if root.exists() else None
    if root.exists() and info is None:
        sys.exit(f"{root} exists: pick another --holder, or `setup.py stop {holder}` first")
    home = (info or {}).get("home") or (str(Path(args.home).expanduser()) if args.home else next(
        (str(Path(h).expanduser()) for h in judge.HOMES if (Path(h).expanduser() / ".decomp" / "server.json").exists()), ""))
    app = judge.App(home or None)  # fails early, before anything is built, when no app is running
    url = gateway_url(home)
    repo = Path(args.repo) if args.repo else checkout_of(__file__)
    resuming = info is not None
    if resuming:
        print(f"{holder}: a claim was still pending when the app was slow; reusing the session's worktree, venv and "
              f"connection and collecting it (the identical request, so it is never claimed twice)", flush=True)
        asked = info["claim_request"]
    else:
        root.mkdir(parents=True)
        root.chmod(0o700)
        asked = {"function": args.function or "", "random": bool(args.random), "max_size": args.max_size}
        info = {"holder": holder, "slug": args.slug, "function": "", "home": home,
                "port": app.c["port"], "gateway_url": url, "primary": str(repo), "repo": str(root / "repo"),
                "venv": str(root / "venv"), "python": str(root / "venv" / "bin" / "python")}
    try:
        if not resuming:
            make_worktree(repo, root)
            if not args.no_sync:
                make_venv(root, root / "sync.log")
            connection = create_connection(app, args.slug)
            info.update(connection_id=connection["id"], connection_name=connection["name"], token=connection["token"])
        info.update(claim_pending=True, claim_request=asked)
        write_connection(root, info)  # at once: a failure below must still be able to revoke it
        # Claimed last so the claim's ten minutes are not spent on the build; exactly one claim call is made.
        gateway = Gateway(url, info["token"])
        gateway.start()
        strategy = "random" if asked["random"] else "nearest"
        info["function"] = claim(gateway, asked["function"], strategy, asked["max_size"])
        info.pop("claim_pending", None)
        info["started"] = time.time()  # the hand-off clock's start (clock.py HOLDER)
        write_connection(root, info)
        info["renew_pid"] = start_renewal(root, args.interval)
    except AppSlow as error:
        # The app is overloaded, not refusing: what was built stays, and the same command again collects the claim.
        sys.exit(f"{error}\nThe session {holder} is kept (worktree, venv, connection); run the same command again to "
                 f"collect the claim: setup.py {args.slug} {asked['function']} --holder {holder}".replace("  ", " "))
    except BaseException as error:
        message = f"setup failed ({error}); removing {root}"
        write_connection(root, info)
        stop_session(holder)
        sys.exit(message)
    print(f"holder   {holder}\nfunction {info['function']}\nworktree {root / 'repo'}\nvenv     {root / 'venv'}")
    print(env_lines(holder))
    print(f"mcp      {url} (connection {info['connection_name']}, scoped to {args.slug}; token in {root / 'connection.json'}, mode 0600)")
    print(f"python   {info['python']}   (fast_judge.py, passes.py and prove_chain.py: {Path(__file__).parent})")
    print(f"renewal  pid {info['renew_pid']} (every {args.interval // 60} min); end with: setup.py stop {holder}")
    return 0


def main(argv):
    if argv[:1] == ["_renew"]:
        renew_loop(argv[1], int(argv[2]))
        return 0
    if argv[:1] in (["check"], ["status"]):
        if len(argv) != 2:
            sys.exit(__doc__)
        return check_claim(holder_name(argv[1]))
    if len(argv) > 1 and argv[0] in ("sync", "try", "env", "release", "reclaim") and not argv[1].startswith("-"):
        claimlost.warn(session_dir(argv[1]))  # loud, before whatever the command prints
    if argv[:1] == ["sync"]:
        if len(argv) != 2:
            sys.exit(__doc__)
        return sync_session(argv[1])
    if argv[:1] == ["try"]:
        if len(argv) not in (3, 4):
            sys.exit(__doc__)
        return try_candidate(argv[1], argv[2], argv[3] if len(argv) == 4 else "")
    if argv[:1] == ["env"]:
        if len(argv) != 2:
            sys.exit(__doc__)
        print(env_lines(argv[1]))
        return 0
    if argv[:1] == ["release"]:
        if len(argv) != 2:
            sys.exit(__doc__)
        return release_session(holder_name(argv[1]))
    if argv[:1] == ["reclaim"]:
        parser = argparse.ArgumentParser(prog="setup.py reclaim")
        parser.add_argument("holder")
        parser.add_argument("function", nargs="?", default="")
        parser.add_argument("--random", action="store_true")
        parser.add_argument("--max-size", type=int, default=NEAREST_MAX_INSNS)
        args = parser.parse_args(argv[1:])
        return reclaim_session(holder_name(args.holder), args.function, "random" if args.random else "nearest",
                               args.max_size)
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
