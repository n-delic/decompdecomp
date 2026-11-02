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


def test_agent_local_pass_settings_are_opt_in_and_validate_budgets(app):
    client,net,*_=app
    assert not net.agent_local_repairs['enabled']
    headers={'X-Decomp':'1'}
    for bad in ({'agent_local_trials':True},{'agent_local_trials':7},{'agent_local_seconds':16},{'agent_local_enabled':'yes'}):
        assert client.post('/api/settings',json=bad,headers=headers).status_code==400
    response=client.post('/api/settings',json={'agent_local_enabled':True,'agent_local_trials':3,'agent_local_seconds':4},headers=headers)
    assert response.status_code==200 and net.agent_local_repairs=={'enabled':True,'trials':3,'seconds':4}
    client.post('/api/settings',json={'agent_local_enabled':False},headers=headers)


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

def test_an_outside_agent_pairs_through_the_window(app):
    """An agent outside the app asks with its name and a secret of its own; the user decides in the window, for one
    project; the agent reads the token with its secret and its MCP tools are that project's alone."""
    client, net, _ = app

    with TestClient(client.app, base_url=f"http://127.0.0.1:{PORT}") as anon:
        r = anon.post("/api/pair/request", json={"name": "outside-cli", "secret": "its-own-long-secret"})
        assert r.status_code == 200 and r.json()["id"]
        assert anon.post("/api/pair/request", json={"name": "x", "secret": "short"}).status_code == 400

        assert anon.get("/api/pair/requests").status_code == 401  # the window's secret, like every GUI route
        pending = client.get("/api/pair/requests").json()["requests"]
        assert [r["name"] for r in pending] == ["outside-cli"]

        with net.store.tx() as db:
            db.execute("INSERT INTO projects(slug,root,config) VALUES('pair-demo','/tmp/pair-demo','{}')")
        assert anon.post("/api/pair/status", json={"id": pending[0]["id"], "secret": "its-own-long-secret"}).json() == {"state": "pending"}
        write = {"x-decomp": "1"}  # every GUI write carries it
        assert client.post("/api/pair/decide", headers=write, json={"id": pending[0]["id"], "approve": True,
                                                                    "project": "nope"}).status_code == 400
        assert client.post("/api/pair/decide", headers=write, json={"id": pending[0]["id"], "approve": True,
                                                                    "project": "pair-demo"}).status_code == 200
        answer = anon.post("/api/pair/status", json={"id": pending[0]["id"], "secret": "its-own-long-secret"}).json()
        assert answer["state"] == "approved" and answer["token"].startswith("dn_")
        assert anon.post("/api/pair/status", json={"id": pending[0]["id"], "secret": "the-wrong-secret"}).status_code == 400

        from decompnet.app import prompt_settings
        prompt_settings.save_global(net, {'instructions':'Custom app instructions for this test'})
        # its MCP tools are the paired project's: initialize, then tools/list with the token
        headers = {"Authorization": f"Bearer {answer['token']}"}
        r = anon.post("/mcp", headers={**headers, "Accept": "application/json, text/event-stream"},
                      json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                       "clientInfo": {"name": "outside-cli", "version": "0"}}})
        initialized = json.loads(r.text.split('data: ',1)[1].splitlines()[0])['result']
        assert initialized['instructions']=='Custom app instructions for this test'
        from decompnet.app.agents import Session
        manager = app[2]
        paired_agent = net.authenticate(answer['token'])
        custom = Session('prompt-mcp','codex',paired_agent.name,'pair-demo',paired_agent.name,
                         {'prompts':{'instructions':'Private per-agent app instructions'}})
        manager.sessions[custom.id] = custom
        per_agent = anon.post('/mcp', headers={**headers,'Accept':'application/json, text/event-stream'},
                             json={'jsonrpc':'2.0','id':10,'method':'initialize',
                                   'params':{'protocolVersion':'2025-03-26','capabilities':{},
                                             'clientInfo':{'name':'other-initialize','version':'0'}}})
        assert json.loads(per_agent.text.split('data: ',1)[1].splitlines()[0])['result']['instructions']=='Private per-agent app instructions'
        assert initialized['instructions']=='Custom app instructions for this test'
        manager.sessions.pop(custom.id)

        prompt_settings.save_global(net,{})
        sid = r.headers["mcp-session-id"]
        r = anon.post("/mcp", headers={**headers, "Accept": "application/json, text/event-stream",
                                       "Mcp-Session-Id": sid, "mcp-session-id": sid},
                      json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        r = anon.post("/mcp", headers={**headers, "Accept": "application/json, text/event-stream",
                                       "Mcp-Session-Id": sid, "mcp-session-id": sid},
                      json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = json.loads(r.text.split("data: ", 1)[1].splitlines()[0])["result"]["tools"]
        assert tools and "claim_function" in {t["name"] for t in tools}
        paired = net.authenticate(answer['token'])
        net.set_selection('pair-demo', paired.name, {'disabled_tools':['guide']})
        r = anon.post('/mcp', headers={**headers, 'Accept':'application/json, text/event-stream', 'Mcp-Session-Id':sid},
                      json={'jsonrpc':'2.0','id':3,'method':'tools/list'})
        filtered = json.loads(r.text.split('data: ',1)[1].splitlines()[0])['result']['tools']
        assert 'guide' not in {t['name'] for t in filtered}
        assert 'claim_function' in {t['name'] for t in filtered}



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


def test_selected_models_start_only_the_chosen_models_within_the_helper_limit(tmp_path, monkeypatch):
    """Starting multiple models uses only the explicit selection, with `count` sessions for each model."""
    from .test_loading import tiny_game

    (tmp_path / "settings.json").write_text(json.dumps({
        "launcher": {"kind": "claude", "model": "opus", "count": 1},
    }))
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
    many_models = client.post("/api/agents/start", headers={"X-Decomp": "1"}, json={
        "kind": "claude", "slug": "game", "count": 4,
        "options": {"models": ["sonnet", "opus", "haiku"], "effort": "high"},
    })
    assert many_models.status_code == 200, many_models.text
    assert many_models.json()["started"] == [f"{model}#high-{i}" for model in ("sonnet", "opus", "haiku") for i in range(4)]
    assert started == [
        ("claude", "game", 4, {"model": f"{model}#high", "effort": "high", "per_session": agents.PER_SESSION})
        for model in ("sonnet", "opus", "haiku")
    ]
    started.clear()

    unavailable = client.post("/api/agents/start", headers={"X-Decomp": "1"}, json={
        "kind": "claude", "slug": "game", "options": {"models": ["retired-model"]},
    })
    assert unavailable.status_code == 400
    assert started == []

    response = client.post("/api/agents/start", headers={"X-Decomp": "1"}, json={
        "kind": "claude", "slug": "game", "count": 2,
        "options": {"models": ["sonnet", "haiku"], "effort": "high"},
    })

    assert response.status_code == 200, response.text
    assert response.json()["started"] == [f"{model}#high-{i}" for model in ("sonnet", "haiku") for i in range(2)]
    assert started == [
        ("claude", "game", 2, {"model": f"{model}#high", "effort": "high", "per_session": agents.PER_SESSION})
        for model in ("sonnet", "haiku")
    ]
    saved = json.loads((tmp_path / "settings.json").read_text())["launcher"]
    assert saved["model"] == "" and saved["models"] == ["sonnet", "haiku"]


def test_one_start_refuses_more_than_24_helpers_and_allows_exactly_24(tmp_path, monkeypatch):
    """Each selected model starts `count` helpers: any number of models used to multiply without a total limit."""
    from .test_loading import tiny_game

    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    tiny_game(net)
    mcp, manager, security, _ = build(net, tmp_path, PORT, SECRET)
    names = [f"model-{i}" for i in range(7)]
    monkeypatch.setattr(manager, "detected", lambda: [{"id": "claude", "models": [{"id": name} for name in names]}])
    started = []

    def start(kind, slug, count=1, options=None):
        started.append((kind, slug, count, options))
        return [f"{options['model']}-{i}" for i in range(count)]

    monkeypatch.setattr(manager, "start", start)
    client = TestClient(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                        base_url=f"http://127.0.0.1:{PORT}", headers=WINDOW)
    too_many = client.post("/api/agents/start", headers={"X-Decomp": "1"}, json={
        "kind": "claude", "slug": "game", "count": 4, "options": {"models": names},
    })
    assert too_many.status_code == 400
    assert too_many.json()["error"] == (
        "that's 28 helpers at once; one start can begin at most 24: pick fewer models or fewer of each")
    assert started == []

    exactly = client.post("/api/agents/start", headers={"X-Decomp": "1"}, json={
        "kind": "claude", "slug": "game", "count": 4, "options": {"models": names[:6]},
    })
    assert exactly.status_code == 200, exactly.text
    assert exactly.json()["started"] == [f"{name}-{i}" for name in names[:6] for i in range(4)]
    assert started == [
        ("claude", "game", 4, {"model": name, "per_session": agents.PER_SESSION}) for name in names[:6]
    ]


def test_api_models_are_discovered_and_started_only_on_their_saved_endpoint(tmp_path, monkeypatch):
    """Each API model uses the saved endpoint that listed it, and its key never reaches another endpoint."""
    from decompnet.app import support
    from decompnet.app.routes import setup

    from .test_loading import tiny_game

    settings = support.Settings(tmp_path / "settings.json")
    first = settings.save_endpoint({"provider": "custom", "name": "first", "base_url": "http://one.test/v1",
                                    "model": "fallback-one", "api_key": "secret-one", "max_sessions": 2})
    second = settings.save_endpoint({"provider": "custom", "name": "second", "base_url": "http://two.test/v1",
                                     "model": "fallback-two", "api_key": "secret-two", "max_sessions": 1})
    settings.save()
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    tiny_game(net)
    mcp, manager, security, _ = build(net, tmp_path, PORT, SECRET)
    requests = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "model-a", "name": "Model A"}, {"id": "model-b"}]}

    def get(url, **kwargs):
        requests.append((url, kwargs["headers"]))
        return Response()

    monkeypatch.setattr(setup.httpx, "get", get)
    client = TestClient(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                        base_url=f"http://127.0.0.1:{PORT}", headers=WINDOW)
    listed = client.get(f"/api/endpoints/models?id={first['id']}")
    assert listed.json() == {"models": [{"id": "model-a", "name": "Model A"}, {"id": "model-b", "name": "model-b"}]}
    assert requests == [("http://one.test/v1/models", {"Authorization": "Bearer secret-one"})]
    assert "secret-one" not in listed.text

    started = []

    def start(kind, slug, count=1, options=None):
        started.append((count, options))
        return [f"{options['endpoint']}:{options['model']}:{i}" for i in range(count)]

    monkeypatch.setattr(manager, "start", start)
    common = {"kind": "api", "slug": "game", "options": {"multiple_models": True, "models": [
        {"endpoint": first["id"], "model": "model-a"}, {"endpoint": first["id"], "model": "model-b"},
    ]}}
    malformed = client.post("/api/agents/start", headers={"X-Decomp": "1"}, json={
        "kind": "api", "slug": "game", "options": {"models": [{"endpoint": first["id"], "model": "x", "extra": []}]},
    })
    assert malformed.status_code == 400 and started == []
    client.post("/api/settings", headers={"X-Decomp": "1"}, json={
        "endpoint": {"id": first["id"], "max_sessions": 4},
    })
    over_endpoint_limit = client.post("/api/agents/start", headers={"X-Decomp": "1"}, json=common | {"count": 3})
    assert over_endpoint_limit.status_code == 400 and started == []
    assert "allows 4 sessions" in over_endpoint_limit.json()["error"]

    client.post("/api/settings", headers={"X-Decomp": "1"}, json={
        "endpoint": {"id": first["id"], "max_sessions": 10},
    })
    response = client.post("/api/agents/start", headers={"X-Decomp": "1"}, json=common | {"count": 5})
    assert response.status_code == 200, response.text
    assert response.json()["started"] == [f"{first['id']}:model-a:{i}" for i in range(5)] + [
        f"{first['id']}:model-b:{i}" for i in range(5)]
    assert [(n, options["endpoint"], options["model"], options["api_key"]) for n, options in started] == [
        (5, first["id"], "model-a", "secret-one"), (5, first["id"], "model-b", "secret-one")]
    started.clear()
    common["options"]["models"] = [{"endpoint": first["id"], "model": "model-a"},
                                    {"endpoint": second["id"], "model": "model-b"}]
    response = client.post("/api/agents/start", headers={"X-Decomp": "1"}, json=common | {"count": 1})
    assert response.status_code == 200, response.text
    assert [(n, options["endpoint"], options["model"], options["api_key"]) for n, options in started] == [
        (1, first["id"], "model-a", "secret-one"), (1, second["id"], "model-b", "secret-two")]
    saved = json.loads((tmp_path / "settings.json").read_text())["launcher"]
    assert saved["multiple"] and saved["models"] == common["options"]["models"]
    assert "secret-one" not in json.dumps(saved) and "secret-two" not in json.dumps(saved)


