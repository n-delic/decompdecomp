"""The local app: GUI API security, demo import, live feed, agent output parsing, the built-in API agent."""

import json
import logging
import os
import shutil
import sys
import threading
import time

import httpx
import pytest
from starlette.testclient import TestClient

from decompnet import agents as kinds
from decompnet import toolchains
from decompnet.agents import api
from decompnet.app import agents, window
from decompnet.app.engine import RomNetwork
from decompnet.app.server import build

# the tests that import the demo ROM (the rest, such as the GUI's security, run anywhere)
needs_demo = pytest.mark.skipif(toolchains.installed("gcc-2.7.2-kmc") is None or not shutil.which("ld.lld"),
                                reason="needs gcc-2.7.2-kmc")
PORT, SECRET = 6499, "s3cret-for-tests"


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    (home / ".decomp").mkdir()
    net = RomNetwork(home / ".decomp" / "decomp.db", home)
    mcp, manager, security, _ = build(net, home / ".decomp", PORT, SECRET)
    app = mcp.streamable_http_app(transport_security=security, host="127.0.0.1")
    client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", headers=WINDOW)
    return client, net, manager


WINDOW = {"User-Agent": f"Mozilla/5.0 AppleWebKit/605.1.15 {window.TOKEN_AGENT}{SECRET}"}


def test_on_windows_it_stops_updating_and_shows_the_way_back_to_1_0_2(tmp_path, monkeypatch):
    """1.0.2 on Windows (in WSL) can install this version by itself, and it doesn't run there. Going back to 1.0.2
    must stick: 1.0.2 reads the same settings, so its update checks are switched off, and its old tab says how."""
    from decompnet import hostenv

    kernel = tmp_path / "osrelease"
    kernel.write_text("6.6.87.2-microsoft-standard-WSL2\n")
    monkeypatch.setattr(hostenv, "KERNEL_RELEASE", kernel)
    monkeypatch.setenv("WSL_DISTRO_NAME", hostenv.WSL_DISTRO)  # the distribution 1.0.2's installer made
    home = tmp_path / "home"
    (home / ".decomp").mkdir(parents=True)
    (home / ".decomp" / "settings.json").write_text(json.dumps({"auto_update": True}))
    net = RomNetwork(home / ".decomp" / "decomp.db", home)
    mcp, _, security, _ = build(net, home / ".decomp", PORT, SECRET)
    saved = json.loads((home / ".decomp" / "settings.json").read_text())
    assert saved["auto_update"] is False and saved["check_updates"] is False
    anon = TestClient(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                      base_url=f"http://127.0.0.1:{PORT}")
    page = anon.get("/").text
    assert "doesn't run on Windows" in page and "v1.0.2-alpha.tar.gz" in page and "DECOMP_TRAY=0" in page
    kernel.write_text("6.12.0-generic\n")
    assert "doesn't run on Windows" not in anon.get("/").text


def test_in_a_wsl_distribution_of_your_own_it_keeps_updating(tmp_path, monkeypatch):
    """1.0.4 supports WSL 2 (the Linux install, in a distribution of the user's own), and every start there switched
    update checks off and its old tab said Windows wasn't supported: that's for 1.0.2's own distribution only."""
    from decompnet import hostenv

    kernel = tmp_path / "osrelease"
    kernel.write_text("6.6.87.2-microsoft-standard-WSL2\n")
    monkeypatch.setattr(hostenv, "KERNEL_RELEASE", kernel)
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    home = tmp_path / "home"
    (home / ".decomp").mkdir(parents=True)
    (home / ".decomp" / "settings.json").write_text(json.dumps({"auto_update": True}))
    build(RomNetwork(home / ".decomp" / "decomp.db", home), home / ".decomp", PORT, SECRET)
    assert json.loads((home / ".decomp" / "settings.json").read_text()) == {"auto_update": True}

