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


def test_try_route_keeps_exact_credit_and_adoption_separate(app,monkeypatch):
    from .test_loading import tiny_game
    client,net,_=app
    tiny_game(net,'exact-recheck-fields')
    monkeypatch.setattr(net,'attempt',lambda *a:{'score':0,'matched':False,'exact':True,'new_match':False,
        'adopted':False,'text':'Exact scratch recheck','checks':{'linked_bytes':{'exact':True}}})
    response=client.post('/api/try',headers={'X-Decomp':'1'},json={'slug':'exact-recheck-fields','name':'f','source':'candidate'})
    assert response.status_code==200,response.text
    result=response.json()
    assert result['exact'] and not result['matched'] and not result['new_match'] and not result['adopted']


def test_maintenance_cpu_setting_is_validated_saved_and_applied_live(app):
    from decompnet.app import maintenance
    client, net, *_ = app
    headers = {'X-Decomp':'1'}
    for value in (-1, 101, 50.5, True, '50', None):
        assert client.post('/api/settings', json={'maintenance_cpu_percent':value}, headers=headers).status_code == 400
    try:
        response = client.post('/api/settings', json={'maintenance_cpu_percent':50}, headers=headers)
        assert response.json()['maintenance_cpu_percent'] == 50
        assert maintenance._cpu_limit == 50
        assert client.get('/api/background-work').json()['maintenance_cpu_percent'] == 50
        saved = json.loads((net.workspace / '.decomp' / 'settings.json').read_text())
        assert saved['maintenance_cpu_percent'] == 50
    finally:
        client.post('/api/settings', json={'maintenance_cpu_percent':0}, headers=headers)


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    (home / ".decomp").mkdir()
    net = RomNetwork(home / ".decomp" / "decomp.db", home)
    mcp, manager, security, _ = build(net, home / ".decomp", PORT, SECRET)
    # Route tests drive saves explicitly; live autosave scheduling has its own integration tests.
    net.autosave.close()
    app = mcp.streamable_http_app(transport_security=security, host="127.0.0.1")
    client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", headers=WINDOW)
    return client, net, manager


WINDOW = {"User-Agent": f"Mozilla/5.0 AppleWebKit/605.1.15 {window.TOKEN_AGENT}{SECRET}"}


def test_opening_a_project_recovers_its_own_successful_save_destination(app, tmp_path):
    from .test_loading import tiny_game

    client, net, _ = app
    first, second = "save-location-first", "save-location-second"
    tiny_game(net, first)
    tiny_game(net, second)
    for slug in (first, second):
        target = tmp_path / slug
        response = client.post("/api/save", headers={"X-Decomp": "1"},
                               json={"slug": slug, "folder": str(target)})
        assert response.status_code == 200, response.text
        assert response.json()["folder"] == str(target.resolve())
    for slug in (second, first):
        for quick in (False, True):
            response = client.get("/api/project", params={"slug": slug, "quick": "1" if quick else "0"})
            assert response.status_code == 200, response.text
            assert response.json()["save_folder"] == str((tmp_path / slug).resolve())
            state = client.get("/api/repository-setup", params={"slug":slug}).json()
            assert state["folder"] == state["save_folder"] == str((tmp_path / slug).resolve())


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
    monkeypatch.setattr(manager, "_preflight_tools", lambda token, **kwargs: None)
    monkeypatch.setattr(manager, "prepare_session", lambda *a, **kw: None)
    code = "print('Not logged in \\u00b7 Please run /login'); raise SystemExit(1)"
    monkeypatch.setattr(manager, "_command", lambda s, token, workdir: ([sys.executable, "-c", code], dict(os.environ), ""))
    s = agents.Session("claude-1", "claude", "Claude Code #1", "game", "claude-1", {})
    monkeypatch.setattr(manager.net, "selection_remaining", lambda *a, **kw: 1)
    monkeypatch.setattr(manager.net, "selection_counts", lambda *a, **kw: {"available":1})
    token = manager.net.local_agent(s.agent_name)[1]
    assert manager._run_cli(s, token, tmp_path) is False
    assert s.signed_out and "isn't signed in" in s.error and "/login" in s.error


def test_an_agent_out_of_credit_stops_and_says_so(tmp_path, monkeypatch):
    """DeepSeek's "Insufficient Balance" (here through opencode): stop at once and say what to do. "Too many
    requests" is the provider's limit of requests at once: not a failure, the session waits and tries again."""
    manager = agents.Manager(RomNetwork(tmp_path / "app.db", tmp_path), tmp_path, "http://127.0.0.1:9/mcp")
    monkeypatch.setattr(manager, "_preflight_tools", lambda token, **kwargs: None)
    monkeypatch.setattr(manager, "prepare_session", lambda *a, **kw: None)
    s = agents.Session("opencode-1", "opencode", "opencode #1", "game", "opencode-1", {})

    monkeypatch.setattr(manager.net, "selection_remaining", lambda *a, **kw: 1)
    monkeypatch.setattr(manager.net, "selection_counts", lambda *a, **kw: {"available":1})

    def says(text: str) -> None:
        code = f"print({text!r}); raise SystemExit(1)"
        monkeypatch.setattr(manager, "_command", lambda s, token, workdir: ([sys.executable, "-c", code], dict(os.environ), ""))

    says("AI_APICallError: Insufficient Balance (request_id: 3e759c3b)")
    with pytest.raises(agents.Fatal, match="out of credit .*Insufficient Balance.*top it up"):
        manager._run_cli(s, manager.net.local_agent(s.agent_name)[1], tmp_path)
    says("AI_APICallError: Too many requests. Your current concurrency is 5, which exceeds your concurrency limit of 5")
    monkeypatch.setattr(manager.net, "selection_remaining", lambda *a, **kw: 1)
    monkeypatch.setattr(manager.net, "selection_counts", lambda *a, **kw: {"available":1})
    token = manager.net.local_agent(s.agent_name)[1]
    assert manager._run_cli(s, token, tmp_path) is False and s.throttled


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
    assert "Recent application log:" in job["log"]
    assert "KeyError" in "\n".join(job["log"]) and "Traceback" in "\n".join(job["log"])
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
        if not refused:  # Force one server-side refusal even when compact schemas shrink below 30 KB.
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
    def run(net, slug, name, minutes, variants, log, compiler_profiles=1, threads_per_function=1, **throughput):
        assert compiler_profiles == 2 and threads_per_function == 3
        log('Testing only '+name)
        log.progress({'tested':1, 'skipped':0, 'phase':'local repairs'})
        return {'function':name, 'matched':False, 'tested':1}
    monkeypatch.setattr(deep_permuter, 'run', run)
    payload = {'slug':'selected-game','function':'selected_function','minutes':10,'max_variants':99,'compiler_profiles':2,'threads_per_function':3}
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


def test_function_deep_job_can_be_reattached_and_duplicate_start_is_reused(app, monkeypatch):
    from decompnet.app import deep_permuter
    client, net, _ = app
    release = threading.Event()
    started = threading.Event()
    calls = []
    monkeypatch.setattr(deep_permuter, 'validate', lambda *args: None)
    monkeypatch.setattr(net, 'function_detail', lambda slug, name, **kwargs: {'name': name})

    def run(net, slug, name, minutes, variants, log, compiler_profiles=1, threads_per_function=1, **throughput):
        assert compiler_profiles == 1
        calls.append(name)
        log('Search still running')
        log.progress({'tested': 17, 'phase': 'random rewrites', 'best_score': 42})
        started.set()
        assert release.wait(10)
        return {'tested': 17, 'reason': 'Time limit reached'}

    monkeypatch.setattr(deep_permuter, 'run', run)
    payload = {'slug': 'reconnect-game', 'function': 'f', 'minutes': 1, 'max_variants': 99}
    try:
        first = client.post('/api/permuter/function', json=payload, headers={'X-Decomp': '1'}).json()
        assert started.wait(5)
        duplicate = client.post('/api/permuter/function', json=payload, headers={'X-Decomp': '1'}).json()
        assert duplicate == first
        detail = client.get('/api/function?slug=reconnect-game&name=f').json()
        assert detail['deep_job'] == first['job']
        assert 'deep_job' not in client.get('/api/function?slug=other-game&name=f').json()
        snapshot = client.get('/api/job?id='+detail['deep_job']).json()
        assert snapshot['progress']['tested'] == 17
        assert snapshot['log'] == ['Search still running']
        assert calls == ['f']
    finally:
        release.set()


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
    assert response.json()['job'] == 'sweep' and calls == [('example',7,99,True,0,1,False,1,False,0)]
    client.post('/api/permuter/pass', json={'slug':'example','threads':2,'threads_per_function':4}, headers={'X-Decomp':'1'})
    assert calls[-1] == ('example',5,10000,False,2,1,False,4,False,0)
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
        assert job.cross_seconds == 1200
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


@pytest.mark.parametrize('data', [{'seconds':0}, {'seconds':86401}, {'seconds':'120'}, {'seconds':None},
                                {'attempts':True}, {'attempts':1.5}, {'attempts':1000001}])
def test_cross_game_invalid_budgets_do_not_reserve_a_pass(app, monkeypatch, data):
    client, net, _ = app
    monkeypatch.setattr(net, 'project_row', lambda slug: {'slug':slug})
    response = client.post('/api/cross-game/run', headers={'X-Decomp':'1'}, json={'slug':'invalid', **data})
    assert response.status_code == 400
    assert 'invalid' not in net.twins.jobs