def test_an_endpoint_lists_at_most_100_models(tmp_path, monkeypatch):
    """An OpenAI-compatible server may list hundreds of models; the launcher's picker shows this many at most."""
    from decompnet.app import support
    from decompnet.app.routes import setup

    from .test_loading import tiny_game

    settings = support.Settings(tmp_path / "settings.json")
    entry = settings.save_endpoint({"provider": "custom", "name": "many", "base_url": "http://many.test/v1",
                                    "model": "fallback"})
    settings.save()
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    tiny_game(net)
    mcp, _, security, _ = build(net, tmp_path, PORT, SECRET)

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": f"model-{i}"} for i in range(setup.MAX_ENDPOINT_MODELS + 1)]}

    monkeypatch.setattr(setup.httpx, "get", lambda url, **kwargs: Response())
    client = TestClient(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                        base_url=f"http://127.0.0.1:{PORT}", headers=WINDOW)
    models = client.get(f"/api/endpoints/models?id={entry['id']}").json()["models"]
    assert len(models) == setup.MAX_ENDPOINT_MODELS
    assert models[0]["id"] == "model-0" and models[-1]["id"] == f"model-{setup.MAX_ENDPOINT_MODELS - 1}"


def test_a_picked_api_model_survives_restart_and_resume(tmp_path, monkeypatch):
    """A helper started on the endpoint's second model came back on its default after a restart, which recorded its
    costs against the wrong model."""
    from decompnet.app import support

    from .test_loading import tiny_game

    settings = support.Settings(tmp_path / "settings.json")
    entry = settings.save_endpoint({"provider": "custom", "name": "one", "base_url": "http://one.test/v1",
                                    "model": "endpoint-default"})
    settings.save()
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    tiny_game(net)
    mcp, manager, security, _ = build(net, tmp_path, PORT, SECRET)
    monkeypatch.setattr(manager, "_prepare", lambda slug: None)
    monkeypatch.setattr(manager, "_run", lambda session: None)
    client = TestClient(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                        base_url=f"http://127.0.0.1:{PORT}", headers=WINDOW)
    started = client.post("/api/agents/start", headers={"X-Decomp": "1"}, json={
        "kind": "api", "slug": "game", "options": {"endpoints": [entry["id"]], "model": "picked-single"},
    })
    assert started.status_code == 200, started.text
    selected = client.post("/api/agents/start", headers={"X-Decomp": "1"}, json={
        "kind": "api", "slug": "game", "options": {
            "multiple_models": True, "models": [{"endpoint": entry["id"], "model": "picked-multiple"}],
        },
    })
    assert selected.status_code == 200, selected.text
    picked = dict(zip(started.json()["started"] + selected.json()["started"], ["picked-single", "picked-multiple"]))
    for sid, model in picked.items():
        assert manager.sessions[sid].options["model"] == model

    client.post("/api/settings", headers={"X-Decomp": "1"}, json={
        "endpoint": {"id": entry["id"], "model": "endpoint-default-changed"},
    })
    for sid, model in picked.items():
        manager.stop(sid)
        restarted = client.post("/api/agents/restart", headers={"X-Decomp": "1"}, json={"id": sid})
        assert restarted.status_code == 200, restarted.text
        assert manager.sessions[sid].options["model"] == model
        assert manager.sessions[sid].state == "starting"

        manager.stop(sid)
        session = manager.sessions[sid]
        session.state, session.stopped_by = "stopped", agents.BY_QUIT
        manager._save(session)
    assert set(manager.resume()) == set(picked)
    for sid, model in picked.items():
        assert manager.sessions[sid].options["model"] == model