def test_only_the_apps_own_window_gets_the_gui(app):
    """The page and its API answer only the app's window, whose user agent carries the per-launch secret: the
    address alone, pasted in a browser, opens nothing (the old ?k= link and its cookie are gone)."""
    client, _, _ = app
    anon = TestClient(client.app, base_url=f"http://127.0.0.1:{PORT}")
    assert anon.get("/api/state").status_code == 401
    assert anon.get("/").status_code == 401
    assert anon.get(f"/?k={SECRET}", follow_redirects=False).status_code == 401
    wrong = TestClient(client.app, base_url=f"http://127.0.0.1:{PORT}",
                       headers={"User-Agent": f"Mozilla/5.0 {window.TOKEN_AGENT}not-the-secret"})
    assert wrong.get("/api/state").status_code == 401
    assert client.get("/").status_code == 200
    evil = TestClient(client.app, base_url="http://evil.example:6499", headers=WINDOW)
    assert evil.get("/api/state").status_code == 403
    # writes need the custom header too (a cross-site form can't send it)
    assert client.post("/api/settings", json={"per_session": 3}).status_code == 403
    assert client.post("/api/settings", json={"per_session": 3}, headers={"X-Decomp": "1"}).status_code == 200
    # these tests script the agents themselves: no autopilot after the import
    assert client.post("/api/settings", json={"autopilot": False}, headers={"X-Decomp": "1"}).json()["autopilot"] is False


def test_reimport_runs_as_a_background_project_job(app, tmp_path, monkeypatch):
    client, net, manager = app
    from types import SimpleNamespace

    monkeypatch.setattr("decompnet.app.routes.projects.consoles.identify", lambda _: SimpleNamespace(id="n64"))
    root = tmp_path / "reimport-game"
    root.mkdir()
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)",
                   ("reimport-game", "Game", "mips", str(root),
                    json.dumps({"kind": "rom", "platform": "n64", "collab": {"open": True}})))
    game = tmp_path / "game.z64"
    game.write_bytes(b"own game")
    stopped = []
    monkeypatch.setattr(manager, "stop_project", lambda slug: stopped.append(slug))
    calls = []

    def reimport(slug, path, confirmed, name, log):
        calls.append((slug, path, confirmed, name))
        log("reimport finished")
        return {"slug": slug, "functions": 10, "matched": 4}

    monkeypatch.setattr(net, "reimport_rom", reimport)
    headers = {"X-Decomp": "1"}
    assert client.post("/api/reimport", json={"slug": "reimport-game", "path": str(game)},
                       headers=headers).status_code == 400
    response = client.post("/api/reimport", json={
        "slug": "reimport-game", "path": str(game), "confirmed": True,
    }, headers=headers)
    assert response.status_code == 200
    job_id = response.json()["job"]
    for _ in range(100):
        result = client.get(f"/api/job?id={job_id}").json()
        if result["state"] != "running":
            break
        time.sleep(0.01)
    assert result["state"] == "done"
    assert result["result"] == {"slug": "reimport-game", "functions": 10, "matched": 4}
    assert calls == [("reimport-game", game, True, "game.z64")]
    assert stopped == ["reimport-game"]
    net.rooms["reimport-game"] = object()
    blocked = client.post("/api/reimport", json={
        "slug": "reimport-game", "path": str(game), "confirmed": True,
    }, headers=headers)
    assert blocked.status_code == 400
    assert "leave the shared room" in blocked.json()["error"]
    net.rooms.pop("reimport-game")
    with net.store.tx() as db:
        db.execute("DELETE FROM projects WHERE slug='reimport-game'")


@needs_demo
def test_import_demo_and_follow_it_live(app):
    client, net, _ = app
    h = {"X-Decomp": "1"}
    assert client.post("/api/import?confirmed=0", content=b"x" * 10, headers=h).status_code == 400
    job = client.post("/api/import?demo=1", content=b"", headers=h).json()["job"]
    for _ in range(200):
        j = client.get(f"/api/job?id={job}").json()
        if j["state"] != "running":
            break
        time.sleep(0.1)
    assert j["state"] == "done", j
    slug = j["result"]["slug"]
    st = client.get("/api/state").json()
    assert any(a["id"] == "api" for a in st["agents"])
    listed = client.get("/api/projects").json()[0]
    assert listed["slug"] == slug and listed["functions"] >= 95 and listed["compiler"]["name"]
    full = client.get(f"/api/map?slug={slug}").json()
    assert len(full["rows"]) >= 95 and "name" in full["keys"]
    assert client.get(f"/api/map?slug={slug}&since={full['sig']}").json() == {"sig": full["sig"], "same": True}
    live = client.get(f"/api/live?slug={slug}").json()
    assert live["overview"]["functions"] >= 95 and live["events"][0]["kind"] == "import"
    detail = client.get(f"/api/function?slug={slug}&name=func_80000460").json()
    assert "glabel" in detail["asm"]