def test_cross_game_custom_budgets_report_current_work_and_cancel(app, monkeypatch):
    from decompnet.app.cross_game_progress import report
    client, net, _ = app
    entered = threading.Event()
    monkeypatch.setattr(net, 'project_row', lambda slug: {'slug':slug})
    monkeypatch.setattr(net, 'autopilot_status', lambda slug: None)
    monkeypatch.setattr(net.twins, 'cross_game_results', lambda slug: {'counts':{}})
    def across(slug, check):
        assert check.reuse_limit == check.partial_limit == 1000000
        assert 86390 < check.reuse_deadline - time.monotonic() <= 86400
        check.cross_tried = 7
        report(check, 'Compiling candidate', game='Demo donor', function='target', donor='original')
        entered.set()
        assert check.stop.wait(5)
        return 0
    monkeypatch.setattr(net.twins, 'across_games', across)
    response = client.post('/api/cross-game/run', headers={'X-Decomp':'1'},
                           json={'slug':'custom','seconds':86400,'attempts':1000000})
    assert response.status_code == 200 and entered.wait(5)
    jid = response.json()['job']
    snapshot = client.get('/api/job', params={'id':jid}).json()
    assert snapshot['progress']['function'] == 'target' and snapshot['progress']['game'] == 'Demo donor'
    assert snapshot['progress']['attempts'] == 7 and snapshot['progress']['attempt_limit'] == 1000000
    assert client.post('/api/job/cancel', headers={'X-Decomp':'1'}, json={'id':jid}).json()['cancelled']
    for _ in range(100):
        snapshot = client.get('/api/job', params={'id':jid}).json()
        if snapshot['state'] != 'running':break
        time.sleep(.01)
    assert snapshot['state'] == 'cancelled'
    assert net.twins.jobs['custom'].state == 'stopped'


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
    monkeypatch.setattr(agents.agent_startup,'project_facts',lambda net,slug:{'title':'Synthetic game','kind':'rom','platform':'n64',
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


@pytest.mark.parametrize("limits", [{}, {"seconds":None,"attempts":None}, {"seconds":30}, {"attempts":12}, {"seconds":30,"attempts":12}])
def test_reconcile_matches_starts_a_cancellable_budgeted_job(app,monkeypatch,limits):
    from decompnet.app import match_reconciliation
    client,_,_=app
    checked=[]
    monkeypatch.setattr(match_reconciliation,'validate',lambda *args:checked.append(args[1:]))
    def run(net,slug,seconds,attempts,log):
        assert hasattr(log,'cancel_event')
        assert (seconds,attempts) == (limits.get('seconds'),limits.get('attempts'))
        log.progress({'matched':1,'attempts':3,'matches_per_minute':2})
        return {'matched':1,'resumable':True}
    monkeypatch.setattr(match_reconciliation,'run',run)
    response=client.post('/api/reconcile-matches',json={'slug':'near-game',**limits},headers={'X-Decomp':'1'})
    assert response.status_code==200
    for _ in range(100):
        result=client.get('/api/job?id='+response.json()['job']).json()
        if result['state']!='running':break
        time.sleep(.01)
    assert result['state']=='done' and result['result']['matched']==1
    assert checked==[('near-game',limits.get('seconds'),limits.get('attempts'))]


def test_generation_metrics_route_is_source_free_and_process_local(app):
    from decompnet.app import repair_generation
    client,net,_=app
    with repair_generation.session(net,'private-scope',None,['one'],accept=lambda *args:True):
        repair_generation.call(lambda:[('one','private source')])
    response=client.get('/api/repair-generation-metrics')
    assert response.status_code==200
    result=response.json()
    assert result['retained']>=1 and result['limit']==64 and 'private' not in response.text


def test_source_runtime_route_reports_home_and_requires_explicit_data_choice(app):
    client, _, _ = app
    status=client.get('/api/update/runtime')
    assert status.status_code==200 and status.json()['home']
    rejected=client.post('/api/update/runtime',json={'path':'/not/a/checkout'},headers={'X-Decomp':'1'})
    assert rejected.status_code==400 and 'Choose current' in rejected.json()['error']
    configured=client.post('/api/update/runtime',json={'action':'automatic','enabled':True},headers={'X-Decomp':'1'})
    assert configured.status_code==200 and configured.json()['automatic'] is True
    assert client.get('/api/update/runtime').json()['automatic'] is True
    invalid=client.post('/api/update/runtime',json={'action':'automatic','enabled':'yes'},headers={'X-Decomp':'1'})
    assert invalid.status_code==400


def test_background_agent_creation_keeps_server_responsive_and_reports_steps(tmp_path,monkeypatch):
    from .test_loading import tiny_game
    net=RomNetwork(tmp_path/'app.db',tmp_path);tiny_game(net)
    mcp,manager,security,_=build(net,tmp_path,PORT,SECRET)
    client=TestClient(mcp.streamable_http_app(transport_security=security,host='127.0.0.1'),
                      base_url=f'http://127.0.0.1:{PORT}',headers=WINDOW)
    entered,release=threading.Event(),threading.Event()
    monkeypatch.setattr(net,'warm_briefs_soon',lambda slug:None)
    def start(*args):
        agents.report_start('Waiting for saved project verification in test')
        entered.set()
        assert release.wait(5)
        return ['new-worker']
    monkeypatch.setattr(manager,'start',start)
    try:
        response=client.post('/api/agents/start',headers={'X-Decomp':'1'},json={
            'kind':'claude','slug':'game','background':True,'options':{'model':'opus'}})
        assert response.status_code==200
        job=response.json()['job'];assert entered.wait(3)
        status=client.get('/api/job?id='+job).json()
        assert status['state']=='running'
        assert any('saved project verification' in line for line in status['log'])
        assert client.get('/api/close').status_code==200
    finally:
        release.set()
    for _ in range(100):
        status=client.get('/api/job?id='+job).json()
        if status['state']!='running':break
        time.sleep(.01)
    assert status['state']=='done' and status['result']=={'started':['new-worker']}


def test_map_reads_do_not_start_saved_progress_verification(tmp_path,monkeypatch):
    from .test_loading import tiny_game
    net=RomNetwork(tmp_path/'app.db',tmp_path)
    tiny_game(net)
    mcp,_,security,_=build(net,tmp_path,PORT,SECRET)
    client=TestClient(mcp.streamable_http_app(transport_security=security,host='127.0.0.1'),
                      base_url=f'http://127.0.0.1:{PORT}',headers=WINDOW)
    monkeypatch.setattr(net.progress,'sync',lambda *args,**kwargs:pytest.fail('map read must not trigger verification'))
    response=client.get('/api/map?slug=game')
    assert response.status_code==200 and len(response.json()['rows'])==4
    assert client.get('/api/map?slug=game&since='+response.json()['sig']).json()['same']
    def columns(slug):
        assert net.progress.viewing
        net._project(slug)
        return []
    monkeypatch.setattr(net,'map_columns',columns)
    assert client.get('/api/mapcolumns?slug=game').status_code==200
    net.progress._activity('game',phase='Verifying saved functions',done=2,total=4)
    activity=client.get('/api/project-loading?slug=game').json()['verification']
    assert activity['done']==2 and activity['total']==4


def test_close_dialog_identifies_active_workers_without_stopping_them(app, monkeypatch):
    from types import SimpleNamespace
    client, _, manager = app
    def worker(sid, name, slug, state, why=''):
        return SimpleNamespace(id=sid, agent_name=name, label=name, slug=slug, state=state, why=why)
    sessions = {'one': worker('one', 'claude-1', 'resident-evil', 'working'),
                'two': worker('two', 'permuter-2', 'v-rally', 'waiting', 'Waiting for eligible functions'),
                'off': worker('off', 'stopped-3', 'v-rally', 'stopped')}
    monkeypatch.setattr(manager, 'sessions', sessions)
    monkeypatch.setattr(manager, 'running', lambda: ['one', 'two'])
    result = client.get('/api/close').json()
    assert result['busy'] and result['helpers'] == 2
    assert [w['name'] for w in result['workers']] == ['claude-1', 'permuter-2']
    assert result['workers'][0]['project'] == 'resident-evil'
    assert result['workers'][1]['state'] == 'waiting'
    assert result['workers'][1]['why'] == 'Waiting for eligible functions'
    assert isinstance(result['tasks'], list)
    assert sessions['one'].state == 'working'


def test_checkout_frontend_reload_injection_and_guard(app, monkeypatch):
    from decompnet.app import frontend_reload
    client, _, _ = app
    monkeypatch.setattr(frontend_reload, 'enabled', lambda: True)
    monkeypatch.setattr(frontend_reload, 'snapshot', lambda: {'css': 'css1', 'page': 'page1'})
    page = client.get('/').text
    assert 'name="decomp-live-reload" data-css="css1" data-page="page1"' in page
    assert '/assets/js/live-reload.js' in page
    response = client.get('/api/frontend-revision')
    assert response.json() == {'enabled': True, 'css': 'css1', 'page': 'page1'}
    assert response.headers['cache-control'] == 'no-store'
    assert client.get('/api/frontend-revision', headers={'User-Agent': 'unauthorized'}).status_code == 401
    monkeypatch.setattr(frontend_reload, 'enabled', lambda: False)
    assert 'decomp-live-reload' not in client.get('/').text
    assert '/assets/js/live-reload.js' not in client.get('/').text
    assert client.get('/api/frontend-revision').json() == {'enabled': False}


def test_claim_handover_is_explicit_and_cannot_release_another_agent(app):
    # The shared fixture routes use its own network; add a small coordination-only project.
    client, net, _ = app
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES('handover','Handover','','mips',0,'','{}')").lastrowid
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit) VALUES(?, 'handover_f',16,4,'open','')", (pid,))
    human, _ = net.local_agent('you')
    net.claim_row(human, 'handover', 'handover_f')
    response = client.post('/api/claim/handover', json={'slug':'handover','name':'handover_f'}, headers={'X-Decomp':'1'})
    assert response.status_code == 200
    assert not net.store.one('SELECT * FROM claims WHERE function_id IN (SELECT id FROM functions WHERE project_id=?)', pid)
    other, _ = net.local_agent('handover-worker')
    net.claim_row(other, 'handover', 'handover_f')
    response = client.post('/api/claim/handover', json={'slug':'handover','name':'handover_f'}, headers={'X-Decomp':'1'})
    assert response.status_code == 400
    assert net.store.one('SELECT agent_id FROM claims WHERE function_id IN (SELECT id FROM functions WHERE project_id=?)', pid)['agent_id'] == other.id


def test_tool_presets_and_usage_schema_are_available_to_settings(app):
    client, _, _ = app
    response = client.get('/api/agent-tools')
    assert response.status_code == 200
    data = response.json()
    assert {'matching','polishing','investigation','full'} == {p['id'] for p in data['presets']}
    full = next(p for p in data['presets'] if p['id']=='full')
    matching = next(p for p in data['presets'] if p['id']=='matching')
    assert matching['tools'] < full['tools']
    assert all('task_calls' in t and t['schema_chars'] > 0 for t in data['tools'])


def test_global_tool_preset_route_applies_and_validates_atomically(app):
    from decompnet.app import tool_policy
    client, net, _ = app
    previous = net.store.one('SELECT value FROM settings WHERE key=?', tool_policy.KEY)
    try:
        response = client.post('/api/agent-tools', json={'preset':'matching'}, headers={'X-Decomp':'1'})
        assert response.status_code == 200
        tools = {t['name']:t for t in response.json()['tools']}
        assert tools['compile_and_diff']['enabled'] and tools['compile_patch']['enabled']
        assert not tools['try_compiler']['enabled']
        response = client.post('/api/agent-tools', json={'preset':'invalid'}, headers={'X-Decomp':'1'})
        assert response.status_code == 400
        assert 'try_compiler' in tool_policy.disabled(net)
    finally:
        with net.store.tx() as db:
            if previous:
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (tool_policy.KEY, previous['value']))
            else:
                db.execute('DELETE FROM settings WHERE key=?', (tool_policy.KEY,))


