"""A free OpenCode model out of its allowance offers the same model on OpenCode Go, when the user has it."""
from types import SimpleNamespace

from decompnet import agents
from decompnet.app.agents import Session

KNOWN = [{"id": "opencode/big-pickle"}, {"id": "opencode-go/big-pickle"}, {"id": "opencode/flint-40b-free"},
         {"id": "opencode/deepseek-v4.1-flash-free"}, {"id": "opencode-go/deepseek-v4.1-flash"}]
LIMIT = "provider.free-limit: Free usage limit reached. Connect a provider or subscribe to OpenCode Go"


def test_opencode_offers_its_go_model_for_a_free_model_out_of_its_allowance():
    kind = agents.get("opencode")
    assert kind.limit_alternative("opencode/big-pickle", LIMIT, KNOWN) == {"model": "opencode-go/big-pickle",
                                                                          "label": "OpenCode Go"}
    assert kind.limit_alternative("opencode/big-pickle#low", LIMIT, KNOWN)["model"] == "opencode-go/big-pickle#low"
    assert kind.limit_alternative("opencode/flint-40b-free", LIMIT, KNOWN) is None, "Go doesn't list it"
    assert kind.limit_alternative("opencode/deepseek-v4.1-flash-free", LIMIT, KNOWN)["model"] == \
        "opencode-go/deepseek-v4.1-flash", "Go names it without -free"
    assert kind.limit_alternative("opencode/big-pickle", "provider.no-route: Model unavailable", KNOWN) is None
    assert kind.limit_alternative("opencode-go/big-pickle", LIMIT, KNOWN) is None, "already on Go"
    assert agents.get("claude").limit_alternative("haiku", LIMIT, KNOWN) is None


def test_a_paused_helpers_card_carries_it(monkeypatch):
    kind = agents.get("opencode")
    monkeypatch.setattr(kind, "cached_models", lambda: KNOWN)
    s = SimpleNamespace(state="paused", model="opencode/big-pickle", why=LIMIT, error="", todo="", options={})
    assert Session.limit_alternative(s, kind)["model"] == "opencode-go/big-pickle"
    s.state = "working"
    assert Session.limit_alternative(s, kind) is None
    s.state, s.options = "paused", {"model_replacement": "9"}
    assert Session.limit_alternative(s, kind) is None, "already replaced"


def test_the_cards_button_switches_once_with_feedback():
    import shutil
    import subprocess
    from pathlib import Path

    import pytest
    node = shutil.which("node")
    if node is None:
        pytest.skip("needs node to run the GUI's scripts")
    subprocess.run([node, str(Path(__file__).with_name("gui_limit_alternative.js"))], check=True,
                   capture_output=True, text=True, encoding="utf-8", timeout=60)