def test_multiple_model_mode_requires_a_selection(tmp_path):
    from .test_loading import tiny_game

    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    tiny_game(net)
    mcp, _, security, _ = build(net, tmp_path, PORT, SECRET)
    client = TestClient(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                        base_url=f"http://127.0.0.1:{PORT}", headers=WINDOW)
    response = client.post("/api/agents/start", headers={"X-Decomp": "1"}, json={
        "kind": "claude", "slug": "game", "options": {"multiple_models": True},
    })
    assert response.status_code == 400 and response.json()["error"] == "choose at least one model"


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
    net.claim(agent, slug, "func_800007F4")  # The scripted model targets this function explicitly.
    manager._session_begin(s)
    assert api.API.run(manager, s, agent) is True
    transcript = manager.transcripts.read(slug, s.id)['events']
    requests = [json.loads(event['text']) for event in transcript if event['kind'] == 'api_request']
    assert requests == seen
    assert any(event['kind'] == 'prompt' for event in transcript)
    assert any(event['kind'] == 'tool_response' for event in transcript)
    assert 'test-key' not in json.dumps(transcript)
    assert net._function("func_800007F4", slug)["status"] == "matched"
    assert "byte-identical" in seen[-1]["messages"][-1]["content"]


def test_an_agent_nobody_signed_in_to_stops_and_says_so(tmp_path, monkeypatch):
    """Claude Code without a sign-in exits at once: say that (not "exited with code 1") and don't keep retrying."""
    manager = agents.Manager(RomNetwork(tmp_path / "app.db", tmp_path), tmp_path, "http://127.0.0.1:9/mcp")
    monkeypatch.setattr(manager, "_preflight_tools", lambda token: None)
    monkeypatch.setattr(manager, "prepare_session", lambda *a, **kw: None)
    code = "print('Not logged in \\u00b7 Please run /login'); raise SystemExit(1)"
    monkeypatch.setattr(manager, "_command", lambda s, token, workdir: ([sys.executable, "-c", code], dict(os.environ), ""))
    s = agents.Session("claude-1", "claude", "Claude Code #1", "game", "claude-1", {})
    assert manager._run_cli(s, "token", tmp_path) is False
    assert s.signed_out and "isn't signed in" in s.error and "/login" in s.error


def test_an_agent_out_of_credit_stops_and_says_so(tmp_path, monkeypatch):
    """DeepSeek's "Insufficient Balance" (here through opencode): stop at once and say what to do. "Too many
    requests" is the provider's limit of requests at once: not a failure, the session waits and tries again."""
    manager = agents.Manager(RomNetwork(tmp_path / "app.db", tmp_path), tmp_path, "http://127.0.0.1:9/mcp")
    monkeypatch.setattr(manager, "_preflight_tools", lambda token: None)
    monkeypatch.setattr(manager, "prepare_session", lambda *a, **kw: None)
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
                                        "cache_write": 4000, "turns": 9, "coverage": "complete"}]
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
    monkeypatch.setattr(window, "IS_MACOS", False)
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


def test_rom_library_setting_and_background_lookup(app, tmp_path, monkeypatch):
    client, net, _ = app
    from types import SimpleNamespace

    from decompnet.app import romlibrary

    library = tmp_path / 'library'
    library.mkdir()
    response = client.post('/api/settings', json={'rom_library': str(library)}, headers={'X-Decomp': '1'})
    assert response.json()['rom_library'] == str(library)
    assert client.get('/api/state').json()['settings']['rom_library'] == str(library)
    monkeypatch.setattr(net, 'fork_slug', lambda slug: True)
    monkeypatch.setattr(net, 'project_row', lambda slug: {'slug': slug})
    p = SimpleNamespace(root=tmp_path)
    monkeypatch.setattr(net.forks, 'get', lambda slug: (None, p, {}))
    monkeypatch.setattr(net.forks, 'needs', lambda slug: {'sha1': 'a' * 40})
    seen = []
    def find(project, needs, folder, log):
        seen.append((project, needs, folder))
        return {'path': str(library / 'game.z64'), 'in_project': False}
    monkeypatch.setattr(romlibrary, 'find', find)
    assert client.post('/api/fork/find-rom', json={'slug': 'demo'}).status_code == 403
    job = client.post('/api/fork/find-rom', json={'slug': 'demo'}, headers={'X-Decomp': '1'}).json()['job']
    for _ in range(100):
        result = client.get('/api/job', params={'id': job}).json()
        if result['state'] in ('done', 'error'):
            break
        time.sleep(.01)
    assert result['state'] == 'done' and result['result']['path'].endswith('game.z64')
    assert seen == [(p, {'sha1': 'a' * 40}, str(library))]
    assert client.post('/api/settings', json={'rom_library': ''}, headers={'X-Decomp': '1'}).json()['rom_library'] == ''