def test_focused_agent_start_passes_scope_and_duration_and_rejects_multiple(app,monkeypatch):
    from .test_loading import tiny_game

    client,net,manager=app
    tiny_game(net)
    seen=[]
    monkeypatch.setattr(manager,'start',lambda kind,slug,count=1,options=None:seen.append(options) or ['focused'])
    response=client.post('/api/agents/start',headers={'X-Decomp':'1'},json={
        'kind':'codex','slug':'game','options':{'focus_function':'f0','focus_minutes':15,'model':'test'}})
    assert response.status_code==200,response.text
    assert seen[0]['focus_function']=='f0' and seen[0]['focus_minutes']==15
    response=client.post('/api/agents/start',headers={'X-Decomp':'1'},json={
        'kind':'codex','slug':'game','count':2,'options':{'focus_function':'f0','focus_minutes':15}})
    assert response.status_code==400
    assert len(seen)==1


def test_compiler_suggestion_background_reports_progress_and_reuses_job(app, monkeypatch):
    client, net, *_ = app
    entered, release = threading.Event(), threading.Event()
    calls, saves = [], []
    def resolve(slug, sid, apply, log):
        calls.append((slug, sid, apply))
        log('Checking 2 matched functions before changing compiler settings.')
        entered.set()
        assert release.wait(5)
        log('Compiler checks passed; settings and scores saved.')
    monkeypatch.setattr(net, 'resolve_suggestion', resolve)
    monkeypatch.setattr(net.progress, 'sync', lambda *a, **kw: saves.append(a) if a[0] == 'suggestion-progress' else None)
    monkeypatch.setattr(net, 'overview', lambda slug: {'slug': slug, 'suggestions': []})
    payload = {'slug': 'suggestion-progress', 'id': 1, 'action': 'apply', 'background': True}
    try:
        first = client.post('/api/suggestion', json=payload, headers={'X-Decomp': '1'})
        assert first.status_code == 200
        assert entered.wait(2)
        job = first.json()['job']
        assert saves == []  # the initial request must not wait for project-wide disk synchronization
        second = client.post('/api/suggestion', json=payload, headers={'X-Decomp': '1'})
        assert second.json()['job'] == job
        status = client.get('/api/job', params={'id': job}).json()
        assert status['state'] == 'running'
        assert any('Checking 2 matched' in line for line in status['log'])
    finally:
        release.set()
    for _ in range(100):
        status = client.get('/api/job', params={'id': job}).json()
        if status['state'] != 'running':
            break
        time.sleep(.01)
    assert status['state'] == 'done' and status['result']['slug'] == payload['slug']
    assert calls == [(payload['slug'], 1, True)] and saves == [(payload['slug'],)]


def test_failed_compiler_suggestion_keeps_job_error_visible(app, monkeypatch):
    from decompnet.network import NetworkError
    client, net, *_ = app
    def reject(*args, **kwargs):
        raise NetworkError('One matched function would no longer match. Narrow the range.')
    monkeypatch.setattr(net, 'resolve_suggestion', reject)
    response = client.post('/api/suggestion', json={'slug': 'failed-suggestion', 'id': 2,
                           'action': 'apply', 'background': True}, headers={'X-Decomp': '1'})
    assert response.status_code == 200
    for _ in range(100):
        status = client.get('/api/job', params={'id': response.json()['job']}).json()
        if status['state'] != 'running':
            break
        time.sleep(.01)
    assert status['state'] == 'error' and 'Narrow the range' in status['error']


def test_join_existing_project_passes_stored_copy_to_normal_join(app, tmp_path, monkeypatch):
    from decompnet.app import collab
    client, net, *_ = app
    stored = tmp_path / 'game.z64'
    stored.write_bytes(b'own copy')
    calls = []
    monkeypatch.setattr(collab,'project_game',lambda n, slug: stored if slug == 'existing' else None)
    def join(n, link, name, game, log):
        calls.append((link,name,game))
        return 'joined-existing'
    monkeypatch.setattr(collab,'join_room',join)
    response = client.post('/api/collab/join?project=existing&confirmed=1&link=http://host/join/open&name=sam',
                           content=b'', headers={'X-Decomp':'1'})
    assert response.status_code == 200
    for _ in range(100):
        job = client.get('/api/job',params={'id':response.json()['job']}).json()
        if job['state'] != 'running':
            break
        time.sleep(.01)
    assert job['state'] == 'done' and job['result']['slug'] == 'joined-existing'
    assert calls == [('http://host/join/open','sam',stored)] and stored.is_file()
    rejected = client.post('/api/collab/join?project=existing&link=http://host/join/open&name=sam',
                           content=b'', headers={'X-Decomp':'1'})
    assert rejected.status_code == 400


def test_room_retry_all_processes_snapshot_beyond_five_and_continues_errors(app, monkeypatch, tmp_path):
    from decompnet.app import room_matches
    from decompnet.network import NetworkError
    client, net, *_ = app
    slug='retry-all-room'
    with net.store.tx() as db:
        pid=db.execute("INSERT INTO projects(slug,root,config) VALUES(?,?,'{\"kind\":\"rom\"}')",(slug,str(tmp_path))).lastrowid
    rows=[{'key':f'{room_matches.PREFIX}{pid}:{i}','function':f'f{i}','direction':'host'} for i in range(8)]
    monkeypatch.setattr(room_matches,'pending',lambda *args:rows)
    calls=[]
    def retry(n,s,key, **kwargs):
        calls.append(key)
        if key==rows[1]['key']:raise NetworkError('Compiler unavailable')
        return {'matched':True}
    monkeypatch.setattr(room_matches,'retry',retry)
    response=client.post('/api/collab/matches',json={'slug':slug,'all':True},headers={'X-Decomp':'1'})
    assert response.status_code==200
    for _ in range(100):
        job=client.get('/api/job',params={'id':response.json()['job']}).json()
        if job['state']!='running':break
        time.sleep(.01)
    assert job['state']=='done' and {k:job['result'][k] for k in ('matched','failed','total')}=={'matched':7,'failed':1,'total':8}
    assert len(job['result']['results'])==8
    assert job['progress']['results'][1]['reason']=='Compiler unavailable'
    assert job['progress']['results'][0]['matched'] is True
    assert calls==[r['key'] for r in rows]
    assert job['progress']['finished']==8 and job['progress']['total']==8
    calls.clear()
    response=client.post('/api/collab/matches',json={'slug':slug,'key':rows[0]['key']},headers={'X-Decomp':'1'})
    for _ in range(100):
        job=client.get('/api/job',params={'id':response.json()['job']}).json()
        if job['state']!='running':break
        time.sleep(.01)
    assert job['state']=='done' and calls==[rows[0]['key']]


def test_background_work_routes_are_guarded(app):
    client, *_=app
    endpoint='/api/background-work'
    assert client.get(endpoint, headers={'User-Agent':'external'}).status_code==401
    assert client.get(endpoint).status_code==200
    snapshot=client.get(endpoint).json()
    assert 'cpu_note' in snapshot and 'threads' in snapshot
    assert client.post(endpoint,json={'kind':'service','key':'x'}).status_code==403
    assert client.post(endpoint,json={'kind':'service','key':'x'},headers={'X-Decomp':'1'}).status_code==400


def test_brief_project_list_skips_progress_summaries(app, monkeypatch, tmp_path):
    client, net, _ = app
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                   ('brief-mcp-test', 'Brief MCP test', 'mips', str(tmp_path), '{}'))
    monkeypatch.setattr(net, 'summary', lambda *args:pytest.fail('MCP picker must not calculate project progress'))
    response=client.get('/api/projects?brief=1')
    assert response.status_code==200
    assert {'slug':'brief-mcp-test','name':'Brief MCP test'} in response.json()
    assert client.get('/api/projects?brief=1', headers={'User-Agent':'external'}).status_code==401


def test_manual_mcp_catalog_uses_read_only_ui_snapshot(app, monkeypatch, tmp_path):
    from decompnet.app import tools
    client, net, _=app
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                   ('catalog-snapshot', 'Catalog', 'mips', str(tmp_path), '{}'))
    def names(*args):
        assert net.progress.viewing
        return ['project_info']
    monkeypatch.setattr(tools, 'project_tools', names)
    response=client.get('/api/manual-mcp?slug=catalog-snapshot')
    assert response.status_code==200
    assert [t['name'] for t in response.json()['tools']]==['project_info']