def test_the_home_page_waits_for_no_agent_and_no_projects_files(tmp_path, monkeypatch):
    """The first /api/state ran every agent's --version and `agy models` (3.7 s before the home page showed), and the
    project list built every project's whole overview every 5 s, a fork's files included (2.5-3.7 s with agents at
    work). The state says which agents are installed without running them; the list reads the database only."""
    import threading

    from .test_loading import tiny_game

    ran = []  # an agent's program run anywhere but the background search ("detect-agents", Manager.detected)
    quick_look = agents.detect
    monkeypatch.setattr(agents, "detect", lambda quick=False: (
        ran.append(threading.current_thread().name) if not quick and threading.current_thread().name != "detect-agents"
        else None) or quick_look(quick=True))
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    tiny_game(net)
    mcp, _, security, _ = build(net, tmp_path, PORT, SECRET)
    client = TestClient(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                        base_url=f"http://127.0.0.1:{PORT}", headers=WINDOW)
    monkeypatch.setattr(net, "overview", lambda slug: pytest.fail("built a whole overview for the list"))
    st = client.get("/api/state").json()
    assert {a["id"] for a in st["agents"]} == {a.id for a in kinds.AGENTS} and "projects" not in st
    assert ran == []
    (game,) = client.get("/api/projects").json()
    assert game["slug"] == "game" and game["functions"] == 4 and game["compiler"]["name"] and "broken" not in game


def test_helpers_started_to_polish_matched_code_get_that_task(tmp_path, monkeypatch):
    """The launcher's "Polish matched code" sends task=readable, and the start route kept only the model and the
    claims per session: the helpers got the decompile prompt instead. What else a page sends still stays out."""
    from .test_loading import tiny_game

    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    tiny_game(net)
    mcp, manager, security, _ = build(net, tmp_path, PORT, SECRET)
    started = []
    monkeypatch.setattr(manager, "start", lambda kind, slug, count=1, options=None: started.append(options) or ["1"])
    client = TestClient(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                        base_url=f"http://127.0.0.1:{PORT}", headers=WINDOW)
    r = client.post("/api/agents/start", headers={"X-Decomp": "1"}, json={
        "kind": "claude", "slug": "game", "options": {"model": "opus", "task": "readable", "base_url": "http://x"}})
    assert r.status_code == 200, r.text
    assert started == [{"model": "opus", "task": "readable", "per_session": agents.PER_SESSION}]


def test_remove_paused_removes_only_paused_helpers_on_the_project(app, monkeypatch):
    from types import SimpleNamespace

    client, _, manager = app
    monkeypatch.setattr(manager, "sessions", {
        "paused-here": SimpleNamespace(id="paused-here", slug="game", state="paused"),
        "working-here": SimpleNamespace(id="working-here", slug="game", state="working"),
        "paused-elsewhere": SimpleNamespace(id="paused-elsewhere", slug="other", state="paused"),
    })
    removed = []
    monkeypatch.setattr(manager, "remove", lambda sid: removed.append(sid))

    response = client.post("/api/agents/stop", json={"slug": "game", "remove_paused": True},
                           headers={"X-Decomp": "1"})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "removed": 1}
    assert removed == ["paused-here"]


def test_all_models_starts_the_selected_count_for_each_available_model(tmp_path, monkeypatch):
    """All Models multiplies the selected count by the detected model list, rather than the one-start cap."""
    from .test_loading import tiny_game

    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    tiny_game(net)
    mcp, manager, security, _ = build(net, tmp_path, PORT, SECRET)
    monkeypatch.setattr(manager, "detected", lambda: [{"id": "claude", "models": [
        {"id": "sonnet"}, {"id": "opus"}, {"id": "haiku"},
    ]}])
    started = []

    def start(kind, slug, count=1, options=None):
        started.append((kind, slug, count, options))
        return [f"{options['model']}-{i}" for i in range(count)]

    monkeypatch.setattr(manager, "start", start)
    client = TestClient(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                        base_url=f"http://127.0.0.1:{PORT}", headers=WINDOW)
    response = client.post("/api/agents/start", headers={"X-Decomp": "1"}, json={
        "kind": "claude", "slug": "game", "count": 2, "options": {"all_models": True},
    })

    assert response.status_code == 200, response.text
    assert response.json()["started"] == [f"{model}-{i}" for model in ("sonnet", "opus", "haiku") for i in range(2)]
    assert started == [
        ("claude", "game", 2, {"model": model, "per_session": agents.PER_SESSION})
        for model in ("sonnet", "opus", "haiku")
    ]