def test_free_search_settings_are_bounded_and_can_disable_automatic_work(app):
    client, net, _ = app
    response = client.post('/api/settings', json={'reuse_auto': False, 'reuse_seconds': 9999, 'reuse_attempts': -1},
                           headers={'X-Decomp': '1'})
    assert response.status_code == 200
    assert response.json()['reuse_seconds'] == 600 and response.json()['reuse_attempts'] == 1
    assert not net.cascades.enabled
    client.post('/api/settings', json={'reuse_seconds': 120, 'reuse_attempts': 128}, headers={'X-Decomp': '1'})


def test_work_selection_route_edits_only_the_named_worker(tmp_path, monkeypatch):
    from .test_loading import tiny_game

    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    tiny_game(net)
    mcp, manager, security, _ = build(net, tmp_path, PORT, SECRET)
    monkeypatch.setattr(manager, "_prepare", lambda slug: None)
    monkeypatch.setattr(manager, "_run", lambda session: None)
    client = TestClient(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                        base_url=f"http://127.0.0.1:{PORT}", headers=WINDOW)
    response = client.post("/api/agents/start", headers={"X-Decomp": "1"}, json={
        "kind": "codex", "slug": "game", "options": {"selection": {"strategy": "hardest", "match_below": 80}}})
    assert response.status_code == 200, response.text
    sid = response.json()["started"][0]
    response = client.post("/api/agents/selection", headers={"X-Decomp": "1"}, json={
        "id": sid, "selection": {"strategy": "untried", "match_below": 70}})
    assert response.status_code == 200, response.text
    assert manager.sessions[sid].snapshot()["selection"] == {"strategy": "untried", "match_below": 70.0}
    invalid = client.post("/api/agents/selection", headers={"X-Decomp": "1"}, json={
        "id": sid, "selection": {"match_below": 101}})
    assert invalid.status_code == 400
    assert manager.sessions[sid].snapshot()["selection"]["match_below"] == 70


def test_iterative_settings_opt_in_and_separate_limits(app):
    client, net, _ = app
    assert not net.cascades.iterative_enabled
    response = client.post('/api/settings', json={'iterative_enabled': True, 'iterative_seconds': 9999,
                                                  'iterative_attempts': -1}, headers={'X-Decomp': '1'})
    assert response.status_code == 200
    assert response.json()['iterative_enabled'] is True
    assert response.json()['iterative_seconds'] == 600
    assert response.json()['iterative_attempts'] == 1
    assert net.cascades.iterative_enabled and net.cascades.iterative_seconds == 600
    assert net.cascades.attempts == 128 and net.cascades.seconds == 120
    client.post('/api/settings', json={'iterative_enabled': False}, headers={'X-Decomp': '1'})
    assert not net.cascades.iterative_enabled


def test_sdk_inventory_is_global_and_selection_is_a_guarded_project_setting(app, monkeypatch):
    client, net, _ = app
    from decompnet.app import sdk_library
    monkeypatch.setattr(sdk_library, 'state', lambda net, slug=None: {'folders': [], 'libraries': [], 'selection': None})
    assert client.get('/api/sdk-library').json()['folders'] == []
    assert client.post('/api/sdk-library', json={'folders': []}).status_code == 403
    selected = []
    monkeypatch.setattr(sdk_library, 'choose', lambda net, slug, selection: selected.append((slug, selection)))
    response = client.post('/api/sdk-library', json={'slug': 'example', 'selection': []}, headers={'X-Decomp': '1'})
    assert response.status_code == 200 and selected == [('example', [])]


def test_transcript_route_requires_window_auth_and_checks_project(app, tmp_path, monkeypatch):
    client, net, manager = app
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                   ('trace-demo', 'Transcript fixture', 'mips', str(tmp_path), '{}'))
    s = agents.Session('9001', 'codex', 'Transcript', 'trace-demo', 'trace-helper', {})
    manager._session_begin(s)
    s.record('prompt', 'The full initial prompt')
    url = f'/api/transcript?slug=trace-demo&helper=9001&session={s.session_id}'
    assert client.get(url, headers={'User-Agent': 'outside'}).status_code == 401
    result = client.get(url)
    assert result.status_code == 200
    assert result.json()['events'][0]['text'] == 'The full initial prompt'
    assert client.get(url.replace('trace-demo', 'other')).status_code == 400
    from pathlib import Path
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    payload = {'slug': 'trace-demo', 'helper': 9001}
    assert client.post('/api/transcript', json=payload).status_code == 403
    exported = client.post('/api/transcript', json=payload, headers={'X-Decomp': '1'})
    assert exported.status_code == 200
    data = json.loads(Path(exported.json()['path']).read_text())
    assert data['sessions'][0]['events'][0]['text'] == 'The full initial prompt'



def test_agent_rename_route_keeps_identity_and_checks_input(app):
    client, net, manager = app
    session = manager._record('game', 'codex', 'rename-test', 'Original', {})
    response = client.post('/api/agents/rename', headers={'X-Decomp': '1'},
                           json={'id': session.id, 'label': 'SDK hunter'})
    assert response.status_code == 200 and response.json()['label'] == 'SDK hunter'
    assert session.agent_name == 'rename-test'
    invalid = client.post('/api/agents/rename', headers={'X-Decomp': '1'},
                          json={'id': session.id, 'label': ''})
    assert invalid.status_code == 400
    assert session.label == 'SDK hunter'
    assert client.post('/api/agents/rename', json={'id': session.id, 'label': 'No header'}).status_code == 403


def test_toolchain_reinstall_keeps_explicit_license_acceptance(app, monkeypatch, tmp_path):
    client, _, _ = app
    calls = []
    def install(tid, **kwargs):
        calls.append((tid, kwargs))
        return tmp_path
    monkeypatch.setattr(toolchains, 'install', install)
    response = client.post('/api/toolchain', json={'id': 'ido-5.2', 'accept_license': True, 'force': True},
                           headers={'X-Decomp': '1'})
    assert response.status_code == 200
    for _ in range(200):
        job = client.get('/api/job', params={'id': response.json()['job']}).json()
        if job['state'] != 'running':
            break
        time.sleep(0.01)
    assert job['state'] == 'done', job
    assert calls[0][0] == 'ido-5.2'
    assert calls[0][1]['force'] is True and calls[0][1]['accept_license'] is True


def test_permuter_settings_apply_immediately_and_survive_reloading(app):
    from decompnet.app.support import Settings

    client, net, manager = app
    values = {'permuter_max_insns': 2000, 'permuter_seconds': 180, 'permuter_max_turns': 12}
    response = client.post('/api/settings', json=values, headers={'X-Decomp': '1'})
    assert response.status_code == 200
    assert all(response.json()[k] == v for k, v in values.items())
    assert (net.permuter.max_insns, net.permuter.seconds, net.permuter.max_turns) == (2000, 180, 12)
    saved = Settings(manager.home / 'settings.json').public()
    assert all(saved[k] == v for k, v in values.items())
    response = client.post('/api/settings', json={'permuter_max_insns': 999999, 'permuter_seconds': -1,
                                                'permuter_max_turns': 9999}, headers={'X-Decomp': '1'})
    assert response.json()['permuter_max_insns'] == 100000
    assert (net.permuter.seconds, net.permuter.max_turns) == (1, 1000)
    client.post('/api/settings', json={'permuter_max_insns': 400, 'permuter_seconds': 60,
                                     'permuter_max_turns': 4}, headers={'X-Decomp': '1'})