def test_retry_paused_route_is_bounded_guarded_and_does_not_start_helpers(tmp_path):
    from decompnet import claim_stalls

    from .test_loading import tiny_game
    net=RomNetwork(tmp_path/'decomp.db',tmp_path);tiny_game(net)
    mcp,manager,security,_=build(net,tmp_path,PORT,SECRET)
    session=manager._record('game','codex','worker','Worker',{'model':'model-a'})
    agent,_=net.local_agent(session.agent_name)
    with net.store.tx() as db:db.execute("UPDATE functions SET best_source='void f(){}',best_score=10")
    for fn in net.store.all('SELECT * FROM functions'):claim_stalls.released(net,fn,agent=agent,exhausted=True)
    client=TestClient(mcp.streamable_http_app(transport_security=security,host='127.0.0.1'),base_url=f'http://127.0.0.1:{PORT}',headers=WINDOW)
    payload={'id':session.id,'limit':1}
    assert client.post('/api/agents/retry-paused',json=payload).status_code==403
    assert client.post('/api/agents/retry-paused',json={**payload,'limit':True},headers={'X-Decomp':'1'}).status_code==400
    response=client.post('/api/agents/retry-paused',json=payload,headers={'X-Decomp':'1'})
    assert response.status_code==200,response.text
    assert response.json()['resumed']==1
    assert session.runs==0 and not net.store.all('SELECT * FROM claims')
    response=client.post('/api/agents/retry-paused',json={**payload,'background':True},headers={'X-Decomp':'1'})
    job_id=response.json()['job']
    import time
    for _ in range(200):
        job=client.get('/api/job',params={'id':job_id}).json()
        if job['state'] in {'done','error'}:break
        time.sleep(0.01)
    assert job['state']=='done',job
    assert 'resumed' in job['result']
    assert any('checked' in line.lower() for line in job['log'])
    assert client.post('/api/agents/retry-paused',json={'id':'missing'},headers={'X-Decomp':'1'}).status_code==400


def test_map_encoding_runs_in_worker_reuses_bytes_and_invalidates_on_edit(app, monkeypatch):
    from decompnet.app.routes import state
    from tests.test_loading import tiny_game
    client, net, *_ = app
    tiny_game(net,'wire-cache')
    worker_threads=[]
    def rows(slug):
        worker_threads.append(threading.get_ident())
        return [dict(name='wire-fn',vram=1,unit='',size=4,
                     best=net.store.one("SELECT SUM(attempts) FROM functions WHERE project_id=(SELECT id FROM projects WHERE slug=?)",slug)[0])]
    monkeypatch.setattr(net,'function_map',rows)
    encoded=[]
    dumps=state.json.dumps
    def encode(value,*args,**kwargs):
        if isinstance(value,dict) and 'sig' in value and 'rows' in value:
            encoded.append(threading.get_ident())
        return dumps(value,*args,**kwargs)
    monkeypatch.setattr(state.json,'dumps',encode)
    monkeypatch.setenv('DECOMP_PERF','1')
    first=client.get('/api/map?slug=wire-cache').json()
    second=client.get('/api/map?slug=wire-cache')
    assert second.json()==first
    assert encoded==worker_threads and len(encoded)==1
    assert 'encode;dur=' in second.headers['Server-Timing']
    with net.store.tx() as db:
        db.execute("UPDATE functions SET attempts=attempts+1 WHERE project_id=(SELECT id FROM projects WHERE slug='wire-cache')")
    assert client.get('/api/map?slug=wire-cache').json()['rows'] != first['rows']
    assert len(encoded)==2


def test_agent_blocker_review_routes_are_guarded_and_acknowledgment_keeps_status(app,tmp_path,monkeypatch):
    client,net,_=app
    slug='blocker-review-test'
    agent,_=net.local_agent('review-worker')
    with net.store.tx() as db:
        pid=db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                       (slug,'Review test',str(tmp_path),json.dumps({'kind':'rom','platform':'n64'}))).lastrowid
        fid=db.execute("INSERT INTO functions(project_id,name,status) VALUES(?,'review_fn','open')",(pid,)).lastrowid
        for _ in range(3):
            db.execute('INSERT INTO notes(project_id,function_id,agent_id,text,created_at) VALUES(?,?,?,?,?)',
                       (pid,fid,agent.id,'No compile: unresolved asm concern',time.time()))
    response=client.get('/api/agent-reviews',params={'slug':slug})
    assert response.status_code==200 and response.json()['items'][0]['historical']
    assert client.post('/api/agent-review',json={'slug':slug,'function':'review_fn'}).status_code==403
    monkeypatch.setattr(net.progress,'sync',lambda *args,**kwargs:pytest.fail('Acknowledging evidence must not reverify project code'))
    response=client.post('/api/agent-review',json={'slug':slug,'function':'review_fn'},headers={'X-Decomp':'1'})
    assert response.status_code==200 and response.json()['reviewed']
    assert client.get('/api/agent-reviews',params={'slug':slug}).json()['items']==[]
    assert net.store.one('SELECT status FROM functions WHERE id=?',fid)['status']=='open'


def test_data_region_metadata_routes_are_guarded_and_do_not_reverify(app,tmp_path,monkeypatch):
    from decompnet.app import data_regions
    client,net,_=app
    with net.store.tx() as db:
        pid=db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                       ('region-test','Review',str(tmp_path),json.dumps({'kind':'rom','platform':'n64'}))).lastrowid
        db.execute("INSERT INTO functions(project_id,name,status,vram,rom_offset,size,unit) VALUES(?,'table_entry','open',4096,100,8,'image')",(pid,))
    agent,_=net.local_agent('region-worker')
    net.claim_row(agent,'region-test','table_entry')
    data_regions.report(net,agent,'region-test','table_entry','0x1000','0x1008','Pointer table hypothesis')
    result=client.get('/api/data-regions?slug=region-test')
    assert result.status_code==200
    key=result.json()['items'][0]['id']
    body={'slug':'region-test','id':key,'action':'pause'}
    assert client.post('/api/data-regions',json=body).status_code==403
    assert client.post('/api/knowledge/verify',json={'slug':'region-test','id':1}).status_code==403
    monkeypatch.setattr(net.progress,'sync',lambda *args,**kw:pytest.fail('Review metadata must not reverify code'))
    assert client.post('/api/data-regions',json=body,headers={'X-Decomp':'1'}).status_code==200
    assert net.store.one("SELECT status FROM functions WHERE name='table_entry' AND project_id=?",pid)['status']=='open'


def test_project_overview_serializes_running_source_preparation(app,monkeypatch):
    from decompnet.app.permute import State
    from decompnet.network import Agent
    from tests.test_loading import tiny_game
    client,net,*_=app
    slug='source-worker-json'
    tiny_game(net,slug)
    state=State(variants=12,matched=1)
    state.preparation_agent=Agent(1,'autopilot',None)
    state.source_measurement={'pair':('private body','private header')}
    monkeypatch.setitem(net.permuter.jobs,slug,state)
    monkeypatch.setattr(net,'warm_briefs_soon',lambda *a:None)
    response=client.get('/api/project',params={'slug':slug})
    assert response.status_code==200,response.text
    assert response.json()['permuter']['variants']==12
    assert response.json()['permuter']['matched']==1
    assert 'preparation_agent' not in response.text and 'private body' not in response.text


def test_batch_naming_route_is_authenticated_and_previews_without_mutation(app, monkeypatch):
    from decompnet.app import symbol_names
    client, _, _ = app
    calls = []
    monkeypatch.setattr(symbol_names, 'prepare', lambda net, slug, rows: calls.append((slug, rows)) or [('old', 'new')])
    payload = {'slug': 'game', 'preview': True, 'names': [{'old': 'old', 'new': 'new', 'address': 1}]}
    assert client.post('/api/symbol-names', json=payload,
                       headers={'X-Decomp': '1', 'User-Agent': 'outside'}).status_code in (401, 403)
    assert not calls
    response = client.post('/api/symbol-names', json=payload, headers={'X-Decomp': '1'})
    assert response.status_code == 200 and response.json() == {'renames': 1}
    assert calls == [('game', payload['names'])]


def test_batch_compiler_suggestions_reuse_background_job(app, monkeypatch):
    from decompnet.app import function_profile_suggestions
    client, net, *_ = app
    entered, release = threading.Event(), threading.Event()
    calls=[]
    def accept(net, slug, log):
        calls.append(slug)
        entered.set()
        assert release.wait(5)
        return {'applied':1,'skipped':0,'results':[], 'overview':{}}
    monkeypatch.setattr(function_profile_suggestions,'accept_all',accept)
    monkeypatch.setattr(net.progress,'sync',lambda *a,**kw:pytest.fail('request must not sync a background batch'))
    payload={'slug':'batch-suggestions','action':'apply-all','background':True}
    try:
        first=client.post('/api/suggestion',json=payload,headers={'X-Decomp':'1'})
        assert first.status_code==200
        assert entered.wait(2)
        second=client.post('/api/suggestion',json=payload,headers={'X-Decomp':'1'})
        assert second.json()['job']==first.json()['job']
        assert calls==['batch-suggestions']
    finally:
        release.set()


def test_match_library_api_is_guarded_and_does_not_expose_source(app):
    client, net, *_ = app
    assert client.get('/api/match-library',headers={'User-Agent':'outside'}).status_code==401
    assert client.post('/api/match-library',json={}).status_code==403
    data=client.get('/api/match-library').json()
    assert data['functions']>=0 and isinstance(data['items'],list)
    assert all('source' not in item and 'header' not in item for item in data['items'])
    response=client.post('/api/match-library',json={},headers={'X-Decomp':'1'})
    assert response.status_code==200 and response.json()['job']