def test_settings_never_echo_the_api_key(app):
    client, _, _ = app
    out = client.post("/api/settings", json={"api": {"provider": "deepseek", "api_key": "sk-abcdef123456"}},
                      headers={"X-Decomp": "1"}).json()
    assert "sk-abcdef" not in json.dumps(out) and out["api"]["has_key"]
    assert "sk-abcdef" not in json.dumps(client.get("/api/state").json())


def test_windows_installer_route_uses_the_native_installer(app, monkeypatch):
    from types import SimpleNamespace

    from decompnet.app.routes import setup

    client, _, manager = app
    calls = []
    monkeypatch.setattr(kinds.sys, "platform", "win32")
    monkeypatch.setattr(manager, "refresh_detected", lambda **kwargs: None)
    proc = SimpleNamespace(stdout=["installed\n"], wait=lambda: 0, returncode=0)
    monkeypatch.setattr(setup.subprocess, "Popen", lambda argv, **kwargs: calls.append(argv) or proc)
    response = client.post("/api/agents/install", json={"kind": "claude"}, headers={"X-Decomp": "1"})
    assert response.status_code == 200
    for _ in range(200):
        job = client.get("/api/job", params={"id": response.json()["job"]}).json()
        if job["state"] != "running":
            break
        time.sleep(0.01)
    assert job["state"] == "done", job
    assert calls[0][0] == "powershell.exe" and "claude.ai/install.ps1" in calls[0][-1]


def test_the_notice_of_the_removed_bridge_stays_dismissed(app):
    """The home page says once which old `decomp mcp` entries the first start removed; Dismiss is remembered."""
    client, _, _ = app
    assert client.get("/api/state").json()["settings"]["bridges_removed_seen"] is False
    out = client.post("/api/settings", json={"bridges_removed_seen": True}, headers={"X-Decomp": "1"}).json()
    assert out["bridges_removed_seen"] is True
    assert client.get("/api/state").json()["settings"]["bridges_removed_seen"] is True


def test_parse_agent_output():
    claude = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Matched it."},
        {"type": "tool_use", "name": "mcp__decomp__compile_and_diff", "input": {"function": "f", "source": "a\nb"}}]}})
    assert kinds.get("claude").parse_line(claude) == [("text", "Matched it."), ("tool", "compile_and_diff(f, 2 lines of C)")]
    codex = json.dumps({"type": "item.started", "item": {"type": "mcp_tool_call", "server": "decomp", "tool": "claim_function",
                                                        "arguments": {"strategy": "easiest"}}})
    assert kinds.get("codex").parse_line(codex) == [("tool", "claim_function(easiest)")]
    opencode = json.dumps({"type": "text", "part": {"text": "done"}})
    assert kinds.get("opencode").parse_line(opencode) == [("text", "done")]
    assert kinds.get("claude").parse_line("not json at all") == [("info", "not json at all")]


@needs_demo
def test_builtin_api_agent_matches_a_function(app, monkeypatch):
    """A scripted OpenAI-compatible model: claim, compile the right C, stop. The tool loop must do the rest."""
    client, net, manager = app
    slug = net.store.one("SELECT slug FROM projects")["slug"]
    script = iter([
        {"tool_calls": [{"id": "1", "type": "function", "function": {"name": "claim_function", "arguments": json.dumps({"function": "func_800007F4"})}}]},
        {"tool_calls": [{"id": "2", "type": "function", "function": {"name": "compile_and_diff", "arguments": json.dumps(
            {"function": "func_800007F4", "source": "s32 func_800007F4(s32 v) { return v < 0 ? -v : v; }"})}}]},
        {"content": "Matched."},
    ])
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        assert request.headers["authorization"] == "Bearer test-key" and body["tools"]
        msg = {"role": "assistant", "content": None} | next(script)
        return httpx.Response(200, json={"choices": [{"message": msg}]})

    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    s = agents.Session("api-t", "api", "Test", slug, "api-test", {"base_url": "http://mock/v1", "model": "m", "api_key": "test-key"})
    agent, _ = net.local_agent("api-test")
    assert api.API.run(manager, s, agent) is True
    assert net._function("func_800007F4", slug)["status"] == "matched"
    assert "byte-identical" in seen[-1]["messages"][-1]["content"]