def test_the_worker_thread_setting_applies_immediately_and_survives_reloading(app):
    from decompnet.app import workers
    from decompnet.app.support import Settings

    client, net, manager = app
    response = client.post('/api/settings', json={'worker_threads': 3}, headers={'X-Decomp': '1'})
    assert response.status_code == 200 and response.json()['worker_threads'] == 3
    assert workers.WORKERS == 3
    assert Settings(manager.home / 'settings.json').public()['worker_threads'] == workers.WORKERS
    assert response.json()['worker_threads_default'] == workers.default_workers()
    response = client.post('/api/settings', json={'worker_threads': 9999}, headers={'X-Decomp': '1'})
    assert response.json()['worker_threads'] == 9999 and workers.WORKERS == 9999  # (no limit)
    response = client.post('/api/settings', json={'worker_threads': 0}, headers={'X-Decomp': '1'})
    assert response.json()['worker_threads'] == 0 and workers.WORKERS == workers.default_workers()


def test_the_iterative_repair_thread_setting_applies_immediately_and_survives_reloading(app):
    from decompnet.app.support import Settings

    client, net, manager = app
    response = client.post('/api/settings', json={'iterative_threads': 3}, headers={'X-Decomp': '1'})
    assert response.status_code == 200 and response.json()['iterative_threads'] == 3
    assert net.cascades.iterative_threads == 3
    assert Settings(manager.home / 'settings.json').public()['iterative_threads'] == net.cascades.iterative_threads
    response = client.post('/api/settings', json={'iterative_threads': 9999}, headers={'X-Decomp': '1'})
    assert response.json()['iterative_threads'] == 9999  # (no limit)
    response = client.post('/api/settings', json={'iterative_threads': 0}, headers={'X-Decomp': '1'})
    assert response.json()['iterative_threads'] == 0 and net.cascades.iterative_threads == 0


def test_reset_agent_metrics_requires_app_auth_and_rejects_missing_helper(app):
    client, _, _ = app
    assert client.post('/api/agents/reset-metrics', json={'id': 'missing'}, headers={'X-Decomp': '1'}).status_code == 400
    assert client.post('/api/agents/reset-metrics', json={'id': 'missing'}).status_code == 403


def test_permuter_can_run_without_starting_paid_helpers(app, monkeypatch):
    client, net, manager = app
    calls=[]
    monkeypatch.setattr(net, 'project_row', lambda slug: {'slug':slug})
    monkeypatch.setattr(net.permuter, 'start', lambda slug: calls.append(('start',slug)))
    monkeypatch.setattr(net.permuter, 'stop', lambda slug: calls.append(('stop',slug)))
    monkeypatch.setattr(net.permuter, 'status', lambda slug: {'state':'running'})
    headers={'X-Decomp':'1'}
    assert client.post('/api/permuter',json={'slug':'game'}).status_code == 403
    response=client.post('/api/permuter',json={'slug':'game'},headers=headers)
    assert response.status_code == 200 and response.json()['permuter']['state']=='running'
    assert client.post('/api/permuter',json={'slug':'game','action':'stop'},headers=headers).status_code==200
    assert client.post('/api/permuter',json={'slug':'game','action':'bad'},headers=headers).status_code==400
    assert calls==[('start','game'),('stop','game')]


def test_source_preparation_settings_and_bounded_job(app, tmp_path, monkeypatch):
    from decompnet.app import source_preparation
    client, net, _ = app
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,arch,root,config) VALUES('source-test','Game','mips',?,'{}')", (str(tmp_path),))
    headers = {'X-Decomp': '1'}
    root = tmp_path / 'reference'
    root.mkdir()
    (root / 'game.c').write_text('int original(void) { return 1; }')
    data = {'slug': 'source-test', 'root': str(root), 'enabled': True, 'mappings': 'f = original @ game.c'}
    saved = client.post('/api/source-preparation', json=data, headers=headers)
    assert saved.status_code == 200
    assert client.get('/api/source-preparation?slug=source-test').json()['root'] == str(root)
    invalid = client.post('/api/source-preparation', json=data | {'mappings': 'not a mapping'}, headers=headers)
    assert invalid.status_code == 400
    calls = []
    monkeypatch.setattr(source_preparation, 'run', lambda *args: calls.append(args) or {'tested': 1, 'improved': 1, 'matched': 0, 'errors': []})
    response = client.post('/api/source-preparation', json={'slug': 'source-test', 'action': 'run'}, headers=headers)
    assert response.status_code == 200
    job = response.json()['job']
    for _ in range(100):
        result = client.get('/api/job', params={'id': job}).json()
        if result['state'] in {'done', 'error'}:
            break
        time.sleep(.01)
    assert result['state'] == 'done' and calls
    assert result['result']['improved'] == 1


def test_deep_permuter_route_starts_a_cancellable_job_for_one_function(app, monkeypatch):
    from decompnet.app import deep_permuter
    client, _, _ = app
    checked = []
    monkeypatch.setattr(deep_permuter, 'validate', lambda *args: checked.append(args[1:]))
    def run(net, slug, name, minutes, variants, log):
        log('Testing only '+name)
        log.progress({'tested':1, 'skipped':0, 'phase':'local repairs'})
        return {'function':name, 'matched':False, 'tested':1}
    monkeypatch.setattr(deep_permuter, 'run', run)
    payload = {'slug':'selected-game','function':'selected_function','minutes':10,'max_variants':99}
    response = client.post('/api/permuter/function', json=payload, headers={'X-Decomp':'1'})
    assert response.status_code == 200
    job_id = response.json()['job']
    for _ in range(100):
        result = client.get('/api/job?id='+job_id).json()
        if result['state'] != 'running':
            break
        time.sleep(.01)
    assert result['state'] == 'done'
    assert result['result']['function'] == 'selected_function'
    assert checked == [('selected-game','selected_function',10,99)]


def test_deep_sweep_routes_validate_start_stop_and_status(app, monkeypatch):
    client, net, _ = app
    calls = []
    monkeypatch.setattr(net.deep_sweeps, 'start', lambda *args: calls.append(args) or {'job':'sweep','enabled':True})
    monkeypatch.setattr(net.deep_sweeps, 'stop', lambda slug: {'enabled':False,'project':slug})
    monkeypatch.setattr(net.deep_sweeps, 'status', lambda slug: {'project':slug,'completed':2})
    assert client.get('/api/permuter/pass?slug=example').json()['completed'] == 2
    monkeypatch.setattr(net.deep_sweeps, 'reset_metrics', lambda slug: {'reset':slug})
    assert client.post('/api/permuter/pass', json={'slug':'example','action':'reset-metrics'}, headers={'X-Decomp':'1'}).json() == {'reset':'example'}
    response = client.post('/api/permuter/pass', json={'slug':'example','minutes':7,'max_variants':99,'repeat':True}, headers={'X-Decomp':'1'})
    assert response.json()['job'] == 'sweep' and calls == [('example',7,99,True,0)]
    assert not client.post('/api/permuter/pass', json={'slug':'example','action':'stop'}, headers={'X-Decomp':'1'}).json()['enabled']
    assert client.post('/api/permuter/pass', json={'slug':'example','repeat':'yes'}, headers={'X-Decomp':'1'}).status_code == 400
    assert client.post('/api/permuter/pass', json={'slug':'example','action':'unknown'}, headers={'X-Decomp':'1'}).status_code == 400


