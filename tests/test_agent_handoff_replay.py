"""Replay a stalled checksum and hand it off through the real ROM and project judges.

The standard ROM fixture runs the console's build.py in the shared demo build directory.
"""
from pathlib import Path

import pytest

from decompnet import consoles
from decompnet.app import agent_tokens, tools
from decompnet.release_receipts import current
from tests.test_consoles import _function
from tests.test_consoles import demo as rom_fixture
from tests.test_forks import _ref, needs_tools
from tests.test_forks import fork as fork_fixture

demo = rom_fixture
fork = fork_fixture


@pytest.mark.parametrize("demo", [consoles.get("n64")], indirect=True)
def test_rom(demo):
    _, net, slug, _, symbols = demo
    name, exact = _function(net, slug, symbols, "checksum_save")

    def change_inputs(agent):
        net.edit_context(agent, slug, "", "\n/* Handoff scope replay */\n", "Change the header")

    replay(net, slug, name, exact, change_inputs)


@needs_tools
def test_import(fork):
    net, slug, _ = fork

    def change_inputs(_agent):
        header = Path(net.project_row(slug)["root"]) / "include" / "game.h"
        header.write_text(header.read_text() + "\n/* Handoff scope replay */\n")

    replay(net, slug, "checksum_save", _ref("checksum_save"), change_inputs)


def replay(net, slug, name, exact, change_inputs):
    agent, _ = net.local_agent("first-worker")
    peer, _ = net.local_agent("next-worker")
    assert "CLAIMED" in tools._claim(net, agent, slug, name, "")
    near = exact.replace("0x1234", "0x1235")
    for spelling in ("0x1235", "(0x1235)", "(0x1230 + 5)"):
        result = tools._compile(net, agent, slug, name, near.replace("0x1235", spelling))
    assert "Multiple edits produced identical instructions" in result
    # The handoff must describe what the next claim actually inherits, even after tied trials.
    tools.call(net, agent, slug, "get_candidate", {"function": name, "base": "saved_best"})
    source, revision = agent_tokens.candidate(net, agent, slug, name)
    assert "0x1235" in source
    claim = net._function(name, slug)
    args = dict(function=name, notes="Stalled on a repeated diff.", move_on=True,
                claim_token=current(net, claim, agent), next_experiment={
                    "revision": revision, "tried": "Equivalent expressions for the initializer",
                    "changed": "Identical instructions in all three trials",
                    "title": "Test the target initializer", "old": "0x1235", "new": "0x1234"})
    reply = tools.call(net, agent, slug, "release_function", args)
    assert "Next experiment saved" in reply
    text = tools._claim(net, peer, slug, name, "")
    assert "Equivalent expressions for the initializer" in text
    assert "Identical instructions in all three trials" in text
    assert "Untested next step: Test the target initializer" in text
    assert "0x1234" in tools.call(net, peer, slug, "get_function", dict(function=name, section="experiment"))
    assert tools.call(net, agent, slug, "release_function", args) == reply  # An old retry cannot overwrite a live handoff.
    change_inputs(peer)
    for section in ("experiment", "brief"):
        text = tools.call(net, peer, slug, "get_function", dict(function=name, section=section))
        assert "Test the target initializer" not in text
    assert "Test the target initializer" not in tools._claim(net, peer, slug, name, "")