def test_an_agent_nobody_signed_in_to_stops_and_says_so(tmp_path, monkeypatch):
    """Claude Code without a sign-in exits at once: say that (not "exited with code 1") and don't keep retrying."""
    manager = agents.Manager(RomNetwork(tmp_path / "app.db", tmp_path), tmp_path, "http://127.0.0.1:9/mcp")
    code = "print('Not logged in \\u00b7 Please run /login'); raise SystemExit(1)"
    monkeypatch.setattr(manager, "_command", lambda s, token, workdir: ([sys.executable, "-c", code], dict(os.environ), ""))
    s = agents.Session("claude-1", "claude", "Claude Code #1", "game", "claude-1", {})
    assert manager._run_cli(s, "token", tmp_path) is False
    assert s.signed_out and "isn't signed in" in s.error and "/login" in s.error


def test_an_agent_out_of_credit_stops_and_says_so(tmp_path, monkeypatch):
    """DeepSeek's "Insufficient Balance" (here through opencode): stop at once and say what to do. "Too many
    requests" is the provider's limit of requests at once: not a failure, the session waits and tries again."""
    manager = agents.Manager(RomNetwork(tmp_path / "app.db", tmp_path), tmp_path, "http://127.0.0.1:9/mcp")
    s = agents.Session("opencode-1", "opencode", "opencode #1", "game", "opencode-1", {})

    def says(text: str) -> None:
        code = f"print({text!r}); raise SystemExit(1)"
        monkeypatch.setattr(manager, "_command", lambda s, token, workdir: ([sys.executable, "-c", code], dict(os.environ), ""))

    says("AI_APICallError: Insufficient Balance (request_id: 3e759c3b)")
    with pytest.raises(agents.Fatal, match="out of credit .*Insufficient Balance.*top it up"):
        manager._run_cli(s, "token", tmp_path)
    says("AI_APICallError: Too many requests. Your current concurrency is 5, which exceeds your concurrency limit of 5")
    assert manager._run_cli(s, "token", tmp_path) is False and s.throttled


@needs_demo
def test_builtin_api_agent_out_of_credit(app, monkeypatch):
    client, net, manager = app
    slug = net.store.one("SELECT slug FROM projects")["slug"]
    answers = iter([httpx.Response(429, json={"error": {"message": "Rate limit reached for requests"}}),
                    httpx.Response(402, json={"error": {"message": "Insufficient Balance"}})])
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(lambda r: next(answers)), **kw))
    s = agents.Session("api-c", "api", "Test", slug, "api-credit", {"base_url": "http://mock/v1", "model": "m",
                                                                    "context_tokens": 32768})
    agent, _ = net.local_agent("api-credit")
    assert api.API.run(manager, s, agent) is False and s.throttled
    with pytest.raises(agents.Fatal, match="out of credit"):
        api.API.run(manager, s, agent)


def test_agent_spend_is_read_from_their_output():
    """What each session spent, its tokens (those served from the provider's cache apart) and turns, as each agent
    reports them: recorded per session and model, to compare models on cost per match."""
    def usage(kind, obj):
        return [json.loads(text) for k, text in kinds.get(kind).parse_line(json.dumps(obj)) if k == "usage"]
    claude = {"type": "result", "total_cost_usd": 0.42, "num_turns": 9, "usage": {
        "input_tokens": 30, "output_tokens": 900, "cache_read_input_tokens": 50000, "cache_creation_input_tokens": 4000}}
    assert usage("claude", claude) == [{"usd": 0.42, "input": 30, "output": 900, "cache_read": 50000,
                                        "cache_write": 4000, "turns": 9}]
    codex = {"type": "turn.completed", "usage": {"input_tokens": 900, "cached_input_tokens": 600, "output_tokens": 100}}
    assert usage("codex", codex) == [{"input": 300, "cache_read": 600, "output": 100, "turns": 1}]
    opencode = {"type": "step_finish", "part": {"cost": 0.01, "tokens": {"input": 10, "output": 5, "reasoning": 7,
                                                                         "cache": {"read": 400, "write": 0}}}}
    assert usage("opencode", opencode) == [{"usd": 0.01, "input": 10, "output": 12, "cache_read": 400, "turns": 1}]
    assert api.api_usage({"prompt_tokens": 1000, "completion_tokens": 50, "prompt_cache_hit_tokens": 700}) == \
        {"input": 300, "cache_read": 700, "output": 50, "turns": 1}