def test_cross_game_run_is_dedicated_and_rejects_duplicate_passes(app, monkeypatch):
    client, net, _ = app
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(net, 'project_row', lambda slug: {'slug':slug})
    monkeypatch.setattr(net, 'autopilot_status', lambda slug: None)
    monkeypatch.setattr(net.twins, 'cross_game_results', lambda slug: {'counts':{'improved':2}})
    def across(slug, job):
        assert slug == 'cross-demo' and job.reuse_limit == 128
        entered.set()
        assert release.wait(5)
        job.cross_tried = 3
        return 1
    monkeypatch.setattr(net.twins, 'across_games', across)
    monkeypatch.setattr(net, 'start_autopilot', lambda *a: pytest.fail('must not launch full autopilot'))
    try:
        response = client.post('/api/cross-game/run', headers={'X-Decomp':'1'}, json={'slug':'cross-demo'})
        assert response.status_code == 200
        assert entered.wait(5)
        assert client.post('/api/cross-game/run', headers={'X-Decomp':'1'}, json={'slug':'cross-demo'}).status_code == 400
    finally:
        release.set()
    for _ in range(100):
        job = client.get('/api/job', params={'id':response.json()['job']}).json()
        if job['state'] != 'running':
            break
        time.sleep(.01)
    assert job['state'] == 'done' and job['result'] == {'matched':1,'improved':0,'tried':3}
    assert net.twins.jobs['cross-demo'].state == 'done'


def test_cross_game_run_failure_releases_busy_state(app, monkeypatch):
    client, net, _ = app
    monkeypatch.setattr(net, 'project_row', lambda slug: {'slug':slug})
    monkeypatch.setattr(net, 'autopilot_status', lambda slug: None)
    monkeypatch.setattr(net.twins, 'cross_game_results', lambda slug: {'counts':{}})
    def fail(*args):
        raise ValueError('Test compiler unavailable')
    monkeypatch.setattr(net.twins, 'across_games', fail)
    response = client.post('/api/cross-game/run', headers={'X-Decomp':'1'}, json={'slug':'cross-failure'})
    for _ in range(100):
        job = client.get('/api/job', params={'id':response.json()['job']}).json()
        if job['state'] != 'running':
            break
        time.sleep(.01)
    assert job['state'] == 'error' and net.twins.jobs['cross-failure'].state == 'done'


def test_quality_alternative_selection_uses_explicit_review_route(app, monkeypatch):
    from decompnet import candidate_recovery
    client, net, _ = app
    calls = []
    monkeypatch.setattr(candidate_recovery, 'adopt', lambda *args: calls.append(args[1:]) or {'score':55})
    body = {'slug':'example', 'function':'selected', 'source_hash':'revision', 'action':'adopt', 'attempt_id':123}
    response = client.post('/api/repair-examples/quality', json=body, headers={'X-Decomp':'1'})
    assert response.status_code == 200 and response.json()['score'] == 55
    assert calls == [('example', 'selected', 'revision', 123)]


def test_candidate_recovery_batches_are_bounded_and_do_not_duplicate_jobs(app, monkeypatch):
    from decompnet.app import candidate_rechecks
    client, net, _ = app
    import threading
    gate = threading.Event()
    calls = []
    def start(*args):
        calls.append(args)
        gate.wait(30)
        return {'functions':1,'tested':1}
    monkeypatch.setattr(candidate_rechecks, 'run', start)
    response = client.post('/api/repair-examples/recovery', json={'slug':'missing','limit':20}, headers={'X-Decomp':'1'})
    assert response.status_code == 400
    # Reuse the same project fixture setup as the single-function route: project lookup is the only prerequisite.
    monkeypatch.setattr(net, 'project_row', lambda slug: {'slug':slug})
    for bad in (True, 0, 201, '20'):
        assert client.post('/api/repair-examples/recovery', json={'slug':'example','limit':bad}, headers={'X-Decomp':'1'}).status_code == 400
    response = client.post('/api/repair-examples/recovery', json={'slug':'example','limit':3}, headers={'X-Decomp':'1'})
    try:
        assert response.status_code == 200 and response.json()['job']
        duplicate = client.post('/api/repair-examples/recovery', json={'slug':'example','limit':3}, headers={'X-Decomp':'1'})
        assert duplicate.status_code == 400 and 'running' in duplicate.json()['error']
    finally:
        gate.set()


def test_slow_resume_does_not_block_building_the_app(tmp_path, monkeypatch):
    from decompnet.app.deep_sweep import Sweeps

    entered, release, resumed = threading.Event(), threading.Event(), threading.Event()
    def slow_resume(_self):
        entered.set()
        release.wait(10)
    def helpers_resume(_self):
        resumed.set()
    monkeypatch.setattr(Sweeps, 'resume', slow_resume)
    monkeypatch.setattr(agents.Manager, 'resume', helpers_resume)
    home = tmp_path / '.decomp'
    home.mkdir()
    net = RomNetwork(home / 'decomp.db', tmp_path)
    try:
        mcp, _, security, _ = build(net, home, PORT, SECRET)
        assert entered.wait(3)
        client = TestClient(mcp.streamable_http_app(transport_security=security, host='127.0.0.1'),
                            base_url=f'http://127.0.0.1:{PORT}')
        assert client.get('/health').status_code == 200
    finally:
        release.set()
    assert resumed.wait(3)


def test_project_list_reads_saved_summary_without_waiting_for_progress_sync(tmp_path, monkeypatch):
    home = tmp_path / '.decomp'
    home.mkdir()
    net = RomNetwork(home / 'decomp.db', tmp_path)
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                   ('game', 'Game', 'mips', str(tmp_path), json.dumps({'kind': 'fork', 'platform': 'n64'})))
    def no_sync(*args, **kwargs):
        raise AssertionError('project list must not wait for synchronization')
    monkeypatch.setattr(net.progress, 'sync', no_sync)
    summary = net.summary('game')
    assert summary['title'] == 'Game'
    assert summary['functions'] == 0


def test_project_overview_reads_database_while_progress_lock_is_busy(app, monkeypatch):
    client, net, _ = app
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                   ('busy-view', 'Busy project', 'mips', '/unused', '{}'))
    monkeypatch.setattr(net, 'overview', lambda slug: {'title': net.project_row(slug)['name'],
                                                     'progress_syncing': net.progress.syncing(slug)})
    monkeypatch.setattr(net, 'warm_briefs_soon', lambda _: None)
    done, result = threading.Event(), []
    def request():
        try:
            result.append(client.get('/api/project?slug=busy-view'))
        finally:
            done.set()
    worker = threading.Thread(target=request, daemon=True)
    try:
        with net.progress._project_lock('busy-view'):
            worker.start()
            assert done.wait(2), 'overview waited for saved-code verification'
        assert result[0].status_code == 200
        assert result[0].json() == {'title': 'Busy project', 'progress_syncing': False}
    finally:
        worker.join(timeout=3)
        with net.store.tx() as db:
            db.execute("DELETE FROM projects WHERE slug='busy-view'")


