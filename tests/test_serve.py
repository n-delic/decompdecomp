"""`decomp serve`: the app with no window on a machine with no desktop, and the browser sessions that reach its GUI
(sessions.py). The auth matrix runs in-process against build(); starting for real, with no DISPLAY, runs the actual
`decomp serve` as a subprocess, the way a server would."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from decompnet.app import sessions as sessions_module
from decompnet.app import window
from decompnet.app.engine import RomNetwork
from decompnet.app.server import build
from decompnet.app.sessions import COOKIE, Sessions, tunnel_port

PORT, SECRET = 6499, "s3cret-for-tests"
JAR = f"{COOKIE}_{PORT}"  # the session cookie of a browser that reaches the app on its own port


@pytest.fixture(scope="module")
def serving(tmp_path_factory):
    """The app as `decomp serve` builds it: with browser sessions, and no window of its own."""
    home = tmp_path_factory.mktemp("home")
    (home / ".decomp").mkdir()
    net = RomNetwork(home / ".decomp" / "decomp.db", home)
    sessions = Sessions()
    mcp, _, security, _ = build(net, home / ".decomp", PORT, SECRET, pair=sessions)
    app = mcp.streamable_http_app(transport_security=security, host="127.0.0.1")
    return TestClient(app, base_url=f"http://127.0.0.1:{PORT}"), sessions  # a browser: no User-Agent secret


def pair(browser: TestClient, sessions: Sessions) -> str:
    """Show a code in the server's terminal, open its link: the browser ends up with a session cookie, on the GUI."""
    code = sessions.new_code()
    redirected = browser.get(f"/pair?code={code}", follow_redirects=False)
    assert redirected.status_code == 303 and redirected.headers["location"] == "/"
    set_cookie = redirected.headers["set-cookie"].lower()
    assert JAR.lower() in set_cookie and "httponly" in set_cookie and "samesite=strict" in set_cookie
    assert "max-age" not in set_cookie and "expires" not in set_cookie  # the browser's session only: none on disk
    assert browser.get("/").status_code == 200  # the redirect's target, with the cookie the browser now carries
    return code


def test_a_code_works_once_and_a_used_code_is_refused(serving):
    browser, sessions = serving
    assert browser.get("/pair").status_code == 403  # no code at all
    assert browser.get("/pair?code=not-a-code").status_code == 403
    code = pair(browser, sessions)
    assert browser.get(f"/pair?code={code}").status_code == 403  # spent: one code, one browser


def test_a_sessions_reads_work_and_writes_still_need_the_header(serving):
    browser, sessions = serving
    pair(browser, sessions)
    assert browser.get("/api/state").status_code == 200
    assert browser.post("/api/settings", json={"per_session": 3}).status_code == 403  # a page from another site
    assert browser.post("/api/settings", json={"per_session": 3}, headers={"X-Decomp": "1"}).status_code == 200
    evil = TestClient(browser.app, base_url=f"http://evil.example:{PORT}")
    evil.cookies.set(JAR, browser.cookies.get(JAR))  # even with a real session's cookie
    assert evil.get("/api/state").status_code == 403  # the host check doesn't lean on the secret alone


def test_the_app_with_a_window_refuses_a_session_cookie(serving, tmp_path):
    """Built without sessions — the app with its own window — a paired browser's cookie opens nothing, and /pair
    says so: only `decomp serve` accepts browsers."""
    home = tmp_path / "home"
    (home / ".decomp").mkdir(parents=True)
    net = RomNetwork(home / ".decomp" / "decomp.db", home)
    mcp, _, security, _ = build(net, home / ".decomp", PORT, SECRET)
    windowed = TestClient(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                          base_url=f"http://127.0.0.1:{PORT}")
    browser, sessions = serving  # a real pairing, from the serving app above
    code = sessions.new_code()
    token = sessions.spend(code)
    windowed.cookies.set(JAR, token)
    assert windowed.get("/").status_code == 401
    assert windowed.get("/api/state").status_code == 401
    assert windowed.get(f"/pair?code={code}").status_code == 404
    own = {"User-Agent": f"{window.TOKEN_AGENT}{SECRET}", "X-Decomp": "1"}  # `decomp serve` again, beside it
    assert windowed.post("/api/pair", headers=own).status_code == 404


