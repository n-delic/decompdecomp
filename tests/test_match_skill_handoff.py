"""handoff.py (the match-function skill): the hand-off note built from the session, then add_note and
release_function with every argument they need."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "match-function" / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("skill_handoff", SCRIPTS / "handoff.py")
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)
ROWS = [(" ", "jr $ra", "jr $ra"), ("r", "move $a1, $v0", "move $a2, $v0"), ("|", "lw $t0, 0($a0)", "lh $t0, 0($a0)")]


class Judge:
    slug, name, closed = "game", "f", False

    def rows(self, source):
        return 30, ROWS

    def ask(self, request):
        return {"text": "$a2 where the target has $a1 (1 row):\n  in $a2: pseudo 80\n    why not $a1: ...\n"
                        "    cause: call-argument copy: insn 36 into $a1"}

    def close(self):
        self.closed = True


class Gateway:
    def __init__(self, held=True):
        self.calls, self.held = [], held

    def call(self, tool, arguments):
        self.calls.append((tool, arguments))
        if tool == "get_claims":
            return json.dumps({"claims": [{"function": "f", "claim_token": "tok"}] if self.held else []})
        return f"{tool} ok"


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(handoff.setup, "TMP", tmp_path)
    root = tmp_path / "dd-agent"
    (root / "start").mkdir(parents=True)
    (root / "start" / "best.c").write_text("void f(void) {}\n")
    (root / "connection.json").write_text(json.dumps({"slug": "game", "function": "f", "home": str(tmp_path)}))
    return root


def test_the_note_has_rows_register_pairs_tried_approaches_and_the_source(session):
    gateway, said = Gateway(), []
    assert handoff.handoff("agent", ["cast dropped: 40", "temp for the cursor: 35"], gateway=gateway, judge=Judge(),
                           findings="sched2 keeps the load first", write=said.append) == 0
    note = said[0]
    assert note.startswith(f"Hand-off of f: best 30 (2 differing rows) in {session / 'start' / 'best.c'}")
    assert "  r | move $a1, $v0 | move $a2, $v0" in note and "jr $ra" not in note
    assert "$a2 where the target has $a1" in note and "cause: call-argument copy" in note
    assert "  - cast dropped: 40" in note and "sched2 keeps the load first" in note
    assert gateway.calls[0] == ("add_note", {"function": "f", "text": note})
    tool, arguments = gateway.calls[2]
    assert tool == "release_function" and arguments["function"] == "f" and arguments["claim_token"] == "tok"
    assert "handed off at 30" in arguments["notes"]
    info = json.loads((session / "connection.json").read_text())
    assert info["function"] == "" and info["released"] == ["f"]  # the renewal lets it go


def test_dry_run_calls_nothing_and_tried_is_required(session):
    gateway = Gateway()
    assert handoff.handoff("agent", ["x: 1"], dry_run=True, gateway=gateway, judge=Judge(), write=lambda _: None) == 0
    assert gateway.calls == []
    with pytest.raises(SystemExit, match="--tried is required"):
        handoff.handoff("agent", [], gateway=gateway, judge=Judge())


def test_a_lost_claim_keeps_the_note_and_releases_nothing(session):
    gateway, said = Gateway(held=False), []
    assert handoff.handoff("agent", ["x: 1"], gateway=gateway, judge=Judge(), write=said.append) == 1
    assert [tool for tool, _ in gateway.calls] == ["add_note", "get_claims"] and "not claimed" in said[-1]
