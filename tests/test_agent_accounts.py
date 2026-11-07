"""#45: named subscription logins stay separate through launching, restarts and usage history."""

import json
import shlex
import sys
import time
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from decompnet.agents import claude
from decompnet.app import agents
from decompnet.app.server import build
from decompnet.app.support import Settings

from .test_agents import URL, Quick, _game, _manager
from .test_app_server import PORT, SECRET, WINDOW


def test_accounts_keep_only_login_references_and_never_reuse_removed_ids(tmp_path):
    settings = Settings(tmp_path / "settings.json")
    first = settings.save_account({"kind": "claude", "name": "Personal", "api_key": "not-saved"})
    second = settings.save_account({"kind": "claude", "name": "Work", "config_dir": str(tmp_path / "work")})
    assert first["config_dir"] == str(tmp_path / "agent-accounts" / "a1")
    assert not (tmp_path / "agent-accounts").exists()  # adding a reference doesn't touch a login
    assert "not-saved" not in json.dumps(settings.public())
    assert "CLAUDE_CONFIG_DIR" in settings.public()["agent_accounts"][0]["signin"]
    settings.save()
    restored = Settings(settings.path)
    assert restored.account_options("claude", "a2")["account_name"] == "Work"
    with pytest.raises(ValueError, match="cannot change"):
        restored.save_account(second | {"config_dir": str(tmp_path / "another")})
    restored.remove_account(first["id"])
    assert restored.save_account({"kind": "claude", "name": "Third"})["id"] == "a3"
    with pytest.raises(ValueError, match="no longer saved"):
        restored.account_options("claude", "a1")
    with pytest.raises(ValueError, match="no longer saved"):
        restored.account_options("codex", "a2")


def test_accounts_validate_before_saving_and_do_not_delete_login_files(tmp_path):
    settings = Settings(tmp_path / "settings.json")
    for data in ({"kind": "claude", "name": ""}, {"kind": "codex", "name": "Work"},
                 {"kind": "claude", "name": "Work", "config_dir": "relative"}):
        with pytest.raises(ValueError):
            settings.save_account(data)
    assert settings.accounts() == []
    folder = tmp_path / "own-login"
    folder.mkdir()
    (folder / "settings.json").write_text('{}')
    account = settings.save_account({"kind": "claude", "name": "Work", "config_dir": str(folder)})
    for data in ({"kind": "claude", "name": "work"},
                 {"kind": "claude", "name": "Other", "config_dir": str(folder)}):
        with pytest.raises(ValueError):
            settings.save_account(data)
    settings.remove_account(account["id"])
    assert (folder / "settings.json").read_text() == '{}'


def test_claude_uses_each_accounts_directory_and_default_model_without_changing_the_parent(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "default"))
    for name, model in (("one", "opus"), ("two", "sonnet"), ("default", "haiku")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "settings.json").write_text(json.dumps({"model": model}))
    original = {"PATH": "somewhere", "CLAUDE_CONFIG_DIR": str(tmp_path / "default")}
    for name, model in (("one", "opus"), ("two", "sonnet")):
        options = {"account_id": name, "account_dir": str(tmp_path / name)}
        env = claude.CLAUDE.session_environment(options, original)
        assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / name)
        assert claude.CLAUDE.default_model(options) == model
        assert str(tmp_path / name) in claude.CLAUDE.signin_for(options)
    assert original["CLAUDE_CONFIG_DIR"] == str(tmp_path / "default")
    assert claude.CLAUDE.session_environment({}, original) == original
    assert claude.CLAUDE.config_model() == "haiku"