def test_resuming_helpers_does_not_hold_agent_state_lock_during_progress_sync(tmp_path, monkeypatch):
    from types import SimpleNamespace
    net = RomNetwork(tmp_path / 'db.sqlite', tmp_path)
    manager = agents.Manager(net, tmp_path, 'http://127.0.0.1:6499/mcp')
    manager._found = []
    helper = SimpleNamespace(id='saved-helper', state='stopped', stopped_by=agents.BY_QUIT,
                             slug='game', kind='codex', options={})
    manager.sessions = {helper.id: helper}
    entered, release, readable = threading.Event(), threading.Event(), threading.Event()
    def project_row(_):
        entered.set()
        release.wait(5)
    monkeypatch.setattr(net, 'project_row', project_row)
    monkeypatch.setattr(manager, '_prepare', lambda _: None)
    monkeypatch.setattr(manager, '_launch', lambda _: None)
    resumed = threading.Thread(target=manager.resume, daemon=True)
    def detect():
        manager.detected()
        readable.set()
    reader = threading.Thread(target=detect, daemon=True)
    try:
        resumed.start()
        assert entered.wait(2)
        reader.start()
        assert readable.wait(2), 'agent detection waited for project synchronization'
    finally:
        release.set()
        resumed.join(timeout=3)
        reader.join(timeout=3)


def test_setup_agent_choices_endpoint_returns_installed_clis(app, monkeypatch):
    from decompnet.app import setup_fix
    client, _, _ = app
    expected = [{'id': 'codex', 'name': 'Codex', 'installed': True}]
    monkeypatch.setattr(setup_fix, 'choices', lambda: expected)
    response = client.get('/api/fork/fix-setup')
    assert response.status_code == 200
    assert response.json() == {'agents': expected}


def test_sidebar_reads_saved_snapshot_without_triggering_verification(app, monkeypatch):
    client, net, _ = app
    seen = []
    monkeypatch.setattr(net.progress, 'sync', lambda *args, **kwargs: pytest.fail('UI must not start verification'))
    def summary(slug):
        seen.append(net.progress.viewing)
        return {'slug':slug,'title':slug}
    monkeypatch.setattr(net, 'summary', summary)
    with net.store.tx() as db:
        db.execute("INSERT OR IGNORE INTO projects(slug,name,root,config) VALUES('ui-snapshot','UI','/tmp','{}')")
    response = client.get('/api/projects')
    assert response.status_code == 200 and seen and all(seen)


def test_manual_mcp_connections_require_window_and_write_header(app, tmp_path):
    client, net, _ = app
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                   ('manual-test', 'Manual test', 'mips', str(tmp_path), '{}'))
    endpoint = '/api/manual-mcp'
    assert client.get(endpoint, headers={'User-Agent': 'external'}).status_code == 401
    payload = {'action': 'create', 'slug': 'manual-test'}
    assert client.post(endpoint, json=payload).status_code == 403
    headers = {'X-Decomp': '1'}
    assert client.post(endpoint, json={'action': 'create', 'slug': 'missing'}, headers=headers).status_code == 400
    result = client.post(endpoint, json=payload, headers=headers)
    assert result.status_code == 200
    connection = result.json()
    assert connection['url'] == f'http://127.0.0.1:{PORT}/mcp'
    assert connection['token'].startswith('dn_')
    snapshot = client.get(endpoint + '?slug=manual-test').json()
    assert snapshot['transport'] == 'Streamable HTTP'
    assert any(t['name'] == 'project_info' and t['inputSchema'] for t in snapshot['tools'])
    assert connection['token'] not in json.dumps(snapshot)
    assert client.post(endpoint, json={'action': 'revoke', 'id': connection['id']}, headers=headers).status_code == 200
    assert all(c['id'] != connection['id'] for c in client.get(endpoint).json()['connections'])


def test_update_source_selection_is_guarded_and_does_not_install(app, monkeypatch):
    from decompnet.app import updates
    client, *_ = app
    requests = []
    monkeypatch.setattr(updates, 'configure_source', lambda repo, branch: requests.append((repo, branch)) or
                        {'repo': 'another/fork', 'branch': branch, 'channel': 'branch'})
    monkeypatch.setattr(updates.Updater, 'apply', lambda *args: pytest.fail('Saving a source must not install'))
    data = {'action': 'source', 'repo': 'https://github.com/another/fork', 'branch': 'main'}
    assert client.post('/api/update', json=data).status_code == 403
    response = client.post('/api/update', json=data, headers={'X-Decomp': '1'})
    assert response.status_code == 200
    assert response.json()['current']['repo'] == 'another/fork'
    assert requests == [(data['repo'], 'main')]


def test_metrics_export_route_uses_private_project_guard_and_returns_saved_path(app, monkeypatch):
    from decompnet.app import metrics_export
    client, net, _ = app
    calls = []
    monkeypatch.setattr(metrics_export, 'save', lambda slug, csv: calls.append((slug, csv)) or {'path': '/Downloads/metrics.csv'})
    response = client.post('/api/permuter/pass', json={'slug':'example','action':'export-metrics','csv':'metrics'}, headers={'X-Decomp':'1'})
    assert response.status_code == 200 and response.json()['path'] == '/Downloads/metrics.csv'
    assert calls == [('example', 'metrics')]
    assert client.post('/api/permuter/pass', json={'slug':'example','action':'export-metrics','csv':'metrics'}).status_code == 403


def test_prompt_settings_are_guarded_and_preview_does_not_save(app):
    from decompnet.app import prompt_settings
    client, net, _ = app
    endpoint='/api/agent-prompts'
    assert client.get(endpoint,headers={'User-Agent':'external'}).status_code==401
    assert client.post(endpoint,json={'overrides':{}}).status_code==403
    headers={'X-Decomp':'1'}
    assert client.post(endpoint,json={'overrides':{'initial_rom':'{unknown}'}},headers=headers).status_code==400
    data={'overrides':{'instructions':'Test global app instructions'}}
    assert client.post(endpoint,json={**data,'action':'preview'},headers=headers).json()['instructions']=='Test global app instructions'
    assert prompt_settings.global_overrides(net)=={}
    saved=client.post(endpoint,json=data,headers=headers)
    assert saved.status_code==200
    assert next(f for f in saved.json()['fields'] if f['key']=='instructions')['custom']
    assert client.post(endpoint,json={'helper':'missing','overrides':{}},headers=headers).status_code==400
    client.post(endpoint,json={'overrides':{}},headers=headers)