def test_an_older_app_still_running_after_an_upgrade_is_replaced(tmp_path):
    """After an upgrade the old version may still be running: `decomp` stops it and starts the new one, instead of
    talking to an app that doesn't know the new endpoints."""
    import http.server
    import threading

    from decompnet.app.cli import replace_older

    quit_called = threading.Event()

    class Old(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"ok": True, "app": "decomp-wtf", "version": "0.4.2"}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            quit_called.set()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")
            threading.Thread(target=server.shutdown, daemon=True).start()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Old)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state = tmp_path / "server.json"
    state.write_text(json.dumps({"port": server.server_address[1], "secret": "s"}))
    assert replace_older(state, log=lambda *_: None) is None and quit_called.is_set()
    server.server_close()


def test_an_older_copy_never_stops_a_newer_app(tmp_path, monkeypatch):
    """`decomp` from an older install brings the newer running app's window up instead of replacing it."""
    from decompnet.app import cli

    monkeypatch.setattr(cli, "_running_version", lambda port: "99.0.0")
    state = tmp_path / "server.json"
    state.write_text(json.dumps({"port": 1234, "secret": "s"}))
    assert cli.replace_older(state, log=lambda *_: None) == {"port": 1234, "secret": "s"}
    assert cli._version_key("0.10.0") > cli._version_key("0.9.4")


def test_a_job_always_ends_and_says_why(tmp_path):
    """Whatever goes wrong in an import, the job ends with words the user can act on and the log has the whole
    story: before, an error of a kind nobody caught left the GUI on "Analysing…" forever."""
    import subprocess

    from decompnet.app.support import Jobs

    jobs = Jobs(tmp_path / "logs" / "decomp.log")

    def wait(jid):
        for _ in range(200):
            if jobs.jobs[jid]["state"] != "running":
                return jobs.jobs[jid]
            time.sleep(0.01)
        raise AssertionError("the job never ended")

    def bug(log):
        log("reading the ROM")
        return {}["missing"]

    def slow(log):
        raise subprocess.TimeoutExpired(["/usr/bin/systemd-run", "--user", "--", "/x/dtk", "dol", "split"], 1800)

    def failed(log):
        raise subprocess.CalledProcessError(2, ["ninja"], stderr=b"ld: undefined symbol foo")

    job = wait(jobs.start("import", bug))
    assert job["state"] == "error" and "KeyError" in job["error"] and "report" in job["error"]
    assert "Traceback" in job["trace"]
    assert "dtk took longer than 30 minutes" in wait(jobs.start("import", slow))["error"]
    assert wait(jobs.start("build", failed))["error"] == "ninja failed (2): ld: undefined symbol foo"
    logged = (tmp_path / "logs" / "decomp.log").read_text()
    assert "reading the ROM" in logged and "KeyError" in logged and "Traceback" in logged


def test_a_cancelled_job_stops_at_its_next_checkpoint(tmp_path):
    from decompnet.app.support import Jobs

    jobs = Jobs()
    at_step = threading.Event()
    finish_step = threading.Event()

    def work(log):
        at_step.set()
        finish_step.wait(2)
        log.check_cancel()

    jid = jobs.start("restore", work)
    assert at_step.wait(1)
    assert jobs.cancel(jid)
    finish_step.set()
    for _ in range(100):
        if jobs.jobs[jid]["state"] != "running":
            break
        time.sleep(0.01)
    assert jobs.jobs[jid]["state"] == "cancelled"


def test_cancellation_accepted_before_a_job_returns_cannot_report_success():
    from decompnet.app.support import Jobs

    jobs = Jobs()
    entered, finish = threading.Event(), threading.Event()

    def work(log):
        entered.set()
        assert finish.wait(2)
        return {"slug": "must-not-open"}

    jid = jobs.start("restore", work)
    assert entered.wait(1)
    assert jobs.cancel(jid)
    finish.set()
    for _ in range(100):
        if jobs.jobs[jid]["state"] != "running":
            break
        time.sleep(0.01)
    assert jobs.jobs[jid]["state"] == "cancelled"
    assert jobs.jobs[jid]["result"] is None


def test_the_log_can_be_read_from_the_gui(app):
    client, _, _ = app
    res = client.get("/api/log")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/plain")
    anon = TestClient(client.app, base_url=f"http://127.0.0.1:{PORT}")
    assert anon.get("/api/log").status_code == 401