@pytest.mark.parametrize("override", ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                                     "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                                     "ANTHROPIC_PROFILE"])
def test_named_accounts_refuse_inherited_or_configured_credential_overrides(tmp_path, override):
    options = {"account_id": "a1", "account_dir": str(tmp_path)}
    with pytest.raises(ValueError, match=override) as error:
        claude.CLAUDE.session_environment(options, {override: "secret"})
    assert "secret" not in str(error.value)
    (tmp_path / "settings.json").write_text(json.dumps({"env": {override: "secret"}}))
    with pytest.raises(ValueError, match=override):
        claude.CLAUDE.session_environment(options, {})


def test_account_login_commands_quote_paths_for_the_host_shell(monkeypatch):
    folder = "/tmp/a user's login; $(false)"
    monkeypatch.setattr(sys, "platform", "linux")
    assert shlex.split(claude.CLAUDE.account_signin(folder)) == ["CLAUDE_CONFIG_DIR=" + folder, "claude"]
    monkeypatch.setattr(sys, "platform", "win32")
    assert claude.CLAUDE.account_signin("C:\\a user's login") == "$env:CLAUDE_CONFIG_DIR='C:\\a user''s login'; claude"


def test_command_launch_passes_account_to_the_child_and_checks_removed_accounts(tmp_path, monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
                "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(key, raising=False)
    manager = _manager(tmp_path, monkeypatch, "claude")
    session = agents.Session("1", "claude", "Work", "game", "claude-1",
                             {"account_id": "a1", "account_dir": str(tmp_path / "work")})
    _, env, _ = manager._command(session, "token", tmp_path)
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "work")
    manager.options_now = lambda kind, options: None
    with pytest.raises(agents.Fatal, match="removed"):
        manager._command(session, "token", tmp_path)


def test_accounts_survive_restart_without_reassigning_helpers_or_old_usage(tmp_path, monkeypatch):
    net = _game(tmp_path)
    monkeypatch.setattr(agents.Manager, "_prepare", lambda *args: None)
    monkeypatch.setattr(agents.Manager, "_run", lambda *args: None)
    monkeypatch.setattr(claude.CLAUDE, "default_model", lambda options: "opus")
    manager = agents.Manager(net, tmp_path, URL)
    options = {"account_id": "a1", "account_name": "Personal", "account_dir": str(tmp_path / "personal")}
    (first,) = manager.start("claude", "game", options=options)
    manager.sessions[first].thread.join()
    session = manager.sessions[first]
    agent, _ = net.local_agent(session.agent_name)
    began = time.time()
    row = manager._session_begin(session)
    manager.account(session, {"tokens": 100, "usd": 0.25})
    manager._session_end(session, row, agent, began, "done")
    manager.stop(first)
    (second,) = manager.start("claude", "game", options=options | {"account_id": "a2", "account_name": "Work"})
    manager.sessions[second].thread.join()
    assert first != second and manager.sessions[second].tokens == 0
    second_session = manager.sessions[second]
    agent, _ = net.local_agent(second_session.agent_name)
    row = manager._session_begin(second_session)
    manager.account(second_session, {"tokens": 200, "usd": 0.5})
    manager._session_end(second_session, row, agent, began, "limit")
    manager.stop(second)
    restored = agents.Manager(net, tmp_path, URL)
    assert restored.sessions[first].options == options | {"model": "opus"}
    assert restored.sessions[second].options["account_id"] == "a2"
    assert restored.sessions[second].snapshot()["account_name"] == "Work"
    assert manager.start("claude", "game", options=options | {"account_id": "a2", "account_name": "Work"}) == [second]
    manager.sessions[second].thread.join()
    manager.stop(second)
    manager.remove(first)
    usage = {r["account_id"]: r for r in restored.account_usage()}
    assert (usage["a1"]["tokens"], usage["a2"]["tokens"]) == (100, 200)
    assert usage["a1"]["usd"] == 0.25 and usage["a2"]["sessions"] == 1


def test_account_routes_resolve_saved_options_and_stop_only_removed_accounts(tmp_path, monkeypatch):
    from .test_loading import tiny_game

    net = _game(tmp_path)
    # This route is shared by ROM and project helpers: the account is resolved before either engine runs.
    tiny_game(net, slug="other")
    mcp, manager, security, _ = build(net, tmp_path, PORT, SECRET)
    client = TestClient(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                        base_url=f"http://127.0.0.1:{PORT}", headers=WINDOW)
    writes = {"X-Decomp": "1"}
    assert client.post("/api/agent-accounts", json={"kind": "claude", "name": "Work"}).status_code == 403
    response = client.post("/api/agent-accounts", headers=writes, json={"kind": "claude", "name": "Work"})
    assert response.status_code == 200, response.text
    account = response.json()["agent_accounts"][0]
    started = []
    monkeypatch.setattr(manager, "start", lambda kind, slug, count, options: started.append(options) or ["1"])
    monkeypatch.setattr(manager, "detected", lambda: [{"id": "claude", "models": []}])
    response = client.post("/api/agents/start", headers=writes, json={"kind": "claude", "slug": "game",
        "options": {"account_id": account["id"], "models": ["opus", "sonnet"], "account_dir": "/untrusted"}})
    assert response.status_code == 200, response.text
    assert len(started) == 2 and all(o["account_dir"] == account["config_dir"] for o in started)
    assert client.get("/api/state").json()["settings"]["launcher"]["account_id"] == account["id"]
    assert manager.options_now("claude", started[0])["account_id"] == account["id"]
    manager.sessions = {"selected": SimpleNamespace(options={"account_id": account["id"]}, state="working"),
                        "another": SimpleNamespace(options={"account_id": "another"}, state="working"),
                        "default": SimpleNamespace(options={}, state="working")}
    stopped = []
    monkeypatch.setattr(manager, "stop", lambda sid: stopped.append(sid))
    response = client.post("/api/agent-accounts", headers=writes, json={"remove": account["id"]})
    assert response.status_code == 200 and stopped == ["selected"]
    assert manager.options_now("claude", started[0]) is None
    response = client.post("/api/agents/start", headers=writes, json={"kind": "claude", "slug": "game",
        "options": {"account_id": account["id"]}})
    assert response.status_code == 400 and len(started) == 2



def test_old_sessions_remain_unassigned_when_account_columns_are_added(tmp_path):
    net = _game(tmp_path)
    agents.Manager(net, tmp_path, URL)
    with net.store.tx() as db:
        db.execute("ALTER TABLE helper_sessions DROP COLUMN account_id")
        db.execute("ALTER TABLE helper_sessions DROP COLUMN account_name")
        db.execute("INSERT INTO helper_sessions(helper_id, project, kind, model, agent, started_at, ended_at, tokens) "
                   "VALUES(1, 'game', 'claude', 'opus', 'claude-1', 1, 2, 123)")
    manager = agents.Manager(net, tmp_path, URL)
    (row,) = manager.account_usage()
    assert row["account_id"] == row["name"] == "" and row["tokens"] == 123


def test_a_limited_account_restarts_on_itself_and_leaves_another_account_running(tmp_path, monkeypatch):
    net = _game(tmp_path)
    manager = agents.Manager(net, tmp_path, URL)
    options = {"account_id": "a1", "account_name": "Personal", "account_dir": str(tmp_path / "personal")}
    session = manager._record("game", "claude", "claude-1", "Personal", options)
    another = manager._record("game", "claude", "claude-2", "Work", options | {"account_id": "a2"})
    another.state = "working"
    session.stop = Quick()
    runs = []

    def run(current, token, workdir):
        runs.append(current.options["account_id"])
        assert another.state == "working" and not another.stop.is_set()
        if len(runs) == 1:
            current.limited, current.limit_until = True, time.time() + 3600
            return False
        manager.stop(current.id)
        return True

    monkeypatch.setattr(manager, "_run_cli", run)
    manager._run(session)
    assert runs == ["a1", "a1"] and session.restarts == 1
    assert all(r["account_id"] == "a1" for r in net.store.all("SELECT account_id FROM helper_sessions"))
    assert another.state == "working"



def test_a_session_that_hits_its_limit_still_records_its_reported_usage():
    items = claude.CLAUDE.parse({"type": "result", "is_error": True, "result": "usage limit reached",
                               "total_cost_usd": 0.5, "num_turns": 3,
                               "usage": {"input_tokens": 50, "output_tokens": 25}}, {})
    assert ("error", "usage limit reached") in items
    usage = json.loads(next(text for kind, text in items if kind == "usage"))
    assert usage["input"] == 50 and usage["output"] == 25 and usage["usd"] == 0.5



def test_renaming_an_account_updates_helper_labels_without_changing_the_login(tmp_path):
    net = _game(tmp_path)
    manager = agents.Manager(net, tmp_path, URL)
    options = {"account_id": "a1", "account_name": "Personal", "account_dir": str(tmp_path / "login")}
    session = manager._record("game", "claude", "claude-1", "claude-1", options)
    manager.rename_account("a1", "Second subscription")
    assert session.snapshot()["account_name"] == "Second subscription"
    assert session.options["account_dir"] == options["account_dir"] and session.options["account_id"] == "a1"
    restored = agents.Manager(net, tmp_path, URL)
    assert restored.sessions[session.id].options == options | {"account_name": "Second subscription"}
    settings = Settings(tmp_path / "settings.json")
    account = settings.save_account({"kind": "claude", "name": "Personal"})
    renamed = settings.save_account({"id": account["id"], "name": "Second subscription"})
    assert renamed["id"] == account["id"] and renamed["config_dir"] == account["config_dir"]
    assert renamed["name"] == "Second subscription"