def test_project_autosave_controls_are_exposed_in_background_api(app, monkeypatch):
    from types import SimpleNamespace
    client, net, *_ = app
    calls = []
    auto = SimpleNamespace(snapshot=lambda:[dict(key='game',state='paused',thread_id=None,detail='Paused')],
                           pause=lambda *args:calls.append(args))
    monkeypatch.setattr(net, 'autosave', auto)
    rows = client.get('/api/background-work').json()['items']
    assert any(r['kind']=='autosave' and r['project']=='game' and r['state']=='paused' for r in rows)
    for action in ('stop','resume'):
        result = client.post('/api/background-work', headers={'X-Decomp':'1'},
                             json={'kind':'autosave','key':'game','action':action})
        assert result.status_code==200
    assert calls == [('game',), ('game',False)]


@pytest.mark.parametrize('hosting',[False,True])
def test_sharing_dialog_reads_while_saved_progress_holds_project_lock(app,monkeypatch,hosting):
    import asyncio

    from decompnet.app import collab
    client,net,*_=app
    slug='sharing-read-'+str(hosting).lower()
    cfg={'kind':'rom','rom':{},'collab':{'mode':'address','address':'https://rooms.example.invalid','port':6481}}
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                   (slug,slug,str(net.workspace),json.dumps(cfg)))
    checked=[]
    def check_read(label):
        assert net.progress.viewing
        try:asyncio.get_running_loop()
        except RuntimeError:pass
        else:pytest.fail('Sharing helper ran on the HTTP event loop')
        checked.append(label)
    project_row=net.project_row
    def read_project(name):
        if name==slug:check_read('project')
        return project_row(name)
    monkeypatch.setattr(net,'project_row',read_project)
    def addresses():
        check_read('addresses')
        return [{'label':'LAN','address':'http://192.0.2.1:6481'}]
    monkeypatch.setattr(collab,'local_addresses',addresses)
    monkeypatch.setattr(collab,'cloudflared_available',lambda:False)
    if hosting:
        class Hosted:
            def members(self,refresh=True):
                check_read('members');return []
            @property
            def settings(self):
                check_read('settings');return {'visibility':'private','approve':False}
            def pending(self):
                check_read('pending');return []
        monkeypatch.setitem(net.rooms,slug,Hosted())
    # A different request thread must answer without acquiring this main test thread's
    # project lock. Checking viewing above also prevents an accidental synchronous scan.
    with net.progress._project_lock(slug):
        response=client.get('/api/collab?slug='+slug)
    assert response.status_code==200
    data=response.json()
    assert data['sharing']==hosting and data['address']==cfg['collab']['address']
    assert data['addresses'][0]['label']=='LAN'
    assert 'project' in checked and 'addresses' in checked
    if hosting:assert {'members','settings','pending'}<=set(checked)


def test_sharing_dialog_reports_missing_project(app):
    client,*_=app
    response=client.get('/api/collab?slug=missing-sharing-project')
    assert response.status_code==400
    assert 'no project' in response.json()['error']


def test_database_cleanup_routes_validate_preview_and_schedule_guarded_jobs(app):
    from decompnet.app import database_cleanup
    client,net,_=app
    headers={'X-Decomp':'1'}
    assert client.get('/api/database-cleanup',headers={'User-Agent':'outside'}).status_code==401
    assert client.post('/api/database-cleanup',json={'action':'start'}).status_code==403
    for value in [{'action':'start','removed':False,'before':''},
                  {'action':'save','automatic':True,'before':''},
                  {'action':'save','before':'3000-01-01'}]:
        assert client.post('/api/database-cleanup',json=value,headers=headers).status_code==400
    data=client.post('/api/database-cleanup',json={'action':'preview','removed':True},headers=headers).json()
    assert data['messages']>=0 and data['sessions']>=0 and 'disk_bytes' in data
    value={'removed':True,'before':'2000-01-01','automatic':True}
    result=client.post('/api/database-cleanup',json=dict(value,action='save'),headers=headers)
    assert result.status_code==200 and database_cleanup.saved(net)==value
    assert client.get('/api/database-cleanup').json()['options']==value
    response=client.post('/api/database-cleanup',json={'action':'start','removed':True},headers=headers)
    assert response.status_code==200 and response.json()['job']
    database_cleanup.save(net,{'removed':True,'before':'','automatic':False})


def test_header_storage_routes_guard_preview_and_maintenance_actions(app,monkeypatch):
    from decompnet.app import header_cleanup
    client,net,*_=app
    headers={'X-Decomp':'1'}
    assert client.get('/api/header-storage',headers={'User-Agent':'outside'}).status_code==401
    assert client.post('/api/header-storage',json={'action':'headers'}).status_code==403
    data=client.get('/api/header-storage').json()
    assert 'stored_bytes' in data and 'logical_bytes' in data
    preview=client.post('/api/header-storage',json={'action':'preview'},headers=headers)
    assert preview.status_code==200 and 'unused_versions' in preview.json()
    calls=[]
    original=header_cleanup.start
    def start(net,jobs,action):
        if action not in ('headers','compact'):return original(net,jobs,action)
        calls.append(action)
        return 'synthetic-job'
    monkeypatch.setattr(header_cleanup,'start',start)
    for action in ('headers','compact'):
        assert client.post('/api/header-storage',json={'action':action},headers=headers).json()=={'job':'synthetic-job'}
    assert calls==['headers','compact']
    assert client.post('/api/header-storage',json={'action':'unknown'},headers=headers).status_code==400


def test_dismiss_compiler_inbox_passes_explicit_project_snapshot(app, monkeypatch):
    from decompnet.app import function_profile_suggestions
    client, net, *_ = app
    calls = []
    monkeypatch.setattr(function_profile_suggestions, 'dismiss_all',
                        lambda net, slug, ids:calls.append((slug, ids)) or 2)
    monkeypatch.setattr(net, 'overview', lambda slug:{'slug':slug, 'suggestions':[]})
    response = client.post('/api/suggestion', json={'slug':'inbox', 'action':'dismiss-all', 'ids':[4, 7]},
                           headers={'X-Decomp':'1'})
    assert response.status_code == 200
    assert response.json() == {'dismissed':2, 'overview':{'slug':'inbox', 'suggestions':[]}}
    assert calls == [('inbox', [4, 7])]


def test_candidate_storage_routes_guard_validate_and_keep_history_scoped(app):
    client,net,*_=app
    headers={'X-Decomp':'1'}
    assert client.get('/api/database-storage',headers={'User-Agent':'outside'}).status_code==401
    assert client.post('/api/database-storage',json={'action':'pack'}).status_code==403
    assert client.post('/api/database-storage',json={'action':'prune','days':0},headers=headers).status_code==400
    data=client.get('/api/database-storage').json()
    assert 'stored_bytes' in data and 'history_plan' in data
    assert any('COVERING INDEX' in plan for plan in data['history_plan'])
    assert client.post('/api/database-storage',json={'action':'save','days':90,'automatic':False},headers=headers).status_code==200
    with net.store.tx() as db:
        pid=db.execute("INSERT INTO projects(slug,name,root,config) VALUES('payload-history','History','/scratch','{}')").lastrowid
        fid=db.execute("INSERT INTO functions(project_id,name) VALUES(?,'target')",(pid,)).lastrowid
        other=db.execute("INSERT INTO functions(project_id,name) VALUES(?,'other')",(pid,)).lastrowid
        a=db.execute("INSERT INTO attempts(function_id,source,score,compiled) VALUES(?,'candidate',12,1)",(fid,)).lastrowid
    query='/api/attempt-history?slug=payload-history&function=target'
    page=client.get(query).json()
    assert page['attempts'][0]['id']==a and page['attempts'][0]['source_available']
    assert client.get(query+'&attempt='+str(a)).json()['source']=='candidate'
    assert client.get(query.replace('function=target','function=other')+'&attempt='+str(a)).status_code==400
    assert client.get(query+'&before=invalid').status_code==400
    assert other!=fid


def test_match_library_detail_and_name_routes(app,monkeypatch):
    client,net,*_=app
    calls=[]
    monkeypatch.setattr(net.match_library,'detail',lambda fid:{'id':fid,'name':'shared','occurrences':[]})
    monkeypatch.setattr(net.match_library,'set_name',lambda fid,name:calls.append((fid,name)) or name)
    assert client.get('/api/match-library?id=7').json()['id']==7
    assert client.post('/api/match-library',json={'action':'name','id':7,'name':'new_name'}).status_code==403
    reply=client.post('/api/match-library',headers={'X-Decomp':'1'},json={'action':'name','id':7,'name':'new_name'})
    assert reply.json()=={'name':'new_name'}
    assert calls==[(7,'new_name')]
    assert client.get('/api/match-library?id=invalid').status_code==400


def test_deep_pass_controls_do_not_wait_for_progress_sync(app,monkeypatch):
    client,net,_=app
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,arch,root,config) VALUES('example','Example','mips','','{}')")
    def blocked(*args,**kwargs):pytest.fail('deep-pass controls must not synchronize the project')
    monkeypatch.setattr(net.progress,'sync',blocked)
    def snapshot(slug,*args):
        assert net.progress.viewing
        net.project_row(slug)
        return {'job':'local-search'}
    for method in ('start','stop','status'):
        monkeypatch.setattr(net.deep_sweeps,method,snapshot)
    assert client.get('/api/permuter/pass?slug=example').json()=={'job':'local-search'}
    for action in ('start','stop'):
        response=client.post('/api/permuter/pass',json={'slug':'example','action':action},headers={'X-Decomp':'1'})
        assert response.status_code==200 and response.json()=={'job':'local-search'}


def test_repair_evidence_settings_routes_validate_and_persist(app):
    client,net,*_=app
    from decompnet.repair_corpus import DEFAULTS, read_options
    headers={'X-Decomp':'1'}
    chosen=dict(enabled=False,automatic_limit=0,manual_limit=12)
    action=dict(action='save-repair-evidence',**chosen)
    assert client.post('/api/database-storage',json=action).status_code==403
    for bad in ({'enabled':'yes'},{'automatic_limit':-1},{'manual_limit':1001}):
        assert client.post('/api/database-storage',json={**action,**bad},headers=headers).status_code==400
    try:
        response=client.post('/api/database-storage',json=action,headers=headers)
        assert response.status_code==200 and response.json()['repair_evidence']==chosen
        assert client.get('/api/database-storage').json()['repair_evidence']==chosen
        assert read_options(net.store)==chosen
    finally:
        client.post('/api/database-storage',json=dict(action='save-repair-evidence',**DEFAULTS),headers=headers)