def test_a_project_that_cant_be_read_is_listed_with_the_reason(app, monkeypatch):
    """One odd project used to empty the whole sidebar (or quietly vanish from it)."""
    client, net, _ = app
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                   ("odd-game", "Odd Game", "", "mips", 1, "/nowhere", "{}"))
    real = net.summary

    def summary(slug, *a, **k):
        if slug == "odd-game":
            raise TypeError("'NoneType' object is not subscriptable")
        return real(slug, *a, **k)

    monkeypatch.setattr(net, "summary", summary)
    listed = {p["slug"]: p for p in client.get("/api/projects").json()}
    assert "TypeError" in listed["odd-game"]["broken"] and listed["odd-game"]["title"] == "Odd Game"
    assert any("broken" not in p for p in listed.values()) or len(listed) == 1
    assert client.get("/api/state").status_code == 200
    with net.store.tx() as db:
        db.execute("DELETE FROM projects WHERE slug='odd-game'")


@needs_demo  # works on the demo project an earlier test imports
def test_the_api_agent_stays_within_a_small_local_context(app, monkeypatch):
    """A local llama.cpp model with a small context: the agent learns it from /props, trims older tool output to
    fit, and when the server still says the request is too long, trims harder and sends it again."""
    client, net, manager = app
    slug = net.store.one("SELECT slug FROM projects")["slug"]
    names = [r["name"] for r in net.store.all("SELECT name FROM functions WHERE project_id=(SELECT id FROM projects "
                                                 "WHERE slug=?) ORDER BY n_insns DESC LIMIT 6", slug)]
    script = iter([{"tool_calls": [{"id": str(k), "type": "function", "function": {
        "name": "get_function", "arguments": json.dumps({"function": n})}}]} for k, n in enumerate(names)]
        + [{"content": "done looking"}])
    sizes, refused = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/props":
            return httpx.Response(200, json={"default_generation_settings": {"n_ctx": 16384}})
        size = len(request.content)
        if size > 30000 and not refused:  # once: the server counts tokens its own way
            refused.append(size)
            return httpx.Response(400, json={"error": {"message": "the request exceeds the available context size"}})
        sizes.append(size)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None} | next(script)}]})

    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    s = agents.Session("api-c", "api", "Test", slug, "api-ctx", {"base_url": "http://127.0.0.1:8080/v1", "model": "m"})
    agent, _ = net.local_agent("api-ctx")
    assert api.API.run(manager, s, agent) is True
    assert len(sizes) == len(names) + 1 and max(sizes) < 16384 * 3  # all of it, each within the context
    assert refused  # and it got past a refusal


def test_fit_keeps_the_task_and_the_latest_exchange():
    msgs = [{"role": "system", "content": "rules " * 100}, {"role": "user", "content": "task " * 100}]
    for k in range(6):
        msgs += [{"role": "assistant", "content": f"thinking {k} " * 50},
                 {"role": "tool", "tool_call_id": str(k), "content": f"assembly {k} " * 400}]
    api.fit(msgs, 3000)
    assert msgs[0]["content"] == "rules " * 100 and msgs[1]["content"] == "task " * 100
    assert msgs[-1]["content"] == "assembly 5 " * 400  # the latest result, whole
    assert "trimmed" in msgs[3]["content"]


