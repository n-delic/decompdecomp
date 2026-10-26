"""Imported credits and older helper names must not prevent a project or helper from opening."""

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


@pytest.mark.parametrize("name", ["@alex.claude-opus-1", "provider/model#max", "helper-" + "x" * 60, "", "   "])
def test_imported_agent_names_get_a_stable_valid_identity(tmp_path, name):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    agent, token = net.local_agent(name)
    assert 1 <= len(agent.name) <= 40
    assert all(c.isalnum() or c in "-_." for c in agent.name)
    assert net.authenticate(token).id == agent.id
    again, token = net.local_agent(name)
    assert again.id == agent.id
    assert net.authenticate(token).id == agent.id
    assert net.store.one("SELECT COUNT(*) AS n FROM agents")["n"] == 1


def test_imported_agent_names_do_not_merge_different_authors(tmp_path):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    names = ["@alex", "alex", "a/b", "a b", "helper-" + "x" * 60, "helper-" + "x" * 61]
    assert len({net.local_agent(name)[0].id for name in names}) == len(names)


def test_existing_agent_names_keep_their_identity(tmp_path):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    agent, _ = net.local_agent("claude-opus-1")
    with net.store.tx() as db:
        db.execute("UPDATE agents SET name=? WHERE id=?", ("@old-helper", agent.id))
    restored, _ = net.local_agent("@old-helper")
    assert (restored.id, restored.name) == (agent.id, "@old-helper")


def test_registering_an_invalid_agent_name_still_reports_an_error(tmp_path):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with pytest.raises(NetworkError, match="agent name must"):
        net.register_agent("bad name!")
