"""The connector (agents/__init__.py) and every agent that plugs into it.

What holds for every agent without any agent installed (CI checks it on every push): its facts are complete, ids and
older ids are unique and lead to it, a program the app runs says how a session runs and reads its own output, the
template is a complete example that isn't registered, and nothing outside agents/ asks an agent's name. No agent's
program is ever run here: a stand-in prints a version.
"""

import re
from pathlib import Path

import pytest

from decompnet import agents, network
from decompnet.agents import template
from decompnet.app import agents as helpers

SRC = Path(__file__).resolve().parent.parent / "src" / "decompnet"
EVERY = [*agents.AGENTS, template.EXAMPLE]
TOKEN = "secret-token-1234"
PROMPT = "line one\nline two"


@pytest.fixture
def stand_in(tmp_path, monkeypatch):
    """A program that only says its version, and a home without any agent's settings."""
    exe = tmp_path / "bin" / "agent"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\necho 1.0.0\n")
    exe.chmod(0o755)
    for var in ("HOME", "XDG_CONFIG_HOME", "CODEX_HOME"):
        monkeypatch.setenv(var, str(tmp_path / "home"))
    monkeypatch.setattr(agents, "known_versions", {})
    return exe


@pytest.mark.parametrize("agent", EVERY, ids=[a.id for a in EVERY])
def test_every_agent_says_everything_the_app_asks(agent, stand_in, tmp_path):
    assert agent.id and agent.name and agent.vendor and agent.docs.startswith("https://")
    described = agent.describe()
    assert described["id"] == agent.id and described["name"] == agent.name and "uses_endpoints" in described
    if agent.command:  # a program the user installs and signs in to: the app runs it, a session at a time
        assert agent.install and agent.signin
        assert type(agent).parse is not agents.Agent.parse, "it reads what it prints"
        work = tmp_path / "work"
        work.mkdir()
        cmd, env = agent.session_command(str(stand_in), work, "http://127.0.0.1:1/mcp", TOKEN, "some-model")
        assert cmd[0] == str(stand_in) and "some-model" in cmd
        assert not any(TOKEN in a for a in cmd), "a token on the command line is there for every process to read"
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())
        assert "line one" in agent.stdin(PROMPT) and agent.parse_line("not json") == [("info", "not json")]
        assert agent.version(str(stand_in)) == "1.0.0"
    else:  # built into the app: a loop of its own
        assert type(agent).run is not agents.Agent.run, "it says how a session runs"


def test_ids_are_unique_and_older_ids_lead_to_their_agent():
    ids = [key for a in agents.AGENTS for key in (a.id, *a.aliases)]
    assert len(ids) == len(set(ids))
    for agent in agents.AGENTS:
        assert agents.get(agent.id) is agent
        assert all(agents.get(old) is agent and agents.kind_id(old) == agent.id for old in agent.aliases)
    assert agents.kind_id("autopilot") == "autopilot"  # any other name stays as it is


def test_an_unknown_agent_says_which_there_are():
    with pytest.raises(ValueError, match="no agent called 'gemini-cli'.*Claude Code.*API key"):
        agents.get("gemini-cli")


def test_the_template_is_not_registered():
    assert template.EXAMPLE not in agents.AGENTS and template.EXAMPLE.id not in {a.id for a in agents.AGENTS}
    with pytest.raises(ValueError):
        agents.get(template.EXAMPLE.id)


def test_the_launcher_gets_the_facts_it_shows_agents_by():
    """The GUI shows the API helper first, with its endpoints instead of models, and opencode's effort: from what
    each agent says, not from its name."""
    rows = {row["id"]: row for row in helpers.detect(quick=True)}
    assert [row["id"] for row in helpers.detect(quick=True)] == [a.id for a in agents.AGENTS]
    assert sum(row["recommended"] for row in rows.values()) == 1 and rows["api"]["recommended"]
    assert rows["api"]["uses_endpoints"] and rows["api"]["installed"] and rows["opencode"]["takes_effort"]


def test_an_agent_that_runs_one_makers_models_names_their_family():
    """A function a model released goes to another model family first (network.model_family): Claude Code runs only
    Anthropic's models, also under a helper's name from before ("gemini-2": Antigravity's, Google's)."""
    assert network.model_family("claude-opus-4-6-1") == "claude" and network.model_family("codex-1") == "gpt"
    assert network.model_family("gemini-2") == network.model_family("antigravity-1") == "gemini"
    assert network.model_family("opencode-deepseek-v4.1-flash-1") == "deepseek"  # the model in its name says


def test_antigravity_loads_decomps_tools_up_front():
    from decompnet.app import tools

    assert tools.ALWAYS_LOAD == {"antigravity/alwaysLoad": True}


# An agent's id or older id compared with a string: `kind == "claude"`, `kind in ("opencode", ...)`, `a.id === "api"`
_IDS = "|".join(re.escape(key) for a in agents.AGENTS for key in (a.id, *a.aliases))
_NAME_CHECK = re.compile(rf"""(?:[!=]==?|\bin)\s*[(\[{{]?[^)\]}}\n]*?["']({_IDS})["']|["']({_IDS})["']\s*[!=]==?""")


def test_nothing_outside_agents_asks_an_agents_name():
    """What one agent does its own way is in its module in agents/: the rest of the app asks agents.get(kind) for a
    fact or a part. A check of an agent's name elsewhere is a bug (cleanup.py's list of the old bridge's
    registrations is about files older versions wrote, not about the agents)."""
    found = []
    for path in sorted([*SRC.rglob("*.py"), *SRC.rglob("*.js")]):
        if SRC / "agents" in path.parents:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _NAME_CHECK.search(line):
                found.append(f"{path.relative_to(SRC)}:{number}: {line.strip()}")
    assert not found, "\n".join(found)