def test_settings_saved_from_many_threads_stay_whole(tmp_path):
    """Helpers' threads and the page save the settings at once while the app reads them: a file caught half-written
    came back as empty settings, and with them the helpers to resume."""
    import threading

    from decompnet.app.support import Settings

    st = Settings(tmp_path / "settings.json")
    st.save()
    broken = []

    def write(n):
        for k in range(150):
            st.data["running_agents"] = [{"kind": "opencode", "slug": "king-king", "options": {}}] * ((n * k) % 60 + 1)
            st.save()

    def read():
        for _ in range(600):
            try:
                json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
            except ValueError:
                broken.append(1)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(1, 5)] + [threading.Thread(target=read)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not broken


def test_a_page_error_lands_in_the_apps_log(app, caplog):
    client, _, _ = app
    with caplog.at_level(logging.WARNING, logger="decompdecomp.page"):
        r = client.post("/api/log", json={"message": "x is undefined", "where": "base.js:12:3", "stack": "at f"},
                        headers={"X-Decomp": "1"})
    assert r.status_code == 200 and "page: x is undefined (base.js:12:3)" in caplog.text


def test_decomp_stop_stops_a_running_1_0_2_and_says_so_only_when_it_has(tmp_path, monkeypatch):
    """A running 1.0.2 took the secret only in its cookie: the new `decomp stop` (and the installer, which runs it)
    said "stopped" while the old app kept serving the user's projects."""
    import http.server
    import threading

    from decompnet.app import cli

    class Old(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # /health
            body = json.dumps({"ok": True, "app": "decomp-wtf", "version": "1.0.2-alpha"}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # /api/quit: only with the old cookie
            ok = "decomp_live=s3cret" in (self.headers.get("Cookie") or "")
            self.send_response(200 if ok else 401)
            self.end_headers()
            self.wfile.write(b"{}")
            if ok:
                threading.Thread(target=server.shutdown, daemon=True).start()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Old)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    home = tmp_path / "home"
    (home / ".decomp").mkdir(parents=True)
    (home / ".decomp" / "server.json").write_text(json.dumps({"port": server.server_address[1], "secret": "s3cret",
                                                              "pid": 1}))
    monkeypatch.setenv("DECOMP_HOME", str(home))
    server_alive = cli.stop(None)
    server.server_close()
    assert server_alive == 0


def test_paths_are_picked_in_the_systems_own_chooser(app, tmp_path, monkeypatch):
    """"Open a saved project" asked for two typed paths: the window shows files' contents to the page, never where
    they are. The system's chooser (picker.py, run like the window by the system's python3) answers with the path."""
    import subprocess

    from decompnet.app import picker, window

    monkeypatch.setattr(window, "IS_WINDOWS", False)
    monkeypatch.setattr(window, "SYSTEM_PYTHON", str(tmp_path / "python3"))
    monkeypatch.setattr(picker.subprocess, "run", lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 0 if argv[3] == "folder" else 1,
                                                    stdout="/home/you/saves/mario\n", stderr=""))
    client, _, _ = app
    pick = lambda d: client.post("/api/pick", json=d, headers={"X-Decomp": "1"})  # noqa: E731
    assert pick({"kind": "folder", "title": "The save"}).json() == {"path": "/home/you/saves/mario"}
    assert pick({"kind": "file"}).json() == {"path": ""}
    assert pick({"kind": "/etc/passwd"}).status_code == 400


def test_windows_path_picker_uses_the_apps_python(app, monkeypatch):
    """Windows has no /usr/bin/python3: run the native chooser with the app's own Python."""
    import subprocess
    import sys

    from decompnet.app import picker, window

    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="C:\\Users\\you\\save\n", stderr="")

    monkeypatch.setattr(window, "IS_WINDOWS", True)
    monkeypatch.setattr(picker, "subprocess", type("Subprocess", (), {
        "run": staticmethod(run), "TimeoutExpired": subprocess.TimeoutExpired,
        "CREATE_NO_WINDOW": 0x08000000,
    }))
    client, _, _ = app
    response = client.post("/api/pick", json={"kind": "folder"}, headers={"X-Decomp": "1"})

    assert response.json() == {"path": "C:\\Users\\you\\save"}
    assert calls[0][0][0] == sys.executable
    assert calls[0][1]["creationflags"] == 0x08000000


def test_the_map_brings_polished_and_set_aside_and_changes_when_a_function_is_polished(tmp_path):
    """The map's route sent a fixed list of fields: polished and not_c never reached the page, so the Functions tab's
    Polished filter stayed empty while agents polished. Its signature left polishing out too, so no refetch."""
    from .test_loading import tiny_game

    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    tiny_game(net)
    mcp, _, security, _ = build(net, tmp_path, PORT, SECRET)
    client = TestClient(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                        base_url=f"http://127.0.0.1:{PORT}", headers=WINDOW)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched' WHERE name='func_0'")
    before = client.get("/api/map?slug=game").json()
    with net.store.tx() as db:
        db.execute("UPDATE functions SET readable=1 WHERE name='func_0'")
    after = client.get(f"/api/map?slug=game&since={before['sig']}").json()
    assert {"polished", "not_c"} <= set(after["keys"]) and "same" not in after
    rows = {r[after["keys"].index("name")]: dict(zip(after["keys"], r)) for r in after["rows"]}
    assert rows["func_0"]["polished"] and not rows["func_1"]["polished"]