def test_per_agent_prompt_preview_and_save_are_isolated(app,tmp_path,monkeypatch):
    from decompnet.app.agents import Session
    client, net, manager = app
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                   ('prompt-demo','Prompt demo','mips',str(tmp_path),'{}'))
    helper = Session('991','codex','prompt-reader','prompt-demo','prompt-reader',{})
    manager.sessions[helper.id]=helper
    monkeypatch.setattr(net,'overview',lambda slug:{'title':'Synthetic game','kind':'rom','platform':'n64',
                                                   'compiler':{'toolchain':'gcc-2.7.2-kmc'}})
    payload={'helper':helper.id,'overrides':{'initial_rom':'Experiment {title}','instructions':'Private instructions'}}
    preview=client.post('/api/agent-prompts',json={**payload,'action':'preview'},headers={'X-Decomp':'1'})
    assert preview.status_code==200
    assert 'Experiment Synthetic game' in preview.json()['initial']
    assert preview.json()['instructions']=='Private instructions'
    assert 'prompts' not in helper.options
    response=client.post('/api/agent-prompts',json=payload,headers={'X-Decomp':'1'})
    assert response.status_code==200 and helper.options['prompts']==payload['overrides']
    assert next(f for f in client.get('/api/agent-prompts').json()['fields'] if f['key']=='instructions')['value'] != 'Private instructions'
    manager.sessions.pop(helper.id)

def test_agent_tool_controls_are_guarded_persistent_and_leave_core_tools_enabled(app):
    from decompnet.app import tool_policy
    client, net, _ = app
    endpoint = '/api/agent-tools'
    assert client.get(endpoint, headers={'User-Agent':'external'}).status_code == 401
    payload = {'name':'guide','enabled':False}
    assert client.post(endpoint, json=payload).status_code == 403
    result = client.post(endpoint, json=payload, headers={'X-Decomp':'1'})
    assert result.status_code == 200
    guide = next(t for t in result.json()['tools'] if t['name']=='guide')
    assert not guide['enabled'] and guide['global_disabled'] and guide['calls'] >= 0
    assert 'Older unrecorded calls' in result.json()['coverage']
    assert 'guide' in tool_policy.disabled(net)
    assert client.post(endpoint, json={'name':'claim_function','enabled':False}, headers={'X-Decomp':'1'}).status_code == 400
    assert client.post(endpoint, json={'helper':'missing','name':'guide','enabled':True}, headers={'X-Decomp':'1'}).status_code == 400
    assert client.post(endpoint, json={'name':'guide','enabled':True}, headers={'X-Decomp':'1'}).status_code == 200


def test_agent_tool_controls_update_only_the_selected_helper(app, tmp_path):
    from decompnet.app.agents import Session
    client, net, manager = app
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                   ('tool-control-demo','Tool control demo','mips',str(tmp_path),'{}'))
    session = Session('tool-control', 'codex', 'tool-reader', 'tool-control-demo', 'tool-reader', {})
    manager.sessions[session.id] = session
    response = client.post('/api/agent-tools', json={'helper':session.id,'name':'guide','enabled':False}, headers={'X-Decomp':'1'})
    assert response.status_code == 200
    assert not next(t for t in response.json()['tools'] if t['name']=='guide')['enabled']
    assert net.selection('tool-control-demo','tool-reader')['disabled_tools'] == ['guide']
    from decompnet.app.tool_policy import disabled
    assert 'guide' not in disabled(net)
    manager.sessions.pop(session.id)


def test_fit_bounds_an_oversized_latest_tool_result_without_changing_prompts():
    messages = [{'role':'system','content':'Custom instructions'}, {'role':'user','content':'Custom initial prompt'},
                {'role':'assistant','tool_calls':[{'id':'one','type':'function','function':{'name':'get_function','arguments':'{}'}}]},
                {'role':'tool','tool_call_id':'one','content':'assembly ' * 20000}]
    assert api.fit(messages, 2000) <= 2000
    assert messages[0]['content'] == 'Custom instructions'
    assert messages[1]['content'] == 'Custom initial prompt'
    assert messages[-1]['tool_call_id'] == 'one'
    assert 'trimmed' in messages[-1]['content']


def test_remove_notes_is_function_scoped_and_requires_window_auth(app, tmp_path):
    client, net, manager = app
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                         ('notes-test', 'Notes fixture', 'mips', str(tmp_path), '{}')).lastrowid
        one = db.execute('INSERT INTO functions(project_id,name) VALUES(?,?)', (pid, 'one')).lastrowid
        two = db.execute('INSERT INTO functions(project_id,name) VALUES(?,?)', (pid, 'two')).lastrowid
        ids = [db.execute('INSERT INTO notes(project_id,function_id,text,created_at) VALUES(?,?,?,?)',
                           (pid, fid, 'note', 1)).lastrowid for fid in (one, one, two)]
    payload = {'slug': 'notes-test', 'function': 'one', 'action': 'remove', 'ids': ids}
    assert client.post('/api/note', json=payload).status_code == 403
    assert client.post('/api/note', json=payload, headers={'X-Decomp':'1'}).status_code == 200
    remaining = net.store.all('SELECT id FROM notes WHERE project_id=?', pid)
    assert [r['id'] for r in remaining] == [ids[-1]]


def test_finish_near_starts_a_cancellable_budgeted_job(app,monkeypatch):
    from decompnet.app import finish_near
    client,_,_=app
    checked=[]
    monkeypatch.setattr(finish_near,'validate',lambda *args:checked.append(args[1:]))
    def run(net,slug,seconds,attempts,log):
        assert hasattr(log,'cancel_event')
        log.progress({'matched':1,'attempts':3,'matches_per_minute':2})
        return {'matched':1,'resumable':True}
    monkeypatch.setattr(finish_near,'run',run)
    response=client.post('/api/finish-near',json={'slug':'near-game','seconds':30,'attempts':12},headers={'X-Decomp':'1'})
    assert response.status_code==200
    for _ in range(100):
        result=client.get('/api/job?id='+response.json()['job']).json()
        if result['state']!='running':break
        time.sleep(.01)
    assert result['state']=='done' and result['result']['matched']==1
    assert checked==[('near-game',30,12)]


def test_reconcile_matches_starts_a_cancellable_budgeted_job(app,monkeypatch):
    from decompnet.app import match_reconciliation
    client,_,_=app
    checked=[]
    monkeypatch.setattr(match_reconciliation,'validate',lambda *args:checked.append(args[1:]))
    def run(net,slug,seconds,attempts,log):
        assert hasattr(log,'cancel_event')
        log.progress({'matched':1,'attempts':3,'matches_per_minute':2})
        return {'matched':1,'resumable':True}
    monkeypatch.setattr(match_reconciliation,'run',run)
    response=client.post('/api/reconcile-matches',json={'slug':'near-game','seconds':30,'attempts':12},headers={'X-Decomp':'1'})
    assert response.status_code==200
    for _ in range(100):
        result=client.get('/api/job?id='+response.json()['job']).json()
        if result['state']!='running':break
        time.sleep(.01)
    assert result['state']=='done' and result['result']['matched']==1
    assert checked==[('near-game',30,12)]


def test_generation_metrics_route_is_source_free_and_process_local(app):
    from decompnet.app import repair_generation
    client,net,_=app
    with repair_generation.session(net,'private-scope',None,['one'],accept=lambda *args:True):
        repair_generation.call(lambda:[('one','private source')])
    response=client.get('/api/repair-generation-metrics')
    assert response.status_code==200
    result=response.json()
    assert result['retained']>=1 and result['limit']==64 and 'private' not in response.text