def test_repair_evidence_database_download_is_guarded_and_standalone(app,tmp_path):
    import sqlite3
    client,net,*_=app
    archive=net.store.conn.repair_archive
    key=archive.put('{"case":"export-test"}',{'format':1,'evidence':{'source':'frozen source'}})
    assert client.get('/api/repair-examples/database',headers={'User-Agent':'outside'}).status_code==401
    with net.store.tx() as db:
        pid=db.execute("INSERT INTO projects(slug,root,config) VALUES('evidence-export-review',?,'{}')",(str(tmp_path),)).lastrowid
        fid=db.execute("INSERT INTO functions(project_id,name) VALUES(?,'example')",(pid,)).lastrowid
        db.execute("INSERT INTO repair_examples(project_id,function_id,created_at,evidence,explanation) VALUES(?,?,0,?,?)",(pid,fid,'{\"after\":{}}','Useful explanation for corpus review'))
    response=client.get('/api/repair-examples/database')
    assert response.status_code==200
    path=tmp_path/'download.sqlite3';path.write_bytes(response.content)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute('SELECT 1 FROM cases WHERE hash=?',(key,)).fetchone()
        assert db.execute("SELECT explanation FROM reviews WHERE project='evidence-export-review'").fetchone()[0]=='Useful explanation for corpus review'


def test_settings_job_discovery_is_guarded_small_and_independent_of_database_stats(app,monkeypatch):
    from decompnet.app import header_cleanup
    client,net,*_=app
    assert client.get('/api/settings-jobs',headers={'User-Agent':'outside'}).status_code==401
    captured={}
    def start(net,jobs,action):
        captured['jobs']=jobs
        with jobs._lock:
            jobs.jobs['settings-discovery-test']={'id':'settings-discovery-test','kind':'Database compaction',
                'state':'running','progress':{'phase':'Compacting database'},'log':['large private log'],
                'result':{'large':'result'}}
        return 'settings-discovery-test'
    monkeypatch.setattr(header_cleanup,'start',start)
    client.post('/api/header-storage',json={'action':'compact'},headers={'X-Decomp':'1'})
    monkeypatch.setattr(net.store,'one',lambda *_:(_ for _ in ()).throw(AssertionError('database access')))
    try:
        data=client.get('/api/settings-jobs').json()
        assert data['headers']=={'id':'settings-discovery-test','kind':'Database compaction','state':'running',
                                 'progress':{'phase':'Compacting database'}}
        captured['jobs'].jobs['settings-discovery-test']['state']='done'
        assert client.get('/api/settings-jobs').json()['headers'] is None
    finally:
        captured['jobs'].jobs.pop('settings-discovery-test',None)


def test_provider_retry_route_is_guarded_and_does_not_spawn_or_stop_workers(tmp_path):
    net=RomNetwork(tmp_path/'app.db',tmp_path)
    mcp,manager,security,_=build(net,tmp_path,PORT,SECRET)
    net.autosave.close()
    client=TestClient(mcp.streamable_http_app(transport_security=security,host='127.0.0.1'),
                      base_url=f'http://127.0.0.1:{PORT}',headers=WINDOW)
    s=manager._record('example','claude','test-provider','Test provider',{})
    s.state='waiting';s.provider_waiting=True
    path='/api/agents/retry-provider'
    assert client.post(path,json={'id':s.id}).status_code==403
    response=client.post(path,json={'id':s.id},headers={'X-Decomp':'1'})
    assert response.status_code==200 and response.json()=={'requested':True}
    assert s.provider_retry_requested and not s.stop.is_set() and s.thread is None
    s.provider_waiting=False
    assert client.post(path,json={'id':s.id},headers={'X-Decomp':'1'}).status_code==400

@pytest.mark.parametrize('path', ['/api/try', '/api/note', '/api/release'])
def test_foreground_routes_do_not_wait_for_saved_recheck_publication(app, tmp_path, monkeypatch, path):
    from .test_loading import tiny_game

    client, net, _ = app
    slug = 'foreground-' + path.rsplit('/', 1)[-1]
    tiny_game(net, slug)
    folder = tmp_path/'saved'
    folder.mkdir()
    from decompnet.app.progress import _Recheck
    active = _Recheck(folder, net.progress._signature(folder), ready=True)
    net.progress.rechecking[slug] = active
    def attempt(*args):
        assert net.progress.local.foreground
        return {'score': 0, 'matched': True, 'text': 'controlled exact verdict'}
    monkeypatch.setattr(net, 'attempt', attempt)
    name = net.store.one('SELECT name FROM functions WHERE project_id=(SELECT id FROM projects WHERE slug=?)', slug)['name']
    try:
        response = client.post(path, headers={'X-Decomp':'1'}, json={'slug':slug, 'name':name,
                               'function':name, 'source':'candidate', 'text':'post-match notes'})
        assert response.status_code == 200, response.text
        assert net.progress.rechecking[slug] is active
    finally:
        net.progress.rechecking.pop(slug, None)


def test_push_route_requires_write_header_and_does_not_sync_progress(app, monkeypatch):
    from decompnet.app import repository_push
    client, net, _ = app
    calls = []
    monkeypatch.setattr(repository_push, 'status', lambda net, slug, folder: {'unpushed':3, 'destination':'origin/main'})
    def push(net, slug, folder, log):
        calls.append((slug, folder))
        return {'pushed':True}
    monkeypatch.setattr(repository_push, 'run', push)
    monkeypatch.setattr(net.progress, 'sync', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('Push must not sync or commit progress')))
    assert client.get('/api/repository-push?slug=game&folder=/repo').json()['unpushed'] == 3
    assert client.post('/api/repository-push', json={'slug':'game','folder':'/repo'}).status_code == 403
    response = client.post('/api/repository-push', headers={'X-Decomp':'1'}, json={'slug':'game','folder':'/repo'})
    assert response.status_code == 200
    job = response.json()['job']
    for _ in range(50):
        result = client.get('/api/job?id='+job).json()
        if result['state'] in ('done','error'):break
        time.sleep(.01)
    assert result['state'] == 'done' and result['result']['pushed']
    assert calls == [('game','/repo')]


def test_reopening_nonmatch_trial_reuses_job_for_same_project_and_profile(app,monkeypatch):
    from decompnet.app import compiler_default_test
    client,net,*_=app
    release=threading.Event();entered=threading.Event()
    def trial(*args,**kwargs):
        entered.set()
        assert release.wait(5)
        return {'checked':0}
    monkeypatch.setattr(compiler_default_test,'run',trial)
    body={'slug':'nonmatch-discovery','action':'nonmatches','profile':{'toolchain':'example','flags':['-O2']}}
    try:
        first=client.post('/api/compiler/test-default',json=body,headers={'X-Decomp':'1'}).json()
        assert entered.wait(3)
        second=client.post('/api/compiler/test-default',json=body,headers={'X-Decomp':'1'}).json()
        assert first['job']==second['job']
        other=client.post('/api/compiler/test-default',json={**body,'profile':{'toolchain':'example','flags':['-O1']}},headers={'X-Decomp':'1'}).json()
        assert other['job']!=first['job']
    finally:release.set()


def test_repository_setup_requires_write_guard_and_runs_as_job(app, monkeypatch):
    from decompnet.app import repository_setup
    client, net, _ = app
    calls = []
    monkeypatch.setattr(repository_setup, 'status', lambda net, slug: {'repository':False})
    def setup(net, slug, action, origin, log, **kwargs):
        calls.append((slug, action, origin))
        return {'repository':True}
    monkeypatch.setattr(repository_setup, 'run', setup)
    assert client.get('/api/repository-setup?slug=game').json() == {'repository':False}
    data = {'slug':'game', 'action':'origin', 'origin':'git@example.invalid:project.git'}
    assert client.post('/api/repository-setup', json=data).status_code == 403
    response = client.post('/api/repository-setup', json=data, headers={'X-Decomp':'1'})
    assert response.status_code == 200
    for _ in range(50):
        result = client.get('/api/job?id='+response.json()['job']).json()
        if result['state'] in ('done', 'error'):break
        time.sleep(.01)
    assert result['state'] == 'done' and result['result']['repository']
    assert calls == [('game', 'origin', data['origin'])]


def test_close_window_keep_working_explains_background_state_in_terminal(app,monkeypatch,capsys):
    client,*_=app
    closed=[]
    monkeypatch.setattr(window,'close_app',lambda:closed.append(True))
    response=client.post('/api/close',json={'keep':True},headers={'X-Decomp':'1'})
    assert response.status_code==200 and closed==[True]
    assert 'keep working in the background' in capsys.readouterr().err


def test_auto_push_preference_requires_write_guard_and_valid_threshold(app, monkeypatch):
    from decompnet.app import auto_push
    client, net, _ = app
    calls = []
    def configure(net, slug, enabled, threshold, folder):
        calls.append((slug, enabled, threshold, folder))
        return {'enabled':enabled, 'threshold':threshold}
    monkeypatch.setattr(auto_push, 'configure', configure)
    data = {'slug':'game', 'enabled':True, 'folder':'/repo'}
    assert client.post('/api/repository-auto-push', json=data).status_code == 403
    response = client.post('/api/repository-auto-push', json=data, headers={'X-Decomp':'1'})
    assert response.status_code == 200 and response.json()['threshold'] == 10
    assert calls == [('game', True, 10, '/repo')]


def test_tools_catalog_exposes_wibo_on_mac(app, monkeypatch):
    client, net, manager = app
    monkeypatch.setattr(toolchains, 'current_platform', lambda:'macos-arm64')
    rows = client.get('/api/state').json()['toolchains']
    row = next(t for t in rows if t['id'] == 'wibo')
    assert row['supported'] and row['kind'] == 'tool'
    assert 'Windows' in row['use'] and 'macOS' in row['use']