class Serve:
    """A real `decomp serve` in a subprocess of its own home, with no DISPLAY and no WAYLAND_DISPLAY, printing what
    it prints; the way it runs on a server, read here like a user would."""

    def __init__(self, home: Path, restart: dict | None = None):
        home.mkdir(parents=True, exist_ok=True)
        free = socket.socket()
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
        free.close()
        env = dict(os.environ, DECOMP_HOME=str(home), DECOMP_PORT=str(port), DECOMP_UPDATES="off",
                   DECOMP_DETECT_AGENTS="off", DECOMP_TRAY="0")
        env.pop("DECOMP_WINDOW", None)  # (the tests' "no window" would skip the desktop check serve must skip itself)
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)
        argv = [sys.executable, "-m", "decompnet.app.cli", "serve"]
        if restart is not None:  # as updates.restart starts the new version: that port exactly, that secret
            restart = dict(restart, port=port)
            env["DECOMP_RESTART"] = json.dumps(restart)
            argv[-1:] = ["--port", str(port), "serve"]
        self.asked = port
        self.lines: list[str] = []
        self.proc = subprocess.Popen(argv, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        threading.Thread(target=self._read, daemon=True).start()
        for _ in range(300):  # it answers /health once the server runs (the port it took is in server.json)
            try:
                info = json.loads((home / ".decomp" / "server.json").read_text(encoding="utf-8"))
                with httpx.Client(base_url=f"http://127.0.0.1:{info['port']}") as asking:
                    if asking.get("/health").json().get("app") == "decomp-wtf":
                        self.port, self.secret = int(info["port"]), str(info["secret"])
                        return
            except (OSError, ValueError, KeyError, httpx.HTTPError):  # not up yet, or its old file's port
                pass
            time.sleep(0.1)
        self.stop()
        raise AssertionError("decomp serve didn't start:\n" + "\n".join(self.lines))

    def _read(self) -> None:
        for line in self.proc.stdout:
            self.lines.append(line.rstrip())

    def link(self) -> str:
        """The one-time link it printed (b in the TUI shows a fresh one; here the first is enough)."""
        for _ in range(100):
            for line in self.lines:
                if "/pair?code=" in line:
                    return line.split()[0]
            time.sleep(0.1)
        raise AssertionError("decomp serve printed no link:\n" + "\n".join(self.lines))

    def stop(self) -> list[str]:
        try:
            self.proc.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            pass
        else:
            return self.lines
        ask = httpx.Client(base_url=f"http://127.0.0.1:{self.port}")
        try:  # it may have ended on its own between the check above and this ask
            ask.post("/api/quit", json={}, headers={"User-Agent": f"{window.TOKEN_AGENT}{self.secret}", "X-Decomp": "1"})
        except httpx.HTTPError:
            pass
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
        return self.lines


def test_serve_starts_with_no_display_and_pairs_a_browser(tmp_path):
    serve = Serve(tmp_path / "home")
    try:
        link = serve.link()
        assert link.startswith(f"http://127.0.0.1:{serve.port}/pair?code=")
        browser = httpx.Client(base_url=f"http://127.0.0.1:{serve.port}", follow_redirects=True)
        paired = browser.get(link)  # the code is traded for a cookie, and the browser lands on the GUI
        assert paired.status_code == 200 and "<html" in paired.text.lower()
        assert browser.get("/api/projects").status_code == 200  # the session reads like the window does
        again = httpx.Client(base_url=f"http://127.0.0.1:{serve.port}")
        assert again.get(link).status_code == 403  # the code is spent: a second browser can't reuse it
        browser.close()
        again.close()
    finally:
        serve.stop()


def test_sessions_end_when_the_app_stops(tmp_path):
    """Sessions live in the app's memory: a stop ends them, and the next `decomp serve` pairs the browser again."""
    home = tmp_path / "home"
    first = Serve(home)
    browser = httpx.Client(base_url=f"http://127.0.0.1:{first.port}", follow_redirects=True)
    assert browser.get(first.link()).status_code == 200
    first.stop()
    second = Serve(home)
    try:
        old = httpx.Client(base_url=f"http://127.0.0.1:{second.port}", follow_redirects=True)
        old.cookies.set(f"{COOKIE}_{second.port}", browser.cookies.get(f"{COOKIE}_{first.port}", ""))
        assert old.get("/").status_code == 401  # the old app's session went with the old app
        assert old.get(second.link()).status_code == 200  # a fresh link pairs again
        old.close()
    finally:
        second.stop()
    browser.close()


def test_decomp_stop_ends_serve_as_quit_does(tmp_path):
    """`decomp stop` in another shell, the way to stop `decomp serve` from outside on every system (Windows has no
    `kill` a console program can catch), ends it as Quit does: it says so, and the app exits 0."""
    home = tmp_path / "home"
    serve = Serve(home)
    try:
        env = dict(os.environ, DECOMP_HOME=str(home), DECOMP_UPDATES="off", DECOMP_DETECT_AGENTS="off")
        stopped = subprocess.run([sys.executable, "-m", "decompnet.app.cli", "stop"], env=env, capture_output=True,
                                 text=True, timeout=60)
        assert stopped.returncode == 0 and "stopped" in stopped.stdout, stopped.stdout + stopped.stderr
        assert serve.proc.wait(timeout=15) == 0
    finally:
        serve.stop()


def test_a_tunnel_on_another_port_pairs_and_reads(serving):
    """The tunnel's end on the user's machine may have another port (their own decompdecomp holds the app's): the
    link and the session work there, each port with its own cookie; another name than 127.0.0.1 or localhost doesn't."""
    browser, sessions = serving
    tunnel = TestClient(browser.app, base_url="http://localhost:7777")
    assert tunnel.get("/api/state").status_code == 403  # not paired: the host check as always
    redirected = tunnel.get(f"/pair?code={sessions.new_code()}", follow_redirects=False)
    assert redirected.status_code == 303 and f"{COOKIE}_7777" in redirected.headers["set-cookie"]
    assert tunnel.get("/").status_code == 200 and tunnel.get("/api/state").status_code == 200
    assert tunnel.get("/assets/app.css").status_code == 200  # the page's own files come through the tunnel too
    other = TestClient(browser.app, base_url="http://127.0.0.1:7778")
    other.cookies.set(f"{COOKIE}_7778", tunnel.cookies.get(f"{COOKIE}_7777"))
    assert other.get("/api/state").status_code == 200  # the same session, through a second tunnel
    on_80 = TestClient(browser.app, base_url="http://localhost")  # (a Windows user needs no admin rights for it)
    redirected = on_80.get(f"/pair?code={sessions.new_code()}", follow_redirects=False)
    assert redirected.status_code == 303 and f"{COOKIE}_80=" in redirected.headers["set-cookie"]
    assert on_80.get("/").status_code == 200 and on_80.get("/api/state").status_code == 200
    rebinding = TestClient(browser.app, base_url="http://attacker.example:7777")
    assert rebinding.get(f"/pair?code={sessions.new_code()}").status_code == 403


@pytest.mark.parametrize("host, port", [
    ("127.0.0.1:7777", "7777"), ("localhost:7777", "7777"), ("localhost:1", "1"),
    ("127.0.0.1.evil.example:80", None), ("localhost.evil.example:7777", None),
    ("localhost:7777.evil.example", None),  # (a prefix match, re.match for fullmatch, would take this one)
    ("evil.localhost:7777", None), ("xlocalhost:7777", None), ("LOCALHOST:7777", None),  # browsers send lowercase
    ("localhost", "80"), ("127.0.0.1", "80"),  # a tunnel on port 80: the browser leaves http's own port out
    ("localhost:", None), ("[::1]:7777", None), ("localhost:7777 ", None),
    ("localhost:123456", None), ("", None), (None, None)])
def test_only_this_machines_own_names_are_a_tunnel(host, port):
    """The Host a paired browser may use: 127.0.0.1 or localhost, with a port, and nothing else (sessions.TUNNEL)."""
    assert tunnel_port(host) == port


@pytest.mark.parametrize("host", ["localhost:7777.evil.example", "127.0.0.1.evil.example:80", "LOCALHOST:7777"])
def test_a_name_that_only_looks_like_a_tunnel_pairs_nothing(serving, host):
    browser, sessions = serving
    assert browser.get(f"/pair?code={sessions.new_code()}", headers={"host": host}).status_code == 403


def test_a_cookie_or_code_that_isnt_ascii_is_refused_not_an_error(serving):
    browser, sessions = serving
    stranger = TestClient(browser.app, base_url=f"http://127.0.0.1:{PORT}")
    assert stranger.get("/pair", params={"code": "é"}).status_code == 403
    stranger.cookies.set(JAR, "%C3%A9")
    assert stranger.get("/api/state").status_code == 401


def test_a_code_left_unused_too_long_is_refused(monkeypatch):
    sessions = Sessions()
    now = [1000.0]
    monkeypatch.setattr(sessions_module.time, "monotonic", lambda: now[0])
    old, fresh = sessions.new_code(), None
    now[0] += sessions_module.CODE_SECONDS + 1
    fresh = sessions.new_code()
    with pytest.raises(ValueError):
        sessions.spend(old)
    assert sessions.valid(sessions.spend(fresh))


def test_only_the_newest_codes_wait(monkeypatch):
    """`b` pressed again and again: the WAITING_CODES newest codes work, an older one gave way to them."""
    sessions = Sessions()
    codes = [sessions.new_code() for _ in range(sessions_module.WAITING_CODES + 1)]
    with pytest.raises(ValueError):
        sessions.spend(codes[0])
    assert all(sessions.valid(sessions.spend(code)) for code in codes[1:])


def test_an_update_restarts_serve_as_serve(monkeypatch):
    """An update replaces the process with the new version, on the same port with the same secret: `decomp serve`
    comes back as `decomp serve`, the app with a window as itself."""
    from decompnet.app import updates

    class Replaced(Exception):
        pass

    ran = []

    def execve(path, argv, env):
        ran.append((argv, json.loads(env["DECOMP_RESTART"])))
        raise Replaced  # (execve never returns)

    monkeypatch.setattr(updates.os, "execve", execve)
    for serve in (True, False):
        with pytest.raises(Replaced):
            updates.restart(6464, SECRET, False, serve=serve)
    (served, said), (windowed, _) = ran
    assert served[-3:] == ["--port", "6464", "serve"] and said == {"port": 6464, "secret": SECRET, "window": False}
    assert windowed[-2:] == ["--port", "6464"]


def test_serve_restarted_by_an_update_comes_back_on_its_port_with_its_secret(tmp_path):
    """The other end of the test above: the new version, started as updates.restart starts it, listens on the old
    port exactly and keeps the old secret (decomp's own commands and the agents' configs go on working), and is
    `decomp serve` again: it pairs browsers, and says it was updated."""
    serve = Serve(tmp_path / "home", restart={"secret": "the-old-secret", "window": False})
    try:
        assert (serve.port, serve.secret) == (serve.asked, "the-old-secret")
        own = {"User-Agent": f"{window.TOKEN_AGENT}the-old-secret", "X-Decomp": "1"}
        with httpx.Client(base_url=f"http://127.0.0.1:{serve.port}") as ask:
            assert ask.post("/api/pair", headers=own).status_code == 200  # sessions: serve, not the windowed app
        link = serve.link()
        for _ in range(50):
            if any("updated and restarted" in line for line in serve.lines):
                break
            time.sleep(0.1)
        assert any("updated and restarted" in line for line in serve.lines), "\n".join(serve.lines)
        assert link.startswith(f"http://127.0.0.1:{serve.port}/pair?code=")
    finally:
        serve.stop()


def test_serve_builds_an_updater_that_restarts_as_serve(monkeypatch, tmp_path):
    """build(..., pair=...) — `decomp serve` — hands its updater `serve`, so an update's restart (the test above)
    comes back headless; the app with a window doesn't."""
    from decompnet.app import updates

    told, real = [], updates.Updater
    monkeypatch.setattr(updates, "Updater", lambda *args, **kw: told.append(kw.get("serve")) or real(*args, **kw))
    for name, pair in (("served", Sessions()), ("windowed", None)):
        home = tmp_path / name
        (home / ".decomp").mkdir(parents=True)
        build(RomNetwork(home / ".decomp" / "decomp.db", home), home / ".decomp", PORT, SECRET, pair=pair)
    assert told == [True, False]


def test_only_decomps_own_commands_get_a_fresh_code(serving):
    browser, sessions = serving
    pair(browser, sessions)
    assert browser.post("/api/pair", headers={"X-Decomp": "1"}).status_code == 401  # a paired browser: no
    own = {"User-Agent": f"{window.TOKEN_AGENT}{SECRET}", "X-Decomp": "1"}
    # the secret without the header: what a page from another site could make a browser send, if it had the secret
    assert browser.post("/api/pair", headers={"User-Agent": own["User-Agent"]}).status_code == 401
    code = browser.post("/api/pair", headers=own).json()["code"]
    assert browser.get(f"/pair?code={code}", follow_redirects=False).status_code == 303


def test_serve_again_shows_a_fresh_link_to_the_running_one(tmp_path):
    """Where the TUI's `b` isn't there (no terminal), or the first link went unused: `decomp serve` again prints a
    fresh link to the app already running, and starts nothing."""
    home = tmp_path / "home"
    serve = Serve(home)
    try:
        env = dict(os.environ, DECOMP_HOME=str(home), DECOMP_UPDATES="off", DECOMP_DETECT_AGENTS="off")
        again = subprocess.run([sys.executable, "-m", "decompnet.app.cli", "serve"], env=env, capture_output=True,
                               text=True, timeout=60)
        assert "already running" in again.stdout and f"127.0.0.1:{serve.port}/pair?code=" in again.stdout
        link = again.stdout.split()[-1]
        browser = httpx.Client(base_url=f"http://127.0.0.1:{serve.port}", follow_redirects=True)
        assert browser.get(link).status_code == 200
        browser.close()
    finally:
        serve.stop()


def test_a_browser_that_hasnt_paired_is_told_how(serving):
    """Under `decomp serve`, the page says how to pair, not the windowed app's "it opens in a window of its own"."""
    browser, _ = serving
    stranger = TestClient(browser.app, base_url=f"http://127.0.0.1:{PORT}")
    page = stranger.get("/")
    assert page.status_code == 401 and "one-time link" in page.text and "window of its own" not in page.text
    assert "run <code>decomp serve</code> again" in page.text  # (with no terminal there's no TUI to ask)