def test_push_is_queued_behind_sync_deduplicated_and_discoverable(app, monkeypatch):
    from decompnet.app import repository_operations, repository_push, repository_push_queue, repository_sync
    client, net, _ = app
    syncing, release, queued = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(net, 'project_row', lambda slug: {'slug':slug})
    def sync(net, slug, folder, log):
        with repository_operations.operation(folder, slug, 'Syncing saved progress', log):
            syncing.set()
            assert release.wait(10)
        return {'synced':True}
    pushed = []
    def push(net, slug, folder, log):
        def output(line):
            log(line)
            queued.set()
        output.progress = log.progress
        with repository_push_queue.turn(folder, slug, output, log.check_cancel):
            pushed.append(slug)
        return {'pushed':True}
    monkeypatch.setattr(repository_sync, 'run', sync)
    monkeypatch.setattr(repository_push, 'run', push)
    monkeypatch.setattr(repository_push, 'status', lambda *a: (_ for _ in ()).throw(AssertionError('Do not delay queued-job discovery with Git status')))
    headers = {'X-Decomp':'1'}
    payload = {'slug':'game','folder':'/repo'}
    try:
        sync_response = client.post('/api/repository-sync', json=payload, headers=headers)
        assert sync_response.status_code == 200 and syncing.wait(5)
        response = client.post('/api/repository-push', json=payload, headers=headers)
        assert response.status_code == 200 and queued.wait(5)
        job = response.json()['job']
        assert client.post('/api/repository-push', json=payload, headers=headers).json()['job'] == job
        active = client.get('/api/repository-push?slug=game&folder=/repo').json()
        assert active['job'] == job and active['slug'] == 'game' and active['folder'] == '/repo'
        snapshot = client.get('/api/job?id='+job).json()
        assert snapshot['state'] == 'running' and snapshot['progress']['queued']
        client.post('/api/job/cancel', json={'id':job}, headers=headers)
        for _ in range(100):
            snapshot = client.get('/api/job?id='+job).json()
            if snapshot['state'] != 'running':break
            time.sleep(.01)
        assert snapshot['state'] == 'cancelled' and not pushed
    finally:
        release.set()


def test_deep_controls_are_discovered_without_loading_statistics(app, monkeypatch):
    client, net, _ = app
    monkeypatch.setattr(net.deep_sweeps, 'status', lambda *a: pytest.fail('Must not read statistics'))
    monkeypatch.setattr(net.deep_sweeps, 'control', lambda slug: {'job':'search-job','state':'running','control_only':True,'project':slug})
    response = client.get('/api/permuter/pass?slug=example&view=control')
    assert response.status_code == 200
    assert response.json()['job'] == 'search-job' and response.json()['control_only']


def test_score_commit_preferences_require_write_guard_and_default_to_ten(app, monkeypatch):
    from decompnet.app import score_commits
    client, net, _ = app
    calls = []
    def configure(net, slug, enabled, threshold, folder):
        calls.append((slug, enabled, threshold, folder))
        return {'enabled':enabled, 'threshold':threshold}
    monkeypatch.setattr(score_commits, 'configure', configure)
    data = {'slug':'game', 'enabled':True, 'folder':'/repo'}
    assert client.post('/api/repository-score-commits', json=data).status_code == 403
    response = client.post('/api/repository-score-commits', json=data, headers={'X-Decomp':'1'})
    assert response.status_code == 200 and response.json()['threshold'] == 10
    assert calls == [('game', True, 10, '/repo')]


@pytest.mark.parametrize('cancel', [False, True])
def test_starting_compiler_comparison_returns_job_without_saved_file_sync(app, monkeypatch, cancel):
    client, net, *_ = app
    slug = 'comparison-start-'+str(cancel)
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                   (slug, 'Comparison', '.', '{}'))
    entered, release = threading.Event(), threading.Event()
    calls = []
    def fork(project):
        assert net.progress.viewing  # Initial mode validation must not restore saved files either.
        return None
    def compare(project, *, log, **options):
        calls.append((project, options))
        log.progress({'phase':'preparation','stage':'Checking installed compiler profiles','elapsed':0,'results':[{'function':'saved'}]})
        entered.set()
        while not release.wait(.01):log.check_cancel()
        log.check_cancel()
        return {'tried':1}
    monkeypatch.setattr(net, 'fork_slug', fork)
    monkeypatch.setattr(net, 'run_compiler_comparison', compare)
    monkeypatch.setattr(net.progress, 'sync', lambda *a, **kw:pytest.fail('job startup must not synchronize files'))
    payload = {'slug':slug,'all_installed':True,'flags_only':True,'auto_apply':True,'untested_only':True}
    headers = {'X-Decomp':'1'}
    job = None
    try:
        response = client.post('/api/compilermap', json=payload, headers=headers)
        assert response.status_code == 200
        job = response.json()['job']
        assert entered.wait(2)
        status = client.get('/api/job', params={'id':job}).json()
        assert status['state'] == 'running'
        assert status['progress']['stage'] == 'Checking installed compiler profiles'
        # A fresh window finds the scoped job without a compiler-map read or saved-file sync.
        monkeypatch.setattr(net, 'compiler_answer', lambda *a, **kw:pytest.fail('discovery must not load the compiler map'))
        discovered = client.get('/api/compilermap', params={'slug':slug, 'view':'comparison'}).json()
        assert discovered['job'] == job and discovered['state'] == 'running'
        assert 'results' not in discovered['progress']
        assert discovered['scope']['slug'] == slug and discovered['scope']['flags_only']
        assert discovered['scope']['auto_apply']
        assert client.get('/api/compilermap', params={'slug':'another-project', 'view':'comparison'}).json() == {'job':None}
        repeated = client.post('/api/compilermap', json={**payload, 'flags_only':False}, headers=headers).json()
        assert repeated['job'] == job and repeated['scope']['flags_only']
        assert calls == [(slug, {'function':'','untested_only':True,'flags_only':True,
                                 'used_profiles_only':False,'auto_apply':True})]
        if cancel:
            assert client.post('/api/job/cancel', json={'id':job}, headers=headers).json()['cancelled']
        else:release.set()
        for _ in range(100):
            status = client.get('/api/job', params={'id':job}).json()
            if status['state'] != 'running':break
            time.sleep(.01)
        assert status['state'] == ('cancelled' if cancel else 'done')
        assert client.get('/api/compilermap', params={'slug':slug, 'view':'comparison'}).json()['state'] == status['state']
        if cancel:assert 'Completed results are saved' in status['error']
        else:assert status['result'] == {'tried':1}
    finally:
        release.set()


def test_synchronous_compiler_map_updates_still_sync_saved_files(app, monkeypatch):
    client, net, *_ = app
    slug = 'comparison-synchronous-update'
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)', (slug,'Comparison','.','{}'))
    saves = []
    monkeypatch.setattr(net, 'compiler_answer', lambda slug, **kw:{'checked':True})
    monkeypatch.setattr(net.progress, 'sync', lambda *a, **kw:saves.append((a, kw)))
    response = client.post('/api/compilermap', json={'slug':slug}, headers={'X-Decomp':'1'})
    assert response.status_code == 200 and response.json() == {'checked':True}
    assert saves == [((slug,), {'force':True})]


def test_agent_metric_reset_runs_off_event_loop_and_returns_boundary(app, monkeypatch):
    import asyncio
    client, net, manager = app
    calls=[]
    def reset(sid):
        with pytest.raises(RuntimeError,match='no running event loop'):asyncio.get_running_loop()
        calls.append(sid)
        return {'metrics_since':123,'metrics_after':456}
    monkeypatch.setattr(manager,'reset_metrics',reset)
    response=client.post('/api/agents/reset-metrics',json={'id':'selected'},headers={'X-Decomp':'1'})
    assert response.status_code==200
    assert response.json()=={'reset':True,'metrics_since':123,'metrics_after':456}
    assert calls==['selected']


def test_single_function_profile_response_does_not_restore_project(app, monkeypatch):
    client, net, *_ = app
    slug = 'single-profile-apply'
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)', (slug,'Profile','.','{}'))
    monkeypatch.setattr(net.progress, 'sync', lambda *a, **kw:pytest.fail('must not restore whole project'))
    calls = []
    def apply(*args, **kwargs):
        calls.append(args)
        return {'matched':True}
    monkeypatch.setattr(net, 'apply_function_compiler', apply)
    response = client.post('/api/compiler', json={'slug':slug,'function':'only',
                           'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}, headers={'X-Decomp':'1'})
    assert response.status_code == 200 and response.json() == {'matched':True}
    assert calls == [(slug,'only','gcc-2.7.2-kmc',['-O2'])]


def test_room_reconciliation_discovers_active_scoped_job_and_deduplicates(app,monkeypatch,tmp_path):
    from decompnet.app import room_reconcile
    client,net,*_=app
    slug='reconcile-host'
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,root,config) VALUES(?,?,'{\"kind\":\"rom\"}')",(slug,str(tmp_path)))
    entered=threading.Event();release=threading.Event()
    from types import SimpleNamespace
    room=SimpleNamespace()
    net.rooms[slug]=room
    monkeypatch.setattr(room_reconcile,'snapshot',lambda _: {'active':True,'members':{},'results':{}})
    def work(room,log):
        entered.set()
        while not release.wait(.01):log.check_cancel()
        return {'active':False,'members':{},'results':{}}
    monkeypatch.setattr(room_reconcile,'run',work)
    try:
        first=client.post('/api/collab/matches',json={'slug':slug,'reconcile':True},headers={'X-Decomp':'1'})
        assert first.status_code==200
        identity=first.json()['job']
        assert entered.wait(2)
        second=client.post('/api/collab/matches',json={'slug':slug,'reconcile':True},headers={'X-Decomp':'1'})
        assert second.json()['job']==identity
        attached=client.get('/api/collab/matches?slug='+slug).json()
        assert attached['reconcile_job']==identity and attached['reconciliation']['active']
        client.post('/api/job/cancel',json={'id':identity},headers={'X-Decomp':'1'})
    finally:
        release.set();net.rooms.pop(slug,None)


def test_github_setup_duplicate_and_reopen_attach(app, monkeypatch):
    from decompnet.app import repository_setup
    client, net, _ = app
    entered, release = threading.Event(), threading.Event()
    calls = []
    def setup(net, slug, action, origin, log, **kwargs):
        calls.append((action, kwargs))
        entered.set()
        release.wait(5)
        return {'repository':True, 'origin':True}
    monkeypatch.setattr(repository_setup, 'run', setup)
    data = {'slug':'game', 'action':'github', 'github_name':'owner/progress', 'visibility':'public'}
    try:
        first = client.post('/api/repository-setup', json=data, headers={'X-Decomp':'1'}).json()
        assert entered.wait(2)
        assert client.post('/api/repository-setup', json=data, headers={'X-Decomp':'1'}).json() == first
        assert client.get('/api/repository-setup?slug=game').json() == first
        assert calls == [('github', {'github_name':'owner/progress', 'visibility':'public'})]
    finally:release.set()


def test_github_owner_discovery_is_project_scoped(app, monkeypatch, tmp_path):
    from decompnet.app import repository_github, repository_setup
    client, net, _ = app
    calls = []
    monkeypatch.setattr(repository_setup, 'folder', lambda net, slug: (tmp_path/slug, {}))
    def owners(path):
        calls.append(path)
        return {'owners':['person','Studio'], 'warning':''}
    monkeypatch.setattr(repository_github, 'owners', owners)
    response = client.get('/api/repository-github-owners?slug=game')
    assert response.status_code == 200
    assert response.json()['owners'] == ['person','Studio']
    assert calls == [tmp_path/'game']


def test_restore_accepts_optional_local_game_but_keeps_confirmation(app, monkeypatch):
    from decompnet.app import saves
    client, net, _ = app
    calls = []
    def restore(net, source, game, log):
        calls.append((source,game))
        return {'slug':'game'}
    monkeypatch.setattr(saves, 'restore', restore)
    data = {'source':'/local/checkout', 'path':''}
    assert client.post('/api/restore', json=data, headers={'X-Decomp':'1'}).status_code == 400
    response = client.post('/api/restore', json={**data,'confirmed':True}, headers={'X-Decomp':'1'})
    assert response.status_code == 200
    for _ in range(100):
        job=client.get('/api/job?id='+response.json()['job']).json()
        if job['state'] != 'running':break
        time.sleep(.01)
    assert job['state'] == 'done' and calls == [('/local/checkout',None)]


def test_deep_maximum_options_reach_jobs_and_reject_invalid_limits(app, monkeypatch):
    from decompnet.app import deep_permuter
    client,net,_=app
    received=[]
    monkeypatch.setattr(deep_permuter,'validate',lambda *args:None)
    def run(*args,**kwargs):
        received.append(kwargs)
        return {'tested':0,'reason':'finished'}
    monkeypatch.setattr(deep_permuter,'run',run)
    payload={'slug':'maximum-game','function':'maximum-function','maximum_throughput':True,'compile_slots':10}
    response=client.post('/api/permuter/function',json=payload,headers={'X-Decomp':'1'})
    assert response.status_code==200
    for _ in range(100):
        job=client.get('/api/job',params={'id':response.json()['job']}).json()
        if job['state']!='running':break
        time.sleep(.01)
    assert job['state']=='done' and received[0]['maximum_throughput'] is True
    assert received[0]['compile_slots']==10
    for change in ({'compile_slots':257},{'maximum_throughput':'yes'}):
        assert client.post('/api/permuter/function',json=payload|change,headers={'X-Decomp':'1'}).status_code==400
    starts=[]
    monkeypatch.setattr(net.deep_sweeps,'start',lambda *args:starts.append(args) or {'job':'maximum-pass'})
    assert client.post('/api/permuter/pass',json=payload,headers={'X-Decomp':'1'}).status_code==200
    assert starts[0][-2:]==(True,10)


def test_repository_fork_owner_job_guard_and_rediscovery(app,monkeypatch):
    from decompnet.app import repository_fork, repository_push
    client,net,_=app
    started,release=threading.Event(),threading.Event()
    calls=[]
    monkeypatch.setattr(repository_fork,'info',lambda *a:{'owners':['mine','team'],'source':'original/repo','origin_revision':'revision'})
    def run(net,slug,folder,owner,revision,log):
        calls.append((slug,folder,owner,revision));started.set()
        assert release.wait(10)
        return {'fork':'team/repo'}
    monkeypatch.setattr(repository_fork,'run',run)
    monkeypatch.setattr(repository_push,'status',lambda *a:(_ for _ in ()).throw(AssertionError('Discover work first')))
    data={'slug':'game','folder':'/repo','owner':'team','origin_revision':'revision'}
    assert client.get('/api/repository-fork?slug=game').json()['owners']==['mine','team']
    assert client.post('/api/repository-fork',json=data).status_code==403
    try:
        result=client.post('/api/repository-fork',json=data,headers={'X-Decomp':'1'})
        assert result.status_code==200 and started.wait(5)
        job=result.json()['job']
        assert client.post('/api/repository-fork',json=data,headers={'X-Decomp':'1'}).json()['job']==job
        assert client.get('/api/repository-fork?slug=game&folder=/repo').json()['job']==job
        assert client.get('/api/repository-push?slug=game&folder=/repo').json()['fork_job']==job
        assert client.get('/api/job?id='+job).json()['scope']=={'slug':'game','folder':'/repo'}
        assert calls==[('game','/repo','team','revision')]
    finally:release.set()


def test_project_directory_history_requires_guard_and_native_switch(app,tmp_path,monkeypatch):
    from decompnet.app import data_directories
    client,net,_=app
    monkeypatch.setenv('DECOMP_DIRECTORIES_FILE',str(tmp_path/'locations.json'))
    state=client.get('/api/data-directories').json()
    assert state['current']==str(net.workspace.resolve()) and state['switchable']
    calls=[]
    def choose(self,path,switch=False):
        calls.append((path,switch))
        return self.status()
    monkeypatch.setattr(data_directories.Controller,'choose',choose)
    payload={'path':str(tmp_path),'action':'switch'}
    assert client.post('/api/data-directories',json=payload).status_code==403
    assert client.post('/api/data-directories',json=payload,headers={'X-Decomp':'1'}).status_code==200
    assert calls==[(str(tmp_path),True)]
    assert client.post('/api/data-directories',json={**payload,'action':'unsafe'},headers={'X-Decomp':'1'}).status_code==400
    assert not (tmp_path/'locations.json').exists()


def test_background_attempt_returns_before_verification_and_is_rediscoverable(app, monkeypatch):
    from decompnet.attempt_status import update

    from .test_loading import tiny_game
    client,net,_=app
    tiny_game(net,'attempt-stages')
    entered,finish=threading.Event(),threading.Event()
    calls=[]
    def attempt(agent,name,source,slug):
        calls.append((name,source,slug))
        entered.set()
        assert finish.wait(5)
        update('compiling',name)
        update('checking',name)
        update('saving',name)
        return {'score':0,'matched':True,'exact':True,'new_match':True,'adopted':True,'text':'exact','checks':{}}
    monkeypatch.setattr(net,'attempt',attempt)
    monkeypatch.setattr(net.progress,'sync',lambda *a,**k:False)
    body={'slug':'attempt-stages','name':'f','source':'candidate','background':True}
    try:
        response=client.post('/api/try',json=body,headers={'X-Decomp':'1'})
        assert response.status_code==200,response.text
        job=response.json()['job']
        assert entered.wait(5)
        status=client.get('/api/try/status?slug=attempt-stages&name=f').json()
        assert status['job']==job and status['progress']['stage']=='waiting'
        assert status['scope']=={'slug':'attempt-stages','function':'f'}
        assert client.get('/api/try/status?slug=attempt-stages&name=another').json()['job'] is None
        assert client.post('/api/try',json=body,headers={'X-Decomp':'1'}).json()['job']==job
        assert len(calls)==1
    finally:finish.set()
    for _ in range(100):
        result=client.get('/api/job?id='+job).json()
        if result['state']!='running':break
        time.sleep(.01)
    assert result['state']=='done',result
    assert result['result']['matched'] and result['result']['adopted']
    assert result['progress']['stage']=='saving'
    assert client.get('/api/try/status?slug=attempt-stages&name=f').json()['job'] is None


def test_synchronous_attempt_keeps_final_response_and_exposes_running_job(app, monkeypatch):
    from .test_loading import tiny_game
    client,net,_=app
    tiny_game(net,'sync-attempt-stages')
    entered,finish=threading.Event(),threading.Event()
    responses=[]
    def attempt(*args):
        entered.set()
        assert finish.wait(5)
        return {'score':7,'matched':False,'text':'score 7'}
    monkeypatch.setattr(net,'attempt',attempt)
    monkeypatch.setattr(net.progress,'sync',lambda *a,**k:False)
    body={'slug':'sync-attempt-stages','name':'f','source':'candidate'}
    worker=threading.Thread(target=lambda:responses.append(client.post('/api/try',json=body,headers={'X-Decomp':'1'})))
    worker.start()
    try:
        assert entered.wait(5) and not responses
        state=client.get('/api/try/status?slug=sync-attempt-stages&name=f').json()
        assert state['job'] and state['progress']['stage']=='waiting'
        different=client.post('/api/try',json={**body,'background':True,'source':'different'},headers={'X-Decomp':'1'})
        assert different.status_code==400 and 'already compiling' in different.text
    finally:
        finish.set();worker.join(5)
    assert not worker.is_alive() and responses[0].status_code==200
    assert responses[0].json()['score']==7 and 'job' not in responses[0].json()
